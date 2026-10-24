"""EXPERIMENTAL: LiDAR point cloud(s) -> filtered, fused cloud -> Poisson surface -> vertex-coloured GLB.

Status: implemented and unit-tested on synthetic geometry only. It has NOT been validated on a real Record3D room
scan; the next pass must check frame alignment, units and axes on the real export first (namma.lidar.inventory).

What it does not do: photo texturing (needs verified camera poses + intrinsics), filling surfaces the scanner never
saw (Poisson closes small gaps; large holes are trimmed back by the density filter rather than invented), or
writing an IF-13 scene package (the Studio integration is a separate step).

Requires Open3D (optional dependency: requirements-lidar.txt). Open3D's Windows wheels are CPU-only; these steps
run on the CPU with `cpu_threads` worth of parallelism where Open3D provides it.
"""
import json
import time
from pathlib import Path

import numpy as np

from namma.lidar import ply, pointcloud
from namma.recon import hardware
from namma.recon.glb import write_glb
from namma.recon.io import write_ply_xyz

# separate from the photo profiles (namma.studio.profiles): different algorithms, not part of the frozen config
LIDAR_PROFILES = {
    "preview": {"voxel_m": 0.02, "outlier_k": 16, "outlier_std": 2.0, "poisson_depth": 8, "density_trim": 0.03,
                "target_triangles": 300_000},
    "standard": {"voxel_m": 0.01, "outlier_k": 16, "outlier_std": 2.0, "poisson_depth": 9, "density_trim": 0.02,
                 "target_triangles": 1_500_000},
    "high": {"voxel_m": 0.006, "outlier_k": 20, "outlier_std": 2.0, "poisson_depth": 10, "density_trim": 0.02,
             "target_triangles": 3_000_000},
}
# Open3D Poisson peak RAM, rough: bytes per input point at depth 8 / 9 / 10 (octree + solver); used for a pre-check
POISSON_BYTES_PER_POINT = {8: 600, 9: 1100, 10: 2000}


def open3d():
    try:
        import open3d as o3d
    except ImportError as e:
        raise RuntimeError("Open3D is required for LiDAR meshing: python -m pip install -r requirements-lidar.txt") from e
    return o3d


def fuse_ply_files(paths, voxel, log=print):
    """Voxel-fuse PLY clouds that already share one coordinate frame (check with inventory measure=True first).
    Memory follows the occupied voxels, one input file at a time."""
    acc = pointcloud.VoxelAccumulator(voxel)
    has_color = True
    t0 = time.perf_counter()
    for i, p in enumerate(paths):
        pts = ply.read_points(p)
        has_color &= pts["rgb"] is not None
        acc.add(pts["xyz"], pts["rgb"])
        if log and (i % 25 == 0 or i == len(paths) - 1):
            log(f"fused {i + 1}/{len(paths)} files: {len(acc.keys):,} voxels, {acc.nbytes / 2**20:.0f} MB, "
                f"{time.perf_counter() - t0:.1f} s")
    xyz, rgb, cnt = acc.result(has_color=has_color)
    return xyz, rgb, {"files": len(paths), "points_in": acc.points_in, "voxels": int(len(xyz)), "voxel_m": voxel}


