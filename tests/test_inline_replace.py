import unittest

from book_replace import preview_replacement
from ebook_core import Book, Chapter
from replacement_rules import Rule
from rule_groups import Step, preview_rule_group


def book_with(source):
    return Book(chapters=[Chapter("正文", source)])


class InlineReplacementTests(unittest.TestCase):
    def test_nested_formatting_link_attributes_and_empty_anchors_survive(self):
        source = "<p>前&#x7532;<i id='spot'></i><strong>乙<a href='#spot'><em>丙</em></a></strong>丁后</p>"
        book = book_with(source)
        plan = preview_replacement(book, "甲乙丙丁", "甲天地丁")
        self.assertEqual((plan.matches, plan.replacements), (1, 1))
        self.assertEqual(plan.hits[0].before, "前【甲乙丙丁】后")
        plan.apply(book)
        self.assertEqual(book.chapters[0].html, source.replace("乙", "天").replace("丙", "地"))

    def test_growth_shrink_deletion_and_entity_spelling(self):
        source = "<p>甲<a id='id' href='#id'>乙</a><em>丙</em>&#x4e01;</p>"
        for new, expected in (
            ("戊己庚辛丁", "<p>戊<a id='id' href='#id'>己</a><em>庚辛</em>&#x4e01;</p>"),
            ("甲丁", "<p>甲<a id='id' href='#id'></a><em></em>&#x4e01;</p>"),
            ("", "<p><a id='id' href='#id'></a><em></em></p>"),
            ("甲乙增丙丁", "<p>甲<a id='id' href='#id'>乙增</a><em>丙</em>&#x4e01;</p>"),
            ("甲乙丙丁！", "<p>甲<a id='id' href='#id'>乙</a><em>丙</em>&#x4e01;！</p>"),
        ):
            with self.subTest(new=new):
                book = book_with(source)
                preview_replacement(book, "甲乙丙丁", new).apply(book)
                self.assertEqual(book.chapters[0].html, expected)

    def test_captured_text_across_tags_and_adjacent_selection(self):
        source = "<p>1<b>号2</b><i>号</i></p><p><b>3</b>号</p>"
        book = book_with(source)
        plan = preview_replacement(book, r"(?P<num>\d)号", r"第\g<num>期", regular=True, preview_limit=1)
        self.assertEqual((plan.matches, plan.replacements), (3, 3))
        self.assertEqual([hit.before for hit in plan.page(1, 2)], ["1号【2号】", "【3号】"])
        plan.apply(book, {1, 2})
        self.assertEqual(book.chapters[0].html, "<p>1<b>号第</b><i>2期</i></p><p><b>第</b>3期</p>")

    def test_boundaries_exclude_hidden_markup_line_breaks_and_other_blocks(self):
        breaks = ("<br/>", "<img src='甲乙.png'/>", "</p><p>", "<hr>", "\r\n", "&#10;",
                  "<span hidden>甲乙</span>", "<i aria-hidden='true'>甲乙</i>",
                  "<em style='color:red; display: none !important;'>甲乙</em>", "<span hidden/>",
                  "<script>甲乙</script>", "<!--甲乙-->", "<table><tr><td></td></tr></table>")
        for separator in breaks:
            with self.subTest(separator=separator):
                book = book_with(f"<p>甲{separator}乙</p>")
                self.assertEqual(preview_replacement(book, r"甲\s*乙", "改", regular=True).matches, 0)
        opaque = book_with('<?xml version="1.0"?><p>甲<span>&custom;</span>乙</p>')
        self.assertEqual(preview_replacement(opaque, "甲乙", "改").matches, 0)

    def test_word_boundaries_and_zero_width_regex_use_the_visible_run(self):
        book = book_with("<p>a<b>b</b>c</p><p><i>d</i></p>")
        self.assertEqual(preview_replacement(book, r"\bb\b", "!", regular=True).matches, 0)
        preview_replacement(book, r"^|(?=b)|$", "+", regular=True).apply(book)
        self.assertEqual(book.chapters[0].html, "<p>+a<b>+b</b>c+</p><p><i>+d+</i></p>")

    def test_entities_inside_replacements_and_escaping_at_joins(self):
        book = book_with("<p>甲&#20013;乙<em>丙</em></p><p>A > B & C &#x4E2D; &#38; &unknown;<b>末</b></p>")
        preview_replacement(book, "甲中乙丙", "天中地人").apply(book)
        preview_replacement(book, "unknown;末", "amp;新").apply(book)
        self.assertEqual(book.chapters[0].html,
                         "<p>天&#20013;地<em>人</em></p><p>A > B & C &#x4E2D; &#38; &amp;amp;新<b></b></p>")

    def test_fix_only_changed_html_delimiters_and_ambiguous_semicolonless_entities(self):
        book = book_with("<p>A < 2，&not 空。中<1</p>")
        preview_replacement(book, " 空", "in;").apply(book)
        preview_replacement(book, "1", "tag>").apply(book)
        self.assertEqual(book.chapters[0].html, "<p>A < 2，¬in;。中&lt;tag&gt;</p>")
        xhtml = book_with('<?xml version="1.0"?><p>正常 > 原文，]]空></p>')
        preview_replacement(xhtml, "空", "").apply(xhtml)
        self.assertEqual(xhtml.chapters[0].html, '<?xml version="1.0"?><p>正常 > 原文，]]&gt;</p>')

    def test_rule_group_runs_each_step_against_revised_visible_text(self):
        book = book_with("<p>辨形，己<span>经</span>核对。</p>")
        plan = preview_rule_group(book, (Step("纠错", Rule("己经", "已经", False, True)),
                                         Step("简化", Rule("经核对", "核对", False, True))))
        self.assertEqual([stage.matches for stage in plan.stages], [1, 1])
        self.assertEqual(plan.changes[0], "<p>辨形，已<span></span>核对。</p>")


if __name__ == "__main__":
    unittest.main()
