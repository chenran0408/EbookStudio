"""Native preview and confirmation for directory and chapter changes."""
from __future__ import annotations

from copy import deepcopy

from lxml import etree
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from book_structure import (
    StructureEdit, TocDestination, edit_toc_entry, heading_toc, merge_chapters, move_toc_entry,
    split_chapter, split_choices, toc_destinations,
)
from epub_io import current_toc, is_fixed_layout


class TocEntryDialog(QDialog):
    def __init__(self, parent, book, entry):
        super().__init__(parent)
        self.book = book
        self.original = entry
        self.setWindowTitle('编辑目录条目')
        self.resize(460, 210)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title = QLineEdit(entry['title'])
        self.title.selectAll()
        self.chapter = QComboBox()
        self.destination = QComboBox()
        for label, field in (('目录标题', self.title), ('目标章节', self.chapter), ('跳转位置', self.destination)):
            form.addRow(label, field)
            field.setAccessibleName(label)
        for combo in (self.chapter, self.destination):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(15)
        for index, chapter in enumerate(book.chapters, 1):
            self.chapter.addItem(f'{index} · {chapter.title or "未命名章节"}', chapter.path)
        start = self.chapter.findData(entry['path'])
        if start < 0:
            start = self.chapter.count()
            self.chapter.addItem('原书附加页面', entry['path'])
        self.chapter.setCurrentIndex(start)
        layout.addLayout(form)
        self.message = QLabel(objectName='muted')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.save_button = buttons.addButton('确认编辑', QDialogButtonBox.ButtonRole.AcceptRole)
        self.save_button.setObjectName('primary')
        self.save_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.title.textChanged.connect(self.update_message)
        self.chapter.currentIndexChanged.connect(self.load_destinations)
        self.load_destinations(start)
        self.title.setFocus()

    def load_destinations(self, index):
        self.destination.clear()
        path = self.chapter.currentData()
        original_path = path == self.original['path']
        fragment = self.original['fragment']
        self.target_error = ''
        try:
            targets = toc_destinations(self.book, index) if 0 <= index < len(self.book.chapters) else []
        except (ValueError, etree.LxmlError) as error:
            targets = []
            self.target_error = str(error)
        for target in targets:
            self.destination.addItem(target.label, target)
        if original_path:
            selected = next((i for i, target in enumerate(targets)
                             if target.heading_index is None and target.fragment == fragment), None)
            if selected is None:
                label = f'原跳转位置 · #{fragment}' if fragment else '原跳转位置'
                selected = self.destination.count()
                self.destination.addItem(label, TocDestination(path, label, fragment))
            self.destination.setCurrentIndex(selected)
        self.update_message()

    def update_message(self):
        title_available = bool(self.title.text().strip())
        self.save_button.setEnabled(title_available and self.destination.count() > 0)
        self.message.setText(self.target_error or ('请输入目录中显示的标题。' if not title_available else ''))
        self.message.setVisible(bool(self.message.text()))


