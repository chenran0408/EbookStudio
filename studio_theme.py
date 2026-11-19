"""Qt styling for the desktop workbench."""

STYLE = """
QMainWindow, QDialog { background:#F4F6F6; color:#162A2D; }
QMenuBar, QMenu { background:#fff; color:#162A2D; }
QMenuBar::item:selected, QMenu::item:selected { background:#E9F4F1; color:#0B6D62; }
QLabel#section { color:#162A2D; font-size:14px; font-weight:700; }
QLabel#muted { color:#526466; }

QPushButton, QLineEdit, QComboBox { border:1px solid #D8E2E1; border-radius:7px; padding:6px 9px; }
QPushButton, QLineEdit, QComboBox, QListWidget, QTreeWidget, QPlainTextEdit { background:#fff; color:#162A2D; }
QPushButton:hover { background:#E9F4F1; border-color:#9DC2BB; }
QPushButton:disabled { background:#F4F6F6; color:#687979; }
QPushButton:checked { background:#E9F4F1; border-color:#9DC2BB; color:#0B6D62; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QListWidget:focus, QTreeWidget:focus, QPlainTextEdit:focus {
    border-color:#0B6D62;
}
QPushButton#primary { background:#0B6D62; color:#fff; border-color:#0B6D62; font-weight:700; padding-left:14px; padding-right:14px; }
QPushButton#primary:hover { background:#09584F; border-color:#09584F; }
QPushButton#primary:focus { border:2px solid #162A2D; padding:5px 13px; }
QPushButton#primary:disabled { background:#E9F0EF; border-color:#D8E2E1; color:#687979; }

QWidget#header { background:#fff; border-bottom:1px solid #D8E2E1; }
QLabel#brand { color:#162A2D; font-size:21px; font-weight:700; }
QLabel#projectState { color:#526466; font-size:12px; }
QWidget#navigation { background:#122927; }
QWidget#navigation QPushButton { background:transparent; color:#ECF5F3; border:1px solid transparent; padding:8px 4px; }
QWidget#navigation QPushButton:hover { background:#203D39; }
QWidget#navigation QPushButton:checked { background:#28504A; color:#fff; border-color:#416D65; font-weight:700; }
QWidget#navigation QPushButton:focus { border-color:#A6CDC5; }
QWidget#navigation QPushButton:disabled { background:transparent; color:#AAB9B7; }

QWidget#welcome, QScrollArea#welcomeScroll { background:#F4F6F6; border:0; }
QLabel#startTitle { color:#162A2D; font-size:36px; font-weight:700; }
QWidget#recentPanel, QWidget#currentBook { background:#fff; border:1px solid #D8E2E1; border-radius:10px; }
QLabel#currentBookTitle { color:#162A2D; font-size:15px; font-weight:700; }
QWidget#draftNotice { background:#E9F4F1; border:1px solid #B6D4CE; border-radius:10px; }
QPushButton#startCard, QPushButton#startPrimaryCard { background:#fff; border:1px solid #D8E2E1; border-radius:10px; }
QPushButton#startPrimaryCard { background:#E9F4F1; border-color:#9DC2BB; }
QPushButton#startCard:hover, QPushButton#startPrimaryCard:hover { background:#E9F4F1; border-color:#0B6D62; }
QPushButton#startCard:focus, QPushButton#startPrimaryCard:focus { border:2px solid #0B6D62; }
QPushButton#startCard:pressed, QPushButton#startPrimaryCard:pressed { background:#DCEEE9; }
QPushButton#startCard:disabled, QPushButton#startPrimaryCard:disabled { background:#F4F6F6; border-color:#D8E2E1; }
QLabel#cardTitle { color:#162A2D; font-size:16px; font-weight:700; }
QLabel#cardDetail { color:#526466; }
QPushButton#startPrimaryCard QLabel#cardTitle { color:#0B6D62; }

QWidget#sidebar { background:#fff; }
QWidget#writing { background:#F4F6F6; }
QWidget#emptyEditor { background:#fff; border:1px solid #D8E2E1; border-radius:10px; }
QLineEdit#chapterTitle { font-weight:700; font-size:16px; padding:7px 10px; }
QListWidget, QTreeWidget, QPlainTextEdit { border:1px solid #D8E2E1; border-radius:7px; }
QListWidget#recentList { border:1px solid transparent; }
QListWidget#recentList:focus { border-color:#0B6D62; }
QListWidget#recentList::item { padding:7px 6px; }
QTreeWidget#chapterTree { show-decoration-selected:1; }
QListWidget::item, QTreeWidget::item { padding:6px; }
QListWidget::item:hover, QTreeWidget::item:hover { background:#F1F8F6; }
QListWidget::item:selected, QTreeWidget::item:selected { background:#E9F4F1; color:#0B6D62; }
QTabBar#documentModes::tab { background:#fff; color:#526466; border:1px solid #D8E2E1; padding:7px 13px; margin-right:4px; border-radius:7px; }
QTabBar#documentModes::tab:selected { background:#E9F4F1; color:#0B6D62; border-color:#9DC2BB; font-weight:700; }
QTabBar#documentModes::tab:disabled { color:#687979; }
QTabBar#documentModes::tab:focus { border-color:#0B6D62; }
QSplitter#workspaceSplit::handle { background:#E2EAE8; width:5px; }
QSplitter#workspaceSplit::handle:hover { background:#B6D4CE; }
QStatusBar { background:#fff; color:#526466; }
QProgressBar { border:1px solid #D8E2E1; border-radius:4px; text-align:center; }
QProgressBar::chunk { background:#B6D4CE; border-radius:3px; }
"""
