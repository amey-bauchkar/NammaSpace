"""Ground-truth loaders (read-only on the dataset files).

ETH3D      : laser scans (dslr_scan_eval/scan*.ply + scan_alignment.mlp) and DSLR camera poses (COLMAP text),
             metric, dataset frame.
ARKitScenes: lowres_wide.traj (world->camera, axis-angle + translation, metric, 10 Hz), ARKit LiDAR mesh and
             human-annotated oriented furniture boxes, all in the same z-up metric frame (verified: box axes
             have a vertical third axis and the floor lies at a constant z).
TUM RGB-D  : motion-capture trajectory (timestamp tx ty tz qx qy qz qw = camera pose in world), metric.
"""
import json
import re
from pathlib import Path

import cv2
import numpy as np

from namma.recon.io import read_colmap_images_txt, read_ply


def voxel_down(pts, voxel):
    keys = np.floor(pts / voxel).astype(np.int64)
    keys -= keys.min(0)
    dims = keys.max(0) + 1
    lin = (keys[:, 0] * dims[1] + keys[:, 1]) * dims[2] + keys[:, 2]
    _, idx = np.unique(lin, return_index=True)
    return pts[idx]


def _mlp(path):
    txt = Path(path).read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r'filename="([^"]+)">\s*<MLMatrix44>(.*?)</MLMatrix44>', txt, re.S):
        out[m.group(1)] = np.array([float(v) for v in m.group(2).split()]).reshape(4, 4)
    return out


def eth3d(scene_dir, voxel=0.01):
    scene_dir = Path(scene_dir)
    imgs = read_colmap_images_txt(scene_dir / "dslr_calibration_jpg" / "images.txt")
    cams = {Path(n).stem.lower(): v["C"] for n, v in imgs.items()}
    rots = {Path(n).stem.lower(): v["R"] for n, v in imgs.items()}
    ev = scene_dir / "dslr_scan_eval"
    align = _mlp(ev / "scan_alignment.mlp")
    pts = []
    for ply in sorted(ev.glob("scan*.ply")):
        v = read_ply(ply)["vertex"]
        p = np.column_stack([v["x"], v["y"], v["z"]]).astype(np.float64)
        M = align.get(ply.name, np.eye(4))
        pts.append(p @ M[:3, :3].T + M[:3, 3])
    pts = voxel_down(np.vstack(pts), voxel)
    return {"kind": "eth3d_laser", "cameras": cams, "rotations": rots, "points": pts, "up_axis": None,
            "units": "m", "note": f"ETH3D laser scan, voxel {voxel} m; GT DSLR poses"}


def arkit(raw_dir, video_id, mesh_points_max=2_000_000, seed=0):
    raw = Path(raw_dir)
    traj = np.loadtxt(raw / "lowres_wide.traj")
    ts, C, R = [], [], []
    for row in traj:
        Rw2c, _ = cv2.Rodrigues(row[1:4])
        ts.append(row[0])
        R.append(Rw2c)
        C.append(-Rw2c.T @ row[4:7])
    d = read_ply(raw / f"{video_id}_3dod_mesh.ply")
    v = d["vertex"]
    verts = np.column_stack([v["x"], v["y"], v["z"]]).astype(np.float64)
    faces = d["faces"]
    pts = sample_mesh(verts, faces, mesh_points_max, seed) if faces is not None else verts
    ann = json.loads((raw / f"{video_id}_3dod_annotation.json").read_text(encoding="utf-8"))
    boxes = []
    for o in ann["data"]:
        s = o["segments"]["obbAligned"]
        boxes.append({"label": o.get("label"), "uid": o.get("uid"), "centroid": np.array(s["centroid"], float),
                      "axes_lengths": np.array(s["axesLengths"], float),
                      "axes": np.array(s["normalizedAxes"], float).reshape(3, 3)})
    return {"kind": "arkit", "traj_t": np.array(ts), "traj_C": np.array(C), "traj_R": R, "points": pts,
            "boxes": boxes, "up_axis": "z", "units": "m",
            "note": "ARKit VIO trajectory and ARKit LiDAR mesh (sensor reconstruction, not a laser scan); "
                    "annotated furniture boxes (human)"}


def sample_mesh(verts, faces, n_max, seed=0, spacing=0.01):
    f = np.asarray(faces)
    a, b, c = verts[f[:, 0]], verts[f[:, 1]], verts[f[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    n = int(min(n_max, max(len(f), area.sum() / (spacing * spacing))))
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(f), n, p=area / area.sum())
    u, w = rng.random(n), rng.random(n)
    flip = u + w > 1
    u[flip], w[flip] = 1 - u[flip], 1 - w[flip]
    return a[idx] + u[:, None] * (b[idx] - a[idx]) + w[:, None] * (c[idx] - a[idx])


def tum(sample_dir):
    cams = {}
    for ln in (Path(sample_dir) / "groundtruth_subset.txt").read_text().splitlines():
        if not ln or ln.startswith("#"):
            continue
        p = ln.split()
        cams[p[0]] = np.array([float(x) for x in p[1:4]])
    return {"kind": "tum_mocap", "cameras": cams, "points": None, "units": "m",
            "note": "TUM motion-capture camera positions (nearest within 20 ms)"}


def interp_centres(t_query, traj_t, traj_C):
    """Linear interpolation of camera centres; NaN outside the trajectory time range."""
    out = np.full((len(t_query), 3), np.nan)
    ok = (t_query >= traj_t[0]) & (t_query <= traj_t[-1])
    for k in range(3):
        out[ok, k] = np.interp(t_query[ok], traj_t, traj_C[:, k])
    return out


def box_corners_xy_footprint(box, up_axis="z"):
    """Footprint polygon (4 corners) of an oriented box projected on the floor plane (z-up frame)."""
    c, L, A = box["centroid"], box["axes_lengths"], box["axes"]
    corners = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                corners.append(c + A.T @ (np.array([sx, sy, sz]) * L / 2))
    return np.array(corners)
