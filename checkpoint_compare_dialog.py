"""Browse checkpoint differences without applying them."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QPlainTextEdit,
    QSplitter, QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from checkpoint_compare import excerpts


class CheckpointCompareDialog(QDialog):
    def __init__(self, parent, label, changes):
        super().__init__(parent)
        self.setWindowTitle("与当前书稿比较")
        self.resize(850, 590)
        self.changes = changes
        layout = QVBoxLayout(self)
        summary = QLabel()
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        summary.setText(f"「{label}」与当前书稿有 {len(changes)} 项变化。"
                        if changes else f"「{label}」与当前书稿的资料、章节、目录和资源一致。")
        layout.addWidget(summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("返回检查点")
        buttons.rejected.connect(self.reject)
        if not changes:
            self.resize(500, 150)
            layout.addWidget(buttons)
            return
        self.items = QTreeWidget()
        self.items.setAccessibleName("版本差异列表")
        self.items.setHeaderLabels(["项目", "类别", "变化", "书内位置"])
        self.items.setRootIsDecorated(False)
        self.items.setUniformRowHeights(True)
        self.items.setColumnWidth(0, 180)
        self.items.setColumnWidth(1, 92)
        self.items.setColumnWidth(2, 62)
        for change in changes:
            item = QTreeWidgetItem([change.name, change.kind, change.action, change.path])
            item.setToolTip(3, change.path)
            self.items.addTopLevelItem(item)
        self.markup = QCheckBox("查看 XHTML 源码")
        self.markup.setAccessibleName("查看 XHTML 源码")
        self.markup.toggled.connect(self.display)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.panes = []
        for heading in ("检查点版本", "当前书稿"):
            column = QWidget()
            group = QVBoxLayout(column)
            group.setContentsMargins(0, 0, 0, 0)
            title = QLabel(heading)
            editor = QPlainTextEdit()
            editor.setAccessibleName(heading + "改动")
            editor.setReadOnly(True)
            title.setBuddy(editor)
            group.addWidget(title)
            group.addWidget(editor)
            self.panes.append(editor)
            split.addWidget(column)
        layout.addWidget(self.items, 2)
        layout.addWidget(self.markup)
        layout.addWidget(split, 3)
        self.items.currentItemChanged.connect(self.select_item)
        self.items.setCurrentItem(self.items.topLevelItem(0))
        self.items.setFocus()
        layout.addWidget(buttons)

    def select_item(self):
        change = self.changes[self.items.indexOfTopLevelItem(self.items.currentItem())]
        self.markup.setVisible(change.source is not None)
        self.markup.blockSignals(True)
        self.markup.setChecked(change.source is not None and change.before == change.after)
        self.markup.blockSignals(False)
        self.display()

    def display(self):
        item = self.items.currentItem()
        if item is None:
            return
        change = self.changes[self.items.indexOfTopLevelItem(item)]
        values = change.source if self.markup.isChecked() and change.source else (change.before, change.after)
        for editor, lines, color in zip(self.panes, excerpts(*values), ("#fce8e6", "#ddf4e6")):
            editor.setPlainText("\n".join(text for changed, text in lines))
            selections = []
            for i, (changed, _) in enumerate(lines):
                if changed:
                    selection = QTextEdit.ExtraSelection()
                    selection.cursor = QTextCursor(editor.document().findBlockByNumber(i))
                    selection.format.setBackground(QColor(color))
                    selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                    selections.append(selection)
            editor.setExtraSelections(selections)
