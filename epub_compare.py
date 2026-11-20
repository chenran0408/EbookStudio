"""Compare the editable publication in two independently packaged EPUB files."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import posixpath
from zipfile import BadZipFile

from lxml import etree

from checkpoint_compare import Change, compare_books, readable
from epub_io import DC, OPF, _xml, open_epub


def _unique_keys(paths, key):
    unique = {}
    for path in paths:
        value = key(path)
        if value:
            unique[value] = path if value not in unique else None
    return unique


def _pair_unique(pairs, old, new, old_key, new_key, confirm=None):
    left = _unique_keys(old.keys() - pairs.keys(), old_key)
    right = _unique_keys(new.keys() - set(pairs.values()), new_key)
    for key, original in left.items():
        revised = right.get(key)
        if original and revised and (confirm is None or confirm(original, revised)):
            pairs[original] = revised


def _relative(book, path):
    return posixpath.relpath(path, posixpath.dirname(book._epub_path) or '.')


def _pair_paths(pairs, before, after, old, new):
    _pair_unique(pairs, old, new, str, str)
    _pair_unique(pairs, old, new, lambda path: _relative(before, path), lambda path: _relative(after, path))


def _similar_body(left, right, title):
    # A shared heading alone is not evidence that the rest of a chapter survived.
    left = ''.join(left.split()).removeprefix(''.join(title.split()))
    right = ''.join(right.split()).removeprefix(''.join(title.split()))
    if not left or not right:
        return False
    if left == right:
        return True
    # Compare short runs of text: works for unsegmented Chinese and is linear in chapter size.
    def triples(text):
        return {text[i:i+3] for i in range(len(text)-2)}

    a, b = triples(left), triples(right)
    return bool(a and b and len(a & b) * 2 >= min(len(a), len(b))
                and max(len(a), len(b)) <= min(len(a), len(b)) * 3)


def _chapter_pairs(before, after):
    old = {chapter.path: chapter for chapter in before.chapters}
    new = {chapter.path: chapter for chapter in after.chapters}
    pairs = {p: p for p in old.keys() & new.keys() if old[p].html == new[p].html}
    _pair_unique(pairs, old, new, lambda p: old[p].html.strip(), lambda p: new[p].html.strip())

    original_text = {p: readable(old[p].html) for p in old.keys() - pairs.keys()}
    revised_text = {p: readable(new[p].html) for p in new.keys() - set(pairs.values())}
    _pair_unique(pairs, old, new, original_text.get, revised_text.get)

    old_titles = Counter(c.title.strip() for c in before.chapters)
    new_titles = Counter(c.title.strip() for c in after.chapters)
    def unique_title(chapter):
        title = chapter.title.strip()
        return title if old_titles[title] == new_titles[title] == 1 else None

    _pair_unique(pairs, old, new, lambda p: unique_title(old[p]), lambda p: unique_title(new[p]),
                 lambda a, b: _similar_body(original_text[a], revised_text[b], old[a].title))
    _pair_paths(pairs, before, after, old, new)
    return pairs


def _asset_pairs(before, after):
    old, new = before.assets, after.assets
    pairs = {p: p for p in old.keys() & new.keys() if old[p] == new[p]}
    def fingerprint(asset):
        return asset.media_type, sha256(asset.data).digest()

    _pair_unique(pairs, old, new, lambda p: fingerprint(old[p]), lambda p: fingerprint(new[p]))
    _pair_paths(pairs, before, after, old, new)
    return pairs


def _metadata(book):
    package = _xml(book._epub_members[book._epub_path])
    metadata = package.find(f'{{{OPF}}}metadata')
    # open_epub supplies an ID for editing a book that lacks one. Compare only IDs from the file.
    identifier = next((node for node in metadata.findall(f'{{{DC}}}identifier')
                       if node.get('id') == package.get('unique-identifier') and node.text), None)
    if identifier is None:
        book.identifier = ''
    first_fields = {metadata.find(f'{{{DC}}}{name}') for name in ('title', 'creator', 'language')}
    extra = []
    for node in metadata:
        if not isinstance(node.tag, str):
            continue
        if node in first_fields or node is identifier:
            # The simple value has its own row. Keep refinements such as language and role.
            attrs = {k: v for k, v in node.attrib.items() if k != 'id'}
            if attrs:
                extra.append(f'{etree.QName(node).localname} {attrs}')
        else:
            tag = etree.QName(node).localname
            attrs = ' '.join(f'{etree.QName(k).localname}={v}' for k, v in sorted(node.attrib.items()))
            extra.append(' · '.join(value for value in (tag, attrs, ''.join(node.itertext()).strip()) if value))
    return '\n'.join(extra)


def compare_epubs(original: Path, revision: Path):
    """Read both inputs without adopting a book into the editor or creating an output."""
    def load(path, label):
        try:
            return open_epub(path)
        except (OSError, BadZipFile, KeyError, ValueError, etree.XMLSyntaxError) as exc:
            raise ValueError(f'无法读取{label} EPUB：{path}\n{exc}') from exc

    before = load(original, '原版')
    after = load(revision, '修订版')
    old_meta, new_meta = _metadata(before), _metadata(after)
    changes = compare_books(before, after, chapter_paths=_chapter_pairs(before, after),
                            asset_paths=_asset_pairs(before, after))
    if old_meta != new_meta:
        changes.append(Change('书稿资料', '更改', '其他出版资料', old_meta, new_meta))
    # Keep editorial decisions above archive housekeeping when a repack renames the whole book.
    priority = {'正文': 0, '章节': 1, '章节顺序': 2, '目录': 3, '资源文件': 5, '章节文件': 5}
    return sorted(changes, key=lambda change: priority.get(change.kind, 4))
