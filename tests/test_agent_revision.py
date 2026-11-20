from copy import deepcopy
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ebook_core import Asset, Book, Chapter
from epub_io import write_epub
from replacement_rules import Rule
from rule_groups import SavedGroups, Step
from studio_agent import execute


class AgentRevisionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        environment = patch.dict(os.environ, {"EBOOK_STUDIO_WORKSPACE": str(self.folder / "workspace")})
        environment.start()
        self.addCleanup(environment.stop)
        self.book = Book(chapters=[Chapter("同名", "<p>撰稿 / 许禾</p>"),
                                   Chapter("同名", "<em>图 / 文</em>")])
        self.source = self.folder / "识别稿.bookproj"
        self.book.save(self.source)
        self.steps = [{"name": "全角", "query": " / ", "replacement": " ／ "},
                      {"name": "紧排", "query": " ／ ", "replacement": "／"}]

    def preview(self, **kwargs):
        return execute({"action": "proofread", "source": str(self.source), "steps": self.steps, **kwargs})

    def test_desktop_spacing_rule_can_be_reused_verbatim(self):
        steps = [{"name": " ", "query": " ", "replacement": "", "regular": False, "case_sensitive": False}]
        report = self.preview(steps=steps)
        self.assertEqual(report["stages"][0]["name"], " ")
        self.assertEqual(report["replacements"], 4)

    def test_preview_then_apply_with_roundtrip_and_input_protection(self):
        original = self.source.read_bytes()
        report = self.preview(example_limit=1)
        self.assertEqual(list(self.folder.iterdir()), [self.source])
        self.assertEqual(report["changed_chapters"], 2)
        self.assertEqual(report["replacements"], 4)
        self.assertEqual([s["matches"] for s in report["stages"]], [2, 2])
        self.assertIn("【 ／ 】", report["stages"][1]["examples"][0]["before"])
        self.assertEqual(report["stages"][1]["examples"][0]["chapter"], 1)
        self.assertEqual(report["stages"][1]["examples"][0]["path"], self.book.chapters[0].path)
        options = {"mode": "apply", "preview_id": report["preview_id"]}
        with self.assertRaises(ValueError):
            self.preview(output=str(self.source), overwrite=True, **options)
        output = self.folder / "校订.bookproj"
        self.preview(output=str(output), **options)
        self.assertEqual([c.html for c in Book.load(output).chapters], ["<p>撰稿／许禾</p>", "<em>图／文</em>"])
        with self.assertRaises(FileExistsError):
            self.preview(output=str(output), **options)
        self.assertEqual(self.source.read_bytes(), original)

    def test_stale_source_or_steps_reject_before_writing(self):
        preview_id = self.preview()["preview_id"]
        out = self.folder / "out.bookproj"
        options = {"mode": "apply", "preview_id": preview_id, "output": str(out)}
        with self.assertRaisesRegex(ValueError, "重新预览"):
            self.preview(steps=self.steps[::-1], **options)
        self.book.author = "修订署名"
        self.book.save(self.source)
        with self.assertRaisesRegex(ValueError, "重新预览"):
            self.preview(**options)
        with self.assertRaisesRegex(ValueError, "仅用于"):
            self.preview(output=str(out))
        self.assertFalse(out.exists())

    def test_saved_desktop_groups_are_read_only_and_reflect_edits(self):
        groups = {"action": "rule_groups"}
        self.assertEqual(execute(groups)["groups"], [])
        workspace = self.folder / "workspace"
        self.assertFalse(workspace.exists())
        workspace.mkdir()
        settings = SavedGroups(workspace)
        steps = (Step("全角", Rule(" / ", " ／ ", False, False)),)
        settings.change("署名", None, steps)
        self.assertEqual(execute(groups)["groups"], [{"name": "署名", "step_count": 1}])
        fields = execute({**groups, "name": "署名"})["steps"]
        job = {"action": "proofread", "source": str(self.source)}
        result = execute({**job, "group": "署名"})
        self.assertEqual(result["preview_id"], execute({**job, "steps": fields})["preview_id"])
        settings.change("署名", steps, (Step("紧排", Rule(" / ", "／", False, False)),))
        with self.assertRaisesRegex(ValueError, "重新预览"):
            execute({**job, "group": "署名", "mode": "apply", "preview_id": result["preview_id"],
                     "output": str(self.folder / "校订.bookproj")})
        with self.assertRaisesRegex(ValueError, "选择一项"):
            execute({**job, "group": "署名", "steps": fields})

    def test_compare_bounds_includes_late_changes_and_resource_identity(self):
        original, revised = self.folder / "a.epub", self.folder / "b.epub"
        self.book.chapters[0].html = "<p>" + "正文" * 400 + "甲。</p>"
        self.book.assets["image.png"] = Asset(b"old-image", "image/png")
        write_epub(self.book, original, mobile=False)
        other = deepcopy(self.book)
        other.chapters[0].html = other.chapters[0].html.replace("甲。", "乙。")
        other.assets["image.png"].data = b"revised-image"
        write_epub(other, revised, mobile=False)
        base = {"action": "compare", "original": str(original), "revision": str(revised)}
        before = [p.read_bytes() for p in (original, revised)]
        first = execute({**base, "limit": 1, "max_chars": 24})
        self.assertEqual((first["counts"]["正文"], first["counts"]["资源"]), (1, 1))
        self.assertEqual(first["next_offset"], 1)
        change, = first["changes"]
        self.assertTrue(change["before_truncated"] and change["after_truncated"])
        self.assertIn("甲", change["before"])
        self.assertIn("乙", change["after"])
        rest = execute({**base, "offset": first["next_offset"]})
        self.assertIsNone(rest["next_offset"])
        self.assertEqual(rest["total"], first["total"])
        image, = [row for row in rest["changes"] if row["kind"] == "资源"]
        self.assertEqual(image["path"], "image.png")
        self.assertIn("SHA-256", image["after"])
        self.assertEqual(before, [p.read_bytes() for p in (original, revised)])
        self.assertEqual(execute({**base, "revision": str(original)})["total"], 0)
        # The EPUB proofreader uses the same file reader as the comparison.
        self.assertEqual(self.preview(source=str(original))["replacements"], 2)
