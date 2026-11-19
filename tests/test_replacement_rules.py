from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from replacement_rules import Rule, SavedRules
from workspace import Workspace


class ReplacementRuleTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.workspace = Workspace(Path(folder.name))
        self.addCleanup(self.workspace.close)
        self.store = SavedRules(self.workspace.directory)
        self.literal = Rule("己经", "已经", False, False)
        self.regex = Rule(r"第(\d+)其", r"第\1期", True, True)

    def test_reopen_update_and_delete_preserve_names_and_options(self):
        self.store.change("中文 OCR", None, self.literal)
        reopened = SavedRules(self.workspace.directory)
        self.assertEqual(reopened.read(), {"中文 OCR": self.literal})
        reopened.change("中文 OCR", self.literal, self.regex)
        self.assertEqual(self.store.read()["中文 OCR"], self.regex)
        self.store.change("中文 OCR", self.regex, None)
        self.assertEqual(reopened.read(), {})

    def test_independent_windows_keep_unrelated_rules_and_reject_stale_edits(self):
        other = SavedRules(self.workspace.directory)
        self.store.change("纠正期号", None, self.literal)
        other.change("去掉误字", None, Rule("删", "", False, True))
        self.store.change("纠正期号", self.literal, self.regex)
        for previous in (None, self.literal):
            with self.assertRaisesRegex(ValueError, "重新选择"):
                other.change("纠正期号", previous, self.literal)
        with self.assertRaisesRegex(ValueError, "重新选择"):
            other.change("纠正期号", self.literal, None)
        self.assertEqual(other.read(), {"纠正期号": self.regex, "去掉误字": Rule("删", "", False, True)})
