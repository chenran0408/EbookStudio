from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from lxml import etree
from PIL import Image

from ebook_core import Asset, Book, Chapter
from epub_compare import compare_epubs
from epub_io import CONTAINER, DC, OPF, write_epub


class EpubFileComparisonTests(unittest.TestCase):
    def test_renamed_reordered_and_revised_chapters_assets_and_toc(self):
        original = Book('观察笔记', '原作者', chapters=[
            Chapter('春', '<p>春雨浸湿河岸边的小路，记录点设在桥东。</p>', path='one.xhtml'),
            Chapter('同名', '<p>东边的旧渡口。</p>', path='two.xhtml'),
            Chapter('同名', '<p>西边倾斜的路标。</p>', path='three.xhtml'),
            Chapter('撤下', '<p>撤下的编后记。</p>', path='old.xhtml')],
            assets={'stamp.svg': Asset(b'<svg/>', 'image/svg+xml'),
                    'reading.css': Asset(b'p{color:red}', 'text/css')})
        revised = deepcopy(original)
        a, b, c, _ = revised.chapters
        a.path = 'edited.xhtml'
        a.html = a.html.replace('桥东', '桥西')
        b.path, c.path = c.path, b.path  # A repack reused the numbered filenames.
        revised.chapters = [c, a, b, Chapter('新增', '<p>调查补记。</p>', path='add.xhtml')]
        revised.author = '新编者'
        revised.assets['stamp.svg'].data = b'<svg> </svg>'
        revised.assets['reading.css'].data = b'p{color:blue}'
        with TemporaryDirectory() as temp:
            files = [Path(temp)/name for name in ('初版.epub','修订.epub')]
            write_epub(original, files[0], mobile=False)
            write_epub(revised, files[1], mobile=False)
            digests = [sha256(p.read_bytes()).hexdigest() for p in files]
            changes = compare_epubs(*files)
            self.assertEqual([sha256(p.read_bytes()).hexdigest() for p in files], digests)
            self.assertEqual(compare_epubs(files[0], files[0]), [])
        self.assertEqual({(c.before, c.after) for c in changes if c.kind == '章节文件'},
                         {('one.xhtml','edited.xhtml'), ('two.xhtml','three.xhtml'), ('three.xhtml','two.xhtml')})
        body = [c for c in changes if c.kind == '正文']
        self.assertEqual(len(body), 1)
        self.assertIn('桥东', body[0].before)
        self.assertIn('桥西', body[0].after)
        self.assertEqual({(c.action, c.name) for c in changes if c.kind == '章节'},
                         {('删除','撤下'), ('新增','新增')})
        self.assertIn('章节顺序', {c.kind for c in changes})
        self.assertIn('目录', {c.kind for c in changes})
        self.assertIn('作者', {c.name for c in changes if c.kind == '书稿资料'})
        assets = {c.name: c for c in changes if c.kind == '资源'}
        self.assertIn('color:blue', assets['reading.css'].after)
        self.assertNotEqual(assets['stamp.svg'].before, assets['stamp.svg'].after)

    def test_unrelated_duplicate_titles_are_not_paired_after_rename(self):
        a = Book(chapters=[Chapter('同名', '<p>浅滩测量。</p>', path='old1.xhtml'),
                           Chapter('同名', '<p>晴天晒谷。</p>', path='old2.xhtml')])
        b = deepcopy(a)
        b.chapters = [Chapter('同名','<p>整修大门。</p>',path='new1.xhtml'),
                      Chapter('同名','<p>昨夜下雪。</p>',path='new2.xhtml')]
        with TemporaryDirectory() as temp:
            p, q = [Path(temp)/name for name in ('a.epub', 'b.epub')]
            write_epub(a, p)
            write_epub(b, q)
            changes = compare_epubs(p, q)
        self.assertEqual({(c.action, c.path) for c in changes if c.kind == '章节'},
                         {('删除','old1.xhtml'), ('删除','old2.xhtml'), ('新增','new1.xhtml'), ('新增','new2.xhtml')})
        self.assertNotIn('章节文件', {c.kind for c in changes})

    def test_relocated_package_maps_edited_files_and_preserves_logical_toc(self):
        book = Book(chapters=[Chapter('一', '<p>旧文</p>', path='one.xhtml')])
        with TemporaryDirectory() as temp:
            p, q = [Path(temp)/name for name in ('a.epub', 'b.epub')]
            write_epub(book, p, mobile=False)
            with ZipFile(p) as before, ZipFile(q, 'w') as after:
                for item in before.infolist():
                    name, data = item.filename, before.read(item)
                    if name == 'META-INF/container.xml':
                        doc = etree.fromstring(data)
                        node = doc.find(f'.//{{{CONTAINER}}}rootfile')
                        node.set('full-path', 'EPUB/' + node.get('full-path'))
                        data = etree.tostring(doc)
                    elif name != 'mimetype':
                        if name == 'one.xhtml':
                            data = data.replace('旧文'.encode(), '新篇'.encode())
                            data = data.replace(b'<body>', b'<body class="revised">')
                        elif name.endswith('.opf'):
                            doc = etree.fromstring(data)
                            meta = doc.find(f'{{{OPF}}}metadata')
                            etree.SubElement(meta, f'{{{DC}}}subject').text = '观察'
                            data = etree.tostring(doc)
                        elif name.endswith('.css'):
                            data += b'\np { color: teal; }'
                        name = 'EPUB/' + name
                    after.writestr(name, data)
            changes = compare_epubs(p, q)
        self.assertNotIn('目录', {c.kind for c in changes})
        self.assertNotIn('章节', {c.kind for c in changes})
        self.assertEqual({c.kind for c in changes}, {'正文','章节属性','章节文件','资源文件','资源','书稿资料'})
        self.assertIn('观察', next(c.after for c in changes if c.name == '其他出版资料'))
        self.assertIn('teal', next(c.after for c in changes if c.kind == '资源'))

    def test_unreadable_revision_names_the_input_without_altering_original(self):
        with TemporaryDirectory() as temp:
            p, q = [Path(temp)/name for name in ('a.epub', 'not-a-book.epub')]
            write_epub(Book(chapters=[Chapter('章')]), p)
            q.write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError, '无法读取修订版'):
                compare_epubs(p, q)
            self.assertEqual(q.read_bytes(), b'bad')

    def test_reader_sees_each_picture_including_one_sided_addition(self):
        from PySide6.QtWidgets import QApplication
        from checkpoint_compare_dialog import CheckpointCompareDialog

        def png(color):
            stream = BytesIO()
            Image.new('RGB', (12, 12), color).save(stream, 'PNG')
            return Asset(stream.getvalue(), 'image/png')

        with patch.dict(os.environ, {'QT_QPA_PLATFORM': 'offscreen'}):
            app = QApplication.instance() or QApplication([])
        original = Book(chapters=[Chapter('记录')], assets={'color.png': png('red')})
        revised = deepcopy(original)
        revised.assets.update({'color.png': png('blue'), 'green.png': png('green')})
        with TemporaryDirectory() as temp:
            p, q = [Path(temp)/name for name in ('a.epub', 'b.epub')]
            write_epub(original, p, mobile=False)
            write_epub(revised, q, mobile=False)
            changes = compare_epubs(p, q)
        view = CheckpointCompareDialog(None, None, changes, files=(p, q))
        for index, change in enumerate(changes):
            if change.kind != '资源':
                continue
            view.items.setCurrentItem(view.items.topLevelItem(index))
            left, right = view.images
            if change.name == 'color.png':
                self.assertEqual(left.pixmap().toImage().pixelColor(0, 0).name(), '#ff0000')
                self.assertEqual(right.pixmap().toImage().pixelColor(0, 0).name(), '#0000ff')
            else:
                self.assertEqual(left.text(), '此版本没有这项资源')
                self.assertEqual(right.pixmap().toImage().pixelColor(0, 0).name(), '#008000')
        self.assertEqual({c.name for c in changes if c.kind == '资源'}, {'color.png','green.png'})
        view.close()
