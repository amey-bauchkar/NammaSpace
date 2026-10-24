"""Start, observe, cancel and resume worker processes. One heavy reconstruction at a time.

The single-job rule protects the laptop's RAM (Phase 12: D-01 / D-04 memory crashes): a second
reconstruction request is refused with a clear message instead of being started in parallel.
A run whose worker died (closed laptop, crash) is reported as "interrupted" and can be resumed.
"""
import os
import subprocess
import sys

import psutil

from namma import PROJECT_ROOT
from namma.core import timefmt
from namma.core.jsonio import load_strict, write_json
from namma.studio.profiles import config_sha256, get_profile

FLAGS = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0))


class JobError(RuntimeError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.status = code, status


def _alive(pid):
    try:
        p = psutil.Process(int(pid))
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, ValueError, TypeError):
        return False


def _spawn(args, log_path):
    env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""),
               PYTHONIOENCODING="utf-8")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "ab")
    return subprocess.Popen([sys.executable, "-m", "namma.studio.worker", *args], stdout=log, stderr=subprocess.STDOUT,
                            cwd=str(PROJECT_ROOT), env=env, creationflags=FLAGS)


class JobManager:
    def __init__(self, store):
        self.store = store

    # ----------------------------------------------------------------- capture check
    def start_ingest(self, project_id, max_frames=None):
        if max_frames is None:
            from namma.recon import hardware
            max_frames = hardware.resources().max_images
        d = self.store.path(project_id)
        st = d / "capture" / "ingest_status.json"
        if st.exists():
            s = load_strict(st)
            if s.get("status") == "running" and _alive(s.get("pid")):
                raise JobError("INGEST-RUNNING", "The capture check is already running.")
        self.store.clear_capture(project_id)
        write_json(st, {"status": "starting", "started_at": timefmt.now_iso(), "label": "Starting", "fraction": 0})
        p = _spawn(["ingest", "--project-dir", str(d), "--max-frames", str(max_frames)], d / "capture" / "ingest_worker.log")
        return {"pid": p.pid}

    def ingest_status(self, project_id):
        d = self.store.path(project_id)
        st = d / "capture" / "ingest_status.json"
        s = load_strict(st) if st.exists() else {"status": "not_started"}
        if s.get("status") in ("running", "starting") and s.get("pid") and not _alive(s["pid"]):
            s["status"] = "error"
            s["error"] = "The capture check stopped unexpectedly; run it again."
        rep = d / "capture" / "capture_report.json"
        if s.get("status") == "done" and rep.exists():
            s["report"] = load_strict(rep)
        return s

    # ----------------------------------------------------------------- reconstruction
    def active(self):
        """(project_id, run_id) of the reconstruction that is running now (Studio worker or CLI), or None."""
        for proj in self.store.list():
            for r in proj.get("runs", []):
                d = self.store.root / proj["project_id"] / "runs" / r["run_id"]
                st = load_strict(d / "run.json") if (d / "run.json").exists() else {}
                w = load_strict(d / "worker.json") if (d / "worker.json").exists() else {}
                pids = [x for x in (st.get("pid"), w.get("pid")) if x]
                if st.get("status", "queued" if w else None) in ("running", "queued") and any(_alive(x) for x in pids):
                    return proj["project_id"], r["run_id"]
        return None

    def start_run(self, project_id, profile):
        d = self.store.path(project_id)
        rep = d / "capture" / "capture_report.json"
        if not rep.exists() or load_strict(rep)["status"] != "ready":
            raise JobError("CAPTURE-NOT-READY", "Check the capture first; it must have no blocking problems.", 400)
        act = self.active()
        if act:
            raise JobError("JOB-BUSY", f"Another reconstruction is running ({act[0]}). This laptop runs one at a time "
                                       "to stay within memory; wait for it to finish or cancel it.")
        cfg = get_profile(profile)
        run_id = self.store.new_run_id(project_id)
        proj = self.store.get(project_id)
        proj["runs"].append({"run_id": run_id, "profile": profile, "created_at": timefmt.now_iso(),
                             "config_sha256": config_sha256(cfg)})
        self.store.save(proj)
        run_dir = d / "runs" / run_id
        run_dir.mkdir(parents=True)
        p = _spawn(["reconstruct", "--project-dir", str(d), "--run", run_id, "--profile", profile],
                   run_dir / "logs" / "worker.log")
        write_json(run_dir / "worker.json", {"pid": p.pid, "started_at": timefmt.now_iso(), "profile": profile})
        return {"run_id": run_id, "pid": p.pid}

    def resume_run(self, project_id, run_id):
        run_dir = self.store.run_dir(project_id, run_id)
        st = self.run_status(project_id, run_id)
        if st["status"] in ("running", "queued"):
            raise JobError("RUN-RUNNING", "This run is still running.")
        if st["status"] == "succeeded":
            raise JobError("RUN-DONE", "This run already finished.")
        act = self.active()
        if act:
            raise JobError("JOB-BUSY", f"Another reconstruction is running ({act[0]}).")
        profile = st.get("profile") or "standard"
        (run_dir / "CANCEL").unlink(missing_ok=True)
        p = _spawn(["reconstruct", "--project-dir", str(self.store.path(project_id)), "--run", run_id,
                    "--profile", profile], run_dir / "logs" / "worker.log")
        write_json(run_dir / "worker.json", {"pid": p.pid, "started_at": timefmt.now_iso(), "profile": profile})
        return {"run_id": run_id, "pid": p.pid}

    def cancel_run(self, project_id, run_id):
        run_dir = self.store.run_dir(project_id, run_id)
        (run_dir / "CANCEL").write_text(timefmt.now_iso(), encoding="utf-8")
        return {"cancel_requested": True}

    def run_status(self, project_id, run_id):
        run_dir = self.store.run_dir(project_id, run_id)
        st_path = run_dir / "run.json"
        st = load_strict(st_path) if st_path.exists() else {"run_id": run_id, "status": "queued", "stages": [],
                                                            "progress": 0}
        w = run_dir / "worker.json"
        pids = [x for x in (st.get("pid"), load_strict(w).get("pid") if w.exists() else None) if x]
        if st.get("status") in ("running", "queued") and pids and not any(_alive(x) for x in pids):
            if (run_dir / "run.json").exists() or not w.exists() or (timefmt.parse(timefmt.now_iso()) - timefmt.parse(
                    load_strict(w)["started_at"])).total_seconds() > 60:
                st["status"] = "interrupted"
                st["error"] = {"stage": (st.get("current") or {}).get("stage"),
                               "message": "Processing stopped unexpectedly (the worker is no longer running).",
                               "hint": "Press Resume: completed steps are kept and processing continues."}
        if w.exists():
            st["profile"] = st.get("profile") or load_strict(w).get("profile")
        return st

    def tail_log(self, project_id, run_id, lines=60):
        run_dir = self.store.run_dir(project_id, run_id)
        logs = sorted((run_dir / "logs").glob("*.log"), key=lambda p: p.stat().st_mtime)
        if not logs:
            return {"file": None, "lines": []}
        last = logs[-1]
        with open(last, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 20000))
            text = f.read().decode("utf-8", "replace")
        return {"file": last.name, "lines": text.splitlines()[-lines:]}
