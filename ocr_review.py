"""Compare a scanned page with its chapter and explicitly accept single-page OCR."""

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
from uuid import uuid4

from lxml import etree, html
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from ebook_core import Asset, Book, Chapter, asset_href, resolve_href
from epub_io import _set_body, _xml, chapter_html
from ocr_import import DEFAULT_MODEL, OcrError, ocr_book, scan_digest, scan_pages, scan_provenance, scan_rgb
from ocr_runtime import local_server
from paddle_import import paddle_book
from studio_dialogs import MODELS, _actions, _label
from workspace_tools import ocr_readiness


_SCAN_FILTER = "扫描件 (*.pdf *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)"
_CONTAINERS = {"article", "aside", "blockquote", "div", "footer", "header", "main", "section"}
_PROTECTED = "descendant-or-self::*[@id or self::img or (self::a and (@href or @name))]"
_INLINE = {"a", "img", "span"}


@dataclass
class OcrReplacement:
    html: str
    assets: dict[str, Asset]
    scan: dict
    retained_at_end: int


def known_scan(chapter: Chapter) -> dict | None:
    """Old projects without a complete association require manual page selection."""
    info = chapter.scan
    if not isinstance(info, dict):
        return None
    path, digest, page = (info.get(key) for key in ("path", "sha256", "page"))
    if (isinstance(path, str) and Path(path).is_absolute()
            and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
            and type(page) is int and page > 0):
        return info
    return None


def associate_path(path: Path, previous: dict | None) -> tuple[Path, str]:
    source = path.expanduser().resolve()
    digest = scan_digest(source)
    if previous and digest != previous["sha256"]:
        raise OcrError("这个文件与原扫描件的 SHA-256 不一致。请选择相同的原文件。")
    return source, digest


def recognize_page(scan: dict, engine: str, progress=None) -> Book:
    source = Path(scan["path"])
    pages = str(scan["page"])
    if engine == "paddle":
        book = paddle_book(source, pages=pages, progress=progress)
    else:
        model = os.environ.get("EBOOK_STUDIO_OCR_MODEL", DEFAULT_MODEL)
        with local_server() as (address, _):
            book = ocr_book(source, pages=pages, progress=progress, base_url=address, model=model)
    if len(book.chapters) != 1 or book.chapters[0].scan["sha256"] != scan["sha256"]:
        raise OcrError("扫描件内容已变化，请重新核对原页。")
    return book


def _fragment(document: str):
    return html.fragment_fromstring(chapter_html(document), create_parent="div")


def _serialize_fragment(root) -> str:
    if not len(root) and not root.text:
        return ""
    wrapped = html.tostring(root, encoding="unicode", method="xml", with_tail=False)
    return wrapped.removeprefix("<div>").removesuffix("</div>")


def _protected_blocks(root):
    for node in root:
        if node.tag in _CONTAINERS:
            if node_id := node.get("id"):
                yield html.Element("span", id=node_id)
            yield from _protected_blocks(node)
        elif node.xpath(_PROTECTED):
            yield node


def _asset_key(document: str, url: str) -> str | None:
    try:
        return resolve_href(document, url)
    except ValueError:
        return None


def _block_match(node, chapter: Chapter, assets: dict[str, Asset]):
    text = " ".join(node.text_content().split())
    images = []
    for image in node.xpath("descendant-or-self::img[@src]"):
        url = image.get("src")
        asset = assets.get(_asset_key(chapter.path, url))
        images.append(sha256(asset.data).digest() if asset else url)
    return node.tag, text, tuple(images)


