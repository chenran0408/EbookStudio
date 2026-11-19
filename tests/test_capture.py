import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

from lxml import etree
from PIL import Image

from capture import capture, capture_file, capture_html, discover


def demo_image() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 5), color="#c43622").save(buffer, "PNG")
    return buffer.getvalue()


class CaptureTests(unittest.TestCase):
    def test_directory_and_linked_article_capture(self):
        image = demo_image()
        pages = {
            "/contents": ("text/html", b"""
                <html><body><nav><a href="/home">Home</a></nav><main>
                <a href="/chapter-one">First chapter</a>
                <a href="/chapter-one#two">Duplicate chapter</a>
                <a href="/chapter-two">Second chapter</a>
                </main></body></html>
            """),
            "/chapter-one": ("text/html; charset=utf-8", """
                <html><head><title>Browser title</title></head><body>
                <article><!-- site comment --><h1>山野笔记</h1>
                <p>　　安装 <code>demo</code> 后开始。</p>
                <figure><img src="/placeholder" data-src="/image" alt="示意图" onload="demo()">
                    <figcaption>第一张图</figcaption></figure>
                <table><caption>样本表</caption><tr><td colspan="2">数据</td></tr></table>
                <p id="note">参考<a href="#note">本节</a><a href="/reading">资料</a>
                <a href="javascript:demo()">链接文本</a></p>
                <script>window.BAD_SCRIPT = true;</script>
                <footer>服务条款</footer>
                </article></body></html>
            """.encode("utf-8")),
            "/image": ("image/png", image),
        }
        visited = []

        class Site(BaseHTTPRequestHandler):
            def do_GET(self):
                visited.append(self.path)
                if self.path not in pages:
                    self.send_error(404)
                    return
                media_type, body = pages[self.path]
                self.send_response(200)
                self.send_header("Content-Type", media_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Site) as server:
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                candidates = discover(f"{base}/contents")
                self.assertEqual([item.title for item in candidates], ["First chapter", "Second chapter"])
                self.assertEqual(visited, ["/contents"])
                chapter, assets = capture(candidates[0].url)
                self.assertEqual(visited, ["/contents", "/chapter-one", "/image"])
            finally:
                server.shutdown()
                worker.join()
        self.assertEqual(chapter.title, "山野笔记")
        self.assertEqual(chapter.source, f"{base}/chapter-one")
        body = etree.fromstring(chapter.html.encode("utf-8"))
        asset_key = body.find(".//img").attrib["src"]
        self.assertEqual(assets[asset_key].data, image)
        self.assertEqual(body.findtext(".//figcaption").strip(), "第一张图")
        self.assertEqual(body.find(".//td").get("colspan"), "2")
        self.assertEqual(body.find(".//p").text, "安装 ")
        self.assertEqual([node.get("href") for node in body.findall(".//a")],
                         ["#note", f"{base}/reading", None])
        for bad in ("onload", "BAD_SCRIPT", "服务条款", "site comment"):
            self.assertNotIn(bad, chapter.html)

    def test_local_html_inlines_its_images_but_not_files_outside_folder(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "private.png").write_bytes(demo_image())
            folder = root / "原稿"
            folder.mkdir()
            (folder / "插图.png").write_bytes(demo_image())
            page = folder / "正文.html"
            page.write_text(
                '<h1>自制文稿</h1><p>这是 <em>本地书稿</em>。</p>'
                '<img src="插图.png" alt="正文图"><img src="../private.png" alt="外部文件">',
                encoding="utf-8",
            )
            warnings = []
            chapter, assets = capture_file(page, warnings=warnings)
        self.assertEqual(chapter.title, "自制文稿")
        self.assertEqual(len(assets), 1)
        self.assertEqual(len(warnings), 1)
        self.assertIn("在导入文件夹之外", warnings[0])
        self.assertIn("本地书稿", chapter.html)

    def test_qiushi_issue_preserves_order_and_excludes_site_links(self):
        source = "https://www.qstheory.cn/20260801/issue/c.html"
        fixture = """
            <div class="content">
                <a href="/20260801/advert/c.html">横幅广告</a>
                <div id="detailContent">
                    <p><a href="/20260801/a/c.html">第一篇</a></p>
                    <p><a href="/20260801/a/c.html">第一篇的副标题</a></p>
                    <p><a href="/20260801/b/c.html">第二篇</a></p>
                    <p><a href="/qssyggw/2021-02/18/c_1112042256.htm">【网站声明】</a></p>
                    <a href="https://else.example/20260801/c/c.html">站外新闻</a>
                </div>
            </div>
        """
        with patch("capture._load_page", return_value=(fixture, source)):
            candidates = discover(source)
        self.assertEqual([item.title for item in candidates], ["第一篇 第一篇的副标题", "第二篇"])

    def test_capture_copied_html_and_xhtml(self):
        chapter, assets = capture_html("<h1>复制原稿</h1><p>测试手稿。</p>")
        self.assertEqual(chapter.title, "复制原稿")
        self.assertIn("测试手稿", chapter.html)
        self.assertEqual(assets, {})
        chapter, _ = capture_html(
            '<?xml version="1.0" encoding="UTF-8"?><html><head><title>原稿</title>'
            '</head><body><p>第二份手稿。</p></body></html>'
        )
        self.assertEqual(chapter.title, "原稿")
        self.assertIn("第二份手稿", chapter.html)

    def test_generic_article_extractor(self):
        narrative = [
            "出发前，我找到一张手绘的地图。标注的土路通往山脚，走起来还算平缓。",
            "午后云层逐渐消散，溪水映出天空。石桥旁有两处适合休息的地方。",
            "返程的时候，我记录了大致的路线和用时，并把几处岔路画在笔记里。",
        ]
        chapter, _ = capture_html(
            '<html><head><title>无标记的文章</title></head><body>'
            '<nav><a href="/home">入口导航</a></nav><div>'
            + "".join(f"<p>{item}</p>" for item in narrative)
            + '</div><footer>网站页脚</footer></body></html>',
            "https://example.test/note",
        )
        self.assertEqual(chapter.title, "无标记的文章")
        for item in narrative:
            self.assertIn(item, chapter.html)
        self.assertNotIn("入口导航", chapter.html)
        self.assertNotIn("网站页脚", chapter.html)


if __name__ == "__main__":
    unittest.main()
