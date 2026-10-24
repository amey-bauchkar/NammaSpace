"""Point-cloud operations for LiDAR captures, with bounded memory (numpy / scipy; Open3D optional elsewhere).

- voxel_downsample: one centroid (and mean colour) per occupied voxel;
- VoxelAccumulator: fuses a *sequence* of clouds into one voxel grid frame by frame, so memory follows the number
  of occupied voxels, not the total number of input points (a 1000-frame export never sits in RAM at once);
- statistical_outlier_mask: classic k-nearest-neighbour distance test (scipy cKDTree);
- cloud_stats / sequence_frame_check: measurements that tell whether per-frame clouds already share one coordinate
  frame. These are heuristics and are reported as such: they never replace checking the real export.
"""
import numpy as np
from scipy.spatial import cKDTree

from namma.recon import hardware


def _keys(xyz, voxel, origin):
    """Packed int64 voxel keys (21 bits per axis: +-1M voxels, i.e. +-10 km at 1 cm)."""
    q = np.floor((xyz - origin) / voxel).astype(np.int64)
    if q.size and (q.min() < -(1 << 20) or q.max() >= (1 << 20)):
        raise ValueError("cloud too large for the voxel size (more than 1M voxels along one axis)")
    q += 1 << 20
    return (q[:, 0] << 42) | (q[:, 1] << 21) | q[:, 2]


def voxel_downsample(xyz, voxel, rgb=None):
    """-> (xyz float32, rgb uint8 | None, counts int). Centroid of the points in each occupied voxel."""
    xyz = np.asarray(xyz, np.float64)
    if not len(xyz):
        return xyz.astype(np.float32), rgb, np.zeros(0, int)
    keys = _keys(xyz, voxel, np.zeros(3))
    uniq, inv, cnt = np.unique(keys, return_inverse=True, return_counts=True)
    out = np.zeros((len(uniq), 3))
    np.add.at(out, inv, xyz)
    out /= cnt[:, None]
    col = None
    if rgb is not None:
        col = np.zeros((len(uniq), 3))
        np.add.at(col, inv, np.asarray(rgb, np.float64))
        col = np.clip(np.round(col / cnt[:, None]), 0, 255).astype(np.uint8)
    return out.astype(np.float32), col, cnt


class VoxelAccumulator:
    """Running per-voxel sums over many clouds. add() costs O(points of that frame + occupied voxels)."""

    def __init__(self, voxel):
        self.voxel = float(voxel)
        self.keys = np.zeros(0, np.int64)
        self.sums = np.zeros((0, 3))
        self.csum = np.zeros((0, 3))
        self.count = np.zeros(0, np.int64)
        self.frames = 0
        self.points_in = 0

    def add(self, xyz, rgb=None):
        xyz = np.asarray(xyz, np.float64)
        if not len(xyz):
            return
        k = _keys(xyz, self.voxel, np.zeros(3))
        u, inv, c = np.unique(k, return_inverse=True, return_counts=True)
        s = np.zeros((len(u), 3))
        np.add.at(s, inv, xyz)
        cs = np.zeros((len(u), 3))
        if rgb is not None:
            np.add.at(cs, inv, np.asarray(rgb, np.float64))
        keys = np.concatenate([self.keys, u])
        uu, inv2 = np.unique(keys, return_inverse=True)
        sums = np.zeros((len(uu), 3))
        csum = np.zeros((len(uu), 3))
        cnt = np.zeros(len(uu), np.int64)
        np.add.at(sums, inv2, np.vstack([self.sums, s]))
        np.add.at(csum, inv2, np.vstack([self.csum, cs]))
        np.add.at(cnt, inv2, np.concatenate([self.count, c]))
        self.keys, self.sums, self.csum, self.count = uu, sums, csum, cnt
        self.frames += 1
        self.points_in += len(xyz)

    def result(self, min_count=1, has_color=True):
        keep = self.count >= min_count
        n = self.count[keep][:, None]
        xyz = (self.sums[keep] / n).astype(np.float32)
        rgb = np.clip(np.round(self.csum[keep] / n), 0, 255).astype(np.uint8) if has_color else None
        return xyz, rgb, self.count[keep]

    @property
    def nbytes(self):
        return self.keys.nbytes + self.sums.nbytes + self.csum.nbytes + self.count.nbytes


def statistical_outlier_mask(xyz, k=16, std_ratio=2.0, workers=None):
    """True for inliers: mean distance to the k nearest neighbours <= global mean + std_ratio * std."""
    xyz = np.asarray(xyz, np.float64)
    if len(xyz) <= k:
        return np.ones(len(xyz), bool)
    workers = workers or hardware.resources().cpu_threads
    d, _ = cKDTree(xyz).query(xyz, k=k + 1, workers=workers)
    m = d[:, 1:].mean(1)
    return m <= m.mean() + std_ratio * m.std()


