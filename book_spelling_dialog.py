"""Qt spell-review and personal dictionary, shared by all books in a workspace."""
from __future__ import annotations

import sqlite3

from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from book_revision_dialogs import show_book_replace
from book_spelling import PersonalDictionary, english_suggestions, scan, term_expression, word_key


class ScanTask(QThread):
    def __init__(self, book, entries, parent):
        super().__init__(parent)
        self.book = book
        self.entries = entries
        self.findings = []
        self.error = ""

    def run(self):
        try:
            self.findings = scan(self.book, self.entries, self.isInterruptionRequested)
        except (OSError, ValueError) as error:
            self.error = str(error)


def tree(labels, name):
    widget = QTreeWidget()
    widget.setAccessibleName(name)
    widget.setHeaderLabels(labels)
    widget.setRootIsDecorated(False)
    widget.setAlternatingRowColors(True)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    return widget


class SpellDialog(QDialog):
    def __init__(self, studio):
        super().__init__(studio)
        self.setWindowTitle("拼写与个人词典")
        self.resize(720, 470)
        self.book = studio.book
        self.store = PersonalDictionary(studio.workspace.directory)
        self.entries = []
        self.findings = []
        self.task = None
        self.chapter = None
        self.correction = None

        outer = QVBoxLayout(self)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        results = QWidget()
        self.tabs.addTab(results, "拼写检查")
        layout = QVBoxLayout(results)
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("筛选拼写结果")
        self.search.setPlaceholderText("筛选词语或章节")
        layout.addWidget(self.search)
        self.list = tree(["待核对词语", "所在章节", "次数"], "拼写检查结果")
        self.list.header().setStretchLastSection(False)
        self.list.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.list.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.list, 1)
        self.context = QLabel()
        self.context.setWordWrap(True)
        self.context.setTextFormat(Qt.TextFormat.PlainText)
        self.context.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.context)
        form = QFormLayout()
        self.suggest = QComboBox()
        self.suggest.setAccessibleName("建议写法")
        form.addRow("建议写法", self.suggest)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.jump = QPushButton("打开所在章节")
        self.jump.clicked.connect(self.open_chapter)
        self.preview = QPushButton("预览替换…")
        self.preview.clicked.connect(self.correct)
        self.accept_word = QPushButton("认可此词")
        self.accept_word.clicked.connect(self.allow_word)
        for button in (self.jump, self.preview, self.accept_word):
            button.setAutoDefault(False)
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)

        page = QWidget()
        self.tabs.addTab(page, "个人词典")
        details = QVBoxLayout(page)
        hint = QLabel("专名可以只填词语。要检查常见错字，请同时填写错词和建议写法。")
        hint.setWordWrap(True)
        details.addWidget(hint)
        self.words = tree(["词语", "建议写法"], "个人词条")
        self.words.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        details.addWidget(self.words, 1)
        inputs = QFormLayout()
        self.word = QLineEdit()
        self.word.setAccessibleName("词语")
        inputs.addRow("词语", self.word)
        self.revised = QLineEdit()
        self.revised.setAccessibleName("词典建议写法")
        self.revised.setPlaceholderText("留空表示认可此词")
        inputs.addRow("建议写法", self.revised)
        details.addLayout(inputs)
        actions = QHBoxLayout()
        self.store_word = QPushButton("保存词条")
        self.store_word.clicked.connect(self.save_entry)
        self.remove = QPushButton("移除词条")
        self.remove.setEnabled(False)
        self.remove.clicked.connect(self.remove_entry)
        for button in (self.store_word, self.remove):
            button.setAutoDefault(False)
            actions.addWidget(button)
        actions.addStretch()
        details.addLayout(actions)

        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.search.textChanged.connect(self.render)
        self.list.currentItemChanged.connect(self.selection)
        self.list.itemDoubleClicked.connect(self.open_chapter)
        self.words.currentItemChanged.connect(self.edit_entry)
        self.tabs.currentChanged.connect(self.update_status)
        self.refresh()

    def refresh(self):
        self.entries = self.store.entries()
        self.words.clear()
        for word, fix in self.entries:
            self.words.addTopLevelItem(QTreeWidgetItem([word, fix]))
        self.word.clear()
        self.revised.clear()
        self.remove.setEnabled(False)
        self.stop_scan()
        self.findings = []
        self.task = ScanTask(self.book, self.entries, self)
        self.task.finished.connect(self.finished_scan)
        self.task.start()
        self.render()

    def stop_scan(self):
        if self.task is not None:
            self.task.finished.disconnect(self.finished_scan)
            self.task.requestInterruption()
            self.task.wait()
            self.task.deleteLater()
            self.task = None

    def finished_scan(self):
        if self.task is None or self.sender() is not self.task:
            return
        task = self.task
        self.task = None
        self.findings = task.findings
        self.render()
        if task.error:
            self.status.setText("无法读取英文词典：" + task.error)
        task.deleteLater()

    def done(self, code):
        self.stop_scan()
        super().done(code)

    def update_status(self):
        if self.tabs.currentIndex():
            self.status.setText(f"本机个人词典有 {len(self.entries)} 个词条，所有书稿均可使用。")
        elif self.task:
            self.status.setText("正在检查英文拼写和个人词典中的错词…")
        else:
            count = len(self.filtered)
            if count:
                extra = "，可输入词语或章节缩小范围" if count > 400 else ""
                self.status.setText(f"显示 {min(count, 400)} / {count} 条词语与章节记录{extra}。")
            else:
                self.status.setText("没有匹配筛选条件的词语。" if self.search.text() else "未发现英文拼写或个人词典中记录的错词。")

    def render(self):
        query = self.search.text().strip().casefold()
        self.filtered = [item for item in self.findings if query in item.word.casefold() or query in item.title.casefold()]
        self.list.clear()
        for item in self.filtered[:400]:
            self.list.addTopLevelItem(QTreeWidgetItem([item.word, item.title, str(item.count)]))
        if self.list.topLevelItemCount():
            self.list.setCurrentItem(self.list.topLevelItem(0))
        else:
            self.selection(None)
        self.update_status()

    def selection(self, current, previous=None):
        self.suggest.clear()
        self.jump.setEnabled(current is not None)
        self.accept_word.setEnabled(current is not None)
        self.context.clear()
        if current is not None:
            finding = self.filtered[self.list.indexOfTopLevelItem(current)]
            self.context.setText(finding.context)
            choices = (finding.custom,) if finding.custom else english_suggestions(finding.word)
            self.suggest.addItems(choices)
        self.preview.setEnabled(bool(self.suggest.count()))

    def selected(self):
        return self.filtered[self.list.indexOfTopLevelItem(self.list.currentItem())]

    def open_chapter(self, *_args):
        if self.list.currentItem():
            self.chapter = self.selected().chapter
            self.accept()

    def correct(self):
        if self.list.currentItem() and self.suggest.currentText():
            self.correction = (term_expression(self.selected().word), self.suggest.currentText().replace("\\", "\\\\"))
            self.accept()

    def edit_entry(self, current, _previous):
        self.remove.setEnabled(current is not None)
        if current:
            self.word.setText(current.text(0))
            self.revised.setText(current.text(1))

    def confirm(self, title, message):
        return QMessageBox.question(self, title, message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                    QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes

    def save_entry(self, *, value=None):
        word, fix = value or (self.word.text().strip(), self.revised.text().strip())
        previous = next((e for e in self.entries if word_key(e[0]) == word_key(word)), None)
        if previous == (word, fix):
            self.status.setText("这个词条已经保存在个人词典中。")
            return
        if previous and not self.confirm("更新个人词条", f"更新「{previous[0]}」的词典设置？"):
            return
        try:
            self.store.save(word, fix, previous=previous)
            self.refresh()
        except (ValueError, OSError, sqlite3.Error) as error:
            self.status.setText(str(error))

    def allow_word(self):
        if self.list.currentItem():
            self.save_entry(value=(self.selected().word, ""))

    def remove_entry(self):
        item = self.words.currentItem()
        if not item or not self.confirm("移除个人词条", f"从本机词典中移除「{item.text(0)}」？"):
            return
        try:
            self.store.remove(item.text(0), item.text(1))
            self.refresh()
        except (ValueError, OSError, sqlite3.Error) as error:
            self.status.setText(str(error))


def show_spelling(studio):
    if not studio.book_started or studio.task is not None:
        return

    def open_review():
        try:
            dialog = SpellDialog(studio)
        except (OSError, sqlite3.Error) as error:
            studio.error(str(error))
            return
        dialog.exec()
        chapter, correction = dialog.chapter, dialog.correction
        dialog.deleteLater()
        if correction:
            show_book_replace(studio, spelling=correction)
        elif chapter is not None:
            studio.go_to_chapter(chapter)

    studio.flush_current_edits(open_review)
