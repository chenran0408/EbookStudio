"""Named, book-independent replacement settings in the local studio workspace."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from ebook_core import publish_file
from workspace import _lock_file


@dataclass(frozen=True)
class Rule:
    query: str
    replacement: str
    regular: bool
    case_sensitive: bool


class SavedRules:
    def __init__(self, directory: Path):
        self.path = directory / "replacement-rules.json"

    def read(self) -> dict[str, Rule]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        try:
            return {name: Rule(**fields) for name, fields in data.items()}
        except (AttributeError, TypeError) as exc:
            raise ValueError("本机校对规则的记录格式有误。") from exc

    def change(self, name: str, previous: Rule | None, replacement: Rule | None):
        """Change exactly the rule the user reviewed, preserving other sessions' edits."""
        if not name.strip():
            raise ValueError("请输入规则名称。")
        with _lock_file(self.path.with_suffix(".lock"), wait=True):
            rules = self.read()
            if rules.get(name) != previous:
                raise ValueError("此规则已在其他窗口更改，请重新选择规则。")
            if replacement is None:
                del rules[name]
            else:
                rules[name] = replacement
            with publish_file(self.path) as temporary:
                temporary.write_text(json.dumps({key: asdict(rule) for key, rule in rules.items()},
                                                ensure_ascii=False, indent=2), encoding="utf-8")
