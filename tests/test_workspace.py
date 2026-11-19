from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ebook_core import Asset, Book, Chapter
from workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
    def workspace(self, root):
        workspace = Workspace(root)
        self.addCleanup(workspace.close)
        return workspace

    def test_recent_projects_share_order_and_ignore_missing_files(self):
        first, second = self.workspace(self.root), self.workspace(self.root)
        a, b = self.root / '甲.bookproj', self.root / '乙.bookproj'
        for file in (a, b):
            file.touch()
        first.remember(a)
        second.remember(b)
        first.remember(a)
        self.assertEqual(second.recent(), [a.resolve(), b.resolve()])
        a.unlink()
        self.assertEqual(first.recent(), [b.resolve()])
        second.forget(b)
        self.assertEqual(first.recent(), [])

    def test_recent_list_recovers_from_damaged_json_on_next_remember(self):
        workspace = self.workspace(self.root)
        (self.root / 'recent.json').write_bytes(b'{broken')
        book = self.root / 'recover.bookproj'
        book.touch()
        self.assertEqual(workspace.recent(), [])
        workspace.remember(book)
        self.assertEqual(workspace.recent(), [book.resolve()])

    def test_explicit_pin_survives_recent_limit_and_temporary_file_loss(self):
        first, second = self.workspace(self.root), self.workspace(self.root)
        book = self.root / '长期书稿.bookproj'
        book.touch()
        first.remember(book)
        first.set_pinned(book, True)
        for index in range(13):
            newer = self.root / f'{index}.epub'
            newer.touch()
            second.remember(newer)
        self.assertNotIn(book.resolve(), second.recent())
        self.assertEqual(second.pinned(), [book.resolve()])

        book.unlink()
        self.assertEqual(first.pinned(), [book.resolve()])
        second.set_pinned(book, False)
        self.assertEqual(first.pinned(), [])

    def test_other_open_windows_cannot_list_or_discard_live_drafts(self):
        first, second = self.workspace(self.root), self.workspace(self.root)
        first.write_draft(Book('甲的草稿'))
        self.assertEqual(second.candidates(), [])
        self.assertFalse(second.discard_draft(first.own_draft))
        self.assertEqual(Book.load(first.own_draft).title, '甲的草稿')
        first.close()
        self.assertEqual(second.candidates(), [first.own_draft])
        outside = self.root / first.own_draft.name
        outside.write_bytes(b'outside')
        self.assertFalse(second.discard_draft(outside))
        self.assertTrue(outside.exists())
        self.assertTrue(second.discard_draft(first.own_draft))
        self.assertEqual(second.candidates(), [])

    def test_failed_draft_replace_preserves_previous_archive(self):
        writer, reader = self.workspace(self.root), self.workspace(self.root)
        book = Book('草稿', chapters=[Chapter('第一章', '<p>测试。</p>')])
        writer.write_draft(book)
        original = writer.own_draft.read_bytes()
        book.assets['../outside.png'] = Asset(b'bad path', 'image/png')
        with self.assertRaises(ValueError):
            writer.write_draft(book)
        self.assertEqual(writer.own_draft.read_bytes(), original)
        self.assertFalse(list(writer.own_draft.parent.glob('*.tmp')))
        writer.close()
        damaged = writer.own_draft.parent / ('draft-' + 'a' * 32 + '.bookproj')
        damaged.write_bytes(b'interrupted archive')
        self.assertEqual(reader.candidates(), [writer.own_draft])
        self.assertEqual(Book.load(writer.own_draft).chapters[0].title, '第一章')


if __name__ == '__main__':
    unittest.main()
