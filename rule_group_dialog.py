"""Build a sequence of correction rules and review each step before applying it."""
import re
from threading import Event

from PySide6.QtCore import QObject, QSignalBlocker, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QMessageBox, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from book_replace import PREVIEW_LIMIT
from book_revision_dialogs import _field, _status
from ebook_core import Book
from replacement_rules import Rule, SavedRules
from rule_groups import SavedGroups, Step, apply_rule_group, preview_rule, preview_rule_group


class StepDialog(QDialog):
    def __init__(self, parent, rules, step):
        super().__init__(parent)
        self.setWindowTitle("校对步骤")
        self.resize(470, 260)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        presets = QComboBox()
        presets.setAccessibleName("已有校对规则")
        presets.addItem("自定义规则")
        for name in sorted(rules, key=str.casefold):
            presets.addItem(name, rules[name])
        form.addRow("从已有规则填入", presets)
        self.name = _field(form, "步骤名称")
        self.query = _field(form, "查找文字")
        self.replacement = _field(form, "替换为")
        self.name.setPlaceholderText("可选，留空使用查找文字")
        self.replacement.setPlaceholderText("留空会删除匹配的文字")
        layout.addLayout(form)
        options = QHBoxLayout()
        self.regular = QCheckBox("正则表达式")
        self.case_sensitive = QCheckBox("区分大小写")
        options.addWidget(self.regular)
        options.addWidget(self.case_sensitive)
        options.addStretch()
        layout.addLayout(options)
        self.message = _status()
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("确定")
        ok.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.query.textChanged.connect(lambda text: ok.setEnabled(bool(text)))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        presets.activated.connect(lambda i: self.fill(Step(presets.itemText(i), presets.itemData(i))) if i else None)
        if step:
            self.fill(step)
        self.query.setFocus()

    def fill(self, step):
        self.name.setText(step.name)
        self.query.setText(step.rule.query)
        self.replacement.setText(step.rule.replacement)
        self.regular.setChecked(step.rule.regular)
        self.case_sensitive.setChecked(step.rule.case_sensitive)

    def accept(self):
        rule = Rule(self.query.text(), self.replacement.text(), self.regular.isChecked(),
                    self.case_sensitive.isChecked())
        try:
            preview_rule(Book(), rule)
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        self.step = Step(self.name.text().strip() or rule.query, rule)
        super().accept()


class PreviewSignals(QObject):
    ready = Signal(object)
    failed = Signal(object)