def cloud_stats(xyz):
    xyz = np.asarray(xyz, np.float64)
    if not len(xyz):
        return {"points": 0}
    lo, hi = np.percentile(xyz, 1, axis=0), np.percentile(xyz, 99, axis=0)
    dist = np.linalg.norm(xyz, axis=1)
    return {"points": int(len(xyz)), "centroid": xyz.mean(0).round(4).tolist(),
            "extent_p1_p99": (hi - lo).round(4).tolist(), "bounds_p1_p99": [lo.round(4).tolist(), hi.round(4).tolist()],
            "distance_from_origin_p5_p50_p95": np.percentile(dist, [5, 50, 95]).round(4).tolist()}


def sequence_frame_check(frames, overlap_tol=0.05, max_points=20000, workers=None):
    """frames: list of (N_i, 3) arrays in file order. Measures whether they look like one shared frame (fusable as
    is) or like per-sensor-pose clouds (need poses / registration first). Returns measurements + an interpretation
    with an explicit confidence, never a certainty."""
    rng = np.random.default_rng(0)
    sub = [f[rng.choice(len(f), min(len(f), max_points), replace=False)].astype(np.float64) for f in frames if len(f)]
    if len(sub) < 2:
        return {"frames": len(sub), "interpretation": "single frame", "confidence": "n/a"}
    workers = workers or hardware.resources().cpu_threads
    per = [cloud_stats(f) for f in sub]
    ext_mean = np.mean([p["extent_p1_p99"] for p in per], axis=0)
    union = np.vstack(sub)
    ext_union = np.percentile(union, 99, axis=0) - np.percentile(union, 1, axis=0)
    growth = float(np.max(ext_union / np.maximum(ext_mean, 1e-9)))
    overlaps, tols = [], []
    for a, b in zip(sub[:-1], sub[1:]):
        ta = cKDTree(a)
        # a fixed tolerance is meaningless for sparse frames: never less than 2.5 x the frame's own point spacing
        spacing = float(np.median(ta.query(a[: min(len(a), 2000)], k=2, workers=workers)[0][:, 1]))
        tol = max(overlap_tol, 2.5 * spacing)
        d, _ = ta.query(b, k=1, workers=workers)
        overlaps.append(float((d < tol).mean()))
        tols.append(tol)
    near_origin = float(np.median([p["distance_from_origin_p5_p50_p95"][0] for p in per]))
    centroids = np.array([p["centroid"] for p in per])
    drift = float(np.linalg.norm(centroids[-1] - centroids[0]))
    # sensor-relative frames sit in front of the origin along ONE axis (the camera's viewing axis): every centroid
    # has the same dominant axis and sign, small lateral offset, and nothing lies at the origin itself.
    # (Measured on a real 1662-frame Record3D "Zipped PLY" export: centroids (0, 0, -0.9..-1.6) for all 12 sampled
    # frames, nearest points 0.8-1.1 m from the origin, total centroid drift 0.23 m.)
    ax = np.argmax(np.abs(centroids), axis=1)
    sign = np.sign(centroids[np.arange(len(centroids)), ax])
    axial = np.abs(centroids[np.arange(len(centroids)), ax])
    lateral = np.sqrt(np.maximum((centroids ** 2).sum(1) - axial ** 2, 0))
    dom_axis, dom_sign = np.bincount(ax).argmax(), (np.sign(np.median(sign)) or 1.0)
    same_axis = float(np.mean((ax == dom_axis) & (sign == dom_sign)))
    on_axis = float(np.mean(lateral < 0.25 * np.maximum(axial, 1e-9)))
    ext = float(np.max(ext_mean))
    frustum = same_axis >= 0.9 and on_axis >= 0.9 and near_origin > 0.2 and drift < 0.5 * ext
    res = {"frames": len(sub), "consecutive_overlap_median": round(float(np.median(overlaps)), 3),
           "union_vs_frame_extent_growth": round(growth, 2), "centroid_drift": round(drift, 3),
           "median_nearest_point_distance_from_origin": round(near_origin, 3),
           "frames_on_one_viewing_axis": round(min(same_axis, on_axis), 3),
           "overlap_tolerance_median": round(float(np.median(tols)), 4)}
    if frustum:
        res.update(interpretation="frames look sensor-relative (each cloud in front of its own origin, along one "
                                  "viewing axis): camera poses or frame-to-frame registration are needed before "
                                  "fusion", confidence="heuristic: strong pattern, but confirm visually")
    elif growth > 1.5 and res["consecutive_overlap_median"] > 0.3:
        res.update(interpretation="frames look like parts of one shared coordinate frame (fusable by voxel merging)",
                   confidence="heuristic: verify on the real export")
    elif growth <= 1.2 and near_origin > 0.05:
        res.update(interpretation="frames look sensor-relative (each near the origin): poses or registration are "
                                  "needed before fusion", confidence="heuristic: verify on the real export")
    else:
        res.update(interpretation="undetermined: inspect a few frames visually and look for pose metadata",
                   confidence="low")
    return res
