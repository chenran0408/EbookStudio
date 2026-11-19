"""Present EPUBCheck findings and open matching chapters in the workspace."""

from urllib.parse import unquote

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHeaderView, QLabel, QPlainTextEdit,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)


class EpubCheckDialog(QDialog):
    def __init__(self, parent, report, name, *, context="", chapters=()):
        super().__init__(parent)
        self.setWindowTitle("EPUBCheck 校验报告")
        self.resize(670, 460)
        self.report = report
        self.chapter_index = None
        self.chapter_rows = {chapter.path: row for row, chapter in enumerate(chapters)}
        outcome = "校验通过" if report.valid else "校验未通过"
        self.summary = f"{outcome} · {report.summary}"
        self.context = "\n".join(filter(None, (name, context, f"EPUBCheck {report.version}")))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(QLabel(self.summary, objectName="section"))
        subtitle = QLabel(self.context, objectName="muted")
        subtitle.setTextFormat(Qt.TextFormat.PlainText)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.results = QTreeWidget(self)
        self.results.setAccessibleName("EPUBCheck 问题列表")
        self.results.setHeaderLabels(["级别", "代码", "书内位置", "问题"])
        self.results.setRootIsDecorated(False)
        self.results.setUniformRowHeights(True)
        self.results.setAllColumnsShowFocus(True)
        self.results.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.results.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.results.setColumnWidth(2, 160)
        for index, issue in enumerate(report.issues):
            location = issue.locations[0].display_path if issue.locations else ""
            row = QTreeWidgetItem(self.results, [issue.severity_label, issue.code, location, issue.message])
            row.setData(0, Qt.ItemDataRole.UserRole, index)
            row.setToolTip(2, location)
            row.setToolTip(3, issue.message)
        self.details = QPlainTextEdit(self)
        self.details.setAccessibleName("所选问题详情")
        self.details.setReadOnly(True)
        if report.issues:
            layout.addWidget(self.results, 3)
            layout.addWidget(self.details, 2)
        else:
            empty = QLabel("未发现 EPUB 标准符合性问题。")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(empty, 1)
            self.results.hide()
            self.details.hide()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_button.setText("关闭")
        close_button.setAutoDefault(False)
        self.copy_button = QPushButton("复制报告")
        self.copy_button.setAutoDefault(False)
        self.jump = QPushButton("打开章节", objectName="primary")
        self.jump.setAutoDefault(False)
        self.jump.setEnabled(False)
        buttons.addButton(self.copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(self.jump, QDialogButtonBox.ButtonRole.ActionRole)
        self.jump.setVisible(bool(self.chapter_rows and report.issues))
        buttons.rejected.connect(self.reject)
        self.copy_button.clicked.connect(self.copy_report)
        self.jump.clicked.connect(self.open_chapter)
        self.results.currentItemChanged.connect(self.update_selection)
        self.results.itemActivated.connect(lambda _item, _column: self.open_chapter())
        layout.addWidget(buttons)
        if report.issues:
            self.results.setCurrentItem(self.results.topLevelItem(0))
            self.results.setFocus()
        else:
            close_button.setFocus()

    def update_selection(self, current, _previous):
        self.chapter_index = None
        if current is None:
            self.details.clear()
        else:
            issue = self.report.issues[current.data(0, Qt.ItemDataRole.UserRole)]
            self.details.setPlainText(issue.details())
            for location in issue.locations:
                row = self.chapter_rows.get(unquote(location.path), self.chapter_rows.get(location.path))
                if row is not None:
                    self.chapter_index = row
                    break
        self.jump.setEnabled(self.chapter_index is not None)

    def copy_report(self):
        sections = [self.summary, self.context, *(issue.details() for issue in self.report.issues)]
        QApplication.clipboard().setText("\n\n".join(sections))
        self.copy_button.setText("已复制报告")

    def open_chapter(self):
        if self.chapter_index is not None:
            self.accept()
