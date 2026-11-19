"""Route external file drops over a window, including its text editors and lists."""
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget


class FileDrops(QObject):
    def __init__(self, window, available, receive):
        super().__init__(window)
        self.window = window
        self.available = available
        self.receive = receive
        window.setAcceptDrops(True)
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return False
        if not isinstance(watched, QWidget) or watched.window() is not self.window:
            return False
        urls = event.mimeData().urls()
        if not urls or not all(url.isLocalFile() for url in urls):
            return False
        if not self.available() or not event.possibleActions() & Qt.DropAction.CopyAction:
            event.ignore()
            return True
        # The book stays in its original directory, including when Shift was held.
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if kind == QEvent.Type.Drop:
            paths = [Path(url.toLocalFile()) for url in urls]
            # Return to the native drag source before opening a modal dialog.
            QTimer.singleShot(0, self.window, lambda: self.receive(paths))
        return True
