"""Project store: one folder per project (no database, ARCH-003).

<root>/<project_id>/project.json      title, created, notices, runs
<root>/<project_id>/capture/originals uploaded files exactly as received (never modified)
<root>/<project_id>/runs/<run_id>/    pipeline runs (see studio.pipeline)
<root>/<project_id>/pois.json         human-authored POIs (model frame of a run)
<root>/<project_id>/calibration.json  optional scale calibration
"""
import os
import re
import secrets
import shutil
import time
import unicodedata
from pathlib import Path

from namma import PROJECT_ROOT
from namma.core import timefmt
from namma.core.jsonio import load_strict, write_json

DEFAULT_ROOT = PROJECT_ROOT / "projects"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,80}$")
RUN_RE = re.compile(r"^run-[0-9]{8}-[0-9]{6}(-[a-z0-9]+)?$")
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class ProjectError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def slug(text):
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:40] or "space"


def safe_filename(name):
    """Keep only the base name, replace unsafe characters, refuse hidden / empty names."""
    base = re.split(r"[\\/]", name or "")[-1]
    base = unicodedata.normalize("NFKC", base)
    base = SAFE_NAME.sub("_", base).strip("._")
    if not base or len(base) > 120:
        raise ProjectError("UPLOAD-BAD-NAME", f"unusable file name {name!r}")
    return base


def _tree_bytes(path):
    """Total size of the files under path. os.scandir reuses the directory listing's metadata on Windows: 34 ms for
    a 1.5 GB bin of 3,132 files, against 370 ms with Path.rglob + stat."""
    total, stack = 0, [str(path)]
    while stack:
        with os.scandir(stack.pop()) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                elif e.is_file(follow_symlinks=False):
                    total += e.stat(follow_symlinks=False).st_size
    return total


class ProjectStore:
    def __init__(self, root=None):
        self.root = Path(root or DEFAULT_ROOT)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, project_id):
        if not ID_RE.match(project_id or ""):
            raise ProjectError("PROJECT-BAD-ID", "invalid project id", 404)
        p = self.root / project_id
        if not (p / "project.json").exists():
            raise ProjectError("PROJECT-NOT-FOUND", f"no project {project_id}", 404)
        return p

    def create(self, title, notices=None):
        title = (title or "").strip()[:80] or "Untitled space"
        pid = f"{slug(title)}-{time.strftime('%Y%m%d')}-{secrets.token_hex(3)}"
        d = self.root / pid
        (d / "capture" / "originals").mkdir(parents=True)
        doc = {"project_id": pid, "title": title, "created_at": timefmt.now_iso(), "notices": list(notices or []),
               "runs": []}
        write_json(d / "project.json", doc)
        write_json(d / "pois.json", [])
        return doc

    def get(self, project_id):
        return load_strict(self.path(project_id) / "project.json")

    def save(self, doc):
        write_json(self.path(doc["project_id"]) / "project.json", doc)

    def list(self):
        out = []
        for d in sorted(self.root.iterdir()):
            if d.is_dir() and (d / "project.json").exists() and not d.name.startswith("_"):
                try:
                    out.append(load_strict(d / "project.json"))
                except ValueError:
                    continue
        return sorted(out, key=lambda p: p["created_at"], reverse=True)

    def originals(self, project_id):
        return self.path(project_id) / "capture" / "originals"

    def clear_capture(self, project_id):
        cap = self.path(project_id) / "capture"
        for sub in ("frames", "thumbs", "frame_candidates"):
            shutil.rmtree(cap / sub, ignore_errors=True)
        for f in ("capture_manifest.json", "capture_report.json", "capture.if01.json"):
            (cap / f).unlink(missing_ok=True)

    def remove_upload(self, project_id, name):
        p = self.originals(project_id) / safe_filename(name)
        if p.exists():
            p.unlink()
        self.clear_capture(project_id)

    def new_run_id(self, project_id):
        return f"run-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"

    def run_dir(self, project_id, run_id):
        if not RUN_RE.match(run_id or ""):
            raise ProjectError("RUN-BAD-ID", "invalid run id", 404)
        d = self.path(project_id) / "runs" / run_id
        if not d.exists():
            raise ProjectError("RUN-NOT-FOUND", f"no run {run_id}", 404)
        return d

    def trash(self, project_id):
        """Move (never hard-delete) a project to <root>/_trash/."""
        src = self.path(project_id)
        dst = self.root / "_trash" / f"{project_id}-{time.strftime('%Y%m%d%H%M%S')}"
        dst.parent.mkdir(exist_ok=True)
        shutil.move(str(src), str(dst))
        return str(dst)

    # ------------------------------------------------------------------ bin: list, restore, delete permanently
    def _binned(self, name):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,120}-\d{14}", name or ""):
            raise ProjectError("BIN-BAD-NAME", "invalid bin entry", 404)
        p = self.root / "_trash" / name
        if not (p / "project.json").exists():
            raise ProjectError("BIN-NOT-FOUND", f"no bin entry {name}", 404)
        return p

    def list_trash(self):
        out = []
        for d in sorted((self.root / "_trash").glob("*")) if (self.root / "_trash").exists() else []:
            if (d / "project.json").exists():
                try:
                    doc = load_strict(d / "project.json")
                except ValueError:
                    continue
                size = _tree_bytes(d)
                out.append({"name": d.name, "project_id": doc.get("project_id"), "title": doc.get("title", d.name),
                            "deleted_at": d.name[-14:], "bytes": size})
        return sorted(out, key=lambda x: x["deleted_at"], reverse=True)

    def restore(self, name):
        src = self._binned(name)
        pid = load_strict(src / "project.json")["project_id"]
        dst = self.root / pid
        if dst.exists():
            raise ProjectError("BIN-RESTORE-EXISTS", "a space with the same id already exists", 409)
        shutil.move(str(src), str(dst))
        return pid

    def purge(self, name):
        """Permanently delete one bin entry (the only hard delete; the UI asks twice)."""
        p = self._binned(name)
        shutil.rmtree(p)
        return name