def prepare_replacement(book: Book, chapter: Chapter, recognized: Book) -> OcrReplacement:
    """Stage a body replacement and isolate new crops before touching the open book."""
    new = recognized.chapters[0]
    proposed = _fragment(new.html)
    new_images = proposed.xpath(".//img[@src]")
    old = _fragment(chapter.html)
    blocks = list(_protected_blocks(old))
    matches = defaultdict(deque)
    tags = {node.tag for node in blocks}
    for element in proposed.iterdescendants():
        if element.tag in tags:
            matches[_block_match(element, new, recognized.assets)].append(element)

    retained_at_end = 0
    for element in blocks:
        copy = deepcopy(element)
        copy.tail = None
        options = matches[_block_match(element, chapter, book.assets)]
        # A previous swap can have removed a matching descendant.
        while options and proposed not in options[0].iterancestors():
            options.popleft()
        if options:
            match = options.popleft()
            copy.tail = match.tail
            match.getparent().replace(match, copy)
        else:
            if copy.tag in _INLINE:
                wrapper = html.Element("p")
                wrapper.append(copy)
                copy = wrapper
            proposed.append(copy)
            retained_at_end += 1

    assets = {}
    prefix = f"ocr/{uuid4().hex}"
    for image in new_images:
        if proposed not in image.iterancestors():
            continue
        key = _asset_key(new.path, image.get("src"))
        if key in recognized.assets:
            target = f"{prefix}/{key}"
            assets[target] = recognized.assets[key]
            image.set("src", asset_href(target, chapter.path))

    fragment = _serialize_fragment(proposed)
    if re.search(r"<(?:[\w.-]+:)?html\b", chapter.html, re.IGNORECASE):
        original = _xml(chapter.html.encode("utf-8"))
        _set_body(original.find("{*}body"), fragment)
        fragment = etree.tostring(original.getroottree(), encoding="utf-8", xml_declaration=True).decode("utf-8")
    return OcrReplacement(fragment, assets, dict(new.scan), retained_at_end)


def _readable(document: str) -> str:
    tree = _fragment(document)
    for node in tree.xpath(".//script | .//style"):
        node.drop_tree()
    for node in tree.iterdescendants():
        if node.tag == "img":
            alt = node.get("alt", "")
            marker = f"【插图：{alt}】" if alt else "【插图】"
            node.tail = marker + (node.tail or "")
        elif node.tag in {"td", "th"}:
            node.tail = "\t" + (node.tail or "")
        elif node.tag in {"p", "li", "br", "tr", "h1", "h2", "h3", "h4", "figcaption"}:
            node.tail = "\n" + (node.tail or "")
    return re.sub(r"\n{3,}", "\n\n", tree.text_content().strip())


def _review_layout(dialog: QDialog, title: str) -> QVBoxLayout:
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 19, 22, 18)
    layout.setSpacing(12)
    return layout


def _text_panel(label: str, value: str) -> QWidget:
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(_label(label, "section"))
    text = QPlainTextEdit(value)
    text.setAccessibleName(label)
    text.setReadOnly(True)
    text.document().setDocumentMargin(10)
    layout.addWidget(text, 1)
    return panel


