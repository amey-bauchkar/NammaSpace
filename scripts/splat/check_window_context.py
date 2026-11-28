"""Validate regional context against full-store window renders on private training cameras."""
import argparse
import random
from pathlib import Path

import torch

from render_development import load_parameters, load_strict, sha256_file, verify_checkpoint
from splat_crops import crop_K
from train_splat import render, se3_exp


def visible_rows(info, width, height):
    # gsplat's projected extents include rotated anisotropic splats at every depth.
    xy = info["means2d"][0]
    r = info["radii"][0]
    return ((r > 0).all(1) & (xy[:, 0] + r[:, 0] >= 0)
            & (xy[:, 1] + r[:, 1] >= 0) & (xy[:, 0] - r[:, 0] < width)
            & (xy[:, 1] - r[:, 1] < height))


@torch.no_grad()
def compare_window(params, view, intrinsics, width, height, degree, keep_from=None):
    full, alpha, info = render(params, view, intrinsics, width, height, degree)
    mask = visible_rows(info, width, height)
    if keep_from is not None:
        mask[keep_from:] = True
    selected = int(mask.sum())
    if selected:
        reduced, reduced_alpha, _ = render({k: v[mask] for k, v in params.items()},
                                           view, intrinsics, width, height, degree)
    else:
        reduced, reduced_alpha = torch.zeros_like(full), torch.zeros_like(alpha)
    error = max((full - reduced).abs().amax().item(), (alpha - reduced_alpha).abs().amax().item())
    if not torch.isfinite(full).all() or not torch.isfinite(reduced).all() or not torch.isfinite(alpha).all() or not torch.isfinite(reduced_alpha).all():
        raise ValueError("nonfinite window render")
    return mask, {"selected": selected, "max_abs_error": error}


def check_window(params, view, intrinsics, width, height, degree):
    return compare_window(params, view, intrinsics, width, height, degree)[1]


def validate(checkpoint, seed, size, *, diagnostic_repeated_cameras=False):
    verify_checkpoint(checkpoint)
    cameras = load_strict(checkpoint / "cameras.json")
    split = load_strict(checkpoint / "splits.json")
    names = [Path(c["name"]).stem for c in cameras]
    if len(set(names)) != len(names) or not set(names) <= set(split["train"]):
        raise ValueError("requires distinct training cameras")
    if not cameras or size < 1 or (len(cameras) < 100 and not diagnostic_repeated_cameras):
        raise ValueError("requires 100 distinct cameras and a positive window size")
    cfg = load_strict(checkpoint / "config.json")
    degree = min((cfg["iters"] - 1) // 1000, cfg["sh_degree"])
    params = load_parameters(checkpoint / "splat.ply", "cuda")
    rng = random.Random(seed)
    sampled = rng.sample(cameras, min(len(cameras), 100))
    while len(sampled) < 100:
        sampled.extend(rng.sample(cameras, min(len(cameras), 100 - len(sampled))))
    rows = []
    for camera in sampled:
        def tensor(key):
            return torch.tensor(camera[key], dtype=torch.float32, device="cuda")
        view = se3_exp(tensor("train_delta_se3")[None])[0] @ tensor("T_camera_from_world")
        width, height = min(size, camera["width"]), min(size, camera["height"])
        x = rng.randrange(camera["width"] - width + 1)
        y = rng.randrange(camera["height"] - height + 1)
        result = check_window(params, view, crop_K(tensor("K"), x, y), width, height, degree)
        rows.append(dict(camera=camera["name"], window=[x, y, width, height], **result))
    return {"checkpoint": sha256_file(checkpoint / "manifest.json"), "seed": seed, "views": rows,
            "equivalent": all(r["max_abs_error"] <= 1e-6 for r in rows),
            "distinct_cameras": len({r["camera"] for r in rows}),
            "qualification_eligible": not diagnostic_repeated_cameras,
            "total_rows": len(params["means"])}


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("private_report", type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--diagnostic-repeated-cameras", action="store_true",
                        help="permit repeated cameras for a provisional benchmark")
    args = parser.parse_args()
    report = validate(args.checkpoint, args.seed, args.size,
                      diagnostic_repeated_cameras=args.diagnostic_repeated_cameras)
    with args.private_report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, allow_nan=False)
    raise SystemExit(0 if report["equivalent"] else 1)
