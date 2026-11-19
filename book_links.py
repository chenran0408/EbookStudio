"""Shared traversal of relative references in book markup and stylesheets."""
from __future__ import annotations

import re

from lxml import etree


_URL_ATTRIBUTES = {'href', 'src', 'poster', 'data', 'background'}
_CSS_URL = re.compile(r'''url\(\s*(?:"([^"]*)"|'([^']*)'|([^\s)]+))\s*\)''', re.I)
_CSS_IMPORT = re.compile(r'''(@import\s+)(["'])(.*?)\2''', re.I)


def map_css_references(css, visit):
    def url(match):
        value = next(group for group in match.groups() if group is not None)
        updated = visit(value)
        return match[0] if updated == value else f'url("{updated}")'

    def imported(match):
        updated = visit(match[3])
        return match[0] if updated == match[3] else f'{match[1]}{match[2]}{updated}{match[2]}'

    return _CSS_IMPORT.sub(imported, _CSS_URL.sub(url, css))


def map_document_references(document, visit):
    """Visit HTML, SVG, NCX and OPF URLs, replacing only values that changed."""
    for node in document.iter():
        if not isinstance(node.tag, str):
            continue
        for name, value in list(node.attrib.items()):
            local = etree.QName(name).localname
            updated = value
            if local in _URL_ATTRIBUTES:
                updated = visit(value)
            elif local == 'srcset' and 'data:' not in value.casefold():
                parts = re.split(r'(\s*,\s*)', value)
                for index in range(0, len(parts), 2):
                    candidate = re.match(r'^(\s*)(\S+)(.*)$', parts[index])
                    if candidate:
                        parts[index] = candidate[1] + visit(candidate[2]) + candidate[3]
                updated = ''.join(parts)
            elif local == 'style' or 'url(' in value.casefold():
                updated = map_css_references(value, visit)
            if updated != value:
                node.set(name, updated)
        if etree.QName(node).localname == 'style' and node.text:
            updated = map_css_references(node.text, visit)
            if updated != node.text:
                node.text = updated
