"""Phase 12 reconstruction utilities: algorithm unit tests on SYNTHETIC inputs.

These tests check the code (alignment, readers, floor-plane / IF-04 extraction, marker
triangulation). They are not evidence about any real reconstruction.
"""
import math

import cv2
import numpy as np
import pytest

from namma.contracts.semantic import validate_document
from namma.core.issues import errors_only
from namma.fixtures import exp010
from namma.geometry.model import load_geometry
from namma.navigation.grid import build_navlayer
from namma.recon.align import apply_sim3, pose_errors, umeyama
from namma.recon.floorplan import extract_if04, fit_plane_ransac, site_frame, split_holes, to_site
from namma.recon.io import qvec_to_rotmat, read_colmap_images_txt, read_ply, write_ply_xyz
from namma.recon.markers import marker_sheet, normalized_points, register_to_site, triangulate
from shapely.geometry import Polygon


def rot(axis, deg):
    v = np.asarray(axis, float) / np.linalg.norm(axis)
    return cv2.Rodrigues(v * math.radians(deg))[0]


def test_umeyama_recovers_known_similarity():
    rng = np.random.default_rng(1)
    src = rng.normal(size=(20, 3))
    R, s, t = rot([1, 2, 3], 37), 2.5, np.array([1.0, -2.0, 0.5])
    dst = s * src @ R.T + t
    s2, R2, t2 = umeyama(src, dst)
    assert s2 == pytest.approx(s) and np.allclose(R2, R) and np.allclose(t2, t)
    assert np.allclose(apply_sim3(s2, R2, t2, src), dst)


def test_pose_errors_zero_for_similar_trajectories():
    gt, est = {}, {}
    S, Rs, ts = 0.3, rot([0, 1, 0], 50), np.array([3.0, 1.0, -1.0])
    for i in range(6):
        Rg = rot([0, 1, 0], 10 * i)
        Cg = np.array([i, 1.5 + 0.3 * math.sin(i), 0.2 * i * i])   # not collinear
        gt[f"im{i}"] = {"R": Rg, "C": Cg}
        Ce = Rs.T @ (Cg - ts) / S            # inverse similarity: est frame
        est[f"im{i}"] = {"R": Rg @ Rs, "C": Ce}
    pe = pose_errors(est, gt)
    assert pe["position_error_m"].max() < 1e-9 and pe["rotation_error_deg"].max() < 1e-4  # arccos precision near 1
    assert pe["scale"] == pytest.approx(S)


def test_ply_roundtrip_and_colmap_images(tmp_path):
    xyz = np.random.default_rng(0).normal(size=(50, 3))
    write_ply_xyz(tmp_path / "p.ply", xyz, rgb=np.zeros((50, 3)))
    v = read_ply(tmp_path / "p.ply")["vertex"]
    assert np.allclose(np.column_stack([v["x"], v["y"], v["z"]]), xyz, atol=1e-6)
    q = [math.cos(0.2), 0, math.sin(0.2), 0]
    (tmp_path / "images.txt").write_text("# header\n1 %f %f %f %f 1 2 3 1 dslr_images/A.JPG\n\n" % tuple(q),
                                         encoding="utf-8")
    im = read_colmap_images_txt(tmp_path / "images.txt")["dslr_images/A.JPG"]
    assert np.allclose(im["R"], qvec_to_rotmat(q)) and np.allclose(im["C"], -im["R"].T @ [1, 2, 3])


