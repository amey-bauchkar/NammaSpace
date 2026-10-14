"""Download, verify and extract registered public datasets (datasets/registry.json).

    python scripts/datasets/fetch_datasets.py --list
    python scripts/datasets/fetch_datasets.py --scene eth3d_pipes [--scene ...] [--extract] [--dry-run]

- Only URLs listed in the registry are fetched (no crawling, no credentials).
- Files go to external_data/<scene_id>/<path>; a completed file whose size matches is never re-downloaded.
- Partial downloads resume with HTTP Range (<name>.part).
- Size must equal the registry byte count; SHA-256 is computed and must match when the registry has one.
- Every result is appended to external_data/downloads.jsonl.
- --extract unpacks archives into external_data/<scene_id>/extracted/ with safe extractors
  (7z via the Windows bsdtar, tar via tarfile filter="data", zip with path checks). Nothing extracted is executed.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "datasets" / "registry.json"
DATA = ROOT / "external_data"
LOG = DATA / "downloads.jsonl"
CHUNK = 1 << 20
UA = "NammaSpace-dataset-fetch/1.0 (research; registry-listed files only)"


def load_registry():
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    scenes = {}
    for ds in reg["datasets"]:
        for sc in ds["scenes"]:
            scenes[sc["scene_id"]] = {**sc, "dataset_id": ds["dataset_id"], "license": ds["license"]}
    return reg, scenes


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def log(entry):
    DATA.mkdir(parents=True, exist_ok=True)
    entry["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def download(url, dest, expected_bytes, retries=4):
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    t0 = time.time()
    for attempt in range(1, retries + 1):
        have = part.stat().st_size if part.exists() else 0
        if have > expected_bytes:
            part.unlink()
            have = 0
        if have == expected_bytes:
            break
        req = urllib.request.Request(url, headers={"User-Agent": UA, **({"Range": f"bytes={have}-"} if have else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                if have and resp.status != 206:      # server ignored Range: start again
                    have = 0
                    part.unlink(missing_ok=True)
                with open(part, "ab") as out:
                    last = time.time()
                    while True:
                        block = resp.read(CHUNK)
                        if not block:
                            break
                        out.write(block)
                        if time.time() - last > 15:
                            done = part.stat().st_size
                            print(f"    {dest.name}: {done / 2**20:.0f} / {expected_bytes / 2**20:.0f} MB", flush=True)
                            last = time.time()
        except OSError as e:
            print(f"    attempt {attempt} failed: {e}", flush=True)
            time.sleep(3 * attempt)
    size = part.stat().st_size if part.exists() else 0
    if size != expected_bytes:
        raise RuntimeError(f"{url}: got {size} bytes, registry says {expected_bytes}")
    os.replace(part, dest)
    return time.time() - t0


def bsdtar():
    p = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    return str(p) if p.exists() else shutil.which("bsdtar")


def safe_zip_extract(src, out):
    with zipfile.ZipFile(src) as z:
        for name in z.namelist():
            target = (out / name).resolve()
            if not str(target).startswith(str(out.resolve())):
                raise RuntimeError(f"{src}: unsafe member path {name}")
        z.extractall(out)


def extract(src, out):
    out.mkdir(parents=True, exist_ok=True)
    marker = out / f".extracted-{src.name}"
    if marker.exists():
        return "already extracted"
    name = src.name.lower()
    if name.endswith(".7z"):
        tool = bsdtar()
        if not tool:
            raise RuntimeError("no bsdtar available to extract .7z (Windows 10+ ships C:\\Windows\\System32\\tar.exe)")
        subprocess.run([tool, "-xf", str(src), "-C", str(out)], check=True)
    elif name.endswith((".tgz", ".tar.gz", ".tar")):
        with tarfile.open(src) as t:
            t.extractall(out, filter="data")
    elif name.endswith(".zip"):
        safe_zip_extract(src, out)
    else:
        return "not an archive"
    marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z"), encoding="utf-8")
    return "extracted"


def fetch_scene(scene, do_extract, dry_run):
    base = DATA / scene["scene_id"]
    total = sum(f["bytes"] for f in scene["files"])
    print(f"{scene['scene_id']} ({scene['role']}, {scene['license']}): {len(scene['files'])} files, {total / 2**20:.1f} MB")
    for f in scene["files"]:
        dest = base / f["path"]
        if dry_run:
            print(f"  would fetch {f['url']} -> {dest.relative_to(ROOT)} ({f['bytes']} bytes)")
            continue
        if dest.exists() and dest.stat().st_size == f["bytes"]:
            status, seconds = "present", 0.0
        else:
            print(f"  fetching {f['url']}", flush=True)
            seconds = download(f["url"], dest, f["bytes"])
            status = "downloaded"
        digest = sha256_of(dest)
        if f.get("sha256") and f["sha256"] != digest:
            log({"scene_id": scene["scene_id"], "url": f["url"], "status": "sha256_mismatch", "sha256": digest})
            raise RuntimeError(f"{dest}: SHA-256 {digest} != registry {f['sha256']}")
        log({"scene_id": scene["scene_id"], "url": f["url"], "path": str(dest.relative_to(ROOT)).replace("\\", "/"),
             "bytes": dest.stat().st_size, "sha256": digest, "status": status, "seconds": round(seconds, 1)})
        print(f"  {status}: {dest.name} sha256={digest[:16]}...", flush=True)
        if do_extract:
            print(f"    {extract(dest, base / 'extracted')}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", action="append", default=[])
    ap.add_argument("--extract", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    _, scenes = load_registry()
    if a.list or not a.scene:
        for sid, sc in scenes.items():
            print(f"{sid:32s} {sc['role']:14s} {sum(f['bytes'] for f in sc['files']) / 2**20:9.1f} MB  {sc['license']}")
        return 0
    free = shutil.disk_usage(ROOT).free
    need = sum(f["bytes"] for s in a.scene for f in scenes[s]["files"]) * 3   # archive + extracted + margin
    if need > free:
        sys.exit(f"not enough disk: need about {need / 2**30:.1f} GB, free {free / 2**30:.1f} GB")
    for sid in a.scene:
        if sid not in scenes:
            sys.exit(f"unknown scene {sid}; see --list")
        fetch_scene(scenes[sid], a.extract, a.dry_run)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
