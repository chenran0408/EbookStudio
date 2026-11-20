"""Structured job entry point used by the console and the local MCP adapter."""
from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
import json
import os
import sys

from lxml import html

import conversion
from ebook_core import Book, Chapter, publish_file
from epub_io import chapter_html
from ocr_import import DEFAULT_MODEL, ocr_book
from ocr_runtime import local_server
from paddle_import import paddle_book
from workspace_tools import conversion_stem, converter_readiness, ocr_readiness, plan_destinations


def _object(properties: dict, *required: str) -> dict:
    result = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        result["required"] = list(required)
    return result


def _string(description: str, **constraints) -> dict:
    return {"type": "string", "description": description, **constraints}


def _boolean(description: str, default: bool = False) -> dict:
    return {"type": "boolean", "description": description, "default": default}


def _array(items: dict, description: str) -> dict:
    return {"type": "array", "description": description, "items": items, "minItems": 1}


_TITLE = _string("Book title.", minLength=1)
_AUTHOR = _string("Author; an empty string clears the author.")
_LANGUAGE = _string("Language tag, such as zh-CN or en.", minLength=1)
_METADATA = {"title": _TITLE, "author": _AUTHOR, "language": _LANGUAGE}
_CHAPTER_PROPERTIES = {
    "title": _string("Chapter heading shown in the table of contents.", minLength=1),
    "html": _string("Editable HTML body fragment, with no enclosing document."),
    "level": {"type": "integer", "minimum": 1, "description": "Table of contents level. Top level is 1."},
    "source": _string("Optional original source URL."),
}
_CHAPTER = _object(_CHAPTER_PROPERTIES, "title", "html")
_CHAPTER_NUMBER = {"type": "integer", "minimum": 1, "description": "Chapter number, starting at 1."}
_SOURCE = _string("Path to an existing local input file.", minLength=1)
_PROJECT = _string("Output .bookproj path in an existing directory.", minLength=1)
_OUTPUT = _string("Output file path with target extension in an existing directory.", minLength=1)
_OVERWRITE = _boolean("Replace an existing output file only. Input files are always protected.")
_STEP = _object({
    "name": _string("Optional step label; the desktop may use the literal search text as its label."),
    "query": _string("Text or regular expression. Keep meaningful spaces."),
    "replacement": _string("Replacement text. Empty deletes the match."),
    "regular": _boolean("Use a regular expression with capture groups."),
    "case_sensitive": _boolean("Match letter case."),
}, "query", "replacement")


def _action(description: str, schema: dict) -> dict:
    return {"description": description, "inputSchema": schema}


