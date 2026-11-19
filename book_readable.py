"""Extract visible text for book and library searches."""
from lxml import etree, html

from epub_io import chapter_html


BLOCK_ELEMENTS = {
    "address", "article", "aside", "blockquote", "br", "caption", "dd", "div",
    "dl", "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5",
    "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
    "table", "td", "th", "tr", "ul",
}
HIDDEN_ELEMENTS = {"head", "script", "style", "template", "noscript"}


def readable_sections(document, boundaries=None):
    """Split readable text at the requested HTML anchors, preserving inline text continuity."""
    if not document.strip():
        return []
    try:
        document = chapter_html(document)
    except (etree.XMLSyntaxError, ValueError):
        # A writer can search a chapter while its XHTML source is still being edited.
        pass
    try:
        page = html.document_fromstring(document)
    except etree.ParserError:
        return []
    body = page.find("body")
    if body is None:
        return []
    sections = []
    pieces = []
    active = None

    def finish():
        text = " ".join("".join(pieces).split())
        if text:
            sections.append((active, text))
        pieces.clear()

    def visit(node):
        nonlocal active
        if not isinstance(node.tag, str):
            return
        tag = node.tag.rsplit(":", 1)[-1]
        if tag in HIDDEN_ELEMENTS or "hidden" in node.attrib:
            return
        if boundaries:
            anchor = node.get("id") or node.get("xml:id") or node.get("name")
            if anchor in boundaries and boundaries[anchor] != active:
                finish()
                active = boundaries[anchor]
        separated = tag in BLOCK_ELEMENTS
        if separated:
            pieces.append(" ")
        if node.text:
            pieces.append(node.text)
        for child in node:
            visit(child)
            if child.tail:
                pieces.append(child.tail)
        if separated:
            pieces.append(" ")

    visit(body)
    finish()
    return sections
