from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from ocr_import import OcrError, _recognize, _render_markdown, ocr_book


def example_page(letter):
    image = Image.new("RGB", (235, 321), "white")
    ImageDraw.Draw(image).text((15, 20), letter, fill="black")
    return image


class OcrImportTests(unittest.TestCase):
    def test_pdf_uses_page_number_order_and_reports_progress(self):
        with TemporaryDirectory() as folder:
            pdf = Path(folder) / "扫描页.pdf"
            images = [example_page(str(n)) for n in range(1, 4)]
            images[0].save(pdf, save_all=True, append_images=images[1:])
            progress = []
            with patch("ocr_import._recognize", side_effect=["# 第一页", "# 第三页"]) as recognize:
                book = ocr_book(pdf, pages="3, 1, 3", progress=lambda *step: progress.append(step))
            self.assertEqual(book.title, "扫描页")
            self.assertEqual([page.title for page in book.chapters], ["第 1 页", "第 3 页"])
            self.assertIn("<h1>第三页</h1>", book.chapters[1].html)
            self.assertEqual(progress[-1][:2], (2, 2))
            for call in recognize.call_args_list:
                raster = Image.open(BytesIO(call.args[0]))
                self.assertEqual(raster.format, "PNG")
                self.assertTrue(all(edge % 32 == 0 for edge in raster.size))

    def test_truncated_result_identifies_the_page_and_does_not_return_partial_book(self):
        with TemporaryDirectory() as folder:
            file = Path(folder) / "原稿.tiff"
            example_page("1").save(file, save_all=True, append_images=[example_page("2")])
            recognized = ["第一帧", OcrError("识别结果达到词元的上限")]
            with patch("ocr_import._recognize", side_effect=recognized):
                with self.assertRaisesRegex(OcrError, "原稿.tiff 第 2 页.*达到词元的上限"):
                    ocr_book(file)

    def test_otsl_spans_and_latex_are_editable_and_remote_content_is_inert(self):
        source = (
            "# 原始文字\n\n"
            "<fcel>左侧<lcel><fcel>右侧<nl><ucel><xcel><fcel>第二行<nl>\n\n"
            r"\(\alpha_{\beta}\)" "\n\n"
            "<script>alert('x')</script> ![插图](https://example.invalid/secret.png)\n\n"
            "| 项目 | 数量 |\n| --- | --- |\n| 书稿 | 3 |\n"
        )
        rendered = _render_markdown(source)
        self.assertIn('<td rowspan="2" colspan="2">左侧</td>', rendered)
        self.assertIn("<th>数量</th>", rendered)
        self.assertIn(r"<code>\(\alpha_{\beta}\)</code>", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertNotIn("https://example.invalid", rendered)
        self.assertIn("插图", rendered)

    def test_transport_uses_the_checkpoint_prompt_and_rejects_output_limit(self):
        with patch("ocr_import.build_opener") as make_opener:
            request_open = make_opener.return_value.open
            request_open.return_value = BytesIO(b'{"response": "# OCR", "done_reason":"stop"}')
            text = _recognize(b"sample", "http://127.0.0.1:11437", "test", 1024)
            self.assertEqual(text, "# OCR")
            request = request_open.call_args.args[0]
            payload = json.loads(request.data)
            self.assertEqual(request.full_url, "http://127.0.0.1:11437/api/generate")
            self.assertTrue(payload["raw"])
            self.assertIn("[img-0]Extract all information", payload["prompt"])
            self.assertTrue(payload["prompt"].endswith("<think>\n\n</think>\n\n"))
            self.assertEqual(payload["options"]["num_predict"], 1024)
            request_open.return_value = BytesIO(b'{"response": "partial", "done_reason":"length"}')
            with self.assertRaisesRegex(OcrError, "上限"):
                _recognize(b"sample", "http://127.0.0.1:11437", "test", 1024)

    def test_page_selection_and_network_boundary(self):
        with patch("ocr_import.build_opener") as make_opener:
            with self.assertRaisesRegex(OcrError, "回环"):
                ocr_book("sample.png", base_url="http://127.0.0.1@example.com:11437")
            self.assertFalse(make_opener.called)
        with TemporaryDirectory() as folder:
            file = Path(folder) / "scan.png"
            example_page("1").save(file)
            with self.assertRaisesRegex(OcrError, "页码范围"):
                ocr_book(file, pages="1-3")

    def test_blank_page_is_preserved_without_model_hallucinations(self):
        with TemporaryDirectory() as folder:
            file = Path(folder) / "blank.png"
            Image.new("RGB", (600, 800), "white").save(file)
            with patch("ocr_import._recognize") as recognize:
                result = ocr_book(file)
            recognize.assert_not_called()
            self.assertEqual(len(result.chapters), 1)
            self.assertIn("空白页", result.chapters[0].html)


if __name__ == "__main__":
    unittest.main()

