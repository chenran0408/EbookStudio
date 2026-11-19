from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
import unittest

from lxml import etree
from PIL import Image

from book_resources import replacement_asset, resource_category, resource_references
from book_structure import edit_toc_entry, heading_toc, merge_chapters, move_toc_entry, split_chapter, split_choices, toc_destinations
from ebook_core import Asset, Book, Chapter, asset_href, resolve_href
from epub_io import EPUB, OPF, XHTML, current_toc, open_epub, write_epub


X = f'{{{XHTML}}}'


def image_data(color='red', format='PNG'):
    with BytesIO() as output:
        Image.new('RGB', (12, 8), color).save(output, format)
        return output.getvalue()


def toc_links(archive):
    nav = etree.fromstring(archive.read('nav.xhtml'))
    toc = nav.find(f'.//{X}nav[@{{{EPUB}}}type="toc"]')
    return [(link.get('href'), link.text) for link in toc.iter(f'{X}a')]


class BookStructureTests(unittest.TestCase):
    def test_manual_toc_edit_creates_anchors_and_round_trips_without_rewriting_chapters(self):
        book = Book('手动目录', chapters=[
            Chapter('第一章', '<p id="heading-1">开场</p><h2>进入正题</h2>', path='one.xhtml'),
            Chapter('第二章', '<h2 id="现有">已有位置</h2>', path='two.xhtml'),
        ])
        target = toc_destinations(book, 0)[1]
        staged = edit_toc_entry(book, 0, ' 开始阅读 ', target, 0).book
        self.assertIsNone(book.toc_entries)
        self.assertNotIn('heading-1-2', book.chapters[0].html)
        self.assertEqual([chapter.title for chapter in staged.chapters], ['第一章', '第二章'])
        self.assertIn('id="heading-1-2"', staged.chapters[0].html)
        target = toc_destinations(staged, 1)[1]
        staged = edit_toc_entry(staged, 1, '原有位置', target, 1).book
        self.assertEqual(staged.chapters[1].html, book.chapters[1].html)
        with self.assertRaisesRegex(ValueError, '目录标题'):
            edit_toc_entry(staged, 1, ' ', target, 1)
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            project, epub = folder / 'Manual.bookproj', folder / 'Manual.epub'
            staged.save(project)
            write_epub(Book.load(project), epub)
            with ZipFile(epub) as archive:
                self.assertEqual(toc_links(archive), [('one.xhtml#heading-1-2', '开始阅读'),
                                                      ('two.xhtml#%E7%8E%B0%E6%9C%89', '原有位置')])

    def test_toc_branch_moves_keep_subentries_and_do_not_change_spine(self):
        chapters = [Chapter(title, '<p>正文</p>', level, path=f'{i}.xhtml')
                    for i, (title, level) in enumerate([('甲', 1), ('乙', 2), ('乙下一层', 3),
                                                        ('丙', 2), ('丁', 1), ('戊', 1)])]
        book = Book(chapters=chapters)
        edit = move_toc_entry(book, 1, 'outdent', 0)
        labels = lambda project: [(entry['title'], entry['level']) for entry in current_toc(project)]
        self.assertEqual(labels(edit.book), [('甲', 1), ('丙', 2), ('乙', 1), ('乙下一层', 2), ('丁', 1), ('戊', 1)])
        self.assertEqual(edit.toc_index, 2)
        edit = move_toc_entry(edit.book, 2, 'down', 0)
        self.assertEqual(labels(edit.book), [('甲', 1), ('丙', 2), ('丁', 1), ('乙', 1), ('乙下一层', 2), ('戊', 1)])
        edit = move_toc_entry(edit.book, 3, 'indent', 0)
        self.assertEqual(labels(edit.book), [('甲', 1), ('丙', 2), ('丁', 1), ('乙', 2), ('乙下一层', 3), ('戊', 1)])
        edit = move_toc_entry(edit.book, 2, 'up', 0)
        self.assertEqual(labels(edit.book), [('丁', 1), ('乙', 2), ('乙下一层', 3), ('甲', 1), ('丙', 2), ('戊', 1)])
        self.assertEqual(edit.book.chapters, chapters)
        self.assertIsNone(book.toc_entries)
        with self.assertRaisesRegex(ValueError, '无法'):
            move_toc_entry(book, 0, 'indent', 0)

    def test_heading_toc_round_trips_and_includes_later_chapters(self):
        draft = Book('一本书', chapters=[Chapter('第一章', '<h1>第一章</h1><h2>小节</h2><h3 id="附录">细节</h3>',
                                             path='chap.xhtml')])
        result = heading_toc(draft)
        self.assertIsNone(draft.toc_entries)
        self.assertNotIn('heading-2', draft.chapters[0].html)
        self.assertEqual([item['level'] for item in result.toc_entries], [1, 2, 3])
        result.chapters.append(Chapter('追加', '<p>后续内容。</p>', path='new.xhtml'))
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            bookproj, epub = folder / 'book.bookproj', folder / 'book.epub'
            result.save(bookproj)
            reloaded = Book.load(bookproj)
            self.assertEqual(reloaded.toc_entries, result.toc_entries)
            write_epub(reloaded, epub)
            with ZipFile(epub) as archive:
                self.assertEqual(toc_links(archive), [
                    ('chap.xhtml', '第一章'), ('chap.xhtml#heading-2', '小节'),
                    ('chap.xhtml#%E9%99%84%E5%BD%95', '细节'), ('new.xhtml', '追加'),
                ])
            reloaded.chapters[0].html = '<p>旧锚点已删除。</p>'
            with self.assertRaisesRegex(ValueError, '锚点'):
                write_epub(reloaded, folder / 'invalid.epub')

    def test_split_imported_chapter_preserves_nested_wrapper_styles_and_cross_links(self):
        main, cross = 'OPS/main/long.xhtml', 'OPS/extras/refs.xhtml'
        body = ('<article class="kept"><h1 id="intro">第一部分</h1>'
                '<p><a href="#detail">下半部分</a></p>'
                '<section class="box"><p>前半节。</p><h2 id="detail">后半节</h2>'
                '<p><a href="#intro">回到开头</a></p></section></article>')
        draft = Book('原书', chapters=[Chapter('第一部分', body, path=main),
                                     Chapter('其他章节', '<p><a href="../main/long.xhtml#detail">详见</a></p>', path=cross)],
                     stylesheet='.kept { text-align: right }')
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            before, after = folder / 'original.epub', folder / 'split.epub'
            write_epub(heading_toc(draft), before)
            imported = open_epub(before)
            self.assertIsNone(imported.toc_entries)
            choices = split_choices(imported, 0)
            self.assertEqual([choice.title for choice in choices], ['后半节'])
            edit = split_chapter(imported, 0, choices[0].index)
            self.assertEqual(len(imported.chapters), 2)
            self.assertEqual(len(edit.book.chapters), 3)
            first, second, _ = edit.book.chapters
            self.assertNotIn('id="detail"', first.html)
            self.assertIn('id="detail"', second.html)
            self.assertIn('class="kept"', second.html)
            self.assertIn('class="box"', second.html)
            self.assertGreaterEqual(edit.relinked, 3)
            write_epub(edit.book, after)
            with ZipFile(after) as archive:
                left = etree.fromstring(archive.read(first.path))
                right = etree.fromstring(archive.read(second.path))
                third = etree.fromstring(archive.read(cross))
                self.assertEqual(resolve_href(first.path, left.find(f'.//{X}a').get('href')), second.path)
                self.assertEqual(resolve_href(second.path, right.find(f'.//{X}a').get('href')), first.path)
                self.assertEqual(resolve_href(cross, third.find(f'.//{X}a').get('href')), second.path)
                self.assertEqual(right.find(f'.//{X}link').get('href'), left.find(f'.//{X}link').get('href'))
                self.assertIn((f'{asset_href(second.path)}#detail', '后半节'), toc_links(archive))

    def test_merge_imported_documents_rebases_assets_and_disambiguates_anchors(self):
        first, second, third = 'OPS/one/ch.xhtml', 'OPS/two/ch.xhtml', 'OPS/other.xhtml'
        picture = 'OPS/two/images/pic.png'
        draft = Book('原书', chapters=[
            Chapter('首章', '<h1 id="title">首章</h1><p>内容</p>', path=first),
            Chapter('次章', '<h1 id="title">次章</h1><p><img src="images/pic.png" alt="图"/> '
                    '<a href="#title">返回</a></p>', path=second),
            Chapter('参考', '<p><a href="two/ch.xhtml#title">看次章标题</a> '
                    '<a href="two/ch.xhtml">看整章</a></p>', path=third),
        ], assets={picture: Asset(image_data(), 'image/png')})
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            original, output = folder / 'original.epub', folder / 'merged.epub'
            write_epub(draft, original)
            imported = open_epub(original)
            edit = merge_chapters(imported, 0)
            self.assertEqual(len(imported.chapters), 3)
            self.assertEqual(len(edit.book.chapters), 2)
            self.assertEqual(current_toc(edit.book)[1]['path'], first)
            write_epub(edit.book, output)
            with ZipFile(output) as archive:
                self.assertNotIn(second, archive.namelist())
                merged = etree.fromstring(archive.read(first))
                info = etree.fromstring(archive.read(third))
                ids = set(merged.xpath('//@id'))
                self.assertIn('title', ids)
                self.assertIn('title-2', ids)
                self.assertEqual(merged.find(f'.//{X}a').get('href'), '#title-2')
                self.assertEqual(resolve_href(first, merged.find(f'.//{X}img').get('src')), picture)
                for link in info.iter(f'{X}a'):
                    self.assertEqual(resolve_href(third, link.get('href')), first)
                    self.assertIn(link.get('href').split('#')[1], ids)

    def test_fixed_layout_disallows_chapter_restructuring(self):
        draft = Book('固定分页', chapters=[Chapter('第一页', '<p>引言</p><h2>正文</h2>'), Chapter('第二页', '<p>结尾</p>')])
        with TemporaryDirectory() as temp:
            epub = Path(temp) / 'book.epub'
            write_epub(draft, epub)
            imported = open_epub(epub)
            package = etree.fromstring(imported._epub_members[imported._epub_path])
            metadata = package.find(f'{{{OPF}}}metadata')
            etree.SubElement(metadata, f'{{{OPF}}}meta', property='rendition:layout').text = 'pre-paginated'
            imported._epub_members[imported._epub_path] = etree.tostring(package)
            with self.assertRaisesRegex(ValueError, '固定版式'):
                split_chapter(imported, 0, 0)
            with self.assertRaisesRegex(ValueError, '固定版式'):
                merge_chapters(imported, 0)

    def test_resource_replacement_keeps_path_and_original_until_confirmation(self):
        image, css, font = 'images/red.png', 'styles/page.css', 'fonts/text.woff2'
        draft = Book('资源书', chapters=[Chapter('图片', '<p><img src="images/red.png" alt="图"/></p>', path='one.xhtml')],
                     assets={image: Asset(image_data(), 'image/png'),
                             css: Asset(b'body{background:url("../images/red.png")}', 'text/css'),
                             font: Asset(b'woff2', 'font/woff2')}, cover=image)
        refs = resource_references(draft)[image]
        self.assertEqual([ref.label for ref in refs], ['第 1 章 · 图片', css, '书籍封面'])
        self.assertEqual(refs[0].chapter, 0)
        self.assertEqual([resource_category(path, draft.assets[path]) for path in (image, css, font)],
                         ['图片', '样式表', '字体'])
        staged = replacement_asset(draft, image, image_data('blue'))
        self.assertEqual(staged.media_type, draft.assets[image].media_type)
        self.assertNotEqual(staged.data, draft.assets[image].data)
        with self.assertRaisesRegex(ValueError, 'PNG'):
            replacement_asset(draft, image, image_data('blue', 'JPEG'))
        with self.assertRaises(ValueError):
            replacement_asset(draft, font, b'other')


if __name__ == '__main__':
    unittest.main()
