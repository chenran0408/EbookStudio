import unittest

from lxml import html

from ebook_core import resolve_href
from first_book import first_book


class FirstBookTests(unittest.TestCase):
    def test_new_copy_has_independent_content_and_checkpoint_identity(self):
        first = first_book()
        first.chapters[0].html = "<p>我改过的书稿</p>"
        first.assets.clear()
        fresh = first_book()
        self.assertNotEqual(first.identifier, fresh.identifier)
        self.assertNotIn("我改过的书稿", fresh.chapters[0].html)
        self.assertEqual([1, 2], [chapter.level for chapter in fresh.chapters])
        chapter = fresh.chapters[0]
        image, = html.fragment_fromstring(chapter.html, create_parent=True).xpath(".//img")
        asset = fresh.assets[resolve_href(chapter.path, image.get("src"))]
        self.assertTrue(image.get("alt"))
        self.assertEqual(asset.media_type, "image/png")
        self.assertTrue(asset.data.startswith(b"\x89PNG\r\n\x1a\n"))
