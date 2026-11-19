"""Read-only comparison of the editable contents of a book and a checkpoint."""
from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha256
import json
import re

from lxml import etree

from book_readable import readable_sections
from book_typography import decode_stylesheet
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


def readable(html):
    text = ' '.join(text for _, text in readable_sections(html))
    return re.sub(r'([。！？]|[.!?](?=\s))\s*', r'\1\n', text).rstrip()


def formatted(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def compare_books(before, after):
    changes = []

    def compare(kind, name, left, right, path=''):
        if left != right:
            changes.append(Change(kind, '更改', name, formatted(left), formatted(right), path))

    old = {ch.path: ch for ch in before.chapters}
    new = {ch.path: ch for ch in after.chapters}
    for path in dict.fromkeys((*old, *new)):
        a, b = old.get(path), new.get(path)
        if not a or not b:
            changes.append(Change('章节', '新增' if b else '删除', (b or a).title,
                                  readable(a.html) if a else '', readable(b.html) if b else '',
                                  path, (a.html if a else '', b.html if b else '')))
            continue
        if a.html != b.html:
            first, second = readable(a.html), readable(b.html)
            kind = '正文' if first != second else '章节排版'
            changes.append(Change(kind, '更改', b.title, first, second, path, (a.html, b.html)))
        for field, label in (('title', '章节标题'), ('level', '章节层级'), ('source', '原文来源'),
                             ('scan', '扫描来源'), ('web', '采集信息')):
            compare(label, b.title, getattr(a, field), getattr(b, field), path)
        # Imported chapters can also carry linked or embedded styles in their HTML head.
        if before._epub_members.get(path) != after._epub_members.get(path):
            def head(book, chapter):
                node = editable_chapter_document(book, chapter).find('{*}head')
                return etree.tostring(node, encoding='unicode') if node is not None else ''

            compare('章节页头', b.title, head(before, a), head(after, b), path)

    common_before = [path for path in old if path in new]
    common_after = [path for path in new if path in old]
    if common_before != common_after:
        def order(chapters):
            return '\n'.join(f'{i}. {ch.title}  ·  {ch.path}' for i, ch in enumerate(chapters, 1))

        changes.append(Change('章节顺序', '调序', '阅读顺序', order(before.chapters), order(after.chapters)))

    for field, label in (('title', '书名'), ('author', '作者'), ('language', '语言'), ('identifier', '标识符'),
                         ('cover', '封面'), ('stylesheet', '书稿样式'), ('linked_stylesheets', '关联样式表'),
                         ('font_subsets', '字体子集'), ('inline_toc_path', '书内目录页'),
                         ('capture_profiles', '网页采集配置')):
        compare('书稿资料', label, getattr(before, field), getattr(after, field))

    def toc(book):
        return '\n'.join('  ' * (entry['level'] - 1) + entry['title'] + '  ·  ' + entry['path']
                         + ('#' + entry['fragment'] if entry['fragment'] else '')
                         for entry in current_toc(book))

    compare('目录', '阅读器目录', toc(before), toc(after))

    for path in sorted(before.assets.keys() | after.assets.keys()):
        a, b = before.assets.get(path), after.assets.get(path)
        if a == b:
            continue

        def description(asset):
            if not asset:
                return ''
            info = f'{asset.media_type}\n{len(asset.data):,} 字节\nSHA-256 {sha256(asset.data).hexdigest()}'
            return info + '\n' + decode_stylesheet(asset.data) if asset.media_type == 'text/css' else info

        action = '新增' if a is None else '删除' if b is None else '更改'
        changes.append(Change('资源', action, path.rsplit('/', 1)[-1], description(a), description(b), path))
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
