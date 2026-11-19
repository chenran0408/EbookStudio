import tempfile
import unittest
from pathlib import Path

from lxml import html
from PIL import Image

from paddle_content import paddle_chapter


class PaddleContentTests(unittest.TestCase):
    def test_keeps_paddle_table_spans_footnotes_and_embedded_crop(self):
        markdown = """## 工坊记录

注记 $ ^{①} $

<table border=1><tr><td rowspan="2" colspan="2" style="color:red">湿纸移交</td><td>晾置</td></tr><tr><td>点验</td></tr></table>

<div><img src="imgs/figure.jpg" width="42%" alt="工坊插图" /></div>

![外部图片](https://example.org/image.png)
<script>alert(1)</script>
"""
        with tempfile.TemporaryDirectory() as scratch:
            crop = Path(scratch) / "crop.png"
            Image.new("RGB", (2, 2), "white").save(crop)
            chapter, assets = paddle_chapter(markdown, {"imgs/figure.jpg": crop}, 4)

        body = html.fragment_fromstring(chapter.html)
        self.assertEqual("第 4 页 · 工坊记录", chapter.title)
        self.assertEqual("湿纸移交", body.xpath("string(.//td[@rowspan='2' and @colspan='2'])"))
        self.assertEqual("①", body.xpath("string(.//sup)"))
        self.assertEqual(list(assets), body.xpath(".//img/@src"))
        self.assertEqual("工坊插图", body.xpath("string(.//img/@alt)"))
        self.assertFalse(body.xpath(".//img/@width | .//script | .//*[@style]"))
