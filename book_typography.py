"""Book stylesheet editing and editable-source font subsetting."""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import re
import unicodedata

from fontTools import subset
from fontTools.ttLib import TTFont, TTLibError
from lxml import etree
import tinycss2
from tinycss2.serializer import serialize_string_value

from ebook_core import Asset, Book, asset_href


DEFAULT_STYLE_PATH = 'styles/book.css'
FONT_TYPES = {'ttf': 'font/ttf', 'otf': 'font/otf', 'woff': 'font/woff', 'woff2': 'font/woff2'}
_STYLE_GROUPS = {'media', 'supports', 'layer', 'container', 'scope', 'starting-style', 'keyframes', '-webkit-keyframes'}
_TEXT_ATTRS = {'alt', 'title', 'aria-label', 'aria-description', 'label', 'placeholder', 'value'}
_GENERATED_TEXT = (
    ''.join(chr(cp) for cp in range(32, 127))
    + ''.join(chr(cp) for cp in range(0xff01, 0xff5f))
    + '　•◦▪·–—…−零〇一二三四五六七八九十百千万亿兆兩两壹贰叁肆伍陆柒捌玖拾佰仟萬億'
)


@dataclass(frozen=True)
class CssIssue:
    line: int
    column: int
    message: str


def _tokens(items):
    for item in items:
        yield item
        for name in ('content', 'arguments', 'prelude'):
            children = getattr(item, name, None)
            if children:
                yield from _tokens(children)


def css_issues(text: str) -> list[CssIssue]:
    errors = {}

    def rules(items):
        for item in items:
            if item.type == 'error':
                errors[item.source_line, item.source_column] = item.message
            elif item.type in {'at-rule', 'qualified-rule'} and item.content is not None:
                if getattr(item, 'lower_at_keyword', '') in _STYLE_GROUPS:
                    rules(tinycss2.parse_rule_list(item.content, skip_whitespace=True, skip_comments=True))
                else:
                    rules(tinycss2.parse_blocks_contents(item.content, skip_whitespace=True, skip_comments=True))

    stylesheet = tinycss2.parse_stylesheet(text, skip_whitespace=True, skip_comments=True)
    for item in _tokens(stylesheet):
        if item.type == 'error':
            errors[item.source_line, item.source_column] = item.message
    rules(stylesheet)
    return [CssIssue(line, column, message) for (line, column), message in sorted(errors.items())]


def decode_stylesheet(data: bytes) -> str:
    _, encoding = tinycss2.parse_stylesheet_bytes(data)
    css = encoding.codec_info.decode(data, 'replace')[0].removeprefix('\ufeff')
    return re.sub(r'^@charset\s+"[^"]+";', '@charset "UTF-8";', css, count=1, flags=re.I)


def stylesheet_text(book: Book, path: str | None) -> str:
    return book.stylesheet if path is None else decode_stylesheet(book.assets[path].data)


def set_stylesheet(book: Book, path: str | None, text: str) -> None:
    if path is None:
        book.stylesheet = text
    else:
        book.assets[path] = Asset(text.encode('utf-8'), 'text/css')


@dataclass(frozen=True)
class FontInfo:
    family: str
    style: str
    format: str
    flags: int

    @property
    def subsettable(self) -> bool:
        return not self.flags & (0x0002 | 0x0004 | 0x0100 | 0x0200)


def _font_info(font: TTFont) -> FontInfo:
    if not (font.getBestCmap() and any(table in font for table in ('glyf', 'CFF ', 'CFF2'))):
        raise ValueError('字体没有可用于书稿的 Unicode 字形。')
    names = font['name'] if 'name' in font else None
    family = names.getBestFamilyName() if names is not None else None
    style = names.getBestSubFamilyName() if names is not None else None
    return FontInfo(family or '嵌入字体', style or '', font.flavor or ('otf' if font.sfntVersion == 'OTTO' else 'ttf'),
                    font['OS/2'].fsType if 'OS/2' in font else 0)


def font_info(data: bytes) -> FontInfo:
    try:
        with TTFont(BytesIO(data), lazy=True) as font:
            return _font_info(font)
    except (TTLibError, OSError, KeyError) as exc:
        raise ValueError('无法读取独立字体。请选择有效的 TTF、OTF、WOFF 或 WOFF2。') from exc


