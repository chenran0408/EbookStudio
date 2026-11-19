from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image
from pypdf import PdfReader, PdfWriter

import conversion
from ebook_core import publish_file


class ConversionTests(unittest.TestCase):
    def test_required_tools_match_single_and_two_stage_routes(self):
        for source, target, tools in (
            ("epub", "epub2", ("calibre",)),
            ("md", "epub2", ("pandoc",)),
            ("md", "pdf", ("pandoc", "calibre")),
            ("mobi", "md", ("calibre", "pandoc")),
            ("epub", "kepub.epub", ("calibre",)),
            ("cbz", "pdf", ()),
            ("cbr", "cbz", ("calibre",)),
        ):
            with self.subTest(source=source, target=target):
                self.assertEqual(conversion.required_tools(source, target), tools)

    def test_publish_preserves_destination_created_during_write(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            target = folder / "book.epub"
            with self.assertRaises(FileExistsError):
                with publish_file(target, overwrite=False) as staged:
                    staged.write_bytes(b"prepared")
                    target.write_bytes(b"appeared")
            self.assertEqual(target.read_bytes(), b"appeared")
            self.assertEqual(list(folder.iterdir()), [target])
            with publish_file(target) as staged:
                staged.write_bytes(b"replace approved")
            self.assertEqual(target.read_bytes(), b"replace approved")

    def test_markdown_failed_publication_removes_only_new_assets(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "book.epub"
            target = folder / "book.md"
            source.write_bytes(b"epub")
            preserved = folder / "book-assets"
            preserved.mkdir()
            (preserved / "keep.png").write_bytes(b"preserved")

            def fake_converter(tool, args, cwd):
                sidecar = next(value.partition("=")[2] for value in args if value.startswith("--extract-media="))
                (cwd / sidecar).mkdir()
                (cwd / sidecar / "new.png").write_bytes(b"new resource")
                Path(args[args.index("-o") + 1]).write_bytes(b"new draft")
                target.write_bytes(b"appeared while converting")

            with patch.object(conversion, "_run", side_effect=fake_converter):
                with self.assertRaises(FileExistsError):
                    conversion.convert(source, target, overwrite=False)
            self.assertEqual(target.read_bytes(), b"appeared while converting")
            self.assertEqual((preserved / "keep.png").read_bytes(), b"preserved")
            self.assertEqual(set(folder.iterdir()), {source, target, preserved})

    def test_device_extensions_and_options(self):
        self.assertEqual(conversion.normalize_format("book.KEPUB.EPUB"), "kepub.epub")
        self.assertEqual(conversion.normalize_format("book.fb2.zip"), "fbz")
        self.assertIn("epub", conversion.targets_for("book.mobi"))
        self.assertNotIn("azw", conversion.targets_for("book.epub"))
        self.assertNotIn("md", conversion.targets_for("comic.cbr"))

        with TemporaryDirectory() as directory:
            source = Path(directory) / "book.epub"
            source.write_bytes(b"epub")
            target = source.with_name("book.kepub.epub")

            def fake_converter(tool, args, cwd):
                self.assertEqual(tool, "calibre")
                self.assertEqual(Path(args[1]).suffix, ".kepub")
                Path(args[1]).write_bytes(b"kepub")

            with patch.object(conversion, "_run", side_effect=fake_converter):
                conversion.convert(source, target)
            self.assertEqual(target.read_bytes(), b"kepub")

    def test_epub2_export_uses_requested_version(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "book.md"
            source.write_text("# Sample", encoding="utf-8")

            def fake_converter(tool, args, cwd):
                self.assertEqual(tool, "pandoc")
                self.assertEqual(args[args.index("-t") + 1], "epub2")
                Path(args[args.index("-o") + 1]).write_bytes(b"epub2")

            with patch.object(conversion, "_run", side_effect=fake_converter):
                target = source.with_suffix(".epub")
                conversion.convert(source, target, epub_version=2)
                self.assertEqual(target.read_bytes(), b"epub2")

    def test_failure_preserves_existing_destination(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "book.md"
            target = source.with_suffix(".epub")
            source.touch()
            target.write_bytes(b"original")

            def fake_converter(tool, args, cwd):
                Path(args[args.index("-o") + 1]).write_bytes(b"partial")
                raise conversion.ConversionError("backend failed")

            with patch.object(conversion, "_run", side_effect=fake_converter):
                with self.assertRaisesRegex(conversion.ConversionError, "backend failed"):
                    conversion.convert(source, target)
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(sorted(Path(directory).iterdir()), sorted([source, target]))

    def test_scanned_pdf_needs_ocr_before_text_conversion(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "scan.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with source.open("wb") as stream:
                writer.write(stream)
            with patch.object(conversion, "_run") as runner:
                with self.assertRaisesRegex(conversion.ConversionError, "OCR"):
                    conversion.convert(source, source.with_suffix(".epub"))
                runner.assert_not_called()

    def test_comic_archive_page_order_and_pdf_export(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "comic.cbz"
            with ZipFile(source, "w") as archive:
                for page, size in ((10, (30, 40)), (2, (45, 60))):
                    buffer = BytesIO()
                    Image.new("RGB", size, "tomato").save(buffer, format="PNG")
                    archive.writestr(f"Pages/{page}.png", buffer.getvalue())
            pages = conversion.read_comic(source)
            self.assertEqual([name for name, _ in pages], ["Pages/2.png", "Pages/10.png"])

            target = source.with_suffix(".pdf")
            conversion.convert(source, target)
            reader = PdfReader(target)
            self.assertEqual(len(reader.pages), 2)
            self.assertGreater(reader.pages[0].mediabox.width, reader.pages[1].mediabox.width)

            reordered = source.with_name("reordered.cbz")
            conversion.write_cbz(list(reversed(pages)), reordered)
            self.assertEqual(conversion.read_comic(reordered)[0][1], pages[1][1])
            with self.assertRaises(FileExistsError):
                conversion.write_cbz(pages, reordered, overwrite=False)
            self.assertEqual(conversion.read_comic(reordered)[0][1], pages[1][1])


if __name__ == "__main__":
    unittest.main()
