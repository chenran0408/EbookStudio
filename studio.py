"""Windows desktop workspace for creating and editing local ebooks."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import posixpath
import re
import secrets
import sqlite3
import sys
from urllib.parse import unquote, urlsplit

from lxml import etree, html
from PySide6.QtCore import QLibraryInfo, QObject, QSignalBlocker, Qt, QThread, QTimer, QTranslator, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence, QShortcut
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTabBar, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from book_info import BookInfoDialog
from book_library import BookLibrary
from book_library_dialog import BookLibraryDialog
from book_library_search_dialog import LibraryTextDialog
from book_links import map_css_references
from book_quality_dialog import show_book_quality
from book_resources_dialog import show_book_resources
from book_revision_dialogs import show_book_checkpoints, show_book_replace
from book_search import select_search_result
from book_spelling_dialog import show_spelling
from book_structure_dialog import show_book_structure
from book_typography import DEFAULT_STYLE_PATH, FONT_TYPES, decode_stylesheet
from book_typography_dialog import show_typography
from ebook_core import Asset, Book, Chapter, asset_href, resolve_href
from epub_compare_dialog import show_epub_compare
from epub_validation_dialog import EpubCheckDialog
from first_book import first_book
from file_drops import FileDrops
from inline_toc import show_inline_toc
import capture
from capture_review import CaptureProfilesDialog, WebUpdateDialog
from capture_update import CaptureProfile, apply_updates, collect_updates, profiles_for, remember_profile
from command_palette import CommandPalette
import conversion
from ocr_batch import OcrBatchStore
from ocr_batch_dialog import OcrBatchDialog
from ocr_import import IMAGE_TYPES as SCAN_IMAGE_TYPES
from ocr_review import show_ocr_review
from project_shelf import ProjectShelf
from rule_group_dialog import show_rule_groups
from studio_dialogs import ExportDialog, ScanDialog
from studio_operations import (
    LABELS, capture_pages, comic_pages, convert_files, export_book_with_report, inspect_epub, read_book, safe_name,
)
from studio_theme import STYLE
from workspace import Workspace
from workspace_tools import conversion_stem, converter_readiness, ocr_readiness, plan_destinations


APP_ROOT = Path(__file__).resolve().parent
FILE_FILTER = (
    "书籍与稿件 (*.bookproj *.epub *.azw3 *.azw *.azw4 *.mobi *.kepub *.fb2 *.fbz *.fb2.zip "
    "*.pdf *.docx *.odt *.rtf *.md *.markdown *.html *.htm *.txt *.cbz *.cbr *.chm *.djvu);;所有文件 (*)"
)

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".svg": "image/svg+xml"}

VIEW_MODES = ("rich", "source", "preview")


class Task(QThread):
    result = Signal(object)
    failure = Signal(str)
    progress = Signal(int, int, str)

    def __init__(self, function, parent, *, reports_progress=False):
        super().__init__(parent)
        self.function = function
        self.reports_progress = reports_progress

    def run(self):
        try:
            result = self.function(self.progress.emit) if self.reports_progress else self.function()
        except Exception as exc:
            self.failure.emit(str(exc) or type(exc).__name__)
        else:
            self.result.emit(result)


class EditorBridge(QObject):
    loaded = Signal()
    edited = Signal(str, str)

    @Slot()
    def ready(self):
        self.loaded.emit()

    @Slot(str, str)
    def changed(self, token, document):
        self.edited.emit(token, document)


# Controls match the editor's schema. Rich editing is available only where
# re-serializing a body will preserve its structure and attributes.
RICH_ELEMENTS = {
    name: set() for name in ("p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "b", "strong",
                             "i", "em", "s", "del", "u", "sub", "sup", "ul", "li", "blockquote",
                             "pre", "code", "table", "thead", "tbody", "tr", "main", "article", "section",
                             "footer", "div", "figure", "figcaption")
}
RICH_ELEMENTS |= {
    "a": {"href", "title", "target", "rel"}, "img": {"src", "alt", "title"}, "ol": {"start"},
    "td": {"colspan", "rowspan", "colwidth"}, "th": {"colspan", "rowspan", "colwidth"},
}
XHTML = "http://www.w3.org/1999/xhtml"


def source_tree(document: str):
    if not re.search(r"<html(?:\s|>)", document, re.I):
        return None
    return etree.fromstring(document.encode("utf-8"), parser=etree.XMLParser(resolve_entities=False, no_network=True))


def can_edit_rich(document: str) -> bool:
    try:
        original = source_tree(document)
        if original is not None:
            body = original.find("{*}body")
            if body is None or body.attrib:
                return False
            elements = body.iterdescendants()
        else:
            elements = html.fragment_fromstring(document or "<p></p>", create_parent=True).iterdescendants()
        for element in elements:
            if not isinstance(element.tag, str):
                return False
            name = etree.QName(element).localname
            attrs = {key for key in element.attrib if key not in {"xmlns", "xmlns:epub"}}
            if name not in RICH_ELEMENTS or attrs - RICH_ELEMENTS[name]:
                return False
        return True
    except (etree.XMLSyntaxError, etree.ParserError, ValueError):
        return False


def edit_body(document: str, content: str) -> str:
    original = source_tree(document)
    if original is None:
        return content
    body = original.find("{*}body")
    replacement = html.fragment_fromstring(content, create_parent=True)
    for old in list(body):
        body.remove(old)
    body.text = replacement.text
    for node in replacement.iterdescendants():
        node.tag = f"{{{XHTML}}}{node.tag}"
    body.extend(replacement)
    return etree.tostring(original.getroottree(), encoding="utf-8", xml_declaration=True).decode("utf-8")


def book_assets(book: Book, chapter: Chapter, *, preview: bool = True) -> tuple[dict[str, str], str]:
    document = html.fromstring(chapter.html if chapter.html.strip() else "<p></p>")
    mapping = {}
    styles = []
    source = book._epub_members.get(chapter.path)

    def add_style(css, base):
        if not preview:
            return

        def embed_font(href):
            mime = FONT_TYPES.get(posixpath.splitext(urlsplit(href).path)[1][1:].lower())
            if mime is None:
                return href
            try:
                asset = book.assets.get(resolve_href(base, href))
            except ValueError:
                return href
            if asset is None or mime is None:
                return href
            encoded = base64.b64encode(asset.data).decode("ascii")
            return f"data:{mime};base64,{encoded}"

        styles.append(map_css_references(css, embed_font))

    if source:
        head = html.fromstring(source).find("head")
        if head is not None:
            for link in head.xpath(".//link[@href]"):
                key = resolve_href(chapter.path, link.get("href"))
                asset = book.assets.get(key)
                if asset is not None and asset.media_type == "text/css":
                    add_style(decode_stylesheet(asset.data), key)
            for style in head.xpath(".//style"):
                add_style(style.text or "", chapter.path)
    folder = posixpath.dirname(chapter.path or "")
    for element in document.xpath("//img[@src] | //link[@href] | //style"):
        value = element.get("src") if element.tag == "img" else element.get("href")
        if element.tag == "style":
            add_style(element.text or "", chapter.path)
            continue
        url = urlsplit(value)
        if url.scheme or url.netloc:
            continue
        original = unquote(url.path)
        key = posixpath.normpath(posixpath.join(folder, original))
        asset = book.assets.get(key) or book.assets.get(original)
        if asset is None:
            continue
        if element.tag == "link" and asset.media_type == "text/css":
            add_style(decode_stylesheet(asset.data), key if key in book.assets else original)
        elif element.tag == "img" and asset.media_type in IMAGE_TYPES.values():
            payload = base64.b64encode(asset.data).decode("ascii")
            mapping[value] = f"data:{asset.media_type};base64,{payload}"
    if not source:
        add_style(book.stylesheet, DEFAULT_STYLE_PATH)
    for key in book.linked_stylesheets:
        if (asset := book.assets.get(key)) is not None:
            add_style(decode_stylesheet(asset.data), key)
    return mapping, "\n".join(styles)


def add_asset(book: Book, path: Path, chapter: Chapter | None = None):
    mime = IMAGE_TYPES.get(path.suffix.lower())
    if mime is None:
        raise ValueError("请选择 JPEG、PNG、GIF 或 SVG 图片。")
    data = path.read_bytes()
    relative = f"images/{sha256(data).hexdigest()[:20]}{path.suffix.lower()}"
    folder = posixpath.dirname(chapter.path or "") if chapter else ""
    key = posixpath.join(folder, relative)
    book.assets.setdefault(key, Asset(data, mime))
    return relative, key, mime, data


class CaptureDialog(QDialog):
    def __init__(self, fetch, parent, *, profile=None, configure=False):
        super().__init__(parent)
        self.setWindowTitle("从网页采集")
        self.resize(650, 420)
        self.fetch = fetch
        self.task = None
        self.profile = profile
        self.configure = configure
        self.skipped = set(profile.skipped) if profile else set()
        self.tabs = QTabWidget()
        articles = QWidget()
        direct = QVBoxLayout(articles)
        direct.addWidget(QLabel("粘贴要导入的文章网址，每行一个。"))
        self.urls = QPlainTextEdit()
        self.urls.setAccessibleName("文章网址")
        self.urls.setPlaceholderText("https://example.com/article")
        direct.addWidget(self.urls, 1)
        self.url_notice = QLabel(objectName="muted")
        self.url_notice.setWordWrap(True)
        direct.addWidget(self.url_notice)
        self.tabs.addTab(articles, "文章网址")

        directory = QWidget()
        listing = QVBoxLayout(directory)
        row = QHBoxLayout()
        self.directory = QLineEdit()
        self.directory.setAccessibleName("目录页网址")
        self.directory.setPlaceholderText("粘贴目录页网址")
        self.directory.returnPressed.connect(self.find)
        row.addWidget(self.directory, 1)
        self.find_button = QPushButton("查找文章")
        self.find_button.clicked.connect(self.find)
        row.addWidget(self.find_button)
        listing.addLayout(row)
        self.notice = QLabel("先查找目录页中的文章，再选择要导入的章节。", objectName="muted")
        self.notice.setWordWrap(True)
        listing.addWidget(self.notice)
        self.candidates = QListWidget()
        self.candidates.setAccessibleName("目录候选章节")
        listing.addWidget(self.candidates, 1)
        selection = QHBoxLayout()
        for label, check in (("全选", True), ("取消勾选", False)):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, value=check: self.check_candidates(value))
            selection.addWidget(button)
        selection.addStretch()
        listing.addLayout(selection)
        self.tabs.addTab(directory, "从目录选文章")

        self.rendered = QCheckBox("网页内容加载不全时，使用浏览器渲染")
        self.options = QPushButton("提取规则（可选）", checkable=True)
        rules = QWidget()
        form = QFormLayout(rules)
        form.setContentsMargins(0, 0, 0, 0)
        self.article_xpath = QLineEdit()
        self.article_xpath.setPlaceholderText("例如 //main//article")
        self.article_xpath.setAccessibleName("正文 XPath")
        form.addRow("正文 XPath", self.article_xpath)
        self.directory_xpath = QLineEdit()
        self.directory_xpath.setPlaceholderText("例如 //div[@id='contents']")
        self.directory_xpath.setAccessibleName("目录 XPath")
        form.addRow("目录 XPath", self.directory_xpath)
        self.rule_notice = QLabel(objectName="muted")
        self.rule_notice.setWordWrap(True)
        form.addRow(self.rule_notice)
        rules.hide()
        self.options.toggled.connect(rules.setVisible)
        self.save_selection = QCheckBox("保存规则和文章选择，供增量更新")
        self.save_selection.setChecked(configure)
        self.save_selection.setVisible(not configure)
        self.actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.actions.accepted.connect(self.accept)
        self.actions.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.options, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(rules)
        layout.addWidget(self.rendered)
        layout.addWidget(self.save_selection)
        layout.addWidget(self.actions)
        self.tabs.currentChanged.connect(self.update_actions)
        self.urls.textChanged.connect(self.update_actions)
        self.directory.textChanged.connect(self.directory_edited)
        self.directory_xpath.textChanged.connect(self.directory_edited)
        self.candidates.itemChanged.connect(self.update_actions)
        if profile is not None:
            self.rendered.setChecked(profile.rendered)
            self.article_xpath.setText(profile.article_xpath)
            self.directory_xpath.setText(profile.directory_xpath)
            self.options.setChecked(bool(profile.article_xpath or profile.directory_xpath))
            if profile.directory:
                self.tabs.setCurrentIndex(1)
                self.directory.setText(profile.directory)
                self.found([capture.Candidate(url, url) for url in [*profile.urls, *profile.skipped]])
                self.notice.setText("已载入保存的选择。点查找文章可更新当前目录。")
            else:
                self.urls.setPlainText("\n".join(profile.urls))
        self.update_actions()

    def directory_edited(self, _text):
        self.candidates.clear()
        self.notice.setText("先查找目录页中的文章，再选择要导入的章节。")
        self.update_actions()

    def update_actions(self, _value=None):
        urls = self.selected_urls()
        self.url_notice.setText(f"{len(urls)} 篇待导入" if urls and self.tabs.currentIndex() == 0 else "")
        button = self.actions.button(QDialogButtonBox.StandardButton.Ok)
        button.setEnabled(bool(urls) and not self.task)
        button.setText("保存规则" if self.configure else f"导入 {len(urls)} 篇" if urls else "导入")
        self.find_button.setEnabled(bool(self.directory.text().strip()) and not self.task)

    def check_candidates(self, checked):
        with QSignalBlocker(self.candidates):
            for index in range(self.candidates.count()):
                self.candidates.item(index).setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
        self.update_actions()

    def find(self):
        url = self.directory.text().strip()
        if not url or self.task:
            return
        try:
            capture._web_url(url)
        except ValueError as exc:
            self.notice.setText(str(exc))
            return
        self.notice.setText("正在识别章节…")
        rendered = self.rendered.isChecked()
        if self.candidates.count():
            self.skipped = set(self.unselected_urls())
        selector = self.directory_xpath.text().strip()
        options = {"directory_xpath": selector} if selector else {}
        self.task = Task(lambda: self.fetch(url, rendered=rendered, **options), self)
        self.task.result.connect(self.found)
        self.task.failure.connect(self.notice.setText)
        self.task.finished.connect(self.clear_task)
        self.actions.setEnabled(False)
        self.tabs.setEnabled(False)
        self.rendered.setEnabled(False)
        self.directory_xpath.setEnabled(False)
        self.task.start()

    def clear_task(self):
        self.task.deleteLater()
        self.task = None
        self.actions.setEnabled(True)
        self.tabs.setEnabled(True)
        self.rendered.setEnabled(True)
        self.directory_xpath.setEnabled(True)
        self.update_actions()

    def found(self, values):
        self.candidates.clear()
        for candidate in values:
            item = QListWidgetItem(candidate.title)
            item.setToolTip(candidate.url)
            item.setData(Qt.ItemDataRole.UserRole, candidate.url)
            state = Qt.CheckState.Unchecked if capture.web_url_key(candidate.url) in self.skipped else Qt.CheckState.Checked
            item.setCheckState(state)
            self.candidates.addItem(item)
        self.notice.setText(f"找到 {len(values)} 个候选链接，请核对后勾选。")
        self.update_actions()

    def selected_urls(self):
        if self.tabs.currentIndex() == 0:
            urls = [line.strip() for line in self.urls.toPlainText().splitlines() if line.strip()]
        else:
            urls = [
                item.data(Qt.ItemDataRole.UserRole) for index in range(self.candidates.count())
                if (item := self.candidates.item(index)).checkState() == Qt.CheckState.Checked
            ]
        return list(dict.fromkeys(urls))

    def unselected_urls(self):
        return [capture.web_url_key(item.data(Qt.ItemDataRole.UserRole))
                for index in range(self.candidates.count())
                if (item := self.candidates.item(index)).checkState() != Qt.CheckState.Checked]

    def capture_profile(self):
        from_directory = self.tabs.currentIndex() == 1
        options = {'identifier': self.profile.identifier} if self.profile else {}
        return CaptureProfile(
            self.selected_urls(),
            directory=self.directory.text().strip() if from_directory else '',
            skipped=self.unselected_urls() if from_directory else [],
            rendered=self.rendered.isChecked(),
            article_xpath=self.article_xpath.text().strip(),
            directory_xpath=self.directory_xpath.text().strip(),
            **options,
        )

    def accept(self):
        urls = self.selected_urls()
        if urls and not self.task:
            for url in urls:
                try:
                    capture._web_url(url)
                except ValueError:
                    self.url_notice.setText("有网址格式不正确，请检查是否以 http:// 或 https:// 开头。")
                    self.urls.setFocus()
                    return
            for field in (self.article_xpath, self.directory_xpath):
                if field.text().strip():
                    try:
                        etree.XPath(field.text().strip())
                    except etree.XPathError:
                        self.options.setChecked(True)
                        self.rule_notice.setText("XPath 语法不正确，请修改后重试。")
                        field.setFocus()
                        return
            super().accept()

    def reject(self):
        if self.task is None:
            super().reject()


class BookTitleLabel(QLabel):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextFormat(Qt.TextFormat.PlainText)

    def set_title(self, text):
        if text != self.full_text:
            self.full_text = text
            self.setAccessibleName(text)
            self.setToolTip(text)
            self.fit_title()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_title()

    def fit_title(self):
        self.setText(self.fontMetrics().elidedText(
            self.full_text, Qt.TextElideMode.ElideRight, self.contentsRect().width(),
        ))


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.book = Book()
        self.project = None
        self.dirty = False
        self.active = -1
        self.token = ""
        self.source_document = None
        self.task = None
        self.web_ready = False
        self.preview_fragment = ""
        self.workspace = Workspace()
        self.book_started = False
        self.showing_start = True
        self.collapsed_paths = set()
        self.preferred_mode = "rich"
        self.recovered_draft = None
        self.draft_task = None
        self.edit_revision = 0
        self.last_output = None
        self.draft_timer = QTimer(self)
        self.draft_timer.setSingleShot(True)
        self.draft_timer.setInterval(5000)
        self.draft_timer.timeout.connect(self.autosave_draft)
        self.resize(1320, 840)
        self.setMinimumSize(780, 550)
        self.setStyleSheet(STYLE)
        self.build()
        self.reload(0)

    def build(self):
        layout_root = QWidget()
        self.setCentralWidget(layout_root)
        layout = QVBoxLayout(layout_root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QWidget(objectName="header")
        header.setMinimumHeight(62)
        bar = QHBoxLayout(header)
        bar.setContentsMargins(20, 9, 18, 9)
        bar.setSpacing(18)
        identity = QVBoxLayout()
        identity.setSpacing(1)
        self.workspace_title = BookTitleLabel(objectName="brand")
        identity.addWidget(self.workspace_title)
        self.project_state = QLabel(objectName="projectState")
        identity.addWidget(self.project_state)
        bar.addLayout(identity, 1)
        self.command_button = QPushButton("查找命令")
        self.command_button.setToolTip("查找命令（Ctrl+Shift+P）")
        bar.addWidget(self.command_button)
        self.project_actions = QWidget()
        actions = QHBoxLayout(self.project_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(7)
        self.import_button = QPushButton("导入内容")
        source_menu = QMenu(self.import_button)
        source_menu.addAction("从文件追加章节…", self.import_files)
        source_menu.addAction("从网页追加章节…", self.capture_urls)
        source_menu.addAction("网页增量采集…", self.open_web_profiles)
        self.import_button.setMenu(source_menu)
        actions.addWidget(self.import_button)
        self.save_button = QPushButton("保存工程")
        self.save_button.clicked.connect(self.save)
        actions.addWidget(self.save_button)
        self.quality_button = QPushButton("检查书稿")
        self.quality_button.clicked.connect(self.check_book_quality)
        actions.addWidget(self.quality_button)
        self.export_button = QPushButton("导出", objectName="primary")
        self.export_button.clicked.connect(self.export)
        actions.addWidget(self.export_button)
        bar.addWidget(self.project_actions)
        layout.addWidget(header)
        self.header = header

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        layout.addLayout(body, 1)
        self.navigation = QWidget(objectName="navigation")
        self.navigation.setFixedWidth(84)
        nav = QVBoxLayout(self.navigation)
        nav.setContentsMargins(8, 14, 8, 12)
        nav.setSpacing(7)
        self.nav_start = QPushButton("开始", checkable=True)
        self.nav_start.clicked.connect(self.show_start)
        nav.addWidget(self.nav_start)
        self.nav_book = QPushButton("书稿", checkable=True)
        self.nav_book.clicked.connect(self.show_book)
        nav.addWidget(self.nav_book)
        nav.addStretch()
        tools = QPushButton("本机工具")
        tools.setToolTip("检查本机 OCR 模型和转换工具")
        tools.clicked.connect(self.show_tools)
        nav.addWidget(tools)
        body.addWidget(self.navigation)
        self.screens = QStackedWidget()
        body.addWidget(self.screens, 1)
        self.welcome = self.build_welcome()
        self.screens.addWidget(self.welcome)
        split = QSplitter(objectName="workspaceSplit")
        split.setChildrenCollapsible(False)
        self.screens.addWidget(split)
        self.split = split
        sidebar = QWidget(objectName="sidebar")
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 16, 14, 14)
        side.setSpacing(10)
        overview = QHBoxLayout()
        self.chapter_count = QLabel("章节目录", objectName="section")
        overview.addWidget(self.chapter_count, 1)
        book_info = QPushButton("书籍资料")
        book_info.clicked.connect(self.edit_book_info)
        overview.addWidget(book_info)
        side.addLayout(overview)
        self.chapter_filter = QLineEdit()
        self.chapter_filter.setClearButtonEnabled(True)
        self.chapter_filter.setAccessibleName("搜索章节标题")
        self.chapter_filter.setPlaceholderText("搜索章节标题")
        self.chapter_filter.setToolTip("Ctrl+P 查找章节。Enter 跳转，Esc 清除。")
        self.chapter_filter.textChanged.connect(self.filter_chapters)
        self.chapter_filter.returnPressed.connect(self.jump_to_match)
        clear_filter = QShortcut(QKeySequence("Esc"), self.chapter_filter)
        clear_filter.setContext(Qt.ShortcutContext.WidgetShortcut)
        clear_filter.activated.connect(self.clear_chapter_filter)
        search = QHBoxLayout()
        search.setSpacing(6)
        search.addWidget(self.chapter_filter, 1)
        search_text = QPushButton("搜索正文")
        search_text.setToolTip("在整本书稿中搜索正文（Ctrl+Shift+F）")
        search_text.clicked.connect(self.search_book_text)
        search.addWidget(search_text)
        side.addLayout(search)
        self.no_matches = QLabel("没有匹配的章节", objectName="muted")
        self.no_matches.hide()
        side.addWidget(self.no_matches)
        self.chapters = QTreeWidget(objectName="chapterTree")
        self.chapters.setAccessibleName("章节目录")
        self.chapters.setHeaderHidden(True)
        self.chapters.setColumnCount(1)
        self.chapters.setIndentation(18)
        self.chapters.currentItemChanged.connect(
            lambda current, _old: self.select(current.data(0, Qt.ItemDataRole.UserRole) if current else -1)
        )
        path_role = int(Qt.ItemDataRole.UserRole) + 1
        self.chapters.itemCollapsed.connect(lambda item: self.collapsed_paths.add(item.data(0, path_role)))
        self.chapters.itemExpanded.connect(lambda item: self.collapsed_paths.discard(item.data(0, path_role)))
        side.addWidget(self.chapters, 1)
        operations = QHBoxLayout()
        operations.setSpacing(5)
        self.chapter_operations = {}
        for label, callback in (
            ("新增", self.add_chapter), ("上移", lambda: self.move(-1)), ("下移", lambda: self.move(1)),
            ("层级", self.change_level), ("删除", self.remove),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            operations.addWidget(button)
            self.chapter_operations[label] = button
        side.addLayout(operations)
        self.sidebar = sidebar
        split.addWidget(sidebar)

        writing = QWidget(objectName="writing")
        main = QVBoxLayout(writing)
        main.setContentsMargins(16, 16, 16, 14)
        main.setSpacing(10)
        chapter_form = QHBoxLayout()
        label = QLabel("章节标题")
        self.chapter_title = QLineEdit(objectName="chapterTitle")
        self.chapter_title.textEdited.connect(self.rename)
        label.setBuddy(self.chapter_title)
        chapter_form.addWidget(label)
        chapter_form.addWidget(self.chapter_title, 1)
        self.insert_picture = QPushButton("插入图片")
        self.insert_picture.clicked.connect(self.add_image)
        chapter_form.addWidget(self.insert_picture)
        main.addLayout(chapter_form)
        row = QHBoxLayout()
        self.mode = QTabBar(objectName="documentModes")
        self.mode.setAccessibleName("章节编辑方式")
        self.mode.setDrawBase(False)
        self.mode.setExpanding(False)
        for label in ("正文", "源码", "预览"):
            self.mode.addTab(label)
        self.mode.currentChanged.connect(self.mode_changed)
        row.addWidget(self.mode)
        row.addStretch()
        self.split_preview = QPushButton("实时预览", checkable=True)
        self.split_preview.toggled.connect(self.open_view)
        row.addWidget(self.split_preview)
        self.device = QComboBox()
        self.device.setAccessibleName("预览阅读设备")
        self.device.addItem("平板预览", 760)
        self.device.addItem("手机预览", 390)
        self.device.currentIndexChanged.connect(self.open_view)
        row.addWidget(self.device)
        self.mode_hint = QLabel("", objectName="muted")
        self.mode_hint.setWordWrap(True)
        main.addLayout(row)
        main.addWidget(self.mode_hint)

        self.views = QStackedWidget()
        main.addWidget(self.views, 1)
        empty = QWidget(objectName="emptyEditor")
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch()
        empty_label = QLabel("书稿还没有章节", objectName="section")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_label)
        add_first = QPushButton("添加章节")
        add_first.clicked.connect(self.add_chapter)
        empty_layout.addWidget(add_first, alignment=Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch()
        self.views.addWidget(empty)
        self.source = QPlainTextEdit()
        self.source.setAccessibleName("XHTML 源码")
        source_font = QFont()
        source_font.setFamilies(["Cascadia Code", "Consolas", "Microsoft YaHei UI"])
        source_font.setPointSize(10)
        self.source.setFont(source_font)
        self.source.textChanged.connect(self.source_changed)
        self.views.addWidget(self.source)
        self.web = QWebEngineView()
        self.web.setAccessibleName("正文编辑与预览")
        self.bridge = EditorBridge(self)
        self.bridge.loaded.connect(self.web_loaded)
        self.bridge.edited.connect(self.editor_changed)
        self.channel = QWebChannel(self.web.page())
        self.channel.registerObject("studio", self.bridge)
        self.web.page().setWebChannel(self.channel)
        self.web.load(QUrl.fromLocalFile(str(APP_ROOT / "web" / "index.html")))
        self.views.addWidget(self.web)
        split.addWidget(writing)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([276, 960])
        self.writing = writing

        file_menu = self.menuBar().addMenu("文件")
        for label, standard, function in (
            ("新建书籍", QKeySequence.StandardKey.New, self.new),
            ("打开书籍或稿件", QKeySequence.StandardKey.Open, self.open),
            ("保存工程", QKeySequence.StandardKey.Save, self.save),
            ("工程另存为", QKeySequence.StandardKey.SaveAs, lambda: self.save(as_new=True)),
        ):
            action = QAction(label, self)
            action.setShortcut(standard)
            action.triggered.connect(function)
            file_menu.addAction(action)
        sample_action = file_menu.addAction("用示例制作第一本书", self.open_first_book)
        sample_action.setProperty("commandKeywords", "入门 教程 样书 EPUB")
        self.recent_menu = file_menu.addMenu("最近打开")
        self.recent_menu.aboutToShow.connect(self.refresh_recent_menu)
        library_action = file_menu.addAction("书库…", self.open_library)
        library_action.setProperty("commandKeywords", "作者 标签 书架 本机书籍")
        full_text_action = file_menu.addAction("跨书全文检索…", lambda: self.open_library(full_text=True))
        full_text_action.setProperty("commandKeywords", "书库 正文 句子 期号 章节")
        file_menu.addAction("恢复草稿…", self.choose_recovery)
        revision_menu = self.menuBar().addMenu("校对")
        self.check_book_action = revision_menu.addAction("检查书稿…", self.check_book_quality)
        self.replace_text_action = revision_menu.addAction("整书查找替换…", lambda: show_book_replace(self))
        self.replace_text_action.setShortcut(QKeySequence("Ctrl+Shift+H"))
        self.rule_groups_action = revision_menu.addAction("顺序校对规则组…", lambda: show_rule_groups(self))
        self.rule_groups_action.setProperty("commandKeywords", "OCR 批量 替换 顺序 组合 纠错")
        self.spelling_action = revision_menu.addAction("拼写检查与个人词典…", lambda: show_spelling(self))
        self.spelling_action.setProperty("commandKeywords", "拼写 字典 词典 OCR 错词 专名")
        self.checkpoints_action = revision_menu.addAction("检查点…", lambda: show_book_checkpoints(self))
        compare_action = revision_menu.addAction("比较两份 EPUB…", lambda: show_epub_compare(self))
        compare_action.setProperty("commandKeywords", "电子书 对比 差异 原版 修订版 版本")
        self.ocr_review_action = revision_menu.addAction("OCR 原图对照…", self.review_current_scan)
        view_menu = self.menuBar().addMenu("视图")
        view_menu.addAction("开始页", self.show_start)
        self.show_book_action = view_menu.addAction("返回书稿", self.show_book)
        jump = view_menu.addAction("搜索章节标题", self.focus_chapter_filter)
        jump.setShortcut(QKeySequence("Ctrl+P"))
        self.search_text_action = view_menu.addAction("搜索正文", self.search_book_text)
        self.search_text_action.setShortcut(QKeySequence("Ctrl+Shift+F"))
        convert_menu = self.menuBar().addMenu("出书")
        convert_menu.addAction("导入章节文件", self.import_files)
        scan_action = convert_menu.addAction("识别扫描 PDF 或图片", self.import_scan)
        scan_action.setProperty("commandKeywords", "OCR 扫描件 照片 文字识别")
        convert_menu.addAction("OCR 批次任务…", self.manage_ocr_batches)
        convert_menu.addAction("采集网页…", self.capture_urls)
        self.book_info_action = convert_menu.addAction("书籍资料…", self.edit_book_info)
        self.structure_action = convert_menu.addAction("目录和章节管理…", self.manage_book_structure)
        self.inline_toc_action = convert_menu.addAction("生成或更新书内目录页…", lambda: show_inline_toc(self))
        self.inline_toc_action.setProperty("commandKeywords", "书内目录 正文目录 可翻阅目录")
        self.resources_action = convert_menu.addAction("书稿资源…", self.manage_book_resources)
        self.web_update_action = convert_menu.addAction("网页增量采集…", self.open_web_profiles)
        typography_action = convert_menu.addAction("CSS 与字体…", self.edit_stylesheet)
        typography_action.setProperty("commandKeywords", "样式表 排版 字体子集")
        convert_menu.addAction("批量转换格式", self.batch_convert)
        self.export_action = convert_menu.addAction("导出 EPUB 或其他格式", self.export)
        self.export_action.setProperty("commandKeywords", "PDF DOCX TXT HTML")
        check_epub_action = convert_menu.addAction("检查 EPUB 文件", self.check_epub)
        check_epub_action.setProperty("commandKeywords", "EPUBCheck 校验 验证")
        tools_menu = self.menuBar().addMenu("工具")
        tools_menu.addAction("检查本机工具", self.show_tools)
        tools_menu.addAction("Agent 接入说明", lambda: self.open_document("Agent接入.md"))
        tools_menu.addAction("第三方软件与许可", lambda: self.open_document("THIRD_PARTY.md"))
        self.palette_commands = tuple(
            (menu.title(), action)
            for menu in (file_menu, revision_menu, view_menu, convert_menu, tools_menu)
            for action in menu.actions()
            if not action.isSeparator() and action.menu() is None
        )
        palette_action = view_menu.addAction("查找命令…", self.open_command_palette)
        palette_action.setShortcut(QKeySequence("Ctrl+Shift+P"))
        self.command_button.clicked.connect(palette_action.trigger)
        self.file_drops = FileDrops(self, lambda: self.task is None, self.receive_files)
        self.task_progress = QProgressBar()
        self.task_progress.setAccessibleName("当前任务进度")
        self.task_progress.setFixedWidth(135)
        self.task_progress.setFormat("%v / %m")
        self.statusBar().addPermanentWidget(self.task_progress)
        self.task_progress.hide()
        self.output_button = QPushButton("打开输出位置")
        self.output_button.clicked.connect(self.open_output_folder)
        self.statusBar().addPermanentWidget(self.output_button)
        self.output_button.hide()
        self.last_export_report = None
        self.export_report_button = QPushButton("导出校验报告")
        self.export_report_button.clicked.connect(self.show_export_report)
        self.statusBar().addPermanentWidget(self.export_report_button)
        self.export_report_button.hide()
        self.statusBar().showMessage("新建书籍、打开文件，或直接把文件拖到这里。")
        self.refresh_welcome()

    def open_command_palette(self):
        if self.task is not None:
            return
        dialog = CommandPalette(self, self.palette_commands)
        dialog.move(self.geometry().center().x() - dialog.width() // 2, self.geometry().top() + 56)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.command is not None:
            dialog.command.trigger()
        dialog.deleteLater()

    def build_welcome(self):
        scroll = QScrollArea(objectName="welcomeScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget(objectName="welcome")
        scroll.setWidget(page)
        outer = QVBoxLayout(page)
        self.start_outer = outer
        content = QWidget()
        content.setMaximumWidth(1030)
        blocks = QVBoxLayout(content)
        blocks.setContentsMargins(0, 0, 0, 0)
        blocks.setSpacing(15)
        centered = QHBoxLayout()
        centered.addStretch(1)
        centered.addWidget(content, 18)
        centered.addStretch(1)
        outer.addLayout(centered)
        outer.addStretch()

        start_heading = QHBoxLayout()
        start_heading.addWidget(QLabel("开始制作电子书", objectName="startTitle"), 1)
        sample = QPushButton("用示例制作第一本书")
        sample.setToolTip("打开可编辑的中文示例，练习保存工程和导出 EPUB")
        sample.clicked.connect(self.open_first_book)
        start_heading.addWidget(sample)
        blocks.addLayout(start_heading)
        self.current_book_strip = QWidget(objectName="currentBook")
        current = QHBoxLayout(self.current_book_strip)
        current.setContentsMargins(14, 9, 14, 9)
        current.setSpacing(12)
        summary = QVBoxLayout()
        summary.setSpacing(2)
        self.start_project_title = BookTitleLabel(objectName="currentBookTitle")
        summary.addWidget(self.start_project_title)
        self.start_project_state = QLabel(objectName="muted")
        summary.addWidget(self.start_project_state)
        current.addLayout(summary, 1)
        continue_book = QPushButton("继续编辑")
        continue_book.clicked.connect(self.show_book)
        current.addWidget(continue_book)
        blocks.addWidget(self.current_book_strip)

        self.draft_notice = QWidget(objectName="draftNotice")
        notice = QHBoxLayout(self.draft_notice)
        notice.setContentsMargins(14, 9, 14, 9)
        self.draft_label = QLabel()
        self.draft_label.setWordWrap(True)
        notice.addWidget(self.draft_label, 1)
        recovery = QPushButton("恢复草稿")
        recovery.clicked.connect(self.choose_recovery)
        notice.addWidget(recovery)
        blocks.addWidget(self.draft_notice)

        columns = QHBoxLayout()
        columns.setSpacing(18)
        actions = QWidget()
        action_layout = QVBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(10)
        action_layout.addWidget(QLabel("创建与打开", objectName="section"))
        grid = QGridLayout()
        grid.setSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for index, (name, detail, callback) in enumerate((
            ("新建书稿", "从空白章节开始写作", self.new),
            ("打开书籍或工程", "继续编辑 EPUB、工程或文稿", self.open),
            ("网页采集", "把文章或网站目录整理成书稿", self.capture_urls),
            ("扫描件 OCR", "把 PDF 扫描件或照片转成书稿", self.import_scan),
        )):
            card = QPushButton(objectName="startPrimaryCard" if index == 0 else "startCard")
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            card.setMinimumHeight(86)
            card.setAccessibleName(name)
            card.setAccessibleDescription(detail)
            card.clicked.connect(callback)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(15, 12, 15, 12)
            card_layout.setSpacing(4)
            card_layout.addStretch()
            title = QLabel(name, objectName="cardTitle")
            caption = QLabel(detail, objectName="cardDetail")
            caption.setWordWrap(True)
            for label in (title, caption):
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                card_layout.addWidget(label)
            card_layout.addStretch()
            grid.addWidget(card, index // 2, index % 2)
        action_layout.addLayout(grid)
        extras = QHBoxLayout()
        batch = QPushButton("批量转换格式")
        batch.clicked.connect(self.batch_convert)
        extras.addWidget(batch)
        ocr_batches = QPushButton("OCR 批次任务")
        ocr_batches.clicked.connect(self.manage_ocr_batches)
        extras.addWidget(ocr_batches)
        extras.addStretch()
        action_layout.addLayout(extras)
        action_layout.addStretch()
        columns.addWidget(actions, 3)

        self.project_shelf = ProjectShelf(self.workspace)
        self.project_shelf.open_requested.connect(self.open_path)
        self.project_shelf.pin_failed.connect(self.error)
        self.project_shelf.library_requested.connect(self.open_library)
        columns.addWidget(self.project_shelf, 2, Qt.AlignmentFlag.AlignTop)
        blocks.addLayout(columns)
        self.resize_start_page()
        return scroll

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "start_outer"):
            self.resize_start_page()

    def resize_start_page(self):
        compact = self.width() < 1050
        margins = (24, 20, 24, 8) if compact else (28, 32, 28, 24)
        self.start_outer.setContentsMargins(*margins)

    def refresh_welcome(self):
        self.project_shelf.refresh()
        count = len(self.recovery_candidates())
        self.draft_label.setText(f"有 {count} 份未保存草稿可恢复。")
        self.draft_notice.setVisible(bool(count))

    def refresh_recent_menu(self):
        self.recent_menu.clear()
        paths = self.workspace.recent()
        if not paths:
            self.recent_menu.addAction("暂无记录").setEnabled(False)
        for path in paths:
            action = self.recent_menu.addAction(path.name, lambda checked=False, p=path: self.open_path(p))
            action.setToolTip(str(path))

    def recovery_candidates(self):
        return [path for path in self.workspace.candidates() if path != self.recovered_draft]

    def choose_recovery(self):
        paths = self.recovery_candidates()
        if not paths:
            self.statusBar().showMessage("目前没有可恢复的草稿。")
            return
        path = paths[0]
        if len(paths) > 1:
            from datetime import datetime

            labels = [f"草稿 {index} · {datetime.fromtimestamp(item.stat().st_mtime):%m-%d %H:%M:%S}"
                      for index, item in enumerate(paths, 1)]
            choice, ok = QInputDialog.getItem(self, "恢复草稿", "选择要恢复的书稿", labels, 0, False)
            if not ok:
                return
            path = paths[labels.index(choice)]
        if self.can_leave():
            self.start_task("正在恢复草稿…", lambda: Book.load(path), lambda book: self.restore_book(book, path))

    def restore_book(self, book, path):
        self.replace_book(book, None)
        self.recovered_draft = path
        self.changed()
        self.statusBar().showMessage("草稿已恢复 · 保存工程时请选择保存位置。")

    def autosave_draft(self):
        if not self.dirty or not self.book_started or self.draft_task is not None:
            return
        revision = self.edit_revision
        snapshot = deepcopy(self.book)
        task = Task(lambda: self.workspace.write_draft(snapshot), self)
        self.draft_task = task
        task.failure.connect(lambda _error: self.statusBar().showMessage("草稿自动保存失败，请手动保存工程。")
                             if self.draft_task is task else None)
        task.finished.connect(lambda: self.draft_finished(task, revision))
        task.start()

    def draft_finished(self, task, revision):
        if task is self.draft_task:
            self.draft_task = None
            task.deleteLater()
            if self.dirty and self.edit_revision != revision:
                self.draft_timer.start()

    def discard_draft(self):
        self.draft_timer.stop()
        if self.draft_task is not None:
            task, self.draft_task = self.draft_task, None
            task.wait()
            task.deleteLater()
        self.workspace.discard_draft()
        if self.recovered_draft is not None:
            self.workspace.discard_draft(self.recovered_draft)
            self.recovered_draft = None

    def update_title(self):
        title = self.book.title or "未命名电子书"
        prefix = "* " if self.dirty else ""
        self.setWindowTitle(f"{prefix}{title} — 电子书工作台")
        showing_book = self.book_started and not self.showing_start
        state = "未保存修改" if self.dirty else "已保存" if self.project else "尚未保存工程"
        self.workspace_title.set_title(title if showing_book else "电子书工作台")
        self.current_book_strip.setVisible(self.book_started)
        self.start_project_title.set_title(title)
        self.start_project_state.setText(f"当前书稿 · {state}")
        self.nav_start.setChecked(not showing_book)
        self.nav_book.setChecked(showing_book)
        self.nav_book.setEnabled(self.book_started)
        self.project_actions.setVisible(showing_book)
        self.project_state.setVisible(showing_book)
        self.project_state.setText(state)
        self.search_text_action.setEnabled(self.book_started)
        for action in (self.checkpoints_action, self.resources_action, self.web_update_action,
                       self.check_book_action, self.show_book_action, self.book_info_action):
            action.setEnabled(self.book_started)
        ready = self.book_started and bool(self.book.chapters)
        for action in (self.replace_text_action, self.rule_groups_action, self.ocr_review_action, self.structure_action,
                       self.inline_toc_action, self.spelling_action):
            action.setEnabled(ready)
        self.export_button.setEnabled(bool(self.book.chapters))
        self.export_action.setEnabled(ready)
        self.screens.setCurrentWidget(self.split if showing_book else self.welcome)

    def show_start(self):
        self.showing_start = True
        self.refresh_welcome()
        self.update_title()

    def show_book(self):
        if self.book_started:
            self.showing_start = False
            self.update_title()

    def changed(self):
        self.dirty = True
        self.edit_revision += 1
        if not self.draft_timer.isActive() and self.draft_task is None:
            self.draft_timer.start()
        self.update_title()

    def row_label(self, chapter):
        return chapter.title or "未命名章节"

    def reload(self, row=0):
        with QSignalBlocker(self.chapters):
            self.chapters.clear()
            self.chapter_items = []
            parents = []
            for index, chapter in enumerate(self.book.chapters):
                while parents and parents[-1][0] >= chapter.level:
                    parents.pop()
                parent = parents[-1][1] if parents else self.chapters.invisibleRootItem()
                item = QTreeWidgetItem(parent, [self.row_label(chapter)])
                item.setData(0, Qt.ItemDataRole.UserRole, index)
                item.setData(0, int(Qt.ItemDataRole.UserRole) + 1, chapter.path)
                self.chapter_items.append(item)
                parents.append((chapter.level, item))
            if 0 <= row < len(self.chapter_items):
                self.chapters.setCurrentItem(self.chapter_items[row])
        self.select(row if 0 <= row < len(self.chapter_items) else -1)
        self.filter_chapters(self.chapter_filter.text())
        self.update_title()

    def filter_chapters(self, text):
        words = text.casefold().split()
        self.chapter_matches = [
            index for index, chapter in enumerate(self.book.chapters)
            if all(word in self.row_label(chapter).casefold() for word in words)
        ]
        visible = set(self.chapter_matches)
        if words:
            for index in self.chapter_matches:
                parent = self.chapter_items[index].parent()
                while parent is not None:
                    visible.add(parent.data(0, Qt.ItemDataRole.UserRole))
                    parent = parent.parent()
        with QSignalBlocker(self.chapters):
            for index, (chapter, item) in enumerate(zip(self.book.chapters, self.chapter_items)):
                item.setHidden(index not in visible)
                item.setExpanded(bool(words) or chapter.path not in self.collapsed_paths)
        count = len(self.book.chapters)
        matched = len(self.chapter_matches)
        self.chapter_count.setText(f"章节目录 · {matched}/{count}" if words else f"章节目录 · {count}")
        self.no_matches.setVisible(bool(words and not matched))
        self.update_chapter_operations()

    def focus_chapter_filter(self):
        if self.book_started:
            self.show_book()
            self.chapter_filter.setFocus()
            self.chapter_filter.selectAll()
        else:
            self.open()

    def clear_chapter_filter(self):
        self.chapter_filter.clear()
        self.chapters.setFocus()

    def jump_to_match(self):
        if self.chapter_matches:
            self.go_to_chapter(self.chapter_matches[0], clear_filter=False)

    def go_to_chapter(self, row, *, clear_filter=True):
        if not 0 <= row < len(self.chapter_items):
            return
        self.show_book()
        if clear_filter:
            self.chapter_filter.clear()
        item = self.chapter_items[row]
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.chapters.setCurrentItem(item)
        self.chapters.scrollToItem(item)
        self.chapters.setFocus()

    def search_book_text(self):
        if not self.book_started:
            return

        def choose():
            row = select_search_result(self, self.book)
            if row is not None:
                self.go_to_chapter(row)

        self.flush_current_edits(choose)

    def check_book_quality(self):
        self.flush_current_edits(lambda: show_book_quality(self))

    def review_current_scan(self):
        self.show_book()
        show_ocr_review(self, checkpoint=self.workspace.save_checkpoint)

    def manage_book_structure(self):
        self.show_book()
        self.flush_current_edits(lambda: show_book_structure(self))

    def manage_book_resources(self):
        self.show_book()
        self.flush_current_edits(lambda: show_book_resources(self))

    def update_chapter_operations(self):
        active = self.chapter() is not None
        filtering = bool(self.chapter_filter.text().strip())
        for label, button in self.chapter_operations.items():
            button.setEnabled(label == "新增" or (active and not filtering))
            button.setToolTip("清空搜索后调整章节" if filtering and label != "新增" else "")

    def chapter(self):
        return self.book.chapters[self.active] if 0 <= self.active < len(self.book.chapters) else None

    def select(self, row):
        self.active = row
        self.preview_fragment = ""
        self.token = secrets.token_hex(8)
        chapter = self.chapter()
        if self.source_document is not None and (chapter is None or chapter.path != self.source_document[0]):
            self.source_document = None
        self.chapter_title.setEnabled(chapter is not None)
        self.chapter_title.setText(chapter.title if chapter else "")
        if chapter:
            rich = can_edit_rich(chapter.html)
            preferred = self.preferred_mode if rich or self.preferred_mode != "rich" else "source"
            with QSignalBlocker(self.mode):
                self.mode.setTabEnabled(0, rich)
                self.mode.setCurrentIndex(VIEW_MODES.index(preferred))
            self.show_mode_hint(rich)
        else:
            self.show_mode_hint(True)
        self.update_chapter_operations()
        self.open_view()

    def view_mode(self):
        return VIEW_MODES[self.mode.currentIndex()]

    def show_mode_hint(self, rich):
        self.mode_hint.setText("" if rich else "该章节含特殊排版，可在源码中保留并修改。")
        self.mode_hint.setVisible(not rich)

    def mode_changed(self, _index):
        self.preferred_mode = self.view_mode()
        self.open_view()

    def rename(self, title):
        chapter = self.chapter()
        if chapter:
            chapter.title = title
            self.chapter_items[self.active].setText(0, self.row_label(chapter))
            if self.chapter_filter.text().strip():
                self.filter_chapters(self.chapter_filter.text())
            self.changed()

    def add_chapter(self):
        active = self.chapter()
        index = self.branch_end(self.active) if active else len(self.book.chapters)
        self.book.chapters.insert(index, Chapter(f"第 {index + 1} 章", "<p></p>", level=active.level if active else 1))
        with QSignalBlocker(self.chapter_filter):
            self.chapter_filter.clear()
        self.changed()
        self.reload(index)
        self.chapter_title.setFocus()
        self.chapter_title.selectAll()

    def branch_end(self, index):
        chapters = self.book.chapters
        level = chapters[index].level
        return next((j for j in range(index + 1, len(chapters)) if chapters[j].level <= level), len(chapters))

    def move(self, offset):
        if self.chapter() is None:
            return
        item = self.chapter_items[self.active]
        parent = item.parent() or self.chapters.invisibleRootItem()
        sibling = parent.indexOfChild(item) + offset
        if not 0 <= sibling < parent.childCount():
            return
        other = parent.child(sibling).data(0, Qt.ItemDataRole.UserRole)
        start, middle = sorted([self.active, other])
        end = self.branch_end(middle)
        chapters = self.book.chapters
        chapters[start:end] = chapters[middle:end] + chapters[start:middle]
        self.changed()
        self.reload(start if offset < 0 else start + end - middle)

    def change_level(self):
        chapter = self.chapter()
        if chapter:
            children = self.book.chapters[self.active:self.branch_end(self.active)]
            depth = max(child.level for child in children) - chapter.level
            previous = self.book.chapters[self.active - 1].level if self.active else 0
            maximum = min(6 - depth, previous + 1)
            level, ok = QInputDialog.getInt(self, "目录层级", "层级（1 为一级目录）",
                                            min(int(chapter.level), maximum), 1, maximum)
            if ok:
                change = level - chapter.level
                for child in children:
                    child.level += change
                self.changed()
                self.reload(self.active)

    def remove(self):
        chapter = self.chapter()
        if chapter is None:
            return
        children = self.book.chapters[self.active + 1:self.branch_end(self.active)]
        prompt = f"从工程中删除「{self.row_label(chapter)}」？"
        if children:
            prompt += f"\n其下属的 {len(children)} 个章节将保留，并提升一级。"
        if QMessageBox.question(self, "删除章节", prompt) != QMessageBox.StandardButton.Yes:
            return
        for child in children:
            child.level -= 1
        self.book.chapters.pop(self.active)
        self.collapsed_paths.discard(chapter.path)
        self.changed()
        self.reload(min(self.active, len(self.book.chapters) - 1))

    def edit_stylesheet(self):
        self.flush_current_edits(lambda: show_typography(self))

    def edit_book_info(self):
        dialog = BookInfoDialog(self, self.book)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            cover = None if dialog.remove_cover else self.book.cover
            try:
                if dialog.cover_path is not None:
                    if dialog.preview.pixmap().isNull():
                        raise ValueError("请选择可以读取的封面图片。")
                    _, cover, _, _ = add_asset(self.book, dialog.cover_path)
            except (OSError, ValueError) as exc:
                self.error(str(exc))
                continue
            values = (dialog.title.text().strip(), dialog.author.text().strip(), dialog.language.text().strip(), cover)
            if values != (self.book.title, self.book.author, self.book.language, self.book.cover):
                self.book.title, self.book.author, self.book.language, self.book.cover = values
                self.changed()
                self.statusBar().showMessage("书籍资料已更新，保存工程后生效。")
            break

    def script(self, method, *args):
        payload = ",".join(json.dumps(arg, ensure_ascii=True) for arg in args)
        self.web.page().runJavaScript(f"window.studioEditor?.{method}({payload})")

    def web_loaded(self):
        self.web_ready = True
        self.open_view()

    def flush_current_edits(self, continuation):
        if not self.web_ready or self.view_mode() != "rich" or self.chapter() is None:
            continuation()
            return
        token = self.token

        def accept(payload):
            if not isinstance(payload, str) or self.token != token:
                return
            current = json.loads(payload)
            if current is not None:
                self.editor_changed(current["token"], current["html"])
            continuation()

        self.web.page().runJavaScript("JSON.stringify(window.studioEditor?.current?.() ?? null)", accept)

    def open_view(self):
        chapter = self.chapter()
        self.mode.setEnabled(chapter is not None)
        mode = self.view_mode()
        if chapter and mode == "rich" and not can_edit_rich(chapter.html):
            with QSignalBlocker(self.mode):
                self.mode.setCurrentIndex(1)
            mode = "source"
        rich = chapter is not None and mode == "rich"
        live_preview = rich and self.split_preview.isChecked()
        self.insert_picture.setEnabled(bool(rich and self.web_ready))
        self.insert_picture.setVisible(rich)
        self.split_preview.setVisible(rich)
        self.device.setVisible(bool(chapter and (mode == "preview" or live_preview)))
        if chapter is None:
            self.views.setCurrentIndex(0)
            return
        if mode == "source":
            current = (chapter.path, chapter.html)
            if current != self.source_document:
                self.source_document = current
                with QSignalBlocker(self.source):
                    self.source.setPlainText(chapter.html)
            self.views.setCurrentIndex(1)
            return
        self.views.setCurrentIndex(2)
        if self.web_ready:
            try:
                resources, stylesheet = book_assets(self.book, chapter, preview=not rich or live_preview)
            except (etree.ParserError, ValueError) as exc:
                self.statusBar().showMessage(f"预览解析失败 · {exc}")
                return
            self.script("open", self.token, chapter.html, resources, "split" if live_preview else mode,
                        stylesheet, self.device.currentData(), self.preview_fragment)
            self.preview_fragment = ""

    def source_changed(self):
        chapter = self.chapter()
        if chapter and self.view_mode() == "source":
            chapter.html = self.source.toPlainText()
            self.source_document = (chapter.path, chapter.html)
            rich = can_edit_rich(chapter.html)
            self.mode.setTabEnabled(0, rich)
            self.show_mode_hint(rich)
            self.changed()

    def editor_changed(self, token, content):
        chapter = self.chapter()
        if token == self.token and chapter and self.view_mode() == "rich":
            revised = edit_body(chapter.html, content)
            if revised != chapter.html:
                chapter.html = revised
                self.changed()

    def add_image(self):
        chapter = self.chapter()
        if not chapter:
            return
        name, _ = QFileDialog.getOpenFileName(self, "插入图片", "", "图片 (*.jpg *.jpeg *.png *.gif *.svg)")
        if name:
            alt, ok = QInputDialog.getText(self, "图片说明", "替代文字（装饰图片可以留空）")
            if ok:
                try:
                    relative, _, mime, data = add_asset(self.book, Path(name), chapter)
                except (OSError, ValueError) as exc:
                    return self.error(str(exc))
                payload = base64.b64encode(data).decode("ascii")
                self.script("image", relative, f"data:{mime};base64,{payload}", alt)

    def error(self, message):
        self.statusBar().showMessage("操作未完成。")
        summary = message.partition("\n")[0]
        self.warning("操作未完成", summary[:240], message if message != summary[:240] else "")

    def warning(self, title, summary, details=""):
        dialog = QMessageBox(QMessageBox.Icon.Warning, title, summary, parent=self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        if details:
            dialog.setDetailedText(details)
        dialog.exec()

    def start_task(self, status, action, complete, *, reports_progress=False):
        if self.task is not None:
            return
        self.statusBar().showMessage(status)
        self.output_button.hide()
        self.task_progress.setRange(0, 0)
        self.task_progress.show()
        for widget in (self.header, self.navigation, self.screens, self.menuBar()):
            widget.setEnabled(False)
        self.task_complete = complete
        self.task = Task(action, self, reports_progress=reports_progress)
        self.task.result.connect(self.task_result)
        self.task.failure.connect(self.error)
        self.task.progress.connect(self.show_task_progress)
        self.task.finished.connect(self.end_task)
        self.task.start()

    @Slot(object)
    def task_result(self, result):
        self.task_complete(result)

    @Slot(int, int, str)
    def show_task_progress(self, done, total, detail):
        self.task_progress.setRange(0, total)
        self.task_progress.setValue(done)
        self.statusBar().showMessage(detail)

    def end_task(self):
        self.task.deleteLater()
        self.task = None
        self.task_progress.hide()
        for widget in (self.header, self.navigation, self.screens, self.menuBar()):
            widget.setEnabled(True)

    def save(self, *, as_new=False):
        if not self.book_started:
            self.statusBar().showMessage("先打开或新建书稿。")
            return False
        path = self.project
        if path is None or as_new:
            choice, _ = QFileDialog.getSaveFileName(self, "保存电子书工程", safe_name(self.book.title) + ".bookproj",
                                                   "电子书工程 (*.bookproj)")
            if not choice:
                return False
            path = Path(choice)
            if path.suffix.lower() != ".bookproj":
                path = path.with_suffix(".bookproj")
        try:
            self.book.save(path)
        except (OSError, ValueError) as exc:
            self.error(str(exc))
            return False
        self.project = path
        self.dirty = False
        self.discard_draft()
        self.workspace.remember(path)
        self.refresh_welcome()
        self.update_title()
        self.statusBar().showMessage(f"已保存工程 · {path}")
        return True

    def can_leave(self):
        if self.task is not None:
            self.statusBar().showMessage("请等待当前操作完成。")
            return False
        if not self.dirty:
            return True
        buttons = QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
        answer = QMessageBox.question(self, "保存工程", "是否保存当前书稿的修改？", buttons, QMessageBox.StandardButton.Save)
        return answer == QMessageBox.StandardButton.Discard or (answer == QMessageBox.StandardButton.Save and self.save())

    def new(self):
        if self.can_leave():
            self.replace_book(Book(chapters=[Chapter("第一章", "<p></p>")]), None)
            self.chapter_title.setFocus()
            self.chapter_title.selectAll()

    def open_first_book(self):
        if self.can_leave():
            self.replace_book(first_book(), None)
            self.statusBar().showMessage("改一句正文，点「保存工程」留存书稿，再点「导出」制作 EPUB。")

    def replace_book(self, book, project):
        self.discard_draft()
        self.book = book
        self.project = project
        self.source_document = None
        self.dirty = False
        self.book_started = True
        self.showing_start = False
        self.collapsed_paths.clear()
        with QSignalBlocker(self.chapter_filter):
            self.chapter_filter.clear()
        self.preferred_mode = "rich"
        self.reload()

    def adopt_book_edit(self, book, row=None):
        """Keep the current project location and adopt a reviewed edit in memory."""
        self.book = book
        self.source_document = None
        self.show_book()
        row = self.active if row is None else row
        self.chapter_filter.clear()
        self.changed()
        self.reload(max(0, min(row, len(book.chapters) - 1)))

    def open(self):
        name, _ = QFileDialog.getOpenFileName(self, "打开书籍或稿件", "", FILE_FILTER)
        if name:
            self.open_path(Path(name))

    def open_library(self, *, full_text=False, add_paths=()):
        try:
            library = BookLibrary(self.workspace.directory)
        except (OSError, sqlite3.Error) as exc:
            self.error(f"无法打开本机书库。\n{exc}")
            return
        dialog = LibraryTextDialog(self, library) if full_text else BookLibraryDialog(self, library)
        if add_paths:
            dialog.add_paths(add_paths)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            hit = dialog.hit if full_text else dialog.chosen_hit
            path = hit.path if hit else dialog.chosen_path
            if path is not None:
                self.open_path(path, chapter_path=hit.chapter_path if hit else None,
                               fragment=hit.fragment if hit else "")
        dialog.deleteLater()

    def open_path(self, path, *, chapter_path=None, fragment=""):
        def chapter_index(book):
            return next((i for i, chapter in enumerate(book.chapters) if chapter.path == chapter_path), None)

        def show_result(row):
            self.go_to_chapter(row)
            if fragment:
                self.preview_fragment = fragment
                with QSignalBlocker(self.mode):
                    self.mode.setCurrentIndex(VIEW_MODES.index("preview"))
                self.open_view()

        if chapter_path and path == self.project:
            row = chapter_index(self.book)
            if row is not None:
                self.flush_current_edits(lambda: show_result(row))
            else:
                self.error("当前书稿中的章节已移动或删除，请保存后刷新索引。")
            return

        def loaded(book):
            row = chapter_index(book) if chapter_path else None
            if chapter_path and row is None:
                self.error("原文件中的章节已更改，请刷新索引。")
                return
            self.opened(book, path)
            if row is not None:
                show_result(row)

        if self.can_leave():
            self.start_task("正在打开书籍…", lambda: read_book(path), loaded)

    def opened(self, book, path, *, remember_source=True):
        self.replace_book(book, path if path.suffix.lower() == ".bookproj" else None)
        if remember_source:
            self.workspace.remember(path)
            self.refresh_welcome()
        self.statusBar().showMessage(f"已打开 {path.name} · {len(book.chapters)} 个章节")

    def import_scan(self, *, path=None):
        if path is None:
            filename, _ = QFileDialog.getOpenFileName(
                self, "选择扫描 PDF 或图片", "",
                "扫描件 (*.pdf *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff);;所有文件 (*)",
            )
            if not filename:
                return
            path = Path(filename)
        dialog = ScanDialog(self, source_name=path.name, multiple_pages=path.suffix.lower() in {".pdf", ".tif", ".tiff"})
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.manage_ocr_batches(new=(path, dialog.pages.text().strip(), dialog.model.currentData()))

    def manage_ocr_batches(self, *, new=None):
        if self.task is not None:
            return
        dialog = OcrBatchDialog(self, OcrBatchStore(self.workspace.directory), new=new)
        if dialog.exec() == QDialog.DialogCode.Accepted and self.can_leave():
            self.replace_book(dialog.book, None)
            self.changed()
            self.statusBar().showMessage(f"已打开 {len(dialog.book.chapters)} 页，可校对正文并保存工程。")

    def import_files(self):
        names, _ = QFileDialog.getOpenFileNames(self, "选择章稿文件", "", FILE_FILTER)
        if names:
            self.start_task("正在导入章稿…", lambda: [read_book(Path(name)) for name in names], self.imported)

    def imported(self, books):
        start = len(self.book.chapters)
        if not start and not self.project and self.book.title == "未命名电子书" and books:
            self.book.title = books[0].title
        for book in books:
            # Books have independent resource namespaces. Relocate a whole
            # incoming package under a unique directory for safe merging.
            prefix = f"imports/{secrets.token_hex(6)}"
            for name, asset in book.assets.items():
                self.book.assets[f"{prefix}/{name}"] = asset
            for index, chapter in enumerate(book.chapters, 1):
                original = chapter.path
                chapter.path = f"{prefix}/{original or f'chapter-{index}.xhtml'}"
                if original in book._epub_chapters:
                    self.book._epub_members[chapter.path] = book._epub_members[original]
                    self.book._epub_chapters[chapter.path] = book._epub_chapters[original]
                self.book.chapters.append(chapter)
        self.book_started = True
        self.showing_start = False
        self.changed()
        self.reload(start)
        self.statusBar().showMessage(f"已导入 {len(self.book.chapters) - start} 个章节。")

    def capture_urls(self):
        dialog = CaptureDialog(capture.discover, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        profile = dialog.capture_profile()
        urls = profile.urls
        if urls:
            start_new_book = self.showing_start and self.book_started
            if start_new_book and not self.can_leave():
                return
            saved_profile = profile if dialog.save_selection.isChecked() else None
            self.start_task("正在采集网页并保存离线图片…",
                            lambda progress: capture_pages(urls, profile.rendered, progress=progress,
                                                           article_xpath=profile.article_xpath),
                            lambda result: self.captured(result, start_new_book=start_new_book, profile=saved_profile),
                            reports_progress=True)

    def captured(self, result, *, start_new_book=False, profile=None):
        books, notices = result
        if books:
            if start_new_book:
                self.replace_book(Book(), None)
            if profile is not None:
                remember_profile(self.book, profile)
            self.imported(books)
        if notices:
            if books:
                self.warning("采集结果", "正文已导入，有些链接或图片需要核对。", "\n".join(notices))
            else:
                self.warning("采集未完成", "没有取得可以导入的章节。", "\n".join(notices))

    def open_web_profiles(self):
        self.flush_current_edits(self._choose_web_profile)

    def _choose_web_profile(self):
        profiles = profiles_for(self.book)
        dialog = CaptureProfilesDialog(self, profiles)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        profile = dialog.selected_profile() if dialog.operation != 'new' else None
        if dialog.operation != 'refresh':
            editor = CaptureDialog(capture.discover, self, profile=profile, configure=True)
            if editor.exec() != QDialog.DialogCode.Accepted:
                return
            profile = editor.capture_profile()
            remember_profile(self.book, profile)
            self.changed()
        if profile:
            self.start_task("正在核对网页更新…", lambda progress: collect_updates(self.book, profile, progress=progress),
                            lambda result: self._review_web_updates(profile, result), reports_progress=True)

    def _review_web_updates(self, profile, result):
        updates, directory_notice = result
        dialog = WebUpdateDialog(self, self.book, updates, directory_notice)
        if dialog.exec() != QDialog.DialogCode.Accepted or not (chosen := dialog.chosen()):
            return
        try:
            edited, first_row = apply_updates(self.book, chosen, profile)
            self.workspace.save_checkpoint(self.book, "网页增量更新前")
        except (OSError, ValueError) as exc:
            self.error(str(exc))
            return
        self.adopt_book_edit(edited, first_row)
        self.statusBar().showMessage(f"已应用 {len(chosen)} 篇网页更新，可从检查点恢复。")

    def export(self):
        if not self.book.chapters:
            return self.error("请先添加一个章节。")
        statuses = converter_readiness()
        formats = {"epub", "epub2"} | set(conversion.targets_for(Path("book.epub")))
        comics = bool(comic_pages(self.book))
        if comics:
            formats.add("cbz")
        groups = [
            (label, [key for key in keys if key in formats]) for label, keys in (
                ("常用格式", ("epub", "pdf")),
                ("设备与兼容格式", ("azw3", "kepub.epub", "mobi", "epub2", "fb2", "cbz")),
                ("可编辑文稿与网页", ("docx", "md", "txt", "html", "odt", "rtf")),
            )
        ]
        missing = {
            key: missing_converters({"cbz" if key == "pdf" and comics else "epub"}, key, statuses)
            for key in formats - {"epub", "cbz"}
        }
        dialog = ExportDialog(self, title=self.book.title, labels=LABELS, format_groups=groups, missing=missing,
                              validator_ready=statuses["epubcheck"].ready)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        formats = dialog.formats()
        if not formats:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择导出位置", self.output_location())
        if folder:
            name = safe_name(self.book.title)
            mobile = dialog.mobile.isChecked()
            plans = self.choose_destinations(folder, [(name, target) for target in formats])
            if plans is not None:
                def start_export():
                    book = self.book
                    self.last_export_report = None
                    self.export_report_button.hide()
                    self.start_task("正在生成电子书…",
                                    lambda progress: export_book_with_report(
                                        book, Path(folder), name, formats, mobile, plans=plans, progress=progress,
                                    ),
                                    lambda result: self.exported(book, plans, result), reports_progress=True)

                self.flush_current_edits(start_export)

    def exported(self, book, plans, result):
        outputs, errors, report = result
        self.converted((outputs, errors))
        if report is None:
            return
        epub = next((plan.destination for plan in plans if plan.format_name == "epub" and plan.destination in outputs),
                    None)
        name = epub.name if epub else book.title
        context = (
            "校验未通过，导出文件未写入。" if not report.valid else
            "已检查本次导出的 EPUB 3。" if epub else
            "已检查生成其他格式前的 EPUB 3 中间文件。"
        )
        self.last_export_report = book, report, name, context
        self.export_report_button.show()
        if not report.valid:
            self.statusBar().showMessage("导出已取消 · " + report.summary)
            self.show_export_report()
        elif not errors:
            self.statusBar().showMessage(f"已保存 {len(outputs)} 个文件 · {report.summary}")

    def show_export_report(self):
        if self.last_export_report is None:
            return
        book, report, name, context = self.last_export_report
        chapters = book.chapters if book is self.book else ()
        dialog = EpubCheckDialog(self, report, name, context=context, chapters=chapters)
        if dialog.exec() == QDialog.DialogCode.Accepted and book is self.book and dialog.chapter_index is not None:
            self.go_to_chapter(dialog.chapter_index)

    def batch_convert(self, names=None):
        if names is None or isinstance(names, bool):
            names, _ = QFileDialog.getOpenFileNames(self, "选择要转换的文件", "", FILE_FILTER)
        if not names:
            return
        paths = [Path(name) for name in names]
        supported = set.intersection(*(set(conversion.targets_for(path)) for path in paths))
        if "epub" in supported or all(conversion.normalize_format(path) == "epub" for path in paths):
            supported.add("epub2")
        if not supported:
            return self.error("所选文件没有共同的目标格式。请按文件类型分别转换。")
        statuses = converter_readiness()
        targets = sorted(supported)
        source_formats = {conversion.normalize_format(path) for path in paths}
        missing = {target: missing_converters(source_formats, target, statuses) for target in targets}
        labels = [LABELS.get(key, key.upper()) + ("（需配置工具）" if missing[key] else "") for key in targets]
        chosen, ok = QInputDialog.getItem(self, "批量格式转换", "目标格式", labels, 0, False)
        if not ok:
            return
        target = targets[labels.index(chosen)]
        if missing[target]:
            return self.warning("转换工具未就绪", f"{LABELS.get(target, target)} 当前无法转换。",
                                "\n".join(missing[target]))
        folder = QFileDialog.getExistingDirectory(self, "选择导出位置", self.output_location())
        if folder:
            requests = [(conversion_stem(path), target) for path in paths]
            plans = self.choose_destinations(folder, requests, protected=paths)
            if plans is not None:
                self.start_task("正在转换文件…",
                                lambda progress: convert_files(paths, Path(folder), target, plans=plans, progress=progress),
                                self.converted, reports_progress=True)

    def choose_destinations(self, folder, requests, *, protected=()):
        plans = plan_destinations(folder, requests, protected=protected)
        conflicts = [plan for plan in plans if plan.conflict]
        if not conflicts:
            return plans
        dialog = QMessageBox(QMessageBox.Icon.Question, "输出文件重名",
                             "保留原文件并给新文件加序号，或替换已有的同名输出？", parent=self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setDetailedText("\n".join(f"{plan.requested.name} → {plan.destination.name}" for plan in conflicts))
        keep = dialog.addButton("另存为新名称", QMessageBox.ButtonRole.AcceptRole)
        replace = dialog.addButton("替换同名输出", QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(keep)
        dialog.exec()
        if dialog.clickedButton() == keep:
            return plans
        if dialog.clickedButton() == replace:
            return plan_destinations(folder, requests, replace=True, protected=protected)
        return None

    def output_location(self):
        if self.last_output:
            return str(self.last_output)
        return str(self.project.parent) if self.project else ""

    def open_output_folder(self):
        if self.last_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_output)))

    def converted(self, result):
        outputs, errors = result
        if outputs:
            self.last_output = outputs[0].parent
            self.output_button.show()
            self.statusBar().showMessage(f"已保存 {len(outputs)} 个文件 · {self.last_output}")
        if errors:
            summary = f"已保存 {len(outputs)} 个文件，另有 {len(errors)} 项未完成。"
            self.statusBar().showMessage(summary)
            self.warning("输出结果", summary, "\n".join(errors))

    def show_tools(self):
        statuses = {**ocr_readiness(), **converter_readiness()}
        labels = {"xiaomi": "小米 OCR", "paddle": "PaddleOCR-VL", "pandoc": "Pandoc",
                  "calibre": "calibre", "epubcheck": "EPUBCheck"}
        dialog = QMessageBox(QMessageBox.Icon.Information, "本机工具",
                             "\n".join(f"{labels[key]} · {'已找到' if item.ready else '待配置'}"
                                       for key, item in statuses.items()), parent=self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setDetailedText("\n".join(f"{labels[key]} · {item.message}" for key, item in statuses.items()))
        dialog.exec()

    def open_document(self, name):
        directory = Path(sys.executable).parent if getattr(sys, "frozen", False) else APP_ROOT
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory / name)))

    def check_epub(self):
        choice, _ = QFileDialog.getOpenFileName(self, "选择要检查的 EPUB", "", "EPUB (*.epub)")
        if choice:
            path = Path(choice)

            def show_report(report):
                self.statusBar().showMessage("EPUBCheck · " + report.summary)
                EpubCheckDialog(self, report, path.name).exec()

            self.start_task("正在运行 EPUBCheck…", lambda: inspect_epub(path), show_report)

    def receive_files(self, paths):
        if len(paths) == 1:
            path = paths[0]
            if path.suffix.lower() in SCAN_IMAGE_TYPES:
                self.import_scan(path=path)
            else:
                self.open_path(path)
            return
        books = [path for path in paths if path.suffix.lower() in {".epub", ".bookproj"}]
        if not books:
            self.batch_convert(paths)
            return
        convertible = [path for path in paths if conversion.targets_for(path)]
        dialog = QMessageBox(QMessageBox.Icon.Question, "拖入多个文件",
                             f"要如何处理这 {len(paths)} 个文件？", parent=self)
        text = f"可加入书库 {len(books)} 本，可批量转换 {len(convertible)} 项。"
        if len(books) != len(paths) or len(convertible) != len(paths):
            text += " 每个操作只处理它支持的文件。"
            details = []
            for path in paths:
                actions = []
                if path in books:
                    actions.append("加入书库")
                if path in convertible:
                    actions.append("批量转换")
                details.append(f"{path.name} · {' / '.join(actions) or '不支持'}")
            dialog.setDetailedText("\n".join(details))
        dialog.setInformativeText(text)
        add = dialog.addButton("加入书库", QMessageBox.ButtonRole.AcceptRole)
        convert = dialog.addButton("批量转换", QMessageBox.ButtonRole.ActionRole)
        convert.setEnabled(bool(convertible))
        dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(add)
        dialog.exec()
        chosen = dialog.clickedButton()
        if chosen is add:
            self.open_library(add_paths=books)
        elif chosen is convert:
            self.batch_convert(convertible)
        dialog.deleteLater()

    def closeEvent(self, event):
        if self.can_leave():
            self.discard_draft()
            self.workspace.close()
            event.accept()
        else:
            event.ignore()


def missing_converters(source_formats, target, statuses):
    required = dict.fromkeys(tool for source in sorted(source_formats)
                             for tool in conversion.required_tools(source, target))
    return [statuses[tool].message for tool in required if not statuses[tool].ready]


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("电子书工作台")
    translator = QTranslator(app)
    if translator.load("qtbase_zh_CN", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    font = QFont("Microsoft YaHei UI", 10)
    font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "DengXian", "Segoe UI"])
    app.setFont(font)
    studio = Studio()
    studio.show()
    if len(sys.argv) == 2:
        studio.open_path(Path(sys.argv[1]))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
