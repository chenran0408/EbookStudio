import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ebook_core import Book, Chapter
from studio_operations import export_book_with_report, inspect_epub
from workspace_tools import plan_destinations


def epubcheck_output(*, error_count, warning_count):
    def finish(path, *, required, options):
        if Path(path).suffix != ".epub" or not required and Path(path).name != "book.epub":
            raise AssertionError(path)
        report_path = Path(options[1])
        messages = []
        for severity, count in (("WARNING", warning_count), ("ERROR", error_count)):
            if count:
                messages.append({
                    "ID": "HTM-025" if severity == "WARNING" else "RSC-007",
                    "severity": severity,
                    "message": "示例消息",
                    "locations": [{"path": "Text/%E7%AC%AC%E4%B8%80%E7%AB%A0.xhtml", "line": 2, "column": 17}],
                })
        report_path.write_text(json.dumps({
            "checker": {"checkerVersion": "5.4.0", "nFatal": 0, "nError": error_count, "nWarning": warning_count},
            "messages": messages,
        }), encoding="utf-8")
        return int(bool(error_count)), "EPUBCheck completed"
    return finish


class EpubValidationTests(unittest.TestCase):
    def test_manual_report_keeps_severity_order_and_scope(self):
        with patch("studio_operations._epubcheck_process", side_effect=epubcheck_output(error_count=1, warning_count=1)):
            report = inspect_epub(Path("输入.epub"))
        self.assertFalse(report.valid)
        self.assertEqual(report.summary, "1 项错误 · 1 项警告")
        self.assertEqual([issue.code for issue in report.issues], ["RSC-007", "HTM-025"])
        self.assertIn("Text/第一章.xhtml:2:17", report.issues[0].details())

    def test_warning_is_available_after_export(self):
        book = Book(title="书稿", chapters=[Chapter("第一章", "<p>正文</p>")])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("studio_operations._epubcheck_process", side_effect=epubcheck_output(error_count=0, warning_count=1)):
                outputs, errors, report = export_book_with_report(book, root, "书稿", ["epub"], False)
            self.assertEqual(outputs, [root / "书稿.epub"])
            self.assertTrue(outputs[0].is_file())
            self.assertEqual(errors, [])
            self.assertTrue(report.valid)
            self.assertEqual(report.warning_count, 1)

    def test_export_errors_block_all_outputs_and_keep_occupied_output(self):
        book = Book(title="书稿", chapters=[Chapter("第一章", "<p>正文</p>")])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "书稿.epub"
            existing.write_bytes(b"preserved original")
            plans = plan_destinations(root, [("书稿", "epub"), ("书稿", "pdf")], replace=True)
            with patch("studio_operations._epubcheck_process", side_effect=epubcheck_output(error_count=1, warning_count=0)):
                outputs, errors, report = export_book_with_report(book, root, "书稿", ["epub", "pdf"], False, plans=plans)
            self.assertEqual((outputs, errors), ([], []))
            self.assertFalse(report.valid)
            self.assertEqual(existing.read_bytes(), b"preserved original")
            self.assertFalse((root / "书稿.pdf").exists())

    def test_failed_tool_without_json_reports_diagnostic(self):
        with (patch("studio_operations._epubcheck_process", return_value=(1, "program diagnostic")),
              self.assertRaisesRegex(ValueError, "program diagnostic")):
            inspect_epub(Path("输入.epub"))
