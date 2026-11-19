"""Qt dialogs for saved web selections and explicit chapter updates."""

from difflib import unified_diff

from lxml import html
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QListWidget, QListWidgetItem,
    QPlainTextEdit, QSplitter, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from capture_update import WebUpdate


class CaptureProfilesDialog(QDialog):
    def __init__(self, parent, profiles):
        super().__init__(parent)
        self.setWindowTitle('网页增量采集')
        self.resize(660, 330)
        self.profiles = profiles
        self.operation = ''
        layout = QVBoxLayout(self)
        help_text = QLabel('选择一套采集规则，核对新增、变化和失败的文章。')
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.list = QListWidget()
        self.list.setAccessibleName('已保存的网页采集规则')
        for profile in profiles:
            item = QListWidgetItem(profile.label)
            item.setToolTip(profile.label)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        configure = buttons.addButton('修改规则…', QDialogButtonBox.ButtonRole.ActionRole)
        inspect = buttons.addButton('检查更新', QDialogButtonBox.ButtonRole.AcceptRole)
        inspect.setObjectName('primary')
        add = buttons.addButton('新增规则…', QDialogButtonBox.ButtonRole.ActionRole)
        add.clicked.connect(lambda: self.finish('new'))
        configure.clicked.connect(lambda: self.finish('edit'))
        inspect.clicked.connect(lambda: self.finish('refresh'))
        self.list.itemActivated.connect(lambda _item: self.finish('refresh'))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.list.setFocus()

    def finish(self, operation):
        if operation == 'new' or self.list.currentRow() >= 0:
            self.operation = operation
            self.accept()

    def selected_profile(self):
        index = self.list.currentRow()
        return self.profiles[index] if index >= 0 else None


def _text_snapshot(chapter) -> list[str]:
    if chapter is None:
        return []
    root = html.fragment_fromstring(chapter.html, create_parent=True)
    blocks = [line.strip() for line in root.xpath('.//text()') if line.strip()]
    images = [f'[图片] {node.get("alt", "")} · {node.get("src", "")}' for node in root.xpath('.//img')]
    return [chapter.title, *blocks, *images]


class WebUpdateDialog(QDialog):
    def __init__(self, parent, book, updates: list[WebUpdate], directory_notice: str):
        super().__init__(parent)
        self.setWindowTitle('核对网页更新')
        self.resize(760, 570)
        self.book = book
        self.updates = updates
        self.setAccessibleName('网页增量采集差异')
        layout = QVBoxLayout(self)
        header = QLabel('确认要更新的文章', objectName='section')
        layout.addWidget(header)
        hint = QLabel('可先查看差异再勾选文章。已有章节的更新需要手动勾选。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        if directory_notice:
            notice = QLabel(directory_notice)
            notice.setTextFormat(Qt.TextFormat.PlainText)
            notice.setWordWrap(True)
            layout.addWidget(notice)

        panels = QSplitter(Qt.Orientation.Vertical)
        self.rows = QTreeWidget()
        self.rows.setAccessibleName('待确认的网页更新')
        self.rows.setRootIsDecorated(False)
        self.rows.setHeaderLabels(['应用', '状态', '文章'])
        self.rows.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.rows.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.rows.header().setStretchLastSection(True)
        panels.addWidget(self.rows)
        for index, update in enumerate(updates):
            state = {'new': '新增', 'changed': '有变化', 'same': '无变化', 'failed': '失败'}[update.state]
            if update.locally_edited and update.state == 'changed':
                state = '有变化 · 保留本地校订'
            if update.notices and update.state != 'failed':
                state += ' · 有提示'
            title = update.incoming.title if update.incoming else update.url
            row = QTreeWidgetItem(self.rows, ['', state, title])
            row.setData(0, Qt.ItemDataRole.UserRole, index)
            row.setToolTip(2, update.url)
            if update.state in {'new', 'changed'}:
                initial = update.state == 'new' and not update.notices
                row.setCheckState(0, Qt.CheckState.Checked if initial else Qt.CheckState.Unchecked)

        self.tabs = QTabWidget()
        self.difference = self.add_view('差异')
        self.current = self.add_view('本地书稿')
        self.remote = self.add_view('最新网页')
        panels.addWidget(self.tabs)
        panels.setStretchFactor(1, 1)
        panels.setSizes([170, 290])
        layout.addWidget(panels, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('关闭')
        self.apply = buttons.addButton('应用所选', QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply.setObjectName('primary')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.rows.currentItemChanged.connect(self.show_entry)
        self.rows.itemChanged.connect(self.update_apply)
        self.rows.setCurrentItem(self.rows.topLevelItem(0))
        self.update_apply()

    def add_view(self, label):
        view = QPlainTextEdit()
        view.setAccessibleName(label)
        view.setReadOnly(True)
        self.tabs.addTab(view, label)
        return view

    def show_entry(self, item, _previous):
        if item is None:
            return
        update = self.updates[item.data(0, Qt.ItemDataRole.UserRole)]
        prior = self.book.chapters[update.chapter_index] if update.chapter_index is not None else None
        before = _text_snapshot(prior)
        after = _text_snapshot(update.incoming)
        diff = list(unified_diff(before, after, fromfile='本地书稿', tofile='最新网页', lineterm=''))
        details = [update.url, *update.notices]
        if update.state == 'changed' and update.locally_edited:
            details.append('此章节有本地编辑或没有历史基线。勾选应用会以网页正文和标题替换此章。')
        if update.state != 'failed':
            details.extend(diff or ['文本与图片地址未发现差异。'])
        self.difference.setPlainText('\n'.join(details))
        self.current.setPlainText('\n'.join(before))
        self.remote.setPlainText('\n'.join(after))

    def update_apply(self, *_):
        chosen = self.chosen()
        self.apply.setEnabled(bool(chosen))
        self.apply.setText(f'应用 {len(chosen)} 篇' if chosen else '应用所选')

    def chosen(self):
        return [self.updates[index] for index in range(self.rows.topLevelItemCount())
                if self.rows.topLevelItem(index).checkState(0) == Qt.CheckState.Checked]
