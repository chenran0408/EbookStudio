"""Local index of editable projects and EPUBs stored in their original locations."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing, contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Iterable
from zipfile import BadZipFile, ZipFile

from lxml.etree import LxmlError

from epub_io import read_epub_metadata


BOOK_FILTER = '电子书工程与 EPUB (*.bookproj *.epub)'
BOOK_READ_ERRORS = (BadZipFile, KeyError, LxmlError, OSError, RuntimeError, ValueError)


def _unique_names(values: Iterable[str]) -> tuple[str, ...]:
    names = {}
    for value in values:
        if name := value.strip():
            names.setdefault(name.casefold(), name)
    return tuple(names.values())


def author_names(value: str) -> tuple[str, ...]:
    return _unique_names(re.split(r'[;；、]', value))


def tag_names(value: str) -> tuple[str, ...]:
    return _unique_names(re.split(r'[;；\n]', value))


def read_book_metadata(path: Path) -> tuple[str, str, tuple[str, ...]]:
    if path.suffix.lower() == '.epub':
        title, creators, subjects = read_epub_metadata(path)
        return title, '；'.join(creators), _unique_names(subjects)
    if path.suffix.lower() != '.bookproj':
        raise ValueError('请选择 .bookproj 或 .epub 文件。')
    with ZipFile(path) as project:
        data = json.loads(project.read('project.json'))
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('无法识别此书稿工程。')
    title, author = data.get('title'), data.get('author')
    if not isinstance(title, str) or not isinstance(author, str):
        raise ValueError('书稿元数据无效。')
    return title.strip() or path.stem, author.strip(), ()


@dataclass(frozen=True)
class LibraryEntry:
    id: int
    path: Path
    title: str
    author: str
    tags: tuple[str, ...]

    @property
    def authors(self) -> tuple[str, ...]:
        return author_names(self.author)

    def matches(self, terms: list[str], author: str | None, tag: str | None) -> bool:
        if author is not None and author not in {name.casefold() for name in self.authors or ('',)}:
            return False
        if tag is not None and tag not in {name.casefold() for name in self.tags}:
            return False
        searchable = ' '.join((self.title, self.author, str(self.path), *self.tags)).casefold()
        return all(term in searchable for term in terms)


@dataclass(frozen=True)
class LibraryImport:
    added: int
    refreshed: int
    errors: tuple[tuple[Path, str], ...]


class BookLibrary:
    def __init__(self, workspace: Path) -> None:
        self.file = workspace / 'library.sqlite3'
        with self._connect() as database:
            database.executescript('''
                CREATE TABLE IF NOT EXISTS books (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    path_key TEXT NOT NULL UNIQUE,
                    file_title TEXT NOT NULL,
                    file_author TEXT NOT NULL,
                    local_title TEXT,
                    local_author TEXT
                );
                CREATE TABLE IF NOT EXISTS tags (
                    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                    tag_key TEXT NOT NULL,
                    label TEXT NOT NULL,
                    PRIMARY KEY (book_id, tag_key)
                );
            ''')

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.file)) as database, database:
            database.execute('PRAGMA foreign_keys = ON')
            yield database

    def entries(self) -> list[LibraryEntry]:
        with self._connect() as database:
            database.execute('BEGIN')
            rows = database.execute('''
                SELECT id, path, COALESCE(local_title, file_title), COALESCE(local_author, file_author)
                FROM books
            ''').fetchall()
            tags = defaultdict(list)
            for book_id, label in database.execute('SELECT book_id, label FROM tags ORDER BY tag_key'):
                tags[book_id].append(label)
        return [LibraryEntry(book_id, Path(path), title, author, tuple(tags[book_id]))
                for book_id, path, title, author in rows]

    def add(self, paths: Iterable[Path]) -> LibraryImport:
        records, errors = {}, []
        for source in paths:
            try:
                path = source.resolve(strict=True)
                key = os.path.normcase(str(path))
                if key not in records:
                    records[key] = (path, *read_book_metadata(path))
            except BOOK_READ_ERRORS as exc:
                errors.append((source, str(exc)))
        added = 0
        with self._connect() as database:
            for key, (path, title, author, tags) in records.items():
                row = database.execute('''
                    INSERT INTO books(path, path_key, file_title, file_author) VALUES (?, ?, ?, ?)
                    ON CONFLICT(path_key) DO NOTHING
                ''', (str(path), key, title, author))
                if row.rowcount:
                    added += 1
                    self._add_tags(database, [row.lastrowid], tags)
                else:
                    database.execute('UPDATE books SET path = ?, file_title = ?, file_author = ? WHERE path_key = ?',
                                     (str(path), title, author, key))
        return LibraryImport(added, len(records) - added, tuple(errors))

    @staticmethod
    def _add_tags(database, book_ids, tags) -> None:
        database.executemany('''
            INSERT OR IGNORE INTO tags(book_id, tag_key, label)
            SELECT id, ?, ? FROM books WHERE id = ?
        ''', ((tag.casefold(), tag, book_id) for book_id in book_ids for tag in tags))

    def edit(self, book_id: int, title: str, author: str, tags: tuple[str, ...]) -> None:
        title, author = title.strip(), author.strip()
        if not title:
            raise ValueError('请输入书名。')
        with self._connect() as database:
            result = database.execute('''
                UPDATE books SET local_title = NULLIF(?, file_title), local_author = NULLIF(?, file_author)
                WHERE id = ?
            ''', (title, author, book_id))
            if not result.rowcount:
                raise ValueError('这本书已移出书库。')
            database.execute('DELETE FROM tags WHERE book_id = ?', (book_id,))
            self._add_tags(database, [book_id], tags)

    def change_tags(self, book_ids: list[int], tags: tuple[str, ...], *, remove: bool = False) -> None:
        with self._connect() as database:
            if remove:
                database.executemany('DELETE FROM tags WHERE book_id = ? AND tag_key = ?',
                                     ((book_id, tag.casefold()) for book_id in book_ids for tag in tags))
            else:
                self._add_tags(database, book_ids, tags)

    def relink(self, book_id: int, source: Path) -> None:
        path = source.resolve(strict=True)
        title, author, _ = read_book_metadata(path)
        try:
            with self._connect() as database:
                result = database.execute('''
                    UPDATE books SET path = ?, path_key = ?, file_title = ?, file_author = ? WHERE id = ?
                ''', (str(path), os.path.normcase(str(path)), title, author, book_id))
                if not result.rowcount:
                    raise ValueError('这本书已移出书库。')
        except sqlite3.IntegrityError as exc:
            raise ValueError('此文件已在书库中。') from exc

    def remove(self, book_ids: Iterable[int]) -> None:
        with self._connect() as database:
            database.executemany('DELETE FROM books WHERE id = ?', ((book_id,) for book_id in book_ids))
