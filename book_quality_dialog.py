"""Display chapter-level issues and move the workspace to a selected chapter."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from book_quality import inspect_book


class BookQualityDialog(QDialog):
    def __init__(self, parent, book):
        super().__init__(parent)
        self.setWindowTitle("检查书稿")
        self.resize(640, 440)
        self.chapter_index = None

        issues = inspect_book(book)
        errors = sum(issue.severity == "error" for issue in issues)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        heading = QLabel(f"{len(book.chapters)} 个章节 · {errors} 项需修正 · {len(issues) - errors} 项提醒")
        heading.setObjectName("section")
        layout.addWidget(heading)
        hint = QLabel("检查空章节、目录层级、图片和书内链接。导出后的 EPUB 仍可用 EPUBCheck 验证。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.results = QTreeWidget()
        self.results.setAccessibleName("书稿检查结果")
        self.results.setHeaderLabels(["状态", "问题", "章节"])
        self.results.setRootIsDecorated(False)
        self.results.setUniformRowHeights(True)
        self.results.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.results.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        if issues:
            for issue in issues:
                chapter = book.chapters[issue.chapter].title if issue.chapter is not None else "全书"
                status = "需修正" if issue.severity == "error" else "提醒"
                row = QTreeWidgetItem(self.results, [status, issue.title, chapter or "未命名章节"])
                row.setData(0, Qt.ItemDataRole.UserRole, issue.chapter)
                row.setData(1, Qt.ItemDataRole.UserRole, issue.detail)
            layout.addWidget(self.results, 1)
        else:
            empty = QLabel("当前检查未发现问题。")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(empty, 1)
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail.setObjectName("muted")
        self.detail.setVisible(bool(issues))
        layout.addWidget(self.detail)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        close.setText("关闭")
        self.jump = QPushButton("打开章节")
        self.jump.setObjectName("primary")
        self.jump.setEnabled(False)
        self.jump.setVisible(bool(issues))
        buttons.addButton(self.jump, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        self.jump.clicked.connect(self.open_chapter)
        self.results.currentItemChanged.connect(self.update_selection)
        self.results.itemActivated.connect(lambda _item, _column: self.open_chapter())
        layout.addWidget(buttons)
        if issues:
            self.results.setCurrentItem(self.results.topLevelItem(0))
            self.results.setFocus()
        else:
            close.setFocus()

    def update_selection(self, current, _previous):
        self.chapter_index = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        self.detail.setText(current.data(1, Qt.ItemDataRole.UserRole) if current else "")
        self.jump.setEnabled(self.chapter_index is not None)

    def open_chapter(self):
        if self.chapter_index is not None:
            self.accept()


def show_book_quality(studio):
    dialog = BookQualityDialog(studio, studio.book)
    if dialog.exec() == QDialog.DialogCode.Accepted and dialog.chapter_index is not None:
        studio.go_to_chapter(dialog.chapter_index)