def bind_font(book: Book, data: bytes, stylesheet: str | None) -> tuple[str, FontInfo]:
    """Embed a user-authorized face and bind it to the selected stylesheet."""
    info = font_info(data)
    if info.flags & (0x0002 | 0x0004 | 0x0200):
        raise ValueError('字体的嵌入标记不允许它用于可编辑书稿。')
    digest = sha256(data).hexdigest()
    path = f'fonts/embedded-{digest}.{info.format}'
    family = f'"{serialize_string_value(info.family)} · {digest[:8]}"'
    address = asset_href(path, stylesheet or DEFAULT_STYLE_PATH)
    rule = f'@font-face {{\n  font-family: {family};\n  src: url("{address}");\n}}\nbody {{ font-family: {family}, serif; }}'
    css = stylesheet_text(book, stylesheet)
    book.assets[path] = Asset(data, FONT_TYPES[info.format])
    if rule not in css:
        set_stylesheet(book, stylesheet, f'{css.rstrip()}\n\n{rule}\n'.lstrip())
    return path, info


def add_linked_stylesheet(book: Book) -> str:
    """Register a stylesheet shared by both imported and new chapters."""
    from epub_io import _unique

    occupied = set(book.assets) | set(book._epub_members) | {chapter.path for chapter in book.chapters}
    path = _unique('styles/typography.css', occupied)
    book.assets[path] = Asset(b'', 'text/css')
    book.linked_stylesheets.append(path)
    return path


def _css_strings(stylesheet: str):
    for item in _tokens(tinycss2.parse_component_value_list(stylesheet)):
        if item.type in {'string', 'ident'}:
            yield item.value


def book_characters(book: Book, documents=()) -> set[int]:
    """Collect visible text and common generated characters across the entire book."""
    from epub_io import XHTML_MEDIA, current_toc, editable_chapter_document

    text = [book.title, book.author, _GENERATED_TEXT, *[chapter.title for chapter in book.chapters]]
    if not documents:
        text.extend(entry['title'] for entry in current_toc(book))
        documents = [editable_chapter_document(book, chapter) for chapter in book.chapters]
    else:
        documents = list(documents)
    for name, asset in book.assets.items():
        if asset.media_type == 'text/css':
            text.extend(_css_strings(decode_stylesheet(asset.data)))
        elif asset.media_type in {XHTML_MEDIA, 'image/svg+xml'}:
            documents.append(asset.data)
    text.extend(_css_strings(book.stylesheet))
    for document in documents:
        if isinstance(document, bytes):
            document = etree.fromstring(document, parser=etree.XMLParser(resolve_entities=False, no_network=True))
        for node in document.iter():
            if not isinstance(node.tag, str):
                continue
            if node.text:
                text.append(node.text)
                if etree.QName(node).localname == 'style':
                    text.extend(_css_strings(node.text))
            if node.tail:
                text.append(node.tail)
            for name, value in node.attrib.items():
                local = etree.QName(name).localname
                if local in _TEXT_ATTRS:
                    text.append(value)
                elif local == 'style':
                    text.extend(_css_strings(value))
    content = ''.join(text)
    composed = unicodedata.normalize('NFC', content)
    decomposed = unicodedata.normalize('NFD', content)
    return set(map(ord, content + composed + decomposed + content.upper() + content.lower() + content.title()))


def subset_font(data: bytes, characters: set[int]) -> bytes:
    try:
        with TTFont(BytesIO(data), recalcTimestamp=False) as font:
            info = _font_info(font)
            if not info.subsettable:
                raise ValueError('此字体的嵌入标记不允许子集化。')
            retained = set(font.getBestCmap()) & characters
            if not retained:
                raise ValueError('此字体没有可保留的书稿字符。')
            options = subset.Options()
            options.layout_features = ['*']
            options.name_IDs = ['*']
            options.name_languages = ['*']
            options.name_legacy = True
            subsetter = subset.Subsetter(options=options)
            subsetter.populate(unicodes=retained)
            subsetter.subset(font)
            if not retained <= set(font.getBestCmap()):
                raise ValueError('子集字形覆盖不完整。')
            output = BytesIO()
            font.save(output)
            result = output.getvalue()
            return result if len(result) < len(data) else data
    except (TTLibError, OSError, KeyError) as exc:
        raise ValueError('无法生成此字体的子集。') from exc


def subset_assets(book: Book, documents) -> dict[str, Asset]:
    if not book.font_subsets:
        return {}
    characters = book_characters(book, documents)
    result = {}
    for path in book.font_subsets:
        asset = book.assets.get(path)
        if asset is not None:
            try:
                result[path] = Asset(subset_font(asset.data, characters), asset.media_type)
            except ValueError as exc:
                raise ValueError(f'字体 {path}：{exc} 请在「字体」中关闭此字体的导出子集。') from exc
    return result
