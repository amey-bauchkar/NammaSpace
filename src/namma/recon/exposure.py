"""Exposure compensation between photos before texturing (removes the brightness "patchwork").

Phones expose every photo differently. The texture is stitched from many photos, so the same wall or door shows
patches of different brightness. OpenMVS's own seam levelling blackens texels in the 2.4.0 build used here (C-02),
so it stays off; instead every photo gets one gain per colour channel, estimated from the 3D points it shares with
other photos (the same point must have the same colour in every photo):

    log c[i, p] = log g[i] + log r[p]      (photo i, point p; per channel; least squares, mean log g = 0)

Observations near black / white (clipped) are ignored; outliers (specular highlights, occlusion) are trimmed.
"""
import shutil
from pathlib import Path

import numpy as np
from PIL import Image


def read_observations(images_txt):
    """name -> (xy (k, 2) float, point3d ids (k,) int) for observations with a 3D point."""
    out = {}
    lines = [ln for ln in Path(images_txt).read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    i = 0
    while i + 1 < len(lines):
        head = lines[i].split()
        if len(head) < 10:
            i += 1
            continue
        name = " ".join(head[9:])
        vals = lines[i + 1].split()
        a = np.array(vals, dtype=np.float64).reshape(-1, 3) if vals else np.zeros((0, 3))
        keep = a[:, 2] >= 0
        out[name] = (a[keep, :2], a[keep, 2].astype(np.int64))
        i += 2
    return out


def sample_colours(image_path, xy):
    """Mean RGB (0-1) of the 3 x 3 neighbourhood at each keypoint."""
    with Image.open(image_path) as im:
        a = np.asarray(im.convert("RGB"), np.float32) / 255
    h, w = a.shape[:2]
    x = np.clip(np.round(xy[:, 0]).astype(int), 1, w - 2)
    y = np.clip(np.round(xy[:, 1]).astype(int), 1, h - 2)
    acc = np.zeros((len(xy), 3), np.float32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            acc += a[y + dy, x + dx]
    return acc / 9


def estimate_gains(obs, image_dir, names, iters=40, trim=0.35, max_gain=2.0):
    """Per-photo, per-channel gains for `names`. Returns (gains {name: [r, g, b]}, stats)."""
    rows = []                                   # (photo index, point id, rgb)
    names = [n for n in names if n in obs and (Path(image_dir) / n).exists()]
    for k, n in enumerate(names):
        xy, pid = obs[n]
        if not len(pid):
            continue
        c = sample_colours(Path(image_dir) / n, xy)
        ok = (c.min(axis=1) > 0.03) & (c.max(axis=1) < 0.97)
        rows.append((np.full(ok.sum(), k), pid[ok], c[ok]))
    if not rows:
        return {n: [1.0, 1.0, 1.0] for n in names}, {"observations": 0}
    img = np.concatenate([r[0] for r in rows])
    pid = np.concatenate([r[1] for r in rows])
    col = np.concatenate([r[2] for r in rows])
    # keep points seen (unclipped) in >= 2 photos
    u, inv, cnt = np.unique(pid, return_inverse=True, return_counts=True)
    keep = cnt[inv] >= 2
    img, inv, col = img[keep], np.unique(inv[keep], return_inverse=True)[1], col[keep]
    n_img, n_pts = len(names), inv.max() + 1
    L = np.log(col)
    g = np.zeros((n_img, 3))
    w = np.ones(len(L), bool)
    before = None
    for it in range(iters):
        # radiance per point = mean over its (inlier) observations of log c - log g
        r = np.zeros((n_pts, 3)); rc = np.zeros(n_pts)
        np.add.at(r, inv[w], (L - g[img])[w]); np.add.at(rc, inv[w], 1)
        r /= np.maximum(rc, 1)[:, None]
        res = L - g[img] - r[inv]
        if before is None:
            before = float(np.median(np.abs(L - L.mean(0) - (r[inv] - r[inv].mean(0)))))
        gs = np.zeros((n_img, 3)); gc = np.zeros(n_img)
        np.add.at(gs, img[w], (L - r[inv])[w]); np.add.at(gc, img[w], 1)
        g = np.where(gc[:, None] > 0, gs / np.maximum(gc, 1)[:, None], 0.0)
        g -= g.mean(0)                                      # overall brightness unchanged
        g = np.clip(g, -np.log(max_gain), np.log(max_gain))
        if it == iters // 2:                                # trim outliers once the solution has settled
            res = L - g[img] - r[inv]
            w = np.abs(res).max(axis=1) < trim
    res = L - g[img] - r[inv]
    # spread between photos of the same point, before (no gains) and after
    def spread(gain):
        rr = np.zeros((n_pts, 3)); rc2 = np.zeros(n_pts)
        np.add.at(rr, inv[w], (L - gain[img])[w]); np.add.at(rc2, inv[w], 1)
        rr /= np.maximum(rc2, 1)[:, None]
        return float(np.median(np.abs((L - gain[img] - rr[inv])[w])))
    stats = {"photos": n_img, "points": int(n_pts), "observations": int(len(L)), "inliers": int(w.sum()),
             "log_spread_before": spread(np.zeros_like(g)), "log_spread_after": spread(g),
             "gain_min": float(np.exp(g).min()), "gain_max": float(np.exp(g).max())}
    return {n: np.exp(g[k]).round(4).tolist() for k, n in enumerate(names)}, stats


def apply_gains(image_dir, gains, backup_dir, quality=95):
    """Write gain-corrected copies over the images in image_dir; originals are kept once in backup_dir."""
    image_dir, backup_dir = Path(image_dir), Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name, gain in gains.items():
        src = image_dir / name
        orig = backup_dir / name
        if not orig.exists():
            shutil.copy2(src, orig)
        with Image.open(orig) as im:
            a = np.asarray(im.convert("RGB"), np.float32)
            exif = im.info.get("exif", b"")
        a = np.clip(a * np.asarray(gain, np.float32), 0, 255).astype(np.uint8)
        Image.fromarray(a).save(src, "JPEG", quality=quality, exif=exif)
