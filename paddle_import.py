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
from time import monotonic, sleep

from ebook_core import Book
from ocr_import import OcrError, scan_digest, scan_pages, scan_provenance, scan_rgb
from paddle_content import paddle_chapter

DEFAULT_PYTHON = Path(r"D:\Dev\SDKs\EbookStudio-PaddleOCR\Scripts\python.exe")
DEFAULT_MODELS = Path(r"D:\Dev\Cache\ebook-studio-paddle-models")
START_TIMEOUT = 180
PAGE_TIMEOUT = 300
STOP_TIMEOUT = 8


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


class PaddleSessionError(OcrError):
    """The worker exited and cannot read any more pages in this session."""


def _stop_worker(process):
    # On Windows the venv launcher may start a separate Python worker process.
    # Let the worker exit on EOF before resorting to terminating that process tree.
    try:
        process.stdin.close()
    except BrokenPipeError:
        pass
    try:
        process.wait(timeout=STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            taskkill = Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"
            subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def _pipe_reader(output):
    """Return available bytes, None while empty, or b'' after the pipe closes."""
    fd = output.fileno()
    if os.name == "nt":
        import msvcrt

        handle = ctypes.c_void_p(msvcrt.get_osfhandle(fd))
        peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe

        def read():
            available = ctypes.c_ulong()
            if not peek(handle, None, 0, None, ctypes.byref(available), None):
                error = ctypes.get_last_error()
                if error in {109, 232}:  # ERROR_BROKEN_PIPE, ERROR_NO_DATA
                    return b""
                raise ctypes.WinError(error)
            if available.value:
                return os.read(fd, min(available.value, 8192))
            return None
    else:
        os.set_blocking(fd, False)

        def read():
            try:
                return os.read(fd, 8192)
            except BlockingIOError:
                return None
    return read


@contextmanager
def paddle_session():
    """Keep one model loaded while the caller sends and commits pages one at a time."""
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
    with TemporaryDirectory(prefix="ebook-studio-paddle-") as directory:
        work = Path(directory)
        tail = deque(maxlen=12)
        with _external_dll_search():
            process = subprocess.Popen(
                [str(python), "-I", "-X", "utf8", "-u", str(worker), str(work)], env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        with process.stdout as output:
            read_output = _pipe_reader(output)
            buffer = b""

            def receive(marker, timeout, activity):
                nonlocal buffer
                deadline = monotonic() + timeout
                while monotonic() < deadline:
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        text = line.decode("utf-8", errors="replace").rstrip("\r")
                        message = text.rsplit("\r", 1)[-1]
                        if message.startswith(marker):
                            return message[len(marker):].strip()
                        tail.append(text)
                    chunk = read_output()
                    if chunk == b"" or (chunk is None and process.poll() is not None):
                        detail = "\n".join([*tail, buffer.decode("utf-8", errors="replace")]).strip()
                        raise PaddleSessionError("PaddleOCR-VL-1.6 识别进程已退出。\n" + detail)
                    if chunk is None:
                        sleep(0.05)
                    else:
                        buffer += chunk
                raise PaddleSessionError(f"PaddleOCR-VL-1.6 {activity}超时。\n" + "\n".join(tail).strip())

            def recognize(image, page):
                scan_rgb(image).save(work / "scan.png")
                try:
                    process.stdin.write("page\n")
                    process.stdin.flush()
                except OSError as exc:
                    raise PaddleSessionError("PaddleOCR-VL-1.6 识别进程已退出。") from exc
                reply = json.loads(receive("EBOOK_STUDIO_RESULT ", PAGE_TIMEOUT, "单页识别"))
                if error := reply.get("error"):
                    raise OcrError(error)
                data = json.loads((work / "result.json").read_text(encoding="utf-8"))
                return paddle_chapter(data["markdown"], {key: work / value for key, value in data["images"].items()}, page)

            try:
                receive("EBOOK_STUDIO_READY", START_TIMEOUT, "模型启动")
                yield recognize
            finally:
                _stop_worker(process)


def paddle_book(path: str | Path, *, pages: str = "", progress=None) -> Book:
    source = Path(path).expanduser().resolve()
    book = Book(title=source.stem)
    with scan_pages(source, pages, pdf_max_edge=3000) as (selected, read_image):
        digest = scan_digest(source)
        if progress:
            progress(0, len(selected), "正在读取页面")
        with paddle_session() as recognize:
            for offset, page in enumerate(selected, 1):
                chapter, images = recognize(read_image(page), page)
                chapter.scan = scan_provenance(source, digest, page, "paddle")
                book.chapters.append(chapter)
                book.assets.update(images)
                if progress:
                    progress(offset, len(selected), f"第 {page} 页已完成")
    return book
