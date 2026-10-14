"""Run an external command, recording wall time and peak resident memory of its process tree.

Usage (from Python):  run(step, argv, log_dir, timeout_s)
Every call appends one JSON line to <log_dir>/commands.jsonl and writes
<log_dir>/<step>.stdout.txt / .stderr.txt. A command that exceeds its time
budget is terminated (whole tree) and recorded as "timeout".
"""
import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import psutil


def _tree_rss(proc):
    total = 0
    try:
        procs = [proc] + proc.children(recursive=True)
    except psutil.Error:
        return 0
    for p in procs:
        try:
            total += p.memory_info().rss
        except psutil.Error:
            pass
    return total


def run(step, argv, log_dir, timeout_s, cwd=None, env=None):
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    out_f = open(log_dir / f"{step}.stdout.txt", "w", encoding="utf-8", errors="replace")
    err_f = open(log_dir / f"{step}.stderr.txt", "w", encoding="utf-8", errors="replace")
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    t0 = time.perf_counter()
    p = subprocess.Popen([str(a) for a in argv], stdout=out_f, stderr=err_f, cwd=cwd, env=env)
    ps = psutil.Process(p.pid)
    peak = [0]
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            peak[0] = max(peak[0], _tree_rss(ps))
            stop.wait(0.25)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    status = "ok"
    try:
        rc = p.wait(timeout=timeout_s)
        if rc != 0:
            status = "error"
    except subprocess.TimeoutExpired:
        status = "timeout"
        for c in ps.children(recursive=True):
            c.kill()
        p.kill()
        rc = p.wait()
    stop.set()
    th.join()
    out_f.close()
    err_f.close()
    rec = {"step": step, "argv": [str(a) for a in argv], "cwd": str(cwd) if cwd else None, "started": started,
           "wall_s": round(time.perf_counter() - t0, 2), "peak_rss_mb": round(peak[0] / 2**20, 1),
           "return_code": rc, "status": status, "timeout_s": timeout_s,
           "machine_ram_gb": round(psutil.virtual_memory().total / 2**30, 1)}
    with open(log_dir / "commands.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(f"[{step}] {status} rc={rc} wall={rec['wall_s']}s peakRSS={rec['peak_rss_mb']}MB", flush=True)
    return rec
