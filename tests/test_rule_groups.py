import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ebook_core import Asset, Book, Chapter
from replacement_rules import Rule, SavedRules
from rule_groups import SavedGroups, Step, apply_rule_group, preview_rule_group
from workspace import Workspace


class RuleGroupTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.workspace = Workspace(Path(folder.name))
        self.addCleanup(self.workspace.close)
        self.book = Book(chapters=[Chapter("第一页", '<p id="p">己经 <em>己经</em> &amp; 原图</p>'),
                                   Chapter("第二页", '<img src="图.png" alt="己经"/>')],
                         assets={"图.png": Asset(b"img", "image/png")})
        self.steps = (Step("辨形", Rule("己经", "已经", False, True)),
                      Step("统一措辞", Rule(r"(已)经", r"\1然", True, False)))

    def test_cascade_reverse_and_single_checkpoint(self):
        book = self.book
        plan = preview_rule_group(book, self.steps)
        self.assertEqual([s.matches for s in plan.stages], [2, 2])
        self.assertIn("【已经】", next(plan.stages[1].page(0, 1)).before)
        self.assertEqual(set(plan.changes), {0})
        self.assertIn("己经", book.chapters[0].html)
        reverse = preview_rule_group(book, self.steps[::-1])
        self.assertEqual([s.replacements for s in reverse.stages], [0, 2])
        self.assertEqual(self.workspace.checkpoints(book), [])
        revised = apply_rule_group(self.workspace, book, plan)
        self.assertEqual(revised.chapters[0].html, book.chapters[0].html.replace("己经", "已然"))
        self.assertEqual(revised.chapters[1], book.chapters[1])
        self.assertEqual(revised.assets, book.assets)
        checkpoint, = self.workspace.checkpoints(revised)
        self.assertEqual(self.workspace.load_checkpoint(revised, checkpoint), book)

    def test_stale_and_failed_steps_do_not_write_partial_results(self):
        book = self.book
        plan = preview_rule_group(book, self.steps)
        book.chapters[1].html += "<p>己经</p>"
        with self.assertRaisesRegex(ValueError, "重新预览"):
            apply_rule_group(self.workspace, book, plan)
        with self.assertRaisesRegex(ValueError, "第 3 步"):
            preview_rule_group(book, (*self.steps, Step("无效规则", Rule("(", "", True, False))))
        self.assertIn("己经", book.chapters[0].html)
        self.assertEqual(self.workspace.checkpoints(book), [])

    def test_reversing_the_first_edit_makes_apply_a_noop(self):
        undo = Step("恢复用词", Rule("已经", "己经", False, True))
        plan = preview_rule_group(self.book, (self.steps[0], undo))
        self.assertEqual([s.replacements for s in plan.stages], [2, 2])
        self.assertIs(apply_rule_group(self.workspace, self.book, plan), self.book)
        self.assertEqual(self.workspace.checkpoints(self.book), [])

    def test_snapshot_steps_survive_rule_changes_reordering_and_reopen(self):
        directory = self.workspace.directory
        groups = SavedGroups(directory)
        rules = SavedRules(directory)
        rules.change("辨形", None, self.steps[0].rule)
        groups.change("扫描件", None, self.steps)
        rules.change("辨形", self.steps[0].rule, None)
        self.assertEqual(SavedGroups(directory).read(), {"扫描件": self.steps})
        groups.change("扫描件", self.steps, self.steps[::-1])
        with self.assertRaisesRegex(ValueError, "其他窗口"):
            SavedGroups(directory).change("扫描件", self.steps, None)
        groups.change("扫描件", self.steps[::-1], None)
        self.assertEqual(groups.read(), {})

    def test_dialog_invalidates_preview_after_reordering(self):
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtWidgets import QApplication
        from rule_group_dialog import RuleGroupDialog

        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            app = QApplication.instance() or QApplication([])
        dialog = RuleGroupDialog(None, self.book, self.workspace)
        self.addCleanup(dialog.close)
        dialog.steps = list(self.steps)
        dialog.changed(1)
        dialog.preview_group()
        loop = QEventLoop()
        timer = QTimer()
        timer.timeout.connect(lambda: loop.quit() if dialog.worker is None else None)
        timer.start(10)
        QTimer.singleShot(5000, loop, loop.quit)
        loop.exec()
        timer.stop()
        self.assertTrue(dialog.apply_button.isEnabled())
        self.assertEqual(dialog.order.topLevelItem(1).text(3), "2")
        self.assertEqual(dialog.examples.topLevelItemCount(), 2)
        dialog.move(-1)
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertEqual(dialog.steps, list(self.steps[::-1]))
        dialog.reject()
        self.assertIn("己经", self.book.chapters[0].html)
