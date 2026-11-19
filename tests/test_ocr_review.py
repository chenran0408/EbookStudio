from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lxml import etree, html

from ebook_core import Asset, Book, Chapter, resolve_href
from ocr_import import OcrError, scan_provenance
from ocr_review import associate_path, known_scan, prepare_replacement


class ScanProvenanceTests(unittest.TestCase):
    def test_relink_accepts_the_same_file_and_rejects_content_change(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "原稿.pdf"
            moved = root / "移动后的原稿.pdf"
            original.write_bytes(b"synthetic source")
            chosen, digest = associate_path(original, None)
            scan = scan_provenance(chosen, digest, 4, "paddle")
            self.assertEqual(digest, sha256(b"synthetic source").hexdigest())
            self.assertEqual(known_scan(Chapter("第四章", scan=scan)), scan)
            original.rename(moved)
            self.assertEqual(associate_path(moved, scan), (moved.resolve(), digest))
            original.write_bytes(b"different source")
            with self.assertRaisesRegex(OcrError, "SHA-256"):
                associate_path(original, scan)

    def test_projects_without_complete_provenance_need_an_explicit_association(self):
        self.assertIsNone(known_scan(Chapter("旧项目")))
        self.assertIsNone(known_scan(Chapter("不完整", scan={"path": "relative.pdf", "page": 1})))

    def test_scan_metadata_roundtrips_with_an_older_chapter(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "第一页.pdf"
            scan = scan_provenance(source, "a" * 64, 1, "xiaomi")
            file = root / "书稿.bookproj"
            Book(chapters=[Chapter("扫描页", scan=scan), Chapter("旧章节")]).save(file)
            restored = Book.load(file)
            self.assertEqual(restored.chapters[0].scan, scan)
            self.assertIsNone(restored.chapters[1].scan)


class ReplacementTests(unittest.TestCase):
    def test_new_crops_get_relative_paths_without_overwriting_existing_assets(self):
        with TemporaryDirectory() as directory:
            original = Chapter(
                "用户标题", '<p>一个<a href="other.xhtml#start" id="old-link">本书链接</a></p>'
                '<figure><img src="../images/crop.png" alt="原图"/><figcaption>原图说明</figcaption></figure>',
                level=2, source="https://example.org/page", path="OPS/Text/page.xhtml",
            )
            untouched = Chapter("另一章", '<p id="start">人工校对文本</p>', path="OPS/Text/other.xhtml")
            book = Book(chapters=[original, untouched], assets={"OPS/images/crop.png": Asset(b"old", "image/png")})
            scan = scan_provenance(Path(directory) / "原稿.pdf", "a" * 64, 5, "paddle")
            recognized = Book(chapters=[Chapter(
                "模型标题", '<p>一个本书链接</p><p>校订后的 OCR 正文</p><img src="images/crop.png"/>',
                scan=scan, path="page.xhtml",
            )], assets={"images/crop.png": Asset(b"new", "image/png")})
            proposal = prepare_replacement(book, original, recognized)

            root = html.fragment_fromstring(proposal.html, create_parent=True)
            self.assertEqual(root.xpath(".//a/@href"), ["other.xhtml#start"])
            self.assertEqual(root[0].xpath(".//a/@id"), ["old-link"])
            self.assertEqual(root.xpath(".//figcaption/text()"), ["原图说明"])
            images = [resolve_href(original.path, src) for src in root.xpath(".//img/@src")]
            self.assertIn("OPS/images/crop.png", images)
            new_key = next(iter(proposal.assets))
            self.assertIn(new_key, images)
            self.assertTrue(new_key.startswith("ocr/"))
            self.assertEqual(proposal.assets[new_key].data, b"new")
            self.assertEqual(proposal.scan, scan)
            self.assertEqual(proposal.retained_at_end, 1)
            self.assertEqual(book.assets["OPS/images/crop.png"].data, b"old")
            self.assertIsNone(original.scan)
            self.assertEqual(original.title, "用户标题")
            self.assertEqual(original.level, 2)
            self.assertEqual(original.path, "OPS/Text/page.xhtml")
            self.assertIn("人工校对文本", untouched.html)

    def test_xhtml_wrapper_and_container_anchor_survive_replacement(self):
        chapter = Chapter(
            "标题",
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>标题</title>'
            '<style>.chapter{color:black}</style></head><body class="chapter">'
            '<main id="scan"><p>旧正文</p></main></body></html>',
        )
        scan = scan_provenance(Path.cwd() / "原稿.pdf", "a" * 64, 1, "xiaomi")
        recognized = Book(chapters=[Chapter("OCR", "<p>新正文</p>", scan=scan)])
        result = prepare_replacement(Book(chapters=[chapter]), chapter, recognized)
        document = etree.fromstring(result.html.encode("utf-8"))
        self.assertEqual(document.xpath("string(//*[local-name()='style'])"), ".chapter{color:black}")
        self.assertEqual(document.xpath("string(//*[local-name()='body']/@class)"), "chapter")
        self.assertEqual(document.xpath("boolean(//*[@id='scan'])"), True)
        self.assertIn("新正文", result.html)
        self.assertNotIn("旧正文", result.html)