class BookStructureDialog(QDialog):
    def __init__(self, parent, book, active):
        super().__init__(parent)
        self.book = book
        self.pending = None
        self.setWindowTitle('目录与章节')
        self.resize(670, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 15)
        layout.setSpacing(10)

        controls = QHBoxLayout()
        headings = QPushButton('按标题生成目录')
        headings.clicked.connect(self.generate)
        controls.addWidget(headings)
        self.default = QPushButton('恢复默认目录')
        self.default.clicked.connect(self.default_toc)
        controls.addWidget(self.default)
        controls.addStretch(1)
        layout.addLayout(controls)
        headings.setEnabled(bool(book.chapters))

        chapter_row = QHBoxLayout()
        label = QLabel('操作章节')
        self.chapter = QComboBox()
        label.setBuddy(self.chapter)
        self.chapter.setAccessibleName(label.text())
        self.chapter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.chapter.setMinimumContentsLength(12)
        chapter_row.addWidget(label)
        chapter_row.addWidget(self.chapter, 1)
        self.split_button = QPushButton('在标题处拆分…')
        self.split_button.clicked.connect(self.choose_split)
        chapter_row.addWidget(self.split_button)
        self.merge_button = QPushButton('与下一章合并')
        self.merge_button.clicked.connect(self.merge)
        chapter_row.addWidget(self.merge_button)
        layout.addLayout(chapter_row)

        self.preview_label = QLabel('当前目录', objectName='section')
        layout.addWidget(self.preview_label)
        self.tree = QTreeWidget()
        self.tree.setAccessibleName('目录预览')
        self.tree.setHeaderLabels(['标题', '链接目标'])
        self.tree.setColumnWidth(0, 290)
        layout.addWidget(self.tree, 1)

        entry_controls = QHBoxLayout()
        self.edit_button = QPushButton('编辑条目…')
        self.edit_button.clicked.connect(self.edit_entry)
        entry_controls.addWidget(self.edit_button)
        self.move_buttons = {}
        for direction, text, description in (
            ('up', '上移', '与上一同级条目交换位置'),
            ('down', '下移', '与下一同级条目交换位置'),
            ('indent', '降一级', '移到上一同级条目下面'),
            ('outdent', '升一级', '移到上一级条目之后'),
        ):
            button = QPushButton(text)
            button.setToolTip(description)
            button.setAccessibleDescription(description)
            button.clicked.connect(lambda _checked=False, move=direction: self.move_entry(move))
            self.move_buttons[direction] = button
            entry_controls.addWidget(button)
        entry_controls.addStretch(1)
        layout.addLayout(entry_controls)

        self.status = QLabel(objectName='muted')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.apply_button = buttons.addButton('应用更改', QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setObjectName('primary')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.fixed = is_fixed_layout(book)
        self.chapter.currentIndexChanged.connect(self.update_chapter_actions)
        self.tree.currentItemChanged.connect(self.update_entry_actions)
        self.tree.itemDoubleClicked.connect(lambda _item, _column: self.edit_entry())
        self.reload_chapters(active)
        self.render(current_toc(book))
        self.apply_button.setEnabled(False)
        self.status.setText('固定版式书籍可编辑目录，章节的原分页会保留。' if self.fixed
                            else '可以修改条目标题、跳转位置和层级。确认后应用到书稿。')
        self.tree.setFocus()

    @property
    def draft(self):
        return self.pending.book if self.pending is not None else self.book

    def reload_chapters(self, active):
        self.chapter.clear()
        for index, chapter in enumerate(self.draft.chapters, 1):
            self.chapter.addItem(f'{index} · {chapter.title or "未命名章节"}')
        self.chapter.setCurrentIndex(max(0, active))
        self.default.setEnabled(self.draft.toc_entries is not None)
        self.update_chapter_actions()

    def update_chapter_actions(self):
        index = self.chapter.currentIndex()
        self.split_button.setEnabled(not self.fixed and index >= 0)
        self.merge_button.setEnabled(not self.fixed and 0 <= index < len(self.draft.chapters) - 1)

    def update_entry_actions(self, current, _previous=None):
        self.edit_button.setEnabled(current is not None)
        parent = current.parent() if current is not None else None
        group = parent if parent is not None else self.tree.invisibleRootItem()
        index = group.indexOfChild(current) if current is not None else -1
        self.move_buttons['up'].setEnabled(index > 0)
        self.move_buttons['down'].setEnabled(0 <= index < group.childCount() - 1)
        self.move_buttons['indent'].setEnabled(index > 0)
        self.move_buttons['outdent'].setEnabled(parent is not None)

    def render(self, entries, selected=0):
        self.entries = entries
        self.tree.clear()
        parents = [self.tree.invisibleRootItem()]
        selected_item = None
        for index, entry in enumerate(entries):
            level = min(max(1, entry['level']), len(parents))
            parents = parents[:level]
            target = entry['path']
            if entry['fragment']:
                target += '#' + entry['fragment']
            item = QTreeWidgetItem(parents[-1], [entry['title'], target])
            item.setToolTip(1, target)
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            if index == selected:
                selected_item = item
            parents.append(item)
        self.tree.expandToDepth(2)
        if selected_item is not None:
            self.tree.setCurrentItem(selected_item)
            self.tree.scrollToItem(selected_item)
        self.update_entry_actions(self.tree.currentItem())

    def edit_entry(self):
        item = self.tree.currentItem()
        if item is None:
            return
        index = item.data(0, Qt.ItemDataRole.UserRole)
        dialog = TocEntryDialog(self, self.draft, self.entries[index])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.prepare(lambda: edit_toc_entry(self.draft, index, dialog.title.text(),
                                                dialog.destination.currentData(), self.chapter.currentIndex()))

    def move_entry(self, direction):
        item = self.tree.currentItem()
        if item is not None:
            index = item.data(0, Qt.ItemDataRole.UserRole)
            self.prepare(lambda: move_toc_entry(self.draft, index, direction, self.chapter.currentIndex()))

    def prepare(self, operation):
        try:
            edit = operation()
            entries = current_toc(edit.book)
        except (ValueError, etree.LxmlError) as error:
            self.status.setText(str(error))
            return
        self.pending = edit
        self.reload_chapters(edit.chapter)
        self.preview_label.setText('更改后目录预览')
        self.render(entries, edit.toc_index if edit.toc_index is not None else 0)
        summary = self.pending.description
        if self.pending.relinked:
            summary += f' 将同步更新 {self.pending.relinked} 处书内链接。'
        self.status.setText(summary)
        self.apply_button.setEnabled(True)

    def generate(self):
        self.prepare(lambda: StructureEdit(heading_toc(self.draft), self.chapter.currentIndex(),
                                           '用正文标题生成有层级的目录，并为缺少锚点的标题建立跳转。'))

    def default_toc(self):
        def reset():
            book = deepcopy(self.draft)
            book.toc_entries = None
            return StructureEdit(book, self.chapter.currentIndex(), '恢复原书目录或按现有章节生成默认目录。')
        self.prepare(reset)

    def choose_split(self):
        index = self.chapter.currentIndex()
        try:
            choices = split_choices(self.draft, index)
        except (ValueError, etree.LxmlError) as error:
            self.status.setText(str(error))
            return
        if not choices:
            self.status.setText('本章没有可供拆分的后续标题。')
            return
        labels = {f'{choice.index + 1} · H{choice.rank} · {choice.title}': choice.index for choice in choices}
        selection, accepted = QInputDialog.getItem(self, '选择拆分点', '新章节从此标题开始', list(labels), 0, False)
        if accepted:
            self.prepare(lambda: split_chapter(self.draft, index, labels[selection]))

    def merge(self):
        self.prepare(lambda: merge_chapters(self.draft, self.chapter.currentIndex()))


def show_book_structure(studio):
    original = studio.book
    dialog = BookStructureDialog(studio, original, studio.active)
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.pending is None:
        return
    edit = dialog.pending
    revision = studio.edit_revision

    def apply(_checkpoint):
        if studio.book is not original or studio.edit_revision != revision:
            studio.error('书稿已有变化，请重新预览目录与章节。')
            return
        studio.adopt_book_edit(edit.book, edit.chapter)
        studio.go_to_chapter(edit.chapter)
        studio.statusBar().showMessage('已应用目录与章节更改，已保存修改前检查点。')

    studio.start_task('正在保存修改前检查点…',
                      lambda: studio.workspace.save_checkpoint(original, '目录与章节修改前'), apply)
