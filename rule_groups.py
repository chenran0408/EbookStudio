"""Preview sequential corrections against the text produced by the preceding step."""
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path

from book_replace import ReplacementPlan, preview_replacement
from ebook_core import Book, publish_file
from replacement_rules import Rule, SavedRules


@dataclass(frozen=True)
class Step:
    name: str
    rule: Rule


class SavedGroups(SavedRules):
    filename = "replacement-groups.json"
    label = "规则组"

    @staticmethod
    def decode(fields):
        return tuple(Step(item["name"], Rule(**item["rule"])) for item in fields)

    @staticmethod
    def encode(steps):
        return [asdict(step) for step in steps]


def preview_rule(book, rule):
    return preview_replacement(book, rule.query, rule.replacement,
                               regular=rule.regular, case_sensitive=rule.case_sensitive)


def export_group(path: Path, name: str, steps):
    data = {"format": "EbookStudio.rule-group", "version": 1, "name": name,
            "steps": [{"name": step.name, **asdict(step.rule)} for step in steps]}
    with publish_file(path) as temporary:
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_group_file(path: Path) -> tuple[str, tuple[Step, ...]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("请使用 UTF-8 编码的规则组 JSON 文件。") from exc
    if not isinstance(data, dict) or data.get("format") != "EbookStudio.rule-group":
        raise ValueError("请选择从电子书工作台导出的规则组文件。")
    if type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("此规则组文件使用了当前工作台不支持的版本。")
    name, rows = data.get("name"), data.get("steps")
    if not isinstance(name, str) or not name.strip() or not isinstance(rows, list) or not rows:
        raise ValueError("规则组需要名称和至少一个步骤。")
    steps = []
    for number, row in enumerate(rows, 1):
        keys = {"name", "query", "replacement", "regular", "case_sensitive"}
        if (not isinstance(row, dict) or row.keys() != keys
                or not all(isinstance(row[k], str) for k in ("name", "query", "replacement"))
                or not row["name"] or not all(isinstance(row[k], bool) for k in ("regular", "case_sensitive"))):
            raise ValueError(f"第 {number} 步的名称、查找文字、替换文字或匹配选项格式有误。")
        rule = Rule(row["query"], row["replacement"], row["regular"], row["case_sensitive"])
        try:
            preview_rule(Book(), rule)
        except ValueError as exc:
            raise ValueError(f"第 {number} 步「{row['name']}」：{exc}") from exc
        steps.append(Step(row["name"], rule))
    return name.strip(), tuple(steps)


def _snapshot(book):
    return tuple((chapter.path, chapter.html) for chapter in book.chapters)


@dataclass(frozen=True)
class GroupPlan:
    identifier: str
    original: tuple[tuple[str, str], ...]
    stages: tuple[ReplacementPlan, ...]
    changes: dict[int, str]


def preview_rule_group(book: Book, steps: tuple[Step, ...], *, cancelled=lambda: False) -> GroupPlan | None:
    if not steps:
        raise ValueError("请先添加校对步骤。")
    # The per-rule planner writes chapter.html only. Share the untouched assets and EPUB originals.
    draft = replace(book, chapters=[replace(chapter) for chapter in book.chapters])
    original = _snapshot(draft)
    stages = []
    for number, step in enumerate(steps, 1):
        if cancelled():
            return None
        try:
            plan = preview_rule(draft, step.rule)
            plan.apply(draft)
        except ValueError as exc:
            raise ValueError(f"第 {number} 步「{step.name}」：{exc}") from exc
        stages.append(plan)
    changes = {i: chapter.html for i, chapter in enumerate(draft.chapters) if chapter.html != original[i][1]}
    return GroupPlan(book.identifier, original, tuple(stages), changes)


def revised_book(book, plan):
    if book.identifier != plan.identifier or _snapshot(book) != plan.original:
        raise ValueError("书稿已有变化，请重新预览整组。")
    if not plan.changes:
        return book
    revised = deepcopy(book)
    for index, html in plan.changes.items():
        revised.chapters[index].html = html
    return revised


def apply_rule_group(workspace, book, plan):
    revised = revised_book(book, plan)
    if revised is not book:
        workspace.save_checkpoint(book, "运行规则组前")
    return revised
