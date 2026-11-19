from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from book_library import BookLibrary, tag_names
from ebook_core import Book, Chapter
from epub_io import write_epub


class BookLibraryTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.library = BookLibrary(self.root)

    def project(self, filename, title, author):
        path = self.root / filename
        Book(title=title, author=author, chapters=[Chapter('第一章', '<p>书稿内容。</p>')]).save(path)
        return path

    def test_duplicate_titles_filter_by_authors_and_tags_across_instances(self):
        first = self.project('a.bookproj', '潮汐日记', '林海；Clara')
        second = self.project('b.bookproj', '潮汐日记', '孟宇')
        self.assertEqual(self.library.add([first, second, first]).added, 2)
        entries = {entry.path: entry for entry in self.library.entries()}
        self.library.change_tags([entries[first].id, entries[second].id], tag_names('旅行；待校对'))
        self.library.change_tags([entries[first].id], tag_names('待校对'), remove=True)
        other = BookLibrary(self.root)
        entries = other.entries()
        self.assertEqual([entry.path for entry in entries if entry.matches(['潮汐'], 'clara', '旅行')], [first])
        self.assertEqual([entry.path for entry in entries if entry.matches([], '孟宇', '待校对')], [second])
        self.assertEqual([entry.path for entry in entries if entry.matches(['b.bookproj'], None, None)], [second])

    def test_readding_updates_file_metadata_and_preserves_catalog_preferences(self):
        path = self.project('书稿.bookproj', '山谷', '旧作者')
        self.library.add([path])
        entry, = self.library.entries()
        original_hash = sha256(path.read_bytes()).digest()
        self.library.edit(entry.id, '山谷校订本', '旧作者', ('校对',))
        self.assertEqual(sha256(path.read_bytes()).digest(), original_hash)
        self.project(path.name, '山谷新版', '新作者')
        result = self.library.add([path])
        self.assertEqual((result.added, result.refreshed), (0, 1))
        updated, = self.library.entries()
        self.assertEqual((updated.id, updated.title, updated.author, updated.tags),
                         (entry.id, '山谷校订本', '新作者', ('校对',)))

    def test_missing_path_can_be_relinked_without_losing_tags(self):
        missing = self.project('旧书.bookproj', '山中', '苏禾')
        other = self.project('另一本.bookproj', '街巷', '陈林')
        self.library.add([missing, other])
        entry = next(entry for entry in self.library.entries() if entry.path == missing)
        self.library.change_tags([entry.id], ('保留',))
        moved = missing.rename(self.root / '已移动.bookproj')
        self.assertFalse(entry.path.is_file())
        with self.assertRaisesRegex(ValueError, '已在书库'):
            self.library.relink(entry.id, other)
        self.library.relink(entry.id, moved)
        updated = next(row for row in self.library.entries() if row.id == entry.id)
        self.assertEqual((updated.path, updated.tags), (moved, ('保留',)))
        self.library.remove([entry.id])
        self.assertTrue(moved.is_file())
        self.assertEqual([row.path for row in self.library.entries()], [other])

    def test_epub_metadata_reads_title_multiple_creators_and_subjects(self):
        valid = self.root / '诗集.epub'
        write_epub(Book(title='夜航', author='李知', chapters=[Chapter('江边', '<p>岸上有树。</p>')]), valid)
        coauthored = self.root / '合著.epub'
        with ZipFile(valid) as source, ZipFile(coauthored, 'w') as target:
            for name in source.namelist():
                data = source.read(name)
                if name.endswith('.opf'):
                    index = data.index(b'</dc:creator>') + len(b'</dc:creator>')
                    data = data[:index] + ('<dc:creator>郑溪</dc:creator>'
                                          '<dc:subject>诗歌</dc:subject>'
                                          '<dc:subject>创作</dc:subject>').encode() + data[index:]
                target.writestr(name, data)
        self.assertEqual(self.library.add([coauthored]).added, 1)
        entry, = self.library.entries()
        self.assertEqual((entry.title, entry.authors, set(entry.tags)), ('夜航', ('李知', '郑溪'), {'诗歌', '创作'}))
        self.assertTrue(entry.matches(['合著.epub'], '郑溪', '诗歌'))
        self.library.change_tags([entry.id], ('诗歌',), remove=True)
        self.library.add([coauthored])
        self.assertEqual(self.library.entries()[0].tags, ('创作',))

    def test_partial_import_keeps_readable_books_and_lists_failures(self):
        broken = self.root / '未读取.epub'
        broken.write_bytes(b'not an epub')
        valid = self.project('有效.bookproj', '日记', '')
        result = self.library.add([valid, broken])
        self.assertEqual(result.added, 1)
        self.assertEqual([path for path, _ in result.errors], [broken])
        entry, = self.library.entries()
        self.assertTrue(entry.matches([], '', None))
        self.assertFalse(entry.matches([], '李知', None))


if __name__ == '__main__':
    unittest.main()
