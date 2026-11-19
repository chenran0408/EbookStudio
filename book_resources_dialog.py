"""Native inspection and replacement preview for bundled ebook resources."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSplitter, QStackedWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from book_resources import can_replace, replacement_asset, resource_category, resource_references


class BookResourcesDialog(QDialog):
    def __init__(self, parent, book):
        super().__init__(parent)
        self.book = book
        self.references = resource_references(book)
        self.pending = None
        self.reference_index = None
        self.setWindowTitle('书籍资源')
        self.resize(760, 510)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 15)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        label = QLabel('资源类型')
        self.category = QComboBox()
        self.category.addItems(['全部', '图片', '样式表', '字体', '其他'])
        label.setBuddy(self.category)
        self.category.setAccessibleName(label.text())
        toolbar.addWidget(label)
        toolbar.addWidget(self.category)
        toolbar.addStretch(1)
        self.count = QLabel(objectName='muted')
        toolbar.addWidget(self.count)
        layout.addLayout(toolbar)

        split = QSplitter()
        self.resources = QTreeWidget()
        self.resources.setHeaderLabels(['书内路径', '类型'])
        self.resources.setAccessibleName('书籍资源列表')
        self.resources.setRootIsDecorated(False)
        self.resources.setColumnWidth(0, 235)
        split.addWidget(self.resources)

        details = QWidget()
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(10, 0, 0, 0)
        detail_layout.setSpacing(8)
        self.properties = QLabel(objectName='section')
        self.properties.setTextFormat(Qt.TextFormat.PlainText)
        self.properties.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.properties.setWordWrap(True)
        detail_layout.addWidget(self.properties)
        self.preview = QStackedWidget()
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(150)
        self.image.setAccessibleName('图片预览')
        self.css = QPlainTextEdit()
        self.css.setReadOnly(True)
        self.css.setAccessibleName('样式表内容')
        self.placeholder = QLabel(objectName='muted')
        self.placeholder.setWordWrap(True)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.addWidget(self.image)
        self.preview.addWidget(self.css)
        self.preview.addWidget(self.placeholder)
        detail_layout.addWidget(self.preview, 2)
        detail_layout.addWidget(QLabel('扫描到的引用位置', objectName='section'))
        self.locations = QListWidget()
        self.locations.setAccessibleName('已发现的资源引用')
        detail_layout.addWidget(self.locations, 1)
        self.jump_button = QPushButton('打开引用章节')
        self.jump_button.clicked.connect(self.open_reference)
        self.locations.currentItemChanged.connect(self.update_jump_button)
        self.locations.itemActivated.connect(lambda _item: self.open_reference())
        detail_layout.addWidget(self.jump_button, alignment=Qt.AlignmentFlag.AlignRight)
        split.addWidget(details)
        split.setSizes([310, 410])
        layout.addWidget(split, 1)

        self.note = QLabel(objectName='muted')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('关闭')
        self.choose_button = buttons.addButton('预览替换…', QDialogButtonBox.ButtonRole.ActionRole)
        self.choose_button.clicked.connect(self.choose_replacement)
        self.apply_button = buttons.addButton('应用替换', QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setObjectName('primary')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resources.currentItemChanged.connect(self.select)
        self.category.currentTextChanged.connect(self.load_rows)
        self.load_rows(self.category.currentText())

    def load_rows(self, category):
        self.resources.clear()
        for path, asset in sorted(self.book.assets.items()):
            group = resource_category(path, asset)
            if category == '全部' or category == group:
                item = QTreeWidgetItem(self.resources, [path, group])
                item.setToolTip(0, path)
                item.setData(0, Qt.ItemDataRole.UserRole, path)
        count = self.resources.topLevelItemCount()
        self.count.setText(f'{count} 项资源')
        if count:
            self.resources.setCurrentItem(self.resources.topLevelItem(0))
        else:
            self.select(None, None)

    def render_asset(self, path, asset):
        properties = f'{path}\n{asset.media_type} · {len(asset.data):,} 字节'
        pixmap = QPixmap()
        if asset.media_type.startswith('image/') and pixmap.loadFromData(asset.data):
            properties += f' · {pixmap.width()} × {pixmap.height()} 像素'
            if pixmap.width() > 338 or pixmap.height() > 170:
                pixmap = pixmap.scaled(338, 170, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.image.setPixmap(pixmap)
            self.preview.setCurrentWidget(self.image)
        elif asset.media_type == 'text/css':
            self.css.setPlainText(asset.data.decode('utf-8-sig', errors='replace'))
            self.preview.setCurrentWidget(self.css)
        else:
            self.placeholder.setText('可在上方查看此资源的文件属性。')
            self.preview.setCurrentWidget(self.placeholder)
        self.properties.setText(properties)

    def select(self, current, _previous):
        self.pending = None
        self.apply_button.setEnabled(False)
        self.locations.clear()
        self.update_jump_button(None)
        if current is None:
            self.properties.clear()
            self.placeholder.setText('当前列表没有资源。')
            self.preview.setCurrentWidget(self.placeholder)
            self.choose_button.setEnabled(False)
            self.note.clear()
            return
        path = current.data(0, Qt.ItemDataRole.UserRole)
        asset = self.book.assets[path]
        self.render_asset(path, asset)
        for reference in self.references.get(path, ()):
            item = QListWidgetItem(reference.label, self.locations)
            item.setData(Qt.ItemDataRole.UserRole, reference.chapter)
        if not self.locations.count():
            self.locations.addItem('本次扫描未发现引用。')
        self.choose_button.setEnabled(can_replace(asset))
        self.note.setText('可用相同格式的文件替换此资源。' if can_replace(asset) else '此类型可查看属性和引用位置。')

    def update_jump_button(self, current, _previous=None):
        row = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self.jump_button.setEnabled(row is not None and self.pending is None)

    def open_reference(self):
        if self.jump_button.isEnabled():
            self.reference_index = self.locations.currentItem().data(Qt.ItemDataRole.UserRole)
            self.accept()

    def choose_replacement(self):
        item = self.resources.currentItem()
        if item is None:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        current = self.book.assets[path]
        file_filter = ('样式表 (*.css)' if current.media_type == 'text/css'
                       else '图片 (*.jpg *.jpeg *.png *.gif *.webp)')
        filename, _ = QFileDialog.getOpenFileName(self, '选择替换文件', '', file_filter)
        if not filename:
            return
        chosen = Path(filename)
        try:
            asset = replacement_asset(self.book, path, chosen.read_bytes())
        except (OSError, ValueError) as error:
            self.select(item, None)
            self.note.setText(str(error))
            return
        self.pending = (path, asset)
        self.render_asset(path, asset)
        self.note.setText(f'预览替换文件「{chosen.name}」。应用后，引用此书内路径的内容会使用新文件。')
        self.apply_button.setEnabled(True)
        self.update_jump_button(self.locations.currentItem())


def show_book_resources(studio):
    original = studio.book
    dialog = BookResourcesDialog(studio, original)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    if dialog.reference_index is not None:
        studio.go_to_chapter(dialog.reference_index)
        return
    if dialog.pending is None:
        return
    path, replacement = dialog.pending
    revision = studio.edit_revision
    book = deepcopy(original)
    book.assets[path] = replacement

    def apply(_checkpoint):
        if studio.book is not original or studio.edit_revision != revision:
            studio.error('书稿已有变化，请重新预览替换。')
            return
        studio.adopt_book_edit(book)
        studio.statusBar().showMessage(f'已替换 {path}，已保存替换前检查点。')

    studio.start_task('正在保存替换前检查点…',
                      lambda: studio.workspace.save_checkpoint(original, '书籍资源替换前'), apply)
