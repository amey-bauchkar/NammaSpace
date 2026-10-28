"""Run one external tool with logging, a time budget, peak-memory sampling and cancellation.

Every call appends one JSON line to <log_dir>/commands.jsonl and keeps the full stdout/stderr
in <log_dir>/<step>.log. A command that exceeds its budget, or whose run is cancelled
(cancel file present), has its whole process tree terminated.
(Generalises scripts/phase12/runlogged.py, which stays unchanged for the Phase 12 record.)
"""
import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    import psutil
except ModuleNotFoundError:  # The separate GPU splat environment deliberately has only training dependencies.
    psutil = None


class StepFailed(RuntimeError):
    def __init__(self, record, message):
        super().__init__(message)
        self.record = record


def _tree(proc):
    if psutil is None:
        return []
    try:
        return [proc] + proc.children(recursive=True)
    except psutil.Error:
        return [proc]


def _tree_rss(proc):
    if psutil is None:
        return 0
    total = 0
    for p in _tree(proc):
        try:
            total += p.memory_info().rss
        except psutil.Error:
            pass
    return total


def _kill_tree(proc):
    if psutil is None:
        try:
            proc.kill()
        except OSError:
            pass
        return
    for p in reversed(_tree(proc)):
        try:
            p.kill()
        except psutil.Error:
            pass


def _follow(directory, pattern, since, on_line, stop):
    """Feed new lines of the newest file matching `pattern` (created after `since`) to on_line.

    OpenMVS writes its progress to its own log file (e.g. DensifyPointCloud-<id>.log), not to stdout.
    """
    pos, current = 0, None
    while not stop.is_set():
        files = [p for p in Path(directory).glob(pattern) if p.stat().st_mtime >= since - 1]
        if files:
            newest = max(files, key=lambda p: p.stat().st_mtime)
            if newest != current:
                current, pos = newest, 0
            try:
                with open(current, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(pos)
                    for line in fh.readlines():
                        try:
                            on_line(line)
                        except Exception:  # noqa: BLE001 - progress parsing must never break a run
                            pass
                    pos = fh.tell()
            except OSError:
                pass
        stop.wait(1.0)


def run_step(step, argv, log_dir, *, timeout_s, cwd=None, cancel_file=None, on_line=None, env=None, follow=None):
    """Run argv; return the record. Raises StepFailed on non-zero exit, timeout or cancellation.

    follow=(directory, glob): also feed lines of the tool's own log file to on_line (progress only).
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{step}.log"
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    t0 = time.perf_counter()
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        log.write("$ " + " ".join(f'"{a}"' if " " in str(a) else str(a) for a in argv) + "\n\n")
        log.flush()
        p = subprocess.Popen([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd,
                             env=env, text=True, encoding="utf-8", errors="replace", bufsize=1,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ps = psutil.Process(p.pid) if psutil is not None else p
        peak = [0]
        reason = [None]
        stop = threading.Event()

        def watchdog():
            while not stop.is_set():
                peak[0] = max(peak[0], _tree_rss(ps))
                if time.perf_counter() - t0 > timeout_s:
                    reason[0] = "timeout"
                    _kill_tree(ps)
                    return
                if cancel_file is not None and Path(cancel_file).exists():
                    reason[0] = "cancelled"
                    _kill_tree(ps)
                    return
                stop.wait(0.25)

        th = threading.Thread(target=watchdog, daemon=True)
        th.start()
        fstop = threading.Event()
        if follow and on_line is not None:
            threading.Thread(target=_follow, args=(follow[0], follow[1], time.time(), on_line, fstop), daemon=True).start()
        tail = []
        for line in p.stdout:
            log.write(line)
            log.flush()             # the log is shown live in the Studio ("Technical log")
            tail.append(line.rstrip())
            if len(tail) > 40:
                tail.pop(0)
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:   # progress parsing must never break a run
                    pass
        rc = p.wait()
        stop.set()
        fstop.set()
        th.join()
    status = reason[0] or ("ok" if rc == 0 else "error")
    rec = {"step": step, "argv": [str(a) for a in argv], "cwd": str(cwd) if cwd else None, "started": started,
           "wall_s": round(time.perf_counter() - t0, 2), "peak_rss_mb": round(peak[0] / 2**20, 1),
           "return_code": rc, "status": status, "timeout_s": timeout_s, "log": log_path.name,
           "machine_ram_gb": round(psutil.virtual_memory().total / 2**30, 1) if psutil is not None else None,
           "available_ram_gb_after": round(psutil.virtual_memory().available / 2**30, 2) if psutil is not None else None}
    with open(log_dir / "commands.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    if status != "ok":
        rec["tail"] = tail[-12:]
        msg = {"timeout": f"{step} exceeded its time budget of {timeout_s} s",
               "cancelled": f"{step} was cancelled"}.get(status, f"{step} failed with exit code {rc}")
        raise StepFailed(rec, msg)
    return rec
