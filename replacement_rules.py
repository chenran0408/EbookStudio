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
    filename = "replacement-rules.json"
    label = "规则"

    def __init__(self, directory: Path):
        self.path = directory / self.filename

    @staticmethod
    def decode(fields):
        return Rule(**fields)

    encode = staticmethod(asdict)

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        try:
            return {name: self.decode(fields) for name, fields in data.items()}
        except (AttributeError, TypeError, KeyError) as exc:
            raise ValueError(f"本机校对{self.label}的记录格式有误。") from exc

    def change(self, name: str, previous, replacement):
        """Change exactly the rule the user reviewed, preserving other sessions' edits."""
        if not name.strip():
            raise ValueError(f"请输入{self.label}名称。")
        with _lock_file(self.path.with_suffix(".lock"), wait=True):
            rules = self.read()
            if rules.get(name) != previous:
                raise ValueError(f"此{self.label}已在其他窗口更改，请重新选择{self.label}。")
            if replacement is None:
                del rules[name]
            else:
                rules[name] = replacement
            with publish_file(self.path) as temporary:
                temporary.write_text(json.dumps({key: self.encode(rule) for key, rule in rules.items()},
                                                ensure_ascii=False, indent=2), encoding="utf-8")
