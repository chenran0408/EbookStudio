"""Compare a saved selection of web articles with an editable book."""

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from pathlib import PurePosixPath
from uuid import uuid4
import posixpath

from lxml import etree, html

import capture
from ebook_core import Asset, Book, Chapter, asset_href
from epub_io import chapter_html, current_toc


@dataclass(frozen=True, slots=True)
class CaptureProfile:
    urls: list[str]
    directory: str = ''
    skipped: list[str] = field(default_factory=list)
    rendered: bool = False
    article_xpath: str = ''
    directory_xpath: str = ''
    identifier: str = field(default_factory=lambda: uuid4().hex)

    @property
    def label(self) -> str:
        if self.directory:
            return self.directory
        return f'{self.urls[0]}（{len(self.urls)} 篇）' if self.urls else '未选择文章'


@dataclass(frozen=True, slots=True)
class WebUpdate:
    url: str
    state: str
    incoming: Chapter | None = None
    assets: dict[str, Asset] = field(default_factory=dict)
    chapter_index: int | None = None
    locally_edited: bool = False
    notices: tuple[str, ...] = ()


def remember_profile(book: Book, profile: CaptureProfile) -> None:
    for index, existing in enumerate(book.capture_profiles):
        if existing['identifier'] == profile.identifier:
            book.capture_profiles[index] = asdict(profile)
            return
    book.capture_profiles.append(asdict(profile))


def profiles_for(book: Book) -> list[CaptureProfile]:
    return [CaptureProfile(**profile) for profile in book.capture_profiles]


def _addresses(profile: CaptureProfile) -> tuple[list[str], str]:
    urls = list(profile.urls)
    if not profile.directory:
        return urls, ''
    try:
        candidates = capture.discover(profile.directory, rendered=profile.rendered,
                                      directory_xpath=profile.directory_xpath)
    except (capture.CaptureError, ValueError) as exc:
        return urls, f'目录更新失败，已继续检查已选文章：{exc}'
    skipped = set(profile.skipped)
    urls.extend(candidate.url for candidate in candidates if capture.web_url_key(candidate.url) not in skipped)
    return urls, ''


def _source_keys(chapter: Chapter) -> set[str]:
    keys = set()
    if chapter.web:
        keys.add(chapter.web['url'])
    if chapter.source.startswith(('http://', 'https://')):
        keys.add(capture.web_url_key(chapter.source))
    return keys


def collect_updates(book: Book, profile: CaptureProfile, *, progress=None) -> tuple[list[WebUpdate], str]:
    """Read source articles without changing the target book or selected profile."""
    urls, directory_notice = _addresses(profile)
    requests = list(dict.fromkeys(capture.web_url_key(url) for url in urls))
    existing = defaultdict(set)
    for index, chapter in enumerate(book.chapters):
        for key in _source_keys(chapter):
            existing[key].add(index)

    results = []
    seen = set()
    for offset, url in enumerate(requests):
        if progress:
            progress(offset, len(requests), f'正在核对第 {offset + 1} 篇网页')
        try:
            notices = []
            incoming, assets = capture.capture(url, rendered=profile.rendered, warnings=notices,
                                               article_xpath=profile.article_xpath)
            keys = _source_keys(incoming)
            indices = set().union(*(existing[key] for key in keys))
            if len(indices) > 1:
                raise capture.CaptureError('书稿中有多个章节来自此网址，请先确定要保留的章节。')
            if keys & seen:
                continue
            seen.update(keys)
            index = next(iter(indices), None)
            if index is None:
                results.append(WebUpdate(url, 'new', incoming, assets, notices=tuple(notices)))
                continue
            current = book.chapters[index]
            current_revision = capture.web_revision(current)
            baseline = current.web or {}
            remote_revision = incoming.web['remote']
            state = 'same' if remote_revision == baseline.get('remote', current_revision) else 'changed'
            edited = current_revision != baseline.get('local')
            results.append(WebUpdate(url, state, incoming, assets, index, edited, tuple(notices)))
        except Exception as exc:
            results.append(WebUpdate(url, 'failed', notices=(str(exc),)))
    return results, directory_notice


def _adopt_images(book: Book, chapter: Chapter, assets: dict[str, Asset]) -> None:
    folder = posixpath.dirname(chapter.path)
    changed = {}
    for name, asset in assets.items():
        destination = posixpath.join(folder, name)
        current = book.assets.get(destination)
        if current is not None and current != asset:
            extension = PurePosixPath(name).suffix
            destination = posixpath.join(folder, 'images', uuid4().hex + extension)
            changed[name] = asset_href(destination, chapter.path)
        book.assets[destination] = asset
    if changed:
        root = html.fromstring(chapter.html)
        for image in root.xpath('.//img[@src]'):
            if image.get('src') in changed:
                image.set('src', changed[image.get('src')])
        chapter.html = etree.tostring(root, encoding='unicode', method='xml')


def _restore_anchors(current: Chapter, incoming: Chapter) -> None:
    """Retain local link targets when a newer site revision changes the HTML."""
    old = html.fragment_fromstring(chapter_html(current.html), create_parent=True)
    anchors = old.xpath('.//*[@id] | .//a[@name]')
    if not anchors:
        return
    new = html.fromstring(incoming.html)
    present = set(new.xpath('//@id | //a/@name'))
    headings = defaultdict(list)
    for heading in new.xpath('.//h1 | .//h2 | .//h3 | .//h4 | .//h5 | .//h6'):
        if not heading.get('id'):
            text = ' '.join(heading.text_content().split())
            headings[(heading.tag, text)].append(heading)
    changed = False
    for node in anchors:
        anchor = node.get('id') or node.get('name')
        if not anchor or anchor in present:
            continue
        text = ' '.join(node.text_content().split())
        if matches := headings.get((node.tag, text)):
            matches.pop(0).set('id', anchor)
        else:
            new.insert(0, etree.Element('span', id=anchor))
        present.add(anchor)
        changed = True
    if changed:
        incoming.html = etree.tostring(new, encoding='unicode', method='xml')


def apply_updates(book: Book, chosen: list[WebUpdate], profile: CaptureProfile) -> tuple[Book, int]:
    """Return an edited copy ready to adopt once a checkpoint has been written."""
    updated = deepcopy(book)
    if updated.toc_entries is None and updated._epub_path:
        updated.toc_entries = current_toc(book)
    first_index = len(updated.chapters)
    for result in chosen:
        if result.state not in {'new', 'changed'} or result.incoming is None:
            raise ValueError('请选择新增或有变化的网页。')
        incoming = deepcopy(result.incoming)
        index = result.chapter_index
        if index is not None:
            current = updated.chapters[index]
            incoming.path = current.path
            incoming.level = current.level
            _restore_anchors(current, incoming)
        _adopt_images(updated, incoming, result.assets)
        incoming.web['local'] = capture.web_revision(incoming)
        if index is None:
            index = len(updated.chapters)
            updated.chapters.append(incoming)
        else:
            updated.chapters[index] = incoming
        first_index = min(first_index, index)
    urls = list(dict.fromkeys([*profile.urls, *(row.url for row in chosen)]))
    remember_profile(updated, replace(profile, urls=urls))
    return updated, first_index
