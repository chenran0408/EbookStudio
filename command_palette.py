"""Search and execute the workbench's existing menu actions."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog, QHeaderView, QLabel, QLineEdit, QStackedWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)


class CommandPalette(QDialog):
    def __init__(self, parent, commands: tuple[tuple[str, QAction], ...]):
        super().__init__(parent)
        self.setWindowTitle("查找命令")
        self.resize(530, 350)
        self.command: QAction | None = None
        self.commands = [entry for entry in commands if entry[1].isEnabled() and entry[1].isVisible()]
        self.visible_actions: list[QAction] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(9)
        self.query = QLineEdit()
        self.query.setAccessibleName("命令关键词")
        self.query.setPlaceholderText("搜索命令，如 导出、OCR 或 检查书稿")
        self.query.setClearButtonEnabled(True)
        self.query.installEventFilter(self)
        layout.addWidget(self.query)

        self.results = QTreeWidget()
        self.results.setAccessibleName("匹配的命令")
        self.results.setColumnCount(2)
        self.results.setHeaderLabels(["命令", "快捷键"])
        self.results.setHeaderHidden(True)
        self.results.setRootIsDecorated(False)
        self.results.setUniformRowHeights(True)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self.results.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        empty = QLabel("没有匹配的命令，请换个关键词。", objectName="muted")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.results_panel = QStackedWidget()
        self.results_panel.addWidget(self.results)
        self.results_panel.addWidget(empty)
        layout.addWidget(self.results_panel, 1)

        layout.addWidget(QLabel("↑ / ↓  选择    Enter  执行    Esc  关闭", objectName="muted"))
        self.query.textChanged.connect(self.filter_commands)
        self.query.returnPressed.connect(lambda: self.choose(self.results.currentItem()))
        self.results.itemActivated.connect(self.choose)
        self.results.itemClicked.connect(self.choose)
        self.filter_commands("")
        self.query.setFocus()

    def filter_commands(self, text):
        words = text.casefold().split()
        self.results.clear()
        self.visible_actions.clear()
        for group, action in self.commands:
            title = action.text().replace("&", "")
            sequence = action.shortcut()
            shortcut = sequence.toString(QKeySequence.SequenceFormat.NativeText)
            keywords = f"{group} {title} {shortcut} {action.property('commandKeywords') or ''}".casefold()
            if not all(word in keywords for word in words):
                continue
            item = QTreeWidgetItem([f"{group}  ·  {title}", shortcut])
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.results.addTopLevelItem(item)
            self.visible_actions.append(action)
        self.results_panel.setCurrentIndex(0 if self.visible_actions else 1)
        if self.visible_actions:
            self.results.setCurrentItem(self.results.topLevelItem(0))

    def eventFilter(self, watched, event):
        if watched is self.query and event.type() == QEvent.Type.KeyPress and not event.modifiers():
            step = {Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1}.get(event.key())
            if step and self.visible_actions:
                row = self.results.indexOfTopLevelItem(self.results.currentItem())
                next_row = max(0, min(row + step, len(self.visible_actions) - 1))
                item = self.results.topLevelItem(next_row)
                self.results.setCurrentItem(item)
                self.results.scrollToItem(item)
                return True
        return super().eventFilter(watched, event)

    def choose(self, item, _column=0):
        if item is not None and self.command is None:
            self.command = self.visible_actions[self.results.indexOfTopLevelItem(item)]
            self.accept()
