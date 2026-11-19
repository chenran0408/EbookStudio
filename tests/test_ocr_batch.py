from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

from ebook_core import Asset, Book, Chapter
from ocr_batch import OcrBatchStore
from ocr_import import OcrError


class OcrBatchTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scan = self.root / "中文三页.tiff"
        originals = [Image.new("RGB", (20, 20), color) for color in ("red", "green", "blue")]
        originals[0].save(self.scan, save_all=True, append_images=originals[1:])

    def test_pause_resume_and_retry_checkpoint_only_the_pages_that_need_it(self):
        store = OcrBatchStore(self.root)
        batch = store.create(self.scan, "3,1,2", "paddle")
        calls = []
        updates = []

        def recognize(image, page):
            calls.append((page, image.convert("RGB").getpixel((1, 1))))
            if page == 2 and sum(seen == 2 for seen, _ in calls) == 1:
                raise OcrError("测试失败页")
            return Chapter(f"第 {page} 页", f"<p>页面 {page}</p>"), {f"img-{page}.png": Asset(b"pixels", "image/png")}

        @contextmanager
        def engine(_batch):
            yield recognize

        with patch("ocr_batch._engine", engine):
            batch.run(stop=lambda: bool(calls), notify=updates.append)
            self.assertEqual([state.status for state in batch.states()], ["done", "pending", "pending"])
            page_one = batch._page_path(1).read_bytes()
            restored, = OcrBatchStore(self.root).batches()
            restored.run(stop=lambda: False, notify=updates.append)
            self.assertEqual([state.status for state in restored.states()], ["done", "failed", "done"])
            self.assertEqual(restored.page_state(2).error, "测试失败页")
            partial = restored.build_book()
            self.assertEqual([chapter.scan["page"] for chapter in partial.chapters], [1, 3])
            restored.run(stop=lambda: False, notify=updates.append, retry=True, page=2)
            self.assertEqual([state.status for state in restored.states()], ["done", "done", "done"])
            self.assertEqual(restored._page_path(1).read_bytes(), page_one)
            self.assertEqual([page for page, _ in calls], [1, 2, 3, 2])

        draft = restored.build_book()
        path = self.root / "可编辑书稿.bookproj"
        draft.save(path)
        loaded = Book.load(path)
        self.assertEqual([chapter.scan["page"] for chapter in loaded.chapters], [1, 2, 3])
        self.assertEqual(set(loaded.assets), {f"img-{page}.png" for page in (1, 2, 3)})
        self.assertEqual([(state.number, state.status) for state in updates], [
            (1, "running"), (1, "done"), (2, "running"), (2, "failed"), (3, "running"),
            (3, "done"), (2, "running"), (2, "done"),
        ])

    def test_source_identity_survives_relocation_and_rejects_a_different_scan(self):
        batch = OcrBatchStore(self.root).create(self.scan, "2", "xiaomi")
        moved = self.scan.rename(self.root / "已移动.tif")
        with self.assertRaisesRegex(OcrError, "找不到扫描原件"):
            batch.run(stop=lambda: False, notify=lambda _: None)
        alternative = self.root / "另一个.tif"
        Image.new("RGB", (30, 30), "white").save(alternative)
        with self.assertRaisesRegex(OcrError, "原件不一致"):
            batch.relocate(alternative)
        updated = batch.relocate(moved)
        self.assertEqual(updated.source, moved)
        self.assertEqual(OcrBatchStore(self.root).batches()[0].digest, batch.digest)

    def test_second_runner_cannot_start_the_same_batch(self):
        batch = OcrBatchStore(self.root).create(self.scan, "1", "xiaomi")
        with batch._locked():
            with self.assertRaisesRegex(OcrError, "另一个工作台窗口"):
                batch.run(stop=lambda: False, notify=lambda _: None)

    def test_paddle_worker_page_timeout_marks_current_page_and_stops_session(self):
        worker = self.root / "test_worker.py"
        worker.write_text("import sys, time\nprint('EBOOK_STUDIO_READY', flush=True)\n"
                          "sys.stdin.readline()\ntime.sleep(30)\n", encoding="utf-8")
        real_popen = subprocess.Popen
        children = []

        def start_worker(arguments, **options):
            if Path(arguments[0]).name.lower() != "python.exe" and str(arguments[0]) != sys.executable:
                return real_popen(arguments, **options)
            arguments[-2] = str(worker)
            child = real_popen(arguments, **options)
            children.append(child)
            return child

        batch = OcrBatchStore(self.root).create(self.scan, "1-2", "paddle")
        with patch.dict(os.environ, {"EBOOK_STUDIO_PADDLE_PYTHON": sys.executable}), \
                patch("paddle_import.PAGE_TIMEOUT", .2), \
                patch("paddle_import.STOP_TIMEOUT", .2), \
                patch("paddle_import.subprocess.Popen", start_worker):
            batch.run(stop=lambda: False, notify=lambda _: None)
        self.assertEqual([state.status for state in batch.states()], ["failed", "pending"])
        self.assertIn("单页识别超时", batch.page_state(1).error)
        self.assertIsNotNone(children[0].poll())


if __name__ == "__main__":
    unittest.main()
