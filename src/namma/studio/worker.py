"""Worker process for long Studio tasks (keeps the web server responsive and survives page reloads).

    python -m namma.studio.worker ingest      --project-dir <dir> [--max-frames 200]
    python -m namma.studio.worker reconstruct --project-dir <dir> --run <run_id> --profile preview|standard
    python -m namma.studio.worker splat       --project-dir <dir> --run <run_id>     (photoreal view, imports)
"""
import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from namma.core import timefmt
from namma.core.jsonio import write_json


def task_ingest(a):
    from namma.capture.ingest import ingest_capture
    d = Path(a.project_dir)
    st = d / "capture" / "ingest_status.json"
    state = {"status": "running", "pid": os.getpid(), "started_at": timefmt.now_iso(), "label": "Starting", "fraction": 0}
    write_json(st, state)

    def progress(label, frac):
        state.update(label=label, fraction=round(frac, 3))
        write_json(st, state)
    try:
        title = json.loads((d / "project.json").read_text(encoding="utf-8")).get("title", "")
        rep = ingest_capture(d, max_frames=a.max_frames, venue_label=title, progress=progress)
        state.update(status="done", result=rep["status"], ended_at=timefmt.now_iso(), fraction=1)
    except Exception as e:
        (d / "capture").mkdir(parents=True, exist_ok=True)
        (d / "capture" / "ingest_traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        state.update(status="error", error=f"{e.__class__.__name__}: {e}", ended_at=timefmt.now_iso())
    write_json(st, state)
    return 0 if state["status"] == "done" else 1


def task_reconstruct(a):
    from namma.studio.pipeline import run_pipeline
    from namma.studio.profiles import get_profile
    cfg = get_profile(a.profile)
    run_dir = Path(a.project_dir) / "runs" / a.run
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "worker.json", {"pid": os.getpid(), "started_at": timefmt.now_iso(), "profile": a.profile})
    st = run_pipeline(a.project_dir, a.run, cfg, stop_after=a.stop_after)
    print(json.dumps({"status": st["status"], "error": st.get("error")}, indent=1))
    return 0 if st["status"] == "succeeded" else 1


def task_splat(a):
    from namma.studio.splat import make_photoreal
    work = Path(a.project_dir) / "runs" / a.run / "65_splat"
    work.mkdir(parents=True, exist_ok=True)
    st = work / "status.json"
    cancel = work / "CANCEL"
    cancel.unlink(missing_ok=True)
    state = {"status": "running", "pid": os.getpid(), "started_at": timefmt.now_iso(), "label": "Starting", "fraction": 0}
    write_json(st, state)

    def progress(label, frac):
        state.update(label=label, fraction=round(frac, 3))
        write_json(st, state)
    try:
        state["result"] = make_photoreal(a.project_dir, a.run, progress=progress, cancel_file=cancel)
        state.update(status="done", ended_at=timefmt.now_iso(), fraction=1)
    except Exception as e:
        (work / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        cancelled = cancel.exists()
        state.update(status="cancelled" if cancelled else "error", ended_at=timefmt.now_iso(),
                     error=None if cancelled else f"{e}")
    write_json(st, state)
    return 0 if state["status"] == "done" else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=["ingest", "reconstruct", "splat"])
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--run")
    ap.add_argument("--profile", default="standard")
    ap.add_argument("--max-frames", type=int, default=300)
    ap.add_argument("--stop-after")
    a = ap.parse_args(argv)
    return {"ingest": task_ingest, "reconstruct": task_reconstruct, "splat": task_splat}[a.task](a)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
