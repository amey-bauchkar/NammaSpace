"""Score a digest-bound observation batch in the Studio CPU environment."""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.core.jsonio import load_strict, sha256_file, write_json
from namma.capture.polycam_raw import opencv_c2w

if __package__:
    from .eval_views import raw_metrics
else:
    from eval_views import raw_metrics


def projected_region(center, extent, pose, camera):
    """Project a fixed world-space cube into an independently registered camera."""
    center = np.asarray(center, dtype=float)
    if center.shape != (3,) or not np.isfinite(center).all() or not np.isfinite(extent) or extent <= 0:
        raise ValueError("invalid scoring region")
    corners = center + extent * (np.indices((2, 2, 2)).reshape(3, -1).T - .5)
    view = np.linalg.inv(opencv_c2w(pose))
    xyz = corners @ view[:3, :3].T + view[:3, 3]
    width, height = camera["width"], camera["height"]
    mask = np.zeros((height, width), np.uint8)
    if np.any(xyz[:, 2] <= 0):
        return mask
    pixels = xyz[:, :2] / xyz[:, 2:3]
    pixels = pixels * [camera["fx"], camera["fy"]] + [camera["cx"], camera["cy"]]
    if not np.isfinite(pixels).all() or np.max(np.abs(pixels)) > 2**24:
        raise ValueError("invalid region projection")
    hull = cv2.convexHull(np.rint(pixels).astype(np.int32))
    if cv2.contourArea(hull) > 0:
        cv2.fillConvexPoly(mask, hull, 1)
    return mask


def score_registered(registration, renders, output, *, region=None):
    batch = load_strict(renders / "renders.json")
    role = batch["role"]
    if region is not None and role != "dev":
        raise ValueError("experimental region scoring requires development observations")
    if role not in {"dev", "final_test"} or batch["pose_fit"] is not False or batch["colour_fit"] is not False:
        raise ValueError("expected a raw development or final-test render batch")
    reg_path = registration / f"{role}_registration.json"
    reg = load_strict(reg_path)
    ids = set(batch["registered"])
    if (sha256_file(reg_path) != batch["registration_sha256"] or reg["role"] != role
            or ids != set(reg["poses_arkit"]) or ids != set(batch["image_sha256"])
            or set(batch["unregistered"]) != set(reg["unregistered"])):
        raise ValueError("render batch does not match registration")
    if role == "final_test":
        claim_path = registration / "final_evaluation_claim.json"
        claim = load_strict(claim_path)
        if (sha256_file(claim_path) != batch["claim_sha256"] or claim["candidate"] != batch["checkpoint_sha256"]
                or claim["registration_sha256"] != batch["registration_sha256"]):
            raise ValueError("render batch does not match final claim")
    rows = {}
    outside_region = []
    for frame in sorted(ids):
        if not frame.isascii() or not frame.isdigit():
            raise ValueError("invalid Polycam frame ID")
        render = renders / f"{frame}.png"
        photo = registration / "images" / f"{frame}.jpg"
        if sha256_file(render) != batch["image_sha256"][frame] or sha256_file(photo) != reg["image_sha256"][frame]:
            raise ValueError("observation or render digest mismatch")
        with Image.open(render) as pred, Image.open(photo) as observed:
            mask = None
            if region is not None:
                mask = projected_region(region["center"], region["extent_m"], reg["poses_arkit"][frame],
                                        reg["intrinsics"][frame])
                if pred.size != observed.size or pred.size != mask.shape[::-1]:
                    raise ValueError("region projection does not match image dimensions")
                x, y, w, h = cv2.boundingRect(mask)
                if not w or not h:
                    outside_region.append(frame)
                    continue
                # The 11-pixel SSIM window needs five context pixels on each side.
                x0, y0 = max(0, x-5), max(0, y-5)
                x1, y1 = min(pred.width, x+w+5), min(pred.height, y+h+5)
                box = (x0, y0, x1, y1)
                pred, observed = pred.crop(box), observed.crop(box)
                mask = mask[y0:y1, x0:x1]
            rows[frame] = raw_metrics(np.asarray(pred.convert("RGB")), np.asarray(observed.convert("RGB")), mask)
    pixels = sum(r["pixels"] for r in rows.values())
    if not pixels:
        raise ValueError("no independently registered observations")
    mse = sum(r["pixels"] * r["mse"] for r in rows.values()) / pixels
    report = {"role": role, "renders_sha256": sha256_file(renders / "renders.json"),
              "registration_sha256": batch["registration_sha256"],
              "checkpoint_sha256": batch["checkpoint_sha256"], "frames": rows,
              "region": region, "outside_region": outside_region,
              "model_sha256": batch.get("model_sha256"), "edit_sha256": batch.get("edit_sha256"),
              "unregistered": batch["unregistered"], "acceptance_evaluated": False,
              "aggregate": {"pixels": pixels, "mse": mse, "perfect_match": mse == 0,
                            "raw_psnr_db": float(-10*np.log10(mse)) if mse else None,
                            "raw_ssim": sum(r["pixels"] * r["raw_ssim"] for r in rows.values()) / pixels}}
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "raw_metrics.json", report)
    return report


def compare_development(baseline, candidate):
    """Compare raw development scores on exactly the same observations."""
    if (baseline["role"] != "dev" or candidate["role"] != "dev"
            or baseline["registration_sha256"] != candidate["registration_sha256"]
            or baseline["checkpoint_sha256"] != candidate["checkpoint_sha256"]
            or baseline.get("region") != candidate.get("region")
            or baseline["frames"].keys() != candidate["frames"].keys()):
        raise ValueError("development scores refer to different observations or parents")
    for frame, scores in baseline["frames"].items():
        if scores["pixels"] != candidate["frames"][frame]["pixels"]:
            raise ValueError("development scores cover different pixel counts")
    a, b = baseline["aggregate"], candidate["aggregate"]
    return {"observations": len(baseline["frames"]),
            "delta_mse": b["mse"] - a["mse"],
            "delta_ssim": b["raw_ssim"] - a["raw_ssim"],
            "delta_psnr_db": (b["raw_psnr_db"] - a["raw_psnr_db"]
                              if None not in (a["raw_psnr_db"], b["raw_psnr_db"]) else None)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("registration", "renders", "output"):
        parser.add_argument(field, type=Path)
    parser.add_argument("--compare-to", type=Path, help="baseline development raw_metrics.json")
    parser.add_argument("--region-ledger", type=Path, help="use this development edit's fixed scoring cube")
    args = parser.parse_args()
    region = None
    if args.region_ledger:
        ledger = load_strict(args.region_ledger)
        region = {key: ledger[key] for key in ("center", "extent_m")}
    report = score_registered(args.registration, args.renders, args.output, region=region)
    if args.compare_to:
        baseline = load_strict(args.compare_to)
        comparison = compare_development(baseline, report)
        write_json(args.output / "comparison.json", {
            **comparison, "baseline_score_sha256": sha256_file(args.compare_to),
            "candidate_score_sha256": sha256_file(args.output / "raw_metrics.json"),
        })
        print(comparison)
    else:
        print(report["aggregate"])
