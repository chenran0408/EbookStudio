"""Import user-selected web pages into an editable book."""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote_to_bytes, urldefrag, urljoin, urlsplit, urlunsplit
from urllib.request import url2pathname

import requests
from lxml import etree
from lxml import html as lhtml
from PIL import Image, UnidentifiedImageError

from ebook_core import Asset, Chapter


class CaptureError(ValueError):
    """An input could not be turned into a readable chapter."""


@dataclass(frozen=True)
class Candidate:
    title: str
    url: str


_WEB = {"http", "https"}
_TIMEOUT = (7, 20)
_PAGE_LIMIT = 16 * 1024 * 1024
_IMAGE_LIMIT = 32 * 1024 * 1024
_QIUSHI_CONTENT = (
    '//*[@id="detailContent"]',
    '//*[@id="content"]',
    '//*[contains(concat(" ", normalize-space(@class), " "), " TRS_Editor ")]',
)
_ARTICLE_BODY = (
    "//article",
    '//*[contains(concat(" ", normalize-space(@class), " "), " entry-content ")]',
    '//*[contains(concat(" ", normalize-space(@class), " "), " article-content ")]',
    '//*[contains(concat(" ", normalize-space(@class), " "), " article-body ")]',
)
_DROP = {
    "script", "style", "iframe", "frame", "object", "embed", "base", "link",
    "form", "button", "input", "select", "textarea", "noscript", "template",
    "nav", "header", "footer", "aside", "video", "audio", "canvas", "svg",
}
_KEEP = {
    "p", "div", "section", "article", "main", "aside", "span", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6", "a", "em", "strong", "i", "b",
    "u", "s", "mark", "sup", "sub", "small", "abbr", "cite", "time", "q",
    "ruby", "rt", "rp", "code", "pre", "kbd", "samp", "var", "blockquote",
    "ul", "ol", "li", "dl", "dt", "dd", "table", "thead", "tfoot", "tbody",
    "tr", "td", "th", "col", "colgroup", "caption", "figure", "figcaption", "img",
}
_ATTRIBUTES = {
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "ol": {"start", "type", "reversed"},
    "li": {"value"},
    "td": {"rowspan", "colspan", "headers"},
    "th": {"rowspan", "colspan", "scope", "headers"},
    "col": {"span"},
    "colgroup": {"span"},
    "time": {"datetime"},
}
_COMMON_ATTRIBUTES = {"id", "lang", "dir", "title"}
_NAV_LABELS = {
    "首页", "上一页", "下一页", "上一章", "下一章", "上一期", "下一期",
    "目录", "返回", "返回顶部", "登录", "注册", "网站声明", "网站声明页",
    "next", "previous", "home", "contents", "log in", "sign in",
}
_IMAGE_FORMATS = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "GIF": ("gif", "image/gif"),
    "WEBP": ("webp", "image/webp"),
}


