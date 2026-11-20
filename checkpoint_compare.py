"""Read-only comparison of the editable contents of a book and a checkpoint."""
from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha256
import json
import re

from lxml import etree

from book_readable import readable_sections
from book_typography import decode_stylesheet
from ebook_core import Asset
from epub_io import current_toc, editable_chapter_document


@dataclass(frozen=True)
class Change:
    kind: str
    action: str
    name: str
    before: str
    after: str
    path: str = ''
    source: tuple[str, str] | None = None
    images: tuple[Asset | None, Asset | None] | None = None


def readable(html):
    text = ' '.join(text for _, text in readable_sections(html))
    return re.sub(r'([。！？]|[.!?](?=\s))\s*', r'\1\n', text).rstrip()


def formatted(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _aligned_paths(left, right, pairs):
    for original in left:
        yield original, pairs.get(original)
    matched = set(pairs.values())
    for revised in right:
        if revised not in matched:
            yield None, revised


def compare_books(before, after, *, chapter_paths=None, asset_paths=None):
    changes = []

    def compare(kind, name, left, right, path=''):
        if left != right:
            changes.append(Change(kind, '更改', name, formatted(left), formatted(right), path))

    old = {ch.path: ch for ch in before.chapters}
    new = {ch.path: ch for ch in after.chapters}
    if chapter_paths is None:
        chapter_paths = {path: path for path in old.keys() & new.keys()}
    if asset_paths is None:
        asset_paths = {path: path for path in before.assets.keys() & after.assets.keys()}
    targets = {**chapter_paths, **asset_paths}
    for left, right in _aligned_paths(old, new, chapter_paths):
        a, b = old.get(left), new.get(right)
        path = right or left
        if not a or not b:
            changes.append(Change('章节', '新增' if b else '删除', (b or a).title,
                                  readable(a.html) if a else '', readable(b.html) if b else '',
                                  path, (a.html if a else '', b.html if b else '')))
            continue
        if left != right:
            changes.append(Change('章节文件', '改名或移动', b.title, left, right, right))
        if a.html != b.html:
            first, second = readable(a.html), readable(b.html)
            kind = '正文' if first != second else '章节排版'
            changes.append(Change(kind, '更改', b.title, first, second, path, (a.html, b.html)))
        for field, label in (('title', '章节标题'), ('level', '章节层级'), ('source', '原文来源'),
                             ('scan', '扫描来源'), ('web', '采集信息')):
            compare(label, b.title, getattr(a, field), getattr(b, field), path)
        # Imported chapters can also carry linked or embedded styles in their HTML head.
        if before._epub_members.get(left) != after._epub_members.get(right):
            def structure(book, chapter):
                doc = editable_chapter_document(book, chapter)
                head = doc.find('{*}head')
                attrs = {tag: dict(node.attrib) for tag, node in [('html', doc), ('body', doc.find('{*}body'))]
                         if node is not None and node.attrib}
                return (etree.tostring(head, encoding='unicode') if head is not None else ''), attrs

            old_head, old_attrs = structure(before, a)
            new_head, new_attrs = structure(after, b)
            compare('章节页头', b.title, old_head, new_head, path)
            compare('章节属性', b.title, old_attrs, new_attrs, path)

    common_before = [chapter_paths[path] for path in old if path in chapter_paths]
    matched = set(chapter_paths.values())
    common_after = [path for path in new if path in matched]
    if common_before != common_after:
        def order(chapters):
            return '\n'.join(f'{i}. {ch.title}  ·  {ch.path}' for i, ch in enumerate(chapters, 1))

        changes.append(Change('章节顺序', '调序', '阅读顺序', order(before.chapters), order(after.chapters)))

    for field, label in (('title', '书名'), ('author', '作者'), ('language', '语言'), ('identifier', '标识符'),
                         ('cover', '封面'), ('stylesheet', '书稿样式'), ('linked_stylesheets', '关联样式表'),
                         ('font_subsets', '字体子集'), ('inline_toc_path', '书内目录页'),
                         ('capture_profiles', '网页采集配置')):
        left, right = getattr(before, field), getattr(after, field)
        if field in ('cover', 'inline_toc_path') and targets.get(left, left) == right:
            continue
        compare('书稿资料', label, left, right)

    def toc(entries):
        return '\n'.join('  ' * (entry['level'] - 1) + entry['title'] + '  ·  ' + entry['path']
                         + ('#' + entry['fragment'] if entry['fragment'] else '')
                         for entry in entries)

    old_toc, new_toc = current_toc(before), current_toc(after)
    if [dict(entry, path=targets.get(entry['path'], entry['path'])) for entry in old_toc] != new_toc:
        compare('目录', '阅读器目录', toc(old_toc), toc(new_toc))

    for left, right in _aligned_paths(sorted(before.assets), sorted(after.assets), asset_paths):
        a, b = before.assets.get(left), after.assets.get(right)
        path = right or left
        if left and right and left != right:
            changes.append(Change('资源文件', '改名或移动', right.rsplit('/', 1)[-1], left, right, right))
        if a == b:
            continue

        def description(asset):
            if not asset:
                return ''
            info = f'{asset.media_type}\n{len(asset.data):,} 字节\nSHA-256 {sha256(asset.data).hexdigest()}'
            return info + '\n' + decode_stylesheet(asset.data) if asset.media_type == 'text/css' else info

        action = '新增' if a is None else '删除' if b is None else '更改'
        images = (a, b) if any(asset and asset.media_type.startswith('image/') for asset in (a, b)) else None
        changes.append(Change('资源', action, path.rsplit('/', 1)[-1], description(a), description(b), path,
                              images=images))
    return changes


def excerpts(left, right):
    """Return aligned changed lines and one line of neighboring context on each side."""
    a, b = left.split('\n'), right.split('\n')
    left_lines, right_lines = [], []
    for group in SequenceMatcher(None, a, b).get_grouped_opcodes(1):
        if left_lines:
            left_lines.append((False, '⋯'))
            right_lines.append((False, '⋯'))
        for tag, i, j, k, m in group:
            length = max(j - i, m - k)
            for n in range(length):
                left_lines.append((tag != 'equal' and n < j - i, a[i+n] if n < j-i else ''))
                right_lines.append((tag != 'equal' and n < m - k, b[k+n] if n < m-k else ''))
    return left_lines, right_lines
