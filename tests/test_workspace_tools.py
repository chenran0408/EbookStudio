from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
from subprocess import CompletedProcess
import unittest
from unittest.mock import patch

from ebook_core import Book, Chapter
from ocr_runtime import local_server, ollama_executable, ollama_models_folder
from studio_operations import export_book, run_epubcheck
from workspace_tools import converter_readiness, conversion_stem, epubcheck_jar, ocr_readiness, plan_destinations


class DestinationTests(unittest.TestCase):
    def test_native_epub_and_device_names_match_real_output_paths(self):
        with TemporaryDirectory() as directory:
            plans = plan_destinations(directory, [
                ("CON", "epub"), ("试读", "epub2"), ("试读", "kepub.epub"),
            ])
            self.assertEqual([item.destination.name for item in plans],
                             ["_CON.epub", "试读 - EPUB 2.epub", "试读.kepub.epub"])
            self.assertEqual(conversion_stem("示例.KEPUB.EPUB"), "示例")
            self.assertEqual(conversion_stem("示例.FB2.ZIP"), "示例")

    def test_default_preserves_existing_and_in_batch_conflicts(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "已完成.epub").write_bytes(b"existing")
            plans = plan_destinations(folder, [("已完成", "epub"), ("已完成", "epub")])
            self.assertEqual([item.destination.name for item in plans],
                             ["已完成 (2).epub", "已完成 (3).epub"])
            self.assertTrue(all(item.conflict and not item.replace_existing for item in plans))
            self.assertEqual(list(folder.iterdir()), [folder / "已完成.epub"])

    def test_replace_never_overwrites_input_or_earlier_batch_destination(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "原书.epub"
            existing = folder / "导出.epub"
            old_numbered_file = folder / "导出 (2).epub"
            for path in (source, existing, old_numbered_file):
                path.write_bytes(b"existing")
            plans = plan_destinations(folder, [
                ("原书", "epub"), ("导出", "epub"), ("导出", "epub"),
            ], replace=True, protected=[source])
            self.assertEqual([item.destination.name for item in plans],
                             ["原书 (2).epub", "导出.epub", "导出 (3).epub"])
            self.assertEqual([item.replace_existing for item in plans], [False, True, False])


class ReadinessTests(unittest.TestCase):
    def test_ocr_status_uses_configured_paths_and_real_manifest_blobs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ollama = root / "ollama.exe"
            python = root / "python.exe"
            ollama.write_bytes(b"program")
            python.write_bytes(b"program")
            ollama_models = root / "ollama-models"
            paddle_models = root / "paddle-models"
            reference = {"digest": "sha256:" + "a" * 64, "size": 2}
            blob = ollama_models / "blobs" / reference["digest"].replace(":", "-")
            manifest = ollama_models / "manifests-v2" / "ollama.com" / "library" / "local-ocr" / "q8_0"
            for path in (blob, manifest):
                path.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"config": reference, "layers": [reference]}), encoding="utf-8")
            for part in ("PP-DocLayoutV3/inference.pdiparams", "PaddleOCR-VL-1.6/model.safetensors"):
                path = paddle_models / part
                path.parent.mkdir(parents=True)
                path.write_bytes(b"weights")
            settings = {
                "EBOOK_STUDIO_OLLAMA": str(ollama),
                "EBOOK_STUDIO_OCR_MODELS": str(ollama_models),
                "EBOOK_STUDIO_OCR_MODEL": "local-ocr:q8_0",
                "EBOOK_STUDIO_PADDLE_PYTHON": str(python),
                "EBOOK_STUDIO_PADDLE_MODELS": str(paddle_models),
            }
            with patch.dict("os.environ", settings):
                blob.write_bytes(b"x")
                self.assertFalse(ocr_readiness()["xiaomi"].ready)
                blob.write_bytes(b"ok")
                self.assertTrue(ocr_readiness()["xiaomi"].ready)
                self.assertTrue(ocr_readiness()["paddle"].ready)
                (paddle_models / "PaddleOCR-VL-1.6/model.safetensors").unlink()
                self.assertFalse(ocr_readiness()["paddle"].ready)

    def test_missing_optional_programs_are_reported(self):
        with TemporaryDirectory() as directory:
            with (patch("workspace_tools.conversion.tool_path", return_value=None),
                  patch("workspace_tools.TOOL_ROOT", Path(directory)),
                  patch("workspace_tools.shutil.which", return_value=None)):
                status = converter_readiness()
            self.assertEqual(status.keys(), {"pandoc", "calibre", "epubcheck"})
            self.assertFalse(any(item.ready for item in status.values()))