ACTIONS = {
    "status": _action("Inspect installed local converters and OCR engines.", _object({})),
    "inspect": _action(
        "Read project or EPUB metadata and chapter headings. Request one chapter to see its text.",
        _object({
            "source": _SOURCE,
            "chapter": _CHAPTER_NUMBER,
            "max_chars": {"type": "integer", "minimum": 1, "maximum": 50000, "default": 12000},
            "include_html": _boolean("Also include the chosen chapter's XHTML body fragment."),
        }, "source"),
    ),
    "create": _action(
        "Create a local editable book project from HTML chapters.",
        _object({
            **_METADATA,
            "chapters": _array(_CHAPTER, "Chapters in reading order."),
            "output": _PROJECT,
            "overwrite": _OVERWRITE,
        }, "chapters", "output"),
    ),
    "update": _action(
        "Edit metadata, replace chapter content or append chapters, then save a new project. Keeps original input.",
        _object({
            "source": _SOURCE,
            "output": _PROJECT,
            "metadata": _object(_METADATA),
            "replace_chapters": _array(
                _object({"chapter": _CHAPTER_NUMBER, **_CHAPTER_PROPERTIES}, "chapter"),
                "Changes to individual existing chapters.",
            ),
            "append_chapters": _array(_CHAPTER, "New chapters after the existing chapters."),
            "overwrite": _OVERWRITE,
        }, "source", "output"),
    ),
    "discover": _action(
        "List chapter candidates from a directory URL without downloading their article bodies.",
        _object({
            "url": _string("HTTP or HTTPS directory URL.", minLength=1),
            "rendered": _boolean("Use the browser renderer for pages that need JavaScript."),
        }, "url"),
    ),
    "capture": _action(
        "Collect selected webpage URLs into an editable project, including offline images.",
        _object({
            "urls": _array(_string("HTTP or HTTPS article URL.", minLength=1), "URLs in reading order."),
            "rendered": _boolean("Use the browser renderer for pages that need JavaScript."),
            "metadata": _object(_METADATA),
            "output": _PROJECT,
            "overwrite": _OVERWRITE,
        }, "urls", "output"),
    ),
    "ocr": _action(
        "Recognize local scanned PDF or images with an installed OCR engine and save editable chapters.",
        _object({
            "source": _SOURCE,
            "engine": _string("Local OCR engine.", enum=["xiaomi", "paddle"], default="xiaomi"),
            "pages": _string("Page ranges, e.g. 1,3-5. Empty selects all pages.", default=""),
            "output": _PROJECT,
            "overwrite": _OVERWRITE,
        }, "source", "output"),
    ),
    "export": _action(
        "Export a book project or EPUB to EPUB, PDF or another supported output format.",
        _object({
            "source": _SOURCE,
            "output": _OUTPUT,
            "format": _string("Target format. Defaults to the output extension. Use epub2 to select EPUB 2."),
            "mobile": _boolean("Apply the mobile image export profile.", True),
            "overwrite": _OVERWRITE,
        }, "source", "output"),
    ),
    "convert": _action(
        "Convert an existing local file to another format, preserving the original file.",
        _object({
            "source": _SOURCE,
            "output": _OUTPUT,
            "format": _string("Target format. Defaults to the output extension. Use epub2 to select EPUB 2."),
            "overwrite": _OVERWRITE,
        }, "source", "output"),
    ),
    "check": _action(
        "Run local EPUBCheck on an existing EPUB and return the validation report.",
        _object({"source": _SOURCE}, "source"),
    ),
    "rule_groups": _action(
        "List the desktop's saved proofreading groups, or read one group's ordered steps by name.",
        _object({"name": _string("Exact saved group name.", minLength=1)}),
    ),
    "proofread": _action(
        "Preview ordered proofreading rules on EPUB or .bookproj. Apply the reviewed preview to a separate .bookproj.",
        _object({
            "source": _SOURCE,
            "group": _string("Saved desktop group name. Supply exactly one of group or steps.", minLength=1),
            "steps": _array(_STEP, "Explicit steps in execution order. Supply exactly one of steps or group."),
            "mode": _string("Preview reads the source. Apply also requires output and preview_id.",
                            enum=["preview", "apply"], default="preview"),
            "preview_id": _string("For apply, the preview_id returned for this input and these steps.", minLength=1),
            "output": _PROJECT, "overwrite": _OVERWRITE,
            "example_limit": {"type": "integer", "minimum": 0, "maximum": 20, "default": 3,
                              "description": "Maximum before/after examples for each step."},
        }, "source"),
    ),
    "compare": _action(
        "Compare two local EPUB files read-only. Return category counts and paginated before/after excerpts.",
        _object({
            "original": _string("Original EPUB path (left).", minLength=1),
            "revision": _string("Revised EPUB path (right).", minLength=1),
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "max_chars": {"type": "integer", "minimum": 1, "maximum": 4000, "default": 600,
                          "description": "Maximum characters in each side of each change."},
        }, "original", "revision"),
    ),
}


