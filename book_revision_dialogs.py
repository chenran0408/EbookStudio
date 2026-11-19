"""Desktop entry points for whole-book replacement and local checkpoints."""

from zipfile import BadZipFile

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from book_replace import preview_replacement, replace_with_checkpoint
from ebook_core import Book
from replacement_rules import Rule, SavedRules


def _status(text=""):
    label = QLabel(text, objectName="muted")
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _field(form, caption):
    label = QLabel(caption)
    field = QLineEdit()
    field.setAccessibleName(caption)
    label.setBuddy(field)
    form.addRow(label, field)
    return field


def _close_buttons(dialog):
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    close = buttons.button(QDialogButtonBox.StandardButton.Close)
    close.setText("关闭")
    close.setAutoDefault(False)
    buttons.rejected.connect(dialog.reject)
    return buttons


class BookReplaceDialog(QDialog):
    def __init__(self, parent, book, workspace):
        super().__init__(parent)
        self.setWindowTitle("全书查找替换")
        self.resize(680, 520)
        self.book = book
        self.rules = SavedRules(workspace.directory)
        self.plan = None
        self.review_index = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(9)
        layout.addWidget(QLabel("替换正文", objectName="section"))
        layout.addWidget(_status("格式标签会分隔可匹配的文字。将替换文字留空可删除匹配内容。"))

        form = QFormLayout()
        rule_row = QHBoxLayout()
        self.rule_list = QComboBox()
        self.rule_list.setAccessibleName("我的校对规则")
        self.rule_list.setPlaceholderText("选择规则以填入设置")
        rule_label = QLabel("我的规则")
        rule_label.setBuddy(self.rule_list)
        rule_row.addWidget(self.rule_list, 1)
        self.save_rule = QPushButton("保存为规则…")
        self.save_rule.setAutoDefault(False)
        self.save_rule.setEnabled(False)
        self.delete_rule = QPushButton("删除规则…")
        self.delete_rule.setAutoDefault(False)
        self.delete_rule.setEnabled(False)
        rule_row.addWidget(self.save_rule)
        rule_row.addWidget(self.delete_rule)
        form.addRow(rule_label, rule_row)
        self.query = _field(form, "查找文字")
        self.replacement = _field(form, "替换为")
        layout.addLayout(form)

        options = QHBoxLayout()
        self.regular = QCheckBox("正则表达式")
        self.case_sensitive = QCheckBox("区分大小写")
        self.preview = QPushButton("预览替换")
        self.preview.setEnabled(False)
        self.preview.setDefault(True)
        options.addWidget(self.regular)
        options.addWidget(self.case_sensitive)
        options.addStretch()
        options.addWidget(self.preview)
        layout.addLayout(options)

        self.status = _status("输入查找文字并预览变更。")
        layout.addWidget(self.status)
        self.results = QListWidget()
        self.results.setAccessibleName("替换前后预览")
        self.results.setWordWrap(True)
        self.results.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.results, 1)
        layout.addWidget(_status("应用替换前会为当前书稿创建检查点。"))

        buttons = _close_buttons(self)
        self.apply_button = buttons.addButton("应用替换", QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setObjectName("primary")
        self.apply_button.setAutoDefault(False)
        self.apply_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.query.textChanged.connect(self.invalidate)
        self.replacement.textChanged.connect(self.invalidate)
        self.regular.toggled.connect(self.invalidate)
        self.case_sensitive.toggled.connect(self.invalidate)
        self.preview.clicked.connect(self.preview_changes)
        self.rule_list.activated.connect(self.load_rule)
        self.save_rule.clicked.connect(self.save_current_rule)
        self.delete_rule.clicked.connect(self.remove_current_rule)
        self.refresh_rules()
        self.query.setFocus()

    def refresh_rules(self, selected=""):
        self.rule_list.clear()
        try:
            rules = self.rules.read()
        except (OSError, ValueError) as exc:
            self.status.setText(f"无法读取校对规则。{exc}")
            rules = {}
        for name in sorted(rules, key=str.casefold):
            self.rule_list.addItem(name, rules[name])
        self.rule_list.setPlaceholderText("选择规则以填入设置" if rules else "尚无保存的规则")
        self.rule_list.setCurrentIndex(self.rule_list.findText(selected))
        self.delete_rule.setEnabled(self.rule_list.currentIndex() >= 0)

    def load_rule(self, index):
        rule = self.rule_list.itemData(index)
        if rule is None:
            return
        self.query.setText(rule.query)
        self.replacement.setText(rule.replacement)
        self.regular.setChecked(rule.regular)
        self.case_sensitive.setChecked(rule.case_sensitive)
        self.invalidate()
        self.delete_rule.setEnabled(True)
        self.preview.setFocus()

    def change_rule(self, name, previous, replacement):
        try:
            self.rules.change(name, previous, replacement)
        except (OSError, ValueError) as exc:
            self.refresh_rules()
            self.status.setText(f"未保存此更改。{exc}")
            return
        self.refresh_rules(name if replacement else "")
        self.status.setText(f"已保存规则「{name}」。" if replacement else f"已删除规则「{name}」。")

    def confirm_rule_change(self, title, question, action):
        dialog = QMessageBox(QMessageBox.Icon.Question, title, question, parent=self)
        confirm = dialog.addButton(action, QMessageBox.ButtonRole.DestructiveRole)
        cancel = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        confirmed = dialog.clickedButton() == confirm
        dialog.deleteLater()
        return confirmed

    def save_current_rule(self):
        rule = Rule(self.query.text(), self.replacement.text(), self.regular.isChecked(),
                    self.case_sensitive.isChecked())
        try:
            # Validate the expression and capture references even if the current book has no matches.
            preview_replacement(Book(), rule.query, rule.replacement,
                                regular=rule.regular, case_sensitive=rule.case_sensitive)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        name, accepted = QInputDialog.getText(self, "保存校对规则", "规则名称", text=self.rule_list.currentText())
        if not accepted:
            return
        name = name.strip()
        index = self.rule_list.findText(name)
        previous = self.rule_list.itemData(index)
        if previous is not None and not self.confirm_rule_change(
            "更新已有规则", f"用当前查找和替换设置更新规则「{name}」？", "更新规则",
        ):
            return
        self.change_rule(name, previous, rule)

    def remove_current_rule(self):
        name = self.rule_list.currentText()
        if self.rule_list.currentIndex() < 0:
            return
        if self.confirm_rule_change("删除校对规则", f"从本机移除规则「{name}」？", "删除规则"):
            self.change_rule(name, self.rule_list.currentData(), None)

    def invalidate(self):
        self.plan = None
        self.results.clear()
        self.apply_button.setEnabled(False)
        self.preview.setEnabled(bool(self.query.text()))
        self.save_rule.setEnabled(bool(self.query.text()))
        self.status.setText("输入查找文字并预览变更。" if not self.query.text() else "点击预览替换查看变更。")

    def preview_changes(self):
        self.invalidate()
        try:
            self.plan = preview_replacement(
                self.book, self.query.text(), self.replacement.text(),
                regular=self.regular.isChecked(), case_sensitive=self.case_sensitive.isChecked(),
            )
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        plan = self.plan
        if not plan.matches:
            self.status.setText("未找到匹配的正文。")
            return
        if not plan.replacements:
            self.status.setText(f"共命中 {plan.matches} 处，替换内容与原文相同。")
            return
        summary = f"命中 {plan.matches} 处 · 将替换 {plan.replacements} 处 · 涉及 {len(plan.chapters)} 章"
        if len(plan.hits) < plan.replacements:
            summary += f" · 显示前 {len(plan.hits)} 处"
        self.status.setText(summary)
        for hit in plan.hits:
            title = hit.chapter_title or "未命名章节"
            item = QListWidgetItem(
                f"{hit.chapter_index + 1}. {title}\n替换前 · {hit.before}\n替换后 · {hit.after}"
            )
            item.setData(Qt.ItemDataRole.UserRole, hit.chapter_index)
            self.results.addItem(item)
        self.results.setCurrentRow(0)
        self.apply_button.setEnabled(True)

    def accept(self):
        if self.plan and self.plan.replacements:
            item = self.results.currentItem()
            self.review_index = item.data(Qt.ItemDataRole.UserRole) if item else self.plan.chapters[0].index
            super().accept()


class BookCheckpointsDialog(QDialog):
    def __init__(self, parent, checkpoints):
        super().__init__(parent)
        self.setWindowTitle("书稿检查点")
        self.resize(560, 440)
        self.checkpoints = checkpoints
        self.create_label = None
        self.selected_checkpoint = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(QLabel("创建检查点", objectName="section"))
        layout.addWidget(_status("检查点保存在本机工作区，按书稿保留各次保存的版本。"))
        form = QFormLayout()
        self.name = _field(form, "名称")
        self.name.setPlaceholderText("手动检查点")
        layout.addLayout(form)
        self.create_button = QPushButton("保存检查点")
        self.create_button.setAutoDefault(False)
        self.create_button.clicked.connect(self.create)
        self.name.returnPressed.connect(self.create)
        layout.addWidget(self.create_button, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(QLabel("已保存的版本", objectName="section"))
        self.results = QListWidget()
        self.results.setAccessibleName("已保存的检查点")
        self.results.setWordWrap(True)
        self.results.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.results, 1)
        self.details = _status("当前书稿还没有检查点。")
        layout.addWidget(self.details)
        layout.addWidget(_status("恢复会先保存当前版本，再以未保存的工程打开所选版本。"))

        buttons = _close_buttons(self)
        self.restore_button = buttons.addButton("恢复所选版本", QDialogButtonBox.ButtonRole.AcceptRole)
        self.restore_button.setObjectName("primary")
        self.restore_button.setAutoDefault(False)
        self.restore_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.results.currentRowChanged.connect(self.select_checkpoint)
        for checkpoint in checkpoints:
            local_time = checkpoint.created_at.astimezone()
            self.results.addItem(f"{checkpoint.label}\n{local_time:%Y-%m-%d %H:%M:%S}")
        if checkpoints:
            self.results.setCurrentRow(0)
        self.name.setFocus()

    def select_checkpoint(self, row):
        if row >= 0:
            checkpoint = self.checkpoints[row]
            self.details.setText(
                f"{checkpoint.title or '未命名电子书'} · {checkpoint.chapter_count} 章 · {checkpoint.asset_count} 项资源"
            )
        else:
            self.details.setText("请选择一个版本。" if self.checkpoints else "当前书稿还没有检查点。")
        self.restore_button.setEnabled(row >= 0)

    def create(self):
        self.create_label = self.name.text().strip() or "手动检查点"
        super().accept()

    def accept(self):
        row = self.results.currentRow()
        if row >= 0:
            self.selected_checkpoint = self.checkpoints[row]
            super().accept()


def show_book_replace(studio, *, spelling=None) -> None:
    """Flush the current editor buffer and open the whole-book replacement dialog."""
    if not studio.book_started or studio.task is not None:
        return
    studio.flush_current_edits(lambda: _replace_current_book(studio, spelling))


def _replace_current_book(studio, spelling=None):
    book = studio.book
    dialog = BookReplaceDialog(studio, book, studio.workspace)
    if spelling:
        pattern, replacement = spelling
        dialog.query.setText(pattern)
        dialog.replacement.setText(replacement)
        dialog.regular.setChecked(True)
        dialog.case_sensitive.setChecked(True)
        dialog.preview_changes()
    result = dialog.exec()
    dialog.deleteLater()
    if result != QDialog.DialogCode.Accepted:
        return
    plan = dialog.plan
    revision = studio.edit_revision
    row = dialog.review_index

    def complete(revised):
        if studio.book is not book or studio.edit_revision != revision:
            studio.error("书稿已有变化，请重新预览替换。")
            return
        studio.adopt_book_edit(revised, row)
        studio.statusBar().showMessage(f"已替换 {plan.replacements} 处 · 已保存替换前检查点。")

    studio.start_task(
        "正在保存替换前检查点…",
        lambda: replace_with_checkpoint(studio.workspace, book, plan),
        complete,
    )


def show_book_checkpoints(studio) -> None:
    """Create, list or restore a checkpoint for the active book."""
    if not studio.book_started or studio.task is not None:
        return
    studio.flush_current_edits(lambda: _manage_checkpoints(studio))


def _manage_checkpoints(studio):
    book = studio.book
    workspace = studio.workspace
    try:
        checkpoints = workspace.checkpoints(book)
    except (OSError, ValueError) as exc:
        studio.error(str(exc))
        return
    dialog = BookCheckpointsDialog(studio, checkpoints)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    if dialog.create_label is not None:
        studio.start_task(
            "正在保存检查点…",
            lambda: workspace.save_checkpoint(book, dialog.create_label),
            lambda checkpoint: studio.statusBar().showMessage(f"已保存检查点 · {checkpoint.label}"),
        )
        return

    checkpoint = dialog.selected_checkpoint
    revision = studio.edit_revision

    def load():
        try:
            restored = workspace.load_checkpoint(book, checkpoint)
        except (BadZipFile, KeyError, OSError, ValueError) as exc:
            raise ValueError(f"无法打开所选检查点。{exc}") from exc
        workspace.save_checkpoint(book, f"恢复“{checkpoint.label}”前")
        return restored

    def complete(restored):
        if studio.book is not book or studio.edit_revision != revision:
            studio.error("书稿已有变化，请重新选择要恢复的版本。")
            return
        studio.replace_book(restored, None)
        studio.changed()
        studio.statusBar().showMessage("已恢复检查点 · 保存工程时请选择保存位置。")

    studio.start_task("正在恢复检查点…", load, complete)
