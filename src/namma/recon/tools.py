"""Locate the external CPU reconstruction tools and pick memory-safe settings.

Tools are NOT bundled with the source (licences: COLMAP BSD-3-Clause; OpenMVS AGPL-3.0,
see THIRD_PARTY_NOTICES.md). Default locations are the portable builds unpacked by
scripts/setup_tools.py; override with the environment variables
NAMMA_COLMAP (path to colmap.exe / colmap) and NAMMA_OPENMVS_DIR (folder with the OpenMVS binaries).
"""
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import psutil

from namma import PROJECT_ROOT

EXE = ".exe" if os.name == "nt" else ""
DEFAULT_COLMAP = PROJECT_ROOT / "tools" / "colmap-4.2.1-nocuda" / "bin" / f"colmap{EXE}"
DEFAULT_OPENMVS = PROJECT_ROOT / "tools" / "openmvs-2.4.0" / "vc17" / "x64" / "Release"
OPENMVS_TOOLS = ("InterfaceCOLMAP", "DensifyPointCloud", "ReconstructMesh", "TextureMesh")


class ToolMissing(RuntimeError):
    kind = "tool_missing"


@dataclass(frozen=True)
class Tools:
    colmap: Path
    openmvs_dir: Path

    def mvs(self, name):
        return self.openmvs_dir / f"{name}{EXE}"

    def describe(self):
        return {"colmap": str(self.colmap), "openmvs_dir": str(self.openmvs_dir)}


def find_tools(require_openmvs=True):
    colmap = Path(os.environ.get("NAMMA_COLMAP") or DEFAULT_COLMAP)
    if not colmap.exists():
        found = shutil.which("colmap")
        if not found:
            raise ToolMissing(f"COLMAP not found at {colmap}. Run: python scripts/setup_tools.py "
                              "(or set NAMMA_COLMAP).")
        colmap = Path(found)
    mvs = Path(os.environ.get("NAMMA_OPENMVS_DIR") or DEFAULT_OPENMVS)
    if require_openmvs:
        missing = [t for t in OPENMVS_TOOLS if not (mvs / f"{t}{EXE}").exists()]
        if missing:
            raise ToolMissing(f"OpenMVS tools {missing} not found in {mvs}. Run: python scripts/setup_tools.py "
                              "(or set NAMMA_OPENMVS_DIR).")
    return Tools(colmap, mvs)


def available_ram_gb():
    return psutil.virtual_memory().available / 2**30


def sift_threads(max_image_px, requested=None, reserve_gb=1.5):
    """Feature-extraction threads that fit in the RAM available now.

    Phase 12 (D-01): 12 threads on 24 MP images crashed (6.4 GB peak with ~6.7 GB free);
    4 threads peaked at 7.85 GB (including page-cache-heavy image decoding). We budget
    ~0.9 GB per thread at 3200 px and scale with pixel count, never exceed 4 by default.
    """
    if requested:
        return max(1, int(requested))
    per_thread = 0.9 * (max_image_px / 3200) ** 2 + 0.15
    fit = int((available_ram_gb() - reserve_gb) // per_thread)
    return max(1, min(4, fit, psutil.cpu_count(logical=True) or 1))


def match_threads(max_features, requested=None, reserve_gb=2.0):
    """Feature-matching threads that fit in the RAM available now.

    Phase 13 (D13-01): COLMAP 4.2.1 (CPU) exhaustive matching WITH guided matching on 31 images x 8192 features
    crashed at 9.3 GB (12 threads) and again at 10.4 GB (6 threads); without guided matching Phase 12 peaked at
    0.36 GB. Guided matching is therefore off in every profile; the thread cap is kept as a second safeguard.
    """
    if requested:
        return max(1, int(requested))
    per_thread = 1.1 * (max_features / 8192) + 0.1
    fit = int((available_ram_gb() - reserve_gb) // per_thread)
    return max(1, min(6, fit, psutil.cpu_count(logical=True) or 1))


def mvs_threads():
    """Leave two logical cores free so the Studio UI stays responsive."""
    return max(1, (psutil.cpu_count(logical=True) or 2) - 2)
