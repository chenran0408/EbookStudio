"""Index readable chapter text from the local catalog and find literal phrases."""
from dataclasses import dataclass
from pathlib import Path
import re

from book_library import BOOK_READ_ERRORS, BookLibrary
from book_readable import readable_sections
from ebook_core import Book
from epub_io import current_toc, open_epub


PAGE_SIZE = 100
_ISSUE = re.compile(r'(?:(?P<year>(?:19|20)\d{2})\s*年\s*)?第\s*(?P<number>[0-9０-９一二三四五六七八九十百零〇两]+)\s*期')
_GAP = re.compile(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])')


def phrase_text(text):
    return _GAP.sub('', ' '.join(text.split()))


def _issue(label):
    matches = list(_ISSUE.finditer(label))
    if len(matches) != 1:
        return ''
    match = matches[0]
    return (f"{match['year']}年" if match['year'] else '') + f"第{match['number']}期"


def indexed_chapters(path):
    book = Book.load(path) if path.suffix.lower() == '.bookproj' else open_epub(path)
    # Annual volumes commonly put the issue on a parent TOC item, not on every article.
    # A range in an anthology's filename cannot identify a specific issue.
    book_issue = next((issue for label in (book.title, path.stem)
                       if not re.search('合订|合集|合刊', label) and (issue := _issue(label))), '')
    stack = []
    chapter_issues, anchors = {}, {}
    for entry in current_toc(book):
        level = entry['level']
        while stack and stack[-1][0] >= level:
            stack.pop()
        label = entry['title'].strip()
        match = _ISSUE.search(label)
        # A heading of the form “回望第 3 期的讨论” is an article, not an issue divider.
        divider = bool(match and not label[match.end():].strip()
                       and (not label[:match.start()].strip()
                            or re.fullmatch(r'《[^》]+》', label[:match.start()].strip())))
        issue = _issue(label) if divider else (stack[-1][1] if stack else book_issue)
        stack.append((level, issue))
        if entry['fragment']:
            fragment = entry['fragment']
            anchors.setdefault(entry['path'], {})[fragment] = (issue, label, fragment)
        else:
            chapter_issues.setdefault(entry['path'], issue)
    position = 0
    for chapter_number, chapter in enumerate(book.chapters, 1):
        chapter_issue = chapter_issues.get(chapter.path, book_issue)
        boundaries = anchors.get(chapter.path, {})
        for section, text in readable_sections(chapter.html, boundaries):
            issue, label, fragment = section or (chapter_issue, chapter.title or '未命名章节', '')
            yield position, chapter.path, fragment, label, issue, phrase_text(text), chapter_number
            position += 1


@dataclass(frozen=True)
class TextHit:
    path: Path
    book_title: str
    chapter_path: str
    fragment: str
    chapter_title: str
    issue: str
    snippet: str
    size: int
    modified: int
    chapter_number: int | None = None

    def current(self):
        info = self.path.stat()
        return (info.st_size, info.st_mtime_ns) == (self.size, self.modified)


@dataclass(frozen=True)
class RefreshResult:
    updated: int
    reused: int
    errors: tuple[str, ...]