def synthetic_room(tilt_deg=7.0, seed=0):
    """Points of a 6 x 4 m room (floor + 2.6 m walls + a table), then rotated / shifted arbitrarily."""
    rng = np.random.default_rng(seed)
    floor = np.column_stack([rng.uniform(0, 6, 40000), np.zeros(40000), rng.uniform(0, 4, 40000)])
    walls = []
    for _ in range(4):
        n = 15000
        h = rng.uniform(0, 2.6, n)
        walls.append(np.column_stack([rng.uniform(0, 6, n), h, np.full(n, 0.0)]))
        walls.append(np.column_stack([rng.uniform(0, 6, n), h, np.full(n, 4.0)]))
        walls.append(np.column_stack([np.full(n, 0.0), h, rng.uniform(0, 4, n)]))
        walls.append(np.column_stack([np.full(n, 6.0), h, rng.uniform(0, 4, n)]))
    top = np.column_stack([rng.uniform(2, 3.2, 6000), np.full(6000, 0.75), rng.uniform(1.5, 2.3, 6000)])
    pts = np.vstack([floor, *walls, top])
    cams = np.column_stack([rng.uniform(1, 5, 12), np.full(12, 1.5), rng.uniform(1, 3, 12)])
    R, t = rot([1, 0.3, 0.2], tilt_deg) @ rot([0, 1, 0], 33), np.array([10.0, -3.0, 2.0])
    return pts @ R.T + t, cams @ R.T + t


def test_floor_plane_and_site_frame_recover_up_axis():
    pts, cams = synthetic_room()
    T, diag = site_frame(pts, cams)
    site = to_site(T, pts)
    floor_like = np.abs(site[:, 1]) < 0.02
    assert floor_like.sum() >= 39000                      # floor points land at y ~ 0
    assert diag["median_camera_height_m"] == pytest.approx(1.5, abs=0.02)
    assert np.linalg.det(T[:3, :3]) == pytest.approx(1.0)  # right-handed rigid


def test_extract_if04_is_contract_valid_and_navigable():
    pts, cams = synthetic_room()
    T, _ = site_frame(pts, cams)
    doc, diag = extract_if04(to_site(T, pts), geometry_id="geom-synth-room", run_id="run-test",
                             frame=exp010.FRAME, provenance=dict(exp010.PROV, origin="automatic"))
    assert not errors_only(validate_document(doc, "IF-04"))
    geom, issues = load_geometry(doc)
    assert geom is not None, issues
    assert 20.0 < diag["walkable_area_m2"] < 24.5
    nav = build_navlayer(geom)
    assert nav.free.sum() > 0
    # the table must be an obstacle footprint covering its true area
    union = geom.blocking_union("F0")
    tbl_centre_site = to_site(T, (np.array([[2.6, 0.75, 1.9]]) @ (rot([1, 0.3, 0.2], 7.0) @ rot([0, 1, 0], 33)).T
                                  + np.array([10.0, -3.0, 2.0])))[0]
    from shapely.geometry import Point
    assert union.contains(Point(tbl_centre_site[0], tbl_centre_site[2]))


def test_split_holes_removes_all_interiors():
    ring = Polygon([(0, 0), (6, 0), (6, 4), (0, 4)], [[(1, 1), (5, 1), (5, 3), (1, 3)]])
    parts = split_holes(ring)
    assert parts and all(not p.interiors for p in parts)
    assert sum(p.area for p in parts) == pytest.approx(ring.area)


def test_ransac_rejects_planes_outside_angle_constraint():
    pts, _ = synthetic_room(tilt_deg=0.0)
    n, d, cnt = fit_plane_ransac(pts, normal_hint=np.array([1.0, 0, 0]), max_angle_deg=10)
    assert abs(n @ np.array([1.0, 0, 0])) > math.cos(math.radians(11))


