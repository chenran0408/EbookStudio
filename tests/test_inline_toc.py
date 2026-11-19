"""Reading-order table of contents round trips and navigation integrity."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

from lxml import etree

from ebook_core import Asset, Book, Chapter, resolve_href
from epub_io import OPF, XHTML, current_toc, open_epub, write_epub
from inline_toc import prepare_inline_toc

X = '{' + XHTML + '}'
O = '{' + OPF + '}'


class InlineTocTests(TestCase):
    def test_selective_nested_navigation_roundtrip_and_update(self):
        first = Chapter('目录', '<p>原书手写的一页。</p>', path='text/contents.xhtml')
        second = Chapter('观察记录', '<h2 id="同名">同名</h2>', path='正文/第 1 篇.xhtml', level=2)
        third = Chapter('同名', '<p id="小节">数据</p>', path='正文/第 2 篇.xhtml', level=2)
        book = Book('样书', chapters=[first, second, third])
        book.toc_entries = [
            dict(title='第一期', path=first.path, fragment='', level=1),
            dict(title='同名', path=second.path, fragment='同名', level=2),
            dict(title='同名', path=third.path, fragment='小节', level=3),
        ]
        edit = prepare_inline_toc(book)
        self.assertTrue(edit.created)
        self.assertIsNone(book.inline_toc_path)
        managed = edit.book.inline_toc_path
        self.assertEqual(managed, 'text/contents-2.xhtml')
        self.assertEqual(edit.book.chapters[1].html, first.html)
        self.assertFalse(prepare_inline_toc(edit.book).changed)
        with TemporaryDirectory() as folder:
            project = Path(folder) / 'sample.bookproj'
            epub = Path(folder) / 'readable.epub'
            original = Path(folder) / 'original.epub'
            write_epub(book, original)
            imported = open_epub(original)
            self.assertIsNone(imported.toc_entries)
            inserted = prepare_inline_toc(imported)
            write_epub(inserted.book, epub)
            reopened_entries = current_toc(open_epub(epub))
            self.assertEqual([e for e in reopened_entries if e['path'] != managed], current_toc(imported))
            edit.book.save(project)
            saved = Book.load(project)
            self.assertEqual(saved.inline_toc_path, managed)
            write_epub(saved, epub)
            with ZipFile(epub) as archive:
                opf = etree.fromstring(archive.read('package.opf'))
                items = {node.get('id'):resolve_href('package.opf',node.get('href'))
                         for node in opf.findall(f'{O}manifest/{O}item')}
                spine = [items[ref.get('idref')] for ref in opf.findall(f'{O}spine/{O}itemref')]
                self.assertEqual(spine, [managed, first.path, second.path, third.path])
                page = etree.fromstring(archive.read(managed))
                links = [a.get('href') for a in page.iter(X+'a')]
                self.assertTrue(page.find(f'.//{X}ol/{X}li/{X}ol/{X}li/{X}ol/{X}li/{X}a') is not None)
                self.assertEqual([(resolve_href(managed, link), unquote(urlsplit(link).fragment)) for link in links],
                                 [(e['path'], e['fragment']) for e in book.toc_entries])

            reopened = open_epub(epub)
            self.assertEqual(reopened.inline_toc_path, managed)
            self.assertFalse(prepare_inline_toc(reopened).changed)
            reopened.toc_entries = current_toc(reopened)
            reopened.toc_entries[-1]['title'] = '第二篇改名'
            update = prepare_inline_toc(reopened)
            self.assertTrue(update.changed)
            self.assertFalse(update.created)
            self.assertEqual(len(update.book.chapters), 4)
            self.assertIn('第二篇改名', update.book.chapters[0].html)
            self.assertNotIn('第二篇改名', reopened.chapters[0].html)
            self.assertFalse(prepare_inline_toc(update.book).changed)

    def test_cover_first_new_page_links_to_auxiliary_documents(self):
        book = Book(chapters=[
            Chapter('封面','<img alt="封面" src="../assets/cover.png"/>', path='text/cover.xhtml'),
            Chapter('第一章', '<p>正文</p>', path='text/one.xhtml'),
        ], assets={
            'assets/cover.png': Asset(b'cover','image/png'),
            'back/词汇.xhtml': Asset(b'<html/>','application/xhtml+xml'),
        }, cover='assets/cover.png')
        book.toc_entries = [dict(title='词汇表', path='back/词汇.xhtml', fragment='附录', level=1)]
        edit = prepare_inline_toc(book)
        self.assertEqual(edit.index, 1)
        root = etree.fromstring(edit.book.chapters[1].html.encode())
        link = next(link for link in root.iter(X+'a') if link.text == '词汇表')
        self.assertEqual(resolve_href(edit.book.inline_toc_path, link.get('href')), 'back/词汇.xhtml')

    def test_publisher_navigation_and_fixed_layout(self):
        book = Book(chapters=[Chapter('甲', '<p>甲</p>')])
        with TemporaryDirectory() as folder:
            epub = Path(folder) / 'seed.epub'
            write_epub(book, epub)
            imported = open_epub(epub)
        path = imported._epub_path
        package = etree.fromstring(imported._epub_members[path])
        nav = next(i for i in package.findall(f'{O}manifest/{O}item') if 'nav' in i.get('properties', '').split())
        ref = etree.SubElement(package.find(O+'spine'), O+'itemref', idref=nav.get('id'))
        imported._epub_members[path] = etree.tostring(package)
        with self.assertRaisesRegex(ValueError, '已经是可翻阅'):
            prepare_inline_toc(imported)
        ref.set('linear','no')
        imported._epub_members[path] = etree.tostring(package)
        self.assertTrue(prepare_inline_toc(imported).created)
        etree.SubElement(package.find(O+'metadata'),O+'meta',property='rendition:layout').text='pre-paginated'
        imported._epub_members[path] = etree.tostring(package)
        with self.assertRaisesRegex(ValueError, '固定版式'):
            prepare_inline_toc(imported)
