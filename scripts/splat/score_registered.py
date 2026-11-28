"""Score a digest-bound observation batch with optional offline LPIPS."""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.core.jsonio import load_strict, sha256_file, write_json
from namma.capture.polycam_raw import opencv_c2w
from namma.afe.screen import screen_development
from namma.afe.metrics import image_structure

if __package__:
    from .eval_views import boundary_metrics, depth_metrics, detail_metrics, raw_metrics, summarize_detail
    from .perceptual import PerceptualScorer
else:
    from eval_views import boundary_metrics, depth_metrics, detail_metrics, raw_metrics, summarize_detail
    from perceptual import PerceptualScorer


def projected_region(center, extent, pose, camera):
    """Project a fixed world-space cube into an independently registered camera."""
    center = np.asarray(center, dtype=float)
    extent = np.asarray(extent, dtype=float)
    if (center.shape != (3,) or extent.shape not in {(), (3,)} or not np.isfinite(center).all()
            or not np.isfinite(extent).all() or (extent <= 0).any()):
        raise ValueError("invalid scoring region")
    corners = center + extent * (np.indices((2, 2, 2)).reshape(3, -1).T - .5)
    view = np.linalg.inv(opencv_c2w(pose))
    xyz = corners @ view[:3, :3].T + view[:3, 3]
    width, height = camera["width"], camera["height"]
    mask = np.zeros((height, width), np.uint8)
    # Match the renderer's near plane. For a cube that crosses it, retain
    # the front vertices and clip its twelve edges before projection.
    near = .01
    front = xyz[:, 2] >= near
    if not front.any():
        return mask
    if not front.all():
        intersections = []
        for axis_bit in (1, 2, 4):
            for i in range(8):
                j = i ^ axis_bit
                if i < j and front[i] != front[j]:
                    a, b = xyz[i], xyz[j]
                    point = a + (b - a) * ((near - a[2]) / (b[2] - a[2]))
                    point[2] = near
                    intersections.append(point)
        xyz = np.vstack((xyz[front], intersections))
    pixels = xyz[:, :2] / xyz[:, 2:3]
    pixels = pixels * [camera["fx"], camera["fy"]] + [camera["cx"], camera["cy"]]
    if not np.isfinite(pixels).all() or np.max(np.abs(pixels)) > 2**24:
        raise ValueError("invalid region projection")
    hull = cv2.convexHull(np.rint(pixels).astype(np.int32))
    if cv2.contourArea(hull) > 0:
        cv2.fillConvexPoly(mask, hull, 1)
    return mask


def aggregate_depth(rows):
    totals = {key: sum(row[key] for row in rows.values()) for key in
              ("measured_pixels", "matched_pixels", "count_within_2cm", "count_within_5cm")}
    count = totals["measured_pixels"]
    if not count:
        return None
    matched = totals["matched_pixels"]
    mae = sum(row["abs_error_sum_m"] for row in rows.values()) / matched if matched else None
    rmse = (sum(row["squared_error_sum_m2"] for row in rows.values()) / matched)**.5 if matched else None
    return {**totals, "frames": len(rows), "coverage": totals["matched_pixels"] / count,
            "matched_mae_m": mae, "matched_rmse_m": rmse,
            "within_2cm": totals["count_within_2cm"] / count,
            "within_5cm": totals["count_within_5cm"] / count}