class RuleGroupDialog(QDialog):
    def __init__(self, parent, book, workspace):
        super().__init__(parent)
        self.setWindowTitle("顺序校对规则组")
        self.resize(740, 555)
        self.book = book
        self.groups = SavedGroups(workspace.directory)
        self.presets = SavedRules(workspace.directory)
        self.saved = self.groups.read()
        self.steps = []
        self.loaded = ""
        self.plan = self.worker = None
        self.offset = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.addWidget(_status("按列表从上到下校对。每一步会在上一步改好的正文中继续查找。"))
        picker = QHBoxLayout()
        self.group_list = QComboBox()
        self.group_list.setAccessibleName("已保存的规则组")
        group_label = QLabel("规则组")
        group_label.setBuddy(self.group_list)
        picker.addWidget(group_label)
        picker.addWidget(self.group_list, 1)
        layout.addLayout(picker)

        def button(row, text, slot):
            control = QPushButton(text)
            control.setAutoDefault(False)
            control.clicked.connect(slot)
            row.addWidget(control)
            return control

        self.save = button(picker, "保存规则组…", self.save_group)
        self.delete = button(picker, "删除规则组…", self.delete_group)
        self.order = QTreeWidget()
        self.order.setAccessibleName("规则执行顺序")
        self.order.setHeaderLabels(["步骤", "查找", "替换为", "命中", "替换次数"])
        self.order.setRootIsDecorated(False)
        header = self.order.header()
        header.setStretchLastSection(False)
        for i in range(5):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch if i < 3 else QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.order, 2)
        actions = QHBoxLayout()
        self.add = button(actions, "添加步骤…", lambda: self.edit_step(new=True))
        self.edit = button(actions, "编辑步骤…", lambda: self.edit_step())
        self.up = button(actions, "上移", lambda: self.move(-1))
        self.down = button(actions, "下移", lambda: self.move(1))
        self.remove = button(actions, "移除步骤", self.remove_step)
        actions.addStretch()
        layout.addLayout(actions)
        self.summary = _status()
        self.summary.setAccessibleName("整组预览结果")
        layout.addWidget(self.summary)
        self.examples = QTreeWidget()
        self.examples.setAccessibleName("当前步骤替换明细")
        self.examples.setHeaderLabels(["章节", "修改前", "修改后"])
        self.examples.setRootIsDecorated(False)
        for i in (1, 2):
            self.examples.header().setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        self.examples.setColumnWidth(0, 135)
        self.examples.setWordWrap(True)
        layout.addWidget(self.examples, 3)
        pages = QHBoxLayout()
        self.page_label = _status()
        pages.addWidget(self.page_label, 1)
        self.previous = button(pages, "上一页", lambda: self.show_page(self.offset - PREVIEW_LIMIT))
        self.next = button(pages, "下一页", lambda: self.show_page(self.offset + PREVIEW_LIMIT))
        layout.addLayout(pages)
        bottom = QHBoxLayout()
        bottom.addStretch()
        self.preview = button(bottom, "预览整组", self.preview_group)
        self.apply_button = button(bottom, "应用整组", self.accept)
        self.apply_button.setObjectName("primary")
        button(bottom, "取消", self.reject)
        layout.addLayout(bottom)

        self.order.currentItemChanged.connect(self.select_step)
        self.order.itemDoubleClicked.connect(lambda: self.edit_step())
        self.group_list.activated.connect(self.load_group)
        self.refresh_picker()
        self.changed(0)
        self.add.setFocus()

    def refresh_picker(self):
        self.group_list.clear()
        self.group_list.addItem("新规则组", "")
        for name in sorted(self.saved, key=str.casefold):
            self.group_list.addItem(name, name)
        self.group_list.setCurrentIndex(self.group_list.findData(self.loaded))
        self.delete.setEnabled(bool(self.loaded))

    def load_group(self, i):
        name = self.group_list.itemData(i)
        if name == self.loaded:
            return
        if tuple(self.steps) != self.saved.get(self.loaded, ()):
            if not self.confirm("切换规则组", "放弃本组尚未保存的步骤和顺序？"):
                self.group_list.setCurrentIndex(self.group_list.findData(self.loaded))
                return
        try:
            self.saved = self.groups.read()
        except (OSError, ValueError) as exc:
            self.group_list.setCurrentIndex(self.group_list.findData(self.loaded))
            self.summary.setText(f"无法读取规则组。{exc}")
            return
        self.loaded = name if name in self.saved else ""
        self.steps = list(self.saved.get(self.loaded, ()))
        self.refresh_picker()
        self.changed(0)

    def confirm(self, title, question):
        return QMessageBox.question(self, title, question, QMessageBox.StandardButton.Yes |
                                    QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def store(self, name, steps):
        try:
            self.groups.change(name, self.saved.get(name), steps)
            self.saved = self.groups.read()
        except (ValueError, OSError) as exc:
            self.summary.setText(f"未保存更改。{exc}")
            return False
        self.loaded = name if steps is not None else ""
        self.refresh_picker()
        return True

    def save_group(self):
        name, ok = QInputDialog.getText(self, "保存校对规则组", "规则组名称", text=self.loaded)
        if not ok:
            return
        name = name.strip()
        if name in self.saved and not self.confirm("更新规则组", f"保存当前步骤和顺序，更新「{name}」？"):
            return
        if self.store(name, tuple(self.steps)):
            self.summary.setText(f"已保存规则组「{name}」。")

    def delete_group(self):
        if self.confirm("删除规则组", f"从本机移除规则组「{self.loaded}」？") and self.store(self.loaded, None):
            self.steps.clear()
            self.changed(0)

    def index(self):
        return self.order.indexOfTopLevelItem(self.order.currentItem())

    def edit_step(self, new=False):
        row = self.index()
        try:
            presets = self.presets.read()
        except (OSError, ValueError) as exc:
            self.summary.setText(f"无法读取已有校对规则。{exc}")
            return
        dialog = StepDialog(self, presets, None if new else self.steps[row])
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        dialog.deleteLater()
        if not accepted:
            return
        if new:
            row = len(self.steps)
            self.steps.append(dialog.step)
        else:
            self.steps[row] = dialog.step
        self.changed(row)

    def move(self, delta):
        row = self.index()
        self.steps.insert(row + delta, self.steps.pop(row))
        self.changed(row + delta)

    def remove_step(self):
        row = self.index()
        self.steps.pop(row)
        self.changed(min(row, len(self.steps) - 1))

    def stop_preview(self):
        if self.worker is not None:
            self.worker.set()
            self.worker = None

    def changed(self, row):
        self.stop_preview()
        self.plan = None
        with QSignalBlocker(self.order):
            self.order.clear()
            for i, step in enumerate(self.steps, 1):
                rule = step.rule
                item = QTreeWidgetItem([f"{i}. {step.name}", "", "", "—", "—"])
                item.setToolTip(0, item.text(0))
                for column, raw in ((1, rule.query), (2, rule.replacement)):
                    visible = re.sub(r"^ +| +\Z", lambda m: "␠" * len(m[0]), raw)
                    item.setText(column, visible or "（删除）")
                    tip = item.text(column)
                    if visible != raw:
                        tip += "\n每个 ␠ 表示一个首尾空格。"
                    if column == 1:
                        tip += (" · 正则表达式" if rule.regular else " · 普通文字")
                        tip += (" · 区分大小写" if rule.case_sensitive else " · 忽略大小写")
                    item.setToolTip(column, tip)
                self.order.addTopLevelItem(item)
            if self.steps:
                self.order.setCurrentItem(self.order.topLevelItem(row))
        self.save.setEnabled(bool(self.steps))
        self.preview.setEnabled(bool(self.steps))
        self.apply_button.setEnabled(False)
        self.summary.setText("点「添加步骤」写下第一个改法，或从上方选择已有规则组。"
                             if not self.steps else "点「预览整组」查看每一步的实际改动。")
        self.select_step()

    def select_step(self):
        row = self.index()
        self.edit.setEnabled(row >= 0)
        self.remove.setEnabled(row >= 0)
        self.up.setEnabled(row > 0)
        self.down.setEnabled(0 <= row < len(self.steps) - 1)
        self.show_page(0)

    def show_page(self, start):
        self.offset = start
        self.examples.clear()
        stage = self.plan.stages[self.index()] if self.plan and self.index() >= 0 else None
        count = stage.replacements if stage else 0
        end = min(start + PREVIEW_LIMIT, count)
        if count:
            for hit in stage.page(start, PREVIEW_LIMIT):
                self.examples.addTopLevelItem(QTreeWidgetItem([hit.chapter_title, hit.before, hit.after]))
            self.page_label.setText(f"当前步骤 · 第 {start + 1}–{end} 处，共 {count} 处")
        else:
            self.page_label.setText("此步没有需要替换的文字。" if stage else "")
        self.previous.setEnabled(start > 0)
        self.next.setEnabled(end < count)

    def preview_group(self):
        self.changed(self.index())
        self.summary.setText("正在按顺序预览…")
        self.preview.setEnabled(False)
        self.worker = cancel = Event()
        signals = PreviewSignals()
        signals.ready.connect(self.preview_ready)
        signals.failed.connect(self.preview_failed)
        book, steps = self.book, tuple(self.steps)

        def run():
            try:
                plan = preview_rule_group(book, steps, cancelled=cancel.is_set)
                if not cancel.is_set():
                    signals.ready.emit((cancel, plan))
            except Exception as exc:
                if not cancel.is_set():
                    signals.failed.emit((cancel, str(exc)))

        QThreadPool.globalInstance().start(run)

    @Slot(object)
    def preview_ready(self, result):
        cancel, plan = result
        if self.worker is not cancel:
            return
        self.worker = None
        self.plan = plan
        for i, stage in enumerate(plan.stages):
            item = self.order.topLevelItem(i)
            item.setText(3, str(stage.matches))
            item.setText(4, str(stage.replacements))
        self.preview.setEnabled(True)
        self.apply_button.setEnabled(bool(plan.changes))
        count = sum(stage.replacements for stage in plan.stages)
        self.summary.setText(f"累计替换 {count} 次，最终修改 {len(plan.changes)} 章。选择步骤查看该步的前后文字。")
        self.show_page(0)

    @Slot(object)
    def preview_failed(self, result):
        cancel, message = result
        if self.worker is cancel:
            self.worker = None
            self.preview.setEnabled(True)
            self.summary.setText(message)

    def reject(self):
        self.stop_preview()
        super().reject()

    def accept(self):
        if self.plan and self.plan.changes:
            super().accept()


def show_rule_groups(studio):
    if studio.book_started and studio.task is None:
        studio.flush_current_edits(lambda: _edit_groups(studio))


def _edit_groups(studio):
    book = studio.book
    try:
        dialog = RuleGroupDialog(studio, book, studio.workspace)
    except (ValueError, OSError) as exc:
        studio.error(f"无法打开校对规则组。{exc}")
        return
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    dialog.deleteLater()
    if not accepted:
        return
    plan = dialog.plan
    revision = studio.edit_revision

    def complete(revised):
        if studio.book is not book or studio.edit_revision != revision:
            studio.error("书稿已有变化，请重新预览整组。")
            return
        studio.adopt_book_edit(revised, min(plan.changes))
        studio.statusBar().showMessage(f"已应用整组 · 修改 {len(plan.changes)} 章，已保存运行前检查点。")

    studio.start_task("正在保存运行前检查点…", lambda: apply_rule_group(studio.workspace, book, plan), complete)