class ScanPreview(QScrollArea):
    def __init__(self):
        super().__init__()
        self.image = QPixmap()
        self.fit = True
        self.paper = QLabel(alignment=Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.paper.setAccessibleName("原扫描页")
        self.setAccessibleName("原扫描页图像")
        self.setWidget(self.paper)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

    def show_page(self, image: QPixmap) -> None:
        self.image = image
        self.verticalScrollBar().setValue(0)
        self.horizontalScrollBar().setValue(0)
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh()

    def refresh(self):
        if self.image.isNull():
            self.paper.clear()
            self.paper.resize(0, 0)
            return
        width = max(1, self.viewport().width() - self.verticalScrollBar().sizeHint().width())
        visible = self.image
        if self.fit and visible.width() > width:
            visible = visible.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        self.paper.setPixmap(visible)
        self.paper.resize(visible.size())


class OcrReviewDialog(QDialog):
    def __init__(self, chapter: Chapter, parent):
        super().__init__(parent)
        self.previous = known_scan(chapter)
        self.path: Path | None = None
        self.digest = ""
        self.selection: dict | None = None
        self.relink_only = False
        self.statuses = ocr_readiness()

        layout = _review_layout(self, "原页对照与单页 OCR")
        layout.addWidget(_label(chapter.title, "section"))
        source_row = QHBoxLayout()
        self.source_label = _label("请选择原扫描件", "muted")
        source_row.addWidget(self.source_label, 1)
        source = QPushButton("选择扫描文件…")
        source.clicked.connect(self.choose_source)
        source_row.addWidget(source)
        layout.addLayout(source_row)

        page_row = QHBoxLayout()
        page_label = QLabel("原页页码")
        self.page = QSpinBox()
        initial_page = self.previous["page"] if self.previous else 1
        self.page.setRange(1, initial_page)
        self.page.setValue(initial_page)
        self.page.setAccessibleName("原页页码")
        page_label.setBuddy(self.page)
        page_row.addWidget(page_label)
        page_row.addWidget(self.page)
        self.load_page = QPushButton("查看此页")
        self.load_page.clicked.connect(self.preview_page)
        page_row.addWidget(self.load_page)
        page_row.addStretch()
        self.zoom = QComboBox()
        self.zoom.setAccessibleName("原页缩放")
        self.zoom.addItem("适应宽度", True)
        self.zoom.addItem("放大查看", False)
        page_row.addWidget(self.zoom)
        layout.addLayout(page_row)

        panes = QSplitter()
        original = QWidget()
        original_layout = QVBoxLayout(original)
        original_layout.setContentsMargins(0, 0, 0, 0)
        original_layout.addWidget(_label("原扫描页", "section"))
        self.preview = ScanPreview()
        self.zoom.currentIndexChanged.connect(self.change_zoom)
        original_layout.addWidget(self.preview, 1)
        panes.addWidget(original)
        panes.addWidget(_text_panel("当前正文", _readable(chapter.html)))
        panes.setChildrenCollapsible(False)
        panes.setSizes([470, 470])
        layout.addWidget(panes, 1)

        controls = QHBoxLayout()
        engine_label = QLabel("重识别模型")
        self.engine = QComboBox()
        self.engine.setAccessibleName("重识别模型")
        engine_label.setBuddy(self.engine)
        for key, name, hint in MODELS:
            status = self.statuses[key]
            self.engine.addItem(name if status.ready else f"{name}（待配置）", key)
            self.engine.setItemData(self.engine.count() - 1, hint, Qt.ItemDataRole.ToolTipRole)
        preferred = self.previous.get("engine") if self.previous else None
        if preferred in self.statuses and self.statuses[preferred].ready:
            self.engine.setCurrentIndex(self.engine.findData(preferred))
        elif not self.statuses["xiaomi"].ready and self.statuses["paddle"].ready:
            self.engine.setCurrentIndex(self.engine.findData("paddle"))
        controls.addWidget(engine_label)
        controls.addWidget(self.engine)
        self.engine_status = _label("", "muted")
        controls.addWidget(self.engine_status, 1)
        layout.addLayout(controls)

        self.message = _label("", "muted")
        layout.addWidget(self.message)
        buttons, self.run = _actions(self, "识别此页")
        self.run.setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("关闭")
        self.relink = buttons.addButton("关联原页", QDialogButtonBox.ButtonRole.ActionRole)
        self.relink.clicked.connect(self.accept_source)
        layout.addWidget(buttons)
        self.engine.currentIndexChanged.connect(self.update_controls)
        self.page.valueChanged.connect(self.invalidate_selection)
        self.resize(970, 660)

        if self.previous:
            self.open_source(Path(self.previous["path"]))
        else:
            self.message.setText("选择扫描件及对应页码，即可查看和关联原页。")
        self.update_controls()
        source.setFocus()

    def update_controls(self):
        available = self.path is not None
        self.page.setEnabled(available and self.previous is None and self.page.maximum() > 1)
        self.load_page.setEnabled(available)
        self.zoom.setEnabled(self.selection is not None)
        state = self.statuses[self.engine.currentData()]
        self.engine_status.setText("" if state.ready else state.message)
        self.run.setEnabled(self.selection is not None and state.ready)
        self.relink.setEnabled(self.selection is not None and self.selection != self.previous)

    def choose_source(self):
        filename, _ = QFileDialog.getOpenFileName(self, "选择原扫描件", "", _SCAN_FILTER)
        if filename:
            self.open_source(Path(filename))

    def open_source(self, path: Path):
        try:
            source, digest = associate_path(path, self.previous)
            with scan_pages(source, "") as (numbers, _):
                count = len(numbers)
        except (OcrError, OSError, RuntimeError) as exc:
            self.message.setText(str(exc))
            return
        self.path, self.digest = source, digest
        page = self.previous["page"] if self.previous else 1
        self.page.setRange(1, max(count, page))
        self.page.setValue(page)
        self.source_label.setText(source.name)
        self.source_label.setToolTip(str(source))
        self.invalidate_selection()
        if self.previous or count == 1:
            self.preview_page()

    def invalidate_selection(self):
        self.selection = None
        self.preview.show_page(QPixmap())
        self.message.setText("请核对页码后选择「查看此页」。")
        self.update_controls()

    def preview_page(self):
        if self.path is None:
            return
        page = self.page.value()
        try:
            with scan_pages(self.path, str(page), pdf_max_edge=1800) as (_, read_image):
                image = scan_rgb(read_image(page))
                image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
                with BytesIO() as buffer:
                    image.save(buffer, "PNG")
                    pixmap = QPixmap()
                    if not pixmap.loadFromData(buffer.getvalue(), "PNG"):
                        raise OcrError("无法显示原扫描页。")
        except (OcrError, OSError, RuntimeError) as exc:
            self.message.setText(str(exc))
            return
        engine = self.previous.get("engine") if self.previous else None
        self.selection = scan_provenance(self.path, self.digest, page, engine)
        self.preview.show_page(pixmap)
        self.message.setText(f"正在查看原件第 {page} 页。")
        self.update_controls()

    def change_zoom(self):
        self.preview.fit = self.zoom.currentData()
        self.preview.refresh()

    def accept_source(self):
        if self.selection:
            self.relink_only = True
            self.accept()

    def accept(self):
        if self.selection and (self.relink_only or self.statuses[self.engine.currentData()].ready):
            super().accept()


def _confirm_replacement(parent, chapter: Chapter, proposed: OcrReplacement) -> bool:
    dialog = QDialog(parent)
    layout = _review_layout(dialog, "确认单页识别结果")
    layout.addWidget(_label(chapter.title, "section"))
    panes = QSplitter()
    panes.addWidget(_text_panel("当前正文", _readable(chapter.html)))
    panes.addWidget(_text_panel("新识别结果", _readable(proposed.html)))
    panes.setChildrenCollapsible(False)
    panes.setSizes([470, 470])
    layout.addWidget(panes, 1)
    if proposed.retained_at_end:
        layout.addWidget(_label("无法按正文对齐的原有链接、锚点或插图已保留在章末。", "muted"))
    buttons, accept = _actions(dialog, "采用新结果")
    accept.setObjectName("primary")
    accept.setDefault(False)
    accept.setAutoDefault(False)
    cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
    cancel.setText("保留当前正文")
    cancel.setFocus()
    layout.addWidget(buttons)
    dialog.resize(970, 650)
    return dialog.exec() == QDialog.DialogCode.Accepted


def show_ocr_review(studio, *, checkpoint=None):
    """Review the active chapter. Call checkpoint(book, label) before an accepted change."""
    def review():
        chapter = studio.chapter()
        if chapter is None or studio.task is not None:
            return
        try:
            dialog = OcrReviewDialog(chapter, studio)
        except (ValueError, etree.LxmlError) as exc:
            studio.error(str(exc))
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        scan = dialog.selection
        book = studio.book

        def preserve(label):
            try:
                if checkpoint is not None:
                    return checkpoint(book, label) is not False
            except (OSError, ValueError) as exc:
                studio.error(str(exc))
                return False
            return True

        if dialog.relink_only:
            if preserve("关联原扫描页"):
                chapter.scan = scan
                studio.changed()
                studio.statusBar().showMessage("原扫描页已关联。")
            return

        engine = dialog.engine.currentData()

        def complete(result):
            if studio.book is not book or studio.chapter() is not chapter:
                return
            try:
                proposed = prepare_replacement(book, chapter, result)
                accepted = _confirm_replacement(studio, chapter, proposed)
            except (ValueError, etree.LxmlError) as exc:
                studio.error(str(exc))
                return
            if accepted and preserve("采用单页 OCR 结果"):
                book.assets.update(proposed.assets)
                chapter.html = proposed.html
                chapter.scan = proposed.scan
                studio.changed()
                studio.select(studio.active)
                studio.statusBar().showMessage("已采用新识别结果。")

        studio.start_task("正在重识别当前页…", lambda progress: recognize_page(scan, engine, progress),
                          complete, reports_progress=True)

    studio.flush_current_edits(review)
