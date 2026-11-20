"""Select two external EPUB files and compare them in the version viewer."""
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from checkpoint_compare_dialog import CheckpointCompareDialog
from epub_compare import compare_epubs


class EpubFilesDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("比较两份 EPUB")
        self.resize(640, 180)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择两个 EPUB 文件。左侧显示原版，右侧显示修订版。"))
        form = QGridLayout()
        layout.addLayout(form)
        self.fields = []
        for row, name in enumerate(("原版", "修订版")):
            label = QLabel(name)
            field = QLineEdit()
            field.setAccessibleName(name + " EPUB 路径")
            field.setPlaceholderText("选择文件或粘贴路径")
            label.setBuddy(field)
            browse = QPushButton("选择" + name + "…")
            browse.clicked.connect(lambda _, i=row: self.choose(i))
            form.addWidget(label, row, 0)
            form.addWidget(field, row, 1)
            form.addWidget(browse, row, 2)
            self.fields.append(field)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.compare = buttons.addButton("开始比较", QDialogButtonBox.ButtonRole.AcceptRole)
        self.compare.setDefault(True)
        self.compare.setEnabled(False)
        for field in self.fields:
            field.textChanged.connect(self.refresh)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.fields[0].setFocus()

    def refresh(self):
        self.status.clear()
        self.compare.setEnabled(all(field.text().strip() for field in self.fields))

    def choose(self, index):
        field = self.fields[index]
        start = field.text().strip() or str(Path(self.fields[1-index].text().strip()).parent)
        title = "选择原版 EPUB" if index == 0 else "选择修订版 EPUB"
        path, _ = QFileDialog.getOpenFileName(self, title, start, "EPUB 电子书 (*.epub)")
        if path:
            field.setText(path)

    def accept(self):
        self.paths = tuple(Path(field.text().strip()) for field in self.fields)
        for path, field in zip(self.paths, self.fields):
            if not path.is_file():
                self.status.setText(f"找不到文件：{path}")
                field.setFocus()
                return
        super().accept()


def show_epub_compare(studio):
    if studio.task is not None:
        return
    dialog = EpubFilesDialog(studio)
    result = dialog.exec()
    dialog.deleteLater()
    if result != QDialog.DialogCode.Accepted:
        return
    original, revision = dialog.paths

    def present(changes):
        view = CheckpointCompareDialog(studio, None, changes, files=(original, revision))
        view.exec()
        view.deleteLater()
        studio.statusBar().showMessage(f"EPUB 对比完成 · {len(changes)} 项变化")

    studio.start_task("正在比较两份 EPUB…", lambda: compare_epubs(original, revision), present)
