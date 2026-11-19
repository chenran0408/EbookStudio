import unittest

from book_search import BookSearch
from ebook_core import Book, Chapter


class BookSearchTests(unittest.TestCase):
    def test_searches_chinese_and_ignores_inline_markup(self):
        search = BookSearch(Book(chapters=[
            Chapter("上卷", "<p>江河<em>入海</em>，长风万里。</p>"),
            Chapter("下卷", "<p>且看江河入海。</p>"),
        ]))

        count, hits = search.find("江河入海")

        self.assertEqual(count, 2)
        self.assertEqual([hit.chapter_index for hit in hits], [0, 1])
        self.assertEqual([hit.title for hit in hits], ["上卷", "下卷"])
        self.assertIn("江河入海，长风万里", hits[0].snippet)

    def test_matches_literal_phrases_case_insensitively_with_normalized_spaces(self):
        search = BookSearch(Book(chapters=[
            Chapter("表达式", "<p>A+B&nbsp;<strong>[2]</strong>.</p><p>AAB 22x</p>"),
        ]))

        count, hits = search.find("  a+b\t[2]. ")

        self.assertEqual(count, 1)
        self.assertIn("A+B [2].", hits[0].snippet)
        self.assertEqual(search.find("AABB"), (0, []))

    def test_skips_hidden_content_and_attributes_in_xhtml(self):
        search = BookSearch(Book(chapters=[
            Chapter("XHTML", """
                <x:html xmlns:x="http://www.w3.org/1999/xhtml">
                  <x:head><x:title>不可检索</x:title></x:head>
                  <x:body>
                    <x:p title="不可检索">正文<x:em>片段</x:em></x:p>
                    <x:script>不可检索</x:script>
                    <x:style>不可检索</x:style>
                    <x:span hidden="hidden">不可检索</x:span>
                  </x:body>
                </x:html>
            """),
        ]))

        self.assertEqual(search.find("不可检索"), (0, []))
        self.assertEqual(search.find("正文片段")[0], 1)

    def test_keeps_spaces_at_block_boundaries_and_searches_incomplete_xhtml(self):
        search = BookSearch(Book(chapters=[
            Chapter("修改中的源码", '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>第一页</p><p>第二页'),
        ]))

        self.assertEqual(search.find("第一页第二页"), (0, []))
        self.assertEqual(search.find("第一页 第二页")[0], 1)

    def test_bounds_snippets_and_display_count_independently_of_match_count(self):
        text = "前" * 50 + "校对 " * 4 + "后" * 50
        search = BookSearch(Book(chapters=[Chapter("正文", f"<p>{text}</p>")]))

        count, hits = search.find("校对", limit=2)

        self.assertEqual(count, 4)
        self.assertEqual(len(hits), 2)
        self.assertTrue(hits[0].snippet.startswith("…"))
        self.assertTrue(hits[0].snippet.endswith("…"))
        self.assertEqual(search.find("  "), (0, []))
        self.assertEqual(search.find("尚未出现"), (0, []))


if __name__ == "__main__":
    unittest.main()
