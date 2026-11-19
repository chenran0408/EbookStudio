"""Inspect installed tools and reserve destination names without writing files."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil

import conversion
from ocr_import import DEFAULT_MODEL
from ocr_runtime import ollama_executable, ollama_models_folder
from paddle_import import DEFAULT_MODELS, DEFAULT_PYTHON

TOOL_ROOT = Path(r"D:\Dev\Tools\EbookStudio")


@dataclass(frozen=True, slots=True)
class ToolStatus:
    ready: bool
    message: str


@dataclass(frozen=True, slots=True)
class OutputDestination:
    format_name: str
    requested: Path
    destination: Path
    conflict: bool
    replace_existing: bool


def _installed_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _manifest_paths(folder: Path, model: str) -> Iterable[Path]:
    # Ollama stores registry / namespace / model / tag under either layout.
    parts = model.split("/")
    leaf, colon, tag = parts[-1].partition(":")
    parts[-1] = leaf
    if not colon:
        tag = "latest"
    valid_component = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*")
    if not all(valid_component.fullmatch(part) for part in [*parts, tag]):
        return
    if len(parts) == 3:
        registry, namespace, name = parts
        registries = [registry]
    elif len(parts) in {1, 2}:
        namespace, name = parts if len(parts) == 2 else ("library", parts[0])
        registries = ["ollama.com", "registry.ollama.ai"]
    else:
        return
    for layout in ("manifests-v2", "manifests"):
        for registry in registries:
            yield folder / layout / registry / namespace / name / tag


def _manifest_complete(path: Path, folder: Path) -> bool:
    if not path.is_file():
        return False
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        layers = manifest["layers"]
        if not layers:
            return False
        for entry in [manifest["config"], *layers]:
            digest = entry["digest"]
            length = entry["size"]
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest) or length <= 0:
                return False
            blob = folder / "blobs" / digest.replace(":", "-", 1)
            if not blob.is_file() or blob.stat().st_size != length:
                return False
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return True


def _xiaomi_readiness() -> ToolStatus:
    try:
        command = ollama_executable()
        if not _installed_file(command):
            return ToolStatus(False, f"Ollama 程序无效：{command}")
    except (OSError, ValueError) as exc:
        return ToolStatus(False, str(exc))
    folder = ollama_models_folder()
    model = os.environ.get("EBOOK_STUDIO_OCR_MODEL", DEFAULT_MODEL)
    if not any(_manifest_complete(path, folder) for path in _manifest_paths(folder, model)):
        return ToolStatus(False, f"未找到完整模型 {model}。模型目录：{folder}")
    return ToolStatus(True, f"已找到 Ollama 和本地模型 {model}。")


def _paddle_readiness() -> ToolStatus:
    python = Path(os.environ.get("EBOOK_STUDIO_PADDLE_PYTHON", str(DEFAULT_PYTHON)))
    folder = Path(os.environ.get("EBOOK_STUDIO_PADDLE_MODELS", str(DEFAULT_MODELS)))
    paths = [
        folder / "PP-DocLayoutV3" / "inference.pdiparams",
        folder / "PaddleOCR-VL-1.6" / "model.safetensors",
    ]
    try:
        if not _installed_file(python):
            return ToolStatus(False, f"未找到 PaddleOCR 的 Python 环境：{python}")
        missing = [path.parent.name for path in paths if not _installed_file(path)]
        if missing:
            return ToolStatus(False, f"模型文件缺失：{'、'.join(missing)}。模型目录：{folder}")
    except OSError as exc:
        return ToolStatus(False, f"无法检查 PaddleOCR 安装：{exc}")
    return ToolStatus(True, "已找到 PaddleOCR 的 Python 环境和本地模型文件。")


def ocr_readiness() -> dict[str, ToolStatus]:
    """Check local files without starting Ollama, importing Paddle, or loading GPU models."""
    return {"xiaomi": _xiaomi_readiness(), "paddle": _paddle_readiness()}


def epubcheck_jar() -> Path | None:
    if configured := os.environ.get("EBOOK_STUDIO_EPUBCHECK"):
        path = Path(configured)
        return path if path.is_absolute() and path.suffix.lower() == ".jar" and path.is_file() else None

    roots = [TOOL_ROOT]
    if local_data := os.environ.get("LOCALAPPDATA"):
        roots.insert(0, Path(local_data) / "EbookStudio" / "tools")
    for root in roots:
        if jar := max((path for path in root.glob("epubcheck-*/epubcheck.jar") if path.is_file()), default=None):
            return jar
    return None


def converter_readiness() -> dict[str, ToolStatus]:
    """Report optional converter and validator installations."""
    results = {}
    for name, label in (("pandoc", "Pandoc"), ("calibre", "calibre")):
        if path := conversion.tool_path(name):
            results[name] = ToolStatus(True, f"已找到 {label}：{path}")
        else:
            results[name] = ToolStatus(False, f"未找到 {label}。需要时可安装或配置路径。")
    jar = epubcheck_jar()
    java = shutil.which("java")
    if jar and java:
        results["epubcheck"] = ToolStatus(True, f"已找到 Java 和 EPUBCheck：{jar}")
    elif not jar and os.environ.get("EBOOK_STUDIO_EPUBCHECK"):
        results["epubcheck"] = ToolStatus(False, "EBOOK_STUDIO_EPUBCHECK 必须指向现有 JAR 的绝对路径。")
    else:
        missing = "Java" if jar else "EPUBCheck" if java else "Java 和 EPUBCheck"
        results["epubcheck"] = ToolStatus(False, f"未找到 {missing}。")
    return results


def conversion_stem(path: str | Path) -> str:
    name = Path(path).name
    for compound in (".kepub.epub", ".fb2.zip"):
        if name.lower().endswith(compound):
            return name[:-len(compound)]
    return Path(name).stem


def _filename_stem(stem: str) -> str:
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", stem).strip(" .") or "电子书"
    if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³])", stem.split(".")[0]):
        stem = "_" + stem
    return stem


def plan_destinations(
    folder: str | Path,
    requests: Iterable[tuple[str, str]],
    *,
    replace: bool = False,
    protected: Iterable[str | Path] = (),
) -> list[OutputDestination]:
    """Plan (stem, format) requests in order. Protected paths are always preserved."""
    folder = Path(folder).resolve()
    reserved = {str(Path(path).resolve()).casefold() for path in protected}
    plans = []
    for stem, format_name in requests:
        stem = _filename_stem(stem)
        if format_name == "epub2":
            stem += " - EPUB 2"
            suffix = "epub"
        elif format_name in conversion.OUTPUT_FORMATS:
            suffix = format_name
        else:
            raise ValueError(f"不支持的输出格式：{format_name}")
        desired = folder / f"{stem}.{suffix}"
        destination = desired
        number = 2
        conflict = False
        while True:
            key = str(destination.resolve()).casefold()
            existing = destination.exists()
            occupied = key in reserved or destination.is_symlink()
            can_replace = replace and destination == desired and destination.is_file()
            if not occupied and (not existing or can_replace):
                break
            conflict = True
            destination = folder / f"{stem} ({number}).{suffix}"
            number += 1
        reserved.add(key)
        plans.append(OutputDestination(
            format_name, desired, destination, conflict or existing, existing,
        ))
    return plans
