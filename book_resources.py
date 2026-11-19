"""Electronic book resource references, inspection and same-format replacement."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath

from lxml import etree
from PIL import Image

from book_links import map_css_references, map_document_references
from ebook_core import Asset, Book, resolve_href
from epub_io import XHTML_MEDIA, _xml, editable_chapter_document, is_fixed_layout, original_navigation_paths


IMAGE_FORMATS = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/gif': 'GIF', 'image/webp': 'WEBP'}
_FONT_SUFFIXES = {'.woff', '.woff2', '.otf', '.ttf', '.ttc'}


@dataclass(frozen=True)
class ResourceReference:
    label: str
    chapter: int | None = None


def resource_category(path: str, asset: Asset) -> str:
    if asset.media_type.startswith('image/'):
        return '图片'
    if asset.media_type == 'text/css':
        return '样式表'
    if asset.media_type.startswith('font/') or PurePosixPath(path).suffix.lower() in _FONT_SUFFIXES:
        return '字体'
    return '其他'


def resource_references(book: Book) -> dict[str, list[ResourceReference]]:
    """List discovered reference locations for each packaged resource."""
    references = defaultdict(dict)

    def visitor(base, site):
        def inspect(value):
            try:
                resource = resolve_href(base, value)
            except ValueError:
                return value
            if resource in book.assets:
                references[resource][site] = None
            return value
        return inspect

    for index, chapter in enumerate(book.chapters):
        try:
            doc = editable_chapter_document(book, chapter)
        except etree.LxmlError:
            continue
        site = ResourceReference(f'第 {index + 1} 章 · {chapter.title or "未命名章节"}', index)
        map_document_references(doc, visitor(chapter.path, site))
    for path, asset in book.assets.items():
        if asset.media_type == 'text/css':
            map_css_references(asset.data.decode('utf-8', errors='surrogateescape'), visitor(path, ResourceReference(path)))
        elif asset.media_type in {XHTML_MEDIA, 'image/svg+xml', 'application/smil+xml'}:
            try:
                map_document_references(_xml(asset.data), visitor(path, ResourceReference(path)))
            except etree.LxmlError:
                continue
    for path in (name for name in original_navigation_paths(book) if name is not None):
        map_document_references(_xml(book._epub_members[path]), visitor(path, ResourceReference(path)))
    if book.stylesheet:
        map_css_references(book.stylesheet, visitor('styles/book.css', ResourceReference('新建章节样式')))
    if book.cover in book.assets:
        references[book.cover][ResourceReference('书籍封面')] = None
    return {path: list(sites) for path, sites in references.items()}


def can_replace(asset: Asset) -> bool:
    return asset.media_type in IMAGE_FORMATS or asset.media_type == 'text/css'


def replacement_asset(book: Book, path: str, data: bytes) -> Asset:
    current = book.assets[path]
    if not can_replace(current):
        raise ValueError('此类资源可检查属性，当前仅支持替换常见位图和 CSS。')
    if current.media_type == 'text/css':
        try:
            data.decode('utf-8-sig')
        except UnicodeDecodeError as error:
            raise ValueError('请选择 UTF-8 编码的 CSS 文件。') from error
        if b'\x00' in data:
            raise ValueError('请选择文本格式的 CSS 文件。')
    else:
        desired = IMAGE_FORMATS[current.media_type]
        try:
            with Image.open(BytesIO(data)) as image:
                actual, size = image.format, image.size
                image.verify()
        except (OSError, Image.DecompressionBombError) as error:
            raise ValueError('无法读取所选图片。') from error
        if actual != desired:
            raise ValueError(f'替换图片需保持 {desired} 格式。')
        if is_fixed_layout(book):
            with Image.open(BytesIO(current.data)) as original:
                if size != original.size:
                    raise ValueError('固定版式书籍的替换图片必须与原图尺寸相同。')
    return Asset(data, current.media_type)
