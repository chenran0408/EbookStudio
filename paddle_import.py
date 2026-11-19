"""Import scanned pages with the separately installed PaddleOCR-VL-1.6 SDK."""

from collections import deque
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from ebook_core import Book
from ocr_import import OcrError, scan_pages, scan_rgb
from paddle_content import paddle_chapter

DEFAULT_PYTHON = Path(r"D:\Dev\SDKs\EbookStudio-PaddleOCR\Scripts\python.exe")
DEFAULT_MODELS = Path(r"D:\Dev\Cache\ebook-studio-paddle-models")


@contextmanager
def _external_dll_search():
    bundled = getattr(sys, "_MEIPASS", None)
    if not bundled or os.name != "nt":
        yield
        return
    # The frozen editor and the external Paddle interpreter use different Python DLLs.
    set_directory = ctypes.windll.kernel32.SetDllDirectoryW
    if not set_directory(None):
        raise ctypes.WinError()
    try:
        yield
    finally:
        set_directory(str(bundled))


def paddle_book(path: str | Path, *, pages: str = "", progress=None) -> Book:
    source = Path(path)
    python = Path(os.environ.get("EBOOK_STUDIO_PADDLE_PYTHON", str(DEFAULT_PYTHON)))
    if not python.is_file():
        raise OcrError("未找到 PaddleOCR-VL-1.6 运行环境。可在使用说明中查看安装和路径配置。")
    env = os.environ.copy()
    env.setdefault("EBOOK_STUDIO_PADDLE_MODELS", str(DEFAULT_MODELS))
    if bundled := getattr(sys, "_MEIPASS", None):
        env["PATH"] = os.pathsep.join(
            entry for entry in env.get("PATH", "").split(os.pathsep)
            if not Path(entry.strip('"')).is_relative_to(bundled)
        )
    worker = Path(__file__).with_name("paddle_worker.py")
    book = Book(title=source.stem)
    with TemporaryDirectory(prefix="ebook-studio-paddle-") as directory:
        work = Path(directory)
        with scan_pages(source, pages, pdf_max_edge=3000) as (selected, read_image):
            if progress:
                progress(0, len(selected), "正在读取页面")
            for page in selected:
                scan_rgb(read_image(page)).save(work / f"scan-{page}.png")
        (work / "pages.json").write_text(json.dumps(selected), encoding="utf-8")

        log = deque(maxlen=12)
        with _external_dll_search():
            process = subprocess.Popen(
                [str(python), "-I", "-X", "utf8", "-u", str(worker), str(work)], env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        with process:
            for line in process.stdout:
                fields = line.strip().split()
                if fields[:1] == ["EBOOK_STUDIO_PAGE"]:
                    offset, page = map(int, fields[1:])
                    if progress:
                        progress(offset, len(selected), f"第 {page} 页已完成")
                else:
                    log.append(line)
            if process.wait():
                raise OcrError("PaddleOCR-VL-1.6 识别失败。\n" + "".join(log).strip())

        for page in selected:
            data = json.loads((work / f"result-{page}.json").read_text(encoding="utf-8"))
            chapter, images = paddle_chapter(
                data["markdown"], {key: work / value for key, value in data["images"].items()}, page,
            )
            book.chapters.append(chapter)
            book.assets.update(images)
    return book
