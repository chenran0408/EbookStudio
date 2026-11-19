"""Book metadata editor. Changes are applied by the caller after acceptance."""
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ebook_core import Book


_STYLE = """
QDialog#bookInfo { background:#f4f6fa; color:#182236; }
QDialog#bookInfo QLabel { color:#182236; }
QDialog#bookInfo QLabel#coverFile, QDialog#bookInfo QLabel#coverHint { color:#56657d; }
QDialog#bookInfo QWidget#coverPanel {
    background:#fff; border:1px solid #dce3ee; border-radius:8px;
}
QDialog#bookInfo QLabel#coverPreview {
    background:#f4f6fa; border:1px solid #dce3ee; border-radius:5px; color:#56657d;
}
QDialog#bookInfo QLabel#coverHeading { font-weight:700; }
QDialog#bookInfo QLineEdit, QDialog#bookInfo QPushButton {
    border:1px solid #dce3ee; border-radius:6px; color:#182236;
    background:#fff; padding:6px 10px;
}
QDialog#bookInfo QLineEdit:focus, QDialog#bookInfo QPushButton:focus {
    border:1px solid #2453c7;
}
QDialog#bookInfo QPushButton:hover { background:#edf2fc; }
QDialog#bookInfo QPushButton:disabled { background:#f4f6fa; color:#56657d; }
QDialog#bookInfo QPushButton#saveBookInfo {
    background:#2453c7; color:#fff; border-color:#2453c7; font-weight:700;
}
QDialog#bookInfo QPushButton#saveBookInfo:hover { background:#1d46ae; }
QDialog#bookInfo QPushButton#saveBookInfo:focus { border-color:#182236; }
QDialog#bookInfo QPushButton#saveBookInfo:disabled {
    background:#e2e8f5; border-color:#dce3ee; color:#56657d;
}
"""


class BookInfoDialog(QDialog):
    """Edit metadata and stage a cover change without modifying the book."""

    def __init__(self, parent: QWidget | None, book: Book) -> None:
        super().__init__(parent)
        self.setObjectName("bookInfo")
        self.setWindowTitle("书籍资料")
        self.setStyleSheet(_STYLE)
        self.setMinimumWidth(460)
        self.resize(520, 355)
        self.cover_path: Path | None = None
        self.remove_cover = False

        self.title = QLineEdit(book.title)
        self.author = QLineEdit(book.author)
        self.language = QLineEdit(book.language)
        self.title.setPlaceholderText("输入书名")
        self.author.setPlaceholderText("可留空")
        self.language.setPlaceholderText("例如 zh-CN 或 en")
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for label_text, field in (
            ("书名", self.title), ("作者", self.author), ("语言", self.language),
        ):
            label = QLabel(label_text)
            label.setBuddy(field)
            field.setAccessibleName(label_text)
            form.addRow(label, field)
        self.title.setAccessibleDescription("书名必填")
        self.language.setAccessibleDescription("出版语言，例如 zh-CN 或 en")

        panel = QWidget(objectName="coverPanel")
        cover = QHBoxLayout(panel)
        cover.setContentsMargins(12, 12, 12, 12)
        cover.setSpacing(14)
        self.preview = QLabel(objectName="coverPreview")
        self.preview.setFixedSize(102, 136)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setAccessibleName("封面预览")
        cover.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignTop)

        details = QVBoxLayout()
        details.setSpacing(5)
        details.addWidget(QLabel("封面", objectName="coverHeading"))
        self.cover_name = QLabel(objectName="coverFile")
        self.cover_name.setTextFormat(Qt.TextFormat.PlainText)
        self.cover_name.setWordWrap(True)
        self.cover_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        details.addWidget(self.cover_name)
        self.cover_hint = QLabel(objectName="coverHint")
        self.cover_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.cover_hint.setWordWrap(True)
        details.addWidget(self.cover_hint)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.pick_button = QPushButton("选择图片…")
        self.pick_button.setAccessibleDescription("从本机选择电子书封面")
        self.pick_button.clicked.connect(self.pick_cover)
        self.remove_button = QPushButton("移除封面")
        self.remove_button.clicked.connect(self.clear_cover)
        for button in (self.pick_button, self.remove_button):
            button.setAutoDefault(False)
            actions.addWidget(button)
        actions.addStretch()
        details.addLayout(actions)
        details.addStretch(1)
        cover.addLayout(details, 1)

        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton("保存资料", QDialogButtonBox.ButtonRole.AcceptRole)
        self.save_button.setObjectName("saveBookInfo")
        self.save_button.setDefault(True)
        cancel_button = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        cancel_button.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        for field in (self.title, self.language):
            field.textChanged.connect(self.update_save_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(16)
        layout.addLayout(form)
        layout.addWidget(panel)
        layout.addStretch(1)
        layout.addWidget(buttons)

        self.update_save_button()
        if book.cover:
            asset = book.assets.get(book.cover)
            if asset is None:
                self.show_cover(Path(book.cover).name, None)
            else:
                buffer = QBuffer()
                buffer.setData(QByteArray(asset.data))
                buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                self.show_cover(Path(book.cover).name, QImageReader(buffer))
        else:
            self.show_cover("", None)
        self.title.setFocus()

    def update_save_button(self) -> None:
        self.save_button.setEnabled(bool(self.title.text().strip() and self.language.text().strip()))

    def pick_cover(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "选择封面", "", "图片 (*.jpg *.jpeg *.png *.gif *.svg)",
        )
        if filename:
            self.cover_path = Path(filename)
            self.remove_cover = False
            self.show_cover(self.cover_path.name, QImageReader(str(self.cover_path)))

    def clear_cover(self) -> None:
        self.cover_path = None
        self.remove_cover = True
        self.show_cover("", None)

    def show_cover(self, name: str, reader: QImageReader | None) -> None:
        self.preview.clear()
        image = None
        if reader is not None and reader.canRead():
            reader.setAutoTransform(True)
            size = reader.size()
            bounds = QSize(90, 124)
            if size.isValid() and (size.width() > bounds.width() or size.height() > bounds.height()):
                reader.setScaledSize(size.scaled(bounds, Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
        if image is not None and not image.isNull():
            pixmap = QPixmap.fromImage(image)
            self.preview.setPixmap(pixmap.scaled(
                90, 124, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
            ))
            self.cover_hint.clear()
        else:
            self.preview.setText("无预览" if name else "暂无封面")
            self.cover_hint.setText("无法预览此图片，可重新选择。" if name else "")
        self.cover_hint.setVisible(bool(self.cover_hint.text()))
        self.cover_name.setText(name or "选择本机图片作为封面")
        self.cover_name.setToolTip(name)
        self.remove_button.setEnabled(bool(name))
