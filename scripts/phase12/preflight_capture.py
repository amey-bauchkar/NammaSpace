"""Preflight for a real iPhone room capture (run BEFORE any reconstruction).

    python scripts/phase12/preflight_capture.py capture_raw/<run_id> [--no-markers]

Checks (report only; nothing is modified):
- capture_plan.json present; ingestion -> IF-01 manifest with SHA-256 per file (Phase 11 ingest);
- photo format: JPEG expected (HEIC means "Most Compatible" was not set);
- one camera: all photos share resolution / EXIF model / focal length (one lens, 1x);
- sharpness: variance of the Laplacian on a 1000-px copy; the lowest 10 % are listed;
- markers: ArUco ids 0-5 detected in >= 3 photos each (needed for metric registration);
- held-out folder separate; measurements folder present.
Writes <run_dir>/../<run_id>.preflight.json and <run_id>.capture_manifest.if01.json.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import ExifTags, Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.core.jsonio import write_json  # noqa: E402
from namma.pipeline.ingest import ingest  # noqa: E402
from namma.recon.markers import detect  # noqa: E402

TAGS = {v: k for k, v in ExifTags.TAGS.items()}


def exif_summary(path):
    with Image.open(path) as im:
        ex = im.getexif()
        sub = ex.get_ifd(0x8769) if hasattr(ex, "get_ifd") else {}
        return {"size": im.size, "make": ex.get(TAGS["Make"]), "model": ex.get(TAGS["Model"]),
                "focal_mm": float(sub.get(TAGS["FocalLength"], 0) or 0),
                "focal_35mm": sub.get(TAGS["FocalLengthIn35mmFilm"])}


def sharpness(path):
    g = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    s = 1000 / max(g.shape)
    g = cv2.resize(g, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--no-markers", action="store_true")
    a = ap.parse_args()
    run = Path(a.run_dir)
    manifest, report = ingest(run, ignore_dirs=("measurements",))
    pre = {"run_dir": str(run), "ingest_ok": report.get("ok"), "ingest_issues": report.get("issues"),
           "coverage": report.get("coverage"), "checks": {}, "problems": []}
    photos = [run / f["path"] for f in (manifest or {}).get("files", []) if f["kind"] == "image_set"]
    heic = [p for p in run.rglob("*") if p.suffix.lower() in (".heic", ".heif")]
    if heic:
        pre["problems"].append(f"{len(heic)} HEIC files: set Settings > Camera > Formats > Most Compatible and recapture, "
                               "or convert losslessly before use")
    main_photos = [p for p in photos if "heldout" not in p.parent.name.lower()]
    held = [p for p in photos if "heldout" in p.parent.name.lower()]
    pre["checks"]["photos_model"] = len(main_photos)
    pre["checks"]["photos_heldout"] = len(held)
    if len(main_photos) < 40:
        pre["problems"].append(f"only {len(main_photos)} model photos (initial setting 80-120 for a 4 x 4 m room)")
    if not held:
        pre["problems"].append("no held-out photos (R01_heldout) - held-out checks will be NOT MEASURABLE")
    ex = [exif_summary(p) for p in main_photos]
    cams = Counter((e["size"], e["model"], e["focal_mm"]) for e in ex)
    pre["checks"]["camera_groups"] = [{"size": list(k[0]), "model": k[1], "focal_mm": k[2], "photos": n}
                                      for k, n in cams.most_common()]
    if len(cams) > 1:
        pre["problems"].append("photos come from more than one camera / lens / resolution (use the 1x lens only)")
    sharp = sorted((sharpness(p), p.name) for p in main_photos)
    pre["checks"]["sharpness_lowest_10pct"] = [{"photo": n, "laplacian_var": round(v, 1)}
                                               for v, n in sharp[:max(1, len(sharp) // 10)]]
    if not a.no_markers:
        seen = Counter()
        for p in main_photos:
            for mid in detect(p):
                seen[mid] += 1
        pre["checks"]["marker_photo_counts"] = {f"M{i}": seen.get(i, 0) for i in range(6)}
        for i in range(6):
            if seen.get(i, 0) < 3:
                pre["problems"].append(f"marker M{i} seen in {seen.get(i, 0)} photos (need >= 3)")
    if not (run / "measurements").is_dir():
        pre["problems"].append("measurements/ folder missing - the experiment needs independent tape measurements")
    pre["verdict"] = "READY" if (report.get("ok") and not pre["problems"]) else "NOT READY"
    write_json(run.parent / f"{run.name}.preflight.json", pre)
    if manifest:
        write_json(run.parent / f"{run.name}.capture_manifest.if01.json", manifest)
    print(json.dumps({k: pre[k] for k in ("verdict", "problems", "checks")}, indent=1, default=str))


if __name__ == "__main__":
    main()
