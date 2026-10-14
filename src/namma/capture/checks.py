"""Per-image capture diagnostics and an overlap graph (fast, before any reconstruction).

All measurements run on downscaled grey copies; originals are only read.
- sharpness: variance of the Laplacian at 1024 px (relative to the set median and an absolute floor);
- exposure: mean luminance and clipped fractions;
- exact duplicates (SHA-256) and near-duplicates (64-bit difference hash, Hamming <= 3);
- overlap: ORB features at 640 px, ratio-test matches verified by a RANSAC fundamental matrix;
  images with no verified neighbour are unlikely to be placed in 3D; several groups means the
  capture is split into areas that do not connect.
The overlap graph is a predictor only; SfM registration is the measured outcome.
"""
import math

import cv2
import numpy as np
from PIL import Image, ImageOps

SHARP_SIDE = 1024
ORB_SIDE = 640
ORB_FEATURES = 1200
MIN_VERIFIED_MATCHES = 30
NEAR_DUP_HAMMING = 3


def load_grey(path, side):
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im.draft("L", (side, side))
        im = im.convert("L")
        im.thumbnail((side, side), Image.Resampling.BILINEAR)
        return np.asarray(im)


def dhash(grey):
    small = cv2.resize(grey, (9, 8), interpolation=cv2.INTER_AREA).astype(np.int16)
    bits = (small[:, 1:] > small[:, :-1]).ravel()
    return int("".join("1" if b else "0" for b in bits), 2)


def image_metrics(path):
    g = load_grey(path, SHARP_SIDE)
    lap = cv2.Laplacian(g, cv2.CV_64F)
    mean = float(g.mean())
    small = load_grey(path, ORB_SIDE)
    orb = cv2.ORB_create(ORB_FEATURES)
    kps, desc = orb.detectAndCompute(small, None)
    pts = np.array([k.pt for k in kps], np.float32) if kps else np.zeros((0, 2), np.float32)
    thumb = cv2.resize(small, (16, 16), interpolation=cv2.INTER_AREA).astype(np.float32).ravel()
    thumb = (thumb - thumb.mean()) / (thumb.std() + 1e-6)
    return {"sharpness": float(lap.var()), "mean_luma": round(mean, 1),
            "dark_clip": round(float(np.mean(g <= 8)), 4), "bright_clip": round(float(np.mean(g >= 247)), 4),
            "dhash": dhash(g), "orb_keypoints": len(kps)}, {"desc": desc, "pts": pts, "thumb": thumb}


def verified_matches(a, b, ratio=0.75):
    if a["desc"] is None or b["desc"] is None or len(a["desc"]) < 8 or len(b["desc"]) < 8:
        return 0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    knn = bf.knnMatch(a["desc"], b["desc"], k=2)
    good = [m[0] for m in knn if len(m) == 2 and m[0].distance < ratio * m[1].distance]
    if len(good) < 15:          # too few to verify geometrically
        return 0
    p1 = a["pts"][[m.queryIdx for m in good]]
    p2 = b["pts"][[m.trainIdx for m in good]]
    _, mask = cv2.findFundamentalMat(p1, p2, cv2.FM_RANSAC, 2.0, 0.99)
    return int(mask.sum()) if mask is not None else 0


def candidate_pairs(n, thumbs, ordered, k_similar=10, k_sequence=4):
    pairs = set()
    for i in range(n):
        for j in range(i + 1, min(n, i + 1 + k_sequence)):
            pairs.add((i, j))
    if not ordered and n > 1:
        t = np.stack(thumbs)
        sim = t @ t.T / t.shape[1]
        np.fill_diagonal(sim, -np.inf)
        for i in range(n):
            for j in np.argsort(-sim[i])[:k_similar]:
                pairs.add((min(i, int(j)), max(i, int(j))))
    return sorted(pairs)


def overlap_graph(feats, ordered):
    n = len(feats)
    pairs = candidate_pairs(n, [f["thumb"] for f in feats], ordered)
    edges = []
    for i, j in pairs:
        m = verified_matches(feats[i], feats[j])
        if m >= MIN_VERIFIED_MATCHES:
            edges.append((i, j, m))
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    degree = [0] * n
    for i, j, _ in edges:
        degree[i] += 1
        degree[j] += 1
        parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    comps = sorted(groups.values(), key=len, reverse=True)
    return {"pairs_tested": len(pairs), "edges": len(edges), "degree": degree, "components": comps}


def flag_images(metrics):
    """Per-image flags relative to the set: blurry, dark, bright, low_detail."""
    sharp = np.array([m["sharpness"] for m in metrics])
    med = float(np.median(sharp)) if len(sharp) else 0.0
    blur_thr = max(25.0, 0.35 * med)
    flags = []
    for m in metrics:
        f = []
        if m["sharpness"] < blur_thr:
            f.append("blurry")
        if m["mean_luma"] < 40 or m["dark_clip"] > 0.3:
            f.append("too_dark")
        if m["mean_luma"] > 225 or m["bright_clip"] > 0.25:
            f.append("too_bright")
        if m["orb_keypoints"] < 200:
            f.append("low_detail")
        flags.append(f)
    return flags, {"sharpness_median": round(med, 1), "blur_threshold": round(blur_thr, 1)}


def near_duplicates(hashes):
    out = []
    for i in range(len(hashes)):
        for j in range(i + 1, min(len(hashes), i + 30)):
            if bin(hashes[i] ^ hashes[j]).count("1") <= NEAR_DUP_HAMMING:
                out.append((i, j))
    return out


def sharpness_only(path):
    g = load_grey(path, SHARP_SIDE)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def plan_video_sampling(duration_s, fps, target_frames, oversample=2):
    """Candidate extraction rate (frames/s) for a deterministic best-of-window selection."""
    target_frames = max(10, int(target_frames))
    rate = min(fps if fps > 0 else 30.0, oversample * target_frames / max(duration_s, 1e-6))
    return max(0.2, round(rate, 4)), oversample


def select_sharpest(scores, window):
    """Indices of the sharpest frame in each consecutive window (ties -> earliest)."""
    out = []
    for s in range(0, len(scores), window):
        block = scores[s:s + window]
        out.append(s + int(np.argmax(block)))
    return out


def target_frames_for(duration_s, per_second=3.0, lo=40, hi=300):
    return int(min(hi, max(lo, math.ceil(duration_s * per_second))))
