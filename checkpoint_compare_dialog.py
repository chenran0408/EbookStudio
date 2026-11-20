"""Browse checkpoint differences without applying them."""
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QColor, QKeySequence, QPixmap, QShortcut, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QSizePolicy, QSplitter, QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from checkpoint_compare import excerpts


class CheckpointCompareDialog(QDialog):
    def __init__(self, parent, label, changes, *, files=None):
        super().__init__(parent)
        self.setWindowTitle("EPUB 对比结果" if files else "与当前书稿比较")
        self.resize(850, 590)
        self.changes = changes
        layout = QVBoxLayout(self)
        summary = QLabel()
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        if files:
            message = f"共 {len(changes)} 项变化。" if changes else "两版的书籍资料、章节、目录和资源一致。"
            summary.setText(f"{message}\n原版　{files[0]}\n修订版　{files[1]}")
            summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        else:
            summary.setText(f"「{label}」与当前书稿有 {len(changes)} 项变化。"
                            if changes else f"「{label}」与当前书稿的资料、章节、目录和资源一致。")
        layout.addWidget(summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭" if files else "返回检查点")
        buttons.rejected.connect(self.reject)
        if not changes:
            self.resize(500, 150)
            layout.addWidget(buttons)
            return
        filters = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setAccessibleName("搜索差异")
        buttons.button(QDialogButtonBox.StandardButton.Close).setAutoDefault(False)
        self.query.setPlaceholderText("搜索项目、书内路径或有改动的行（Ctrl+F）")
        self.query.setToolTip("搜索两侧有改动的行，包括行内保留的文字。纯排版变化按 XHTML 源码检索。")
        self.query.setClearButtonEnabled(True)
        filters.addWidget(self.query, 1)
        self.category = QComboBox()
        self.category.setAccessibleName("差异类别")
        self.category.addItem("全部类别", "")
        for kind in dict.fromkeys(change.kind for change in changes):
            self.category.addItem(kind, kind)
        filters.addWidget(self.category)
        self.reset = QPushButton("重置筛选")
        self.reset.setAutoDefault(False)
        self.reset.clicked.connect(self.reset_filters)
        filters.addWidget(self.reset)
        layout.addLayout(filters)
        self.count = QLabel()
        self.count.setAccessibleName("筛选结果")
        self.count.setWordWrap(True)
        layout.addWidget(self.count)
        self.excerpt_cache = {}
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
        self.images = []
        for heading in (("原版", "修订版") if files else ("检查点版本", "当前书稿")):
            column = QWidget()
            group = QVBoxLayout(column)
            group.setContentsMargins(0, 0, 0, 0)
            title = QLabel(heading)
            editor = QPlainTextEdit()
            editor.setAccessibleName(heading + "改动")
            editor.setReadOnly(True)
            title.setBuddy(editor)
            group.addWidget(title)
            if files:
                image = QLabel()
                image.setAccessibleName(heading + "图片预览")
                image.setAlignment(Qt.AlignmentFlag.AlignCenter)
                image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                group.addWidget(image)
                self.images.append(image)
            group.addWidget(editor)
            self.panes.append(editor)
            split.addWidget(column)
        layout.addWidget(self.items, 2)
        layout.addWidget(self.markup)
        layout.addWidget(split, 3)
        self.items.currentItemChanged.connect(self.select_item)
        self.details = split
        self.query.textChanged.connect(self.filter_items)
        self.category.currentIndexChanged.connect(self.filter_items)
        self.query.returnPressed.connect(self.focus_results)
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self.focus_search)
        self.filter_items()
        self.items.setFocus()
        layout.addWidget(buttons)

    def focus_search(self):
        self.query.setFocus()
        self.query.selectAll()

    def focus_results(self):
        if self.items.currentItem() is not None:
            self.items.setFocus()

    def reset_filters(self):
        with QSignalBlocker(self.category):
            self.category.setCurrentIndex(0)
        with QSignalBlocker(self.query):
            self.query.clear()
        self.filter_items()
        self.focus_search()

    def lines_for(self, index, source):
        key = index, source
        if key not in self.excerpt_cache:
            change = self.changes[index]
            values = change.source if source else (change.before, change.after)
            self.excerpt_cache[key] = excerpts(*values)
        return self.excerpt_cache[key]

    def matches(self, index, query):
        change = self.changes[index]
        if not query or any(query in text.casefold() for text in (change.name, change.path, change.kind, change.action)):
            return True
        source = change.source is not None and change.before == change.after
        raw = change.source if source else (change.before, change.after)
        if not any(query in value.casefold() for value in raw):
            return False
        return any(changed and query in text.casefold() for lines in self.lines_for(index, source)
                   for changed, text in lines)

    def filter_items(self):
        term = self.query.text().strip().casefold()
        kind = self.category.currentData()
        selected = self.items.currentItem()
        first = None
        visible = 0
        with QSignalBlocker(self.items):
            for index, change in enumerate(self.changes):
                item = self.items.topLevelItem(index)
                show = (not kind or change.kind == kind) and self.matches(index, term)
                item.setHidden(not show)
                if show:
                    visible += 1
                    if first is None:
                        first = item
            if selected is None or selected.isHidden():
                selected = first
            self.items.setCurrentItem(selected)
        self.count.setText(f"显示 {visible} / {len(self.changes)} 项变化" if visible
                           else "没有符合条件的变化。可更换关键词、类别，或重置筛选。")
        self.reset.setEnabled(bool(self.query.text() or kind))
        self.items.setVisible(bool(visible))
        self.details.setVisible(bool(visible))
        self.select_item()

    def select_item(self):
        item = self.items.currentItem()
        if item is None:
            self.markup.hide()
            for widget in (*self.panes, *self.images):
                widget.clear()
            return
        change = self.changes[self.items.indexOfTopLevelItem(item)]
        self.markup.setVisible(change.source is not None)
        self.markup.blockSignals(True)
        self.markup.setChecked(change.source is not None and change.before == change.after)
        self.markup.blockSignals(False)
        for side, image in enumerate(self.images):
            image.clear()
            image.setVisible(change.images is not None)
            if change.images is None:
                continue
            asset = change.images[side]
            pixmap = QPixmap()
            raster = {"image/png": "PNG", "image/jpeg": "JPEG", "image/gif": "GIF", "image/webp": "WEBP"}
            if asset is None:
                image.setText("此版本没有这项资源")
            elif asset.media_type in raster and pixmap.loadFromData(asset.data, raster[asset.media_type]):
                if pixmap.width() > 320 or pixmap.height() > 150:
                    pixmap = pixmap.scaled(320, 150, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
                image.setPixmap(pixmap)
            else:
                image.setText("可在下方查看文件信息")
        self.display()

    def display(self):
        item = self.items.currentItem()
        if item is None:
            return
        index = self.items.indexOfTopLevelItem(item)
        query = self.query.text().strip()
        matches = []
        for editor, lines, color in zip(self.panes, self.lines_for(index, self.markup.isChecked()), ("#fce8e6", "#ddf4e6")):
            editor.setPlainText("\n".join(text for changed, text in lines))
            selections = []
            hit = None
            for i, (changed, text) in enumerate(lines):
                if changed:
                    selection = QTextEdit.ExtraSelection()
                    selection.cursor = QTextCursor(editor.document().findBlockByNumber(i))
                    selection.format.setBackground(QColor(color))
                    selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                    selections.append(selection)
                    if hit is None and query and query.casefold() in text.casefold():
                        found = editor.document().find(query, selection.cursor)
                        hit = selection.cursor if found.isNull() else found
                        mark = QTextEdit.ExtraSelection()
                        mark.cursor = hit
                        mark.format.setBackground(QColor("#ffe494"))
                        selections.append(mark)
            editor.setExtraSelections(selections)
            matches.append(hit)
        if any(hit is not None for hit in matches):
            row = next(hit.blockNumber() for hit in matches if hit is not None)
            for editor, hit in zip(self.panes, matches):
                cursor = hit if hit is not None else QTextCursor(editor.document().findBlockByNumber(row))
                editor.setTextCursor(cursor)
                editor.centerCursor()
