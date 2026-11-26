"""Dense start points for splat training: every high-confidence LiDAR pixel of every photo, one point per voxel,
coloured from the photo. The splats keep roughly the spacing they start with, so the start density sets the detail.

  python scripts/splat/dense_seed.py --model <colmap text dir> --images <dir> --depth <dir> --out points3D.txt
         [--voxel 0.015] [--max-points 2000000] [--exclude names.txt]
Depth maps are <photo>.npy in metres (0 = none), as written by namma.studio.splat_lidar.prepare.
"""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image


def read_cameras(model):
    cams = {}
    for ln in (model / "cameras.txt").read_text().splitlines():
        p = ln.split()
        if len(p) >= 8 and not ln.startswith("#"):
            cams[int(p[0])] = (int(p[2]), int(p[3]), *map(float, p[4:8]))
    views = []
    for ln in (model / "images.txt").read_text().splitlines():
        p = ln.split()
        if len(p) >= 10 and not ln.startswith("#") and p[9].lower().endswith((".jpg", ".png")):
            qw, qx, qy, qz, tx, ty, tz = map(float, p[1:8])
            R = np.array([[1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qw * qz), 2 * (qx * qz + qw * qy)],
                          [2 * (qx * qy + qw * qz), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qw * qx)],
                          [2 * (qx * qz - qw * qy), 2 * (qy * qz + qw * qx), 1 - 2 * (qx * qx + qy * qy)]])
            views.append((p[9], int(p[8]), R, np.array([tx, ty, tz])))
    return cams, views


def _dedupe(P, C, voxel):
    key = np.floor(P / voxel).astype(np.int64) + (1 << 20)
    code = (key[:, 0] << 42) | (key[:, 1] << 21) | key[:, 2]
    _, first = np.unique(code, return_index=True)
    return P[first], C[first]


def dense_points(model, images, depth, *, voxel=0.015, max_points=2_000_000, exclude=(), max_depth=5.0, seed=0):
    """-> (N,3 float32 points in the cameras' frame, N,3 uint8 colours)."""
    cams, views = read_cameras(Path(model))
    P_all, C_all, held = [], [], 0
    for name, cid, R, t in views:
        f = Path(depth) / f"{Path(name).stem}.npy"
        if name in exclude or not f.exists():
            continue
        d = np.load(f).astype(np.float32)
        w, h, fx, fy, cx, cy = cams[cid]
        sy, sx = d.shape[0] / h, d.shape[1] / w
        v, u = np.nonzero((d > 0.15) & (d < max_depth))
        z = d[v, u]
        X = np.stack([(u + 0.5 - cx * sx) / (fx * sx) * z, (v + 0.5 - cy * sy) / (fy * sy) * z, z], 1)
        X = (X - t) @ R                                                   # camera -> world
        img = np.asarray(Image.open(Path(images) / name).convert("RGB"))
        col = img[np.minimum(((v + 0.5) / sy).astype(int), img.shape[0] - 1),
                  np.minimum(((u + 0.5) / sx).astype(int), img.shape[1] - 1)]
        P_all.append(X.astype(np.float32))
        C_all.append(col)
        held += len(X)
        if held > 8_000_000:                                              # keep memory flat
            P, C = _dedupe(np.vstack(P_all), np.vstack(C_all), voxel)
            P_all, C_all, held = [P], [C], len(P)
    P, C = _dedupe(np.vstack(P_all), np.vstack(C_all), voxel)
    if len(P) > max_points:
        pick = np.random.default_rng(seed).choice(len(P), max_points, replace=False)
        P, C = P[pick], C[pick]
    return P, C


def write_points(path, P, C):
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(f"{i} {p[0]:.4f} {p[1]:.4f} {p[2]:.4f} {c[0]} {c[1]} {c[2]} 0\n"
                     for i, (p, c) in enumerate(zip(P.tolist(), C.tolist()), 1))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--images", type=Path, required=True)
    ap.add_argument("--depth", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--voxel", type=float, default=0.015)
    ap.add_argument("--max-points", type=int, default=2_000_000)
    ap.add_argument("--exclude", type=Path)
    a = ap.parse_args(argv)
    skip = set(a.exclude.read_text().split()) if a.exclude else set()
    P, C = dense_points(a.model, a.images, a.depth, voxel=a.voxel, max_points=a.max_points, exclude=skip)
    write_points(a.out, P, C)
    print(f"{len(P)} start points, voxel {a.voxel * 100:.1f} cm -> {a.out}")


if __name__ == "__main__":
    main()
