"""Install the reconstruction tools into tools/ (portable, no system changes, no admin rights).

    python scripts/setup_tools.py            # download (if needed), verify SHA-256, unpack (CPU builds)
    python scripts/setup_tools.py --cuda     # also the NVIDIA CUDA builds (same versions) for GPU machines
    python scripts/setup_tools.py --check    # only report what is installed

Pinned official release assets (checksums recorded in Phase 12, phase12_artifacts/logs/downloads.log):
- COLMAP 4.2.1, Windows, no CUDA (BSD-3-Clause)  https://github.com/colmap/colmap/releases/tag/4.2.1
- OpenMVS 2.4.0, Windows x64 (AGPL-3.0)           https://github.com/cdcseacave/openMVS/releases/tag/v2.4.0
CUDA builds (--cuda; SHA-256 = the digests GitHub publishes for the same two releases): COLMAP 4.2.1
colmap-x64-windows-cuda.zip and OpenMVS 2.4.0 OpenMVS_Windows_x64_CUDA.7z (unpacked with Windows' bsdtar).
They need an NVIDIA driver only (no CUDA toolkit); namma.recon.tools prefers them when a GPU is usable
(docs/HARDWARE.md). The CPU builds stay installed as the fallback.
FFmpeg (video input, incl. the phone capture page): `--ffmpeg` installs a portable GPL build of FFmpeg 8.1.3
(github.com/BtbN/FFmpeg-Builds, dated release; SHA-256 = GitHub's digest) into tools/ffmpeg-8.1; namma.capture.media
finds it there or on PATH. BtbN prunes dated builds after some weeks: if the pinned URL is gone, use any FFmpeg
(e.g. `winget install Gyan.FFmpeg`) on PATH instead.
Linux / macOS: install COLMAP and OpenMVS from your package manager or source, then set NAMMA_COLMAP and
NAMMA_OPENMVS_DIR (see docs/INSTALL.md). Only the Windows path has been tested by the team.
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import time
import urllib.error
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
CUDA_ASSETS = [
    {"name": "colmap-cuda", "url": "https://github.com/colmap/colmap/releases/download/4.2.1/colmap-x64-windows-cuda.zip",
     "file": "colmap-x64-windows-cuda.zip", "bytes": 414666626,
     "sha256": "e9c5cbd84c2ea986d2e970a2473fc2d2e6b34a2cdcf5d3df2765c319a63af881",
     "dest": "colmap-4.2.1-cuda", "find": "colmap.exe"},
    {"name": "openmvs-cuda", "url": "https://github.com/cdcseacave/openMVS/releases/download/v2.4.0/OpenMVS_Windows_x64_CUDA.7z",
     "file": "OpenMVS_Windows_x64_CUDA.7z", "bytes": 11334115,
     "sha256": "6aac6b14ef478e501d2514cd1d74ed20e659b53b3bc2835d45a473ddfb921621",
     "dest": "openmvs-2.4.0-cuda", "find": "DensifyPointCloud.exe"},
]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


FFMPEG_ASSET = {"name": "ffmpeg",
                "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-20-13-08/"
                       "ffmpeg-n8.1.3-18-g46d899d7a0-win64-gpl-8.1.zip",
                "file": "ffmpeg-n8.1.3-18-g46d899d7a0-win64-gpl-8.1.zip", "bytes": 193047464,
                "sha256": "4101fe0a2f4a54039b125c4ca22ad1b8403ecc578383b2219c114a06ca599999",
                "dest": "ffmpeg-8.1", "find": "ffmpeg.exe"}


def safe_unzip(zpath, dest):
    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            t = (dest / n).resolve()
            if not str(t).startswith(str(dest.resolve())):
                raise RuntimeError(f"unsafe path in archive: {n}")
        z.extractall(dest)


def download(url, dst, size, attempts=8):
    """Download with resume (HTTP Range) after dropped connections; the SHA-256 check afterwards is the guard."""
    for i in range(attempts):
        have = dst.stat().st_size if dst.exists() else 0
        if have > size:
            dst.unlink()
            have = 0
        if have == size:
            return
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                resumed = have and r.status == 206
                with open(dst, "ab" if resumed else "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
        except (OSError, urllib.error.URLError) as e:
            got = dst.stat().st_size if dst.exists() else 0
            print(f"  interrupted at {got / 2**20:.0f} MB ({e.__class__.__name__}); retry {i + 1}/{attempts}", flush=True)
            time.sleep(min(30, 2 ** i))
    if not dst.exists() or dst.stat().st_size != size:
        raise RuntimeError(f"download of {url} incomplete after {attempts} attempts; run the script again to resume")


def safe_un7z(apath, dest):
    """.7z via Windows' own bsdtar (libarchive); member paths are checked before extracting."""
    tar = Path(r"C:\Windows\System32\tar.exe")
    if not tar.exists():
        raise RuntimeError("cannot unpack .7z: C:\\Windows\\System32\\tar.exe (bsdtar) not found; unpack it manually")
    names = subprocess.run([str(tar), "-tf", str(apath)], capture_output=True, text=True, check=True).stdout.splitlines()
    for n in names:
        t = (dest / n).resolve()
        if not str(t).startswith(str(dest.resolve())):
            raise RuntimeError(f"unsafe path in archive: {n}")
    subprocess.run([str(tar), "-xf", str(apath), "-C", str(dest)], check=True)


