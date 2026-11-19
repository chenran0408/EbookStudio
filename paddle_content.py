"""Turn PaddleOCR-VL's ordered Markdown and page crops into an editable chapter."""

from functools import lru_cache
from pathlib import Path
import re

from lxml import html
from markdown_it import MarkdownIt

from capture import _DROP, _make_asset, _normalize
from ebook_core import Chapter
from ocr_import import INVALID_XML, _latex


@lru_cache(maxsize=1)
def _parser():
    # Paddle includes real HTML tables, including rowspan and colspan.
    parser = MarkdownIt("commonmark", {"html": True}).enable("table")
    parser.inline.add_terminator_char("$")
    parser.inline.ruler.before("escape", "literal_latex", _latex)
    return parser


def paddle_chapter(markdown: str, images: dict[str, Path], page: int):
    # The official export writes circled footnote markers as LaTeX superscripts.
    markdown = re.sub(r"\$\s*\^\{([①-⑳])\}\s*\$", r"<sup>\1</sup>", markdown)
    body = _parser().render(INVALID_XML.sub("", markdown))
    root = html.fragment_fromstring(body or "<p>（空白页）</p>", create_parent=True)
    for node in list(root.iterdescendants()):
        if not isinstance(node.tag, str) or node.tag in _DROP:
            if node.getparent() is not None:
                node.drop_tree()
        elif node.tag == "a":
            node.drop_tag()

    assets = {}
    for image in root.xpath(".//img"):
        source = images.get(image.get("src"))
        if source is None:
            image.drop_tree()
            continue
        key, asset = _make_asset(source.read_bytes())
        assets[key] = asset
        image.set("src", key)
        image.set("alt", image.get("alt", ""))
        image.attrib.pop("width", None)
        image.attrib.pop("height", None)

    heading = next((node.text_content().strip() for node in root.xpath(".//h1 | .//h2")
                    if node.text_content().strip()), "")
    page_title = f"第 {page} 页"
    if heading:
        page_title += f" · {heading}"
    return Chapter(title=page_title, html=_normalize(root, "")), assets