def _validate(value: Any, schema: dict, where: str) -> None:
    kind = schema["type"]
    expected = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    if type(value) is not expected[kind]:
        raise ValueError(f"{where} 必须是 {kind}。")
    if kind == "object":
        properties = schema["properties"]
        missing = [key for key in schema.get("required", ()) if key not in value]
        if missing:
            raise ValueError(f"{where} 缺少字段：{', '.join(missing)}。")
        unknown = value.keys() - properties.keys()
        if unknown:
            raise ValueError(f"{where} 包含未知字段：{', '.join(sorted(unknown))}。")
        for key, item in value.items():
            _validate(item, properties[key], f"{where}.{key}")
    elif kind == "array":
        if len(value) < schema["minItems"]:
            raise ValueError(f"{where} 必须至少有一项。")
        for index, item in enumerate(value, 1):
            _validate(item, schema["items"], f"{where}[{index}]")
    elif kind == "string":
        if len(value.strip()) < schema.get("minLength", 0):
            raise ValueError(f"{where} 不能为空。")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"{where} 必须是以下选项之一：{', '.join(schema['enum'])}。")
    elif kind == "integer":
        minimum = schema["minimum"]
        maximum = schema.get("maximum")
        if value < minimum or (maximum is not None and value > maximum):
            raise ValueError(f"{where} 超出允许范围。")


def _source(path: str, *, book_only: bool = False) -> Path:
    result = Path(path).expanduser().resolve()
    if not result.is_file():
        raise FileNotFoundError(f"找不到源文件：{result}")
    if book_only and result.suffix.lower() not in {".bookproj", ".epub"}:
        raise ValueError("请选择 EPUB 或 .bookproj 工程。")
    return result


def _output(path: str, *, source: Path | None = None, overwrite: bool) -> Path:
    chosen = Path(path).expanduser()
    destination = chosen.parent.resolve() / chosen.name
    if source is not None and destination.resolve() == source:
        raise ValueError("输出不能与源文件相同，请指定新路径。")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"找不到输出目录：{destination.parent}")
    if destination.is_symlink():
        raise ValueError("输出位置不能是符号链接。")
    if destination.exists() and (not overwrite or not destination.is_file()):
        raise FileExistsError(f"输出位置已被占用：{destination}")
    return destination


def _project_output(args: dict, source: Path | None = None) -> Path:
    destination = _output(args["output"], source=source, overwrite=args.get("overwrite", False))
    if destination.suffix.lower() != ".bookproj":
        raise ValueError("请选择 .bookproj 工程输出路径。")
    return destination


def _format_output(args: dict, source: Path):
    overwrite = args.get("overwrite", False)
    destination = _output(args["output"], source=source, overwrite=overwrite)
    extension = conversion.normalize_format(destination)
    format_name = args.get("format", extension)
    if ("epub" if format_name == "epub2" else format_name) != extension:
        raise ValueError("输出文件扩展名与目标格式不符。")
    plan = plan_destinations(
        destination.parent,
        [(conversion_stem(destination), extension)],
        replace=overwrite,
        protected=[source],
    )[0]
    if plan.destination.name.casefold() != destination.name.casefold():
        raise ValueError(f"输出文件名不可用：{destination.name}")
    return replace(plan, format_name=format_name, requested=destination, destination=destination)


def _metadata(book: Book, metadata: dict) -> None:
    for name, value in metadata.items():
        setattr(book, name, value.strip())


def _make_chapter(values: dict) -> Chapter:
    return Chapter(
        title=values["title"].strip(),
        html=values["html"],
        level=values.get("level", 1),
        source=values.get("source", "").strip(),
    )


def _summary(book: Book) -> dict:
    return {
        "title": book.title,
        "author": book.author,
        "language": book.language,
        "cover": book.cover,
        "chapter_count": len(book.chapters),
    }


def _write_project(book: Book, destination: Path, *, overwrite: bool) -> dict:
    with publish_file(destination, overwrite=overwrite) as temporary:
        book.save(temporary)
    return {"output": str(destination), "book": _summary(book)}


def _progress(done: int, total: int, message: str) -> None:
    ratio = f"[{done}/{total}] " if total else ""
    print(ratio + message, file=sys.stderr, flush=True)


def _status(_args: dict) -> dict:
    from agent_mcp import SERVER_VERSION

    return {
        "version": SERVER_VERSION,
        "tools": {name: asdict(info) for name, info in {**converter_readiness(), **ocr_readiness()}.items()},
        "actions": list(ACTIONS),
        "output_formats": sorted(set(conversion.OUTPUT_FORMATS) | {"epub2"}),
    }