def _target(asset):
    """Installed marker: a fixed path, or (CUDA assets, whose inner layout varies) the first match of `find`."""
    if "check" in asset:
        p = TOOLS / asset["check"]
        return p if p.exists() else None
    d = TOOLS / asset["dest"]
    return next(iter(sorted(d.rglob(asset["find"]))), None) if d.exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--cuda", action="store_true", help="also install the NVIDIA CUDA builds")
    ap.add_argument("--ffmpeg", action="store_true", help="also install a portable FFmpeg (video input)")
    a = ap.parse_args()
    if sys.platform != "win32" and not a.check:
        sys.exit("Automatic setup is for Windows. On Linux / macOS install COLMAP and OpenMVS and set "
                 "NAMMA_COLMAP / NAMMA_OPENMVS_DIR (docs/INSTALL.md).")
    ok = True
    for asset in ASSETS + (CUDA_ASSETS if a.cuda or a.check else []) + ([FFMPEG_ASSET] if a.ffmpeg or a.check else []):
        target = _target(asset)
        if target is not None:
            print(f"{asset['name']}: installed ({target.relative_to(ROOT)})")
            continue
        if a.check:
            optional = asset in CUDA_ASSETS or asset is FFMPEG_ASSET
            flag = "--ffmpeg" if asset is FFMPEG_ASSET else "--cuda"
            print(f"{asset['name']}: {f'not installed (optional, {flag})' if optional else 'MISSING'}")
            ok &= optional
            continue
        dl = TOOLS / "downloads" / asset["file"]
        dl.parent.mkdir(parents=True, exist_ok=True)
        if not (dl.exists() and dl.stat().st_size == asset["bytes"]):
            print(f"{asset['name']}: downloading {asset['url']} ({asset['bytes'] / 2**20:.0f} MB)", flush=True)
            download(asset["url"], dl, asset["bytes"])
        digest = sha256(dl)
        if digest != asset["sha256"]:
            sys.exit(f"{asset['name']}: SHA-256 mismatch ({digest}); refusing to unpack")
        dest = TOOLS / asset["dest"]
        dest.mkdir(parents=True, exist_ok=True)
        (safe_un7z if dl.suffix == ".7z" else safe_unzip)(dl, dest)
        if "check" in asset and _target(asset) is None:
            # some archives contain one top-level folder; flatten it
            inner = [p for p in dest.iterdir() if p.is_dir()]
            if len(inner) == 1 and (inner[0] / Path(asset["check"]).relative_to(asset["dest"])).exists():
                for p in inner[0].iterdir():
                    shutil.move(str(p), str(dest / p.name))
                inner[0].rmdir()
        target = _target(asset)
        print(f"{asset['name']}: {'installed (' + str(target.relative_to(ROOT)) + ')' if target else 'UNPACKED BUT NOT FOUND - check ' + str(dest)}")
        ok &= target is not None
    if not (a.ffmpeg or a.check):
        ff = _target(FFMPEG_ASSET) or shutil.which("ffmpeg")
        print(f"ffmpeg: {'found at ' + str(ff) if ff else 'not found (needed for video input: --ffmpeg)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
