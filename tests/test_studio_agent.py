from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import subprocess
import sys
import unittest

from capture import Candidate
from ebook_core import Book, Chapter
from studio_agent import ACTIONS, execute
from workspace_tools import ToolStatus


class StudioAgentTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.scratch = Path(directory.name)
        self.project = self.scratch / "原创书稿.bookproj"

    def create(self):
        result = execute({
            "action": "create", "output": str(self.project), "title": "原创书稿",
            "chapters": [
                {"title": "第一章", "html": "<p>一段测试正文。</p>"},
                {"title": "附录", "html": "<p>不应冒出附录全文。</p>", "level": 2},
            ],
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["book"]["chapter_count"], 2)

    def test_create_inspect_edit_and_export_keep_input_intact(self):
        self.create()
        original = self.project.read_bytes()
        outline = execute({"action": "inspect", "source": str(self.project)})
        self.assertNotIn("selected_chapter", outline)
        self.assertNotIn("不应冒出附录全文", json.dumps(outline, ensure_ascii=False))
        excerpt = execute({
            "action": "inspect", "source": str(self.project),
            "chapter": 1, "include_html": True, "max_chars": 4,
        })["selected_chapter"]
        self.assertEqual(excerpt["text"], "一段测试")
        self.assertTrue(excerpt["text_truncated"])
        self.assertTrue(excerpt["html_truncated"])

        updated = self.scratch / "edited.bookproj"
        execute({
            "action": "update", "source": str(self.project), "output": str(updated),
            "metadata": {"title": "修订稿", "author": "编辑者"},
            "replace_chapters": [{"chapter": 1, "html": "<p>修订文字。</p>"}],
            "append_chapters": [{"title": "补记", "html": "<p>新增内容。</p>"}],
        })
        self.assertEqual(self.project.read_bytes(), original)
        self.assertEqual(Book.load(updated).title, "修订稿")
        output = self.scratch / "edited.epub"
        with patch("studio_operations.run_epubcheck"):
            execute({"action": "export", "source": str(updated), "output": str(output)})
        readback = execute({"action": "inspect", "source": str(output), "chapter": 1})
        self.assertEqual(readback["selected_chapter"]["text"], "修订文字。")
        self.assertEqual(readback["book"]["chapter_count"], 3)

    def test_reject_occupied_destination_and_never_replace_source(self):
        self.create()
        original = self.project.read_bytes()
        with self.assertRaisesRegex(ValueError, "输出不能与源文件相同"):
            execute({
                "action": "update", "source": str(self.project), "output": str(self.project),
                "overwrite": True, "metadata": {"title": "不能就地改"},
            })
        existing = self.scratch / "already.epub"
        existing.write_bytes(b"existing contents")
        with self.assertRaises(FileExistsError):
            execute({"action": "export", "source": str(self.project), "output": str(existing)})
        self.assertEqual(existing.read_bytes(), b"existing contents")
        self.assertEqual(self.project.read_bytes(), original)
        with self.assertRaisesRegex(ValueError, "扩展名"):
            execute({
                "action": "export", "source": str(self.project), "output": str(self.scratch / "book.pdf"),
                "format": "epub2",
            })

    def test_discover_and_capture_use_shared_web_actions(self):
        url = "https://example.test/catalogue"
        article = "https://example.test/article"
        with patch("capture.discover", return_value=[Candidate("篇名", article)]) as discover:
            result = execute({"action": "discover", "url": url, "rendered": True})
        discover.assert_called_once_with(url, rendered=True)
        self.assertEqual(result["candidates"], [{"title": "篇名", "url": article}])
        book = Book("篇名", chapters=[Chapter("篇名", "<p>网页正文</p>", source=article)])
        with patch("studio_operations.capture_pages", return_value=([book], ["插图缺失"])) as capture:
            result = execute({
                "action": "capture", "urls": [article], "output": str(self.project),
                "metadata": {"title": "采集稿"},
            })
        self.assertEqual(capture.call_args.args, ([article], False))
        self.assertEqual(result["warnings"], ["插图缺失"])
        self.assertEqual(Book.load(self.project).chapters[0].source, article)

    def test_ocr_honors_chosen_local_engine_and_pages(self):
        source = self.scratch / "scan.pdf"
        source.touch()
        book = Book(chapters=[Chapter("第 2 页", "<p>识别结果</p>")])
        ready = {name: ToolStatus(True, "ready") for name in ("paddle", "xiaomi")}
        job = {"action": "ocr", "source": str(source), "output": str(self.project),
               "pages": "2,4-5", "engine": "paddle"}
        with patch("studio_agent.ocr_readiness", return_value=ready):
            with patch("studio_agent.paddle_book", return_value=book) as paddle:
                self.assertEqual(execute(job)["engine"], "paddle")
            self.assertEqual(paddle.call_args.args, (source,))
            self.assertEqual(paddle.call_args.kwargs["pages"], "2,4-5")
            job.update(engine="xiaomi", overwrite=True)
            local_address = "http://127.0.0.1:11540"
            with (
                patch("studio_agent.local_server", return_value=nullcontext((local_address, None))),
                patch("studio_agent.ocr_book", return_value=book) as xiaomi,
            ):
                self.assertEqual(execute(job)["engine"], "xiaomi")
            self.assertEqual(xiaomi.call_args.kwargs["base_url"], local_address)
        self.assertEqual(Book.load(self.project).chapters[0].html, "<p>识别结果</p>")

    def test_conversion_prepares_explicit_epub2_destination_with_safe_publish(self):
        source = self.scratch / "notes.md"
        source.write_text("# title", encoding="utf8")
        output = self.scratch / "chosen.epub"

        def convert(path, destination, format_name, *, overwrite):
            self.assertEqual((path, destination, format_name, overwrite), (source, output, "epub2", False))
            destination.write_bytes(b"test output")

        with patch("studio_operations.convert_to", side_effect=convert):
            result = execute({"action": "convert", "source": str(source),
                              "output": str(output), "format": "epub2"})
        self.assertEqual(result["output"], str(output))
        self.assertEqual(source.read_text(encoding="utf8"), "# title")

    def test_cli_reads_utf8_file_and_stdin_and_reports_invalid_jobs_as_json(self):
        script = Path(__file__).resolve().parents[1] / "studio_agent.py"
        command = [sys.executable, str(script)]
        jobs = self.scratch / "job.json"
        jobs.write_text(json.dumps({"action": "create", "title": "电子书", "output": str(self.project),
                                    "chapters": [{"title": "章节", "html": "<p>段落</p>"}]},
                                   ensure_ascii=False), encoding="utf-8-sig")
        create = subprocess.run([*command, "run", str(jobs)], capture_output=True, timeout=30)
        self.assertEqual(create.returncode, 0, create.stderr.decode(errors="replace"))
        self.assertEqual(json.loads(create.stdout)["book"]["title"], "电子书")
        schema = subprocess.run([*command, "schema"], capture_output=True, timeout=30, check=True)
        self.assertEqual(set(json.loads(schema.stdout)["actions"]), set(ACTIONS))
        result = subprocess.run([*command, "run", "-"], input=b'{"action":"create","unexpected":true}',
                                capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
