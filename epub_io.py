"""Read EPUB 2/3 projects and export reflowable EPUB 3 books."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile
import re

from lxml import etree, html
from PIL import Image, ImageOps, UnidentifiedImageError

from ebook_core import Asset, Book, Chapter, archive_path, asset_href, publish_file, resolve_href

OPF = 'http://www.idpf.org/2007/opf'
XHTML = 'http://www.w3.org/1999/xhtml'
EPUB = 'http://www.idpf.org/2007/ops'
XML = 'http://www.w3.org/XML/1998/namespace'
DC = 'http://purl.org/dc/elements/1.1/'
CONTAINER = 'urn:oasis:names:tc:opendocument:xmlns:container'
NCX = 'http://www.daisy.org/z3986/2005/ncx/'
MIMETYPE = b'application/epub+zip'
XHTML_MEDIA = 'application/xhtml+xml'
NCX_MEDIA = 'application/x-dtbncx+xml'
CSS = b'''body { line-height:1.75; }
p { margin:0.65em 0; text-indent:2em; }
img, svg { max-width:100%; height:auto; }
figure { text-align:center; margin:1em auto; }
table { border-collapse:collapse; }
td, th { border:1px solid; padding:0.25em; }
pre { white-space:pre-wrap; }
.cover { text-align:center; }
'''


def _xml(data: bytes) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    return etree.fromstring(data, parser=parser)


def _bytes(element: etree._Element | etree._ElementTree) -> bytes:
    return etree.tostring(element, encoding='UTF-8', xml_declaration=True)


def _put(parent: etree._Element, tag: str, text: str | None = None, **attrs) -> etree._Element:
    result = etree.SubElement(parent, tag, **attrs)
    result.text = text
    return result


def _digest(text: str) -> str:
    return sha256(text.encode('utf-8')).hexdigest()


def _inner(element: etree._Element) -> str:
    return escape(element.text or '', quote=False) + ''.join(
        etree.tostring(child, encoding='unicode') for child in element
    )


def chapter_html(document: str) -> str:
    """Return the body of a complete XHTML document, or a fragment unchanged."""
    if re.search(r'<(?:[\w.-]+:)?html\b', document, re.IGNORECASE):
        body = _xml(document.encode('utf-8')).find(f'{{{XHTML}}}body')
        if body is None:
            raise ValueError('XHTML 文档缺少 body')
        return _inner(body)
    return document


def _manifest(package: etree._Element, package_path: str) -> dict[str, tuple[str, etree._Element]]:
    output = {}
    for item in package.findall(f'{{{OPF}}}manifest/{{{OPF}}}item'):
        if (path := resolve_href(package_path, item.get('href', ''))) is not None:
            output[item.attrib['id']] = (path, item)
    return output


def _has_property(node: etree._Element, name: str) -> bool:
    return name in node.get('properties', '').split()


def _html_toc(doc: etree._Element) -> etree._Element | None:
    return next((nav for nav in doc.iter(f'{{{XHTML}}}nav')
                 if 'toc' in nav.get(f'{{{EPUB}}}type', '').split()), None)


def _navigation(path: str, data: bytes, ncx: bool) -> dict[str, tuple[str, int]]:
    labels = {}
    root = _xml(data)
    if ncx:
        def read_points(parent, level):
            for point in parent.findall(f'{{{NCX}}}navPoint'):
                address = point.find(f'{{{NCX}}}content')
                if address is not None:
                    file = resolve_href(path, address.get('src', ''))
                    title = point.findtext(f'{{{NCX}}}navLabel/{{{NCX}}}text', '').strip()
                    if file and title:
                        labels.setdefault(file, (title, level))
                read_points(point, level + 1)
        nav_map = root.find(f'{{{NCX}}}navMap')
        if nav_map is not None:
            read_points(nav_map, 1)
    else:
        def read_list(parent, level):
            for ordered in parent.findall(f'{{{XHTML}}}ol'):
                for item in ordered.findall(f'{{{XHTML}}}li'):
                    anchor = item.find(f'{{{XHTML}}}a')
                    if anchor is not None:
                        file = resolve_href(path, anchor.get('href', ''))
                        title = ' '.join(''.join(anchor.itertext()).split())
                        if file and title:
                            labels.setdefault(file, (title, level))
                    read_list(item, level + 1)
        nav = _html_toc(root)
        if nav is not None:
            read_list(nav, 1)
    return labels


def _rename_navigation(path: str, data: bytes, titles: dict[str, tuple[str, str]], *, ncx=False) -> bytes:
    """Keep navigation markup and rename matching chapter links without a fragment."""
    if not titles:
        return data
    doc = _xml(data)
    if ncx:
        entries = ((point.find(f'{{{NCX}}}content'), point.find(f'{{{NCX}}}navLabel/{{{NCX}}}text'), 'src')
                   for point in doc.iter(f'{{{NCX}}}navPoint'))
    else:
        toc = _html_toc(doc)
        if toc is None:
            return data
        entries = ((link, link, 'href') for link in toc.findall(f'.//{{{XHTML}}}li/{{{XHTML}}}a'))
    pending = dict(titles)
    for address, label, attribute in entries:
        if address is None or label is None or not (href := address.get(attribute)) or urlsplit(href).fragment:
            continue
        target = resolve_href(path, href)
        if target not in pending:
            continue
        before, after = pending[target]
        if ' '.join(''.join(label.itertext()).split()) != ' '.join(before.split()):
            continue
        for index, part in enumerate(label.xpath('.//text()[normalize-space()]')):
            owner = part.getparent()
            if part.is_tail:
                owner.tail = after if index == 0 else ''
            else:
                owner.text = after if index == 0 else ''
        del pending[target]
    return _bytes(doc.getroottree()) if len(pending) != len(titles) else data


def open_epub(path: str | Path) -> Book:
    with ZipFile(path) as archive:
        names = [archive_path(info.filename) for info in archive.infolist() if not info.is_dir()]
        if len(names) != len(set(names)):
            raise ValueError('EPUB 含有重复的文件路径')
        if archive.read('mimetype') != MIMETYPE:
            raise ValueError('文件缺少有效的 EPUB 媒体类型')
        encryption_path = 'META-INF/encryption.xml'
        if encryption_path in names:
            encryption = _xml(archive.read(encryption_path))
            font_algorithms = {'http://www.idpf.org/2008/embedding', 'http://ns.adobe.com/pdf/enc#RC'}
            for method in encryption.iter('{http://www.w3.org/2001/04/xmlenc#}EncryptionMethod'):
                if method.get('Algorithm') not in font_algorithms:
                    raise ValueError('此 EPUB 含有受保护资源，当前支持无 DRM 的 EPUB')
        container = _xml(archive.read('META-INF/container.xml'))
        package_path = container.find(f'.//{{{CONTAINER}}}rootfile')
        if package_path is None:
            raise ValueError('EPUB 缺少书籍包文件')
        opf_path = archive_path(package_path.attrib['full-path'])
        package_data = archive.read(opf_path)
        package = _xml(package_data)
        metadata = package.find(f'{{{OPF}}}metadata')
        spine = package.find(f'{{{OPF}}}spine')
        if metadata is None or spine is None:
            raise ValueError('EPUB 缺少元数据或阅读顺序')
        manifest = _manifest(package, opf_path)
        originals = {opf_path: package_data}
        epub_nav_path = next((name for name, item in manifest.values() if _has_property(item, 'nav')), None)
        legacy_nav = manifest.get(spine.get('toc', '')) or next(
            ((name, item) for name, item in manifest.values() if item.get('media-type') == NCX_MEDIA),
            None,
        )
        label_map = {}
        if epub_nav_path:
            originals[epub_nav_path] = archive.read(epub_nav_path)
            label_map = _navigation(epub_nav_path, originals[epub_nav_path], False)
        if legacy_nav:
            ncx_path, _ = legacy_nav
            originals[ncx_path] = archive.read(ncx_path)
            for name, label in _navigation(ncx_path, originals[ncx_path], True).items():
                label_map.setdefault(name, label)
        book = Book(
            title=metadata.findtext(f'{{{DC}}}title') or Path(path).stem,
            author=metadata.findtext(f'{{{DC}}}creator', ''),
            language=metadata.findtext(f'{{{DC}}}language') or 'zh-CN',
            _epub_path=opf_path,
            _epub_members=originals,
        )
        identifiers = metadata.findall(f'{{{DC}}}identifier')
        uid = next((item.text for item in identifiers if item.get('id') == package.get('unique-identifier')), None)
        if uid:
            book.identifier = uid
        for reference in spine.findall(f'{{{OPF}}}itemref'):
            name, item = manifest[reference.attrib['idref']]
            if name == epub_nav_path or name in book._epub_chapters:
                continue
            if item.get('media-type') != XHTML_MEDIA:
                raise ValueError(f'阅读顺序中的 {name} 无法作为可重排章节编辑')
            data = archive.read(name)
            document = _xml(data)
            body = document.find(f'{{{XHTML}}}body')
            if body is None:
                raise ValueError(f'章节缺少 XHTML body：{name}')
            content = _inner(body)
            title, level = label_map.get(name, (
                (document.findtext(f'{{{XHTML}}}head/{{{XHTML}}}title') or Path(name).stem).strip(),
                1,
            ))
            book.chapters.append(Chapter(title, content, level, path=name))
            book._epub_members[name] = data
            book._epub_chapters[name] = (title, level, _digest(content))
        for name, item in manifest.values():
            if name not in originals:
                book.assets[name] = Asset(archive.read(name), item.get('media-type', 'application/octet-stream'))
                if _has_property(item, 'cover-image'):
                    book.cover = name
        if not book.cover:
            cover_id = next((element.get('content') for element in metadata.findall(f'{{{OPF}}}meta')
                             if element.get('name') == 'cover'), None)
            if cover_id in manifest and manifest[cover_id][0] in book.assets:
                book.cover = manifest[cover_id][0]
        for name in names:
            if name.startswith('META-INF/') and name not in {'META-INF/container.xml', 'META-INF/signatures.xml'}:
                book._epub_members[name] = archive.read(name)
        if not book.chapters:
            raise ValueError('EPUB 中没有可编辑的 XHTML 章节')
        return book


def _document(title: str, language: str) -> tuple[etree._Element, etree._Element]:
    doc = etree.Element(f'{{{XHTML}}}html', nsmap={None: XHTML, 'epub': EPUB})
    doc.set('lang', language)
    doc.set(f'{{{XML}}}lang', language)
    head = _put(doc, f'{{{XHTML}}}head')
    _put(head, f'{{{XHTML}}}title', title)
    return doc, _put(doc, f'{{{XHTML}}}body')


def _set_body(body: etree._Element, fragment: str) -> None:
    body.text = None
    for element in list(body):
        body.remove(element)
    fragment = chapter_html(fragment)
    try:
        wrapped = (f'<body xmlns="{XHTML}" xmlns:epub="{EPUB}">'
                   f'{fragment}</body>').encode('utf-8')
        parsed = _xml(wrapped)
        body.text = parsed.text
        body.extend(parsed)
    except etree.XMLSyntaxError:
        for part in html.fragments_fromstring(fragment):
            if isinstance(part, str):
                body.text = (body.text or '') + part
            else:
                for node in part.iter():
                    if isinstance(node.tag, str) and not node.tag.startswith('{'):
                        node.tag = f'{{{XHTML}}}{node.tag}'
                body.append(part)


def _write_chapter(book: Book, chapter: Chapter, css: str) -> bytes:
    baseline = book._epub_chapters.get(chapter.path)
    if baseline:
        original = book._epub_members[chapter.path]
        body_changed = _digest(chapter.html) != baseline[2]
        if chapter.title == baseline[0] and not body_changed:
            return original
        doc = _xml(original)
        title = doc.find(f'{{{XHTML}}}head/{{{XHTML}}}title')
        if title is not None and chapter.title != baseline[0]:
            title.text = chapter.title
        if body_changed:
            _set_body(doc.find(f'{{{XHTML}}}body'), chapter.html)
    else:
        doc, body = _document(chapter.title, book.language)
        head = doc.find(f'{{{XHTML}}}head')
        _put(head, f'{{{XHTML}}}link', rel='stylesheet', href=asset_href(css, chapter.path))
        _set_body(_put(body, f'{{{XHTML}}}main'), chapter.html)
        if chapter.source.startswith(('https://', 'http://')):
            footer = _put(body, f'{{{XHTML}}}footer')
            paragraph = _put(footer, f'{{{XHTML}}}p')
            _put(paragraph, f'{{{XHTML}}}a', '原网页', href=chapter.source)
    return _bytes(doc)


def _nav_tree(chapters: Iterable[Chapter]) -> list:
    root = []
    stack = [root]
    for chapter in chapters:
        level = min(max(1, chapter.level), len(stack))
        stack = stack[:level]
        children = []
        stack[-1].append((chapter, children))
        stack.append(children)
    return root


def _write_nav(title: str, language: str, path: str, tree: list,
               previous: bytes | None = None) -> bytes:
    if previous:
        doc = _xml(previous)
        body = doc.find(f'{{{XHTML}}}body')
        toc = _html_toc(doc)
    else:
        doc, body = _document(title, language)
        toc = None
    new_toc = etree.Element(f'{{{XHTML}}}nav')
    new_toc.set(f'{{{EPUB}}}type', 'toc')
    new_toc.set('id', toc.get('id', 'toc') if toc is not None else 'toc')
    _put(new_toc, f'{{{XHTML}}}h1', '目录')
    def add(parent, group):
        for chapter, children in group:
            item = _put(parent, f'{{{XHTML}}}li')
            _put(item, f'{{{XHTML}}}a', chapter.title, href=asset_href(chapter.path, path))
            if children:
                add(_put(item, f'{{{XHTML}}}ol'), children)
    add(_put(new_toc, f'{{{XHTML}}}ol'), tree)
    if toc is not None:
        toc.getparent().replace(toc, new_toc)
    else:
        # Early Qiushi EPUBs stored a plain list in the navigation document.
        # Rebuild that document so the same chapters don't appear twice.
        if previous is not None:
            doc, body = _document(title, language)
        body.append(new_toc)
    return _bytes(doc)


def _nav_from_ncx(book: Book, path: str, ncx_path: str, data: bytes) -> bytes:
    doc, body = _document(book.title, book.language)
    toc = _put(body, f'{{{XHTML}}}nav', id='toc')
    toc.set(f'{{{EPUB}}}type', 'toc')
    _put(toc, f'{{{XHTML}}}h1', '目录')

    def add_list(parent, source):
        for point in source.findall(f'{{{NCX}}}navPoint'):
            label = point.findtext(f'{{{NCX}}}navLabel/{{{NCX}}}text', '').strip()
            address = point.find(f'{{{NCX}}}content')
            if not label or address is None or not (href := address.get('src')):
                continue
            if target := resolve_href(ncx_path, href):
                url = urlsplit(href)
                href = urlunsplit(('', '', asset_href(target, path), url.query, url.fragment))
            item = _put(parent, f'{{{XHTML}}}li')
            _put(item, f'{{{XHTML}}}a', label, href=href)
            if point.find(f'{{{NCX}}}navPoint') is not None:
                add_list(_put(item, f'{{{XHTML}}}ol'), point)

    add_list(_put(toc, f'{{{XHTML}}}ol'), _xml(data).find(f'{{{NCX}}}navMap'))
    return _bytes(doc)


def _write_ncx(book: Book, path: str, tree: list) -> bytes:
    doc = etree.Element(f'{{{NCX}}}ncx', nsmap={None: NCX}, version='2005-1')
    doc.set(f'{{{XML}}}lang', book.language)
    head = _put(doc, f'{{{NCX}}}head')
    def height(group):
        return max((1 + height(children) for _, children in group), default=0)
    for key, value in {
        'dtb:uid': book.identifier, 'dtb:depth': str(height(tree)),
        'dtb:totalPageCount': '0', 'dtb:maxPageNumber': '0',
    }.items():
        _put(head, f'{{{NCX}}}meta', name=key, content=value)
    _put(_put(doc, f'{{{NCX}}}docTitle'), f'{{{NCX}}}text', book.title)
    order = 0
    def add(parent, group):
        nonlocal order
        for chapter, children in group:
            order += 1
            point = _put(parent, f'{{{NCX}}}navPoint', id=f'chapter-{order}', playOrder=str(order))
            _put(_put(point, f'{{{NCX}}}navLabel'), f'{{{NCX}}}text', chapter.title)
            _put(point, f'{{{NCX}}}content', src=asset_href(chapter.path, path))
            add(point, children)
    add(_put(doc, f'{{{NCX}}}navMap'), tree)
    return _bytes(doc)


def _mobile_image(asset: Asset) -> bytes:
    if asset.media_type not in {'image/jpeg', 'image/png'}:
        return asset.data
    try:
        with Image.open(BytesIO(asset.data)) as original, BytesIO() as output:
            if getattr(original, 'is_animated', False):
                return asset.data
            if asset.media_type == 'image/png':
                original.save(output, format='PNG', optimize=True, **{
                    key: original.info[key] for key in ('icc_profile', 'transparency')
                    if key in original.info
                })
            else:
                image = ImageOps.exif_transpose(original).convert('RGB')
                image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                color = {'icc_profile': original.info['icc_profile']} if 'icc_profile' in original.info else {}
                image.save(output, format='JPEG', quality=88, optimize=True, progressive=True, **color)
            result = output.getvalue()
            return result if len(result) < len(asset.data) else asset.data
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return asset.data


def _unique(path: str, occupied: set[str]) -> str:
    candidate = path
    n = 1
    while candidate in occupied:
        stem, _, suffix = path.rpartition('.')
        n += 1
        candidate = f'{stem}-{n}.{suffix}'
    occupied.add(candidate)
    return candidate


def _package(book: Book) -> tuple[etree._Element, etree._Element,
                                  dict[str, tuple[str, etree._Element]]]:
    if book._epub_path:
        package = _xml(book._epub_members[book._epub_path])
        previous = _manifest(package, book._epub_path)
    else:
        package = etree.Element(f'{{{OPF}}}package', nsmap={None: OPF, 'dc': DC})
        previous = {}
    epub2 = package.get('version', '').startswith('2')
    package.set('version', '3.0')
    package.attrib.pop(f'{{{XML}}}base', None)
    metadata = package.find(f'{{{OPF}}}metadata')
    if metadata is None:
        metadata = _put(package, f'{{{OPF}}}metadata')
    if epub2:
        for node in list(metadata):
            if node.tag == f'{{{OPF}}}meta' and node.get('name') != 'cover':
                metadata.remove(node)
                continue
            for key in list(node.attrib):
                if key.startswith(f'{{{OPF}}}'):
                    del node.attrib[key]
    for name, value in [('title', book.title), ('language', book.language), ('creator', book.author)]:
        element = metadata.find(f'{{{DC}}}{name}')
        if element is None and value:
            _put(metadata, f'{{{DC}}}{name}', value)
        elif element is not None:
            if value:
                element.text = value
            else:
                removed_id = element.get('id')
                metadata.remove(element)
                if removed_id:
                    for refinement in list(metadata):
                        if refinement.get('refines') == f'#{removed_id}':
                            metadata.remove(refinement)
    unique = package.get('unique-identifier') or 'book-id'
    package.set('unique-identifier', unique)
    identifiers = metadata.findall(f'{{{DC}}}identifier')
    primary = next((element for element in identifiers if element.get('id') == unique), None)
    if primary is None:
        primary = _put(metadata, f'{{{DC}}}identifier', id=unique)
    primary.text = book.identifier
    for element in metadata.findall(f'{{{OPF}}}meta'):
        if element.get('property') == 'dcterms:modified' or element.get('name') == 'cover':
            metadata.remove(element)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    _put(metadata, f'{{{OPF}}}meta', now, property='dcterms:modified')
    return package, metadata, previous


def write_epub(book: Book, path: str | Path, *, mobile: bool = True) -> None:
    if not book.chapters:
        raise ValueError('请至少添加一个章节')
    if not book.title.strip() or not book.language.strip():
        raise ValueError('请填写书名和语言')
    assets = {archive_path(name): asset for name, asset in book.assets.items()}
    names = [archive_path(chapter.path) for chapter in book.chapters]
    if len(names) != len(set(names)) or set(names) & assets.keys():
        raise ValueError('章节和资源必须使用不同的书内路径')
    if any(name == 'mimetype' or name.startswith('META-INF/') for name in [*names, *assets]):
        raise ValueError('章节和资源不能占用 EPUB 保留路径')
    occupied = set(names) | assets.keys() | set(book._epub_members)
    opf_path = book._epub_path or _unique('package.opf', occupied)
    package, metadata, previous = _package(book)
    previous_spine = package.find(f'{{{OPF}}}spine')
    old_refs = {} if previous_spine is None else {
        item.get('idref'): dict(item.attrib) for item in previous_spine.findall(f'{{{OPF}}}itemref')
    }
    original_spine = dict.fromkeys(previous[id_][0] for id_ in old_refs
                                   if id_ in previous and previous[id_][0] in book._epub_chapters)
    old_nav = next((p for p, item in previous.values() if _has_property(item, 'nav')), None)
    old_ncx = next((p for p, item in previous.values() if item.get('media-type') == NCX_MEDIA), None)
    if {opf_path, old_nav, old_ncx} & (set(names) | assets.keys()):
        raise ValueError('章节和资源不能占用包文件或导航文件的路径')
    nav_path = old_nav or _unique('nav.xhtml', occupied)
    css_path = None
    if any(chapter.path not in book._epub_chapters for chapter in book.chapters):
        css_path = _unique('styles/book.css', occupied)
        assets[css_path] = Asset(CSS + b'\n' + book.stylesheet.encode('utf-8'), 'text/css')
    tree = _nav_tree(book.chapters)
    unchanged_toc_structure = [(c.path, c.level) for c in book.chapters] == [
        (name, book._epub_chapters[name][1]) for name in original_spine
    ]
    renamed = {c.path: (book._epub_chapters[c.path][0], c.title) for c in book.chapters
               if unchanged_toc_structure and c.title != book._epub_chapters[c.path][0]}
    documents = {chapter.path: _write_chapter(book, chapter, css_path) for chapter in book.chapters}
    original_nav = book._epub_members.get(old_nav)
    original_ncx = book._epub_members.get(old_ncx)
    if old_nav and unchanged_toc_structure and _navigation(old_nav, original_nav, False):
        documents[nav_path] = _rename_navigation(nav_path, original_nav, renamed)
        if old_ncx:
            documents[old_ncx] = _rename_navigation(old_ncx, original_ncx, renamed, ncx=True)
    elif old_ncx and unchanged_toc_structure and _navigation(old_ncx, original_ncx, True):
        documents[old_ncx] = _rename_navigation(old_ncx, original_ncx, renamed, ncx=True)
        documents[nav_path] = _nav_from_ncx(book, nav_path, old_ncx, documents[old_ncx])
    else:
        documents[nav_path] = _write_nav(book.title, book.language, nav_path, tree, original_nav)
        if old_ncx:
            documents[old_ncx] = _write_ncx(book, old_ncx, tree)

    cover_page = None
    if book.cover and book.cover not in assets:
        raise ValueError('找不到封面资源')
    original_cover = next((name for name, item in previous.values() if _has_property(item, 'cover-image')), None)
    if original_cover is None:
        old_package = book._epub_members.get(book._epub_path)
        if old_package:
            old_cover = _xml(old_package).find(f'{{{OPF}}}metadata/{{{OPF}}}meta[@name="cover"]')
            if old_cover is not None and old_cover.get('content') in previous:
                original_cover = previous[old_cover.get('content')][0]
    replaced_cover = False
    if book.cover and original_cover and book.cover != original_cover:
        # If a book has a rendered cover page, update its image as well as
        # the manifest thumbnail when the user changes the cover.
        for name in names:
            chapter = _xml(documents[name])
            image = next((node for node in chapter.iter(f'{{{XHTML}}}img')
                          if node.get('src') and resolve_href(name, node.get('src')) == original_cover), None)
            if image is not None:
                image.set('src', asset_href(book.cover, name))
                documents[name] = _bytes(chapter)
                replaced_cover = True
                break
    if book.cover and (not original_cover or (book.cover != original_cover and not replaced_cover)):
        cover_page = _unique('cover.xhtml', occupied)
        doc, body = _document('封面', book.language)
        body.set('class', 'cover')
        if css_path:
            head = doc.find(f'{{{XHTML}}}head')
            _put(head, f'{{{XHTML}}}link', rel='stylesheet', href=asset_href(css_path, cover_page))
        _put(body, f'{{{XHTML}}}img', src=asset_href(book.cover, cover_page), alt=book.title)
        documents[cover_page] = _bytes(doc)

    for name in ('manifest', 'spine', 'guide', 'bindings'):
        element = package.find(f'{{{OPF}}}{name}')
        if element is not None:
            package.remove(element)
    manifest = _put(package, f'{{{OPF}}}manifest')
    spine = _put(package, f'{{{OPF}}}spine')
    previous_by_path = {name: (id_, item) for id_, (name, item) in previous.items()}
    reserved_ids = set(previous)
    ids = {}
    for index, (name, media) in enumerate(
        [(name, NCX_MEDIA if name == old_ncx else XHTML_MEDIA) for name in documents]
        + [(name, asset.media_type) for name, asset in assets.items()], start=1
    ):
        old_id, old = previous_by_path.get(name, (None, None))
        identifier = old_id or f'item-{index}'
        while not old_id and identifier in reserved_ids:
            identifier += '-new'
        ids[name] = identifier
        reserved_ids.add(identifier)
        attr = dict(old.attrib) if old is not None else {}
        properties = set(attr.get('properties', '').split()) - {'cover-image'}
        if name == nav_path:
            properties.add('nav')
        if name == book.cover:
            properties.add('cover-image')
        if name in documents and media == XHTML_MEDIA:
            page = _xml(documents[name])
            for uri, prop in [('http://www.w3.org/2000/svg', 'svg'), ('http://www.w3.org/1998/Math/MathML', 'mathml')]:
                if next(page.iterfind(f'.//{{{uri}}}*'), None) is not None:
                    properties.add(prop)
        if properties:
            attr['properties'] = ' '.join(sorted(properties))
        else:
            attr.pop('properties', None)
        attr.update(id=identifier, href=asset_href(name, opf_path), **{'media-type': media})
        _put(manifest, f'{{{OPF}}}item', **attr)
    retained_ids = set(ids.values())
    for item in manifest:
        for key in ('fallback', 'media-overlay'):
            if item.get(key) and item.get(key) not in retained_ids:
                del item.attrib[key]
    if old_ncx:
        spine.set('toc', ids[old_ncx])
    if cover_page:
        _put(spine, f'{{{OPF}}}itemref', idref=ids[cover_page])
    for chapter in book.chapters:
        id_ = ids[chapter.path]
        _put(spine, f'{{{OPF}}}itemref', **old_refs.get(id_, {'idref': id_}))
    if old_nav and ids[old_nav] in old_refs:
        _put(spine, f'{{{OPF}}}itemref', **old_refs[ids[old_nav]])
    # The package schema requires collections after the spine.
    for collection in package.findall(f'{{{OPF}}}collection'):
        package.append(collection)

    container = etree.Element(f'{{{CONTAINER}}}container', nsmap={None: CONTAINER}, version='1.0')
    _put(_put(container, f'{{{CONTAINER}}}rootfiles'), f'{{{CONTAINER}}}rootfile',
         **{'full-path': opf_path, 'media-type': 'application/oebps-package+xml'})
    fixed_layout = (metadata.findtext(f'{{{OPF}}}meta[@property="rendition:layout"]', '').strip() == 'pre-paginated'
                    or any('rendition:layout-pre-paginated' in ref.get('properties', '').split()
                           for ref in old_refs.values()))
    with publish_file(path) as temporary, ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('mimetype', MIMETYPE, compress_type=ZIP_STORED)
        archive.writestr('META-INF/container.xml', _bytes(container))
        archive.writestr(opf_path, _bytes(package))
        for name, data in documents.items():
            archive.writestr(name, data)
        for name, asset in assets.items():
            payload = _mobile_image(asset) if mobile and not fixed_layout and name != book.cover else asset.data
            archive.writestr(name, payload)
        for name, data in book._epub_members.items():
            if name.startswith('META-INF/'):
                archive.writestr(name, data)
