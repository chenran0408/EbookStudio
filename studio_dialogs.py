"""Native dialogs for scan recognition and ebook export selection."""

from collections.abc import Iterable, Mapping, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout,
)

from workspace_tools import ToolStatus, ocr_readiness


DIALOG_STYLE = """
QDialog#studioDialog { background: #f4f6fa; color: #182236; }
QDialog#studioDialog QLabel { background: transparent; color: #182236; }
QDialog#studioDialog QLabel#studioDialogHeading { font-size: 19px; font-weight: 700; }
QDialog#studioDialog QLabel#studioDialogSection { font-weight: 700; }
QDialog#studioDialog QLabel#studioDialogMuted { color: #56657d; }
QDialog#studioDialog QLabel#studioDialogStatus { color: #56657d; font-weight: 600; }
QDialog#studioDialog QLabel#studioDialogStatus[ready="true"] { color: #2453c7; }
QDialog#studioDialog QFrame#studioDialogPanel {
    background: #fff; border: 1px solid #dce3ee; border-radius: 8px;
}
QDialog#studioDialog QLineEdit, QDialog#studioDialog QComboBox {
    background: #fff; color: #182236; border: 1px solid #c7d2e4;
    border-radius: 5px; min-height: 21px; padding: 5px 8px;
}
QDialog#studioDialog QLineEdit:focus, QDialog#studioDialog QComboBox:focus {
    border: 1px solid #2453c7;
}
QDialog#studioDialog QCheckBox { color: #182236; spacing: 7px; min-height: 22px; }
QDialog#studioDialog QCheckBox:disabled { color: #627088; }
QDialog#studioDialog QPushButton {
    background: #fff; color: #182236; border: 1px solid #c7d2e4;
    border-radius: 5px; min-height: 20px; padding: 6px 12px;
}
QDialog#studioDialog QPushButton:hover { background: #eef3fb; }
QDialog#studioDialog QPushButton:focus { border-color: #2453c7; }
QDialog#studioDialog QPushButton#studioDialogPrimary {
    background: #2453c7; color: #fff; border: 1px solid #2453c7; font-weight: 600;
}
QDialog#studioDialog QPushButton#studioDialogPrimary:hover { background: #1d47ae; }
QDialog#studioDialog QPushButton#studioDialogPrimary:focus { border: 1px solid #112e75; }
QDialog#studioDialog QPushButton#studioDialogPrimary:disabled {
    background: #e6ebf4; color: #56657d; border: 1px solid #dce3ee;
}
"""

MODELS = (
    ("xiaomi", "Xiaomi-OCR-0 Q8", "适合单栏正文和简单表格，识别较快。"),
    ("paddle", "PaddleOCR-VL-1.6", "适合双栏杂志、插图和有合并单元格的表格。"),
)


def _label(text: str, role: str) -> QLabel:
    label = QLabel(text, objectName=role)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _layout(dialog: QDialog, title: str) -> QVBoxLayout:
    dialog.setObjectName("studioDialog")
    dialog.setWindowTitle(title)
    dialog.setStyleSheet(DIALOG_STYLE)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 19, 22, 18)
    layout.setSpacing(13)
    return layout


def _panel(title: str) -> tuple[QFrame, QVBoxLayout]:
    panel = QFrame(objectName="studioDialogPanel")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(14, 11, 14, 11)
    layout.setSpacing(7)
    layout.addWidget(_label(title, "studioDialogSection"))
    return panel, layout


def _actions(dialog: QDialog, label: str) -> tuple[QDialogButtonBox, QPushButton]:
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    primary = buttons.button(QDialogButtonBox.StandardButton.Ok)
    primary.setObjectName("studioDialogPrimary")
    primary.setText(label)
    primary.setDefault(True)
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return buttons, primary


