"""Local project history, pinned files, recoverable drafts, and checkpoints."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from errno import EACCES, EAGAIN, EDEADLK
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import BinaryIO, Iterator
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
import json
import os
import re

from ebook_core import Book, publish_file

_RECENT_LIMIT = 12
_DRAFT_NAME = re.compile(r'draft-[0-9a-f]{32}\.bookproj\Z')


_CHECKPOINT_NAME = re.compile(r'checkpoint-[0-9a-f]{32}\.bookproj\Z')
_CHECKPOINT_METADATA = 'checkpoint.json'


@dataclass(frozen=True)
class Checkpoint:
    path: Path
    label: str
    created_at: datetime
    title: str
    chapter_count: int
    asset_count: int


def _default_directory() -> Path:
    if configured := os.environ.get('EBOOK_STUDIO_WORKSPACE'):
        return Path(configured)
    default = Path.home() / '.local' / 'share'
    return Path(os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_DATA_HOME') or default) / 'EbookStudio'


def _lock_file(path: Path, *, wait: bool) -> BinaryIO | None:
    """An open handle holds the lock. The OS releases it after a crash."""
    handle = path.open('a+b')
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            flags = fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB
            fcntl.flock(handle.fileno(), flags)
    except OSError as exc:
        handle.close()
        if not wait and exc.errno in {EACCES, EAGAIN, EDEADLK}:
            return None
        raise
    return handle


class Workspace:
    """Manage a shared recent list and a draft owned by this instance."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = (Path(directory) if directory is not None else _default_directory()).resolve()
        self._drafts = self.directory / 'drafts'
        self._drafts.mkdir(parents=True, exist_ok=True)
        self._drafts = self._drafts.resolve()
        if self._drafts.parent != self.directory:
            raise ValueError('草稿目录必须位于工作区内')
        self._recent_path = self.directory / 'recent.json'
        self._pinned_path = self.directory / 'pinned.json'
        self.own_draft = self._drafts / f'draft-{uuid4().hex}.bookproj'
        self._session_lock = self.own_draft.with_suffix('.lock')
        self._owner = _lock_file(self._session_lock, wait=True)
        self._writing = Lock()

    @contextmanager
    def _recent_lock(self) -> Iterator[None]:
        with _lock_file(self.directory / 'recent.lock', wait=True):
            yield

    def _read_paths(self, store: Path) -> list[Path]:
        try:
            records = json.loads(store.read_text(encoding='utf-8'))
        except (FileNotFoundError, ValueError):
            return []
        if not isinstance(records, list):
            return []
        return list(dict.fromkeys(
            path for record in records
            if isinstance(record, str) and (path := Path(record)).is_absolute()
        ))

    def _read_recent(self) -> list[Path]:
        return [path for path in self._read_paths(self._recent_path) if path.is_file()][:_RECENT_LIMIT]

    def _save_paths(self, store: Path, records: list[Path]) -> None:
        with publish_file(store) as temporary:
            data = [str(path) for path in records]
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    def recent(self) -> list[Path]:
        """List available projects from most to least recently used."""
        with self._recent_lock():
            return self._read_recent()

    def remember(self, path: str | Path) -> None:
        path = Path(path).resolve()
        if not path.is_file():
            return
        with self._recent_lock():
            records = [path, *(record for record in self._read_recent() if record != path)]
            self._save_paths(self._recent_path, records[:_RECENT_LIMIT])

    def forget(self, path: str | Path) -> None:
        path = Path(path).resolve()
        with self._recent_lock():
            self._save_paths(self._recent_path, [record for record in self._read_recent() if record != path])

    def pinned(self) -> list[Path]:
        """Keep explicit pins even when their paths are temporarily unavailable."""
        with self._recent_lock():
            return self._read_paths(self._pinned_path)

    def set_pinned(self, path: Path, enabled: bool) -> None:
        path = path.resolve()
        with self._recent_lock():
            records = self._read_paths(self._pinned_path)
            if enabled and path not in records:
                self._save_paths(self._pinned_path, [path, *records])
            elif not enabled and path in records:
                self._save_paths(self._pinned_path, [record for record in records if record != path])

    def candidates(self) -> list[Path]:
        """List detached project drafts, latest save first."""
        detached = []
        for path in self._drafts.glob('draft-*.bookproj'):
            if path == self.own_draft or not _DRAFT_NAME.fullmatch(path.name) or path.is_symlink():
                continue
            probe = _lock_file(path.with_suffix('.lock'), wait=False)
            if probe is None:
                continue
            with probe:
                try:
                    with ZipFile(path) as archive:
                        archive.getinfo('project.json')
                    detached.append((path.stat().st_mtime_ns, path))
                except (BadZipFile, FileNotFoundError, KeyError):
                    # An incomplete or damaged archive is unavailable for recovery.
                    continue
        detached.sort(key=lambda pair: (pair[0], pair[1].name), reverse=True)
        return [path for _, path in detached]

    def write_draft(self, book: Book) -> Path:
        """Save an independent snapshot, suitable for calling on a worker thread."""
        with self._writing:
            if self._owner is None:
                raise RuntimeError('工作区会话已经关闭')
            book.save(self.own_draft)
            return self.own_draft

    def discard_draft(self, path: str | Path | None = None) -> bool:
        """Remove this session's draft or a detached draft inside this workspace."""
        if path is None:
            target = self.own_draft
        else:
            selected = Path(path)
            if selected.is_symlink():
                return False
            target = selected.resolve()
        if target.parent != self._drafts or not _DRAFT_NAME.fullmatch(target.name):
            return False
        if target == self.own_draft:
            with self._writing:
                if self._owner is not None:
                    target.unlink(missing_ok=True)
                    return True
        if not target.is_file():
            return False
        probe = _lock_file(target.with_suffix('.lock'), wait=False)
        if probe is None:
            return False
        with probe:
            target.unlink(missing_ok=True)
        return True

    def _checkpoint_directory(self, book: Book) -> Path:
        digest = sha256(book.identifier.encode('utf-8')).hexdigest()
        directory = self.directory / 'checkpoints' / digest
        if directory.resolve() != directory:
            raise ValueError('检查点目录必须位于工作区内')
        return directory

    def save_checkpoint(self, book: Book, label: str) -> Checkpoint:
        """Publish a complete project and its checkpoint information together."""
        directory = self._checkpoint_directory(book)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'checkpoint-{uuid4().hex}.bookproj'
        checkpoint = Checkpoint(
            path, label.strip() or '手动检查点', datetime.now(timezone.utc),
            book.title, len(book.chapters), len(book.assets),
        )
        metadata = {
            'label': checkpoint.label,
            'created_at': checkpoint.created_at.isoformat(),
            'title': checkpoint.title,
            'chapter_count': checkpoint.chapter_count,
            'asset_count': checkpoint.asset_count,
        }
        with publish_file(path, overwrite=False) as temporary:
            book.save(temporary)
            with ZipFile(temporary, 'a', ZIP_DEFLATED) as archive:
                archive.writestr(_CHECKPOINT_METADATA, json.dumps(metadata, ensure_ascii=False))
        return checkpoint

    def checkpoints(self, book: Book) -> list[Checkpoint]:
        """List this book's available checkpoints, newest first."""
        checkpoints = []
        for path in self._checkpoint_directory(book).glob('checkpoint-*.bookproj'):
            if path.is_symlink() or not _CHECKPOINT_NAME.fullmatch(path.name):
                continue
            try:
                with ZipFile(path) as archive:
                    metadata = json.loads(archive.read(_CHECKPOINT_METADATA))
                created_at = datetime.fromisoformat(metadata['created_at']).astimezone(timezone.utc)
                checkpoints.append(Checkpoint(
                    path, metadata['label'], created_at, metadata['title'],
                    metadata['chapter_count'], metadata['asset_count'],
                ))
            except (BadZipFile, FileNotFoundError, KeyError, TypeError, ValueError):
                continue
        return sorted(checkpoints, key=lambda item: (item.created_at, item.path.name), reverse=True)

    def load_checkpoint(self, book: Book, checkpoint: Checkpoint) -> Book:
        path = checkpoint.path
        if (path.is_symlink() or path.resolve().parent != self._checkpoint_directory(book)
                or not _CHECKPOINT_NAME.fullmatch(path.name)):
            raise ValueError('此检查点不在当前书稿的工作区内')
        restored = Book.load(path)
        if restored.identifier != book.identifier:
            raise ValueError('此检查点属于其他书稿')
        return restored

    def close(self) -> None:
        """Release ownership, leaving any saved snapshot available to recover."""
        with self._writing:
            if self._owner is not None:
                self._owner.close()
                self._owner = None
                if not self.own_draft.exists():
                    self._session_lock.unlink(missing_ok=True)