def score_registered(registration, renders, output, *, region=None, region_ledger=None,
                     integrity_baseline=None, detail=False, lpips_alexnet=None, lpips_device="cuda", perceptual_approval=None):
    if perceptual_approval and (region is not None or region_ledger is not None):
        raise ValueError("approved full-image perceptual maps require whole views")
    batch = load_strict(renders / "renders.json")
    role = batch["role"]
    region_ledger_hash = None
    if region_ledger is not None:
        if region is not None:
            raise ValueError("specify a scoring region or its ledger")
        ledger = load_strict(region_ledger)
        if "base_sha256" in ledger:
            lo = np.asarray(ledger["region"]["lower"], dtype=float)
            hi = np.asarray(ledger["region"]["upper"], dtype=float)
            if lo.shape != (3,) or hi.shape != (3,):
                raise ValueError("invalid regional ledger bounds")
            region = {"center": ((lo + hi) / 2).tolist(), "extent_m": (hi - lo).tolist()}
        else:
            region = {key: ledger[key] for key in ("center", "extent_m")}
        region_ledger_hash = sha256_file(region_ledger)
    if integrity_baseline is not None and (not region_ledger_hash or region_ledger_hash != batch.get("edit_sha256")):
        raise ValueError("outside-region integrity requires the rendered edit's own region ledger")
    if region is not None and role != "dev":
        raise ValueError("experimental region scoring requires development observations")
    if lpips_alexnet is not None and role != "dev":
        raise ValueError("experimental LPIPS scoring requires development observations")
    if role not in {"dev", "final_test"} or batch["pose_fit"] is not False or batch["colour_fit"] is not False:
        raise ValueError("expected a raw development or final-test render batch")
    reg_path = registration / f"{role}_registration.json"
    reg = load_strict(reg_path)
    ids = set(batch["registered"])
    if (sha256_file(reg_path) != batch["registration_sha256"] or reg["role"] != role
            or ids != set(reg["poses_arkit"]) or ids != set(batch["image_sha256"])
            or set(batch["unregistered"]) != set(reg["unregistered"])):
        raise ValueError("render batch does not match registration")
    baseline_batch = None
    if integrity_baseline is not None:
        baseline_manifest = integrity_baseline / "renders.json"
        baseline_batch = load_strict(baseline_manifest)
        if (region is None or role != "dev" or not batch.get("edit_sha256")
                or baseline_batch.get("edit_sha256") or baseline_batch.get("spz_roundtrip")
                or baseline_batch["role"] != role or baseline_batch["pose_fit"] is not False
                or baseline_batch["colour_fit"] is not False
                or baseline_batch["checkpoint_sha256"] != batch["checkpoint_sha256"]
                or baseline_batch.get("sh_degree") != batch.get("sh_degree")
                or baseline_batch["registration_sha256"] != batch["registration_sha256"]
                or baseline_batch["image_sha256"].keys() != batch["image_sha256"].keys()):
            raise ValueError("integrity check requires the matching unedited PLY development render")
    if role == "final_test":
        claim_path = registration / "final_evaluation_claim.json"
        claim = load_strict(claim_path)
        if (sha256_file(claim_path) != batch["claim_sha256"] or claim["candidate"] != batch["checkpoint_sha256"]
                or claim["registration_sha256"] != batch["registration_sha256"]):
            raise ValueError("render batch does not match final claim")
    rows = {}
    outside_region = []
    measured_depth = reg.get("measured_depth", {})
    depth_hashes = batch.get("depth_sha256", {})
    if depth_hashes.keys() != measured_depth.keys() or not depth_hashes.keys() <= ids:
        raise ValueError("rendered depth inventory does not match registration")
    depth_rows = {}
    integrity_rows = {}
    detail_rows = {}
    boundary_rows = {}
    perceptual = PerceptualScorer(lpips_alexnet, lpips_device) if lpips_alexnet is not None else None
    perceptual_rows = {}
    approved_perceptual = None
    if perceptual_approval:
        from namma.afe.perceptual import PerceptualMetric
        approved_perceptual = PerceptualMetric(perceptual_approval)
        output.mkdir(parents=True, exist_ok=False)
    for frame in sorted(ids):
        if not frame.isascii() or not frame.isdigit():
            raise ValueError("invalid Polycam frame ID")
        render = renders / f"{frame}.png"
        photo = registration / "images" / f"{frame}.jpg"
        if sha256_file(render) != batch["image_sha256"][frame] or sha256_file(photo) != reg["image_sha256"][frame]:
            raise ValueError("observation or render digest mismatch")
        if frame in measured_depth:
            measured = registration / "measured_depth" / f"{frame}.npy"
            predicted = renders / f"{frame}.depth.npy"
            if (sha256_file(measured) != measured_depth[frame]["sha256"]
                    or sha256_file(predicted) != depth_hashes[frame]):
                raise ValueError("depth grid digest mismatch")
            depth_rows[frame] = depth_metrics(np.load(predicted, allow_pickle=False),
                                              np.load(measured, allow_pickle=False))
        with Image.open(render) as pred, Image.open(photo) as observed:
            mask = None
            if region is not None:
                mask = projected_region(region["center"], region["extent_m"], reg["poses_arkit"][frame],
                                        reg["intrinsics"][frame])
                if pred.size != observed.size or pred.size != mask.shape[::-1]:
                    raise ValueError("region projection does not match image dimensions")
                if baseline_batch is not None:
                    baseline_image = integrity_baseline / f"{frame}.png"
                    if sha256_file(baseline_image) != baseline_batch["image_sha256"][frame]:
                        raise ValueError("integrity baseline render digest mismatch")
                    with Image.open(baseline_image) as before:
                        if before.size != pred.size:
                            raise ValueError("integrity baseline dimensions differ")
                        changed = np.any(np.asarray(before.convert("RGB")) != np.asarray(pred.convert("RGB")), axis=2)
                    outside = mask == 0
                    integrity_rows[frame] = {"outside_pixels": int(outside.sum()),
                                             "changed_outside_pixels": int(np.count_nonzero(changed & outside))}
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
            pred_rgb, obs_rgb = np.asarray(pred.convert("RGB")), np.asarray(observed.convert("RGB"))
            rows[frame] = raw_metrics(pred_rgb, obs_rgb, mask)
            if region is None:
                rows[frame].update(image_structure(pred_rgb, obs_rgb))
            if approved_perceptual:
                result = approved_perceptual.compare(pred_rgb, obs_rgb)
                rows[frame].update(lpips_alex=result["lpips_alex"], lpips_approval_sha256=result["approval_sha256"])
                if role == "dev":
                    defect_map = output / f"{frame}.lpips.npy"
                    np.save(defect_map, result["map"], allow_pickle=False)
                    rows[frame]["lpips_map_sha256"] = sha256_file(defect_map)
            if perceptual is not None:
                perceptual_rows[frame] = perceptual.score(pred_rgb, obs_rgb, mask)
            if detail:
                detail_rows[frame] = summarize_detail([detail_metrics(pred_rgb, obs_rgb, mask)])
                if mask is not None:
                    boundary_rows[frame] = boundary_metrics(pred_rgb, obs_rgb, mask)
    pixels = sum(r["pixels"] for r in rows.values())
    if not pixels:
        raise ValueError("no independently registered observations")
    mse = sum(r["pixels"] * r["mse"] for r in rows.values()) / pixels
    report = {"role": role, "renders_sha256": sha256_file(renders / "renders.json"),
              "registration_sha256": batch["registration_sha256"],
              "checkpoint_sha256": batch["checkpoint_sha256"], "frames": rows,
              "training_reference_sha256": batch.get("training_reference_sha256"),
              "region": region, "outside_region": outside_region,
              "region_ledger_sha256": region_ledger_hash,
              "measured_depth_metrics": depth_rows, "depth_min_alpha": batch.get("depth_min_alpha"),
              "measured_depth_aggregate": aggregate_depth(depth_rows),
              "model_sha256": batch.get("model_sha256"), "edit_sha256": batch.get("edit_sha256"),
              "unregistered": batch["unregistered"], "acceptance_evaluated": False,
              "aggregate": {"pixels": pixels, "mse": mse, "perfect_match": mse == 0,
                            "raw_psnr_db": float(-10*np.log10(mse)) if mse else None,
                            "raw_ssim": sum(r["pixels"] * r["raw_ssim"] for r in rows.values()) / pixels}}
    if baseline_batch is not None:
        outside_pixels = sum(row["outside_pixels"] for row in integrity_rows.values())
        changed_pixels = sum(row["changed_outside_pixels"] for row in integrity_rows.values())
        report["outside_region_integrity"] = {
            "baseline_renders_sha256": sha256_file(baseline_manifest), "frames": integrity_rows,
            "outside_pixels": outside_pixels, "changed_outside_pixels": changed_pixels,
            "pixel_equal": changed_pixels == 0 if outside_pixels else None,
        }
    if detail:
        report["detail_metrics"] = {"method": "raw_luma_canny60_120_l2_laplacian_v1",
                                    "frames": detail_rows, "aggregate": summarize_detail(list(detail_rows.values()))}
        if region is not None:
            pairs = sum(row["pairs"] for row in boundary_rows.values())
            error = sum(row["abs_sum_uint8"] for row in boundary_rows.values())
            report["boundary_metrics"] = {"method": "raw_rgb_4_neighbor_step_residual_v1", "frames": boundary_rows,
                                          "pairs": pairs,
                                          "mean_rgb_step_error": error / (3 * 255 * pairs) if pairs else None}
    if perceptual is not None:
        count = sum(row["patches"] for row in perceptual_rows.values())
        total = sum(row["patches"] * row["mean"] for row in perceptual_rows.values() if row["patches"])
        report["lpips"] = {"protocol": perceptual.protocol, "frames": perceptual_rows,
                           "patches": count, "mean": total / count if count else None}
    if approved_perceptual is None:
        output.mkdir(parents=True, exist_ok=False)
    write_json(output / "raw_metrics.json", report)
    return report