def poisson_mesh(xyz, rgb, cfg, normals=None, log=print):
    """-> (vertices float32, colours float32 0..1 | None, triangles uint32, stats)."""
    o3d = open3d()
    res = hardware.resources()
    need_gb = len(xyz) * POISSON_BYTES_PER_POINT.get(cfg["poisson_depth"], 2000) / 2**30
    avail = hardware.plannable_ram_gb(res)
    if need_gb > avail:
        raise MemoryError(f"Poisson depth {cfg['poisson_depth']} on {len(xyz):,} points needs ~{need_gb:.1f} GB; "
                          f"{avail:.1f} GB can be planned now. Use a larger voxel / lower depth (preview profile), or "
                          "close other programs.")
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.asarray(xyz, np.float64)))
    if rgb is not None:
        pcd.colors = o3d.utility.Vector3dVector(np.asarray(rgb, np.float64) / 255.0)
    if normals is not None:
        pcd.normals = o3d.utility.Vector3dVector(np.asarray(normals, np.float64))
    else:
        pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=cfg["voxel_m"] * 4, max_nn=30))
    # sign of the normals: towards the cloud centre, i.e. into the room for a scan made from inside (heuristic;
    # with known sensor positions orient towards those instead)
    pcd.orient_normals_towards_camera_location(np.asarray(xyz, np.float64).mean(0))
    t0 = time.perf_counter()
    mesh, dens = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=cfg["poisson_depth"],
                                                                           n_threads=res.cpu_threads)
    dens = np.asarray(dens)
    # drop the low-density shell Poisson extrapolates where there were no points
    mesh.remove_vertices_by_mask(dens < np.quantile(dens, cfg["density_trim"]))
    mesh = mesh.crop(pcd.get_axis_aligned_bounding_box())
    if len(mesh.triangles) > cfg["target_triangles"]:
        mesh = mesh.simplify_quadric_decimation(cfg["target_triangles"])
    mesh.remove_degenerate_triangles()
    mesh.remove_unreferenced_vertices()
    V = np.asarray(mesh.vertices, np.float32)
    C = np.asarray(mesh.vertex_colors, np.float32) if mesh.has_vertex_colors() else None
    F = np.asarray(mesh.triangles, np.uint32)
    stats = {"poisson_depth": cfg["poisson_depth"], "vertices": int(len(V)), "triangles": int(len(F)),
             "seconds": round(time.perf_counter() - t0, 1), "estimated_peak_gb": round(need_gb, 2)}
    if log:
        log(f"Poisson: {stats}")
    return V, C, F, stats


def write_vertex_color_glb(path, V, C, F, name="lidar_mesh"):
    """Unlit vertex-coloured glTF binary; the Namma viewer shows vertex colours when there is no texture map."""
    C = np.full((len(V), 3), 0.6, np.float32) if C is None else np.asarray(C, np.float32)
    pos, col, idx = V.astype("<f4").tobytes(), C.astype("<f4").tobytes(), F.astype("<u4").tobytes()
    doc = {"asset": {"version": "2.0", "generator": "namma.lidar.mesh (experimental)"},
           "extensionsUsed": ["KHR_materials_unlit"], "scene": 0, "scenes": [{"nodes": [0]}],
           "nodes": [{"mesh": 0, "name": name}],
           "materials": [{"name": name, "doubleSided": True, "extensions": {"KHR_materials_unlit": {}},
                          "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0, "roughnessFactor": 1}}],
           "meshes": [{"name": name, "primitives": [{"attributes": {"POSITION": 0, "COLOR_0": 1}, "indices": 2,
                                                     "material": 0, "mode": 4}]}],
           "buffers": [{"byteLength": len(pos) + len(col) + len(idx)}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "target": 34962},
                           {"buffer": 0, "byteOffset": len(pos), "byteLength": len(col), "target": 34962},
                           {"buffer": 0, "byteOffset": len(pos) + len(col), "byteLength": len(idx), "target": 34963}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(V), "type": "VEC3",
                          "min": V.min(0).tolist(), "max": V.max(0).tolist()},
                         {"bufferView": 1, "componentType": 5126, "count": len(C), "type": "VEC3"},
                         {"bufferView": 2, "componentType": 5125, "count": int(F.size), "type": "SCALAR"}]}
    return write_glb(path, doc, pos + col + idx)


def build_preview_mesh(ply_paths, out_dir, profile="preview", log=print):
    """Whole experimental route: fuse -> outlier filter -> Poisson -> GLB + a JSON record of every number."""
    cfg = LIDAR_PROFILES[profile]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    xyz, rgb, fstats = fuse_ply_files(ply_paths, cfg["voxel_m"], log=log)
    keep = pointcloud.statistical_outlier_mask(xyz, k=cfg["outlier_k"], std_ratio=cfg["outlier_std"])
    xyz, rgb = xyz[keep], (rgb[keep] if rgb is not None else None)
    write_ply_xyz(out / "fused_points.ply", xyz, rgb)
    V, C, F, mstats = poisson_mesh(xyz, rgb, cfg, log=log)
    write_vertex_color_glb(out / "lidar_mesh.glb", V, C, F)
    rec = {"status": "experimental", "profile": profile, "config": cfg, "fuse": fstats,
           "outliers_removed": int((~keep).sum()), "points_meshed": int(len(xyz)), "mesh": mstats,
           "seconds_total": round(time.perf_counter() - t0, 1), "outputs": ["fused_points.ply", "lidar_mesh.glb"],
           "not_validated": "synthetic tests only; coordinate frame, units and axes of a real export unverified"}
    (out / "lidar_mesh.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec
