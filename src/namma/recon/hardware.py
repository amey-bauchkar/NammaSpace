"""Machine resources (RAM, CPU, NVIDIA GPU, disk) and the configurable limits every heavy step obeys.

One place for hardware-specific numbers, so they are not scattered through the pipeline. Limits come from,
in increasing priority:

  1. defaults derived from the detected machine (below),
  2. a JSON file: config/resources.json (or the path in NAMMA_RESOURCES); see config/resources.example.json,
  3. environment variables NAMMA_<KEY> (e.g. NAMMA_GPU=off, NAMMA_RAM_RESERVE_GB=4).

Nothing here is part of a profile's hashed configuration (namma.studio.profiles): a run made with the frozen
configuration stays comparable whatever the machine; the devices and thread counts actually used are recorded
in each run's logs and stage summaries instead.

Memory model. Windows and the user's other programs keep `ram_reserve_gb` (default: 20 % of RAM, at least 3 GB).
The rest is `ram_budget_gb`, the most a Namma Space job plans for. Thread counts are additionally fitted to the RAM
that is *available now* (namma.recon.tools), so a busy laptop gets fewer threads instead of an out-of-memory crash.
"""
import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, fields
from functools import lru_cache
from pathlib import Path

import psutil

from namma import PROJECT_ROOT

CONFIG_PATH = PROJECT_ROOT / "config" / "resources.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class Resources:
    ram_reserve_gb: float           # kept free for Windows and other programs
    ram_budget_gb: float            # most RAM one Namma Space job plans for
    cpu_threads: int                # OpenMVS / general CPU threads (UI stays responsive)
    sift_max_threads: int           # ceiling for COLMAP feature-extraction threads (RAM fit applies too)
    match_max_threads: int          # ceiling for COLMAP matching threads (RAM fit applies too)
    io_workers: int                 # parallel image decoding in the capture check
    gpu: str                        # "auto" | "on" | "off"
    gpu_index: int                  # CUDA device used by COLMAP / OpenMVS
    vram_reserve_mb: int            # VRAM left to the display and other programs
    min_free_vram_mb: int           # below this (after the reserve) a GPU step falls back to the CPU
    max_images: int                 # capture-check limit (photos / video frames)
    max_video_gb: float             # largest accepted video upload (streamed to disk, not RAM)

    def as_dict(self):
        return asdict(self)


