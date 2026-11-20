import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog

from ebook_core import Book, Chapter
from replacement_rules import Rule
from rule_group_dialog import RuleGroupDialog
from rule_groups import SavedGroups, Step, export_group, read_group_file
from workspace import Workspace


class RuleTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ws = Workspace(self.root / "workspace")
        self.addCleanup(self.ws.close)
        self.steps = (Step("斜杠全角化", Rule(" / ", " ／ ", False, False)),
                      Step("删除两侧空格", Rule(r" (／) ", r"\1", True, True)))
        self.path = self.root / "署名校对.json"
        self.book = Book(chapters=[Chapter("一", "<p>作者 / 画家</p>")])

    def dialog(self):
        dialog = RuleGroupDialog(None, self.book, self.ws)
        self.addCleanup(dialog.close)
        return dialog

    def test_transfer_current_unsaved_uses_filename_and_preserves_local_records(self):
        dialog = self.dialog()
        dialog.steps = list(self.steps)
        dialog.changed(0)
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(self.path), "")):
            dialog.export_current()
        self.assertEqual(read_group_file(self.path), ("署名校对", self.steps))
        self.assertEqual(dialog.groups.read(), {})
        other = self.dialog()
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.path), "")):
            other.import_group()
        self.assertEqual(other.steps, list(self.steps))
        self.assertEqual(SavedGroups(self.ws.directory).read(), {"署名校对": self.steps})
        self.assertEqual(self.ws.checkpoints(self.book), [])

    def test_conflict_cancel_rename_and_explicit_replace(self):
        export_group(self.path, "署名", self.steps)
        existing = (self.steps[1],)
        groups = SavedGroups(self.ws.directory)
        groups.change("署名", None, existing)
        dialog = self.dialog()
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.path), "")):
            with patch.object(QInputDialog, "getText", return_value=("署名", True)):
                with patch.object(dialog, "confirm", return_value=False):
                    dialog.import_group()
            self.assertEqual(groups.read(), {"署名": existing})
            self.assertEqual(dialog.steps, [])
            with patch.object(QInputDialog, "getText", return_value=("备用", True)):
                dialog.import_group()
            self.assertEqual(groups.read(), {"署名": existing, "备用": self.steps})
            with patch.object(QInputDialog, "getText", return_value=("署名", True)):
                with patch.object(dialog, "confirm", return_value=True):
                    dialog.import_group()
            self.assertEqual(groups.read(), {"署名": self.steps, "备用": self.steps})
            self.assertEqual(dialog.loaded, "署名")

    def test_invalid_import_leaves_library_and_current_steps_intact(self):
        groups = SavedGroups(self.ws.directory)
        groups.change("本机", None, self.steps)
        export_group(self.path, "传入", self.steps)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        dlg = self.dialog()
        dlg.load_group(dlg.group_list.findData("本机"))
        original_data = groups.path.read_bytes()
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.path), "")):
            for invalid in ("{", json.dumps({**raw, "version": True}),
                            json.dumps({**raw, "steps": [{**raw["steps"][0], "regular": "false"}]}),
                            json.dumps({**raw, "steps": [raw["steps"][0], {**raw["steps"][1], "query": "("}]})):
                with self.subTest(invalid=invalid):
                    self.path.write_text(invalid, encoding="utf-8")
                    dlg.import_group()
                    self.assertIn("无法导入", dlg.summary.text())
                    self.assertEqual(dlg.steps, list(self.steps))
                    self.assertEqual(groups.path.read_bytes(), original_data)
