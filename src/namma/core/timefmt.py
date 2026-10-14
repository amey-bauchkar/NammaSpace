"""RFC 3339 timestamps.

jsonschema treats `format: date-time` as an annotation unless a format checker
is supplied, and its built-in date-time checker needs the optional
`rfc3339-validator` package (not installed here). This module is the
project's own checker (ARCH-026); architecture/contracts.py and the runtime
validators both use it.
"""
import re
from datetime import datetime, timezone

_RFC3339 = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")


def is_rfc3339(value):
    """True if `value` is an RFC 3339 date-time WITH an explicit offset."""
    if not isinstance(value, str):
        return False
    m = _RFC3339.match(value)
    if not m:
        return False
    if int(m.group(4)) > 23 or int(m.group(5)) > 59:  # fromisoformat accepts 24:00 (ISO 8601); RFC 3339 does not
        return False
    if int(m.group(6)) > 59:  # leap seconds are not accepted by this project
        return False
    offset = m.group(8)
    if offset not in ("Z", "z"):
        if int(offset[1:3]) > 23 or int(offset[4:6]) > 59:
            return False
    try:
        datetime.fromisoformat(value.replace("z", "Z").replace("t", "T"))
    except ValueError:
        return False
    return True


def parse(value):
    if not is_rfc3339(value):
        raise ValueError(f"not an RFC 3339 timestamp with offset: {value!r}")
    return datetime.fromisoformat(value.replace("z", "Z").replace("t", "T"))


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