def _defaults():
    total = psutil.virtual_memory().total / 2**30
    logical = psutil.cpu_count(logical=True) or 2
    physical = psutil.cpu_count(logical=False) or max(1, logical // 2)
    reserve = round(max(3.0, 0.2 * total), 1)
    return {
        "ram_reserve_gb": reserve,
        "ram_budget_gb": round(max(1.0, total - reserve), 1),
        "cpu_threads": max(1, logical - 2),
        # Phase 12/13 fixed these at 4 / 6 on a 2.8-GB-free laptop; the RAM fit in namma.recon.tools remains the
        # real guard, so the ceiling follows the physical cores (hyper-threads add little to SIFT / matching)
        "sift_max_threads": max(1, physical),
        "match_max_threads": max(1, physical),
        "io_workers": max(2, min(8, physical // 2)),
        "gpu": "auto",
        "gpu_index": 0,
        "vram_reserve_mb": 768,
        "min_free_vram_mb": 1536,
        "max_images": 300,
        "max_video_gb": 4.0,
    }


def _coerce(name, value):
    typ = {f.name: f.type for f in fields(Resources)}[name]
    if typ == "str" or typ is str:
        v = str(value).strip().lower()
        if name == "gpu" and v not in ("auto", "on", "off"):
            raise ValueError(f"gpu must be auto, on or off (got {value!r})")
        return v
    if typ == "int" or typ is int:
        return int(float(value))
    return float(value)


def config_file():
    return Path(os.environ.get("NAMMA_RESOURCES") or CONFIG_PATH)


def load_resources(path=None, env=None):
    """Resolved limits (defaults <- JSON file <- NAMMA_* environment). Invalid values raise ValueError naming the key."""
    env = os.environ if env is None else env
    vals = _defaults()
    p = Path(path) if path else config_file()
    sources = {k: "default" for k in vals}
    if p.exists():
        doc = json.loads(p.read_text(encoding="utf-8"))
        for k, v in doc.items():
            if k.startswith("_"):
                continue                      # comments
            if k not in vals:
                raise ValueError(f"{p}: unknown resource setting {k!r}; valid: {sorted(vals)}")
            if v is not None:
                vals[k], sources[k] = _coerce(k, v), f"file:{p.name}"
    for k in vals:
        ev = env.get("NAMMA_" + k.upper())
        if ev not in (None, ""):
            try:
                vals[k], sources[k] = _coerce(k, ev), "env"
            except ValueError as e:
                raise ValueError(f"NAMMA_{k.upper()}={ev!r}: {e}") from e
    total = psutil.virtual_memory().total / 2**30
    if sources["ram_budget_gb"] == "default" and sources["ram_reserve_gb"] != "default":
        vals["ram_budget_gb"] = round(max(1.0, total - vals["ram_reserve_gb"]), 1)
    if vals["ram_budget_gb"] > total:
        raise ValueError(f"ram_budget_gb {vals['ram_budget_gb']} exceeds the installed {total:.1f} GB")
    for k in ("cpu_threads", "sift_max_threads", "match_max_threads", "io_workers"):
        vals[k] = max(1, vals[k])
    res = Resources(**vals)
    return res, sources


@lru_cache(maxsize=1)
def resources():
    """Process-wide resolved limits (cached; tests call resources.cache_clear())."""
    return load_resources()[0]


# ---------------------------------------------------------------------------------------------- NVIDIA GPU
def _nvidia_smi():
    exe = shutil.which("nvidia-smi")
    if not exe and os.name == "nt":
        cand = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        exe = str(cand) if cand.exists() else None
    return exe


def parse_nvidia_smi_csv(text):
    """Rows of `nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.used,memory.free,compute_cap
    --format=csv,noheader,nounits` -> list of dicts."""
    gpus = []
    for line in text.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 7:
            continue
        def num(x):
            try:
                return float(x)
            except ValueError:
                return None
        gpus.append({"index": int(p[0]), "name": p[1], "driver": p[2], "vram_total_mb": num(p[3]),
                     "vram_used_mb": num(p[4]), "vram_free_mb": num(p[5]), "compute_capability": p[6]})
    return gpus


_GPU_CACHE = {"at": -1e9, "value": None}


def detect_gpus(timeout_s=15, max_age_s=10.0):
    """Cached for max_age_s (the Studio polls its health endpoint); see _detect_gpus."""
    now = time.monotonic()
    if _GPU_CACHE["value"] is None or now - _GPU_CACHE["at"] > max_age_s:
        _GPU_CACHE.update(at=now, value=_detect_gpus(timeout_s))
    return _GPU_CACHE["value"]


def _detect_gpus(timeout_s=15):
    """NVIDIA GPUs as the driver reports them now. Never raises: {"available": False, "reason": ...} instead.

    This proves only that the driver sees a GPU. Whether a *tool* uses it depends on that tool's build
    (namma.recon.tools.Tools.cuda) and is recorded per step.
    """
    exe = _nvidia_smi()
    if not exe:
        return {"available": False, "gpus": [], "reason": "nvidia-smi not found (no NVIDIA driver installed)"}
    q = "index,name,driver_version,memory.total,memory.used,memory.free,compute_cap"
    try:
        p = subprocess.run([exe, f"--query-gpu={q}", "--format=csv,noheader,nounits"], capture_output=True, text=True,
                           timeout=timeout_s, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"available": False, "gpus": [], "reason": f"nvidia-smi failed: {e.__class__.__name__}"}
    if p.returncode != 0:
        return {"available": False, "gpus": [], "reason": f"nvidia-smi error: {(p.stderr or p.stdout).strip()[:200]}"}
    gpus = parse_nvidia_smi_csv(p.stdout)
    return {"available": bool(gpus), "gpus": gpus, "reason": None if gpus else "nvidia-smi listed no GPU"}


def gpu_decision(res=None, info=None, need_mb=None):
    """Should a CUDA-capable step use the GPU now? -> (use: bool, reason: str). The tool's own CUDA support is
    checked separately (a CPU-only build never uses the GPU, whatever this says)."""
    res = res or resources()
    if res.gpu == "off":
        return False, "GPU disabled by configuration (gpu=off)"
    info = info if info is not None else detect_gpus()
    if not info["available"]:
        return False, f"no usable NVIDIA GPU: {info['reason']}"
    g = next((g for g in info["gpus"] if g["index"] == res.gpu_index), None)
    if g is None:
        return False, f"GPU index {res.gpu_index} not present (found {[x['index'] for x in info['gpus']]})"
    need = need_mb if need_mb is not None else res.min_free_vram_mb
    free = (g["vram_free_mb"] or 0) - res.vram_reserve_mb
    if free < need and res.gpu != "on":
        return False, (f"{g['name']}: only {free:.0f} MB VRAM free after the {res.vram_reserve_mb} MB reserve "
                       f"(need {need} MB); close GPU-heavy programs or set NAMMA_GPU=on to force")
    return True, f"{g['name']} (driver {g['driver']}, {g['vram_free_mb']:.0f} of {g['vram_total_mb']:.0f} MB free)"


# ---------------------------------------------------------------------------------------------- snapshot
def ram_snapshot():
    vm = psutil.virtual_memory()
    return {"total_gb": round(vm.total / 2**30, 2), "available_gb": round(vm.available / 2**30, 2),
            "used_percent": vm.percent}


def plannable_ram_gb(res=None):
    """RAM a step may plan for now: what is free (other programs keep theirs), capped by the configured budget."""
    res = res or resources()
    return max(0.0, min(psutil.virtual_memory().available / 2**30, res.ram_budget_gb))


def onedrive_synced(path):
    """True when `path` lies in a OneDrive-synchronised folder (sync of GB-sized intermediates slows runs and can
    lock files mid-step)."""
    p = str(Path(path).resolve()).lower()
    roots = [os.environ.get(k) for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")]
    return any(r and p.startswith(str(Path(r).resolve()).lower()) for r in roots) or "\\onedrive\\" in p


def describe_machine(work_dir=None, res=None):
    """Everything a diagnostic needs, as plain JSON-able data."""
    res = res or resources()
    work_dir = Path(work_dir or PROJECT_ROOT)
    du = shutil.disk_usage(work_dir if work_dir.exists() else PROJECT_ROOT)
    return {
        "cpu": {"physical_cores": psutil.cpu_count(logical=False), "logical_cpus": psutil.cpu_count(logical=True)},
        "ram": ram_snapshot(),
        "gpu": detect_gpus(),
        "disk": {"path": str(work_dir), "free_gb": round(du.free / 2**30, 1), "total_gb": round(du.total / 2**30, 1),
                 "onedrive_synced": onedrive_synced(work_dir)},
        "resources": res.as_dict(),
        "config_file": str(config_file()) if config_file().exists() else None,
    }
