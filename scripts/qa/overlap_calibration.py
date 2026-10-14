"""Calibrate the capture check's overlap predictor against real SfM placement (pre-capture QA, QA-05).

    python scripts/qa/overlap_calibration.py

For each photo scene: the overlap graph is built with the current setting (ORB 640 px / 1200 / >= 30 verified) and
with candidate settings; the images it calls ISOLATED (no verified neighbour) are compared with the images COLMAP
actually failed to place in the evaluated runs. Development / QA scenes only; nothing here changes a run.
"""
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from namma.capture import checks  # noqa: E402

SCENES = [  # (name, image folder, sfm_summary of an evaluated run)
    ("eth3d_kicker", "external_data/eth3d_kicker/extracted/kicker/images/dslr_images",
     "phase13_artifacts/projects/eth3d-kicker-dev-20261011-14250f/runs/run-20261011-181544-5aef/20_sfm/sfm_summary.json"),
    ("eth3d_pipes", "external_data/eth3d_pipes/extracted/pipes/images/dslr_images",
     "phase13_artifacts/projects/eth3d-pipes-dev-20261011-1d30cd/runs/run-20261011-014644-b2fc/20_sfm/sfm_summary.json"),
    ("eth3d_office", "external_data/eth3d_office/extracted/office/images/dslr_images",
     "phase13_artifacts/projects/eth3d-office-held-out-20261011-247444/runs/run-20261011-191403-d6a7/20_sfm/sfm_summary.json"),
    ("eth3d_delivery_area", "external_data/eth3d_delivery_area/extracted/delivery_area/images/dslr_images",
     "phase13_artifacts/projects/eth3d-delivery-area-held-out-20261011-ed60c5/runs/run-20261011-200044-2d2d/20_sfm/sfm_summary.json"),
    ("tum_sample", "samples/tum_fr3_office/images",
     "phase13_artifacts/projects/tum-office-sample-final-20261012-dcf3c6/runs/run-20261012-000312-03f1/20_sfm/sfm_summary.json"),
]
SETTINGS = {
    "current_orb640": {"det": "orb", "side": 640, "n": 1200, "ratio": 0.75, "min": 30},
    "sift1024_min20": {"det": "sift", "side": 1024, "n": 2000, "ratio": 0.8, "min": 20},
    "sift1024_min15": {"det": "sift", "side": 1024, "n": 2000, "ratio": 0.8, "min": 15},
}


def feats(path, s):
    g = checks.load_grey(path, s["side"])
    det = cv2.ORB_create(s["n"]) if s["det"] == "orb" else cv2.SIFT_create(s["n"])
    kps, desc = det.detectAndCompute(g, None)
    pts = np.array([k.pt for k in kps], np.float32) if kps else np.zeros((0, 2), np.float32)
    thumb = cv2.resize(checks.load_grey(path, 640), (16, 16), interpolation=cv2.INTER_AREA).astype(np.float32).ravel()
    return {"desc": desc, "pts": pts, "thumb": (thumb - thumb.mean()) / (thumb.std() + 1e-6)}


def verified(a, b, s):
    if a["desc"] is None or b["desc"] is None or len(a["desc"]) < 8 or len(b["desc"]) < 8:
        return 0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING if s["det"] == "orb" else cv2.NORM_L2)
    knn = bf.knnMatch(a["desc"], b["desc"], k=2)
    good = [m[0] for m in knn if len(m) == 2 and m[0].distance < s["ratio"] * m[1].distance]
    if len(good) < 15:
        return 0
    _, mask = cv2.findFundamentalMat(a["pts"][[m.queryIdx for m in good]], b["pts"][[m.trainIdx for m in good]],
                                     cv2.FM_RANSAC, 2.0 * s["side"] / 640, 0.99)
    return int(mask.sum()) if mask is not None else 0


def main():
    out = []
    for name, folder, summ in SCENES:
        files = sorted(p for p in (ROOT / folder).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        unplaced = {Path(x).stem.lower() for x in json.loads((ROOT / summ).read_text())["unregistered"]}
        for key, s in SETTINGS.items():
            t0 = time.time()
            F = [feats(f, s) for f in files]
            pairs = checks.candidate_pairs(len(F), [x["thumb"] for x in F], ordered=False)
            deg = [0] * len(F)
            for i, j in pairs:
                if verified(F[i], F[j], s) >= s["min"]:
                    deg[i] += 1; deg[j] += 1
            iso = {files[i].stem.lower() for i in range(len(F)) if deg[i] == 0}
            row = {"scene": name, "setting": key, "images": len(files), "predicted_isolated": len(iso),
                   "actually_unplaced": len(unplaced), "false_alarms": len(iso - unplaced), "missed": len(unplaced - iso),
                   "seconds": round(time.time() - t0, 1)}
            out.append(row)
            print(json.dumps(row), flush=True)
    (ROOT / "precapture_qa" / "overlap_calibration.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
