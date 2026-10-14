"""Strict JSON I/O, hashing and atomic writes shared by every module.

Rules (ARCH-026):
- NaN, Infinity and numbers that overflow to infinity are rejected at parse time;
- duplicate object keys are rejected;
- files are written atomically (temp file + rename) as UTF-8 with "\n" newlines.
"""
import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path


class StrictJSONError(ValueError):
    """Raised when a document is not acceptable JSON for a contract artifact."""


def _reject_constant(name):
    raise StrictJSONError(f"non-finite JSON number {name!r} is not allowed")


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise StrictJSONError(f"number {text!r} overflows to a non-finite value")
    return value


def _no_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise StrictJSONError(f"duplicate key {key!r}")
        out[key] = value
    return out


def loads_strict(text):
    try:
        return json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float,
                          object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise StrictJSONError(f"invalid JSON: {exc}") from exc


def load_strict(path):
    return loads_strict(read_text_retry(path))


def read_text_retry(path, wait_s=2.0):
    """On Windows opening a file at the instant another process replaces it fails with PermissionError
    (pre-capture QA R150: an intermittent HTTP 500 on run-status polls); retry briefly."""
    deadline = time.monotonic() + wait_s
    delay = 0.005
    while True:
        try:
            return Path(path).read_text(encoding="utf-8")
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.1)


def dumps(obj):
    return json.dumps(obj, indent=1, ensure_ascii=False, allow_nan=False) + "\n"


def _replace(tmp, path, wait_s=5.0):
    """os.replace with retries: on Windows the replace fails (PermissionError, WinError 5 / 32) while another
    process has the destination open - e.g. the Studio server polling a status file the worker is updating."""
    deadline = time.monotonic() + wait_s
    delay = 0.01
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.2)


def write_json(path, obj):
    write_text_atomic(path, dumps(obj))


def write_text_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        _replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def write_bytes_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        _replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def iter_numbers(obj, path="$"):
    """Yield (json_path, value) for every number in a parsed document."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from iter_numbers(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from iter_numbers(v, f"{path}[{i}]")


def non_finite_paths(obj):
    return [p for p, v in iter_numbers(obj) if isinstance(v, float) and not math.isfinite(v)]
