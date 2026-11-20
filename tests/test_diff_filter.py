import os
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from checkpoint_compare import Change
from checkpoint_compare_dialog import CheckpointCompareDialog


class DiffFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        left = ["共同上下文"] + [f"改前第{i}行" for i in range(180)]
        right = ["共同上下文"] + [f"改后第{i}行" for i in range(180)]
        right[-1] = "🖋 最后一行的修订"
        self.changes = [
            Change("正文", "更改", "沿河散步", "\n".join(left), "\n".join(right), "chapter.xhtml"),
            Change("资源", "新增", "cover.jpg", "", "image/jpeg", "Images/cover.jpg"),
            Change("章节排版", "更改", "加注", "相同正文", "相同正文", "notes.xhtml",
                   ("<p>文</p>", "<p class='revision-note'>文</p>")),
        ]
        self.view = CheckpointCompareDialog(None, "原稿", self.changes)
        self.view.show()
        self.app.processEvents()
        self.addCleanup(self.view.close)

    def test_text_search_positions_both_panes_at_the_change_and_skips_context(self):
        v = self.view
        QTest.keyClick(v, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(v.query.hasFocus())
        v.query.setText("共同上下文")
        self.assertIsNone(v.items.currentItem())
        self.assertIn("没有符合", v.count.text())
        self.assertFalse(v.details.isVisible())
        v.query.setText("最后一行")
        self.assertEqual(v.count.text(), "显示 1 / 3 项变化")
        self.assertEqual(v.panes[1].textCursor().selectedText(), "最后一行")
        self.assertEqual(v.panes[0].textCursor().blockNumber(), v.panes[1].textCursor().blockNumber())
        self.assertGreater(v.panes[1].verticalScrollBar().value(), 50)
        QTest.keyClick(v.query, Qt.Key.Key_Return)
        self.assertTrue(v.isVisible())
        self.assertTrue(v.items.hasFocus())

    def test_combined_filters_reset_and_format_only_source(self):
        v = self.view
        v.category.setCurrentText("资源")
        v.query.setText("IMAGES/COVER")
        self.assertEqual(v.items.currentItem().text(0), "cover.jpg")
        self.assertIn("image/jpeg", v.panes[1].toPlainText())
        v.query.setText("散步")
        self.assertIsNone(v.items.currentItem())
        self.assertEqual(v.panes[1].toPlainText(), "")
        v.reset.click()
        self.assertTrue(all(not v.items.topLevelItem(i).isHidden() for i in range(3)))
        self.assertEqual(v.category.currentData(), "")
        self.assertTrue(v.query.hasFocus())
        v.query.setText("revision-note")
        self.assertEqual(v.items.currentItem().text(0), "加注")
        self.assertTrue(v.markup.isChecked())
        self.assertEqual(v.panes[1].textCursor().selectedText(), "revision-note")
