"""Read-only editorial previews and standalone EPUB comparison results for agents."""
from collections import Counter
from hashlib import file_digest
import json

from ebook_core import Book
from epub_compare import compare_epubs
from epub_io import open_epub
from replacement_rules import Rule
from rule_groups import SavedGroups, Step, preview_rule_group, revised_book
from workspace import _default_directory


def _saved_steps(name, groups):
    if name not in groups:
        raise ValueError(f"找不到校对规则组「{name}」。可先用 ebook_rule_groups 列出名称。")
    return groups[name]


def _step_fields(step):
    return {"name": step.name, "query": step.rule.query, "replacement": step.rule.replacement,
            "case_sensitive": step.rule.case_sensitive, "regular": step.rule.regular}


def rule_groups(args):
    groups = SavedGroups(_default_directory()).read()
    if "name" in args:
        name = args["name"]
        return {"name": name, "steps": [_step_fields(s) for s in _saved_steps(name, groups)]}
    return {"groups": [{"name": name, "step_count": len(groups[name])} for name in sorted(groups, key=str.casefold)]}


def proofread(source, args):
    if ("group" in args) == ("steps" in args):
        raise ValueError("请在 group 和 steps 中选择一项。")
    if "group" in args:
        steps = _saved_steps(args["group"], SavedGroups(_default_directory()).read())
    else:
        steps = tuple(Step(row.get("name", row["query"]),
                           Rule(row["query"], row["replacement"], row.get("regular", False),
                                row.get("case_sensitive", False))) for row in args["steps"])
    # Hash and parse the same open input, even if another program replaces the file at its path.
    with source.open("rb") as stream:
        fingerprint = file_digest(stream, "sha256")
        stream.seek(0)
        book = Book.load(stream) if source.suffix.lower() == ".bookproj" else open_epub(stream)
    fingerprint.update(json.dumps([str(source), [_step_fields(s) for s in steps]],
                                  ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    preview_id = fingerprint.hexdigest()
    apply = args.get("mode", "preview") == "apply"
    if apply and preview_id != args["preview_id"]:
        raise ValueError("当前源文件或规则与预览不一致，请重新预览并使用新的 preview_id。")
    plan = preview_rule_group(book, steps)
    count = args.get("example_limit", 3)
    stages = []
    for step, stage in zip(steps, plan.stages):
        examples = [{"chapter": hit.chapter_index + 1, "title": hit.chapter_title,
                     "path": book.chapters[hit.chapter_index].path, "before": hit.before, "after": hit.after}
                    for hit in stage.page(0, count)]
        stages.append({"name": step.name, "matches": stage.matches, "replacements": stage.replacements,
                       "examples": examples})
    summary = {"mode": "apply" if apply else "preview", "source": str(source), "preview_id": preview_id,
               "stages": stages, "changed_chapters": len(plan.changes),
               "replacements": sum(stage.replacements for stage in plan.stages)}
    return summary, revised_book(book, plan) if apply else None


def _change_details(change, max_chars):
    before, after = change.before, change.after
    if before == after and change.source:
        before, after = change.source
    # In long chapters show the first differing passage rather than the first paragraph.
    start = 0
    if max(len(before), len(after)) > max_chars:
        prefix = next((i for i, (a, b) in enumerate(zip(before, after)) if a != b), min(len(before), len(after)))
        start = max(0, prefix - min(80, max_chars // 4))
    return {"kind": change.kind, "action": change.action, "name": change.name, "path": change.path,
            "before": before if len(before) <= max_chars else before[start:start + max_chars],
            "after": after if len(after) <= max_chars else after[start:start + max_chars],
            "before_truncated": len(before) > max_chars, "after_truncated": len(after) > max_chars}


def compare(original, revision, args):
    if any(path.suffix.lower() != ".epub" for path in (original, revision)):
        raise ValueError("请选择两份 EPUB 文件。")
    changes = compare_epubs(original, revision)
    offset, limit = args.get("offset", 0), args.get("limit", 20)
    return {"original": str(original), "revision": str(revision), "total": len(changes),
            "counts": dict(Counter(c.kind for c in changes)), "offset": offset,
            "changes": [_change_details(c, args.get("max_chars", 600)) for c in changes[offset:offset + limit]],
            "next_offset": offset + limit if offset + limit < len(changes) else None}
