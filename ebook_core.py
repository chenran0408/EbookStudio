"""Editable books and the local .bookproj file format.

Chapter.html is an HTML or XHTML body fragment. Resource paths are relative
to the chapter's archive path, and asset keys are relative to the book root.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile
import json
import os
import posixpath


def archive_path(name: str) -> str:
    """Validate a literal resource name, without interpreting it as a URL."""
    if not isinstance(name, str) or not name or any(
        piece in {'', '.', '..'} for piece in name.split('/')
    ) or any(ch in name for ch in ('\\', ':', '\0')):
        raise ValueError(f'无效的书内路径：{name!r}')
    return name


def resolve_href(document: str, href: str) -> str | None:
    """Return the archive path for a local URL, or None for an external URL."""
    ref = urlsplit(href)
    if ref.scheme or ref.netloc:
        return None
    path = unquote(ref.path)
    if not path:
        return archive_path(document)
    if path.startswith('/'):
        raise ValueError(f'无效的书内链接：{href}')
    return archive_path(posixpath.normpath(posixpath.join(posixpath.dirname(document), path)))


def asset_href(asset_path: str, chapter_path: str = '') -> str:
    """Build a local URL to an asset as seen from a chapter."""
    relative = posixpath.relpath(archive_path(asset_path), posixpath.dirname(chapter_path) or '.')
    return quote(relative, safe='/')


def commit_file(staged: str | Path, destination: str | Path, *, overwrite: bool = True) -> None:
    """Publish a staged file on the same volume, atomically preserving an occupied name if requested."""
    temporary = Path(staged)
    target = Path(destination)
    if overwrite:
        temporary.replace(target)
    elif os.name == 'nt':
        temporary.rename(target)
    else:
        os.link(temporary, target)
        temporary.unlink()


@contextmanager
def publish_file(path: str | Path, *, overwrite: bool = True):
    """Write near the destination and publish only after a successful save."""
    target = Path(path)
    with NamedTemporaryFile(dir=target.parent, prefix=f'.{target.stem}-',
                            suffix='.tmp', delete=False) as file:
        temporary = Path(file.name)
    try:
        yield temporary
        commit_file(temporary, target, overwrite=overwrite)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class Asset:
    data: bytes
    media_type: str


@dataclass
class Chapter:
    title: str
    html: str = ''
    level: int = 1
    source: str = ''
    path: str = field(default_factory=lambda: f'chapter-{uuid4().hex}.xhtml')
    scan: dict | None = None
    web: dict | None = None


@dataclass
class Book:
    title: str = '未命名电子书'
    author: str = ''
    language: str = 'zh-CN'
    chapters: list[Chapter] = field(default_factory=list)
    assets: dict[str, Asset] = field(default_factory=dict)
    cover: str | None = None
    identifier: str = field(default_factory=lambda: f'urn:uuid:{uuid4()}')
    stylesheet: str = ''
    toc_entries: list[dict] | None = None
    capture_profiles: list[dict] = field(default_factory=list)
    _epub_path: str | None = field(default=None, repr=False)
    _epub_members: dict[str, bytes] = field(default_factory=dict, repr=False)
    _epub_chapters: dict[str, tuple[str, int, str]] = field(default_factory=dict, repr=False)

    def save(self, path: str | Path) -> None:
        chapter_rows = []
        chapter_context = {}
        for chapter in self.chapters:
            row = asdict(chapter)
            context = {key: value for key in ('scan', 'web') if (value := row.pop(key)) is not None}
            if context:
                chapter_context[chapter.path] = context
            chapter_rows.append(row)
        data = {
            'version': 1,
            'title': self.title,
            'author': self.author,
            'language': self.language,
            'identifier': self.identifier,
            'stylesheet': self.stylesheet,
            'chapters': chapter_rows,
            'chapter_context': chapter_context,
            'toc_entries': self.toc_entries,
            'capture_profiles': self.capture_profiles,
            'assets': {name: asset.media_type for name, asset in self.assets.items()},
            'cover': self.cover,
            'epub_path': self._epub_path,
            'epub_members': list(self._epub_members),
            'epub_chapters': self._epub_chapters,
        }
        with publish_file(path) as temporary, ZipFile(temporary, 'w', ZIP_DEFLATED) as project:
            project.writestr('project.json', json.dumps(data, ensure_ascii=False, indent=2))
            for name, asset in self.assets.items():
                project.writestr(f'assets/{archive_path(name)}', asset.data)
            for name, original in self._epub_members.items():
                project.writestr(f'original/{archive_path(name)}', original)

    @classmethod
    def load(cls, path: str | Path) -> Book:
        with ZipFile(path) as project:
            data = json.loads(project.read('project.json'))
            if data.get('version') != 1:
                raise ValueError('此版本无法打开该书籍工程')
            chapter_context = data.get('chapter_context', {})
            chapters = [Chapter(**row) for row in data['chapters']]
            for chapter in chapters:
                context = chapter_context.get(chapter.path, {})
                chapter.scan = context.get('scan')
                chapter.web = context.get('web')
            return cls(
                title=data['title'],
                author=data['author'],
                language=data['language'],
                identifier=data['identifier'],
                stylesheet=data.get('stylesheet', ''),
                chapters=chapters,
                toc_entries=data.get('toc_entries'),
                capture_profiles=data.get('capture_profiles', []),
                assets={
                    name: Asset(project.read(f'assets/{archive_path(name)}'), media)
                    for name, media in data['assets'].items()
                },
                cover=data['cover'],
                _epub_path=data.get('epub_path'),
                _epub_members={
                    name: project.read(f'original/{archive_path(name)}')
                    for name in data.get('epub_members', [])
                },
                _epub_chapters={
                    name: tuple(info) for name, info in data.get('epub_chapters', {}).items()
                },
            )
