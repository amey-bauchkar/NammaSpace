"""LiDAR fusion and the "surface the model is missing" check for a Polycam LiDAR scan (implementation_plan.md, Phase 4).

Finding on the reference kitchen: Polycam's own LiDAR mesh already holds nearly all the surface the LiDAR measured
(0.11 m2 missing); the 2.29 m2 "gap" came from our photogrammetry repair, now off for LiDAR scans
(mesh_repair.LIDAR_OPTIONS). So the fused mesh is a CHECK (how much measured surface a model lacks) and the fill
mesh is written for inspection; it is not merged into the model.

1. fuse():  TSDF fusion (Open3D VoxelBlockGrid, CPU - the legacy ScalableTSDFVolume returns no triangles on this
            Windows build) of the corrected 512x384 LiDAR depth (confidence >= medium) with Phase 2's poses if they
            exist, else Polycam's corrected poses, and the corrected photos for colour; moved into the GLB frame.
2. gaps():  triangles of the fused mesh that are far (> GAP_M) from every SCANNED surface of the imported model
            (the repair's flat hole fills / backing sheets do not count: they are guesses), kept as connected pieces
            of at least MIN_PIECE_M2 - surface the LiDAR measured but the model is missing.
Codex's pilot found that fused depth smooths furniture edges, so it is never a replacement for the model's surface.

Writes <project>/capture/lidar/: fused.ply, fill.ply (vertex colours, GLB frame), report.json.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from namma.capture import polycam_raw as pr
from namma.capture import polycam_report as rpt

VOXEL_M = 0.015
TRUNC_M = 0.045
DEPTH_MAX_M = 4.0
MIN_CONFIDENCE = 1
WEIGHT_THRESHOLD = 3.0          # a fused surface must be seen by >= 3 frames
GAP_M = 0.04                    # a fused triangle further than this from the model's scanned surface = a gap
MIN_PIECE_M2 = 0.01


def fuse(ex, poses, glb_from_frame, *, ids=None, progress=None):
    """-> open3d.geometry.TriangleMesh (legacy) with vertex colours, in the GLB frame."""
    import open3d as o3d
    progress = progress or (lambda *_: None)
    ids = ids or ex.ids
    f32 = o3d.core.float32
    vbg = o3d.t.geometry.VoxelBlockGrid(("tsdf", "weight", "color"), (f32, f32, f32), ((1,), (1,), (3,)),
                                        VOXEL_M, 16, 60_000, o3d.core.Device("CPU:0"))
    trunc = TRUNC_M / VOXEL_M
    for n, k in enumerate(ids):
        d, c = ex.depth(k), ex.confidence(k)
        d = np.ascontiguousarray(np.where(np.isfinite(d) & (c >= MIN_CONFIDENCE), d, 0).astype(np.float32))
        h, w = d.shape
        from PIL import Image
        col = np.asarray(Image.fromarray(ex.image(k)).resize((w, h), Image.BILINEAR), np.float32) / 255.0
        K = o3d.core.Tensor(pr.scaled_K(ex.intrinsics(k), d.shape), dtype=o3d.core.float64)
        E = o3d.core.Tensor(np.linalg.inv(pr.opencv_c2w(poses[k])), dtype=o3d.core.float64)   # world -> camera
        depth = o3d.t.geometry.Image(o3d.core.Tensor(d))
        color = o3d.t.geometry.Image(o3d.core.Tensor(np.ascontiguousarray(col)))
        coords = vbg.compute_unique_block_coordinates(depth, K, E, 1.0, DEPTH_MAX_M, trunc)
        vbg.integrate(coords, depth, color, K, K, E, 1.0, DEPTH_MAX_M, trunc)
        if n % 20 == 0:
            progress(f"Fusing LiDAR depth ({n + 1}/{len(ids)})", n / len(ids))
    mesh = vbg.extract_triangle_mesh(weight_threshold=WEIGHT_THRESHOLD).to_legacy()
    if not mesh.has_triangles():
        raise ValueError("LiDAR fusion returned no triangles")
    mesh.transform(glb_from_frame)
    return mesh


def _pieces(F, mask):
    """Connected components (shared vertices) of the faces in mask -> label per face (-1 outside mask)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    idx = np.flatnonzero(mask)
    lab = np.full(len(F), -1)
    if not len(idx):
        return lab
    sub = F[idx]
    nv = int(F.max()) + 1
    rows = np.repeat(np.arange(len(idx)), 3)
    g = coo_matrix((np.ones(len(rows)), (rows, sub.ravel())), shape=(len(idx), nv)).tocsr()
    adj = g @ g.T                                               # faces sharing a vertex
    _, l = connected_components(adj, directed=False)
    lab[idx] = l
    return lab