def compare_development(baseline, candidate, *, training_variants=False):
    """Compare raw development scores on exactly the same observations."""
    if training_variants:
        reference = baseline.get("training_reference_sha256")
        same_model_source = bool(reference and reference == candidate.get("training_reference_sha256")
                                 and not baseline.get("edit_sha256") and not candidate.get("edit_sha256"))
    else:
        same_model_source = baseline["checkpoint_sha256"] == candidate["checkpoint_sha256"]
    if (baseline["role"] != "dev" or candidate["role"] != "dev"
            or baseline["registration_sha256"] != candidate["registration_sha256"]
            or not same_model_source
            or baseline.get("region") != candidate.get("region")
            or baseline["frames"].keys() != candidate["frames"].keys()):
        raise ValueError("development scores refer to different observations or parents")
    for frame, scores in baseline["frames"].items():
        if scores["pixels"] != candidate["frames"][frame]["pixels"]:
            raise ValueError("development scores cover different pixel counts")
    a, b = baseline["aggregate"], candidate["aggregate"]
    result = {"observations": len(baseline["frames"]),
            "delta_mse": b["mse"] - a["mse"],
            "delta_ssim": b["raw_ssim"] - a["raw_ssim"],
            "delta_psnr_db": (b["raw_psnr_db"] - a["raw_psnr_db"]
                              if None not in (a["raw_psnr_db"], b["raw_psnr_db"]) else None)}
    depth_a = baseline.get("measured_depth_aggregate")
    depth_b = candidate.get("measured_depth_aggregate")
    if depth_a is not None or depth_b is not None:
        depths_a = baseline.get("measured_depth_metrics", {})
        depths_b = candidate.get("measured_depth_metrics", {})
        if (depth_a is None or depth_b is None or depths_a.keys() != depths_b.keys()
                or baseline.get("depth_min_alpha") != candidate.get("depth_min_alpha")
                or any(row["measured_pixels"] != depths_b[k]["measured_pixels"] for k, row in depths_a.items())):
            raise ValueError("development scores cover different measured depth")
        result["depth"] = {f"delta_{key}": depth_b[key] - depth_a[key]
                           for key in ("coverage", "within_2cm", "within_5cm")}
        same_support = all(row.get("matched_mask_sha256")
                           and row["matched_mask_sha256"] == depths_b[k].get("matched_mask_sha256")
                           for k, row in depths_a.items())
        result["depth"]["same_matched_pixels"] = same_support
        if same_support:
            for key in ("matched_mae_m", "matched_rmse_m"):
                if depth_a.get(key) is not None and depth_b.get(key) is not None:
                    result["depth"][f"delta_{key}"] = depth_b[key] - depth_a[key]
    lpips_a, lpips_b = baseline.get("lpips"), candidate.get("lpips")
    if lpips_a is not None or lpips_b is not None:
        if (lpips_a is None or lpips_b is None or lpips_a["protocol"] != lpips_b["protocol"]
                or {key: row["patches"] for key, row in lpips_a["frames"].items()} !=
                   {key: row["patches"] for key, row in lpips_b["frames"].items()}):
            raise ValueError("development scores use different LPIPS protocols or coverage")
        result["delta_lpips"] = lpips_b["mean"] - lpips_a["mean"] if lpips_a["patches"] else None
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("registration", "renders", "output"):
        parser.add_argument(field, type=Path)
    parser.add_argument("--compare-to", type=Path, help="baseline development raw_metrics.json")
    parser.add_argument("--compare-training-variants", action="store_true",
                        help="compare two unedited checkpoints from the same training reference")
    parser.add_argument("--region-ledger", type=Path, help="use this development edit's fixed scoring cube")
    parser.add_argument("--integrity-baseline", type=Path, help="check outside-region RGB against original renders")
    parser.add_argument("--detail-metrics", action="store_true", help="score raw edge agreement and high-frequency energy")
    parser.add_argument("--perceptual-approval", type=Path)
    parser.add_argument("--lpips-alexnet", type=Path, help="local pinned AlexNet weights for development LPIPS")
    parser.add_argument("--lpips-device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--point-support", type=Path, help="screen this development edit against its point support report")
    args = parser.parse_args()
    if args.compare_training_variants and args.compare_to is None:
        parser.error("--compare-training-variants requires --compare-to")
    report = score_registered(args.registration, args.renders, args.output, region_ledger=args.region_ledger,
                              integrity_baseline=args.integrity_baseline, detail=args.detail_metrics,
                              lpips_alexnet=args.lpips_alexnet, lpips_device=args.lpips_device,
                              perceptual_approval=args.perceptual_approval)
    if args.point_support:
        screening = screen_development(report, load_strict(args.point_support))
        write_json(args.output / "screening.json", {
            **screening, "score_sha256": sha256_file(args.output / "raw_metrics.json"),
            "point_support_sha256": sha256_file(args.point_support),
        })
        print(screening)
    if args.compare_to:
        baseline = load_strict(args.compare_to)
        comparison = compare_development(baseline, report, training_variants=args.compare_training_variants)
        write_json(args.output / "comparison.json", {
            **comparison, "baseline_score_sha256": sha256_file(args.compare_to),
            "candidate_score_sha256": sha256_file(args.output / "raw_metrics.json"),
        })
        print(comparison)
    else:
        print(report["aggregate"])
