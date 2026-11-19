"""Offline spell checking for an English lexicon and the user's editorial vocabulary."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from itertools import islice
from pathlib import Path
import re
import sqlite3

from spylls.hunspell import Dictionary

from book_search import _readable_text


def word_key(word: str) -> str:
    return word.casefold().replace("’", "'")


@lru_cache(maxsize=1)
def english_dictionary() -> Dictionary:
    return Dictionary.from_files(str(Path(__file__).with_name("dictionaries") / "en_US"))


@lru_cache(maxsize=32768)
def known_english(word: str) -> bool:
    return english_dictionary().lookup(word.replace("’", "'"))


@lru_cache(maxsize=512)
def english_suggestions(word: str) -> tuple[str, ...]:
    return tuple(islice(english_dictionary().suggest(word.replace("’", "'")), 4))


class PersonalDictionary:
    def __init__(self, workspace: Path):
        self.file = workspace / "personal-dictionary.sqlite3"
        with closing(sqlite3.connect(self.file)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS terms (key TEXT PRIMARY KEY, word TEXT NOT NULL, suggestion TEXT NOT NULL)")

    def entries(self) -> list[tuple[str, str]]:
        with closing(sqlite3.connect(self.file)) as db:
            return db.execute("SELECT word, suggestion FROM terms ORDER BY key").fetchall()

    def save(self, word: str, suggestion: str, *, previous: tuple[str, str] | None = None) -> None:
        word, suggestion = word.strip(), suggestion.strip()
        if not word:
            raise ValueError("请输入词语。")
        if suggestion and suggestion == word:
            raise ValueError("建议写法与词语相同。认可此词时请留空建议写法。")
        with closing(sqlite3.connect(self.file)) as db, db:
            key = word_key(word)
            if previous is None:
                changed = db.execute("INSERT OR IGNORE INTO terms VALUES (?, ?, ?)", (key, word, suggestion))
            else:
                changed = db.execute("UPDATE terms SET word=?, suggestion=? WHERE key=? AND word=? AND suggestion=?",
                                     (word, suggestion, key, *previous))
            if not changed.rowcount:
                raise ValueError("词条已有变化，请刷新词典后核对。")

    def remove(self, word: str, suggestion: str) -> None:
        with closing(sqlite3.connect(self.file)) as db, db:
            if not db.execute("DELETE FROM terms WHERE key=? AND word=? AND suggestion=?",
                              (word_key(word), word, suggestion)).rowcount:
                raise ValueError("词条已有变化，请刷新词典后核对。")


@dataclass(frozen=True)
class Finding:
    chapter: int
    title: str
    word: str
    context: str
    count: int
    custom: str | None


# Boundaries apply only to Latin words, so Chinese terms can be found within a sentence.
def term_expression(word: str) -> str:
    latin = r"[A-Za-zÀ-ÖØ-öø-ÿ'’]"
    pattern = re.escape(word).replace("'", "['’]").replace(r"\ ", r"\s+")
    first = bool(re.fullmatch(latin, word[0]))
    last = bool(re.fullmatch(latin, word[-1]))
    return (f"(?<!{latin})" if first else "") + pattern + (f"(?!{latin})" if last else "")


_ENGLISH = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+(?:[’'][A-Za-zÀ-ÖØ-öø-ÿ]+)*")
_URL = re.compile(r"(?:https?://|www\.)[a-zA-Z0-9:/?&%#._=~+@-]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.I)


def scan(book, entries: list[tuple[str, str]], cancelled=lambda: False) -> list[Finding]:
    glossary = {word_key(word): suggestion for word, suggestion in entries}
    alternatives = [term_expression(word_key(word)) for word, _ in sorted(entries, key=lambda pair: -len(pair[0]))]
    custom = re.compile("|".join(alternatives), re.I) if alternatives else None
    results = []
    for index, chapter in enumerate(book.chapters):
        if cancelled():
            break
        text = _readable_text(chapter.html)
        counts = Counter()
        samples = {}

        def record(match, suggestion):
            word = match[0]
            key = (word, suggestion)
            counts[key] += 1
            if key not in samples:
                left, right = match.span()
                samples[key] = text[max(0, left - 24):min(len(text), right + 24)]

        excluded = [m.span() for m in _URL.finditer(text)]
        if custom:
            for match in custom.finditer(text):
                suggestion = glossary.get(word_key(" ".join(match[0].split())), "")
                excluded.append(match.span())
                if suggestion and match[0] != suggestion:
                    record(match, suggestion)
        excluded.sort()
        skip = 0
        for match in _ENGLISH.finditer(text):
            if cancelled():
                return []
            while skip < len(excluded) and excluded[skip][1] <= match.start():
                skip += 1
            if skip < len(excluded) and excluded[skip][0] < match.end():
                continue
            if not known_english(match[0]):
                record(match, None)
        for (word, suggestion), snippet in samples.items():
            results.append(Finding(index, chapter.title, word, snippet, counts[(word, suggestion)], suggestion))
    return results
