"""Book workflows shared by the desktop window and the local agent tools."""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from lxml import html

import capture
import conversion
from ebook_core import Asset, Book, Chapter, publish_file, resolve_href
from epub_io import open_epub, write_epub
from workspace_tools import conversion_stem, epubcheck_jar, plan_destinations

if Path(r"D:\Dev\Cache\ebook-studio-browsers").is_dir():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"D:\Dev\Cache\ebook-studio-browsers")

LABELS = {
    "epub": "EPUB 3", "epub2": "EPUB 2", "kepub.epub": "Kobo · KEPUB",
    "kepub": "Kobo · KEPUB", "azw3": "Kindle · AZW3", "mobi": "MOBI",
    "pdf": "PDF", "docx": "Word · DOCX", "odt": "ODT", "rtf": "RTF",
    "md": "Markdown", "txt": "纯文本 · TXT", "html": "HTML",
    "fb2": "FB2", "cbz": "漫画 · CBZ",
}

def safe_name(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", title).strip(" .") or "电子书"


def run_epubcheck(path: Path, *, required: bool = True) -> str | None:
    jar = epubcheck_jar()
    java = shutil.which("java")
    if java is None or jar is None:
        if not required:
            return None
        raise ValueError(
            "需要将 Java 加入 PATH，再用 EBOOK_STUDIO_EPUBCHECK 指定 JAR 的绝对路径，"
            "或把 EPUBCheck 解压到 %LOCALAPPDATA%\\EbookStudio\\tools。"
        )
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run([java, "-jar", str(jar), str(path)], capture_output=True, timeout=180, creationflags=flags)
    report = (result.stdout + result.stderr).decode("utf-8", errors="replace").strip()
    if result.returncode:
        raise ValueError(report)
    return report


def target_path(folder: Path, stem: str, format_name: str) -> Path:
    return plan_destinations(folder, [(stem, format_name)])[0].destination


def convert_files(paths, folder, format_name, *, plans=None, progress=None):
    outputs, errors = [], []
    if plans is None:
        plans = plan_destinations(folder, [(conversion_stem(path), format_name) for path in paths], protected=paths)
    for index, (path, plan) in enumerate(zip(paths, plans)):
        if progress:
            progress(index, len(paths), f"正在转换 {path.name}")
        try:
            convert_to(path, plan.destination, format_name, overwrite=plan.replace_existing)
            outputs.append(plan.destination)
        except Exception as exc:
            errors.append(f"{path.name} · {exc}")
    return outputs, errors


def export_book(book, folder, name, formats, mobile, *, plans=None, progress=None):
    outputs, errors = [], []
    formats = list(formats)
    if plans is None:
        plans = plan_destinations(folder, [(name, target) for target in formats])
    with TemporaryDirectory(prefix="ebook-studio-") as work:
        native = Path(work) / "book.epub"
        write_epub(book, native, mobile=mobile)
        run_epubcheck(native, required=False)
        comics = comic_pages(book) if set(formats) & {"pdf", "cbz"} else []
        comic_source = Path(work) / "comic.cbz"
        if comics and "pdf" in formats:
            conversion.write_cbz(comics, comic_source)
        for index, plan in enumerate(plans):
            destination, output_type = plan.destination, plan.format_name
            if progress:
                progress(index, len(plans), f"正在生成 {LABELS.get(output_type, output_type)}")
            try:
                if output_type == "epub":
                    with publish_file(destination, overwrite=plan.replace_existing) as temporary:
                        shutil.copyfile(native, temporary)
                elif output_type == "cbz":
                    conversion.write_cbz(comics, destination, overwrite=plan.replace_existing)
                elif output_type == "pdf" and comics:
                    conversion.convert(comic_source, destination, overwrite=plan.replace_existing)
                else:
                    convert_to(native, destination, output_type, overwrite=plan.replace_existing)
                outputs.append(destination)
            except Exception as exc:
                errors.append(f"{destination.name} · {exc}")
    return outputs, errors


def convert_to(source, destination, format_name, *, overwrite=True):
    if format_name == "epub2":
        conversion.convert(source, destination, epub_version=2, overwrite=overwrite)
    else:
        conversion.convert(source, destination, overwrite=overwrite)


def read_book(path: Path) -> Book:
    suffix = path.suffix.lower()
    if suffix == ".bookproj":
        return Book.load(path)
    if suffix == ".epub" and not path.name.lower().endswith(".kepub.epub"):
        return open_epub(path)
    if suffix in (".cbz", ".cbr"):
        book = Book(title=path.stem)
        for index, (name, data) in enumerate(conversion.read_comic(path), 1):
            suffix = Path(name).suffix.lower()
            key = f"images/page-{index:05}{suffix}"
            mime = mimetypes.guess_type(name)[0] or "image/jpeg"
            book.assets[key] = Asset(data, mime)
            book.chapters.append(Chapter(f"第 {index} 页", f'<p><img src="{key}" alt="第 {index} 页"></p>'))
        book.cover = next(iter(book.assets))
        return book
    with TemporaryDirectory(prefix="ebook-studio-") as work:
        intermediate = Path(work) / "draft.epub"
        conversion.convert(path, intermediate)
        return open_epub(intermediate)


def capture_pages(urls, rendered, *, progress=None, article_xpath=''):
    book, failures = Book(), []
    for index, url in enumerate(urls):
        if progress:
            progress(index, len(urls), f"正在采集第 {index + 1} 篇")
        try:
            warnings = []
            chapter, assets = capture.capture(url, rendered=rendered, warnings=warnings, article_xpath=article_xpath)
            book.chapters.append(chapter)
            book.assets.update(assets)
            failures.extend(f"{chapter.title} · {item}" for item in warnings)
        except Exception as exc:
            failures.append(f"{url} · {exc}")
    if book.chapters:
        book.title = book.chapters[0].title
    return [book] if book.chapters else [], failures


def comic_pages(book):
    pages = []
    for chapter in book.chapters:
        document = html.fragment_fromstring(chapter.html or "<p></p>", create_parent=True)
        images = document.xpath(".//img[@src]")
        if len(images) != 1 or document.text_content().strip():
            return []
        key = resolve_href(chapter.path, images[0].get("src"))
        asset = book.assets.get(key)
        if asset is None or asset.media_type not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
            return []
        pages.append((key, asset.data))
    return pages

