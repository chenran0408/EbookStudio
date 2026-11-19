"""Local converters used by the conversion window and document import."""
from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from ebook_core import commit_file


class ConversionError(ValueError):
    """A conversion failure suitable for display in the desktop UI."""


OUTPUT_FORMATS = (
    "epub", "pdf", "docx", "md", "html", "txt", "azw3", "kepub.epub",
    "mobi", "fb2", "odt", "rtf", "cbz",
)

_PANDOC_INPUT = {
    "md": "commonmark_x",
    "html": "html+raw_html",
    "docx": "docx",
    "odt": "odt",
    "rtf": "rtf",
    "epub": "epub",
    "fb2": "fb2",
}
_PANDOC_OUTPUT = {
    "epub": "epub3",
    "md": "commonmark_x",
    "html": "html5",
    "txt": "plain",
    "docx": "docx",
    "odt": "odt",
    "rtf": "rtf",
    "fb2": "fb2",
}
_CALIBRE_INPUT = {
    "epub", "pdf", "docx", "html", "txt", "azw", "azw3", "azw4",
    "kepub.epub", "mobi", "fb2", "fbz", "odt", "rtf", "cbz", "cbr", "djvu", "chm",
}
_CALIBRE_OUTPUT = {"epub", "pdf", "docx", "txt", "azw3", "kepub.epub", "mobi", "fb2", "rtf"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp"}
_TOOL_CACHE = Path(r"D:\Dev\Tools\EbookStudio")


def normalize_format(path: str | Path) -> str:
    """Return the canonical format token for a source or destination filename."""
    name = Path(path).name.lower()
    if name.endswith((".kepub.epub", ".kepub")):
        return "kepub.epub"
    if name.endswith(".fb2.zip"):
        return "fbz"
    suffix = Path(name).suffix.lstrip(".")
    return {"markdown": "md", "htm": "html", "xhtml": "html", "djv": "djvu"}.get(suffix, suffix)


def targets_for(path: str | Path) -> list[str]:
    """List supported destination extensions even if their converter needs setup."""
    return _targets_for_format(normalize_format(path))


def _targets_for_format(source: str) -> list[str]:
    if source in {"cbz", "cbr"}:
        return ["epub", "pdf", "cbz"] if source == "cbr" else ["epub", "pdf"]
    if source not in _PANDOC_INPUT and source not in _CALIBRE_INPUT:
        return []
    return [target for target in OUTPUT_FORMATS if target not in {source, "cbz"}]


def required_tools(source_format: str, target_format: str) -> tuple[str, ...]:
    """Report the external programs a conversion route runs, in execution order."""
    source = "epub" if source_format == "epub2" else source_format
    target = "epub" if target_format == "epub2" else target_format
    if target not in _targets_for_format(source) and (source, target) != ("epub", "epub"):
        raise ConversionError(f"暂不支持 {source_format.upper()} 转换为 {target_format.upper()}。")
    if source in {"cbz", "cbr"} and target in {"pdf", "cbz"}:
        return ("calibre",) if source == "cbr" else ()
    if source in _PANDOC_INPUT and target in _PANDOC_OUTPUT and source != target:
        return ("pandoc",)
    if source in _CALIBRE_INPUT and target in _CALIBRE_OUTPUT:
        return ("calibre",)
    if target in _PANDOC_OUTPUT:
        return ("calibre", "pandoc")
    return ("pandoc", "calibre")


def tool_path(tool: str) -> Path | None:
    """Locate Pandoc or calibre by explicit configuration, PATH, or installed paths."""
    if tool == "pandoc":
        executable = "pandoc"
        environment_variable = "EBOOK_STUDIO_PANDOC"
        custom_paths = sorted(_TOOL_CACHE.glob("pandoc*/pandoc.exe"), reverse=True)
        normal_paths = [Path(os.environ.get("LOCALAPPDATA", "")) / "Pandoc" / "pandoc.exe"]
    elif tool == "calibre":
        executable = "ebook-convert"
        environment_variable = "EBOOK_STUDIO_CALIBRE"
        custom_paths = sorted(_TOOL_CACHE.glob("calibre*/**/ebook-convert.exe"))
        normal_paths = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Calibre2" / "ebook-convert.exe",
        ]
    else:
        raise ValueError(f"Unknown conversion tool: {tool}")

    configured = os.environ.get(environment_variable)
    if configured:
        path = Path(configured).expanduser()
        return path.resolve() if path.is_file() else None
    on_path = shutil.which(executable)
    if on_path:
        return Path(on_path)
    return next((path.resolve() for path in custom_paths + normal_paths if path.is_file()), None)


