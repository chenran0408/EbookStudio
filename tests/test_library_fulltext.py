from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from book_library import BookLibrary
from book_library_text import LibraryTextIndex, PAGE_SIZE
from ebook_core import Book, Chapter
from epub_io import write_epub


class LibraryTextTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.library = BookLibrary(self.folder)
        self.index = LibraryTextIndex(self.library)

    def test_same_titles_nested_issues_and_literal_chinese_phrases(self):
        annual = self.folder/'合订本.epub'
        single = self.folder/'单期.bookproj'
        chapters = [
            Chapter('2026年第1期','<p>卷首说明</p>'),
            Chapter('回望第8期的讨论','<p>风穿过<em>纸上</em>的小城。</p>',level=2),
            Chapter('2026年第2期','<p>卷首</p>'),
            Chapter('河畔','<p>风穿过纸上\n的小城。</p>',level=2),
        ]
        write_epub(Book(title='月刊合订本',chapters=chapters),annual)
        Book(title='月刊2025年第3期',chapters=[Chapter('附页','<p>风穿过纸上的小城。A%_B</p>')]).save(single)
        self.library.add([annual,single])
        for entry in self.library.entries():
            self.library.edit(entry.id,'同名月刊','',())
        self.assertEqual(self.index.refresh().updated,2)
        hits, more = self.index.find('风穿过纸上的小城。')
        self.assertFalse(more)
        self.assertEqual([(h.book_title,h.chapter_title,h.issue) for h in hits],
                         [('同名月刊','回望第8期的讨论','2026年第1期'),('同名月刊','河畔','2026年第2期'),
                          ('同名月刊','附页','2025年第3期')])
        self.assertEqual(len(self.index.find('%_')[0]),1)
        self.assertEqual(len(self.index.find('a%_b')[0]),1)
        self.assertEqual(self.index.find('没有的句子'),([],False))

    def test_two_issues_by_anchor_in_one_file_preserve_unknown_preface(self):
        path = self.folder/'合订.bookproj'
        chapter = Chapter('合订正文','<p>扉页针线</p><h2 id="one">春</h2><p>针线<em>还在</em>。</p>'
                          '<a name="two"></a><p>夏日针线。</p>')
        book = Book(title='文学合订',chapters=[chapter],toc_entries=[
            dict(title='合订正文',path=chapter.path,fragment='',level=1),
            dict(title='2025年第1期',path=chapter.path,fragment='one',level=2),
            dict(title='2025年第2期',path=chapter.path,fragment='two',level=2)])
        book.save(path)
        self.library.add([path]);self.index.refresh()
        hits,_=self.index.find('针线')
        self.assertEqual([(hit.issue,hit.chapter_title) for hit in hits],
                         [('', '合订正文'),('2025年第1期','2025年第1期'),('2025年第2期','2025年第2期')])
        self.assertEqual(self.index.find('针线还在')[0][0].chapter_path,chapter.path)

    def test_articles_within_one_issue_share_spine_file(self):
        path = self.folder / '一期两文.epub'
        chapter = Chapter('本期全文', '<p>卷首语</p><h2 id="春">春信</h2><p>桥上晨光。</p>'
                          '<h2 id="夏">夏至</h2><p>桥上暮色。</p>')
        book = Book(title='月刊2026年第6期', chapters=[chapter], toc_entries=[
            dict(title='本期全文', path=chapter.path, fragment='', level=1),
            dict(title='春信', path=chapter.path, fragment='春', level=2),
            dict(title='夏至', path=chapter.path, fragment='夏', level=2)])
        write_epub(book, path)
        self.library.add([path])
        self.index.refresh()
        hits, _ = self.index.find('桥上')
        self.assertEqual([(hit.issue, hit.chapter_title, hit.fragment) for hit in hits],
                         [('2026年第6期', '春信', '春'), ('2026年第6期', '夏至', '夏')])

    def test_old_catalog_persists_incrementally_and_skips_stale_files(self):
        p=self.folder/'原书.bookproj'
        book=Book(title='无期号的书',chapters=[Chapter('原章','<p>旧句的字样</p>')])
        book.save(p);self.library.add([p])
        self.assertEqual(self.index.refresh().updated,1)
        with patch('book_library_text.indexed_chapters',side_effect=AssertionError('unchanged file parsed')):
            self.assertEqual(LibraryTextIndex(BookLibrary(self.folder)).refresh().reused,1)
        hit=self.index.find('旧句')[0][0]
        self.assertEqual(hit.issue,'')
        book.chapters[0].html='<p>新内容，从现在开始。</p>'
        book.save(p)
        self.assertFalse(hit.current())
        self.assertEqual(self.index.refresh().updated,1)
        self.assertFalse(self.index.find('旧句')[0])
        new=self.index.find('从现在')[0][0]
        self.assertTrue(new.current())
        moved=p.rename(self.folder/'新位置.bookproj')
        self.assertEqual(len(self.index.refresh().errors),1)
        self.assertFalse(self.index.find('从现在')[0])
        entry,=self.library.entries()
        self.library.relink(entry.id,moved)
        self.index.refresh()
        self.assertEqual(self.index.find('现在')[0][0].path,moved)
        self.library.remove([entry.id])
        self.assertEqual(self.index.find('现在'),([],False))
        self.assertTrue(moved.is_file())

    def test_next_page_and_hidden_markup(self):
        p=self.folder/'多章.bookproj'
        book=Book(chapters=[Chapter(str(i),'<p>可见短句</p><style>.隐私{}</style><span hidden="">隐藏正文</span>')
                            for i in range(PAGE_SIZE+1)])
        book.save(p);self.library.add([p]);self.index.refresh()
        rows,more=self.index.find('短句')
        self.assertEqual(len(rows),PAGE_SIZE);self.assertTrue(more)
        rows,more=self.index.find('短句',PAGE_SIZE)
        self.assertEqual([r.chapter_title for r in rows],[str(PAGE_SIZE)]);self.assertFalse(more)
        self.assertFalse(self.index.find('隐藏正文')[0])