def _inspect(args: dict) -> dict:
    from studio_operations import read_book

    source = _source(args["source"], book_only=True)
    if "chapter" not in args and (args.get("include_html") or "max_chars" in args):
        raise ValueError("指定 include_html 或 max_chars 时请同时指定章节编号。")
    book = read_book(source)
    rows = [
        {"chapter": index, "title": chapter.title, "level": chapter.level, "source": chapter.source}
        for index, chapter in enumerate(book.chapters, 1)
    ]
    answer = {"source": str(source), "book": _summary(book), "chapters": rows}
    if number := args.get("chapter"):
        if number > len(book.chapters):
            raise ValueError(f"找不到第 {number} 章。")
        fragment = chapter_html(book.chapters[number - 1].html)
        tree = html.fragment_fromstring(fragment, create_parent="div")
        for node in tree.xpath(".//script | .//style"):
            node.drop_tree()
        plain = tree.text_content().strip()
        maximum = args.get("max_chars", 12000)
        details = {**rows[number - 1], "text": plain[:maximum], "text_truncated": len(plain) > maximum}
        if args.get("include_html"):
            details.update(html=fragment[:maximum], html_truncated=len(fragment) > maximum)
        answer["selected_chapter"] = details
    return answer


def _create(args: dict) -> dict:
    destination = _project_output(args)
    book = Book(chapters=[_make_chapter(chapter) for chapter in args["chapters"]])
    _metadata(book, {key: args[key] for key in _METADATA if key in args})
    return _write_project(book, destination, overwrite=args.get("overwrite", False))


def _update(args: dict) -> dict:
    from studio_operations import read_book

    if not any(args.get(key) for key in ("metadata", "replace_chapters", "append_chapters")):
        raise ValueError("请提供要更新的书籍资料或章节。")
    source = _source(args["source"], book_only=True)
    destination = _project_output(args, source)
    book = read_book(source)
    _metadata(book, args.get("metadata", {}))
    for values in args.get("replace_chapters", []):
        number = values["chapter"]
        if number > len(book.chapters):
            raise ValueError(f"找不到第 {number} 章。")
        if len(values) == 1:
            raise ValueError(f"请提供第 {number} 章要更新的内容。")
        chapter = book.chapters[number - 1]
        for key, value in values.items():
            if key != "chapter":
                setattr(chapter, key, value.strip() if key in {"title", "source"} else value)
    book.chapters.extend(_make_chapter(chapter) for chapter in args.get("append_chapters", []))
    return _write_project(book, destination, overwrite=args.get("overwrite", False))


def _discover(args: dict) -> dict:
    from capture import discover

    url = args["url"].strip()
    candidates = discover(url, rendered=args.get("rendered", False))
    return {"url": url, "candidates": [asdict(item) for item in candidates]}


def _capture(args: dict) -> dict:
    from studio_operations import capture_pages

    destination = _project_output(args)
    books, warnings = capture_pages(args["urls"], args.get("rendered", False), progress=_progress)
    if not books:
        raise ValueError("没有取得可导入的章节。\n" + "\n".join(warnings))
    book = books[0]
    _metadata(book, args.get("metadata", {}))
    result = _write_project(book, destination, overwrite=args.get("overwrite", False))
    return {**result, "warnings": warnings}


def _ocr(args: dict) -> dict:
    source = _source(args["source"])
    destination = _project_output(args, source)
    engine = args.get("engine", "xiaomi")
    readiness = ocr_readiness()[engine]
    if not readiness.ready:
        raise ValueError(readiness.message)
    pages = args.get("pages", "")
    if engine == "paddle":
        book = paddle_book(source, pages=pages, progress=_progress)
    else:
        model = os.environ.get("EBOOK_STUDIO_OCR_MODEL", DEFAULT_MODEL)
        with local_server() as (address, _):
            book = ocr_book(source, pages=pages, progress=_progress, base_url=address, model=model)
    result = _write_project(book, destination, overwrite=args.get("overwrite", False))
    return {**result, "engine": engine}