def _run(tool: str, args: list[str], cwd: Path) -> None:
    executable = tool_path(tool)
    if executable is None:
        label = "Pandoc" if tool == "pandoc" else "calibre"
        env_name = f"EBOOK_STUDIO_{tool.upper()}"
        raise ConversionError(f"找不到 {label}。请安装该工具，或设置 {env_name} 指向转换程序。")
    try:
        result = subprocess.run(
            [str(executable), *args],
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(f"{tool} 转换超过 10 分钟，已结束。") from exc
    except OSError as exc:
        raise ConversionError(f"无法启动 {tool}：{exc}") from exc
    if result.returncode:
        details = (result.stderr.strip() or result.stdout.strip())[-1600:]
        raise ConversionError(f"{tool} 转换失败（{result.returncode}）：{details}")


def _pandoc(source: Path, output: Path, source_format: str, target_format: str,
            epub_version: int, media_folder: str | None) -> None:
    reader = _PANDOC_INPUT[source_format]
    writer = f"epub{epub_version}" if target_format == "epub" else _PANDOC_OUTPUT[target_format]
    args = [str(source), "-f", reader, "-t", writer, "-o", str(output),
            f"--resource-path={source.parent}"]
    if target_format == "epub":
        metadata = output.parent / "metadata.yaml"
        metadata.write_text("lang: zh-CN\n", encoding="utf-8")
        args.append(f"--metadata-file={metadata}")
        filter_path = output.parent / "trim-opening-blank.lua"
        filter_path.write_text(
            "function Pandoc(doc)\n"
            "  while doc.blocks[1] and doc.blocks[1].t == 'Para' and #doc.blocks[1].content == 0 do\n"
            "    doc.blocks:remove(1)\n"
            "  end\n"
            "  return doc\n"
            "end\n",
            encoding="utf-8",
        )
        args.extend(["--epub-title-page=false", f"--lua-filter={filter_path}"])
    if target_format == "html":
        args.extend(["--standalone", "--embed-resources"])
    if media_folder:
        args.append(f"--extract-media={media_folder}")
    _run("pandoc", args, output.parent)


def _calibre(source: Path, output: Path, source_format: str, target_format: str,
             epub_version: int) -> None:
    args = [str(source), str(output)]
    if source_format in {"cbz", "cbr"}:
        args.append("--no-process")
    if target_format == "epub":
        args.extend(["--epub-version", str(epub_version), "--no-default-epub-cover"])
    if target_format == "pdf" and os.name == "nt":
        windows_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        if (windows_fonts / "msyh.ttc").is_file():
            args.extend(["--pdf-serif-family", "Microsoft YaHei", "--pdf-sans-family", "Microsoft YaHei"])
    _run("calibre", args, output.parent)


def _check_text_pdf(path: Path) -> None:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:
        raise ConversionError("需要 pypdf 来检查 PDF 的文字层。") from exc
    try:
        with path.open("rb") as stream:
            reader = PdfReader(stream)
            if reader.is_encrypted:
                raise ConversionError("PDF 受加密保护。请使用可直接打开的副本。")
            for page in reader.pages:
                if any(char.isalnum() for char in (page.extract_text() or "")):
                    return
    except PdfReadError as exc:
        raise ConversionError(f"无法读取 PDF：{exc}") from exc
    raise ConversionError("此 PDF 没有可提取的文字。请先进行 OCR，再转换为可编辑电子书。")


def _natural_order(path: str) -> list[str | int]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"([0-9]+)", path)]


def _cbz_pages(path: Path) -> list[tuple[str, bytes]]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [
                entry.filename for entry in archive.infolist()
                if not entry.is_dir() and Path(entry.filename).suffix.lower() in _IMAGE_SUFFIXES
                and "__MACOSX" not in entry.filename.split("/")
            ]
            return [(name, archive.read(name)) for name in sorted(names, key=_natural_order)]
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise ConversionError(f"无法读取漫画文件：{exc}") from exc


def _epub_comic_pages(path: Path) -> list[tuple[str, bytes]]:
    """Read images in the spine of an EPUB generated by calibre's comic input."""
    with zipfile.ZipFile(path) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        package_path = container.find(".//{*}rootfile").attrib["full-path"]
        package = ET.fromstring(archive.read(package_path))
        package_dir = posixpath.dirname(package_path)
        resources = {
            item.attrib["id"]: posixpath.normpath(posixpath.join(package_dir, unquote(item.attrib["href"])))
            for item in package.findall("./{*}manifest/{*}item")
        }
        pages = []
        for entry in package.findall("./{*}spine/{*}itemref"):
            chapter = resources[entry.attrib["idref"]]
            root = ET.fromstring(archive.read(chapter))
            for node in root.findall(".//{*}img"):
                local_path = unquote(urlsplit(node.attrib["src"]).path)
                image = posixpath.normpath(posixpath.join(posixpath.dirname(chapter), local_path))
                pages.append((image, archive.read(image)))
        return pages


