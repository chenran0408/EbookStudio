import os
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from file_drops import FileDrops


class FileDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            cls.app = QApplication.instance() or QApplication([])

    def test_file_drop_over_text_input_is_deferred_and_copied(self):
        window = QWidget()
        field = QLineEdit(window)
        received = []
        receiver = FileDrops(window, lambda: True, received.append)
        mime = QMimeData()
        path = Path.cwd() / '手稿.epub'
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
        key = Qt.KeyboardModifier.ShiftModifier
        button = Qt.MouseButton.LeftButton
        enter = QDragEnterEvent(QPoint(0, 0), actions, mime, button, key)
        QApplication.sendEvent(field, enter)
        self.assertTrue(enter.isAccepted())
        drop = QDropEvent(QPointF(0, 0), actions, mime, button, key)
        QApplication.sendEvent(field, drop)
        self.assertTrue(drop.isAccepted())
        self.assertEqual(drop.dropAction(), Qt.DropAction.CopyAction)
        self.assertEqual(received, [])
        self.app.processEvents()
        self.assertEqual(received, [[path]])
        self.assertEqual(field.text(), '')
        self.app.removeEventFilter(receiver)

    def test_busy_and_move_only_drops_leave_source_and_editor_untouched(self):
        window = QWidget()
        receiver = FileDrops(window, lambda: False, self.fail)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(Path.cwd() / '期刊.epub'))])
        for action in (Qt.DropAction.CopyAction, Qt.DropAction.MoveAction):
            event = QDragEnterEvent(QPoint(0, 0), action, mime,
                                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(window, event)
            self.assertFalse(event.isAccepted())
            receiver.available = lambda: True
        self.app.removeEventFilter(receiver)
