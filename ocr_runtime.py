"""Start a private Ollama instance for an OCR task."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import socket
import subprocess
from tempfile import TemporaryFile
import time
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler


SDK_FOLDER = Path(r"D:\Dev\SDKs\Ollama")
MODEL_FOLDER = Path(r"D:\Dev\Cache\ebook-studio-ocr-models")


def ollama_executable() -> Path:
    configured = os.environ.get("EBOOK_STUDIO_OLLAMA")
    if configured:
        path = Path(configured)
        if path.is_file():
            return path
        raise ValueError("EBOOK_STUDIO_OLLAMA 没有指向有效的 ollama.exe。")
    if path := shutil.which("ollama"):
        return Path(path)
    if local_data := os.environ.get("LOCALAPPDATA"):
        path = Path(local_data) / "Programs" / "Ollama" / "ollama.exe"
        if path.is_file():
            return path
    for path in sorted(SDK_FOLDER.glob("*/ollama.exe"), reverse=True):
        return path
    raise ValueError("没有找到 Ollama。请安装 Ollama，或用 EBOOK_STUDIO_OLLAMA 指定 ollama.exe。")


def ollama_models_folder() -> Path:
    configured = os.environ.get("EBOOK_STUDIO_OCR_MODELS") or os.environ.get("OLLAMA_MODELS")
    if configured:
        return Path(configured).expanduser().resolve()
    if MODEL_FOLDER.is_dir():
        return MODEL_FOLDER
    return Path.home() / ".ollama" / "models"


@contextmanager
def local_server(*, executable: Path | None = None, port: int | None = None):
    """Return a loopback URL and subprocess environment. Close only our process."""
    command = executable or ollama_executable()
    if port is None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
    address = f"127.0.0.1:{port}"
    url = f"http://{address}"
    env = os.environ.copy()
    env["OLLAMA_HOST"] = address
    env["OLLAMA_MODELS"] = str(ollama_models_folder())
    env["OLLAMA_CONTEXT_LENGTH"] = "16384"
    env["OLLAMA_NUM_PARALLEL"] = "1"
    env["OLLAMA_NO_CLOUD"] = "1"
    # PyInstaller's DLL directory affects subprocess lookups on Windows.
    # Ollama has its own bundled GPU libraries. Launch from its own folder.
    with TemporaryFile() as log:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with subprocess.Popen([str(command), "serve"], env=env, cwd=command.parent,
                              stdout=log, stderr=log, creationflags=flags) as process:
            try:
                opener = build_opener(ProxyHandler({}))
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        with opener.open(f"{url}/api/version", timeout=1):
                            break
                    except (URLError, TimeoutError):
                        time.sleep(.25)
                else:
                    log.seek(0)
                    detail = log.read()[-1600:].decode("utf-8", errors="replace").strip()
                    raise ValueError(f"Ollama 未能启动。\n{detail}")
                yield url, env
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

