"""Evaluate a development render against its registered observation in the same pixel grid."""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def raw_metrics(render, photo, mask=None):
    if render.shape != photo.shape or render.ndim != 3 or render.shape[2] != 3:
        raise ValueError("expected aligned RGB images with identical dimensions")
    if render.dtype != np.uint8 or photo.dtype != np.uint8:
        raise ValueError("expected uint8 sRGB")
    h, w = render.shape[:2]
    region = np.ones((h, w), bool) if mask is None else np.asarray(mask, bool)
    if region.shape != (h, w) or not region.any():
        raise ValueError("empty or mismatched region mask")
    x, y = [im.astype(np.float64) / 255 for im in (render, photo)]
    mse = float(np.mean((x[region] - y[region]) ** 2))
    def blur(values):
        return cv2.GaussianBlur(values, (11, 11), 1.5)
    mx, my = blur(x), blur(y)
    vx, vy = blur(x*x) - mx*mx, blur(y*y) - my*my
    cov = blur(x*y) - mx*my
    score = ((2*mx*my + 0.01**2) * (2*cov + 0.03**2)
             / ((mx*mx + my*my + 0.01**2) * (vx + vy + 0.03**2)))
    return {"pixels": int(region.sum()), "mse": mse, "perfect_match": mse == 0,
            "raw_psnr_db": float(-10*np.log10(mse)) if mse else None,
            "raw_ssim": float(score[region].mean())}


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
