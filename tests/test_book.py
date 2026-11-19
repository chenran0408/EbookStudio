from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_STORED, ZipFile
import unittest

from lxml import etree
from PIL import Image
from io import BytesIO

from ebook_core import Asset, Book, Chapter, asset_href, resolve_href
from epub_io import EPUB, XHTML, OPF, open_epub, write_epub


def image_bytes():
    with BytesIO() as output:
        Image.new('RGB', (12, 12), '#8e313b').save(output, 'PNG')
        return output.getvalue()


def rewrite_member(source, destination, name, change):
    with ZipFile(source) as before, ZipFile(destination, 'w') as after:
        for entry in before.infolist():
            data = before.read(entry)
            if entry.filename == name:
                node = etree.fromstring(data)
                change(node)
                data = etree.tostring(node.getroottree())
            after.writestr(entry, data)


class BookTests(unittest.TestCase):
    def test_project_to_offline_epub_with_hierarchical_navigation(self):
        cover = 'images/封面 图.png'
        first = Chapter('第一辑', '<h1>第一辑</h1>')
        article = Chapter('第一篇', '', level=2, path='text/articles/one.xhtml')
        article.html = f'<p>正文 &amp; <em>强调</em></p><img src="{asset_href(cover, article.path)}" alt="图">'
        book = Book('电子书', '作者', chapters=[first, article],
                    assets={cover: Asset(image_bytes(), 'image/png')}, cover=cover)
        with TemporaryDirectory() as temp:
            root = Path(temp)
            native = root / '书稿.bookproj'
            epub = root / '成书.epub'
            book.save(native)
            loaded = Book.load(native)
            self.assertEqual(loaded, book)
            write_epub(loaded, epub)
            with ZipFile(epub) as archive:
                info = archive.infolist()[0]
                self.assertEqual((info.filename, info.compress_type), ('mimetype', ZIP_STORED))
                nav = etree.fromstring(archive.read('nav.xhtml'))
                tree = nav.find(f'.//{{{XHTML}}}nav[@{{{EPUB}}}type="toc"]')
                children = tree.findall(f'{{{XHTML}}}ol/{{{XHTML}}}li/{{{XHTML}}}ol/{{{XHTML}}}li/{{{XHTML}}}a')
                self.assertEqual([(child.text, child.get('href')) for child in children],
                                 [('第一篇', article.path)])
                chapter = etree.fromstring(archive.read(article.path))
                image = chapter.find(f'.//{{{XHTML}}}img')
                self.assertEqual(resolve_href(article.path, image.get('src')), cover)
                self.assertEqual(archive.read(cover), book.assets[cover].data)

    def test_edit_existing_epub_preserves_other_chapter_and_css(self):
        first = Chapter('第一章', '<p>原正文。</p>')
        second = Chapter('第二章', '<p>只改第一章。</p>', path='text/ch2.xhtml')
        first_book = Book('原书', '测试', chapters=[first, second])
        with TemporaryDirectory() as temp:
            root = Path(temp)
            old, new, project = (root / name for name in ('old.epub', 'new.epub', 'project.bookproj'))
            write_epub(first_book, old)
            opened = open_epub(old)
            opened.chapters[0].html = '<p>修改后的正文</p>'
            opened.save(project)
            write_epub(Book.load(project), new)
            with ZipFile(old) as before, ZipFile(new) as after:
                self.assertEqual(before.read(second.path), after.read(second.path))
                self.assertEqual(before.read('styles/book.css'), after.read('styles/book.css'))
                self.assertEqual(before.read('nav.xhtml'), after.read('nav.xhtml'))
                self.assertIn('修改后的正文', after.read(first.path).decode())
            self.assertEqual(len(open_epub(new).chapters), 2)

    def test_rename_epub_chapter_preserves_nested_anchors_and_label_markup(self):
        chapter = Chapter('原目录标题', '<h1 id="start">原目录标题</h1><h2 id="detail">细节</h2>',
                          path='sections.xhtml')
        x = f'{{{XHTML}}}'
        with TemporaryDirectory() as temp:
            root = Path(temp)
            seed = root / 'seed.epub'
            write_epub(Book('原书', chapters=[chapter]), seed)
            for whole_file in (True, False):
                with self.subTest(whole_file=whole_file):
                    old = root / f'original-{whole_file}.epub'
                    new = root / f'renamed-{whole_file}.epub'

                    def add_sections(doc):
                        doc.addprevious(etree.Comment('preserved navigation comment'))
                        item = doc.find(f'.//{x}li')
                        parent = item.find(f'{x}a')
                        parent.text = None
                        etree.SubElement(parent, f'{x}span', attrib={'class': 'chapter-label'}).text = chapter.title
                        if not whole_file:
                            parent.set('href', f'{chapter.path}#start')
                        sublist = etree.SubElement(item, f'{x}ol')
                        child = etree.SubElement(sublist, f'{x}li')
                        etree.SubElement(child, f'{x}a', href=f'{chapter.path}#detail').text = '细节'

                    rewrite_member(seed, old, 'nav.xhtml', add_sections)
                    edited = open_epub(old)
                    edited.chapters[0].title = '改名后的章节'
                    write_epub(edited, new)
                    with ZipFile(old) as before, ZipFile(new) as after:
                        navigation = after.read('nav.xhtml')
                        self.assertIn(b'preserved navigation comment', navigation)
                        nav = etree.fromstring(navigation)
                        item = nav.find(f'.//{x}li')
                        parent = item.find(f'{x}a')
                        label = parent.find(f'{x}span')
                        child = item.find(f'{x}ol/{x}li/{x}a')
                        self.assertEqual(label.get('class'), 'chapter-label')
                        self.assertEqual(child.get('href'), f'{chapter.path}#detail')
                        self.assertEqual(child.text, '细节')
                        self.assertIn('改名后的章节', after.read(chapter.path).decode('utf-8'))
                        if whole_file:
                            self.assertEqual(parent.get('href'), chapter.path)
                            self.assertEqual(label.text, '改名后的章节')
                        else:
                            self.assertEqual(before.read('nav.xhtml'), after.read('nav.xhtml'))

    def test_imported_epub_mobile_reencodes_body_images_only_when_enabled(self):
        image = 'images/正文.jpg'
        cover = 'images/封面.jpg'
        output = BytesIO()
        Image.new('RGB', (2000, 1200), '#8e313b').save(output, 'JPEG', quality=97)
        jpeg = Asset(output.getvalue(), 'image/jpeg')
        chapter = Chapter('正文', f'<p><img src="{asset_href(image)}" alt="图"></p>')
        book = Book('原书', chapters=[chapter], assets={image: jpeg, cover: jpeg}, cover=cover)
        with TemporaryDirectory() as temp:
            root = Path(temp)
            old, new, original_quality = (root / name for name in ('old.epub', 'mobile.epub', 'original.epub'))
            write_epub(book, old, mobile=False)
            imported = open_epub(old)
            write_epub(imported, new, mobile=True)
            write_epub(imported, original_quality, mobile=False)
            with ZipFile(old) as before, ZipFile(new) as optimized, ZipFile(original_quality) as preserved:
                self.assertEqual(optimized.read(chapter.path), before.read(chapter.path))
                self.assertEqual(optimized.read(cover), jpeg.data)
                self.assertEqual(preserved.read(image), jpeg.data)
                compressed = optimized.read(image)
                self.assertLess(len(compressed), len(jpeg.data))
                with Image.open(BytesIO(compressed)) as optimized_image:
                    self.assertEqual(optimized_image.size, (1600, 960))
                self.assertEqual(open_epub(new).assets[image].media_type, 'image/jpeg')
            self.assertEqual(imported.assets[image], jpeg)

    def test_imported_fixed_layout_preserves_image_dimensions_and_encoding(self):
        image = 'images/full-page.jpg'
        output = BytesIO()
        Image.new('RGB', (2000, 1200), '#8e313b').save(output, 'JPEG', quality=97)
        source = Asset(output.getvalue(), 'image/jpeg')
        chapter = Chapter('正文', f'<p><img src="{image}" alt="图"></p>')
        book = Book('原书', chapters=[chapter], assets={image: source})
        with TemporaryDirectory() as temp:
            root = Path(temp)
            seed = root / 'seed.epub'
            write_epub(book, seed, mobile=False)
            for all_pages in (True, False):
                with self.subTest(all_pages=all_pages):
                    def set_layout(doc):
                        if all_pages:
                            metadata = doc.find(f'{{{OPF}}}metadata')
                            etree.SubElement(metadata, f'{{{OPF}}}meta',
                                             property='rendition:layout').text = 'pre-paginated'
                        else:
                            ref = doc.find(f'{{{OPF}}}spine/{{{OPF}}}itemref')
                            ref.set('properties', 'rendition:layout-pre-paginated')

                    old = root / f'fixed-{all_pages}.epub'
                    new = root / f'fixed-output-{all_pages}.epub'
                    rewrite_member(seed, old, 'package.opf', set_layout)
                    write_epub(open_epub(old), new, mobile=True)
                    with ZipFile(new) as archive:
                        self.assertEqual(archive.read(image), source.data)

    def test_old_qiushi_style_nav_is_rebuilt_as_epub3_toc(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            initial, legacy, result = (root / name for name in ('initial.epub', 'legacy.epub', 'result.epub'))
            book = Book('合集', chapters=[Chapter('一期', '<p>本期</p>'),
                                        Chapter('文章', '<p>一篇</p>', level=2)])
            write_epub(book, initial)
            with ZipFile(initial) as before, ZipFile(legacy, 'w') as after:
                for entry in before.infolist():
                    content = before.read(entry)
                    if entry.filename == 'nav.xhtml':
                        old_nav = etree.fromstring(content)
                        toc = old_nav.find(f'.//{{{XHTML}}}nav')
                        toc.tag = f'{{{XHTML}}}section'
                        del toc.attrib[f'{{{EPUB}}}type']
                        content = etree.tostring(old_nav)
                    after.writestr(entry, content)
            write_epub(open_epub(legacy), result)
            with ZipFile(result) as archive:
                document = etree.fromstring(archive.read('nav.xhtml'))
                self.assertIsNotNone(document.find(f'.//{{{XHTML}}}nav[@{{{EPUB}}}type="toc"]'))

    def test_epub2_with_ncx_upgrades_to_epub3(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root / 'old.epub', root / 'new.epub'
            with ZipFile(old, 'w') as archive:
                archive.writestr('mimetype', 'application/epub+zip')
                archive.writestr('META-INF/container.xml', """<container version="1.0"
                    xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>
                    <rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/>
                    </rootfiles></container>""")
                archive.writestr('OPS/book.opf', f"""<package version="2.0" xmlns="{OPF}"
                    xmlns:dc="http://purl.org/dc/elements/1.1/" unique-identifier="id">
                    <metadata><dc:identifier id="id">urn:test:epub2</dc:identifier>
                    <dc:title>原书</dc:title><dc:language>zh-CN</dc:language></metadata>
                    <manifest><item id="oldnav" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
                    <item id="chapter" href="正文.xhtml" media-type="application/xhtml+xml"/></manifest>
                    <spine toc="oldnav"><itemref idref="chapter"/></spine></package>""".encode('utf-8'))
                archive.writestr('OPS/toc.ncx', """<ncx version="2005-1"
                    xmlns="http://www.daisy.org/z3986/2005/ncx/" xml:lang="zh-CN">
                    <head><meta name="dtb:uid" content="urn:test:epub2"/>
                    <meta name="dtb:depth" content="2"/><meta name="dtb:totalPageCount" content="0"/>
                    <meta name="dtb:maxPageNumber" content="0"/></head><docTitle><text>原书</text></docTitle>
                    <navMap><navPoint id="n1" playOrder="1"><navLabel><text>正文</text></navLabel>
                    <content src="正文.xhtml"/>
                    <navPoint id="n2" playOrder="2"><navLabel><text>细节</text></navLabel>
                    <content src="正文.xhtml#detail"/></navPoint></navPoint></navMap></ncx>""".encode('utf-8'))
                archive.writestr('OPS/正文.xhtml', f'<html xmlns="{XHTML}"><head><title>正文</title></head>'
                                 '<body><p id="detail">原版内容。</p></body></html>')
            book = open_epub(old)
            self.assertEqual(book.chapters[0].title, '正文')
            book.chapters[0].title = '新标题'
            write_epub(book, new)
            self.assertEqual(open_epub(new).chapters[0].title, '新标题')
            with ZipFile(new) as archive:
                package = etree.fromstring(archive.read('OPS/book.opf'))
                self.assertEqual(package.get('version'), '3.0')
                self.assertIn(b'%E6%AD%A3%E6%96%87.xhtml', archive.read('OPS/book.opf'))
                x = f'{{{XHTML}}}'
                nav = etree.fromstring(archive.read('nav.xhtml'))
                entry = nav.find(f'.//{x}nav/{x}ol/{x}li')
                self.assertEqual(entry.findtext(f'{x}a'), '新标题')
                detail = entry.find(f'{x}ol/{x}li/{x}a')
                self.assertEqual(detail.get('href'), f'{asset_href("OPS/正文.xhtml")}#detail')
                self.assertEqual(detail.text, '细节')


if __name__ == '__main__':
    unittest.main()
