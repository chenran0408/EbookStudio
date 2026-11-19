"""Native cross-book phrase search and background catalog indexing."""
import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from book_library import BOOK_FILTER
from book_library_text import LibraryTextIndex, PAGE_SIZE


class _SearchTask(QThread):
    progress = Signal(int, int, str)

    def __init__(self, parent, library, phrase, offset, refresh, paths):
        super().__init__(parent)
        self.library, self.phrase = library, phrase
        self.paths, self.import_errors = paths, []
        self.offset, self.refresh = offset, refresh
        self.hits, self.more, self.report, self.error = [], False, None, ''

    def run(self):
        try:
            if self.paths:
                result = self.library.add(self.paths)
                self.import_errors = [f"{path.name} · {reason}" for path, reason in result.errors]
            index = LibraryTextIndex(self.library)
            if self.refresh:
                self.report = index.refresh(self.progress.emit, self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.hits, self.more = index.find(self.phrase, self.offset)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.error = str(exc)


class LibraryTextDialog(QDialog):
    def __init__(self, parent, library):
        super().__init__(parent)
        self.library = library
        self.hit = self.task = None
        self.closing = False
        self.offset = 0
        self.setWindowTitle('跨书全文检索')
        self.resize(830, 500)
        layout = QVBoxLayout(self)
        form = QHBoxLayout()
        label = QLabel('查找正文')
        self.query = QLineEdit()
        self.query.setPlaceholderText('输入记得的一句正文')
        self.query.setAccessibleName('跨书查找文字')
        self.query.setClearButtonEnabled(True)
        label.setBuddy(self.query)
        form.addWidget(label)
        form.addWidget(self.query, 1)
        self.search = QPushButton('搜索')
        self.search.setObjectName('primary')
        self.refresh = QPushButton('刷新索引')
        form.addWidget(self.search)
        form.addWidget(self.refresh)
        layout.addLayout(form)
        self.status = QLabel('搜索已加入书库的 EPUB 和工程。首次搜索会在本机建立正文索引。')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.table = QTreeWidget()
        self.table.setAccessibleName('跨书正文搜索结果')
        self.table.setHeaderLabels(['书名', '期号', '章节', '命中片段'])
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(2, 166)
        layout.addWidget(self.table, 1)
        self.context = QPlainTextEdit()
        self.context.setAccessibleName('所选命中片段与原文件')
        self.context.setReadOnly(True)
        self.context.setMaximumHeight(83)
        layout.addWidget(self.context)
        self.problem = QLabel()
        self.problem.setTextFormat(Qt.TextFormat.PlainText)
        self.problem.setWordWrap(True)
        self.problem.hide()
        layout.addWidget(self.problem)
        actions = QHBoxLayout()
        self.previous = QPushButton('上一页')
        self.next = QPushButton('下一页')
        self.add = QPushButton("添加书籍…")
        self.add.clicked.connect(self.add_books)
        actions.addWidget(self.add)
        actions.addWidget(self.previous)
        actions.addWidget(self.next)
        actions.addStretch()
        self.open = QPushButton('打开命中章节')
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        actions.addWidget(self.open)
        actions.addWidget(buttons)
        layout.addLayout(actions)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.previous.clicked.connect(lambda: self.run(max(0, self.offset-PAGE_SIZE), False))
        self.next.clicked.connect(lambda: self.run(self.offset+PAGE_SIZE, False))
        self.open.clicked.connect(self.open_hit)
        self.table.itemActivated.connect(lambda *_: self.open_hit())
        self.table.currentItemChanged.connect(self.selection)
        self.search.clicked.connect(lambda: self.run(0, True))
        self.refresh.clicked.connect(lambda: self.run(0, True))
        self.query.returnPressed.connect(lambda: self.run(0, True) if self.search.isEnabled() else None)
        self.query.textChanged.connect(self.clear_results)
        buttons.rejected.connect(self.reject)
        self.clear_results()
        self.query.setFocus()

    def clear_results(self):
        self.table.clear()
        self.context.clear()
        self.open.setEnabled(False)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.search.setEnabled(bool(self.query.text().strip()))
        if self.query.text().strip():
            self.status.setText('按 Enter 或点击「搜索」查找正文。')

    def add_books(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '添加到书库', '', BOOK_FILTER)
        if paths:
            self.run(0, True, tuple(Path(path) for path in paths))

    def run(self, offset, refresh, paths=()):
        if self.task is not None:
            return
        self.clear_results()
        self.query.setReadOnly(True)
        self.search.setEnabled(False)
        self.refresh.setEnabled(False)
        self.add.setEnabled(False)
        self.status.setText('正在搜索书库…')
        self.task = _SearchTask(self, self.library, self.query.text().strip(), offset, refresh, paths)
        self.task.progress.connect(self.progress)
        self.task.finished.connect(self.finished_search)
        self.task.start()

    @Slot(int, int, str)
    def progress(self, number, total, name):
        if not self.closing:
            self.status.setText(f'正在检查正文索引 {number}/{total} · {name}')

    @Slot()
    def finished_search(self):
        task, self.task = self.task, None
        task.deleteLater()
        if self.closing:
            super().reject()
            return
        self.query.setReadOnly(False)
        self.search.setEnabled(bool(self.query.text().strip()))
        self.refresh.setEnabled(True)
        self.add.setEnabled(True)
        self.offset = task.offset
        if task.report is not None:
            errors = [*task.import_errors, *task.report.errors]
            self.problem.setVisible(bool(errors))
            self.problem.setText(f'{len(errors)} 个文件未计入结果。{errors[0]}' if errors else '')
            self.problem.setToolTip('\n'.join(errors))
        if task.error:
            self.status.setText(f'无法搜索书库。{task.error}')
            return
        if not task.phrase:
            self.status.setText(f'索引已刷新。更新 {task.report.updated} 本，复用 {task.report.reused} 本。')
            return
        for hit in task.hits:
            item = QTreeWidgetItem([hit.book_title, hit.issue or '未知期号', hit.chapter_title, hit.snippet])
            item.setData(0, Qt.ItemDataRole.UserRole, hit)
            item.setToolTip(0, str(hit.path))
            item.setToolTip(3, hit.snippet)
            self.table.addTopLevelItem(item)
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(task.more)
        if task.hits:
            self.status.setText(f'命中章节 {self.offset+1}–{self.offset+len(task.hits)}。双击可打开章节。')
            self.table.setCurrentItem(self.table.topLevelItem(0))
            self.table.setFocus()
        elif task.report and not any((task.report.updated, task.report.reused, task.report.errors)):
            self.status.setText('书库里还没有书。点击「添加书籍」选择 EPUB 或工程，然后继续搜索。')
        else:
            self.status.setText('未找到匹配的正文。可以缩短句子再试。')

    def selection(self, item):
        hit = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        self.open.setEnabled(hit is not None)
        self.context.setPlainText(f'{hit.snippet}\n{hit.path}' if hit else '')

    def open_hit(self):
        item = self.table.currentItem()
        if item is None or self.task is not None:
            return
        hit = item.data(0, Qt.ItemDataRole.UserRole)
        try:
            current = hit.current()
        except OSError:
            current = False
        if not current:
            self.status.setText('原文件已更改或移动，请刷新索引。')
            return
        self.hit = hit
        self.accept()

    def reject(self):
        if self.task is None:
            super().reject()
        else:
            self.closing = True
            self.task.requestInterruption()
            self.status.setText('将在当前文件处理后关闭。')

    def closeEvent(self, event):
        if self.task is None:
            super().closeEvent(event)
        else:
            self.reject()
            event.ignore()
