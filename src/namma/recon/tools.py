"""Locate the external reconstruction tools and pick memory-safe settings.

Tools are NOT bundled with the source (licences: COLMAP BSD-3-Clause; OpenMVS AGPL-3.0,
see THIRD_PARTY_NOTICES.md). Default locations are the portable builds unpacked by
scripts/setup_tools.py; override with the environment variables
NAMMA_COLMAP (path to colmap.exe / colmap) and NAMMA_OPENMVS_DIR (folder with the OpenMVS binaries).

GPU (docs/HARDWARE.md): when the CUDA builds are installed (setup_tools.py --cuda) and namma.recon.hardware
decides the NVIDIA GPU is usable, COLMAP's CUDA build is used for feature extraction and matching and OpenMVS's
CUDA DensifyPointCloud for depth maps. Every other step (mapping, meshing, texturing, Python stages) is CPU work
in these tools. Whether a binary really has CUDA is read from the binary itself, never assumed.
NAMMA_OPENMVS_CUDA_DIR overrides the folder of the CUDA DensifyPointCloud.
"""
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import psutil

from namma import PROJECT_ROOT
from namma.recon import hardware

EXE = ".exe" if os.name == "nt" else ""
DEFAULT_COLMAP = PROJECT_ROOT / "tools" / "colmap-4.2.1-nocuda" / "bin" / f"colmap{EXE}"
DEFAULT_OPENMVS = PROJECT_ROOT / "tools" / "openmvs-2.4.0" / "vc17" / "x64" / "Release"
CUDA_COLMAP_ROOT = PROJECT_ROOT / "tools" / "colmap-4.2.1-cuda"
CUDA_OPENMVS_ROOT = PROJECT_ROOT / "tools" / "openmvs-2.4.0-cuda"
OPENMVS_TOOLS = ("InterfaceCOLMAP", "DensifyPointCloud", "ReconstructMesh", "TextureMesh")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ToolMissing(RuntimeError):
    kind = "tool_missing"


@dataclass(frozen=True)
class Tools:
    colmap: Path
    openmvs_dir: Path
    colmap_cuda: bool = False                 # this COLMAP binary was built with CUDA
    densify_cuda_dir: Path | None = None      # folder of a CUDA DensifyPointCloud (None: CPU build only)
    use_gpu: bool = False                     # GPU judged usable now (configuration + driver + free VRAM)
    gpu_reason: str = ""                      # why the GPU is / is not used (logs and diagnostics)
    notes: tuple = ()

    def mvs(self, name):
        return self.openmvs_dir / f"{name}{EXE}"

    def densify_exe(self, gpu):
        """DensifyPointCloud to run: the CUDA build when the GPU is to be used, else the validated CPU build."""
        if gpu and self.densify_cuda_dir is not None:
            return self.densify_cuda_dir / f"DensifyPointCloud{EXE}"
        return self.mvs("DensifyPointCloud")

    @property
    def colmap_gpu(self):
        return self.use_gpu and self.colmap_cuda

    @property
    def densify_gpu(self):
        return self.use_gpu and self.densify_cuda_dir is not None

    def describe(self):
        return {"colmap": str(self.colmap), "openmvs_dir": str(self.openmvs_dir), "colmap_cuda": self.colmap_cuda,
                "densify_cuda_dir": str(self.densify_cuda_dir) if self.densify_cuda_dir else None,
                "use_gpu": self.use_gpu, "gpu_reason": self.gpu_reason,
                "gpu_steps": [s for s, on in (("colmap_features", self.colmap_gpu), ("colmap_matching", self.colmap_gpu),
                                              ("openmvs_densify", self.densify_gpu)) if on],
                "notes": list(self.notes)}


