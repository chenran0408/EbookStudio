"""Import local PDF/image pages using Xiaomi-OCR-0 through loopback Ollama."""

from contextlib import closing, contextmanager
from dataclasses import dataclass
from functools import lru_cache
from hashlib import file_digest
from html import escape
from io import BytesIO
import base64
import json
import math
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from markdown_it import MarkdownIt
from PIL import Image, ImageOps

from ebook_core import Book, Chapter


DEFAULT_URL = "http://127.0.0.1:11437"
DEFAULT_MODEL = "xiaomi-ocr-0:q8_0"
MAX_TOKENS = 8192
CONTEXT_TOKENS = 16384
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
# Single-page prompt and chat template from the Xiaomi-OCR-0 inference protocol.
# https://github.com/xiaomi-research/Xiaomi-OCR-0/blob/main/OLLAMA.md
DOCUMENT_PROMPT = (
    "Extract all information from the main body of the document image and represent it "
    "in markdown format, ignoring headers and footers. Tables should be expressed in OTSL "
    "format, formulas in the document should be represented using LATEX format, and the "
    "parsing should be organized according to the reading order."
)
OTSL = re.compile(r"<(fcel|ecel|lcel|ucel|xcel|nl)>")
FENCE = re.compile(r"^[ \t]{0,3}(?P<marker>\x60{3,}|~{3,})(?P<label>.*)$")
INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


class OcrError(ValueError):
    """An OCR failure to report to the user."""


def scan_digest(source: Path) -> str:
    """Fingerprint the original scan without loading it all into memory."""
    try:
        with source.open("rb") as stream:
            return file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        raise OcrError(f"无法读取扫描件 {source.name}：{exc}") from exc


def scan_provenance(source: Path, digest: str, page: int, engine: str | None) -> dict:
    """Record the absolute source path, SHA-256, 1-based page and OCR engine key."""
    return {"path": str(source), "sha256": digest, "page": page, "engine": engine}


def _page_numbers(selection: str, count: int) -> list[int]:
    if count < 1:
        raise OcrError("文件没有可识别的页面。")
    if not selection.strip():
        return list(range(1, count + 1))
    selected = set()
    for part in selection.replace("，", ",").replace("–", "-").split(","):
        span = re.fullmatch(r"\s*([0-9]+)(?:\s*-\s*([0-9]+))?\s*", part)
        if not span:
            raise OcrError("请按 1,3-5 格式输入页码，或留空以识别全部页面。")
        start = int(span.group(1))
        end = int(span.group(2) or span.group(1))
        if not 1 <= start <= end <= count:
            raise OcrError(f"页码范围 {part.strip()} 无效。此文件共 {count} 页。")
        selected.update(range(start, end + 1))
    return sorted(selected)


def scan_rgb(image: Image.Image) -> Image.Image:
    """Apply scan orientation and flatten transparency against white."""
    source = ImageOps.exif_transpose(image)
    if "A" in source.getbands() or "transparency" in source.info:
        foreground = source.convert("RGBA")
        background = Image.new("RGBA", source.size, "white")
        source = Image.alpha_composite(background, foreground)
    return source.convert("RGB")


def _prepared_png(image: Image.Image) -> bytes | None:
    """Scale to the model's 32-pixel grid. None represents a uniformly white page."""
    rgb = scan_rgb(image)
    if all((low, high) == (255, 255) for low, high in rgb.getextrema()):
        return None
    width, height = rgb.size
    if max(width, height) > min(width, height) * 200:
        raise OcrError("图像长宽比例超过模型上限，请先裁成较短的图像。")
    rgb.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    width, height = rgb.size
    resized = tuple(max(32, round(edge / 32) * 32) for edge in rgb.size)
    if resized[0] * resized[1] < 65536:
        factor = math.sqrt(65536 / (width * height))
        resized = tuple(max(32, math.ceil(edge * factor / 32) * 32) for edge in rgb.size)
    with BytesIO() as output:
        rendered = rgb.resize(resized, Image.Resampling.BICUBIC) if rgb.size != resized else rgb
        rendered.save(output, "PNG")
        return output.getvalue()


def _loopback(base_url: str) -> str:
    endpoint = urlsplit(base_url.strip())
    if (
        endpoint.scheme != "http"
        or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}
        or endpoint.username is not None or endpoint.password is not None
        or endpoint.path.rstrip("/") not in {"", "/api", "/v1"}
        or endpoint.query or endpoint.fragment
    ):
        raise OcrError("本地 OCR 仅接受回环地址，例如 http://127.0.0.1:11437。")
    try:
        endpoint.port
    except ValueError as exc:
        raise OcrError("本地 OCR 地址的端口无效。") from exc
    return f"{endpoint.scheme}://{endpoint.netloc}"


