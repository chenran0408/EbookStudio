"""Structured EPUBCheck results shared by the export and check workflows."""

from dataclasses import dataclass
from urllib.parse import unquote


SEVERITIES = {"FATAL": "严重错误", "ERROR": "错误", "WARNING": "警告", "USAGE": "提示"}
SEVERITY_ORDER = {name: index for index, name in enumerate(SEVERITIES)}


@dataclass(frozen=True)
class EpubLocation:
    path: str
    line: int
    column: int
    context: str = ""

    @property
    def display_path(self) -> str:
        suffix = f":{self.line}" if self.line > 0 else ""
        if suffix and self.column > 0:
            suffix += f":{self.column}"
        return unquote(self.path) + suffix


@dataclass(frozen=True)
class EpubIssue:
    severity: str
    code: str
    message: str
    locations: tuple[EpubLocation, ...]
    suggestion: str = ""

    @property
    def severity_label(self) -> str:
        return SEVERITIES.get(self.severity, self.severity)

    def details(self) -> str:
        sections = [f"{self.severity_label} · {self.code}\n{self.message}"]
        for location in self.locations:
            if location.display_path or location.context:
                sections.append("\n".join(filter(None, (location.display_path, location.context))))
        if self.suggestion:
            sections.append(self.suggestion)
        return "\n\n".join(sections)


@dataclass(frozen=True)
class EpubCheckReport:
    version: str
    fatal_count: int
    error_count: int
    warning_count: int
    issues: tuple[EpubIssue, ...]

    @property
    def valid(self) -> bool:
        return self.fatal_count + self.error_count == 0

    @property
    def summary(self) -> str:
        return f"{self.fatal_count + self.error_count} 项错误 · {self.warning_count} 项警告"

    @classmethod
    def from_json(cls, data: dict) -> "EpubCheckReport":
        checker = data["checker"]
        issues = []
        for item in data["messages"]:
            locations = tuple(EpubLocation(
                location.get("path") or "", location.get("line") or 0, location.get("column") or 0,
                location.get("context") or "",
            ) for location in item.get("locations") or ())
            issues.append(EpubIssue(item["severity"], item["ID"], item["message"], locations,
                                    item.get("suggestion") or ""))
        issues.sort(key=lambda issue: SEVERITY_ORDER.get(issue.severity, len(SEVERITY_ORDER)))
        return cls(checker["checkerVersion"], checker["nFatal"], checker["nError"], checker["nWarning"], tuple(issues))
