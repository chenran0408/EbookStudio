"""Plan whole-book replacements without serializing the original markup."""

from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass
from html import escape, unescape
from html.entities import html5
from html.parser import HTMLParser
from itertools import accumulate
import re
from time import monotonic

import regex

from ebook_core import Book
from workspace import Workspace


PREVIEW_LIMIT = 160
_REGEX_SECONDS = 3.0
_CONTEXT = 30
_HIDDEN = {"head", "script", "style", "template", "noscript", "title"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "param", "source", "track", "wbr"}
_REFERENCE = re.compile(r"&(?:#[xX][0-9A-Fa-f]+;?|#[0-9]+;?|[^\W\d][-.\w:]*;?)")
_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


class _MarkupText(HTMLParser):
    def __init__(self, document):
        super().__init__(convert_charrefs=False)
        self.document = document
        self.line_offsets = [0, *(match.end() for match in re.finditer("\n", document))]
        self.spans = []
        self.hidden = []
        self.body = True
        self.xhtml = document.lstrip("\ufeff \t\r\n").startswith("<?xml")
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        name = tag.rsplit(":", 1)[-1]
        if name == "html" and not self.hidden:
            self.spans.clear()
            self.body = False
            self.xhtml = self.xhtml or any(
                attribute.startswith("xmlns") and value == "http://www.w3.org/1999/xhtml"
                for attribute, value in attrs
            )
        if name == "body" and not self.hidden:
            self.body = True
        if name not in _VOID and (self.hidden or name in _HIDDEN or any(key == "hidden" for key, _ in attrs)):
            self.hidden.append(name)

    def handle_startendtag(self, tag, attrs):
        # An empty element has no effect on the visibility of later text.
        pass

    def handle_endtag(self, tag):
        name = tag.rsplit(":", 1)[-1]
        if self.hidden:
            if name in self.hidden:
                stop = len(self.hidden) - 1 - self.hidden[::-1].index(name)
                del self.hidden[stop:]
        elif name in {"body", "html"}:
            self.body = False

    def _record(self, start, end):
        if not self.body or self.hidden or start == end:
            return
        if self.spans and self.spans[-1][1] == start:
            self.spans[-1] = (self.spans[-1][0], end)
        else:
            self.spans.append((start, end))

    def _offset(self):
        line, column = self.getpos()
        return self.line_offsets[line - 1] + column

    def handle_data(self, data):
        start = self._offset()
        self._record(start, start + len(data))

    def _record_reference(self, name, prefix_length):
        start = self._offset()
        end = start + len(name) + prefix_length
        self._record(start, end + self.document.startswith(";", end))

    def handle_entityref(self, name):
        self._record_reference(name, 1)

    def handle_charref(self, name):
        self._record_reference(name, 2)


def _reference_value(source: str, xhtml: bool) -> str | None:
    if not xhtml:
        return unescape(source)
    if not source.endswith(";"):
        return None
    if source.startswith("&#"):
        digits = source[2:-1]
        try:
            return chr(int(digits[1:], 16) if digits.lower().startswith("x") else int(digits))
        except (ValueError, OverflowError):
            return None
    return html5.get(source[1:])


def _text_runs(document, start, end, xhtml):
    """Keep literal data and references with their exact original spelling."""
    parts = []
    position = start
    for match in _REFERENCE.finditer(document, start, end):
        if position < match.start():
            text = document[position:match.start()]
            parts.append((text, text))
        raw = match.group()
        decoded = _reference_value(raw, xhtml)
        if decoded:
            parts.append((raw, decoded))
        else:
            if parts:
                yield start, match.start(), parts
            parts = []
            start = match.end()
        position = match.end()
    if position < end:
        text = document[position:end]
        parts.append((text, text))
    if parts:
        yield start, end, parts


def _original_slice(parts, boundaries, start, end):
    """Preserve complete references. Escape a decoded prefix or suffix of a changed one."""
    if start == end:
        return ""
    pieces = []
    index = bisect_right(boundaries, start) - 1
    while boundaries[index] < end:
        raw, value = parts[index]
        left = max(0, start - boundaries[index])
        right = min(len(value), end - boundaries[index])
        if raw == value:
            pieces.append(raw[left:right])
        elif left == 0 and right == len(value):
            pieces.append(raw)
        else:
            pieces.append(escape(value[left:right], quote=False))
        index += 1
    return "".join(pieces)


def _decode_rebuilt(source: str, xhtml: bool) -> str:
    if not xhtml:
        return unescape(source)
    return _REFERENCE.sub(lambda match: _reference_value(match.group(), True) or match.group(), source)


def _excerpt(text, start, end, replacement=None):
    left = max(0, start - _CONTEXT)
    right = min(len(text), end + _CONTEXT)
    selected = text[start:end] if replacement is None else replacement
    if len(selected) > _CONTEXT * 2:
        selected = selected[:_CONTEXT] + "…" + selected[-_CONTEXT:]
    return (
        ("…" if left else "") + text[left:start] + "【" + selected + "】"
        + text[end:right] + ("…" if right < len(text) else "")
    )


@dataclass(frozen=True)
class ReplacementHit:
    chapter_index: int
    chapter_title: str
    before: str
    after: str


