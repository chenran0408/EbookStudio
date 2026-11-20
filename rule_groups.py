"""Preview sequential corrections against the text produced by the preceding step."""
from copy import deepcopy
from dataclasses import asdict, dataclass, replace

from book_replace import ReplacementPlan, preview_replacement
from ebook_core import Book
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


def apply_rule_group(workspace, book, plan):
    if book.identifier != plan.identifier or _snapshot(book) != plan.original:
        raise ValueError("书稿已有变化，请重新预览整组。")
    if not plan.changes:
        return book
    revised = deepcopy(book)
    for index, html in plan.changes.items():
        revised.chapters[index].html = html
    workspace.save_checkpoint(book, "运行规则组前")
    return revised
