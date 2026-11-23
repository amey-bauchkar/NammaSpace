"""Full-resolution crop training helpers for train_splat.py (no GPU / gsplat imports, so they can be tested anywhere).

Why: a 6 GB GPU cannot hold 289 photos of 4032x3024 (10.5 GB as uint8) nor render whole 12 MP frames per step. With
crops, each training step renders a size x size window of ONE full-resolution photo; every supervised pixel is a
real full-resolution pixel, GPU memory stays at the level of a 1024 px run, and the photos live in an uncompressed
disk cache (np.memmap) from which only the window's rows are read.

Geometry: a crop at (x0, y0) of a pinhole camera K is the same camera with the principal point moved by (-x0, -y0)
(focal lengths unchanged). The LiDAR depth guide covers the whole frame at a lower resolution (e.g. 256x192), so crop
windows start and end on depth-pixel boundaries: the depth window then covers exactly the same part of the scene.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np


def crop_K(K, x0, y0):
    """Intrinsics of the window starting at (x0, y0) of an image with intrinsics K (3x3, numpy or torch)."""
    Kc = K.clone() if hasattr(K, "clone") else np.array(K, copy=True)
    Kc[0, 2] = Kc[0, 2] - x0
    Kc[1, 2] = Kc[1, 2] - y0
    return Kc


def crop_box(W, H, size, rng, depth_shape=None):
    """Random window of about size x size pixels inside a W x H photo.
    -> (x0, y0, w, h, depth window (dx0, dy0, dw, dh) or None).
    With a depth map of shape (dh_total, dw_total) the window is snapped to whole depth pixels (both in position and
    size), so the full-resolution window and the depth window cover the same scene region."""
    size = int(min(size, W, H))
    if depth_shape is None:
        x0 = int(rng.integers(0, W - size + 1))
        y0 = int(rng.integers(0, H - size + 1))
        return x0, y0, size, size, None
    dht, dwt = depth_shape
    sx, sy = W / dwt, H / dht                                   # photo pixels per depth pixel (e.g. 15.75)
    nw = max(1, int(round(size / sx)))                          # window size in depth pixels
    nh = max(1, int(round(size / sy)))
    dx0 = int(rng.integers(0, dwt - nw + 1))
    dy0 = int(rng.integers(0, dht - nh + 1))
    x0, x1 = int(round(dx0 * sx)), int(round((dx0 + nw) * sx))
    y0, y1 = int(round(dy0 * sy)), int(round((dy0 + nh) * sy))
    return x0, y0, min(x1, W) - x0, min(y1, H) - y0, (dx0, dy0, nw, nh)


def cache_photo(cache_dir, name, rgb_uint8):
    """Write a decoded photo (H, W, 3 uint8) as an uncompressed .npy; -> path. Read back with open_cached()."""
    p = Path(cache_dir) / f"{Path(name).stem}.npy"
    p.parent.mkdir(parents=True, exist_ok=True)
    np.save(p, np.ascontiguousarray(rgb_uint8, dtype=np.uint8))
    return p


def open_cached(path):
    """Memory-mapped (H, W, 3) uint8: slicing a window reads only those rows from disk."""
    return np.load(path, mmap_mode="r")


def read_window(mm, x0, y0, w, h):
    """Contiguous copy of a window of a memory-mapped photo."""
    return np.ascontiguousarray(mm[y0:y0 + h, x0:x0 + w])


def coverage_estimate(n_photos, W, H, crop_steps, size):
    """Average number of times each full-resolution pixel is supervised by the crop phase."""
    return crop_steps * size * size / max(1, n_photos * W * H)


def gb(n_bytes):
    return n_bytes / 2 ** 30


def cache_bytes(n_photos, W, H):
    return n_photos * W * H * 3


def default_crop_steps(n_photos, W, H, size, passes=12.0):
    """Crop steps so that each full-resolution pixel is supervised about `passes` times."""
    return int(math.ceil(passes * n_photos * W * H / (size * size)))
