"""Command-line access to the Studio pipeline (same code path as the web app).

    python scripts/studio_cli.py new     --title "Living room" --from <folder of photos | video file> [--root projects]
    python scripts/studio_cli.py check   <project_dir> [--max-frames 200]
    python scripts/studio_cli.py run     <project_dir> --profile preview|standard [--run <run_id>] [--stop-after <stage>]
    python scripts/studio_cli.py publish <package_dir> <out_dir> [--lods 1] [--max-file-mb 100]

`run` without --run starts a new run; with --run it resumes that run (completed stages are skipped).
Input files are COPIED into the project (originals elsewhere are never modified).
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

from _bootstrap import ROOT  # noqa: F401  (adds src/ to sys.path)

from namma.capture.ingest import ingest_capture
from namma.capture.media import kind_of
from namma.core import timefmt
from namma.core.jsonio import load_strict, write_json
from namma.studio.package import publish_static_site
from namma.studio.pipeline import run_pipeline
from namma.studio.profiles import config_sha256, get_profile
from namma.studio.projects import ProjectStore, safe_filename


def progress(label, frac):
    print(f"  {label}: {frac:.0%}", flush=True)


def cmd_new(a):
    store = ProjectStore(a.root)
    notices = a.notice or []
    doc = store.create(a.title, notices=notices)
    dst = store.originals(doc["project_id"])
    src = Path(a.source)
    files = [src] if src.is_file() else sorted(p for p in src.iterdir() if p.is_file())
    n = 0
    for f in files:
        if kind_of(f.name) == "unsupported" and not a.include_all:
            continue
        shutil.copy2(f, dst / safe_filename(f.name))
        n += 1
    print(json.dumps({"project_dir": str(store.root / doc["project_id"]), "files_copied": n}))
    return 0


def cmd_check(a):
    d = Path(a.project_dir)
    title = load_strict(d / "project.json").get("title", "")
    rep = ingest_capture(d, max_frames=a.max_frames, venue_label=title, progress=progress)
    print(json.dumps({k: rep[k] for k in ("status", "mode", "stats", "seconds")}, indent=1))
    for m in rep["errors"] + rep["warnings"]:
        print(f"  [{m['severity']}] {m['code']}: {m['message']}")
    return 0 if rep["status"] == "ready" else 2


def cmd_run(a):
    d = Path(a.project_dir)
    cfg = get_profile(a.profile)
    proj = load_strict(d / "project.json")
    run_id = a.run
    if not run_id:
        store = ProjectStore(d.parent)
        run_id = store.new_run_id(proj["project_id"])
        proj["runs"].append({"run_id": run_id, "profile": a.profile, "created_at": timefmt.now_iso(),
                             "config_sha256": config_sha256(cfg)})
        write_json(d / "project.json", proj)
    print(f"run {run_id} profile {a.profile} config {config_sha256(cfg)[:12]}", flush=True)
    st = run_pipeline(d, run_id, cfg, stop_after=a.stop_after)
    for s in st["stages"]:
        print(f"  {s['name']:10s} {s['status']:9s} {s.get('seconds', '')}")
    print(json.dumps({"run_id": run_id, "status": st["status"], "error": st.get("error")}, indent=1))
    return 0 if st["status"] == "succeeded" else 1


def cmd_publish(a):
    lods = [int(x) for x in a.lods.split(",")] if a.lods else None
    print(json.dumps(publish_static_site(a.package_dir, a.out_dir, include_lods=lods, max_file_mb=a.max_file_mb), indent=1))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new")
    p.add_argument("--title", required=True)
    p.add_argument("--from", dest="source", required=True)
    p.add_argument("--root", default=str(ROOT / "projects"))
    p.add_argument("--notice", action="append")
    p.add_argument("--include-all", action="store_true")
    p = sub.add_parser("check")
    p.add_argument("project_dir")
    p.add_argument("--max-frames", type=int, default=300)
    p = sub.add_parser("run")
    p.add_argument("project_dir")
    p.add_argument("--profile", default="standard")
    p.add_argument("--run")
    p.add_argument("--stop-after")
    p = sub.add_parser("publish")
    p.add_argument("package_dir")
    p.add_argument("out_dir")
    p.add_argument("--lods")
    p.add_argument("--max-file-mb", type=float)
    a = ap.parse_args()
    return {"new": cmd_new, "check": cmd_check, "run": cmd_run, "publish": cmd_publish}[a.cmd](a)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