def gaps(mesh, model_glb):
    """Fused triangles where the model's SCANNED surface is missing -> (face mask, stats)."""
    from scipy.spatial import cKDTree
    from namma.studio.mesh_import import mesh_triangles, sample_surface
    V = np.asarray(mesh.vertices)
    F = np.asarray(mesh.triangles)
    Vm, Fm, _ = mesh_triangles(model_glb, scanned_only=True)
    S, _, _ = sample_surface(Vm, Fm, 3_000_000)
    tree = cKDTree(S)
    cen = V[F].mean(1)
    dist, _ = tree.query(cen, workers=-1)
    far = dist > GAP_M
    area = np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1) / 2
    lab = _pieces(F, far)
    keep = np.zeros(len(F), bool)
    if far.any():
        piece_area = np.bincount(lab[far], weights=area[far])
        big = np.flatnonzero(piece_area >= MIN_PIECE_M2)
        keep = far & np.isin(lab, big)
    n = cen[keep]
    normals = np.cross(V[F[keep, 1]] - V[F[keep, 0]], V[F[keep, 2]] - V[F[keep, 0]])
    normals /= np.linalg.norm(normals, axis=1, keepdims=True) + 1e-12
    up = np.abs(normals[:, 1])
    return keep, {"fused_triangles": int(len(F)), "fused_area_m2": round(float(area.sum()), 2),
                  "fused_within_2cm_of_model": round(float(np.mean(dist <= 0.02)), 4),
                  "gap_triangles": int(keep.sum()), "gap_area_m2": round(float(area[keep].sum()), 3),
                  "gap_pieces": int(len(np.unique(lab[keep]))) if keep.any() else 0,
                  "gap_area_by_orientation_m2": {
                      "horizontal": round(float(area[keep][up > 0.8].sum()), 3),
                      "vertical": round(float(area[keep][up < 0.3].sum()), 3),
                      "sloped": round(float(area[keep][(up >= 0.3) & (up <= 0.8)].sum()), 3)},
                  "gap_height_range_m": [round(float(n[:, 1].min()), 2), round(float(n[:, 1].max()), 2)] if len(n) else None}


def submesh(mesh, keep):
    import open3d as o3d
    F = np.asarray(mesh.triangles)[keep]
    used, inv = np.unique(F.ravel(), return_inverse=True)
    out = o3d.geometry.TriangleMesh()
    out.vertices = o3d.utility.Vector3dVector(np.asarray(mesh.vertices)[used])
    out.triangles = o3d.utility.Vector3iVector(inv.reshape(-1, 3))
    if mesh.has_vertex_colors():
        out.vertex_colors = o3d.utility.Vector3dVector(np.asarray(mesh.vertex_colors)[used])
    return out


def run(project_dir, run_id=None, *, progress=None):
    """Fuse, find gaps against the run's model (lod0.glb), write capture/lidar/{fused,fill}.ply and report.json
    -> report."""
    import open3d as o3d
    from namma.studio.splat_lidar import load_poses
    progress = progress or (lambda *_: None)
    t0 = time.time()
    d = Path(project_dir)
    doc = load_poses(d)
    zp = rpt.find_raw_zip(d)
    if doc is None or doc.get("glb_from_frame") is None:           # no Phase 2: Polycam's corrected poses
        with pr.PolycamExport(zp) as ex:
            doc = {"poses": {k: ex.pose(k) for k in ex.ids}, "source": "corrected",
                   "glb_from_frame": pr.glb_from_camera(pr.load_sidecars(d / "capture" / "originals")["mesh_info"])}
    if run_id is None:
        run_id = sorted(p.name for p in (d / "runs").iterdir() if (p / "60_texture" / "lod0.glb").exists())[-1]
    tex = d / "runs" / run_id / "60_texture"
    model = tex / "lod0.glb"
    out = d / "capture" / "lidar"
    out.mkdir(parents=True, exist_ok=True)
    with pr.PolycamExport(zp) as ex:
        mesh = fuse(ex, doc["poses"], doc["glb_from_frame"], progress=lambda l, f: progress(l, 0.8 * f))
    mesh.compute_vertex_normals()
    o3d.io.write_triangle_mesh(str(out / "fused.ply"), mesh)
    progress("Finding where the model is missing surface", 0.85)
    keep, stats = gaps(mesh, model.read_bytes())
    fill = submesh(mesh, keep)
    o3d.io.write_triangle_mesh(str(out / "fill.ply"), fill)
    rep = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "run_id": run_id, "model": model.name,
           "poses": doc.get("source"), "parameters": {"voxel_m": VOXEL_M, "trunc_m": TRUNC_M, "depth_max_m": DEPTH_MAX_M,
                                                      "min_confidence": MIN_CONFIDENCE,
                                                      "weight_threshold": WEIGHT_THRESHOLD, "gap_m": GAP_M,
                                                      "min_piece_m2": MIN_PIECE_M2},
           **stats, "seconds": round(time.time() - t0, 1)}
    (out / "report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    progress("Done", 1.0)
    return rep


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="LiDAR fusion and gap fill (Phase 4)")
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--run")
    a = ap.parse_args()
    print(json.dumps(run(a.project, a.run, progress=lambda l, f: print(f"[{f:4.0%}] {l}", flush=True)), indent=1))
