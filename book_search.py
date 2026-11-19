"""Search the readable text of an open book."""

from dataclasses import dataclass
import re

from lxml import etree, html
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QVBoxLayout, QWidget,
)

from ebook_core import Book
from epub_io import chapter_html


MAX_HITS = 200
CONTEXT = 32
BLOCK_ELEMENTS = {
    "address", "article", "aside", "blockquote", "br", "caption", "dd", "div",
    "dl", "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5",
    "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
    "table", "td", "th", "tr", "ul",
}
HIDDEN_ELEMENTS = {"head", "script", "style", "template", "noscript"}


def _readable_text(document: str) -> str:
    if not document.strip():
        return ""
    try:
        document = chapter_html(document)
    except (etree.XMLSyntaxError, ValueError):
        # A writer can search a chapter while its XHTML source is still being edited.
        pass
    try:
        page = html.document_fromstring(document)
    except etree.ParserError:
        return ""
    body = page.find("body")
    if body is None:
        return ""
    pieces = []

    def visit(node):
        if not isinstance(node.tag, str):
            return
        tag = node.tag.rsplit(":", 1)[-1]
        if tag in HIDDEN_ELEMENTS or "hidden" in node.attrib:
            return
        separated = tag in BLOCK_ELEMENTS
        if separated:
            pieces.append(" ")
        if node.text:
            pieces.append(node.text)
        for child in node:
            visit(child)
            if child.tail:
                pieces.append(child.tail)
        if separated:
            pieces.append(" ")

    visit(body)
    return " ".join("".join(pieces).split())


@dataclass(frozen=True)
class SearchHit:
    chapter_index: int
    title: str
    snippet: str


class BookSearch:
    """A text snapshot reused for each query while the search dialog is open."""

    def __init__(self, book: Book):
        self.chapters = [(chapter.title, _readable_text(chapter.html)) for chapter in book.chapters]

    def find(self, query: str, *, limit: int = MAX_HITS) -> tuple[int, list[SearchHit]]:
        phrase = " ".join(query.split())
        if not phrase:
            return 0, []
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        hits = []
        count = 0
        for chapter_index, (title, text) in enumerate(self.chapters):
            for match in pattern.finditer(text):
                count += 1
                if len(hits) < limit:
                    start = max(0, match.start() - CONTEXT)
                    end = min(len(text), match.end() + CONTEXT)
                    snippet = ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
                    hits.append(SearchHit(chapter_index, title, snippet))
        return count, hits


class BookSearchDialog(QDialog):
    def __init__(self, parent: QWidget | None, book: Book):
        super().__init__(parent)
        self.setWindowTitle("搜索全书正文")
        self.resize(630, 430)
        self._book = book
        self._search: BookSearch | None = None
        self.chapter_index: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(9)

        label = QLabel("查找文字", objectName="section")
        self.query = QLineEdit()
        self.query.setClearButtonEnabled(True)
        self.query.setAccessibleName(label.text())
        label.setBuddy(self.query)
        layout.addWidget(label)
        layout.addWidget(self.query)

        self.status = QLabel("输入文字开始搜索。", objectName="muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.results = QListWidget()
        self.results.setAccessibleName("正文搜索结果")
        self.results.setWordWrap(True)
        self.results.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.results, 1)

        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        actions.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        self.open_button = actions.addButton("打开章节", QDialogButtonBox.ButtonRole.AcceptRole)
        self.open_button.setObjectName("primary")
        self.open_button.setDefault(True)
        self.open_button.setEnabled(False)
        actions.accepted.connect(self.accept)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

        self.query.textChanged.connect(self._update_results)
        self.results.currentItemChanged.connect(self._update_open_button)
        self.results.itemActivated.connect(lambda _item: self.accept())
        self.query.setFocus()

    def _update_open_button(self, current, _previous):
        self.open_button.setEnabled(current is not None)

    def _update_results(self, query: str):
        self.results.clear()
        if not query.strip():
            self.status.setText("输入文字开始搜索。")
            return
        if self._search is None:
            self._search = BookSearch(self._book)
        count, hits = self._search.find(query)
        if not hits:
            self.status.setText("未找到匹配的正文。")
            return
        self.status.setText(f"共 {count} 处匹配，显示前 {len(hits)} 处。" if count > len(hits) else f"共 {count} 处匹配。")
        for hit in hits:
            title = hit.title.strip() or "未命名章节"
            item = QListWidgetItem(f"{hit.chapter_index + 1}. {title}\n{hit.snippet}")
            item.setData(Qt.ItemDataRole.UserRole, hit.chapter_index)
            self.results.addItem(item)
        self.results.setCurrentRow(0)

    def accept(self):
        item = self.results.currentItem()
        if item is not None:
            self.chapter_index = item.data(Qt.ItemDataRole.UserRole)
            super().accept()


def select_search_result(parent: QWidget | None, book: Book) -> int | None:
    """Return the zero-based index of the selected chapter."""
    dialog = BookSearchDialog(parent, book)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog.chapter_index
    return None
