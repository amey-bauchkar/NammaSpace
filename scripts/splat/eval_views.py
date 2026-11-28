"""Evaluate a development render against its registered observation in the same pixel grid."""
import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def depth_metrics(predicted, measured):
    if (predicted.shape != measured.shape or predicted.ndim != 2
            or not np.isfinite(predicted).all() or not np.isfinite(measured).all()
            or (predicted < 0).any() or (measured < 0).any()):
        raise ValueError("invalid paired metric depth grids")
    valid = measured > 0
    matched = valid & (predicted > 0)
    count = int(valid.sum())
    if not count:
        raise ValueError("no measured depth samples")
    errors = np.abs(predicted[matched].astype(np.float64) - measured[matched])
    within_2cm = int(np.count_nonzero(errors <= .02))
    within_5cm = int(np.count_nonzero(errors <= .05))
    return {"measured_pixels": count, "matched_pixels": len(errors),
            "coverage": len(errors) / count,
            "median_error_m": float(np.median(errors)) if len(errors) else None,
            "matched_mask_sha256": hashlib.sha256(np.packbits(matched)).hexdigest(),
            "abs_error_sum_m": float(errors.sum()),
            "squared_error_sum_m2": float(np.square(errors).sum()),
            "count_within_2cm": within_2cm, "count_within_5cm": within_5cm,
            "within_2cm": within_2cm / count, "within_5cm": within_5cm / count}


def detail_metrics(render, photo, mask=None):
    """Fixed raw luminance detail metrics on the same pixel grid."""
    if (render.shape != photo.shape or render.ndim != 3 or render.shape[2] != 3
            or render.dtype != np.uint8 or photo.dtype != np.uint8):
        raise ValueError("expected aligned uint8 RGB images")
    region = np.ones(render.shape[:2], bool) if mask is None else np.asarray(mask, dtype=bool)
    if region.shape != render.shape[:2] or not region.any():
        raise ValueError("empty or mismatched region mask")
    pred, ref = [cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) for image in (render, photo)]
    edges = [(cv2.Canny(image, 60, 120, L2gradient=True) != 0) & region for image in (pred, ref)]
    predicted, observed = edges
    counts = [int(edges.sum()) for edges in (predicted, observed)]
    matched = [0, 0]
    if all(counts):
        for i in (0, 1):
            distance = cv2.distanceTransform((~edges[1-i]).astype(np.uint8),
                                             cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
            matched[i] = int(np.count_nonzero(distance[edges[i]] <= 2))
    energy = [float(np.square(cv2.Laplacian(image.astype(np.float32) / 255, cv2.CV_32F)[region]).sum(
        dtype=np.float64)) for image in (pred, ref)]
    return {"predicted_edges": counts[0], "observed_edges": counts[1],
            "matched_predicted_edges": matched[0], "matched_observed_edges": matched[1],
            "predicted_energy": energy[0], "observed_energy": energy[1]}


def summarize_detail(rows):
    keys = ("predicted_edges", "observed_edges", "matched_predicted_edges", "matched_observed_edges",
            "predicted_energy", "observed_energy")
    totals = {key: sum(row[key] for row in rows) for key in keys}
    predicted, observed, matched_pred, matched_obs, pred_energy, obs_energy = (totals[k] for k in keys)
    precision = matched_pred / predicted if predicted else 0.
    recall = matched_obs / observed if observed else 0.
    f_score = 2 * precision * recall / (precision + recall) if precision + recall else 0.
    return {**totals, "edge_f_score_2px": f_score if predicted or observed else None,
            "high_frequency_energy_ratio": pred_energy / obs_energy if obs_energy else None}


def boundary_metrics(render, photo, mask):
    """Compare RGB steps across the projected edit boundary to the observation."""
    mask = np.asarray(mask, dtype=bool)
    if render.shape != photo.shape or render.shape != (*mask.shape, 3) or mask.ndim != 2:
        raise ValueError("expected aligned RGB images and a projected region mask")
    residual = render.astype(np.int16) - photo.astype(np.int16)
    horizontal = mask[:, 1:] != mask[:, :-1]
    vertical = mask[1:] != mask[:-1]
    steps_x = residual[:, 1:][horizontal] - residual[:, :-1][horizontal]
    steps_y = residual[1:][vertical] - residual[:-1][vertical]
    count = len(steps_x) + len(steps_y)
    error = int(np.abs(steps_x).sum() + np.abs(steps_y).sum())
    return {"pairs": count, "abs_sum_uint8": error,
            "mean_rgb_step_error": error / (3 * 255 * count) if count else None}


def raw_metrics(render, photo, mask=None):
    if render.shape != photo.shape or render.ndim != 3 or render.shape[2] != 3:
        raise ValueError("expected aligned RGB images with identical dimensions")
    if render.dtype != np.uint8 or photo.dtype != np.uint8:
        raise ValueError("expected uint8 sRGB")
    h, w = render.shape[:2]
    region = np.ones((h, w), bool) if mask is None else np.asarray(mask, bool)
    if region.shape != (h, w) or not region.any():
        raise ValueError("empty or mismatched region mask")
    def blur(values):
        return cv2.GaussianBlur(values, (11, 11), 1.5)

    pixels = int(region.sum())
    squared_error = 0.0
    ssim_sum = 0.0
    # A five-row halo preserves the Gaussian window at strip boundaries.
    for start in range(0, h, 256):
        end = min(h, start + 256)
        mask = region[start:end]
        if not mask.any():
            continue
        low, high = max(0, start - 5), min(h, end + 5)
        core = slice(start - low, end - low)
        x, y = (im[low:high].astype(np.float64) / 255 for im in (render, photo))
        squared_error += float(np.sum((x[core][mask] - y[core][mask]) ** 2))
        mx, my = blur(x)[core], blur(y)[core]
        vx, vy = blur(x*x)[core] - mx*mx, blur(y*y)[core] - my*my
        cov = blur(x*y)[core] - mx*my
        score = ((2*mx*my + 0.01**2) * (2*cov + 0.03**2)
                 / ((mx*mx + my*my + 0.01**2) * (vx + vy + 0.03**2)))
        ssim_sum += float(score[mask].sum())
    mse = squared_error / (3 * pixels)
    return {"pixels": pixels, "mse": mse, "perfect_match": mse == 0,
            "raw_psnr_db": float(-10*np.log10(mse)) if mse else None,
            "raw_ssim": ssim_sum / (3 * pixels)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("render", type=Path)
    parser.add_argument("observation", type=Path)
    parser.add_argument("--mask", type=Path)
    args = parser.parse_args()
    def read(path, mode):
        with Image.open(path) as im:
            return np.asarray(im.convert(mode))
    print(json.dumps(raw_metrics(read(args.render, "RGB"), read(args.observation, "RGB"),
                                 read(args.mask, "L") if args.mask else None), allow_nan=False))
