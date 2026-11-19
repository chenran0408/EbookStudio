from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from book_replace import preview_replacement, replace_with_checkpoint
from ebook_core import Asset, Book, Chapter
from workspace import Workspace


class BookReplacementTests(unittest.TestCase):
    def test_edits_readable_text_preserving_markup_attributes_and_untouched_entities(self):
        original = (
            '<?xml version="1.0"?>\r\n'
            '<x:html xmlns:x="http://www.w3.org/1999/xhtml">\r\n'
            '<x:head><x:title>河流</x:title></x:head><x:body>\r\n'
            '<x:p id=\'河流\' title="河流 &amp; 样本">&#x4e2d; A&amp;B 河流</x:p>'
            '<!-- 河流 --><x:p hidden="hidden">河流</x:p>'
            '<x:script>河流 &lt; 三</x:script><x:style>河流</x:style>'
            '<x:p>河<x:em>流</x:em></x:p></x:body></x:html>'
        )
        book = Book(chapters=[Chapter("第一章", original), Chapter("第二章", "<p>另一条河流</p>")])

        plan = preview_replacement(book, "河流", "<溪流 & 山谷>", preview_limit=1)

        self.assertEqual((plan.matches, plan.replacements, len(plan.chapters)), (2, 2, 2))
        self.assertEqual([hit.chapter_title for hit in plan.hits], ["第一章"])
        self.assertIn("A&B 【河流】", plan.hits[0].before)
        self.assertIn("A&B 【<溪流 & 山谷>】", plan.hits[0].after)
        self.assertEqual(book.chapters[0].html, original)
        plan.apply(book)
        self.assertEqual(
            book.chapters[0].html,
            original.replace("A&amp;B 河流</x:p>", "A&amp;B &lt;溪流 &amp; 山谷&gt;</x:p>"),
        )
        self.assertEqual(book.chapters[1].html, "<p>另一条&lt;溪流 &amp; 山谷&gt;</p>")

    def test_preserves_entities_and_escapes_changed_text_at_reference_boundaries(self):
        book = Book(chapters=[Chapter("正文", "<p>A&amp;B and &NotEqualTilde; and &unknown;</p>")])
        plan = preview_replacement(book, "A&B", "C")
        plan.apply(book)
        self.assertEqual(book.chapters[0].html, "<p>C and &NotEqualTilde; and &unknown;</p>")
        preview_replacement(book, "≂", "近似").apply(book)
        self.assertEqual(book.chapters[0].html, "<p>C and 近似̸ and &unknown;</p>")
        preview_replacement(book, "unknown", "amp").apply(book)
        self.assertEqual(book.chapters[0].html, "<p>C and 近似̸ and &amp;amp;</p>")

    def test_full_xhtml_preserves_custom_references_and_xml_numeric_meaning(self):
        book = Book(chapters=[Chapter("正文",
            '<!DOCTYPE html [<!ENTITY custom_name "自定义">]>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<p>&custom_name; &#x80; 测试</p></body></html>'
        )])
        self.assertEqual(preview_replacement(book, "custom_name", "changed").matches, 0)
        plan = preview_replacement(book, "测试", "示例")
        plan.apply(book)
        self.assertIn("&custom_name; &#x80; 示例", book.chapters[0].html)

    def test_literal_case_and_regex_captures_and_empty_replacement(self):
        book = Book(chapters=[Chapter("正文", "<p>第2025期 和第2026期 A+B</p>")])
        self.assertEqual(preview_replacement(book, "a+b", "", case_sensitive=True).matches, 0)
        preview_replacement(book, "a+b", r"\1").apply(book)
        plan = preview_replacement(book, r"第(?P<year>\d{4})期", r"\g<year> 年", regular=True)
        plan.apply(book)
        self.assertIn(r"2025 年 和2026 年 \1", book.chapters[0].html)
        preview_replacement(book, r" 和", "", regular=True).apply(book)
        self.assertIn(r"2025 年2026 年 \1", book.chapters[0].html)

    def test_regular_errors_are_reported_and_no_match_template_is_checked(self):
        book = Book(chapters=[Chapter("正文", "<p>保留正文。</p>")])
        for pattern, replacement in [("(", ""), ("找不到", r"\g<missing>"), ("找不到", r"\x00")]:
            with self.subTest(pattern=pattern, replacement=replacement), self.assertRaises(ValueError):
                preview_replacement(book, pattern, replacement, regular=True)
        with patch("book_replace.monotonic", side_effect=[1, 10]):
            with self.assertRaisesRegex(ValueError, "超时"):
                preview_replacement(book, "保留", "替换", regular=True)
        self.assertEqual(book.chapters[0].html, "<p>保留正文。</p>")

    def test_zero_width_regex_can_insert_and_noop_matches_are_counted(self):
        book = Book(chapters=[Chapter("正文", "<p>仔细</p><p>核对</p>")])
        plan = preview_replacement(book, r"^", "开始 ", regular=True)
        self.assertEqual(plan.replacements, 2)
        plan.apply(book)
        self.assertEqual(book.chapters[0].html, "<p>开始 仔细</p><p>开始 核对</p>")
        plan = preview_replacement(book, "开始", "开始")
        self.assertEqual((plan.matches, plan.replacements, plan.chapters, plan.hits), (2, 0, (), ()))

    def test_stale_preview_applies_no_chapter(self):
        book = Book(chapters=[Chapter("第一章", "<p>原文</p>"), Chapter("第二章", "<p>原文</p>")])
        plan = preview_replacement(book, "原文", "修改")
        book.chapters[1].html = "<p>编辑了第二章</p>"
        with self.assertRaisesRegex(ValueError, "重新预览"):
            plan.apply(book)
        self.assertEqual(book.chapters[0].html, "<p>原文</p>")


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = Workspace(self.root)
        self.addCleanup(self.workspace.close)

    def test_checkpoints_are_atomic_complete_and_scoped_to_the_book(self):
        book = Book(
            title="原书",
            chapters=[Chapter("正文", "<p>完整内容</p>")],
            assets={"art.png": Asset(b"art", "image/png")},
            cover="art.png",
            stylesheet="p { margin: 2em }",
            _epub_path="OEBPS/content.opf",
            _epub_members={"OEBPS/content.opf": b"package", "OEBPS/article.xhtml": b"original"},
            _epub_chapters={"OEBPS/article.xhtml": ("原章", 2, "hash")},
        )
        first = self.workspace.save_checkpoint(book, "编辑之前")
        original = Book.load(first.path)
        self.assertEqual(original, book)

        book.title = "改了书名"
        book.chapters[0].html = "<p>保留新版</p>"
        second = self.workspace.save_checkpoint(book, "")
        self.assertEqual(second.label, "手动检查点")
        with first.path.with_name("checkpoint-" + "a" * 32 + ".bookproj").open("wb") as broken:
            broken.write(b"damaged")
        self.assertEqual(self.workspace.checkpoints(book), [second, first])
        self.assertEqual(self.workspace.load_checkpoint(book, first), original)
        unrelated = Book("同名也不会合并")
        self.assertEqual(self.workspace.checkpoints(unrelated), [])
        with self.assertRaisesRegex(ValueError, "工作区"):
            self.workspace.load_checkpoint(unrelated, first)

    def test_failed_checkpoint_publication_leaves_existing_versions_and_book_unchanged(self):
        book = Book(chapters=[Chapter("正文", "<p>旧值</p>")])
        first = self.workspace.save_checkpoint(book, "已存版本")
        plan = preview_replacement(book, "旧值", "新值")
        book.assets["../bad.png"] = Asset(b"broken path", "image/png")
        with self.assertRaises(ValueError):
            replace_with_checkpoint(self.workspace, book, plan)
        self.assertEqual(book.chapters[0].html, "<p>旧值</p>")
        self.assertEqual(self.workspace.checkpoints(book), [first])
        self.assertEqual(list(first.path.parent.glob("*.tmp")), [])

    def test_successful_replacement_returns_changed_copy_and_preserves_snapshot(self):
        book = Book(chapters=[Chapter("正文", "<p>旧值</p>")])
        plan = preview_replacement(book, "旧值", "新值")
        revised = replace_with_checkpoint(self.workspace, book, plan)
        self.assertEqual(revised.chapters[0].html, "<p>新值</p>")
        self.assertEqual(book.chapters[0].html, "<p>旧值</p>")
        (checkpoint,) = self.workspace.checkpoints(revised)
        self.assertEqual(checkpoint.label, "批量替换前")
        self.assertEqual(self.workspace.load_checkpoint(revised, checkpoint), book)


if __name__ == "__main__":
    unittest.main()
