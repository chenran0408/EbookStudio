from copy import deepcopy
from unittest import TestCase

from checkpoint_compare import compare_books, excerpts
from ebook_core import Asset, Book, Chapter


class CheckpointCompareTests(TestCase):
    def test_edited_manuscript_compared_by_path(self):
        original = Book(title='原稿', chapters=[
            Chapter('同名', '<p>风起。船靠岸。雨停。</p>', path='spring.xhtml'),
            Chapter('同名', '<p>夏</p>', path='summer.xhtml'),
            Chapter('原卷首', path='old.xhtml')])
        original.assets['images/河.jpg'] = Asset(b'123', 'image/jpeg')
        current = deepcopy(original)
        current.chapters[0].html = '<p>风起。<b>船离岸。</b>雨停。</p>'
        current.chapters[1].html = '<p><i>夏</i></p>'
        current.chapters = [current.chapters[1], Chapter('新章', path='new.xhtml'), current.chapters[0]]
        current.author = '编者'
        current.stylesheet = 'p { color: black }'
        current.assets['images/河.jpg'] = Asset(b'456', 'image/jpeg')
        changes = compare_books(original, current)
        by_kind = {row.kind: row for row in changes}
        sentence = by_kind['正文']
        self.assertEqual(sentence.path, 'spring.xhtml')
        self.assertEqual(by_kind['章节排版'].path, 'summer.xhtml')
        self.assertEqual(by_kind['章节排版'].before, by_kind['章节排版'].after)
        self.assertIn('<i>夏</i>', by_kind['章节排版'].source[1])
        self.assertEqual({(row.action, row.path) for row in changes if row.kind == '章节'},
                         {('新增','new.xhtml'), ('删除','old.xhtml')})
        self.assertIn('summer.xhtml', by_kind['章节顺序'].after.splitlines()[0])
        self.assertIn('新章', by_kind['目录'].after)
        self.assertNotEqual(by_kind['资源'].before, by_kind['资源'].after)
        self.assertEqual({row.name for row in changes if row.kind == '书稿资料'}, {'作者','书稿样式'})
        left, right = excerpts(sentence.before, sentence.after)
        self.assertEqual([s for changed,s in left if changed], ['船靠岸。'])
        self.assertEqual([s for changed,s in right if changed], ['船离岸。'])
        self.assertEqual(original.chapters[0].html, '<p>风起。船靠岸。雨停。</p>')

    def test_no_changes_and_insertions_do_not_move_existing_chapters(self):
        book = Book(chapters=[Chapter('开头'), Chapter('结尾')])
        other = deepcopy(book)
        self.assertEqual(compare_books(book, other), [])
        other.chapters.insert(0, Chapter('题词'))
        self.assertNotIn('章节顺序', {row.kind for row in compare_books(book, other)})
