"""Durable page checkpoints for a single scan and recognition model."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from uuid import uuid4

from ebook_core import Book, publish_file
from ocr_import import (
    DEFAULT_MODEL, MAX_TOKENS, OcrError, scan_digest, scan_pages, scan_provenance, xiaomi_chapter,
)
from ocr_runtime import local_server
from paddle_import import PaddleSessionError, paddle_session
from workspace import _lock_file


ENGINE_NAMES = {"xiaomi": "小米 OCR", "paddle": "PaddleOCR-VL-1.6"}


@dataclass(frozen=True)
class PageState:
    number: int
    status: str
    error: str = ""


@dataclass(frozen=True)
class OcrBatch:
    directory: Path
    source: Path
    digest: str
    pages: tuple[int, ...]
    engine: str
    model: str

    @classmethod
    def open(cls, directory):
        data = json.loads((directory / "batch.json").read_text(encoding="utf-8"))
        return cls(directory, Path(data["source"]), data["digest"], tuple(data["pages"]),
                   data["engine"], data["model"])

    def _write_manifest(self):
        data = {"source": str(self.source), "digest": self.digest, "pages": self.pages,
                "engine": self.engine, "model": self.model}
        with publish_file(self.directory / "batch.json") as temporary:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @contextmanager
    def _locked(self):
        handle = _lock_file(self.directory / "batch.lock", wait=False)
        if handle is None:
            raise OcrError("此批次正在另一个工作台窗口中处理。")
        with handle:
            yield OcrBatch.open(self.directory)

    def _page_path(self, page):
        return self.directory / f"page-{page}.bookproj"

    def _error_path(self, page):
        return self.directory / f"page-{page}.error.txt"

    def page_state(self, page):
        if self._page_path(page).is_file():
            return PageState(page, "done")
        try:
            error = self._error_path(page).read_text(encoding="utf-8")
        except FileNotFoundError:
            return PageState(page, "pending")
        return PageState(page, "failed", error)

    def states(self):
        return [self.page_state(page) for page in self.pages]

    def _verify_source(self, path):
        if not path.is_file():
            raise OcrError("找不到扫描原件。请重新定位原件后继续。")
        if scan_digest(path) != self.digest:
            raise OcrError("扫描文件与本批次的原件不一致，请选择内容相同的文件。")

    def relocate(self, source):
        source = Path(source).expanduser().resolve()
        with self._locked() as current:
            current._verify_source(source)
            with scan_pages(source, ",".join(map(str, current.pages))):
                pass
            updated = replace(current, source=source)
            updated._write_manifest()
            return updated

    def run(self, *, stop, notify, retry=False, page=None):
        """Process pending pages or explicitly retry failed pages. Stop between pages."""
        with self._locked() as current:
            wanted = "failed" if retry else "pending"
            pages = [state.number for state in current.states()
                     if state.status == wanted and (page is None or state.number == page)]
            if not pages or stop():
                return
            current._verify_source(current.source)
            max_edge = 3000 if current.engine == "paddle" else 2048
            with scan_pages(current.source, ",".join(map(str, pages)), pdf_max_edge=max_edge) as (_, read_image):
                if stop():
                    return
                with _engine(current) as recognize:
                    for number in pages:
                        if stop():
                            break
                        notify(PageState(number, "running"))
                        try:
                            chapter, images = recognize(read_image(number), number)
                        except (OSError, RuntimeError, ValueError) as exc:
                            error = str(exc) or type(exc).__name__
                            with publish_file(current._error_path(number)) as temporary:
                                temporary.write_text(error, encoding="utf-8")
                            notify(PageState(number, "failed", error))
                            if isinstance(exc, PaddleSessionError):
                                break
                        else:
                            chapter.scan = scan_provenance(current.source, current.digest, number, current.engine)
                            Book(chapters=[chapter], assets=images).save(current._page_path(number))
                            current._error_path(number).unlink(missing_ok=True)
                            notify(PageState(number, "done"))

    def build_book(self):
        current = OcrBatch.open(self.directory)
        result = Book(title=current.source.stem, identifier=f"urn:uuid:{current.directory.name}")
        for page in current.pages:
            path = current._page_path(page)
            if not path.is_file():
                continue
            fragment = Book.load(path)
            chapter = fragment.chapters[0]
            chapter.scan = scan_provenance(current.source, current.digest, page, current.engine)
            result.chapters.append(chapter)
            result.assets.update(fragment.assets)
        if not result.chapters:
            raise OcrError("本批次还没有可打开的识别结果。")
        return result


class OcrBatchStore:
    def __init__(self, workspace: Path):
        self.directory = workspace / "ocr-batches"

    def create(self, source, pages, engine):
        if engine not in ENGINE_NAMES:
            raise OcrError("请选择本机已配置的 OCR 模型。")
        source = Path(source).expanduser().resolve()
        with scan_pages(source, pages) as (selected, _):
            digest = scan_digest(source)
        directory = self.directory / str(uuid4())
        directory.mkdir(parents=True)
        model = os.environ.get("EBOOK_STUDIO_OCR_MODEL", DEFAULT_MODEL) if engine == "xiaomi" else "PaddleOCR-VL-1.6"
        batch = OcrBatch(directory, source, digest, tuple(selected), engine, model)
        batch._write_manifest()
        return batch

    def batches(self):
        manifests = sorted(self.directory.glob("*/batch.json"), key=lambda path: path.stat().st_ctime, reverse=True)
        return [OcrBatch.open(manifest.parent) for manifest in manifests]


@contextmanager
def _engine(batch):
    if batch.engine == "paddle":
        with paddle_session() as recognize:
            yield recognize
    else:
        with local_server() as (endpoint, _):
            yield lambda image, page: (xiaomi_chapter(image, endpoint, batch.model, MAX_TOKENS, page), {})
