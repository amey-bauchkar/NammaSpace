"""Schema validation against the generated contract schemas.

Source of truth: architecture/contracts.py -> architecture/schemas/IF-nn.schema.json.
This module never defines schemas itself (no duplicate source of truth).
"""
from functools import lru_cache

from jsonschema import Draft202012Validator, FormatChecker

from namma import PROJECT_ROOT
from namma.core.issues import error
from namma.core.jsonio import load_strict
from namma.core.timefmt import is_rfc3339

SCHEMA_DIR = PROJECT_ROOT / "architecture" / "schemas"
CONTRACT_IDS = [f"IF-{i:02d}" for i in range(1, 14)]   # IF-13 added in Phase 13 (R13.0)

_FORMAT_CHECKER = FormatChecker(formats=())


@_FORMAT_CHECKER.checks("date-time")
def _date_time(value):
    return not isinstance(value, str) or is_rfc3339(value)


@lru_cache(maxsize=None)
def _validator(if_id):
    path = SCHEMA_DIR / f"{if_id}.schema.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing - run: python -I architecture/contracts.py")
    return Draft202012Validator(load_strict(path), format_checker=_FORMAT_CHECKER)


def schema_issues(doc, expected=None):
    """Schema errors as Issue objects. `expected` pins the contract id."""
    if not isinstance(doc, dict):
        return [error("SCHEMA-NOT-OBJECT", "artifact must be a JSON object")]
    if_id = doc.get("contract")
    if expected is not None and if_id != expected:
        return [error("SCHEMA-WRONG-CONTRACT", f"expected contract {expected}, got {if_id!r}", "$.contract")]
    if if_id not in CONTRACT_IDS:
        return [error("SCHEMA-UNKNOWN-CONTRACT", f"unknown contract {if_id!r}", "$.contract")]
    out = []
    for e in sorted(_validator(if_id).iter_errors(doc), key=lambda e: list(e.path)):
        path = "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e.path)
        out.append(error("SCHEMA", e.message[:300], path))
    return out
