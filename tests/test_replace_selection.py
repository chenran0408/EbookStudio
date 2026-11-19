from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from book_replace import PREVIEW_LIMIT, preview_replacement, replace_with_checkpoint
from ebook_core import Book, Chapter
from workspace import Workspace


class SelectedReplacementTests(unittest.TestCase):
    def test_select_by_occurrence_in_identical_text_and_across_pages(self):
        quote = '<blockquote><em>山川</em></blockquote>'
        core = '<p id="山川"><a href="#山川">山川</a>与山川</p>'
        other = '<p>山川 &#x5c71;川 山川</p>'
        book = Book(chapters=[Chapter('甲', quote + core * PREVIEW_LIMIT), Chapter('乙', other)])
        plan = preview_replacement(book, '山川', '江海')
        self.assertEqual(len(plan.hits), PREVIEW_LIMIT)
        self.assertEqual(sum(1 for _ in plan.page(PREVIEW_LIMIT, PREVIEW_LIMIT)), PREVIEW_LIMIT)
        first_other = plan.chapters[1].hit_range.start
        plan.apply(book, {PREVIEW_LIMIT, first_other, first_other + 1})
        self.assertEqual(book.chapters[0].html, quote + core * 79 + core.replace('与山川', '与江海') + core * 80)
        self.assertEqual(book.chapters[1].html, '<p>江海 江海 山川</p>')

    def test_zero_width_and_capture_selection_keep_surrounding_entities(self):
        book = Book(chapters=[Chapter('同名', '<p>1 2 &amp; 3</p>'), Chapter('同名', '<p>1 2</p>')])
        preview_replacement(book, r'(?=\d)', '第', regular=True).apply(book, {1, 3})
        self.assertEqual([c.html for c in book.chapters], ['<p>1 第2 &amp; 3</p>', '<p>第1 2</p>'])
        plan = preview_replacement(book, r'第(\d)', r'\1期', regular=True)
        plan.apply(book, {1})
        self.assertEqual([c.html for c in book.chapters], ['<p>1 第2 &amp; 3</p>', '<p>1期 2</p>'])

    def test_empty_selection_and_stale_selection_skip_checkpoint(self):
        book = Book(chapters=[Chapter('丙', '<p>改一处，留一处</p>')])
        plan = preview_replacement(book, '一处', '两处')
        with TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory))
            try:
                self.assertIs(replace_with_checkpoint(workspace, book, plan, frozenset()), book)
                book.chapters[0].html += '<p>后续编辑</p>'
                with self.assertRaisesRegex(ValueError, '重新预览'):
                    replace_with_checkpoint(workspace, book, plan, frozenset({1}))
                self.assertEqual(len(workspace.checkpoints(book)), 0)
            finally:
                workspace.close()
