"""Find and organize local book files from a native desktop catalog."""
from __future__ import annotations

from pathlib import Path
import sqlite3

from PySide6.QtCore import QSignalBlocker, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QStackedWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout,
)

from book_library import BOOK_FILTER, BOOK_READ_ERRORS, BookLibrary, LibraryEntry, LibraryImport, tag_names


def _field(form, name, value=''):
    field = QLineEdit(value)
    field.setAccessibleName(name)
    form.addRow(name, field)
    return field


def _confirm_dialog(dialog, layout):
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    save = buttons.button(QDialogButtonBox.StandardButton.Ok)
    save.setText('应用')
    save.setObjectName('primary')
    buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return save


class CatalogDetailsDialog(QDialog):
    def __init__(self, parent, entry: LibraryEntry):
        super().__init__(parent)
        self.setWindowTitle('编辑书库资料')
        self.resize(480, 220)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title = _field(form, '书名', entry.title)
        self.author = _field(form, '作者', entry.author)
        self.author.setPlaceholderText('多位作者用分号分隔')
        self.tags = _field(form, '标签', '；'.join(entry.tags))
        self.tags.setPlaceholderText('多项标签用分号分隔')
        layout.addLayout(form)
        scope = QLabel('更改仅用于本机书库。多位作者或多项标签用分号分隔。', objectName='muted')
        scope.setWordWrap(True)
        layout.addWidget(scope)
        save = _confirm_dialog(self, layout)
        self.title.textChanged.connect(lambda value: save.setEnabled(bool(value.strip())))
        self.title.selectAll()
        self.title.setFocus()


class BatchTagsDialog(QDialog):
    def __init__(self, parent, count):
        super().__init__(parent)
        self.setWindowTitle('批量标签')
        self.resize(430, 180)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f'为选中的 {count} 本书调整标签', objectName='section'))
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.setAccessibleName('标签操作')
        self.mode.addItems(['添加标签', '移除标签'])
        form.addRow('操作', self.mode)
        self.tags = _field(form, '标签')
        self.tags.setPlaceholderText('例如 期刊；待校对')
        layout.addLayout(form)
        save = _confirm_dialog(self, layout)
        save.setEnabled(False)
        self.tags.textChanged.connect(lambda value: save.setEnabled(bool(tag_names(value))))
        self.tags.setFocus()

    @property
    def removing(self):
        return self.mode.currentIndex() == 1


class LibraryImportTask(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, parent, library, paths):
        super().__init__(parent)
        self.library = library
        self.paths = paths

    def run(self):
        try:
            self.loaded.emit(self.library.add(self.paths))
        except (OSError, sqlite3.Error) as exc:
            self.failed.emit(str(exc))


