import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from lxml import etree

from book_typography import (
    add_linked_stylesheet, bind_font, css_issues, font_info, set_stylesheet, subset_font,
)
from ebook_core import Book, Chapter
from epub_io import XHTML, open_epub, write_epub


def fixture_font(flags=0):
    mapping = {ord(ch): f'g{ord(ch)}' for ch in '龘瀚巡' + ''.join(chr(cp) for cp in range(0x4e00, 0x4f00))}
    order = ['.notdef', *mapping.values()]
    builder = FontBuilder(1000)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(mapping)
    pen = TTGlyphPen(None)
    pen.moveTo((50, 0))
    pen.lineTo((900, 0))
    pen.lineTo((900, 900))
    pen.closePath()
    builder.setupGlyf(dict.fromkeys(order, pen.glyph()))
    builder.setupHorizontalMetrics(dict.fromkeys(order, (1000, 50)))
    builder.setupHorizontalHeader(ascent=900, descent=-100)
    builder.setupNameTable({'familyName': 'Sample', 'styleName': 'Regular', 'licenseDescription': 'Test fixture'})
    builder.setupOS2(sTypoAscender=900, sTypoDescender=-100, usWinAscent=900, usWinDescent=100, fsType=flags)
    builder.setupPost()
    data = BytesIO()
    builder.save(data)
    return data.getvalue()


class BookTypographyTests(unittest.TestCase):
    def test_reports_nested_css_syntax_with_location(self):
        issues = css_issues('p { color: red }\n@media screen {\n  p { color red; }\n}')
        self.assertTrue(issues)
        self.assertTrue(all(issue.line == 3 and issue.column > 0 for issue in issues))
        self.assertEqual(css_issues('@media screen { p { color: red; } }'), [])

    def test_checks_font_embedding_and_subset_flags(self):
        book = Book()
        with self.assertRaisesRegex(ValueError, '嵌入标记'):
            bind_font(book, fixture_font(0x0002), None)
        self.assertFalse(book.assets)
        original = fixture_font(0x0100)
        _, info = bind_font(book, original, None)
        self.assertFalse(info.subsettable)
        with self.assertRaisesRegex(ValueError, '不允许子集'):
            subset_font(original, {ord('龘')})

    def test_recomputes_export_subset_after_reopening_and_new_text(self):
        original = fixture_font()
        book = Book(chapters=[Chapter('第一章', '<p>龘</p>'), Chapter('附录', '<p>汉字</p>', level=2)])
        stylesheet = add_linked_stylesheet(book)
        font, _ = bind_font(book, original, stylesheet)
        book.font_subsets.append(font)
        book.stylesheet = 'p::after { content: "\\5de1"; }'
        self.assertEqual(font_info(book.assets[font].data).family, 'Sample')
        with TemporaryDirectory() as tmp:
            project = Path(tmp) / 'test.bookproj'
            output = Path(tmp) / 'test.epub'
            book.save(project)
            reopened = Book.load(project)
            self.assertEqual(reopened.linked_stylesheets, [stylesheet])
            self.assertEqual(reopened.assets[font].data, original)
            self.assertEqual(reopened.chapters[0].html, '<p>龘</p>')
            reopened.chapters[1].html += '<p>瀚</p>'
            write_epub(reopened, output)
            with ZipFile(output) as archive:
                chapter = etree.fromstring(archive.read(reopened.chapters[0].path))
                self.assertTrue(any(link.get('href') == stylesheet
                                    for link in chapter.findall(f'./{{{XHTML}}}head/{{{XHTML}}}link')))
                payload = archive.read(font)
            self.assertLess(len(payload), len(original))
            with TTFont(BytesIO(payload)) as embedded:
                self.assertTrue({ord(ch) for ch in '龘瀚巡'} <= set(embedded.getBestCmap()))
                self.assertEqual(embedded['name'].getDebugName(13), 'Test fixture')
            self.assertEqual(reopened.assets[font].data, original)

    def test_additional_stylesheet_retains_prior_import_and_toc_rename(self):
        original = Book(chapters=[Chapter('原来的标题', '<p>正文</p>')], stylesheet='p { color: navy; }')
        with TemporaryDirectory() as tmp:
            input_path = Path(tmp) / 'original.epub'
            output_path = Path(tmp) / 'edited.epub'
            write_epub(original, input_path)
            imported = open_epub(input_path)
            imported.chapters[0].title = '修订标题'
            extra = add_linked_stylesheet(imported)
            set_stylesheet(imported, extra, 'p { color: teal; }')
            write_epub(imported, output_path)
            self.assertEqual(open_epub(output_path).chapters[0].title, '修订标题')
            with ZipFile(output_path) as archive:
                chapter = etree.fromstring(archive.read(imported.chapters[0].path))
                links = chapter.findall(f'./{{{XHTML}}}head/{{{XHTML}}}link')
                self.assertEqual([link.get('href') for link in links], ['styles/book.css', extra])


if __name__ == '__main__':
    unittest.main()