def _web_url(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in _WEB or not parsed.hostname or parsed.username is not None:
        raise CaptureError("请输入完整的 HTTP 或 HTTPS 网址。")
    return value


def web_url_key(value: str) -> str:
    """Identify a public article independently of its page fragment."""
    url = urlsplit(_web_url(value))
    return urlunsplit((url.scheme, url.netloc.lower(), url.path or '/', url.query, ''))


def web_revision(chapter: Chapter) -> str:
    data = json.dumps([chapter.title, chapter.html], ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def _download(url: str, limit: int) -> tuple[bytes, str, str, str | None]:
    try:
        with requests.get(
            _web_url(url), timeout=_TIMEOUT, stream=True,
            headers={"User-Agent": "EbookStudio/1.0"},
        ) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                data.extend(chunk)
                if len(data) > limit:
                    raise CaptureError(f"下载超过 {limit // (1024 * 1024)} MB，已停止：{url}")
            media_type = response.headers.get("Content-Type", "")
            return bytes(data), response.url, media_type, response.encoding
    except requests.RequestException as exc:
        raise CaptureError(f"无法读取 {url}：{exc}") from exc


def _load_page(url: str, rendered: bool) -> tuple[str | bytes, str]:
    _web_url(url)
    if not rendered:
        data, final_url, media_type, encoding = _download(url, _PAGE_LIMIT)
        if "charset=" in media_type.lower() and encoding:
            try:
                return data.decode(encoding), final_url
            except (UnicodeError, LookupError):
                pass  # HTML parser can still use the document's encoding declaration.
        return data, final_url

    try:
        from playwright.sync_api import Error as BrowserError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CaptureError("动态网页需要安装 Playwright 和 Chromium。") from exc
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(accept_downloads=False)
                response = page.goto(url, wait_until="load", timeout=30_000)
                if response is None or not response.ok:
                    status = response.status if response else "没有响应"
                    raise CaptureError(f"动态网页无法打开：{status}")
                data = page.content()
                if len(data.encode("utf-8")) > _PAGE_LIMIT:
                    raise CaptureError("动态网页内容超过 16 MB。")
                return data, _web_url(page.url)
            finally:
                browser.close()
    except BrowserError as exc:
        raise CaptureError(f"动态网页抓取失败：{exc}") from exc


def _document(raw_html: str | bytes):
    if isinstance(raw_html, bytes):
        try:
            raw_html = raw_html.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass  # The HTML parser can read a meta charset on non-UTF-8 pages.
    try:
        if isinstance(raw_html, str):
            # lxml rejects a Unicode input containing an XML encoding declaration.
            return lhtml.document_fromstring(
                raw_html.encode("utf-8"), parser=lhtml.HTMLParser(encoding="utf-8")
            )
        return lhtml.document_fromstring(raw_html)
    except (etree.ParserError, ValueError) as exc:
        raise CaptureError("页面没有可读取的 HTML。") from exc


def _text(node) -> str:
    return " ".join(node.text_content().split())


def _qiushi(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "qstheory.cn" or host.endswith(".qstheory.cn")


def _site_disclaimer(title: str, url: str) -> bool:
    label = re.sub(r"\s+", "", title).strip("【】[]()（）")
    return label in {"网站声明", "网站声明页", "版权声明"} or (
        _qiushi(url) and "/qssyggw/" in urlsplit(url).path
    )


def _first_content(document, selectors):
    for selector in selectors:
        for node in document.xpath(selector):
            if _text(node) or node.xpath(".//img"):
                return node
    return None


def _rule_node(document, selector: str, label: str):
    try:
        nodes = document.xpath(selector)
    except etree.XPathError as exc:
        raise CaptureError(f'{label} XPath 无效：{exc}') from exc
    if isinstance(nodes, list):
        for node in nodes:
            if isinstance(node, lhtml.HtmlElement):
                return node
    raise CaptureError(f'{label}规则没有匹配到页面元素。')


def _drop_chrome(root, *, qiushi: bool) -> None:
    for node in list(root.iterdescendants()):
        if node.getparent() is None:
            continue
        if not isinstance(node.tag, str):
            node.drop_tree()
            continue
        role = node.get("role", "")
        hidden = "hidden" in node.attrib or node.get("aria-hidden") == "true"
        footnote = (
            node.get("epub:type") in {"footnote", "endnote"}
            or role in {"doc-footnote", "doc-endnote"}
        )
        if hidden or role in {"navigation", "banner", "contentinfo", "search"}:
            node.drop_tree()
        elif node.tag in _DROP and not footnote:
            node.drop_tree()
        elif qiushi and node.tag == "a" and _site_disclaimer(_text(node), node.get("href", "")):
            node.drop_tree()


def _content_root(document, base_url: str, local: bool):
    if local:
        return document.find("body")
    if _qiushi(base_url):
        root = _first_content(document, _QIUSHI_CONTENT)
        if root is not None:
            return root
    root = _first_content(document, _ARTICLE_BODY)
    if root is not None:
        return root
    import trafilatura

    extracted = trafilatura.extract(
        etree.tostring(document, method="html", encoding="unicode"),
        url=base_url or None,
        output_format="html",
        include_comments=False,
        include_images=True,
        include_tables=True,
        include_links=True,
        include_formatting=True,
        favor_precision=True,
    )
    if extracted:
        return _document(extracted).find("body")
    root = _first_content(document, ("//main", '//*[@role="main"]'))
    return root if root is not None else document.find("body")


def discover(url: str, *, rendered: bool = False, directory_xpath: str = '') -> list[Candidate]:
    """List links on a directory page. No linked page is fetched here."""
    raw_html, base_url = _load_page(url, rendered)
    document = _document(raw_html)
    qiushi = _qiushi(base_url)
    body = _rule_node(document, directory_xpath, '目录') if directory_xpath else None
    if body is None and qiushi:
        body = _first_content(document, _QIUSHI_CONTENT)
    if body is None:
        body = _first_content(document, ("//main", '//*[@role="main"]', "//article", "//body"))
    if body is None:
        return []
    _drop_chrome(body, qiushi=qiushi)
    candidates: dict[str, Candidate] = {}
    for anchor in body.xpath("descendant-or-self::a[@href]"):
        label = _text(anchor)
        link = urldefrag(urljoin(base_url, anchor.get("href").strip()))[0]
        parts = urlsplit(link)
        if not label or label.lower().strip("【】[]()（）") in _NAV_LABELS:
            continue
        if parts.scheme not in _WEB or not parts.hostname or parts.username is not None:
            continue
        if link == urldefrag(base_url)[0] or _site_disclaimer(label, link):
            continue
        if qiushi:
            if not _qiushi(link) or not re.search(r"/(?:c\.html|c_[^/]+\.htm)$", parts.path):
                continue
            if re.fullmatch(r"《?求是》?\s*\d{4}\s*年\s*第\s*\d+\s*期", label):
                continue
            existing = candidates.get(link)
            if existing and label not in existing.title:
                candidates[link] = Candidate(f"{existing.title} {label}", link)
                continue
        candidates.setdefault(link, Candidate(label, link))
    return list(candidates.values())


def _srcset(value: str) -> list[str]:
    if not value or value.lstrip().startswith("data:"):
        return []
    candidates = []
    for part in value.split(","):
        fields = part.strip().split()
        if not fields:
            continue
        match = re.fullmatch(r"(\d+(?:\.\d+)?)[wx]", fields[-1]) if len(fields) > 1 else None
        candidates.append((float(match[1]) if match else 1, fields[0]))
    return [url for _, url in sorted(candidates, reverse=True)]


def _image_sources(image) -> list[str]:
    preferred = []
    for key in ("data-src", "data-original", "data-lazy-src", "data-actualsrc"):
        if image.get(key):
            preferred.append(image.get(key))
    for key in ("data-srcset", "srcset"):
        preferred.extend(_srcset(image.get(key, "")))
    parent = image.getparent()
    if parent is not None and parent.tag == "picture":
        for source in parent.findall("source"):
            preferred.extend(_srcset(source.get("srcset", "")))
    if image.get("src"):
        preferred.append(image.get("src"))
    return list(dict.fromkeys(src.strip() for src in preferred if src.strip()))


def _read_data_url(src: str) -> bytes:
    header, sep, value = src.partition(",")
    if not sep or not header.lower().startswith("data:image/") or len(value) > _IMAGE_LIMIT * 2:
        raise CaptureError("内嵌图片格式或大小不受支持。")
    try:
        data = unquote_to_bytes(value)
        return base64.b64decode(data, validate=True) if header.lower().endswith(";base64") else data
    except binascii.Error as exc:
        raise CaptureError("内嵌图片数据不完整。") from exc


def _external_private_host(url: str, base_url: str) -> bool:
    host = urlsplit(url).hostname
    if host == urlsplit(base_url).hostname or host is None:
        return False
    if host == "localhost" or host.endswith(".localhost") or "." not in host and ":" not in host:
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def _image_bytes(src: str, base_url: str, local_root: Path | None) -> bytes:
    if src.lower().startswith("data:"):
        return _read_data_url(src)
    url = urljoin(base_url, src)
    parts = urlsplit(url)
    if parts.scheme in _WEB:
        if _external_private_host(url, base_url):
            raise CaptureError("页面引用了其他本地地址上的图片。")
        data, _, _, _ = _download(url, _IMAGE_LIMIT)
        return data
    if parts.scheme == "file" and local_root is not None and not parts.netloc:
        path = Path(url2pathname(parts.path)).resolve()
        if not path.is_relative_to(local_root):
            raise CaptureError("HTML 引用的图片在导入文件夹之外。")
        if path.stat().st_size > _IMAGE_LIMIT:
            raise CaptureError("图片超过 32 MB。")
        return path.read_bytes()
    raise CaptureError("图片地址格式不受支持。")


def _svg_asset(data: bytes) -> Asset:
    """Keep static vector artwork with its internal fragment references."""
    image = etree.fromstring(data, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    if image.tag != "{http://www.w3.org/2000/svg}svg" or image.getroottree().docinfo.doctype:
        raise CaptureError("图片格式不受支持。")
    for node in list(image.iter()):
        if not isinstance(node.tag, str):
            continue
        name = etree.QName(node).localname.lower()
        if name in {"script", "foreignobject", "style", "a", "animate", "set", "animatetransform"}:
            node.getparent().remove(node)
            continue
        for key, value in list(node.attrib.items()):
            attribute = etree.QName(key).localname.lower()
            links = re.findall(r"url\((.*?)\)", value, flags=re.IGNORECASE)
            if (
                attribute.startswith("on") or attribute in {"style", "base"}
                or (attribute == "href" and not value.startswith("#"))
                or any(not link.strip(" \"'").startswith("#") for link in links)
            ):
                del node.attrib[key]
    safe_data = etree.tostring(image, encoding="utf-8")
    return Asset(data=safe_data, media_type="image/svg+xml")


def _make_asset(data: bytes) -> tuple[str, Asset]:
    if len(data) > _IMAGE_LIMIT:
        raise CaptureError("图片超过 32 MB。")
    if data.lstrip().startswith((b"<svg", b"<?xml")):
        asset = _svg_asset(data)
        extension = "svg"
    else:
        with Image.open(BytesIO(data)) as image:
            image.verify()
            preferred = _IMAGE_FORMATS.get(image.format)
        if preferred is None:
            # Keep unsupported raster formats readable on EPUB readers.
            out = BytesIO()
            with Image.open(BytesIO(data)) as image:
                image.convert("RGBA").save(out, format="PNG")
            data = out.getvalue()
            preferred = _IMAGE_FORMATS["PNG"]
        extension, media_type = preferred
        asset = Asset(data=data, media_type=media_type)
    digest = hashlib.sha256(asset.data).hexdigest()
    return f"images/{digest[:24]}.{extension}", asset


def _stage_images(root, base_url: str, local_root: Path | None, warnings: list[str]):
    assets: dict[str, Asset] = {}
    sources: dict[str, str] = {}
    for image in root.xpath(".//img"):
        refs = _image_sources(image)
        if _qiushi(base_url):
            refs = [ref for ref in refs if not re.search("zxcode|weixin_share|n[67]/images", ref, re.I)]
        last_error = None
        if not refs:
            image.drop_tree()
            continue
        for src in refs:
            try:
                key = sources.get(src)
                if key is None:
                    key, asset = _make_asset(_image_bytes(src, base_url, local_root))
                    assets[key] = asset
                    sources[src] = key
                image.set("src", key)
                image.set("alt", image.get("alt", ""))
                break
            except (CaptureError, OSError, UnidentifiedImageError, etree.XMLSyntaxError,
                    Image.DecompressionBombError, ValueError) as exc:
                last_error = exc
        else:
            label = image.get("alt", "").strip()
            warnings.append(f"未导入图片 {label or refs[0][:160]}：{last_error}")
            if label:
                replacement = lhtml.Element("span")
                replacement.text = f"[图片：{label}]"
                replacement.tail = image.tail
                image.getparent().replace(image, replacement)
            else:
                image.drop_tree()
    return assets


def _normalize(root, base_url: str) -> str:
    for node in list(root.iterdescendants()):
        if node.getparent() is None:
            continue
        if node.tag not in _KEEP:
            node.drop_tag()
            continue
        allowed = _COMMON_ATTRIBUTES | _ATTRIBUTES.get(node.tag, set())
        for key in list(node.attrib):
            if key not in allowed:
                del node.attrib[key]
        if node.tag == "a" and node.get("href"):
            href = node.get("href").strip()
            if href.startswith("#"):
                node.set("href", href)
            else:
                link = urljoin(base_url, href)
                if urlsplit(link).scheme in _WEB | {"mailto"}:
                    node.set("href", link)
                else:
                    del node.attrib["href"]
        if node.tag == "p":
            first = node.xpath(".//text()")
            if first:
                text = first[0]
                prefix = re.match(r"^[\t\r\n \u00a0\u2003\u3000]+", text)
                if prefix and ("\u2003" in prefix[0] or "\u3000" in prefix[0]):
                    owner = text.getparent()
                    if text.is_tail:
                        owner.tail = text[prefix.end():]
                    else:
                        owner.text = text[prefix.end():]
    wrapper = lhtml.Element("div")
    wrapper.text = root.text
    for child in list(root):
        wrapper.append(child)
    return etree.tostring(wrapper, method="xml", encoding="unicode")


def capture_html(raw_html: str | bytes, base_url: str = "", title: str = "", *,
                 warnings: list[str] | None = None, article_xpath: str = '') -> tuple[Chapter, dict[str, Asset]]:
    """Extract a single chapter and return chapter-relative asset references.

    Provide a file URI for local HTML, its HTTP(S) URL for a web snapshot, or
    an empty base for HTML pasted without a known source.
    """
    document = _document(raw_html)
    local = urlsplit(base_url).scheme not in _WEB
    local_root = None
    if urlsplit(base_url).scheme == "file":
        local_root = Path(url2pathname(urlsplit(base_url).path)).resolve().parent
    root = _rule_node(document, article_xpath, '正文') if article_xpath else _content_root(document, base_url, local)
    if root is None:
        raise CaptureError("页面没有正文。")
    if not title.strip():
        title = next((_text(node) for node in root.xpath(".//h1") if _text(node)), "")
        if not title:
            title = next((_text(node) for node in document.xpath("//head/title") if _text(node)), "")
        if not title and local_root:
            title = Path(url2pathname(urlsplit(base_url).path)).stem
    _drop_chrome(root, qiushi=_qiushi(base_url))
    report = warnings if warnings is not None else []
    assets = _stage_images(root, base_url, local_root, report)
    if not _text(root) and not assets:
        raise CaptureError("没有找到可以加入书稿的正文或图片。")
    chapter = Chapter(title.strip() or "未命名章节", _normalize(root, base_url), source=base_url)
    return chapter, assets


def capture(url: str, *, rendered: bool = False,
            warnings: list[str] | None = None, article_xpath: str = '') -> tuple[Chapter, dict[str, Asset]]:
    """Fetch only the specified article and its images."""
    raw_html, final_url = _load_page(url, rendered)
    chapter, assets = capture_html(raw_html, final_url, warnings=warnings, article_xpath=article_xpath)
    revision = web_revision(chapter)
    chapter.web = {'url': web_url_key(url), 'remote': revision, 'local': revision}
    return chapter, assets


def capture_file(path: str | Path, *,
                 warnings: list[str] | None = None) -> tuple[Chapter, dict[str, Asset]]:
    file = Path(path).resolve()
    if file.stat().st_size > _PAGE_LIMIT:
        raise CaptureError("HTML 文件超过 16 MB。")
    return capture_html(file.read_bytes(), file.as_uri(), warnings=warnings)
