"""Searchable start-page shelf for recently opened and pinned files."""
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout, QWidget,
)


class ProjectShelf(QWidget):
    open_requested = Signal(object)
    pin_failed = Signal(str)
    library_requested = Signal()

    def __init__(self, workspace):
        super().__init__(objectName="recentPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.workspace = workspace
        self.paths: list[Path] = []
        self.pins: set[Path] = set()
        self.available: set[Path] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("我的书稿", objectName="section"), 1)
        self.library_button = QPushButton("书库")
        self.library_button.setAccessibleName("打开书库")
        self.library_button.setToolTip("按作者和标签管理本机书籍")
        self.library_button.clicked.connect(self.library_requested)
        heading.addWidget(self.library_button)
        self.pinned_only = QPushButton("只看置顶", checkable=True)
        heading.addWidget(self.pinned_only)
        layout.addLayout(heading)

        self.search = QLineEdit(objectName="shelfSearch")
        self.search.setPlaceholderText("搜索文件名或路径")
        self.search.setAccessibleName("搜索书稿文件名或路径")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)

        self.files = QListWidget(objectName="recentList")
        self.files.setAccessibleName("书稿文件列表，置顶文件在前")
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.files.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.files.setUniformItemSizes(True)
        self.files.setMinimumHeight(144)
        self.files.setMaximumHeight(180)
        layout.addWidget(self.files)

        self.empty = QLabel(objectName="muted")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        actions = QHBoxLayout()
        actions.addStretch()
        self.pin_button = QPushButton("置顶")
        actions.addWidget(self.pin_button)
        self.open_button = QPushButton("打开书稿")
        actions.addWidget(self.open_button)
        layout.addLayout(actions)

        self.pinned_only.toggled.connect(self.filter)
        self.search.textChanged.connect(self.filter)
        self.search.returnPressed.connect(self.open_selected)
        self.files.currentItemChanged.connect(self.update_actions)
        self.files.itemActivated.connect(self.open_selected)
        self.pin_button.clicked.connect(self.toggle_pin)
        self.open_button.clicked.connect(self.open_selected)

    def refresh(self):
        pins = self.workspace.pinned()
        recent = self.workspace.recent()
        self.paths = list(dict.fromkeys([*pins, *recent]))
        self.pins = set(pins)
        self.available = set(recent) | {path for path in pins if path.is_file()}
        self.filter()

    def selected_path(self) -> Path | None:
        item = self.files.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def filter(self):
        selected = self.selected_path()
        query = self.search.text().strip().casefold()
        only_pins = self.pinned_only.isChecked()
        with QSignalBlocker(self.files):
            self.files.clear()
            for path in self.paths:
                pinned = path in self.pins
                if (only_pins and not pinned) or query not in str(path).casefold():
                    continue
                state = " · 已置顶" if pinned else ""
                if path not in self.available:
                    state = " · 路径不可用"
                item = QListWidgetItem(path.name + state, self.files)
                item.setData(Qt.ItemDataRole.UserRole, path)
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, str(path))
                item.setToolTip(str(path))
                if path == selected:
                    self.files.setCurrentItem(item)
            if self.files.currentRow() < 0 and self.files.count():
                self.files.setCurrentRow(0)

        has_results = bool(self.files.count())
        self.files.setVisible(has_results)
        self.empty.setVisible(not has_results)
        if not has_results:
            if query:
                message = "没有匹配的书稿。试试其他文件名或路径。"
            elif only_pins:
                message = "还没有置顶。关闭「只看置顶」可从最近打开的书稿中选择。"
            else:
                message = "打开或保存一本书后，就可以在这里找到它。"
            self.empty.setText(message)
        self.update_actions()

    def update_actions(self):
        path = self.selected_path()
        pinned = path in self.pins
        self.pin_button.setText("取消置顶" if pinned else "置顶")
        self.pin_button.setEnabled(pinned or path in self.available)
        self.open_button.setEnabled(path in self.available)

    def toggle_pin(self):
        path = self.selected_path()
        if path is None:
            return
        try:
            self.workspace.set_pinned(path, path not in self.pins)
        except OSError as exc:
            self.pin_failed.emit(f"无法保存书稿的置顶状态。\n{exc}")
            return
        self.refresh()
        if not self.pin_button.isEnabled():
            self.search.setFocus()

    def open_selected(self):
        path = self.selected_path()
        if path in self.available:
            self.open_requested.emit(path)
