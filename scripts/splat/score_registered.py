"""Score a digest-bound observation batch in the Studio CPU environment."""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.core.jsonio import load_strict, sha256_file, write_json

if __package__:
    from .eval_views import raw_metrics
else:
    from eval_views import raw_metrics


def score_registered(registration, renders, output):
    batch = load_strict(renders / "renders.json")
    role = batch["role"]
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
    for frame in sorted(ids):
        if not frame.isascii() or not frame.isdigit():
            raise ValueError("invalid Polycam frame ID")
        render = renders / f"{frame}.png"
        photo = registration / "images" / f"{frame}.jpg"
        if sha256_file(render) != batch["image_sha256"][frame] or sha256_file(photo) != reg["image_sha256"][frame]:
            raise ValueError("observation or render digest mismatch")
        with Image.open(render) as pred, Image.open(photo) as observed:
            rows[frame] = raw_metrics(np.asarray(pred.convert("RGB")), np.asarray(observed.convert("RGB")))
    pixels = sum(r["pixels"] for r in rows.values())
    if not pixels:
        raise ValueError("no independently registered observations")
    mse = sum(r["pixels"] * r["mse"] for r in rows.values()) / pixels
    report = {"role": role, "renders_sha256": sha256_file(renders / "renders.json"),
              "registration_sha256": batch["registration_sha256"],
              "checkpoint_sha256": batch["checkpoint_sha256"], "frames": rows,
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
    args = parser.parse_args()
    report = score_registered(args.registration, args.renders, args.output)
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