class _LocalOnlyRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The request body contains a copy of the user's page.
        raise OcrError("本地 OCR 地址返回了重定向，识别请求已停止。")


def _recognize(image: bytes, endpoint: str, model: str, max_tokens: int) -> str:
    prompt = (
        f"<|im_start|>user\n[img-0]{DOCUMENT_PROMPT}"
        "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )
    payload = dict(
        model=model,
        prompt=prompt,
        images=[base64.b64encode(image).decode("ascii")],
        raw=True,
        stream=False,
        truncate=False,
        options=dict(temperature=0, repeat_penalty=1, seed=0, num_ctx=CONTEXT_TOKENS, num_predict=max_tokens),
    )
    request = Request(
        endpoint + "/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    # Do not inherit proxy variables from the host for a loopback request.
    opener = build_opener(ProxyHandler({}), _LocalOnlyRedirects())
    try:
        with opener.open(request, timeout=600) as response:
            reply = json.load(response)
    except HTTPError as exc:
        with exc:
            detail = exc.read(1000).decode("utf-8", errors="replace")
        raise OcrError(f"本地 OCR 返回 HTTP {exc.code}：{detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise OcrError(f"无法连接本地 OCR 服务 {endpoint}，或识别超时：{exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OcrError("本地 OCR 返回了无效的响应。") from exc
    if not isinstance(reply, dict):
        raise OcrError("本地 OCR 返回了无效的响应。")
    if reply.get("error"):
        raise OcrError(str(reply["error"]))
    if reply.get("done_reason") == "length" or (
        not reply.get("done_reason") and (reply.get("eval_count") or 0) >= max_tokens
    ):
        raise OcrError(f"识别结果达到 {max_tokens} 个词元的上限，可能漏掉内容。可将此页拆开后重试。")
    text = reply.get("response")
    if not isinstance(text, str) or not text.strip():
        raise OcrError("模型没有返回可用的文字。")
    return text


def _latex(state, silent):
    """Keep LaTeX delimiters and backslashes as editable text."""
    delimiters = ((r"\(", r"\)"), (r"\[", r"\]"), ("$$", "$$"), ("$", "$"))
    for left, right in delimiters:
        if not state.src.startswith(left, state.pos):
            continue
        if left == "$" and state.src[state.pos + 1:state.pos + 2].isspace():
            return False
        start = state.pos + len(left)
        end = state.src.find(right, start, state.posMax)
        if end <= start or not state.src[start:end].strip():
            return False
        resolved = end + len(right)
        if not silent:
            state.push("code_inline", "code", 0).content = state.src[state.pos:resolved]
        state.pos = resolved
        return True
    return False


@lru_cache(maxsize=1)
def _markdown() -> MarkdownIt:
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    parser.inline.add_terminator_char("$")
    parser.inline.ruler.before("escape", "literal_latex", _latex)
    # Recognized URLs and image syntax should not turn into outbound requests.
    parser.renderer.rules.update(
        link_open=lambda *_: "",
        link_close=lambda *_: "",
        image=lambda tokens, index, *_: escape(tokens[index].content, quote=False),
    )
    return parser


@dataclass(eq=False)
class _Cell:
    row: int
    column: int
    text: str
    bottom: int
    right: int


def _table_html(markup: str) -> str:
    tokens = OTSL.split(markup.strip())
    if tokens[0].strip() or len(tokens) == 1:
        raise OcrError("无法解析模型返回的 OTSL 表格。")
    rows = [[]]
    for tag, value in zip(tokens[1::2], tokens[2::2]):
        if tag == "nl":
            rows.append([])
        else:
            rows[-1].append((tag, value))
        if tag != "fcel" and value.strip():
            raise OcrError("OTSL 表格的合并单元格含有无法归属的文字。")
    if not rows[-1]:
        rows.pop()
    if not rows or any(not row for row in rows):
        raise OcrError("模型返回了空的 OTSL 表格行。")
    width = max(map(len, rows))
    grid: list[list[_Cell]] = []
    origins: list[_Cell] = []
    for r, row in enumerate(rows):
        current: list[_Cell] = []
        grid.append(current)
        for c in range(width):
            tag, text = row[c] if c < len(row) else ("ecel", "")
            if tag in {"fcel", "ecel"}:
                cell = _Cell(r, c, text.strip(), r, c)
                origins.append(cell)
            else:
                left = current[-1] if c else None
                up = grid[r - 1][c] if r else None
                cell = left if tag == "lcel" else up
                if cell is None or (tag == "xcel" and up is not left):
                    raise OcrError("模型返回了无效的 OTSL 合并单元格。")
                cell.bottom = max(cell.bottom, r)
                cell.right = max(cell.right, c)
            current.append(cell)
    by_row = [[] for _ in rows]
    for cell in origins:
        if any(
            grid[r][c] is not cell
            for r in range(cell.row, cell.bottom + 1)
            for c in range(cell.column, cell.right + 1)
        ):
            raise OcrError("模型返回了互相重叠的 OTSL 合并单元格。")
        rowspan, colspan = cell.bottom - cell.row + 1, cell.right - cell.column + 1
        attrs = (f' rowspan="{rowspan}"' if rowspan > 1 else "") + (f' colspan="{colspan}"' if colspan > 1 else "")
        by_row[cell.row].append(f"<td{attrs}>{_markdown().renderInline(cell.text)}</td>")
    return "<table><tbody>" + "".join("<tr>" + "".join(cells) + "</tr>" for cells in by_row) + "</tbody></table>\n"


def _render_markdown(text: str) -> str:
    """Render regular Markdown and OTSL without evaluating the model's raw HTML."""
    text = INVALID_XML.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"(\n[ \t]*\n)", text)
    output, normal = [], []
    fence = None

    for index in range(0, len(blocks), 2):
        paragraph = blocks[index]
        separator = blocks[index + 1] if index + 1 < len(blocks) else ""
        stripped = paragraph.lstrip()
        # Whole-page OCR emits tables as standalone markup blocks.
        table = OTSL.match(stripped) if fence is None else None
        if table:
            output.append(_markdown().render("".join(normal)))
            normal.clear()
            output.append(_table_html(stripped))
        else:
            for line in paragraph.splitlines():
                marker = FENCE.match(line)
                if marker:
                    run = marker.group("marker")
                    if fence is None:
                        fence = run
                    elif run[0] == fence[0] and len(run) >= len(fence) and not marker.group("label").strip():
                        fence = None
            normal.append(paragraph + separator)
    output.append(_markdown().render("".join(normal)))
    return "".join(output)


@contextmanager
def scan_pages(source: Path, pages: str, *, pdf_max_edge: int = 2048):
    """Keep a scan open and return its selected page numbers and image reader."""
    is_pdf = source.suffix.lower() == ".pdf"
    if not is_pdf and source.suffix.lower() not in IMAGE_TYPES:
        raise OcrError("请选择 PDF、PNG、JPEG、WebP、BMP 或 TIFF 文件。")
    try:
        if is_pdf:
            import pypdfium2 as pdfium

            container = pdfium.PdfDocument(source)
            count = len(container)
        else:
            container = Image.open(source)
            count = getattr(container, "n_frames", 1)
    except (OSError, RuntimeError) as exc:
        raise OcrError(f"无法打开 {source.name}：{exc}") from exc

    with closing(container):
        selected = _page_numbers(pages, count)

        def read_image(page):
            if not is_pdf:
                container.seek(page - 1)
                return container
            with closing(container[page - 1]) as pdf_page:
                scale = min(200 / 72, pdf_max_edge / max(pdf_page.get_size()))
                with closing(pdf_page.render(scale=scale)) as bitmap:
                    return bitmap.to_pil().copy()

        yield selected, read_image


def ocr_book(
    path: str | Path, *, pages: str = "", progress=None, base_url: str = DEFAULT_URL,
    model: str = DEFAULT_MODEL, max_tokens: int = MAX_TOKENS,
) -> Book:
    """Return a chapter per selected page in source order, with editable HTML."""
    source = Path(path).expanduser().resolve()
    endpoint = _loopback(base_url)
    if not 1 <= max_tokens <= MAX_TOKENS:
        raise OcrError(f"识别词元上限应在 1 至 {MAX_TOKENS} 之间。")
    book = Book(title=source.stem)
    with scan_pages(source, pages) as (selected, read_image):
        digest = scan_digest(source)
        if progress:
            progress(0, len(selected), "正在读取页面")
        for offset, page in enumerate(selected):
            try:
                if progress:
                    progress(offset, len(selected), f"正在识别第 {page} 页")
                png = _prepared_png(read_image(page))
                if png is None:
                    body = "<p>（空白页）</p>"
                else:
                    body = _render_markdown(_recognize(png, endpoint, model, max_tokens))
                book.chapters.append(Chapter(
                    title=f"第 {page} 页", html=body,
                    scan=scan_provenance(source, digest, page, "xiaomi"),
                ))
            except (OSError, RuntimeError, ValueError) as exc:
                raise OcrError(f"{source.name} 第 {page} 页：{exc}") from exc
            if progress:
                progress(offset + 1, len(selected), f"第 {page} 页已完成")
    return book