@dataclass(frozen=True)
class TextRun:
    """Changes in one decoded text run. Its HTML character references retain their spelling."""
    left: int
    right: int
    first_hit: int
    parts: list
    text: str
    edits: list[tuple[int, int, str]]
    xhtml: bool

    def render(self, selected):
        boundaries = list(accumulate((len(value) for _, value in self.parts), initial=0))
        raw, decoded = [], []
        cursor = 0
        for number, (start, end, new) in enumerate(self.edits, self.first_hit):
            if selected is not None and number not in selected:
                continue
            raw.extend((_original_slice(self.parts, boundaries, cursor, start), escape(new, quote=False)))
            decoded.extend((self.text[cursor:start], new))
            cursor = end
        if not raw:
            return None
        raw.append(_original_slice(self.parts, boundaries, cursor, len(self.text)))
        decoded.append(self.text[cursor:])
        source, visible = "".join(raw), "".join(decoded)
        if '<' in source or (self.xhtml and ']]>' in source) or _decode_rebuilt(source, self.xhtml) != visible:
            # Editing beside a literal angle bracket or ampersand can create markup.
            return escape(visible, quote=False)
        return source


@dataclass(frozen=True)
class ChapterReplacement:
    index: int
    title: str
    path: str
    original: str
    runs: list[TextRun]

    @property
    def hit_range(self):
        last = self.runs[-1]
        return range(self.runs[0].first_hit, last.first_hit + len(last.edits))

    def render(self, selected):
        parts = []
        cursor = 0
        for run in self.runs:
            revised = run.render(selected)
            if revised is not None:
                parts.extend((self.original[cursor:run.left], revised))
                cursor = run.right
        parts.append(self.original[cursor:])
        return "".join(parts)


@dataclass(frozen=True)
class ReplacementPlan:
    identifier: str
    matches: int
    replacements: int
    chapters: tuple[ChapterReplacement, ...]
    preview_limit: int

    def page(self, start: int, limit: int):
        """Materialize just the requested excerpts, using the original context around each edit."""
        stop = min(start + limit, self.replacements)
        for chapter in self.chapters:
            if chapter.hit_range.stop <= start:
                continue
            if chapter.hit_range.start >= stop:
                break
            for run in chapter.runs:
                for i in range(max(0, start - run.first_hit), min(len(run.edits), stop - run.first_hit)):
                    left, right, new = run.edits[i]
                    before = _excerpt(run.text, left, right)
                    after = _excerpt(run.text, left, right, new)
                    yield ReplacementHit(chapter.index, chapter.title, before, after)

    @property
    def hits(self):
        return tuple(self.page(0, self.preview_limit))

    def apply(self, book: Book, selected: frozenset[int] | None = None) -> None:
        if book.identifier != self.identifier or any(
            change.index >= len(book.chapters) or
            (book.chapters[change.index].path, book.chapters[change.index].html) != (change.path, change.original)
            for change in self.chapters
        ):
            raise ValueError("书稿已有变化，请重新预览替换。")
        for change in self.chapters:
            book.chapters[change.index].html = change.render(selected)


def _find_edits(text, pattern, replacement, regular, timeout):
    if not text.strip():
        return 0, []
    edits = []
    matches = 0
    for match in pattern.finditer(text, timeout=timeout):
        matches += 1
        new = match.expand(replacement) if regular else replacement
        if new == match[0]:
            continue
        if _INVALID_XML.search(new):
            raise ValueError("替换文字含有 EPUB 正文无法保存的控制字符。")
        edits.append((match.start(), match.end(), new))
    return matches, edits


def _validate_template(pattern, replacement):
    # A group-only pattern checks references even when the search will have no matches.
    names = {index: name for name, index in pattern.groupindex.items()}
    groups = "".join(
        f"(?P<{names[index]}>)" if index in names else "()" for index in range(1, pattern.groups + 1)
    )
    return regex.compile(groups).sub(replacement, "", count=1)


def preview_replacement(book: Book, query: str, replacement: str, *,
                        regular: bool = False, case_sensitive: bool = False,
                        preview_limit: int = PREVIEW_LIMIT) -> ReplacementPlan:
    """Match within decoded text nodes and retain source positions for the actual change."""
    if not query:
        raise ValueError("请输入要查找的文字。")
    try:
        pattern = regex.compile(query if regular else regex.escape(query), 0 if case_sensitive else regex.IGNORECASE)
        sample = _validate_template(pattern, replacement) if regular else replacement
    except (regex.error, IndexError) as exc:
        raise ValueError(f"请检查正则表达式或替换规则。{exc}") from exc
    if pattern.flags & regex.REVERSE:
        raise ValueError("批量替换按章节顺序处理。请移除逆向搜索标记 (?r)。")
    if _INVALID_XML.search(sample):
        raise ValueError("替换文字含有 EPUB 正文无法保存的控制字符。")
    deadline = monotonic() + _REGEX_SECONDS
    matches = replacements = 0
    changes = []
    try:
        for index, chapter in enumerate(book.chapters):
            markup = _MarkupText(chapter.html)
            runs = []
            for start, end in markup.spans:
                for left, right, run in _text_runs(chapter.html, start, end, markup.xhtml):
                    timeout = deadline - monotonic() if regular else None
                    if timeout is not None and timeout <= 0:
                        raise TimeoutError()
                    text = "".join(value for _, value in run)
                    found, edits = _find_edits(text, pattern, replacement, regular, timeout)
                    matches += found
                    if edits:
                        runs.append(TextRun(left, right, replacements, run, text, edits, markup.xhtml))
                        replacements += len(edits)
            if runs:
                changes.append(ChapterReplacement(index, chapter.title, chapter.path, chapter.html, runs))
    except TimeoutError as exc:
        raise ValueError("正则表达式搜索超时，请缩小匹配范围。") from exc
    return ReplacementPlan(book.identifier, matches, replacements, tuple(changes), preview_limit)


def replace_with_checkpoint(workspace: Workspace, book: Book, plan: ReplacementPlan,
                            selected: frozenset[int] | None = None) -> Book:
    """Return a revised copy only after the current book's checkpoint is published."""
    if selected == frozenset():
        return book
    revised = deepcopy(book)
    plan.apply(revised, selected)
    workspace.save_checkpoint(book, "批量替换前")
    return revised
