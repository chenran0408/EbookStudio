import unittest

from book_quality import inspect_book
from ebook_core import Asset, Book, Chapter


class BookQualityTests(unittest.TestCase):
    def test_accepts_offline_images_and_encoded_links_between_chapters(self):
        book = Book(chapters=[
            Chapter("第一章", '<p>开头 <a href="two.xhtml#%E7%BB%93%E6%9E%9C">结果</a></p>', path="text/one.xhtml"),
            Chapter("第二章", '<figure id="结果"><img src="../images/chart.png"/></figure>', path="text/two.xhtml"),
        ], assets={"images/chart.png": Asset(b"placeholder", "image/png")})
        self.assertEqual(inspect_book(book), [])

    def test_reports_missing_files_and_anchors_with_chapter_positions(self):
        book = Book(chapters=[
            Chapter("结果", '<p><a href="#missing">断开的锚点</a><a href="gone.xhtml">失效章节</a>'
                    '<img src="gone.png"/><img src="https://example.com/chart.jpg"/></p>', path="text/one.xhtml"),
            Chapter("", "<p> </p>", 3, path="text/two.xhtml"),
        ])
        results = {(item.title, item.chapter): item.severity for item in inspect_book(book)}
        self.assertEqual(results, {
            ("链接锚点不存在", 0): "error", ("书内链接失效", 0): "error",
            ("缺少图片", 0): "error", ("图片未离线保存", 0): "warning",
            ("缺少章节标题", 1): "error", ("目录层级跳跃", 1): "warning", ("空章节", 1): "warning",
        })

    def test_preserves_original_body_anchors_and_excludes_removed_chapters(self):
        book = Book(chapters=[Chapter("测试", '<p><a href="#top">顶部</a><a href="gone.xhtml">缺页</a></p>',
                                     path="text/one.xhtml")])
        book._epub_members = {
            "text/one.xhtml": b'<html><head></head><body id="top"><p>Text</p></body></html>',
            "text/gone.xhtml": b'<html><body>Deleted</body></html>',
        }
        book._epub_chapters = {
            "text/one.xhtml": ("测试", 1, "original"),
            "text/gone.xhtml": ("已删除", 1, "original"),
        }
        self.assertEqual([row.title for row in inspect_book(book)], ["书内链接失效"])
