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


def score_registered(registration, renders, output, *, perceptual_approval=None):
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
    perceptual = None
    if perceptual_approval:
        from namma.afe.perceptual import PerceptualMetric
        perceptual = PerceptualMetric(perceptual_approval)
    output.mkdir(parents=True, exist_ok=False)
    from namma.afe.metrics import image_structure
    for frame in sorted(ids):
        if not frame.isascii() or not frame.isdigit():
            raise ValueError("invalid Polycam frame ID")
        render = renders / f"{frame}.png"
        photo = registration / "images" / f"{frame}.jpg"
        if sha256_file(render) != batch["image_sha256"][frame] or sha256_file(photo) != reg["image_sha256"][frame]:
            raise ValueError("observation or render digest mismatch")
        with Image.open(render) as pred, Image.open(photo) as observed:
            x,y = np.asarray(pred.convert("RGB")),np.asarray(observed.convert("RGB"))
            rows[frame] = {**raw_metrics(x,y),**image_structure(x,y)}
            if perceptual:
                result = perceptual.compare(x,y)
                rows[frame]["lpips_alex"] = result["lpips_alex"]
                rows[frame]["lpips_approval_sha256"] = result["approval_sha256"]
                if role=="dev":
                    defect_map = output/f"{frame}.lpips.npy"
                    np.save(defect_map,result["map"],allow_pickle=False)
                    rows[frame]["lpips_map_sha256"] = sha256_file(defect_map)
    pixels = sum(r["pixels"] for r in rows.values())
    if not pixels:
        raise ValueError("no independently registered observations")
    mse = sum(r["pixels"] * r["mse"] for r in rows.values()) / pixels
    report = {"role": role, "renders_sha256": sha256_file(renders / "renders.json"),
              "checkpoint_sha256": batch["checkpoint_sha256"], "frames": rows,
              "unregistered": batch["unregistered"], "acceptance_evaluated": False,
              "aggregate": {"pixels": pixels, "mse": mse, "perfect_match": mse == 0,
                            "raw_psnr_db": float(-10*np.log10(mse)) if mse else None,
                            "raw_ssim": sum(r["pixels"] * r["raw_ssim"] for r in rows.values()) / pixels}}
    write_json(output / "raw_metrics.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("registration", "renders", "output"):
        parser.add_argument(field, type=Path)
    parser.add_argument("--perceptual-approval",type=Path)
    args = parser.parse_args()
    print(score_registered(args.registration, args.renders, args.output,
                           perceptual_approval=args.perceptual_approval)["aggregate"])