def _export(args: dict) -> dict:
    from studio_operations import export_book, read_book

    source = _source(args["source"], book_only=True)
    plan = _format_output(args, source)
    book = read_book(source)
    outputs, errors = export_book(
        book, plan.destination.parent, plan.destination.stem, [plan.format_name],
        args.get("mobile", True), plans=[plan], progress=_progress,
    )
    if errors:
        raise ValueError("\n".join(errors))
    return {"output": str(outputs[0]), "format": plan.format_name, "book": _summary(book)}


def _convert(args: dict) -> dict:
    from studio_operations import convert_to

    source = _source(args["source"])
    plan = _format_output(args, source)
    _progress(0, 1, f"正在转换 {source.name}")
    convert_to(source, plan.destination, plan.format_name, overwrite=plan.replace_existing)
    return {"output": str(plan.destination), "format": plan.format_name}


def _check(args: dict) -> dict:
    from studio_operations import run_epubcheck

    source = _source(args["source"])
    if source.suffix.lower() != ".epub":
        raise ValueError("请选择 EPUB 文件进行检查。")
    return {"source": str(source), "valid": True, "report": run_epubcheck(source)}


def _rule_groups(args: dict) -> dict:
    from agent_revision import rule_groups
    return rule_groups(args)


def _proofread(args: dict) -> dict:
    from agent_revision import proofread

    source = _source(args["source"], book_only=True)
    destination = None
    if args.get("mode", "preview") == "apply":
        if "preview_id" not in args or "output" not in args:
            raise ValueError("应用校对需要先预览，再提供 preview_id 和新的 output 工程路径。")
        destination = _project_output(args, source)
    elif args.keys() & {"output", "preview_id", "overwrite"}:
        raise ValueError("output、overwrite 和 preview_id 仅用于 mode=apply。")
    report, revised = proofread(source, args)
    if destination is not None:
        report.update(_write_project(revised, destination, overwrite=args.get("overwrite", False)))
    return report


def _compare(args: dict) -> dict:
    from agent_revision import compare
    return compare(_source(args["original"]), _source(args["revision"]), args)


_HANDLERS = {
    "status": _status, "inspect": _inspect, "create": _create, "update": _update,
    "discover": _discover, "capture": _capture, "ocr": _ocr, "export": _export, "convert": _convert, "check": _check,
    "rule_groups": _rule_groups, "proofread": _proofread, "compare": _compare,
}


def execute(job: dict) -> dict:
    """Execute a job object with an action and that action's arguments at the top level."""
    if not isinstance(job, dict):
        raise ValueError("任务必须是 JSON 对象。")
    name = job.get("action")
    if not isinstance(name, str) or name not in ACTIONS:
        raise ValueError(f"未知操作：{name}。请使用 schema 查看操作。")
    args = {key: value for key, value in job.items() if key != "action"}
    _validate(args, ACTIONS[name]["inputSchema"], name)
    return {"ok": True, "action": name, **_HANDLERS[name](args)}


def _json_print(value: dict) -> None:
    text = json.dumps(value, ensure_ascii=False) + "\n"
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
    else:
        sys.stdout.write(text)
        sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["mcp"]:
        from agent_mcp import serve
        return serve(execute, ACTIONS) or 0
    if args == ["schema"]:
        _json_print({"ok": True, "actions": ACTIONS})
        return 0
    if len(args) != 2 or args[0] != "run":
        _json_print({"ok": False, "error": "用法：schema、run <任务.json 或 ->、mcp"})
        return 2
    try:
        if args[1] == "-":
            buffer = getattr(sys.stdin, "buffer", sys.stdin)
            job = json.loads(buffer.read())
        else:
            job = json.loads(Path(args[1]).read_bytes())
        result = execute(job)
    except Exception as exc:
        _json_print({"ok": False, "error": str(exc) or type(exc).__name__})
        return 1
    _json_print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
