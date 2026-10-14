"""Install the CPU reconstruction tools into tools/ (portable, no system changes, no admin rights).

    python scripts/setup_tools.py            # download (if needed), verify SHA-256, unpack
    python scripts/setup_tools.py --check    # only report what is installed

Pinned official release assets (checksums recorded in Phase 12, phase12_artifacts/logs/downloads.log):
- COLMAP 4.2.1, Windows, no CUDA (BSD-3-Clause)  https://github.com/colmap/colmap/releases/tag/4.2.1
- OpenMVS 2.4.0, Windows x64 (AGPL-3.0)           https://github.com/cdcseacave/openMVS/releases/tag/v2.4.0
FFmpeg is NOT installed by this script (use `winget install Gyan.FFmpeg` or https://ffmpeg.org/download.html);
it is needed only for video input.
Linux / macOS: install COLMAP and OpenMVS from your package manager or source, then set NAMMA_COLMAP and
NAMMA_OPENMVS_DIR (see docs/INSTALL.md). Only the Windows path has been tested by the team.
"""
import argparse
import hashlib
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
ASSETS = [
    {"name": "colmap", "url": "https://github.com/colmap/colmap/releases/download/4.2.1/colmap-x64-windows-nocuda.zip",
     "file": "colmap-x64-windows-nocuda.zip", "bytes": 127731595,
     "sha256": "c493d88cb4a43f21afba42cb55bf786e5434a0420e73db0ba1454c5d8b3686ae",
     "dest": "colmap-4.2.1-nocuda", "check": "colmap-4.2.1-nocuda/bin/colmap.exe"},
    {"name": "openmvs", "url": "https://github.com/cdcseacave/openMVS/releases/download/v2.4.0/OpenMVS_Windows_x64.zip",
     "file": "OpenMVS_Windows_x64.zip", "bytes": 22363227,
     "sha256": "0c31660c15c9ebc4c106873cf67564d9570d404aef7a6403451da1b6178b2167",
     "dest": "openmvs-2.4.0", "check": "openmvs-2.4.0/vc17/x64/Release/TextureMesh.exe"},
]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def safe_unzip(zpath, dest):
    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            t = (dest / n).resolve()
            if not str(t).startswith(str(dest.resolve())):
                raise RuntimeError(f"unsafe path in archive: {n}")
        z.extractall(dest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if sys.platform != "win32" and not a.check:
        sys.exit("Automatic setup is for Windows. On Linux / macOS install COLMAP and OpenMVS and set "
                 "NAMMA_COLMAP / NAMMA_OPENMVS_DIR (docs/INSTALL.md).")
    ok = True
    for asset in ASSETS:
        target = TOOLS / asset["check"]
        if target.exists():
            print(f"{asset['name']}: installed ({target.relative_to(ROOT)})")
            continue
        if a.check:
            print(f"{asset['name']}: MISSING")
            ok = False
            continue
        dl = TOOLS / "downloads" / asset["file"]
        dl.parent.mkdir(parents=True, exist_ok=True)
        if not (dl.exists() and dl.stat().st_size == asset["bytes"]):
            print(f"{asset['name']}: downloading {asset['url']} ({asset['bytes'] / 2**20:.0f} MB)")
            with urllib.request.urlopen(asset["url"], timeout=120) as r, open(dl, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
        digest = sha256(dl)
        if digest != asset["sha256"]:
            sys.exit(f"{asset['name']}: SHA-256 mismatch ({digest}); refusing to unpack")
        dest = TOOLS / asset["dest"]
        dest.mkdir(parents=True, exist_ok=True)
        safe_unzip(dl, dest)
        if not target.exists():
            # some archives contain one top-level folder; flatten it
            inner = [p for p in dest.iterdir() if p.is_dir()]
            if len(inner) == 1 and (inner[0] / Path(asset["check"]).relative_to(asset["dest"])).exists():
                for p in inner[0].iterdir():
                    shutil.move(str(p), str(dest / p.name))
                inner[0].rmdir()
        print(f"{asset['name']}: {'installed' if target.exists() else 'UNPACKED BUT NOT FOUND - check ' + str(dest)}")
        ok &= target.exists()
    ff = shutil.which("ffmpeg")
    print(f"ffmpeg: {'found at ' + ff if ff else 'not found (needed only for video input)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
