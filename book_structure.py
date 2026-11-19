"""Previewable table of contents edits and chapter restructuring."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit, urlunsplit
import re

from lxml import etree

from book_links import map_css_references, map_document_references
from ebook_core import Asset, Book, Chapter, asset_href, resolve_href
from epub_io import (
    OPF, XHTML, XHTML_MEDIA, XML, TocEntry, _bytes, _inner, _xml, chapter_toc, current_toc,
    editable_chapter_document, is_fixed_layout, original_navigation_paths, remember_chapter_document,
)


_BODY = f'{{{XHTML}}}body'
_HEAD = f'{{{XHTML}}}head'
_HEADING = re.compile(rf'^\{{{re.escape(XHTML)}\}}h([1-6])$')
_WRAPPERS = {f'{{{XHTML}}}{name}' for name in ('body', 'main', 'div', 'article', 'section')}
_ID_ATTRIBUTES = ('id', f'{{{XML}}}id')
_ID_REFERENCES = ('for', 'headers', 'aria-labelledby', 'aria-describedby', 'aria-controls')
_REF_DOCUMENTS = {XHTML_MEDIA, 'image/svg+xml', 'application/smil+xml'}


@dataclass(frozen=True)
class Heading:
    index: int
    title: str
    rank: int


@dataclass
class StructureEdit:
    book: Book
    chapter: int
    description: str
    relinked: int = 0
    toc_index: int | None = None


@dataclass(frozen=True)
class TocDestination:
    path: str
    label: str
    fragment: str = ''
    heading_index: int | None = None


def _headings(body):
    for node in body.iterdescendants():
        if isinstance(node.tag, str) and (match := _HEADING.fullmatch(node.tag)):
            title = ' '.join(''.join(node.itertext()).split())
            if title:
                yield node, title, int(match[1])


def _unique_name(base, occupied):
    name = base
    index = 2
    while name in occupied:
        name = f'{base}-{index}'
        index += 1
    return name


def toc_destinations(book: Book, chapter_index: int) -> list[TocDestination]:
    chapter = book.chapters[chapter_index]
    body = editable_chapter_document(book, chapter).find(_BODY)
    targets = [TocDestination(chapter.path, '章节开头')]
    for index, (node, title, rank) in enumerate(_headings(body), 1):
        anchor = node.get('id') or node.get(f'{{{XML}}}id') or ''
        targets.append(TocDestination(chapter.path, f'H{rank} · {title}', anchor, index if not anchor else None))
    return targets


def _editable_toc(book: Book) -> list[TocEntry]:
    entries = current_toc(book)
    previous = 0
    for entry in entries:
        entry['level'] = min(entry['level'], previous + 1)
        previous = entry['level']
    return entries


def edit_toc_entry(book: Book, index: int, title: str, target: TocDestination, chapter: int) -> StructureEdit:
    """Change an entry and create an anchor only if the chosen heading needs one."""
    title = title.strip()
    if not title:
        raise ValueError('请输入目录标题。')
    result = deepcopy(book)
    entries = _editable_toc(result)
    entry = entries[index]
    anchor = target.fragment
    if target.heading_index is not None:
        destination = next(ch for ch in result.chapters if ch.path == target.path)
        doc = editable_chapter_document(result, destination)
        body = doc.find(_BODY)
        heading, _, _ = next(item for i, item in enumerate(_headings(body), 1) if i == target.heading_index)
        occupied = {value for node in doc.iter() for attr in _ID_ATTRIBUTES if (value := node.get(attr))}
        occupied.update(name for node in doc.iter(f'{{{XHTML}}}a') if (name := node.get('name')))
        anchor = _unique_name(f'heading-{target.heading_index}', occupied)
        heading.set('id', anchor)
        destination.html = _inner(body)
    entry.update(title=title, path=target.path, fragment=anchor)
    result.toc_entries = entries
    preview = current_toc(result)
    selected = next(i for i, item in enumerate(preview) if item == entry)
    message = f'已编辑目录条目「{title}」。'
    if len(preview) > len(entries):
        message += '原章节保留默认目录条目。'
    return StructureEdit(result, chapter, message, toc_index=selected)


def _subtree_end(entries: list[TocEntry], index: int) -> int:
    level = entries[index]['level']
    return next((i for i in range(index + 1, len(entries)) if entries[i]['level'] <= level), len(entries))


def move_toc_entry(book: Book, index: int, direction: str, chapter: int) -> StructureEdit:
    """Move or renest a TOC branch, keeping its descendants attached."""
    entries = _editable_toc(book)
    level = entries[index]['level']
    end = _subtree_end(entries, index)
    previous = next((i for i in reversed(range(index)) if entries[i]['level'] <= level), None)
    has_previous_sibling = previous is not None and entries[previous]['level'] == level
    branch = entries[index:end]
    remainder = entries[:index] + entries[end:]
    position, difference = index, 0
    if direction == 'up' and has_previous_sibling:
        position = previous
    elif direction == 'down' and end < len(entries) and entries[end]['level'] == level:
        position = _subtree_end(entries, end) - len(branch)
    elif direction == 'indent' and has_previous_sibling:
        difference = 1
    elif direction == 'outdent' and level > 1:
        parent = next(i for i in reversed(range(index)) if entries[i]['level'] < level)
        position = _subtree_end(entries, parent) - len(branch)
        difference = -1
    else:
        raise ValueError('此目录条目无法沿所选方向移动。')
    for entry in branch:
        entry['level'] += difference
    result = deepcopy(book)
    result.toc_entries = remainder[:position] + branch + remainder[position:]
    return StructureEdit(result, chapter, f'已调整目录条目「{branch[0]["title"]}」的位置。', toc_index=position)


def heading_toc(book: Book) -> Book:
    """Prepare a replacement navigation without mutating the source project."""
    result = deepcopy(book)
    result.toc_entries = []
    for chapter, entry in zip(result.chapters, chapter_toc(result.chapters)):
        result.toc_entries.append(entry)
        doc = editable_chapter_document(result, chapter)
        body = doc.find(_BODY)
        occupied = {value for node in doc.iter() for attr in _ID_ATTRIBUTES if (value := node.get(attr))}
        depth = []
        changed = False
        for position, (node, title, rank) in enumerate(_headings(body), 1):
            if position == 1 and title == entry['title']:
                continue
            while depth and depth[-1] >= rank:
                depth.pop()
            depth.append(rank)
            anchor = node.get('id') or node.get(f'{{{XML}}}id')
            if not anchor:
                anchor = _unique_name(f'heading-{position}', occupied)
                occupied.add(anchor)
                node.set('id', anchor)
                changed = True
            result.toc_entries.append(TocEntry(title=title, path=chapter.path, fragment=anchor,
                                               level=chapter.level + len(depth)))
        if changed:
            chapter.html = _inner(body)
    return result


def _split_nodes(body):
    headings = {node: (index, title, rank) for index, (node, title, rank) in enumerate(_headings(body))}
    has_previous = False
    content_tags = {f'{{{XHTML}}}{tag}' for tag in ('img', 'video', 'audio', 'hr', 'table')}
    for event, node in etree.iterwalk(body, events=('start', 'end')):
        if event == 'start':
            if node in headings and has_previous and all(parent.tag in _WRAPPERS for parent in node.iterancestors()
                                                        if parent is not body.getparent()):
                yield Heading(*headings[node]), node
            has_previous |= bool((node.text or '').strip()) or node.tag in content_tags
        else:
            has_previous |= bool((node.tail or '').strip())


def split_choices(book: Book, chapter_index: int) -> list[Heading]:
    chapter = book.chapters[chapter_index]
    doc = editable_chapter_document(book, chapter)
    return [heading for heading, _ in _split_nodes(doc.find(_BODY))]


def _require_reflow(book):
    if is_fixed_layout(book):
        raise ValueError('固定版式的 EPUB 保留原分页，不能拆分或合并章节。')
    if any(asset.media_type == 'application/smil+xml' for asset in book.assets.values()):
        raise ValueError('此书包含同步朗读轨道，不能拆分或合并章节。')


def _split_body(body, start):
    """Move the suffix while retaining the surrounding block wrappers."""
    right = None
    while start is not body:
        parent = start.getparent()
        following = list(start.itersiblings())
        branch = etree.Element(parent.tag, attrib={key: val for key, val in parent.attrib.items()
                                                    if key not in _ID_ATTRIBUTES}, nsmap=parent.nsmap)
        branch.append(start if right is None else right)
        for sibling in following:
            branch.append(sibling)
        if parent is not body:
            branch.tail, parent.tail = parent.tail, None
        right = branch
        start = parent
    return right


def _inserted_path(book, path):
    original = PurePosixPath(path)
    occupied = {chapter.path for chapter in book.chapters} | book.assets.keys() | book._epub_members.keys()
    number = 2
    result = str(original.with_name(f'{original.stem}-part-{number}{original.suffix}'))
    while result in occupied:
        number += 1
        result = str(original.with_name(f'{original.stem}-part-{number}{original.suffix}'))
    return result


def _rewrite_href(value, before, after, redirect):
    if not value:
        return value
    try:
        url = urlsplit(value)
        target = resolve_href(before, value)
    except ValueError:
        return value
    if target is None:
        return value
    anchor = unquote(url.fragment)
    new_target, new_anchor = redirect(target, anchor)
    if before == after and (new_target, new_anchor) == (target, anchor):
        return value
    href = '' if new_target == after and (new_anchor or url.query) else asset_href(new_target, after)
    return urlunsplit(('', '', href, url.query, quote(new_anchor, safe='')))


def _commit_document(book, chapter, doc, imported):
    if imported:
        remember_chapter_document(book, chapter, doc)
    else:
        chapter.html = _inner(doc.find(_BODY))


def _update_links(book, redirect, edited):
    changed = 0

    def rewriter(source, target):
        def visit(value):
            nonlocal changed
            updated = _rewrite_href(value, source, target, redirect)
            changed += updated != value
            return updated
        return visit

    for chapter in book.chapters:
        if chapter.path in edited:
            doc, before, imported = edited[chapter.path]
        else:
            doc = editable_chapter_document(book, chapter)
            before, imported = chapter.path, chapter.path in book._epub_chapters
        old_count = changed
        map_document_references(doc, rewriter(before, chapter.path))
        if chapter.path in edited or changed != old_count:
            _commit_document(book, chapter, doc, imported)

    for path, asset in book.assets.items():
        old_count = changed
        if asset.media_type in _REF_DOCUMENTS:
            document = _xml(asset.data)
            map_document_references(document, rewriter(path, path))
            if changed != old_count:
                book.assets[path] = Asset(_bytes(document.getroottree()), asset.media_type)
        elif asset.media_type == 'text/css':
            css = asset.data.decode('utf-8', errors='surrogateescape')
            updated = map_css_references(css, rewriter(path, path))
            if updated != css:
                book.assets[path] = Asset(updated.encode('utf-8', errors='surrogateescape'), asset.media_type)

    for path in (*original_navigation_paths(book), book._epub_path):
        if path is None:
            continue
        old_count = changed
        doc = _xml(book._epub_members[path])
        if path == book._epub_path:
            for tag in ('guide', 'collection', 'metadata'):
                for node in doc.findall(f'{{{OPF}}}{tag}'):
                    map_document_references(node, rewriter(path, path))
        else:
            map_document_references(doc, rewriter(path, path))
        if changed != old_count:
            book._epub_members[path] = _bytes(doc.getroottree())
    return changed


def _remap_entries(entries, redirect):
    for entry in entries:
        entry['path'], entry['fragment'] = redirect(entry['path'], entry['fragment'])


def split_chapter(book: Book, chapter_index: int, heading_index: int) -> StructureEdit:
    _require_reflow(book)
    result = deepcopy(book)
    entries = current_toc(result)
    first = result.chapters[chapter_index]
    imported = first.path in result._epub_chapters
    doc = editable_chapter_document(result, first)
    body = doc.find(_BODY)
    target = next(((heading, node) for heading, node in _split_nodes(body) if heading.index == heading_index), None)
    if target is None:
        raise ValueError('请选择章节中可作为拆分点的标题。')
    heading, start = target
    right_body = _split_body(body, start)
    right_doc = deepcopy(doc)
    right_doc.replace(right_doc.find(_BODY), right_body)
    # Any stable root ID still refers to the original document.
    for attr in _ID_ATTRIBUTES:
        right_doc.attrib.pop(attr, None)
    new_path = _inserted_path(result, first.path)
    following = result.chapters[chapter_index + 1:chapter_index + 2]
    level = first.level + bool(following and following[0].level > first.level)
    second = Chapter(heading.title, level=level, source=first.source, path=new_path)
    # A full-page OCR result can no longer replace either fragment safely.
    first.scan = None
    result.chapters.insert(chapter_index + 1, second)
    moved = {value for node in right_body.iter() for attr in _ID_ATTRIBUTES if (value := node.get(attr))}

    def redirect(path, anchor):
        return (new_path, anchor) if path == first.path and anchor in moved else (path, anchor)

    relinked = _update_links(result, redirect, {first.path: (doc, first.path, imported),
                                               new_path: (right_doc, first.path, imported)})
    _remap_entries(entries, redirect)
    if not any(entry['path'] == new_path for entry in entries):
        affected = [i for i, entry in enumerate(entries) if entry['path'] == first.path]
        insert = affected[-1] + 1 if affected else len(entries)
        entries[insert:insert] = chapter_toc([second])
    result.toc_entries = entries
    return StructureEdit(result, chapter_index + 1, f'拆分《{first.title}》，在「{heading.title}」起新章。', relinked)


def _head_signature(doc, path):
    head = deepcopy(doc.find(_HEAD))
    for title in head.findall(f'{{{XHTML}}}title'):
        head.remove(title)
    map_document_references(head, lambda value: _rewrite_href(value, path, 'index.xhtml', lambda p, a: (p, a)))
    return [(node.tag, dict(node.attrib), (node.text or '').strip())
            for node in head if isinstance(node.tag, str)]


def merge_chapters(book: Book, chapter_index: int) -> StructureEdit:
    _require_reflow(book)
    if chapter_index < 0 or chapter_index + 1 >= len(book.chapters):
        raise ValueError('请选择后面还有章节的条目。')
    result = deepcopy(book)
    entries = current_toc(result)
    first, second = result.chapters[chapter_index:chapter_index + 2]
    first_doc = editable_chapter_document(result, first)
    second_doc = editable_chapter_document(result, second)
    imported = first.path in result._epub_chapters
    if imported != (second.path in result._epub_chapters) or (
            _head_signature(first_doc, first.path) != _head_signature(second_doc, second.path)):
        raise ValueError('相邻章节使用不同的页面样式，无法在保留原版式的情况下合并。')
    if second.level < first.level:
        raise ValueError('下一章属于更高的目录层级，请先调整层级。')
    first_body, second_body = first_doc.find(_BODY), second_doc.find(_BODY)
    occupied = {value for node in first_doc.iter() for attr in _ID_ATTRIBUTES if (value := node.get(attr))}
    anchor_map = {}
    for node in second_body.iter():
        for attr in _ID_ATTRIBUTES:
            if value := node.get(attr):
                revised = anchor_map.get(value) or _unique_name(value, occupied)
                anchor_map[value] = revised
                node.set(attr, revised)
                occupied.add(revised)
    marker = second_body.get('id') or second_body.get(f'{{{XML}}}id') or _unique_name('merged-section', occupied)
    second_body.tag = f'{{{XHTML}}}section'
    if not second_body.get('id') and not second_body.get(f'{{{XML}}}id'):
        second_body.set('id', marker)
    for node in second_doc.iter():
        if node is not second_body and second_body not in node.iterancestors():
            for attr in _ID_ATTRIBUTES:
                if value := node.get(attr):
                    anchor_map.setdefault(value, marker)
    for node in second_body.iter():
        for attr in _ID_REFERENCES:
            if value := node.get(attr):
                updated = ' '.join(anchor_map.get(identifier, identifier) for identifier in value.split())
                if updated != value:
                    node.set(attr, updated)

    def redirect(path, anchor):
        return (first.path, anchor_map.get(anchor, anchor) if anchor else marker) if path == second.path else (path, anchor)

    # The second portion still resolves its URLs from the original document directory.
    relinked = 0

    def second_ref(value):
        nonlocal relinked
        updated = _rewrite_href(value, second.path, first.path, redirect)
        relinked += updated != value
        return updated

    map_document_references(second_body, second_ref)
    if second.source != first.source and second.source.startswith(('https://', 'http://')):
        footer = etree.SubElement(second_body, f'{{{XHTML}}}footer')
        paragraph = etree.SubElement(footer, f'{{{XHTML}}}p')
        etree.SubElement(paragraph, f'{{{XHTML}}}a', href=second.source).text = '原网页'
    first_body.append(second_body)
    first.scan = None
    result.chapters.pop(chapter_index + 1)
    result._epub_chapters.pop(second.path, None)
    result._epub_members.pop(second.path, None)
    if second.level > first.level:
        for chapter in result.chapters[chapter_index + 1:]:
            if chapter.level <= second.level:
                break
            chapter.level -= 1
    relinked += _update_links(result, redirect, {first.path: (first_doc, first.path, imported)})
    _remap_entries(entries, redirect)
    result.toc_entries = entries
    return StructureEdit(result, chapter_index, f'将《{second.title}》接到《{first.title}》正文末尾。', relinked)
