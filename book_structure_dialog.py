"""Native preview and confirmation for directory and chapter changes."""
from __future__ import annotations

from copy import deepcopy

from lxml import etree
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from book_structure import StructureEdit, heading_toc, merge_chapters, split_chapter, split_choices
from epub_io import current_toc, is_fixed_layout


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
        default = QPushButton('恢复默认目录')
        default.clicked.connect(self.default_toc)
        default.setEnabled(book.toc_entries is not None)
        controls.addWidget(default)
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
        for index, chapter in enumerate(book.chapters, 1):
            self.chapter.addItem(f'{index} · {chapter.title or "未命名章节"}')
        self.chapter.setCurrentIndex(max(0, active))
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
        self.update_chapter_actions()
        self.render(current_toc(book))
        self.apply_button.setEnabled(False)
        self.status.setText('固定版式书籍可编辑目录，章节的原分页会保留。' if self.fixed
                            else '选择一项操作查看预览，确认后应用到当前书稿。')

    def update_chapter_actions(self):
        index = self.chapter.currentIndex()
        self.split_button.setEnabled(not self.fixed and index >= 0)
        self.merge_button.setEnabled(not self.fixed and 0 <= index < len(self.book.chapters) - 1)

    def render(self, entries):
        self.tree.clear()
        parents = [self.tree.invisibleRootItem()]
        for entry in entries:
            level = min(max(1, entry['level']), len(parents))
            parents = parents[:level]
            target = entry['path']
            if entry['fragment']:
                target += '#' + entry['fragment']
            item = QTreeWidgetItem(parents[-1], [entry['title'], target])
            item.setToolTip(1, target)
            parents.append(item)
        self.tree.expandToDepth(2)

    def prepare(self, operation):
        try:
            self.pending = operation()
        except (ValueError, etree.LxmlError) as error:
            self.pending = None
            self.preview_label.setText('当前目录')
            self.render(current_toc(self.book))
            self.status.setText(str(error))
            self.apply_button.setEnabled(False)
            return
        self.preview_label.setText('更改后目录预览')
        self.render(current_toc(self.pending.book))
        summary = self.pending.description
        if self.pending.relinked:
            summary += f' 将同步更新 {self.pending.relinked} 处书内链接。'
        self.status.setText(summary)
        self.apply_button.setEnabled(True)

    def generate(self):
        self.prepare(lambda: StructureEdit(heading_toc(self.book), self.chapter.currentIndex(),
                                           '用正文标题生成有层级的目录，并为缺少锚点的标题建立跳转。'))

    def default_toc(self):
        def reset():
            book = deepcopy(self.book)
            book.toc_entries = None
            return StructureEdit(book, self.chapter.currentIndex(), '恢复原书目录或按现有章节生成默认目录。')
        self.prepare(reset)

    def choose_split(self):
        index = self.chapter.currentIndex()
        try:
            choices = split_choices(self.book, index)
        except (ValueError, etree.LxmlError) as error:
            self.status.setText(str(error))
            return
        if not choices:
            self.status.setText('本章没有可供拆分的后续标题。')
            return
        labels = {f'{choice.index + 1} · H{choice.rank} · {choice.title}': choice.index for choice in choices}
        selection, accepted = QInputDialog.getItem(self, '选择拆分点', '新章节从此标题开始', list(labels), 0, False)
        if accepted:
            self.prepare(lambda: split_chapter(self.book, index, labels[selection]))

    def merge(self):
        self.prepare(lambda: merge_chapters(self.book, self.chapter.currentIndex()))


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