class ScanDialog(QDialog):
    """Select a local OCR model and the pages to recognize."""

    def __init__(
        self, parent, *, multiple_pages: bool, source_name: str = "",
        statuses: Mapping[str, ToolStatus] | None = None,
    ):
        super().__init__(parent)
        self.statuses = ocr_readiness() if statuses is None else statuses
        layout = _layout(self, "扫描件 OCR")
        heading = _label("识别扫描件", "studioDialogHeading")
        layout.addWidget(heading)
        if source_name:
            source = _label(source_name, "studioDialogMuted")
            source.setToolTip(source_name)
            source.setAccessibleName("扫描文件")
            layout.addWidget(source)

        panel, panel_layout = _panel("识别模型")
        self.model = QComboBox()
        self.model.setAccessibleName("识别模型")
        for key, name, hint in MODELS:
            state = self.statuses[key]
            self.model.addItem(name if state.ready else f"{name}（待配置）", key)
            self.model.setItemData(self.model.count() - 1, hint, Qt.ItemDataRole.ToolTipRole)
        panel_layout.addWidget(self.model)

        self.usage = _label("", "studioDialogMuted")
        self.status = _label("", "studioDialogStatus")
        self.detail = _label("", "studioDialogMuted")
        panel_layout.addWidget(self.usage)
        panel_layout.addWidget(self.status)
        panel_layout.addWidget(self.detail)
        layout.addWidget(panel)

        self.pages = QLineEdit()
        self.pages.setAccessibleName("页码")
        self.pages.setPlaceholderText("例如 1,3-5")
        if multiple_pages:
            selection = QHBoxLayout()
            page_label = _label("页码", "studioDialogSection")
            page_label.setBuddy(self.pages)
            selection.addWidget(page_label)
            selection.addWidget(self.pages, 1)
            layout.addLayout(selection)
            layout.addWidget(_label("留空识别全部页。", "studioDialogMuted"))
        else:
            self.pages.hide()

        self.actions, self.recognize = _actions(self, "开始识别")
        layout.addWidget(self.actions)
        self.model.currentIndexChanged.connect(self.update_model)
        if not self.statuses["xiaomi"].ready and self.statuses["paddle"].ready:
            self.model.setCurrentIndex(1)
        self.update_model()
        self.model.setFocus()
        self.resize(490, self.sizeHint().height())

    def update_model(self, _index=None):
        index = self.model.currentIndex()
        state = self.statuses[self.model.currentData()]
        self.usage.setText(MODELS[index][2])
        self.status.setText("本机已就绪" if state.ready else "需要配置模型")
        self.status.setProperty("ready", state.ready)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.detail.setText(state.message if not state.ready else "")
        self.detail.setVisible(not state.ready)
        self.recognize.setEnabled(state.ready)

    def accept(self):
        if self.statuses[self.model.currentData()].ready:
            super().accept()


class ExportDialog(QDialog):
    """Select output formats after the caller checks each format's tools."""

    def __init__(
        self, parent, *, title: str, labels: Mapping[str, str],
        format_groups: Iterable[tuple[str, Iterable[str]]],
        missing: Mapping[str, Sequence[str]], validator_ready: bool,
    ):
        super().__init__(parent)
        layout = _layout(self, "导出电子书")
        book_title = _label(title or "未命名电子书", "studioDialogHeading")
        book_title.setAccessibleName("书名")
        layout.addWidget(book_title)

        self.outputs: dict[str, QCheckBox] = {}
        for group_name, keys in format_groups:
            formats = list(keys)
            if not formats:
                continue
            panel, panel_layout = _panel(group_name)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(3)
            for column in range(2):
                grid.setColumnStretch(column, 1)
            for index, key in enumerate(formats):
                box = QCheckBox(labels[key])
                box.setChecked(key == "epub" and not missing.get(key))
                if notes := missing.get(key):
                    box.setEnabled(False)
                    box.setToolTip("\n".join(notes))
                    box.setAccessibleDescription("\n".join(notes))
                grid.addWidget(box, index // 2, index % 2)
                self.outputs[key] = box
            panel_layout.addLayout(grid)
            layout.addWidget(panel)

        self.mobile = QCheckBox("为手机和平板压缩图片")
        self.mobile.setChecked(True)
        self.mobile.setToolTip("缩小电子书文件，保留正文内容和章节结构。")
        layout.addWidget(self.mobile)
        if any(missing.get(key) for key in self.outputs):
            layout.addWidget(_label("置灰的格式需要额外工具。可在「工具 → 检查本机工具」查看。",
                                    "studioDialogMuted"))
        if not validator_ready:
            layout.addWidget(_label("本机尚未配置 EPUBCheck。", "studioDialogMuted"))

        footer = QHBoxLayout()
        self.summary = _label("", "studioDialogMuted")
        self.summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        footer.addWidget(self.summary)
        self.actions, self.choose_location = _actions(self, "选择保存位置")
        footer.addWidget(self.actions)
        layout.addLayout(footer)
        for box in self.outputs.values():
            box.toggled.connect(self.update_selection)
        self.update_selection()
        self.resize(520, self.sizeHint().height())

    def formats(self) -> list[str]:
        return [key for key, box in self.outputs.items() if box.isEnabled() and box.isChecked()]

    def update_selection(self, _checked=False):
        count = len(self.formats())
        self.summary.setText(f"将生成 {count} 个文件" if count else "请选择至少一种格式")
        self.choose_location.setEnabled(count > 0)

    def accept(self):
        if self.formats():
            super().accept()