def test_marker_triangulation_and_held_out_registration():
    """Synthetic cameras observing 5 known floor points: triangulate, register, check held-out error."""
    cam = {"model": "OPENCV", "params": [1000, 1000, 640, 480, 0.05, -0.01, 0.001, 0.0005]}
    K = np.array([[1000, 0, 640], [0, 1000, 480], [0, 0, 1]], float)
    dist = np.array([0.05, -0.01, 0.001, 0.0005])
    marks_site = {0: [0, 0, 0], 1: [3, 0, 0], 2: [0, 0, 2.5], 3: [3, 0, 2.5], 4: [1.5, 0, 1.2]}
    S, Rw, tw = 0.4, rot([0.2, 1, 0.1], 25), np.array([2.0, 1.0, -1.0])   # site -> recon similarity
    marks_rec = {i: (S * Rw @ np.array(p) + tw) for i, p in marks_site.items()}
    centre = np.mean(list(marks_rec.values()), axis=0)
    up = Rw @ np.array([0.0, 1.0, 0.0])
    views = []
    for k in range(6):   # cameras 1.5 m above the floor around the markers, looking at their centroid
        ang = 2 * math.pi * k / 6
        C_site = np.array([1.5 + 2.5 * math.cos(ang), 1.5, 1.2 + 2.5 * math.sin(ang)])
        C = S * Rw @ C_site + tw
        z = (centre - C) / np.linalg.norm(centre - C)
        x = np.cross(z, up)
        x /= np.linalg.norm(x)
        Rc = np.vstack([x, np.cross(z, x), z])
        views.append((Rc, -Rc @ C))
    rec = {}
    for i, X in marks_rec.items():
        obs = []
        for Rc, t in views:
            pix, _ = cv2.projectPoints(X.reshape(1, 3), cv2.Rodrigues(Rc)[0], t, K, dist)
            obs.append((Rc, t, normalized_points(pix.reshape(-1, 2), cam)[0]))
        Xh, err = triangulate(obs)
        assert np.allclose(Xh, X, atol=1e-6) and err < 1e-6
        rec[i] = Xh.tolist()
    reg = register_to_site(rec, marks_site, fit_ids=[0, 1, 2], check_ids=[3, 4])
    assert reg["scale"] == pytest.approx(1 / S)
    assert max(reg["held_out_error_m"].values()) < 1e-6


def test_marker_sheet_is_printable_and_detectable():
    page = marker_sheet(3, "M3 test")
    from namma.recon.markers import _detector
    corners, ids, _ = _detector().detectMarkers(page)
    assert ids is not None and ids.ravel().tolist() == [3]


def test_split_holes_grid_aligned_has_no_slivers():
    from shapely.geometry import box
    from shapely.ops import unary_union
    res = 0.05
    ring = box(0, 0, 3, 2).difference(box(0.5, 0.5, 2.5, 1.5))          # 0.5 m thick wall ring
    tiny = box(0, 0, 0.3, 0.3).difference(box(0.1, 0.1, 0.15, 0.15))  # 1-cell hole -> filled
    parts = split_holes(ring, res) + split_holes(tiny, res)
    assert all(not p.interiors for p in parts)
    import shapely
    assert min(2 * shapely.maximum_inscribed_circle(p, 1e-4).length for p in parts) >= res - 1e-6
    assert unary_union(split_holes(ring, res)).area == pytest.approx(ring.area)
    assert unary_union(split_holes(tiny, res)).area == pytest.approx(0.09)  # 1-cell hole filled



def test_ply_with_variable_length_vertex_lists(tmp_path):
    """OpenMVS dense clouds carry per-point view lists; the reader must skip them correctly."""
    import struct
    pts = [(1.0, 2.0, 3.0, [0, 5]), (4.0, 5.0, 6.0, [1]), (7.0, 8.0, 9.0, [])]
    head = ("ply\nformat binary_little_endian 1.0\nelement vertex 3\nproperty float32 x\nproperty float32 y\n"
            "property float32 z\nproperty uint8 red\nproperty uint8 green\nproperty uint8 blue\n"
            "property list uint8 uint32 view_indices\nproperty list uint8 float32 view_weights\nend_header\n")
    body = b""
    for x, y, z, views in pts:
        body += struct.pack("<fffBBB", x, y, z, 1, 2, 3)
        body += struct.pack("<B", len(views)) + b"".join(struct.pack("<I", v) for v in views)
        body += struct.pack("<B", len(views)) + b"".join(struct.pack("<f", 0.5) for _ in views)
    (tmp_path / "d.ply").write_bytes(head.encode() + body)
    from namma.recon.io import ply_xyz
    assert np.allclose(ply_xyz(tmp_path / "d.ply"), [[1, 2, 3], [4, 5, 6], [7, 8, 9]])
