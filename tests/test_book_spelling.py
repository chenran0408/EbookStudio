import tempfile
import unittest
from pathlib import Path

from book_replace import preview_replacement
from book_spelling import PersonalDictionary, english_suggestions, scan, term_expression
from ebook_core import Book, Chapter


class SpellTests(unittest.TestCase):
    def test_real_dictionary_visible_text_and_suggestions(self):
        book = Book(chapters=[Chapter("二月", '<p>She will re<em>cieve</em> new magazines. 林地己经开花。</p>'
                                          '<a href="typoo.htm">https://imaginnary.example</a>'
                                          '<span hidden="">modulesss</span>')])
        found = scan(book, [("己经", "已经")])
        self.assertEqual([(i.word, i.title, i.count) for i in found], [("己经", "二月", 1), ("recieve", "二月", 1)])
        self.assertIn("receive", english_suggestions("recieve"))
        self.assertEqual(found[0].custom, "已经")
        self.assertIn("林地", found[0].context)

    def test_persist_group_remove_and_preserve_markup(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PersonalDictionary(Path(directory))
            store.save("Myralune", "")
            store.save("己经", "已经")
            store.save("己经岗", "")
            book = Book(chapters=[Chapter("春", '<p title="己经">MYRALUNE 己经岗，己经。<em>己经</em></p>'),
                                  Chapter("秋", "<p>己经</p>")])
            entries = PersonalDictionary(Path(directory)).entries()
            hits = scan(book, entries)
            self.assertEqual([(h.word, h.count, h.chapter) for h in hits], [("己经", 2, 0), ("己经", 1, 1)])
            store.remove("Myralune", "")
            self.assertIn("MYRALUNE", [item.word for item in scan(book, store.entries())])
            with self.assertRaises(ValueError):
                store.save("己经", "改变")
            english = Book(chapters=[Chapter("春","<p>teh <em>teh</em> tehfield</p>")])
            plan = preview_replacement(english, term_expression("teh"), "the", regular=True, case_sensitive=True)
            plan.apply(english)
            self.assertEqual(english.chapters[0].html, "<p>the <em>the</em> tehfield</p>")