class LibraryTextIndex:
    def __init__(self, library: BookLibrary):
        self.library = library
        with library._connect() as database:
            database.executescript("""
                CREATE TABLE IF NOT EXISTS text_books (
                    book_id INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
                    source TEXT NOT NULL, size INTEGER NOT NULL, modified INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS text_chapters (
                    id INTEGER PRIMARY KEY,
                    book_id INTEGER NOT NULL REFERENCES text_books(book_id) ON DELETE CASCADE,
                    position INTEGER NOT NULL, path TEXT NOT NULL, fragment TEXT NOT NULL, title TEXT NOT NULL,
                    issue TEXT NOT NULL, body TEXT NOT NULL, chapter_number INTEGER, UNIQUE(book_id, position)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS text_fts USING fts5(
                    body, content='text_chapters', content_rowid='id', tokenize='trigram'
                );
                CREATE TRIGGER IF NOT EXISTS text_insert AFTER INSERT ON text_chapters BEGIN
                    INSERT INTO text_fts(rowid, body) VALUES (new.id, new.body);
                END;
                CREATE TRIGGER IF NOT EXISTS text_delete AFTER DELETE ON text_chapters BEGIN
                    INSERT INTO text_fts(text_fts, rowid, body) VALUES ('delete', old.id, old.body);
                END;
            """)

            # Indexes created before 0.9 have one row per TOC section, so their
            # section position cannot double as the spine's chapter number.
            legacy_column = "SELECT 1 FROM pragma_table_info('text_chapters') WHERE name='chapter_number'"
            if database.execute(legacy_column).fetchone() is None:
                database.execute('BEGIN IMMEDIATE')
                if database.execute(legacy_column).fetchone() is None:
                    database.execute('ALTER TABLE text_chapters ADD COLUMN chapter_number INTEGER')

    def _sources(self):
        with self.library._connect() as conn:
            return conn.execute("""
                SELECT books.id, books.path, t.source, t.size, t.modified,
                       (SELECT c.chapter_number IS NULL FROM text_chapters c
                        WHERE c.book_id=books.id ORDER BY c.position LIMIT 1)
                FROM books LEFT JOIN text_books t ON t.book_id=books.id ORDER BY books.id
            """).fetchall()

    def availability(self):
        ready, unavailable = set(), []
        for book_id, source, indexed_source, size, modified, legacy in self._sources():
            try:
                info = Path(source).stat()
            except FileNotFoundError:
                state, message = 'missing', '找不到原文件，请在桌面书库重新定位。'
            except OSError as exc:
                state, message = 'unreadable', str(exc)
            else:
                if indexed_source is None or legacy:
                    state, message = 'unindexed', '需要建立或升级正文索引，请调用 ebook_library_index。'
                elif source != indexed_source or (info.st_size, info.st_mtime_ns) != (size, modified):
                    state, message = 'stale', '原文件已变化，请调用 ebook_library_index 刷新。'
                else:
                    ready.add(book_id)
                    continue
            unavailable.append(dict(source=source, state=state, message=message))
        return ready, unavailable

    def refresh(self, progress=None, cancelled=None):
        rows = self._sources()
        updated = reused = 0
        errors = []
        for count, (book_id, raw_path, cached_path, size, modified, legacy) in enumerate(rows, 1):
            if cancelled and cancelled():
                break
            path = Path(raw_path)
            if progress:
                progress(count, len(rows), path.name)
            try:
                info = path.stat()
                stamp = (info.st_size, info.st_mtime_ns)
                if not legacy and raw_path == cached_path and stamp == (size, modified):
                    reused += 1
                    continue
                chapters = list(indexed_chapters(path))
                with self.library._connect() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    if not conn.execute('SELECT 1 FROM books WHERE id=? AND path=?', (book_id, raw_path)).fetchone():
                        continue
                    info = path.stat()
                    if stamp != (info.st_size, info.st_mtime_ns):
                        raise ValueError('读取时文件发生变化，请刷新索引。')
                    conn.execute('DELETE FROM text_books WHERE book_id=?', (book_id,))
                    conn.execute('INSERT INTO text_books VALUES (?,?,?,?)', (book_id, raw_path, *stamp))
                    conn.executemany("""
                        INSERT INTO text_chapters(book_id, position, path, fragment, title, issue, body, chapter_number)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, ((book_id, *row) for row in chapters))
                updated += 1
            except (*BOOK_READ_ERRORS, TypeError, AttributeError) as exc:
                with self.library._connect() as conn:
                    conn.execute('DELETE FROM text_books WHERE book_id=? AND source=? AND size=? AND modified=?',
                                 (book_id, cached_path, size, modified))
                errors.append(f'{path.name} · {exc}')
        return RefreshResult(updated, reused, tuple(errors))

    def find(self, query, offset=0, *, limit=PAGE_SIZE, available_ids=None):
        phrase = phrase_text(query)
        if not phrase:
            return [], False
        # The trigram tokenizer finds Chinese substrings without word segmentation.
        # One or two characters have no trigram, so search the saved chapter text directly.
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        if len(phrase) >= 3:
            where = 'c.id IN (SELECT rowid FROM text_fts WHERE text_fts MATCH ?)'
            parameter = '"' + phrase.replace('"', '""') + '"'
        else:
            where = 'contains_phrase(c.body)'
            parameter = None
        with self.library._connect() as conn:
            conn.create_function('contains_phrase', 1, lambda body: int(pattern.search(body) is not None))
            if available_ids is not None:
                conn.create_function('book_available', 1, available_ids.__contains__)
                where += ' AND book_available(b.id)'
            rows = conn.execute(f"""
                SELECT b.path, COALESCE(b.local_title,b.file_title), c.path, c.fragment, c.title,
                       c.issue, c.body, t.size, t.modified, c.chapter_number
                FROM text_chapters c JOIN text_books t ON t.book_id=c.book_id
                JOIN books b ON b.id=t.book_id AND b.path=t.source
                WHERE {where} ORDER BY b.id,c.position LIMIT ? OFFSET ?
            """, ([parameter] if parameter is not None else []) + [limit + 1, offset]).fetchall()
        hits = []
        for path, title, chapter, fragment, label, issue, body, size, modified, chapter_number in rows[:limit]:
            match = pattern.search(body)
            if match:
                start, end = max(0, match.start()-30), min(len(body), match.end()+30)
                snippet = ('…' if start else '') + body[start:end] + ('…' if end < len(body) else '')
                hits.append(TextHit(Path(path), title, chapter, fragment, label, issue, snippet, size, modified,
                                    chapter_number))
        return hits, len(rows) > limit