class BookLibraryDialog(QDialog):
    def __init__(self, parent, library: BookLibrary):
        super().__init__(parent)
        self.library = library
        self.chosen_path = None
        self.entries: dict[int, LibraryEntry] = {}
        self.available: set[int] = set()
        self.import_task = None
        self.setWindowTitle('书库')
        self.resize(840, 490)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 15)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setAccessibleName('搜索书库')
        self.search.setPlaceholderText('搜索书名、作者、标签或路径')
        self.search.setClearButtonEnabled(True)
        self.add_button = QPushButton('批量添加书籍…')
        self.add_button.setObjectName('primary')
        self.add_button.setToolTip('再次添加同一文件可更新原书资料。')
        header.addWidget(self.search, 1)
        header.addWidget(self.add_button)
        layout.addLayout(header)

        filters = QHBoxLayout()
        self.author = QComboBox()
        self.tag = QComboBox()
        for name, field in (('作者', self.author), ('标签', self.tag)):
            field.setAccessibleName(f'按{name}筛选')
            field.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            field.setMinimumContentsLength(11)
            filters.addWidget(field, 1)
        self.count = QLabel(objectName='muted')
        filters.addWidget(self.count, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addLayout(filters)

        self.table = QTreeWidget()
        self.table.setAccessibleName('书库书籍列表')
        self.table.setHeaderLabels(['书名', '作者', '标签', '文件状态'])
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.setAllColumnsShowFocus(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        columns = self.table.header()
        columns.setStretchLastSection(False)
        columns.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 137), (2, 151), (3, 96)):
            self.table.setColumnWidth(column, width)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setSortingEnabled(True)

        self.empty = QLabel(objectName='muted')
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.results = QStackedWidget()
        self.results.addWidget(self.table)
        self.results.addWidget(self.empty)
        layout.addWidget(self.results, 1)

        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.path.setAccessibleName('所选书籍的原文件路径')
        self.path.setPlaceholderText('选中一本书以查看原文件路径')
        layout.addWidget(self.path)
        self.feedback = QLabel(objectName='muted')
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        self.feedback.hide()
        layout.addWidget(self.feedback)

        actions = QHBoxLayout()
        self.edit_button = QPushButton('编辑资料…')
        self.tags_button = QPushButton('批量标签…')
        self.relink_button = QPushButton('重新定位…')
        self.remove_button = QPushButton('移出书库…')
        self.open_button = QPushButton('打开书籍')
        close = QPushButton('关闭')
        for button in (self.edit_button, self.tags_button, self.relink_button, self.remove_button):
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(self.open_button)
        actions.addWidget(close)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        layout.addLayout(actions)

        self.add_button.clicked.connect(self.choose_import)
        self.search.textChanged.connect(self.filter)
        self.author.currentIndexChanged.connect(self.filter)
        self.tag.currentIndexChanged.connect(self.filter)
        self.search.returnPressed.connect(self.open_selected)
        self.table.itemSelectionChanged.connect(self.update_actions)
        self.table.itemActivated.connect(self.activate_item)
        self.edit_button.clicked.connect(self.edit_selected)
        self.tags_button.clicked.connect(self.edit_tags)
        self.relink_button.clicked.connect(self.relink_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        self.open_button.clicked.connect(self.open_selected)
        close.clicked.connect(self.reject)
        self.reload()
        self.search.setFocus()

    def selected_ids(self):
        return [item.data(0, Qt.ItemDataRole.UserRole) for item in self.table.selectedItems()]

    def reload(self):
        self.entries = {entry.id: entry for entry in self.library.entries()}
        self.available = {entry.id for entry in self.entries.values() if entry.path.is_file()}
        authors, tags = {}, {}
        for entry in self.entries.values():
            for author in entry.authors or ('',):
                authors.setdefault(author.casefold(), author or '未知作者')
            for tag in entry.tags:
                tags.setdefault(tag.casefold(), tag)
        self.fill_filter(self.author, '全部作者', authors)
        self.fill_filter(self.tag, '全部标签', tags)
        self.filter()

    @staticmethod
    def fill_filter(combo, title, values):
        key = combo.currentData()
        with QSignalBlocker(combo):
            combo.clear()
            combo.addItem(title)
            for value, name in sorted(values.items()):
                combo.addItem(name, value)
            combo.setCurrentIndex(max(0, combo.findData(key)))

    def filter(self):
        selected = set(self.selected_ids())
        terms = self.search.text().casefold().split()
        author, tag = self.author.currentData(), self.tag.currentData()
        with QSignalBlocker(self.table):
            self.table.clear()
            for entry in self.entries.values():
                if not entry.matches(terms, author, tag):
                    continue
                availability = '可打开' if entry.id in self.available else '路径不可用'
                row = QTreeWidgetItem([entry.title, entry.author or '未知作者', '、'.join(entry.tags), availability])
                row.setData(0, Qt.ItemDataRole.UserRole, entry.id)
                for column in range(3):
                    row.setToolTip(column, row.text(column))
                row.setToolTip(3, str(entry.path))
                self.table.addTopLevelItem(row)
                row.setSelected(entry.id in selected)
            if not self.table.selectedItems() and self.table.topLevelItemCount():
                self.table.setCurrentItem(self.table.topLevelItem(0))
        count = self.table.topLevelItemCount()
        self.count.setText(f'{count} / {len(self.entries)} 本')
        self.empty.setText('调整搜索词或筛选条件。' if self.entries else '点击「批量添加书籍」加入书稿或 EPUB。')
        self.results.setCurrentWidget(self.table if count else self.empty)
        self.update_actions()

    def update_actions(self):
        ids = self.selected_ids()
        single = len(ids) == 1
        ready = self.import_task is None
        self.edit_button.setEnabled(ready and single)
        self.tags_button.setEnabled(ready and bool(ids))
        self.relink_button.setEnabled(ready and single and ids[0] not in self.available)
        self.remove_button.setEnabled(ready and bool(ids))
        self.open_button.setEnabled(ready and single and ids[0] in self.available)
        path = str(self.entries[ids[0]].path) if single else ''
        self.path.setText(path)
        self.path.setToolTip(path)
        self.path.setCursorPosition(0)

    def note(self, text):
        self.feedback.setText(text)
        self.feedback.show()

    def choose_import(self):
        paths, _ = QFileDialog.getOpenFileNames(self, '添加书籍到书库', '', BOOK_FILTER)
        if paths:
            self.add_paths([Path(path) for path in paths])

    def add_paths(self, paths):
        self.import_task = LibraryImportTask(self, self.library, paths)
        self.import_task.loaded.connect(self.show_import_result)
        self.import_task.failed.connect(lambda error: self.note(f'无法更新书库。{error}'))
        self.import_task.finished.connect(self.finish_import)
        self.add_button.setEnabled(False)
        self.update_actions()
        self.note(f'正在读取 {len(paths)} 个文件的书籍资料…')
        self.import_task.start()

    @Slot(object)
    def show_import_result(self, result: LibraryImport):
        summary = f'添加 {result.added} 本，更新 {result.refreshed} 本。'
        if result.errors:
            summary += f' {len(result.errors)} 个文件无法读取。'
        self.note(summary)
        try:
            self.reload()
        except sqlite3.Error as exc:
            self.note(f'{summary} 刷新书库列表失败。{exc}')
        if result.errors:
            message = QMessageBox(QMessageBox.Icon.Warning, '书库导入结果', summary, parent=self)
            message.setDetailedText('\n'.join(f'{path}\n{error}' for path, error in result.errors))
            message.exec()

    @Slot()
    def finish_import(self):
        self.import_task.deleteLater()
        self.import_task = None
        self.add_button.setEnabled(True)
        self.update_actions()

    def edit_selected(self):
        entry = self.entries[self.selected_ids()[0]]
        dialog = CatalogDetailsDialog(self, entry)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_change(lambda: self.library.edit(entry.id, dialog.title.text(), dialog.author.text(),
                                                      tag_names(dialog.tags.text())), '书库资料已更新。')

    def edit_tags(self):
        ids = self.selected_ids()
        dialog = BatchTagsDialog(self, len(ids))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_change(lambda: self.library.change_tags(ids, tag_names(dialog.tags.text()),
                                                             remove=dialog.removing), f'已更新 {len(ids)} 本书的标签。')

    def relink_selected(self):
        book_id = self.selected_ids()[0]
        selected, _ = QFileDialog.getOpenFileName(self, '定位书籍的原文件', '', BOOK_FILTER)
        if selected:
            self.apply_change(lambda: self.library.relink(book_id, Path(selected)), '原文件路径已更新。')

    def remove_selected(self):
        ids = self.selected_ids()
        confirm = QMessageBox.question(self, '移出书库', f'从书库移出选中的 {len(ids)} 本书？原文件会保留。',
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                       QMessageBox.StandardButton.Cancel)
        if confirm == QMessageBox.StandardButton.Yes:
            self.apply_change(lambda: self.library.remove(ids), f'已移出 {len(ids)} 本书。')

    def apply_change(self, change, success):
        try:
            change()
            self.reload()
        except (*BOOK_READ_ERRORS, sqlite3.Error) as exc:
            self.note(f'无法更新书库。{exc}')
            return
        self.note(success)

    def activate_item(self, row, _column):
        if self.import_task is not None:
            return
        entry = self.entries[row.data(0, Qt.ItemDataRole.UserRole)]
        if not entry.path.is_file():
            self.available.discard(entry.id)
            self.filter()
            self.note('书籍路径已不可用。选中该书后可重新定位。')
            return
        self.chosen_path = entry.path
        self.accept()

    def open_selected(self):
        if self.open_button.isEnabled():
            self.activate_item(self.table.selectedItems()[0], 0)

    def reject(self):
        if self.import_task is None:
            super().reject()
        else:
            self.note('书库正在读取文件，请等待导入完成。')

    def closeEvent(self, event):
        if self.import_task is None:
            super().closeEvent(event)
        else:
            event.ignore()