class PortableToolsTests(unittest.TestCase):
    def test_epubcheck_jar_prefers_explicit_path_then_user_install_then_legacy(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            local_data = root / "Local"
            legacy = root / "legacy"
            legacy_jar = legacy / "epubcheck-5.4.0" / "epubcheck.jar"
            user_jar = local_data / "EbookStudio" / "tools" / "epubcheck-5.3.0" / "epubcheck.jar"
            explicit = root / "另一个目录" / "validator.jar"
            for jar in (legacy_jar, user_jar, explicit):
                jar.parent.mkdir(parents=True)
                jar.write_bytes(b"jar")

            with (patch.dict(os.environ, {"LOCALAPPDATA": str(local_data)}, clear=True),
                  patch("workspace_tools.TOOL_ROOT", legacy)):
                self.assertEqual(epubcheck_jar(), user_jar)
                with patch.dict(os.environ, {"EBOOK_STUDIO_EPUBCHECK": str(explicit)}):
                    self.assertEqual(epubcheck_jar(), explicit)
                with patch.dict(os.environ, {"EBOOK_STUDIO_EPUBCHECK": "validator.jar"}):
                    self.assertIsNone(epubcheck_jar())
                user_jar.unlink()
                self.assertEqual(epubcheck_jar(), legacy_jar)

    def test_epubcheck_status_explicit_check_and_export_find_the_same_jar(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            jar = root / "epubcheck.jar"
            jar.write_bytes(b"jar")
            book = Book(title="试读", chapters=[Chapter("第一章", "<p>正文</p>")])
            exported = root / "试读.epub"
            java = str(root / "java.exe")
            with (patch.dict(os.environ, {"EBOOK_STUDIO_EPUBCHECK": str(jar)}),
                  patch("studio_operations.shutil.which", return_value=java),
                  patch("workspace_tools.conversion.tool_path", return_value=None),
                  patch("studio_operations.subprocess.run",
                        return_value=CompletedProcess([], 0, b"valid", b"")) as check):
                status = converter_readiness()["epubcheck"]
                self.assertTrue(status.ready)
                self.assertIn(str(jar), status.message)
                outputs, errors = export_book(book, root, "试读", ["epub"], True)
                self.assertEqual((outputs, errors), ([exported], []))
                self.assertEqual(run_epubcheck(exported), "valid")

            auto_validation, manual_validation = (call.args[0] for call in check.call_args_list)
            self.assertEqual(auto_validation[:3], [java, "-jar", str(jar)])
            self.assertEqual(Path(auto_validation[-1]).name, "book.epub")
            self.assertEqual(manual_validation, [java, "-jar", str(jar), str(exported)])

    def test_ollama_models_use_standard_store_after_existing_development_store(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy"
            home = root / "user"
            ollama_settings = {"OLLAMA_MODELS": str(root / "ollama-models")}
            app_settings = {"EBOOK_STUDIO_OCR_MODELS": str(root / "ebook-models")}
            with (patch.dict(os.environ, {}, clear=True),
                  patch("ocr_runtime.MODEL_FOLDER", legacy),
                  patch("ocr_runtime.Path.home", return_value=home)):
                self.assertEqual(ollama_models_folder(), home / ".ollama" / "models")
                legacy.mkdir()
                self.assertEqual(ollama_models_folder(), legacy)
                with patch.dict(os.environ, ollama_settings):
                    self.assertEqual(ollama_models_folder(), Path(ollama_settings["OLLAMA_MODELS"]).resolve())
                    with patch.dict(os.environ, app_settings):
                        self.assertEqual(ollama_models_folder(), Path(app_settings["EBOOK_STUDIO_OCR_MODELS"]).resolve())

    def test_windows_ollama_install_and_models_are_used_for_status_and_launch(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            command = root / "Programs" / "Ollama" / "ollama.exe"
            command.parent.mkdir(parents=True)
            command.write_bytes(b"program")
            store = root / "models"
            reference = {"digest": "sha256:" + "a" * 64, "size": 2}
            blob = store / "blobs" / reference["digest"].replace(":", "-")
            manifest = store / "manifests" / "registry.ollama.ai" / "library" / "xiaomi-ocr-0" / "q8_0"
            for path in (blob, manifest):
                path.parent.mkdir(parents=True)
            blob.write_bytes(b"ok")
            manifest.write_text(json.dumps({"config": reference, "layers": [reference]}), encoding="utf-8")
            settings = {"LOCALAPPDATA": str(root), "OLLAMA_MODELS": str(store)}
            with (patch.dict(os.environ, settings, clear=True),
                  patch("ocr_runtime.shutil.which", return_value=None),
                  patch("ocr_runtime.build_opener"),
                  patch("ocr_runtime.subprocess.Popen") as start):
                self.assertEqual(ollama_executable(), command)
                self.assertTrue(ocr_readiness()["xiaomi"].ready)
                start.return_value.__enter__.return_value.poll.side_effect = [None, 0]
                with local_server(port=54321) as (_, env):
                    self.assertEqual(env["OLLAMA_MODELS"], str(store.resolve()))
                self.assertEqual(start.call_args.args[0], [str(command), "serve"])
                self.assertEqual(start.call_args.kwargs["env"]["OLLAMA_MODELS"], str(store.resolve()))


if __name__ == "__main__":
    unittest.main()
