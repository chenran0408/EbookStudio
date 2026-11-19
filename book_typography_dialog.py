"""Native stylesheet and embedded font workspace with an isolated EPUB preview."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from lxml import etree
from PySide6.QtCore import QSignalBlocker, Qt, QTimer, QUrl
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
    QSplitter, QTabWidget, QVBoxLayout, QWidget,
)
from PySide6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from book_resources import resource_category
from book_typography import (
    DEFAULT_STYLE_PATH, add_linked_stylesheet, bind_font, book_characters, css_issues,
    font_info, set_stylesheet, stylesheet_text, subset_font,
)
from ebook_core import archive_path, asset_href
from epub_io import CSS, XHTML, _bytes, _document, _put, _unique, _write_chapter, _xml


FONT_INFO_ROLE = Qt.ItemDataRole.UserRole + 1


class PreviewFiles(QWebEngineUrlRequestInterceptor):
    """Allow EPUB resources only from this dialog's dedicated temporary directory."""

    def __init__(self, directory, parent):
        super().__init__(parent)
        self.directory = directory

    def interceptRequest(self, info):
        url = info.requestUrl()
        if url.scheme() == 'data':
            return
        if not url.isLocalFile() or not Path(url.toLocalFile()).resolve().is_relative_to(self.directory):
            info.block(True)


class PreviewPage(QWebEnginePage):
    address = QUrl()

    def acceptNavigationRequest(self, url, _navigation_type, is_main_frame):
        return is_main_frame and url.isLocalFile() and url.toLocalFile() == self.address.toLocalFile()


class TypographyPreview(QWebEngineView):
    def __init__(self, directory, parent):
        super().__init__(parent)
        self.directory = Path(directory).resolve()
        self.target_width = 390
        self.written = {}
        self.profile = QWebEngineProfile()
        self.files = PreviewFiles(self.directory, self.profile)
        self.profile.setUrlRequestInterceptor(self.files)
        self.preview_page = PreviewPage(self.profile, self)
        for flag in (QWebEngineSettings.WebAttribute.JavascriptEnabled,
                     QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                     QWebEngineSettings.WebAttribute.PluginsEnabled):
            self.preview_page.settings().setAttribute(flag, False)
        self.preview_page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.setPage(self.preview_page)
        self.setAccessibleName('章节排版预览')

    def write(self, name, data):
        if self.written.get(name) is data or self.written.get(name) == data:
            return
        file = self.directory / archive_path(name)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        self.written[name] = data

    def show_book(self, book, chapter, width, face=None):
        self.target_width = width
        self.fit()
        for name, asset in book.assets.items():
            self.write(name, asset.data)
        occupied = set(book.assets) | set(book._epub_members) | {c.path for c in book.chapters}
        if face:
            address = _unique('typography-font-preview.xhtml', occupied)
            doc, body = _document('字体试样', book.language)
            head = doc.find(f'{{{XHTML}}}head')
            source = asset_href(face, address)
            _put(head, f'{{{XHTML}}}style', f'@font-face{{font-family:sample;src:url("{source}")}}'
                 'body{font:24px/1.8 sample,serif;padding:1em}')
            _put(body, f'{{{XHTML}}}h2', book.title)
            _put(body, f'{{{XHTML}}}p', '天地玄黄，宇宙洪荒。0123456789 Aa Bb Cc。')
        else:
            address = chapter.path
            css_path = None
            if address not in book._epub_chapters:
                css_path = _unique(DEFAULT_STYLE_PATH, occupied)
                self.write(css_path, CSS + b'\n' + book.stylesheet.encode('utf-8'))
            doc = _xml(_write_chapter(book, chapter, css_path))
            head = doc.find(f'{{{XHTML}}}head')
        policy = etree.Element(f'{{{XHTML}}}meta', {
            'http-equiv': 'Content-Security-Policy',
            'content': "default-src 'none'; style-src 'unsafe-inline' file: data:; img-src file: data:; "
                       "font-src file: data:; base-uri 'none'; form-action 'none'",
        })
        head.insert(0, policy)
        self.write(address, _bytes(doc.getroottree()))
        self.preview_page.address = QUrl.fromLocalFile(str(self.directory / address))
        self.load(self.preview_page.address)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()

    def fit(self):
        self.setZoomFactor(max(0.25, min(2.0, self.width() / self.target_width)))

    def finish(self):
        self.stop()
        self.preview_page.deleteLater()
        self.profile.deleteLater()


