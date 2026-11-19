"""Qt styling for the desktop workbench."""

STYLE = """
QMainWindow, QDialog { background:#f4f6fa; color:#182236; }
QMenuBar, QMenu { background:#fff; color:#182236; }
QMenuBar::item:selected, QMenu::item:selected { background:#e7eeff; color:#17377e; }
QWidget#header { background:#fff; border-bottom:1px solid #dce3ee; }
QLabel#brand { color:#182236; font-size:19px; font-weight:700; }
QLabel#projectState { color:#56657d; padding:3px 9px; background:#eef2f9; border-radius:6px; }
QWidget#navigation { background:#17243b; }
QWidget#navigation QPushButton { background:transparent; color:#eaf0fc; border:1px solid transparent; padding:8px 4px; }
QWidget#navigation QPushButton:hover { background:#293c5b; }
QWidget#navigation QPushButton:focus { border-color:#c6d5ff; }
QWidget#navigation QPushButton:checked { background:#2b4680; color:white; border-color:#41639f; }
QWidget#navigation QPushButton:disabled { background:transparent; color:#a6b5ce; }
QWidget#welcome, QScrollArea#welcomeScroll { background:#f4f6fa; border:0; }
QLabel#startTitle { color:#182236; font-size:34px; font-weight:700; }
QWidget#startCard, QWidget#recentPanel { background:#fff; border:1px solid #dce3ee; border-radius:9px; }
QWidget#startCard QPushButton { text-align:left; font-weight:700; padding:6px 9px; border-color:transparent; }
QWidget#startCard QPushButton:focus { border-color:#2453c7; }
QWidget#draftNotice { background:#e7eeff; border:1px solid #cbd9ff; border-radius:8px; }
QWidget#sidebar { background:#f8faff; }
QWidget#writing { background:#fff; }
QLineEdit#chapterTitle { font-weight:700; font-size:16px; padding:7px 10px; }
QPushButton, QLineEdit, QComboBox { border:1px solid #c8d3e5; border-radius:6px; padding:6px 9px; }
QPushButton, QLineEdit, QComboBox, QListWidget, QTreeWidget, QPlainTextEdit { background:#fff; color:#182236; }
QPushButton:hover { background:#eef3fe; border-color:#bacbed; }
QPushButton:disabled { background:#f0f3f8; color:#64728a; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QListWidget:focus, QTreeWidget:focus, QPlainTextEdit:focus {
    border-color:#2453c7;
}
QPushButton:checked { background:#e7eeff; border-color:#9bb4ee; color:#17377e; }
QPushButton#primary, QWidget#startCard QPushButton#primary { background:#2453c7; color:#fff; border:1px solid #2453c7; }
QPushButton#primary:hover, QWidget#startCard QPushButton#primary:hover { background:#1c45aa; border-color:#1c45aa; }
QPushButton#primary:focus, QWidget#startCard QPushButton#primary:focus { border:2px solid #152a6b; padding:5px 8px; }
QPushButton#primary:disabled { background:#e4e9f4; border-color:#dce3ee; color:#62718a; }
QLabel#section { font-size:14px; font-weight:700; color:#25324a; }
QLabel#muted { color:#56657d; }
QListWidget, QTreeWidget, QPlainTextEdit { border:1px solid #dce3ee; border-radius:6px; }
QListWidget::item, QTreeWidget::item { padding:6px; }
QListWidget::item:hover, QTreeWidget::item:hover { background:#f1f5fe; }
QListWidget::item:selected, QTreeWidget::item:selected { background:#e7eeff; color:#17377e; }
QTabBar#documentModes::tab { background:#eef2f8; color:#354660; border:1px solid #dce3ee; padding:7px 13px; margin-right:3px; border-radius:6px; }
QTabBar#documentModes::tab:selected { background:#e7eeff; color:#17377e; border-color:#95afea; font-weight:700; }
QTabBar#documentModes::tab:disabled { color:#65738a; }
QTabBar#documentModes::tab:focus { border-color:#2453c7; }
QSplitter::handle { background:#e8edf5; width:5px; }
QSplitter::handle:hover { background:#bacbea; }
QStatusBar { background:#fff; color:#56657d; }
QProgressBar { border:1px solid #c8d3e5; border-radius:4px; background:#f4f6fa; text-align:center; }
QProgressBar::chunk { background:#b8cbfa; border-radius:3px; }
"""
