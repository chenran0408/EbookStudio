"""Inspect editable book content before export without changing the project."""

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from lxml import etree, html

from ebook_core import Book, resolve_href
from epub_io import chapter_html


@dataclass(frozen=True, slots=True)
class BookIssue:
    severity: str
    title: str
    detail: str
    chapter: int | None = None


def inspect_book(book: Book) -> list[BookIssue]:
    """Check chapter structure, empty content, and offline body references."""
    issues = []
    if not book.title.strip():
        issues.append(BookIssue("error", "缺少书名", "请在书籍资料中填写书名。"))
    if not book.language.strip():
        issues.append(BookIssue("error", "缺少语言", "请在书籍资料中设置语言。"))
    if not book.chapters:
        return [*issues, BookIssue("error", "书稿还没有章节", "请创建章节或导入正文。")]

    targets = {chapter.path for chapter in book.chapters} | book.assets.keys()
    # An original EPUB can reference its existing navigation document.
    targets.update(name for name in book._epub_members if name not in book._epub_chapters)
    anchors: dict[str, set[str]] = {}
    bodies = []
    previous_level = 0
    for index, chapter in enumerate(book.chapters):
        if not chapter.title.strip():
            issues.append(BookIssue("error", "缺少章节标题", "请添加一个会显示在目录中的标题。", index))
        if chapter.level < 1 or chapter.level > previous_level + 1:
            issues.append(BookIssue("warning", "目录层级跳跃", "请检查该章节与上一级章节的关系。", index))
        previous_level = max(1, chapter.level)
        try:
            body = html.fragment_fromstring(chapter_html(chapter.html), create_parent="div")
        except (etree.Error, ValueError):
            issues.append(BookIssue("error", "正文无法解析", "请切换到源码视图检查 XHTML。", index))
            bodies.append(None)
            continue
        bodies.append(body)
        anchors[chapter.path] = set(body.xpath(".//@id | .//a/@name"))
        if original := book._epub_members.get(chapter.path):
            wrapper = html.document_fromstring(original)
            anchors[chapter.path].update(wrapper.xpath("/html/@id | /html/body/@id"))
        text = body.xpath(".//text()[not(ancestor::style or ancestor::script)]")
        if not any(part.strip() for part in text) and not body.xpath(".//img | .//svg | .//math"):
            issues.append(BookIssue("warning", "空章节", "正文没有可阅读的文本或图片。若是分组标题可保留。", index))

    for index, (chapter, body) in enumerate(zip(book.chapters, bodies)):
        if body is None:
            continue
        for node in body.xpath(".//a[@href] | .//img"):
            image = node.tag == "img"
            reference = (node.get("src" if image else "href") or "").strip()
            if not reference:
                if image:
                    issues.append(BookIssue("error", "图片缺少地址", "请替换或移除这张图片。", index))
                continue
            try:
                uri = urlsplit(reference)
                target = resolve_href(chapter.path, reference)
            except ValueError:
                issues.append(BookIssue("error", "书内地址无效", reference, index))
                continue
            if target is None:
                if image and uri.scheme != "data":
                    issues.append(BookIssue("warning", "图片未离线保存", reference, index))
            elif target not in targets or (image and target not in book.assets):
                issues.append(BookIssue("error", "缺少图片" if image else "书内链接失效", reference, index))
            elif not image and uri.fragment and target in anchors and unquote(uri.fragment) not in anchors[target]:
                issues.append(BookIssue("error", "链接锚点不存在", reference, index))

    return sorted(dict.fromkeys(issues), key=lambda issue: issue.severity != "error")
