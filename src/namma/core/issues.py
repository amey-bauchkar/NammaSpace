"""Machine-readable validation issues shared by all checkers."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    path: str = "$"

    def as_dict(self):
        return asdict(self)


def error(code, message, path="$"):
    return Issue("error", code, message, path)


def warning(code, message, path="$"):
    return Issue("warning", code, message, path)


def has_errors(issues):
    return any(i.severity == "error" for i in issues)


def errors_only(issues):
    return [i for i in issues if i.severity == "error"]


def as_dicts(issues):
    return [i.as_dict() for i in issues]