def _combo(label):
    combo = QComboBox()
    combo.setAccessibleName(label)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(10)
    return combo


def _message(text=''):
    label = QLabel(text, objectName='muted')
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class TypographyDialog(QDialog):
    def __init__(self, parent, book, active, directory):
        super().__init__(parent)
        self.setWindowTitle('CSS 与字体')
        self.resize(1030, 680)
        self.book = deepcopy(book)
        self.original = book
        self.modified = set()
        self.issues = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        split = QSplitter()
        split.setChildrenCollapsible(False)
        layout.addWidget(split, 1)
        self.tabs = QTabWidget()
        split.addWidget(self.tabs)
        self.build_styles()
        self.build_fonts()

        reader = QWidget()
        right = QVBoxLayout(reader)
        right.setContentsMargins(8, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.chapter = _combo('预览章节')
        for index, chapter in enumerate(book.chapters, 1):
            self.chapter.addItem(f'{index} · {chapter.title}')
        self.chapter.setCurrentIndex(max(0, active))
        self.device = _combo('预览宽度')
        self.device.addItem('手机 · 390 px', 390)
        self.device.addItem('平板 · 760 px', 760)
        toolbar.addWidget(QLabel('预览'))
        toolbar.addWidget(self.chapter, 1)
        toolbar.addWidget(self.device)
        right.addLayout(toolbar)
        self.preview = TypographyPreview(directory, reader)
        right.addWidget(self.preview, 1)
        self.preview_note = _message()
        right.addWidget(self.preview_note)
        split.addWidget(reader)
        split.setSizes([450, 550])

        bottom = QHBoxLayout()
        self.status = _message()
        bottom.addWidget(self.status, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.apply_button = buttons.addButton('应用更改', QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setObjectName('primary')
        self.apply_button.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        layout.addLayout(bottom)

        self.refresh_timer = QTimer(self, singleShot=True, interval=320)
        self.refresh_timer.timeout.connect(self.refresh_preview)
        self.chapter.currentIndexChanged.connect(self.refresh_preview)
        self.device.currentIndexChanged.connect(self.refresh_preview)
        self.tabs.currentChanged.connect(self.refresh_preview)
        self.font_preview.toggled.connect(self.refresh_preview)
        self.load_styles()
        self.load_fonts()
        self.update_apply()

    def build_styles(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        self.sheets = _combo('选择样式表')
        controls.addWidget(self.sheets, 1)
        self.add_sheet = QPushButton('新建全书样式')
        controls.addWidget(self.add_sheet)
        layout.addLayout(controls)
        self.css = QPlainTextEdit()
        self.css.setAccessibleName('CSS 源码')
        self.css.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.css.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.css, 1)
        self.syntax = _message()
        layout.addWidget(self.syntax)
        self.issue_list = QListWidget()
        self.issue_list.setAccessibleName('CSS 语法问题')
        self.issue_list.setMaximumHeight(100)
        self.issue_list.itemActivated.connect(self.go_to_issue)
        layout.addWidget(self.issue_list)
        self.sheets.currentIndexChanged.connect(self.open_stylesheet)
        self.css.textChanged.connect(self.edit_css)
        self.add_sheet.clicked.connect(self.new_stylesheet)
        self.tabs.addTab(page, '样式表')

    def build_fonts(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.fonts = _combo('书内字体')
        layout.addWidget(self.fonts)
        self.font_note = _message()
        layout.addWidget(self.font_note)
        self.import_button = QPushButton('导入字体并绑定到所选样式表…')
        self.import_button.clicked.connect(self.import_font)
        layout.addWidget(self.import_button)
        self.font_preview = QPushButton('显示字体试样', checkable=True)
        layout.addWidget(self.font_preview)
        self.subset_button = QPushButton('测算并启用导出子集…')
        self.subset_button.clicked.connect(self.change_subset)
        layout.addWidget(self.subset_button)
        layout.addWidget(_message('导出子集按全书文字保留字形。编辑工程始终保存原始字体。'))
        layout.addStretch()
        self.fonts.currentIndexChanged.connect(self.font_changed)
        self.tabs.addTab(page, '嵌入字体')

    def load_styles(self, selected=None):
        with QSignalBlocker(self.sheets):
            self.sheets.clear()
            if any(c.path not in self.book._epub_chapters for c in self.book.chapters):
                self.sheets.addItem('新建章节默认样式', None)
            for name, asset in self.book.assets.items():
                if asset.media_type == 'text/css':
                    self.sheets.addItem(name, name)
            self.sheets.setCurrentIndex(max(0, self.sheets.findData(selected)))
        self.open_stylesheet()

    def open_stylesheet(self):
        selected = self.sheets.currentIndex() >= 0
        path = self.sheets.currentData()
        self.css.setEnabled(selected)
        with QSignalBlocker(self.css):
            self.css.setPlainText(stylesheet_text(self.book, path) if selected else '')
        self.css.setPlaceholderText('点击「新建全书样式」开始编辑。' if not selected else '在此输入 CSS 规则。')
        self.show_issues(path)

    def edit_css(self):
        path = self.sheets.currentData()
        value = self.css.toPlainText()
        set_stylesheet(self.book, path, value)
        self.modified.add(path)
        self.refresh_timer.start()
        self.update_apply()

    def show_issues(self, path):
        self.issue_list.clear()
        problems = css_issues(self.css.toPlainText()) if self.css.isEnabled() else []
        if path in self.modified:
            self.issues[path] = problems
        for issue in problems:
            row = QListWidgetItem(f'第 {issue.line} 行，第 {issue.column} 列 · {issue.message}', self.issue_list)
            row.setData(Qt.ItemDataRole.UserRole, (issue.line, issue.column))
        self.issue_list.setVisible(bool(problems))
        self.syntax.setText(f'找到 {len(problems)} 处语法问题。双击可定位。' if problems else
                            'CSS 语法检查通过。' if self.css.isEnabled() else '')

    def go_to_issue(self, item):
        line, column = item.data(Qt.ItemDataRole.UserRole)
        block = self.css.document().findBlockByLineNumber(line - 1)
        if block.isValid():
            cursor = QTextCursor(block)
            cursor.setPosition(block.position() + min(column - 1, block.length() - 1))
            self.css.setTextCursor(cursor)
            self.css.setFocus()

    def new_stylesheet(self):
        try:
            path = add_linked_stylesheet(self.book)
        except (ValueError, etree.LxmlError) as error:
            self.status.setText(str(error))
            return
        self.load_styles(path)
        self.refresh_preview()
        self.css.setFocus()

    def load_fonts(self, selected=None):
        with QSignalBlocker(self.fonts):
            self.fonts.clear()
            for path, asset in self.book.assets.items():
                if resource_category(path, asset) == '字体':
                    try:
                        details = font_info(asset.data)
                    except ValueError as error:
                        label, details = Path(path).name, str(error)
                    else:
                        label = ' · '.join(filter(None, (details.family, details.style)))
                    self.fonts.addItem(label, path)
                    self.fonts.setItemData(self.fonts.count() - 1, details, FONT_INFO_ROLE)
            self.fonts.setCurrentIndex(max(0, self.fonts.findData(selected)))
        self.font_changed()

    def font_changed(self):
        path = self.fonts.currentData()
        self.font_preview.setEnabled(bool(path))
        self.subset_button.setEnabled(False)
        if not path:
            self.font_note.setText('此书稿暂无嵌入字体。')
        else:
            asset = self.book.assets[path]
            enabled = path in self.book.font_subsets
            self.subset_button.setText('关闭导出子集' if enabled else '测算并启用导出子集…')
            self.subset_button.setEnabled(enabled)
            info = self.fonts.currentData(FONT_INFO_ROLE)
            if isinstance(info, str):
                self.font_note.setText(info)
                self.font_preview.setChecked(False)
                self.font_preview.setEnabled(False)
            else:
                size = len(asset.data) / 1024
                self.subset_button.setEnabled(enabled or info.subsettable)
                state = '导出子集已启用' if enabled else '保留完整字体' if info.subsettable else '此字体的嵌入标记禁止子集化'
                self.font_note.setText(f'{info.family} · {info.format.upper()} · {size:,.1f} KB\n{state}')
        self.refresh_preview()

    def import_font(self):
        chosen, _ = QFileDialog.getOpenFileName(self, '选择可嵌入的字体', '', '字体 (*.ttf *.otf *.woff *.woff2)')
        if not chosen:
            return
        rights = QMessageBox.question(self, '字体嵌入许可', '确认你有权把这款字体嵌入书稿并随电子书分发。')
        if rights != QMessageBox.StandardButton.Yes:
            return
        draft = deepcopy(self.book)
        sheet = self.sheets.currentData()
        try:
            if self.sheets.currentIndex() < 0:
                sheet = add_linked_stylesheet(draft)
            path, info = bind_font(draft, Path(chosen).read_bytes(), sheet)
        except (OSError, ValueError, etree.LxmlError) as error:
            self.status.setText(str(error))
            return
        self.book = draft
        self.modified.add(sheet)
        self.load_styles(sheet)
        self.load_fonts(path)
        self.font_preview.setChecked(True)
        self.status.setText(f'已绑定「{info.family}」到所选样式表。')
        self.update_apply()

    def change_subset(self):
        path = self.fonts.currentData()
        if not path:
            return
        if path in self.book.font_subsets:
            self.book.font_subsets.remove(path)
        else:
            data = self.book.assets[path].data
            self.status.setText('正在测算当前书稿的子集体积…')
            self.status.repaint()
            try:
                measured = subset_font(data, book_characters(self.book))
            except (ValueError, etree.LxmlError) as error:
                self.status.setText(str(error))
                return
            before, after = len(data) / 1024, len(measured) / 1024
            rights = QMessageBox.question(
                self, '启用导出字体子集', f'按当前书稿测算：{before:,.1f} KB → {after:,.1f} KB。\n'
                '确认你有权对子集化后的字体随书分发。启用后，每次导出会按当时的书稿重新生成。',
            )
            if rights != QMessageBox.StandardButton.Yes:
                self.status.clear()
                return
            self.book.font_subsets.append(path)
            self.status.setText(f'子集测算体积 {after:,.1f} KB。')
        self.font_changed()
        self.update_apply()

    def refresh_preview(self):
        self.show_issues(self.sheets.currentData())
        self.update_apply()
        face = self.fonts.currentData() if self.tabs.currentIndex() == 1 and self.font_preview.isChecked() else None
        try:
            self.preview.show_book(self.book, self.book.chapters[self.chapter.currentIndex()],
                                   self.device.currentData(), face)
        except (OSError, ValueError, etree.LxmlError) as error:
            self.preview_note.setText(f'无法预览当前章节。{error}')
        else:
            self.preview_note.setText('所选字体的试样' if face else '所选章节及其链接的样式表')

    def update_apply(self):
        self.apply_button.setEnabled(self.book != self.original and not any(self.issues.values())
                                     and not self.refresh_timer.isActive())


def show_typography(studio):
    original = studio.book
    if not original.chapters:
        studio.statusBar().showMessage('先打开或新建有章节的书稿。')
        return
    with TemporaryDirectory(prefix='epub-style-') as directory:
        dialog = TypographyDialog(studio, original, studio.active, directory)
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            dialog.preview.finish()
            dialog.deleteLater()
        draft = dialog.book
    if not accepted or draft == original:
        return
    revision = studio.edit_revision

    def apply(_checkpoint):
        if studio.book is not original or studio.edit_revision != revision:
            studio.error('书稿已有变化，请重新预览 CSS 与字体更改。')
            return
        studio.adopt_book_edit(draft)
        studio.statusBar().showMessage('已应用 CSS 与字体更改，并保存修改前检查点。')

    studio.start_task('正在保存修改前检查点…',
                      lambda: studio.workspace.save_checkpoint(original, 'CSS 与字体更改前'), apply)