@lru_cache(maxsize=16)
def _help_text(exe, mtime, args):
    try:
        p = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW,
                           encoding="utf-8", errors="replace")
        return (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def colmap_has_cuda(exe):
    """COLMAP prints its build in the help banner ("... with CUDA" / "... without CUDA")."""
    exe = Path(exe)
    if not exe.exists():
        return False
    text = _help_text(str(exe), exe.stat().st_mtime, ("help",))
    return bool(re.search(r"\bwith CUDA\b", text)) and "without CUDA" not in text


@lru_cache(maxsize=8)
def _openmvs_help(exe, mtime):
    """OpenMVS writes --help into <tool>-<id>.log in the working directory (stdout stays empty, exit code 1), so
    run it in a temporary folder and read that log."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="namma_mvs_help_") as td:
        try:
            subprocess.run([str(exe), "--help"], cwd=td, capture_output=True, timeout=60, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return "".join(p.read_text(encoding="utf-8", errors="replace") for p in Path(td).glob("*.log"))


def densify_has_cuda(exe):
    """OpenMVS CUDA builds of DensifyPointCloud accept --cuda-device; CPU builds do not list it (checked on the
    official 2.4.0 Windows builds: OpenMVS_Windows_x64_CUDA.7z lists it, OpenMVS_Windows_x64.zip does not)."""
    exe = Path(exe)
    if not exe.exists():
        return False
    return "--cuda-device" in _openmvs_help(str(exe), exe.stat().st_mtime)


def _first(root, name):
    root = Path(root)
    return next(iter(sorted(root.rglob(name))), None) if root.exists() else None


def find_tools(require_openmvs=True, gpu_info=None):
    use_gpu, why = hardware.gpu_decision(hardware.resources(), gpu_info)
    notes = []
    colmap = Path(os.environ["NAMMA_COLMAP"]) if os.environ.get("NAMMA_COLMAP") else None
    if colmap is None and use_gpu:
        cand = _first(CUDA_COLMAP_ROOT, f"colmap{EXE}")
        if cand is not None and colmap_has_cuda(cand):
            colmap = cand
        elif cand is not None:
            notes.append(f"{cand} does not report CUDA support; using the CPU build")
    colmap = colmap or DEFAULT_COLMAP
    if not colmap.exists():
        found = shutil.which("colmap")
        if not found:
            raise ToolMissing(f"COLMAP not found at {colmap}. Run: python scripts/setup_tools.py "
                              "(or set NAMMA_COLMAP).")
        colmap = Path(found)
    colmap_cuda = colmap_has_cuda(colmap)
    if colmap_cuda and not use_gpu:
        notes.append("COLMAP has CUDA but the GPU is not used: " + why)
    mvs = Path(os.environ.get("NAMMA_OPENMVS_DIR") or DEFAULT_OPENMVS)
    if require_openmvs:
        missing = [t for t in OPENMVS_TOOLS if not (mvs / f"{t}{EXE}").exists()]
        if missing:
            raise ToolMissing(f"OpenMVS tools {missing} not found in {mvs}. Run: python scripts/setup_tools.py "
                              "(or set NAMMA_OPENMVS_DIR).")
    dcuda = None
    env_dir = os.environ.get("NAMMA_OPENMVS_CUDA_DIR")
    cand = Path(env_dir) / f"DensifyPointCloud{EXE}" if env_dir else _first(CUDA_OPENMVS_ROOT, f"DensifyPointCloud{EXE}")
    if cand is not None and cand.exists():
        if densify_has_cuda(cand):
            dcuda = cand.parent
        else:
            notes.append(f"{cand} does not list --cuda-device (not a CUDA build, or DLLs missing); dense stays on CPU")
    return Tools(colmap, mvs, colmap_cuda=colmap_cuda, densify_cuda_dir=dcuda, use_gpu=use_gpu, gpu_reason=why,
                 notes=tuple(notes))


def available_ram_gb():
    return psutil.virtual_memory().available / 2**30


def _fit_ram_gb(reserve_gb):
    """RAM a step may fill now: free RAM minus a working reserve, capped by the configured job budget.
    When ram_reserve_gb is 0 or less (uncapped mode), the full configured budget is used without throttling."""
    res = hardware.resources()
    if res.ram_reserve_gb <= 0:
        return res.ram_budget_gb
    return min(available_ram_gb() - reserve_gb, res.ram_budget_gb)


def sift_threads(max_image_px, requested=None, reserve_gb=1.5, gpu=False):
    """Feature-extraction threads that fit in the RAM available now.

    Phase 12 (D-01): 12 threads on 24 MP images crashed (6.4 GB peak with ~6.7 GB free);
    4 threads peaked at 7.85 GB (including page-cache-heavy image decoding). We budget
    ~0.9 GB per thread at 3200 px and scale with pixel count. The ceiling was a fixed 4 on the Phase 12 laptop;
    it is now the configurable `sift_max_threads` (default: physical cores), and the RAM fit still applies.
    With the GPU, SIFT runs on the GPU and the threads only decode images: ~0.35 GB each at 3200 px.
    """
    if requested:
        return max(1, int(requested))
    per_thread = (0.35 if gpu else 0.9) * (max_image_px / 3200) ** 2 + 0.15
    fit = int(_fit_ram_gb(reserve_gb) // per_thread)
    return max(1, min(hardware.resources().sift_max_threads, fit, psutil.cpu_count(logical=True) or 1))


def match_threads(max_features, requested=None, reserve_gb=2.0, gpu=False):
    """Feature-matching threads that fit in the RAM available now.

    Phase 13 (D13-01): COLMAP 4.2.1 (CPU) exhaustive matching WITH guided matching on 31 images x 8192 features
    crashed at 9.3 GB (12 threads) and again at 10.4 GB (6 threads); without guided matching Phase 12 peaked at
    0.36 GB. Guided matching is therefore off in every profile; the thread cap (formerly a fixed 6, now the
    configurable `match_max_threads`) is kept as a second safeguard.
    GPU matching: descriptors are matched on the GPU; the CPU threads run geometric verification. Measured on the
    HP Victus (TUM sample, 61 images, 1830 pairs): 2 threads 50.9 s, 10 threads 18.6 s, 16 threads 15.0 s, peak RSS
    212-217 MB whatever the thread count. 0.15 GB per thread keeps a wide margin.
    With the Studio, an IDE and a browser open only ~1.3-2.6 GB were free, and the 2 GB reserve left ONE thread:
    300 iPhone video frames (8192 features) then matched in 547 s; 10 threads matched the same pairs in 148 s
    (identical result, 1343 vs 1342 verified pairs). GPU matching therefore keeps only a 0.5 GB reserve and
    0.05 GB per thread (measured 380 MB peak for the whole matcher).
    """
    if requested:
        return max(1, int(requested))
    if gpu:
        per_thread, reserve_gb = 0.05, min(reserve_gb, 0.5)
    else:
        per_thread = 1.1 * (max_features / 8192) + 0.1
    fit = int(_fit_ram_gb(reserve_gb) // per_thread)
    return max(1, min(hardware.resources().match_max_threads, fit, psutil.cpu_count(logical=True) or 1))


def mvs_threads():
    """OpenMVS / general CPU threads: `cpu_threads` (default: logical CPUs - 2, so the Studio UI stays responsive)."""
    return max(1, min(hardware.resources().cpu_threads, psutil.cpu_count(logical=True) or 1))