def read_comic(source: str | Path) -> list[tuple[str, bytes]]:
    """Return comic pages in reading order as (name, original image bytes)."""
    source = Path(source).resolve()
    source_format = normalize_format(source)
    if source_format == "cbz":
        pages = _cbz_pages(source)
    elif source_format == "cbr":
        with TemporaryDirectory(prefix="ebook-comic-") as directory:
            intermediate = Path(directory) / "comic.epub"
            _calibre(source, intermediate, "cbr", "epub", 3)
            pages = _epub_comic_pages(intermediate)
    else:
        raise ConversionError("请选择 CBZ 或 CBR 漫画。")
    if not pages:
        raise ConversionError("漫画中没有可读取的图片页。")
    return pages


def write_cbz(pages: list[tuple[str, bytes]], destination: str | Path, *, overwrite: bool = True) -> None:
    """Save the supplied page order in a portable CBZ archive."""
    if not pages:
        raise ConversionError("没有可导出的漫画页。")
    destination = Path(destination)
    destination = destination.parent.resolve() / destination.name
    with TemporaryDirectory(prefix=".ebook-convert-", dir=destination.parent) as directory:
        staged = Path(directory) / "result.cbz"
        with zipfile.ZipFile(staged, "w") as archive:
            for index, (name, data) in enumerate(pages, start=1):
                archive.writestr(f"{index:05d}{Path(name).suffix.lower()}", data)
        commit_file(staged, destination, overwrite=overwrite)


def _comic_pdf(pages: list[tuple[str, bytes]], output: Path) -> None:
    from PIL import Image, ImageOps
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _, data in pages:
        with Image.open(BytesIO(data)) as original:
            image = ImageOps.exif_transpose(original)
            canvas = Image.new("RGB", image.size, "white")
            rgba_image = image.convert("RGBA")
            canvas.paste(rgba_image, mask=rgba_image.getchannel("A"))
            page_pdf = BytesIO()
            canvas.save(page_pdf, format="PDF", resolution=150, quality=95)
            page_pdf.seek(0)
            writer.append(page_pdf, import_outline=False)
    with output.open("wb") as stream:
        writer.write(stream)


def convert(source: str | Path, destination: str | Path, *,
            epub_version: int = 3, overwrite: bool = True) -> None:
    """Convert one local file; EPUB defaults to version 3."""
    source = Path(source).resolve()
    destination = Path(destination)
    destination = destination.parent.resolve() / destination.name
    source_format = normalize_format(source)
    target_format = normalize_format(destination)
    if not source.is_file():
        raise ConversionError(f"找不到源文件：{source}")
    if source == destination.resolve():
        raise ConversionError("输出位置与源文件相同。请选择其他文件名。")
    if epub_version not in {2, 3}:
        raise ConversionError("EPUB 版本应为 2 或 3。")
    tools = required_tools(source_format, target_format)
    if source_format == "pdf":
        _check_text_pdf(source)
    if target_format == "cbz":
        write_cbz(read_comic(source), destination, overwrite=overwrite)
        return

    with TemporaryDirectory(prefix=".ebook-convert-", dir=destination.parent) as directory:
        stage = Path(directory)
        output_extension = "kepub" if target_format == "kepub.epub" else target_format
        output = stage / f"result.{output_extension}"
        if target_format == "md":
            media_folder = f"{destination.stem}-assets"
            suffix = 2
            while (destination.parent / media_folder).exists():
                media_folder = f"{destination.stem}-assets-{suffix}"
                suffix += 1
        else:
            media_folder = None

        if source_format in {"cbz", "cbr"} and target_format == "pdf":
            _comic_pdf(read_comic(source), output)
        elif tools == ("pandoc",):
            _pandoc(source, output, source_format, target_format, epub_version, media_folder)
        elif tools == ("calibre",):
            _calibre(source, output, source_format, target_format, epub_version)
        elif tools == ("calibre", "pandoc"):
            intermediate = stage / "input.epub"
            _calibre(source, intermediate, source_format, "epub", 3)
            _pandoc(intermediate, output, "epub", target_format, epub_version, media_folder)
        else:
            intermediate = stage / "input.epub"
            _pandoc(source, intermediate, source_format, "epub", 3, None)
            _calibre(intermediate, output, "epub", target_format, epub_version)

        if not output.is_file() or not output.stat().st_size:
            raise ConversionError("转换没有生成有效的输出文件。")
        staged_media = stage / media_folder if media_folder else None
        if staged_media and staged_media.is_dir():
            _publish_markdown(output, staged_media, destination, overwrite=overwrite)
        else:
            commit_file(output, destination, overwrite=overwrite)


def _publish_markdown(staged: Path, staged_media: Path, destination: Path, *, overwrite: bool) -> None:
    resources = destination.parent / staged_media.name
    # An exclusive directory creation also preserves a folder that appeared during conversion.
    resources.mkdir()
    moved = []
    try:
        for item in staged_media.iterdir():
            item.rename(resources / item.name)
            moved.append(item)
        commit_file(staged, destination, overwrite=overwrite)
    except BaseException:
        for item in moved:
            (resources / item.name).rename(item)
        resources.rmdir()
        raise
