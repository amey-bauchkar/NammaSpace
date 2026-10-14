"""Phase 13 reconstruction utilities on SYNTHETIC inputs: scale-free model frame, roaming grid,
GLB texture embedding, quality verdicts, COLMAP text parsing. Not evidence about real data.
"""
import io
import json
import math
import struct

import cv2
import numpy as np
import pytest
from PIL import Image

from namma.recon import colmap, glb, modelframe, quality


def rot(axis, deg):
    v = np.asarray(axis, float) / np.linalg.norm(axis)
    return cv2.Rodrigues(v * math.radians(deg))[0]


def synthetic_room(rng, n_floor=40000, n_wall=20000):
    """Room 6 x 4, floor y=0, walls to y=2.6, a table 1 x 0.6 at height 0.75; cameras at ~1.4 looking around."""
    floor = np.column_stack([rng.uniform(0, 6, n_floor), np.zeros(n_floor), rng.uniform(0, 4, n_floor)])
    walls = []
    for _ in range(n_wall):
        side = rng.integers(4)
        t, y = rng.uniform(0, 1), rng.uniform(0, 2.6)
        walls.append([[t * 6, y, 0], [t * 6, y, 4], [0, y, t * 4], [6, y, t * 4]][side])
    table = np.column_stack([rng.uniform(2.5, 3.5, 3000), np.full(3000, 0.75), rng.uniform(1.7, 2.3, 3000)])
    pts = np.vstack([floor, np.array(walls), table])
    pts += rng.normal(scale=0.003, size=pts.shape)
    cams, rots = [], []
    for k in range(24):
        a = 2 * math.pi * k / 24
        c = np.array([3 + 2.0 * math.cos(a), 1.4 + rng.normal(scale=0.05), 2 + 1.2 * math.sin(a)])
        fwd = np.array([math.cos(a), -0.15, math.sin(a)])      # looking outward to the walls, slightly down
        fwd /= np.linalg.norm(fwd)
        up = np.array([0.0, 1.0, 0.0])
        right = np.cross(fwd, up)
        right /= np.linalg.norm(right)
        cam_up = np.cross(right, fwd)
        # COLMAP camera: +X right, +Y down, +Z forward; rows of R (world->camera) are those axes in world coords
        R = np.vstack([right, -cam_up, fwd])
        cams.append(c)
        rots.append(R)
    return pts, np.array(cams), rots


def to_sfm(pts, cams, rots, R, s, t):
    """Express the room in an arbitrary SfM frame: x_sfm = s * R x + t."""
    p = s * pts @ R.T + t
    c = s * cams @ R.T + t
    r = [Rc @ R.T for Rc in rots]
    return p, c, r


@pytest.mark.parametrize("scale", [1.0, 0.137, 7.3])
def test_model_frame_recovers_gravity_floor_and_is_scale_free(scale):
    rng = np.random.default_rng(4)
    pts, cams, rots = synthetic_room(rng)
    R, t = rot([0.3, 1.0, -0.4], 63), np.array([5.0, -2.0, 1.0])
    p, c, r = to_sfm(pts, cams, rots, R, scale, t)
    T, meta = modelframe.model_frame(p, c, r)
    assert meta["floor_detected"] and meta["up_source"] == "floor_plane"
    assert meta["floor_suspect"] is False and meta["below_floor_fraction"] < 0.01
    pm = modelframe.transform_points(T, p)
    cm = modelframe.transform_points(T, c)
    # the floor is y = 0 and cameras are ~1.4 * scale above it
    floor_pts = pm[: 40000]
    assert np.median(np.abs(floor_pts[:, 1])) < 0.01 * scale
    assert meta["eye_height_units"] == pytest.approx(1.4 * scale, rel=0.05)
    assert np.all(cm[:, 1] > 0)
    # rigid: distances preserved
    assert np.linalg.norm(pm[0] - pm[1]) == pytest.approx(np.linalg.norm(p[0] - p[1]), rel=1e-9)
    # up vector of the model frame equals world up of the room within 1 degree
    up_sfm = R @ np.array([0, 1.0, 0])
    y_axis_model_in_sfm = T[1, :3]
    assert math.degrees(math.acos(min(1, float(y_axis_model_in_sfm @ up_sfm)))) < 1.0


def test_model_frame_without_floor_falls_back_to_camera_orientation():
    rng = np.random.default_rng(5)
    pts, cams, rots = synthetic_room(rng)
    walls_only = pts[40000:60000]
    walls_only = walls_only[walls_only[:, 1] > 0.5]
    T, meta = modelframe.model_frame(walls_only, cams, rots)
    assert meta["up_source"] in ("camera_orientation", "floor_plane")
    if meta["up_source"] == "camera_orientation":
        assert not meta["floor_detected"]
    # up remains within ~10 degrees of gravity in either case
    assert T[1, 1] > math.cos(math.radians(10))


def test_roam_grid_blocks_table_and_walls_but_allows_open_floor():
    rng = np.random.default_rng(6)
    pts, cams, _ = synthetic_room(rng)
    grid, stats, masks = modelframe.roam_grid(pts, cams, 1.4)
    res, (x0, z0) = grid["cell"], grid["origin_xz"]

    def cell(x, z):
        return int((z - z0) // res), int((x - x0) // res)
    assert masks["blocked"][cell(3.0, 2.0)]                 # table
    assert masks["allowed"][cell(1.0, 1.0)]                 # open floor
    assert not masks["allowed"][cell(0.0, 2.0)]             # wall
    assert not masks["allowed"][cell(-2.0, 2.0)] if cell(-2.0, 2.0)[1] >= 0 else True
    # RLE rows decode back to the mask
    for r, runs in enumerate(grid["rows"][:50]):
        assert sum(runs) == grid["width"]
        row, c, v = [], 0, False
        for n in runs:
            row += [v] * n
            v = not v
        assert row == masks["allowed"][r].tolist()


def _write_glb_with_external_png(tmp_path):
    png = tmp_path / "mesh_0.png"
    Image.fromarray(np.full((64, 64, 3), (200, 100, 50), np.uint8)).save(png)
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], np.float32).tobytes()
    uvs = np.array([[0, 0], [1, 0], [0, 1]], np.float32).tobytes()
    idx = np.array([0, 1, 2], np.uint32).tobytes()
    binary = positions + uvs + idx
    doc = {"asset": {"version": "2.0", "generator": "OpenMVS"}, "buffers": [{"byteLength": len(binary)}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}, {"buffer": 0, "byteOffset": 36, "byteLength": 24},
                           {"buffer": 0, "byteOffset": 60, "byteLength": 12}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
                         {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2"},
                         {"bufferView": 2, "componentType": 5125, "count": 3, "type": "SCALAR"}],
           "images": [{"name": "C:/Users/someone/private/path/mesh_0", "uri": "mesh_0.png"}],
           "samplers": [{}], "textures": [{"name": "texture", "sampler": 0, "source": 0}],
           "materials": [{"name": "material", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
           "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0}]}],
           "nodes": [{"mesh": 0}], "scenes": [{"nodes": [0]}], "scene": 0}
    src = tmp_path / "mesh.glb"
    glb.write_glb(src, doc, binary)
    return src, binary


def test_embed_textures_makes_glb_self_contained_and_strips_paths(tmp_path):
    src, binary = _write_glb_with_external_png(tmp_path)
    dst = tmp_path / "out.glb"
    st = glb.embed_textures(src, dst, max_side=32)
    doc, b = glb.read_glb(dst)
    assert "uri" not in doc["images"][0] and doc["images"][0]["mimeType"] == "image/jpeg"
    assert "someone" not in json.dumps(doc)
    assert b[:72] == binary[:72]                       # geometry bytes untouched
    v = doc["bufferViews"][doc["images"][0]["bufferView"]]
    im = Image.open(io.BytesIO(b[v["byteOffset"]:v["byteOffset"] + v["byteLength"]]))
    assert im.size == (32, 32)
    assert st["triangles"] == 1 and st["textures"][0]["source_size"] == [64, 64]
    total = struct.unpack("<I", dst.read_bytes()[8:12])[0]
    assert total == dst.stat().st_size and total % 4 == 0


def test_embed_textures_refuses_missing_texture(tmp_path):
    src, _ = _write_glb_with_external_png(tmp_path)
    (tmp_path / "mesh_0.png").unlink()
    with pytest.raises(FileNotFoundError):
        glb.embed_textures(src, tmp_path / "x.glb")


def test_mesh_components_counts_pieces():
    faces = np.array([[0, 1, 2], [1, 2, 3], [4, 5, 6]])
    n, largest, tiny = quality.mesh_components(faces, 7)
    assert n == 2 and largest == pytest.approx(2 / 3)


def base_facts(**kw):
    f = {"images_input": 40, "images_registered": 40, "unregistered": [], "other_models": 0, "low_texture_images": [],
         "sparse_points": 40 * 1000, "mean_reprojection_px": 0.6, "dense_fallback": False, "floor_detected": True,
         "floor_coverage": 0.8, "mesh_largest_component_share": 0.97, "mesh_tiny_components": 3, "scale_status": "unknown"}
    f.update(kw)
    return f


def test_quality_verdicts():
    assert quality.assess(base_facts())[0] == "good"
    v, w = quality.assess(base_facts(images_registered=39, unregistered=["a.jpg"]))
    assert v == "usable_with_warnings" and any(x["code"] == "REG-MISSING" for x in w)
    v, w = quality.assess(base_facts(images_registered=20))
    assert v == "poor"
    assert quality.assess(base_facts(images_registered=2))[0] == "failed"
    v, w = quality.assess(base_facts(floor_coverage=0.3))
    assert any(x["code"] == "FLOOR-COVERAGE-LOW" for x in w)
    codes = {x["code"] for x in quality.assess(base_facts())[1]}
    assert {"SCALE-UNKNOWN", "DOORWAYS-NOT-EVALUATED"} <= codes          # always disclosed


def test_colmap_images_txt_handles_images_without_points(tmp_path):
    p = tmp_path / "images.txt"
    p.write_text("# header\n"
                 "1 1 0 0 0 0 0 0 1 a.jpg\n"
                 "10 20 5 30 40 -1\n"
                 "2 1 0 0 0 1 2 3 1 b.jpg\n"
                 "\n"
                 "3 1 0 0 0 4 5 6 1 c d.jpg\n"
                 "1 2 -1\n", encoding="utf-8")
    imgs = colmap.read_images_txt(p)
    assert set(imgs) == {"a.jpg", "b.jpg", "c d.jpg"}
    assert imgs["a.jpg"]["n_triangulated"] == 1 and imgs["b.jpg"]["n_points2d"] == 0
    assert np.allclose(imgs["b.jpg"]["C"], [-1, -2, -3])


def test_runner_follows_tool_log_file_and_enforces_timeout(tmp_path):
    """OpenMVS writes progress to its own log file; run_step must stream it, and kill a step over budget."""
    import sys

    from namma.recon.runner import StepFailed, run_step
    script = ("import time\n"
              "with open('Tool-123.log', 'w') as f:\n"
              "    for i in range(5):\n"
              "        f.write(f'Depth-map for image {i} estimated' + chr(10)); f.flush(); time.sleep(0.4)\n")
    seen = []
    rec = run_step("tool", [sys.executable, "-c", script], tmp_path / "logs", timeout_s=30, cwd=tmp_path,
                   on_line=seen.append, follow=(tmp_path, "Tool-*.log"))
    assert rec["status"] == "ok"
    assert sum("Depth-map" in s for s in seen) >= 3
    with pytest.raises(StepFailed) as e:
        run_step("slow", [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path / "logs", timeout_s=1)
    assert e.value.record["status"] == "timeout"
    (tmp_path / "CANCEL").write_text("x")
    with pytest.raises(StepFailed) as e:
        run_step("cancel", [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path / "logs", timeout_s=60,
                 cancel_file=tmp_path / "CANCEL")
    assert e.value.record["status"] == "cancelled"


def test_mesh_cleaning_removes_bridging_faces_and_fragments(tmp_path):
    from namma.recon.io import read_ply
    from namma.recon.meshclean import clean_faces, clean_mesh_file, write_ply_mesh
    # a 20 x 20 grid of small triangles (main surface), one huge bridging triangle, one tiny far fragment
    n = 20
    xs, zs = np.meshgrid(np.arange(n + 1) * 0.05, np.arange(n + 1) * 0.05)
    verts = np.column_stack([xs.ravel(), np.zeros(xs.size), zs.ravel()])
    faces = []
    for r in range(n):
        for c in range(n):
            a = r * (n + 1) + c
            faces += [[a, a + 1, a + n + 1], [a + 1, a + n + 2, a + n + 1]]
    base = len(verts)
    verts = np.vstack([verts, [[5, 0, 5], [6, 0, 5], [5, 0, 6]]])           # far tiny fragment (one small face)
    verts[base + 1] = [5.05, 0, 5]
    verts[base + 2] = [5, 0, 5.05]
    faces.append([base, base + 1, base + 2])
    faces.append([0, n, base])                                                # bridging sheet to the fragment
    faces = np.array(faces)
    v2, f2, st = clean_faces(verts, faces, edge_factor=6.0, min_component_share=0.01)
    assert st["long_edge_removed"] == 1 and st["small_component_faces_removed"] == 1
    assert len(f2) == 2 * n * n and len(v2) == (n + 1) ** 2
    src = tmp_path / "m.ply"
    write_ply_mesh(src, verts, faces)
    st2 = clean_mesh_file(src, tmp_path / "c.ply", edge_factor=6.0, min_component_share=0.01)
    d = read_ply(tmp_path / "c.ply")
    assert len(d["faces"]) == st2["faces_out"] == 2 * n * n


def test_model_score_prefers_supported_models():
    from namma.recon.colmap import model_score
    # Phase 13 ARKit case: 28 images / 47 points must lose to 20 images / 1876 points
    assert model_score(20, 1876) > model_score(28, 47)
    assert model_score(30, 12000) == 30


def test_dense_subset_is_even_and_prefers_sharp_frames():
    from namma.studio.pipeline import dense_subset
    names = [f"frame_{i:04d}.jpg" for i in range(300)]
    sharp = {f"frame_{i:04d}": float(i % 3) for i in range(300)}       # every third frame is the sharpest
    sub = dense_subset(names, sharp, 90)
    assert len(sub) == 90 and sub == sorted(sub)
    idx = [int(n[6:10]) for n in sub]
    assert max(b - a for a, b in zip(idx, idx[1:])) <= 6               # evenly spread over the video
    assert sum(1 for i in idx if i % 3 == 2) >= 80                     # sharpest frame chosen in (almost) every bin
    assert dense_subset(names[:50], sharp, 90) == names[:50]



def test_undistort_pixel_budget_keeps_validated_runs_and_shrinks_large_captures():
    from namma.studio.pipeline import undistort_side
    assert undistort_side([(6048, 4032)] * 30, 0, 750) is None          # kicker (validated amount): originals
    assert undistort_side([(1600, 1200)] * 90, 1600, None) == 1600       # preview cap passed on unchanged
    side = undistort_side([(4032, 3024)] * 140, 0, 750)                  # 140 iPhone photos (1.7 GP)
    assert 2600 < side < 2700 and 140 * 4032 * 3024 * (side / 4032) ** 2 / 1e6 <= 750
    assert undistort_side([(1920, 1440)] * 140, 0, 750) is None          # video frames fit the budget

def test_geometry_consistency_gate():
    rng = np.random.default_rng(7)
    pts, cams, _ = synthetic_room(rng)
    ok, ratio = modelframe.geometry_consistency(pts, cams)
    assert ok and ratio > 0.5
    collapsed = np.vstack([np.zeros((50000, 3)) + rng.normal(scale=1e-5, size=(50000, 3)), rng.normal(scale=50, size=(500, 3))])
    ok2, ratio2 = modelframe.geometry_consistency(collapsed, cams)
    assert not ok2 and ratio2 < 0.05


def test_sift_feature_budget_only_shrinks_for_large_preview_sets():
    # pre-capture QA R150-4b: 150 photos with 3000 features: 150/150 placed, matching 34.5 -> 12.8 min
    from namma.studio.pipeline import sift_feature_budget
    from namma.studio.profiles import get_profile
    pre, std = get_profile("preview"), get_profile("standard")
    assert sift_feature_budget(pre, 100) == 6000
    assert sift_feature_budget(pre, 101) == 3000 and sift_feature_budget(pre, 150) == 3000
    assert sift_feature_budget(std, 150) == std["sift"]["max_features"]        # Standard untested: unchanged


def test_match_progress_reads_colmap_database(tmp_path):
    import sqlite3
    import time
    from namma.studio.pipeline import _MatchProgress
    db = tmp_path / "database.db"
    con = sqlite3.connect(db)
    con.execute("create table two_view_geometries (pair_id integer)")
    con.executemany("insert into two_view_geometries values (?)", [(i,) for i in range(5)])
    con.commit(); con.close()
    seen = []
    with _MatchProgress(db, 5, seen.append, every_s=0.05):      # 5 photos -> 10 pairs
        time.sleep(0.4)
    assert seen and abs(seen[-1] - 0.5) < 1e-9



def test_sparse_weak_threshold_follows_the_feature_budget():
    from namma.recon import quality
    base = {"images_input": 150, "images_registered": 150, "sparse_points": 39000, "mean_reprojection_px": 0.5,
            "floor_detected": True, "floor_coverage": 0.6, "mesh_largest_component_share": 0.98, "scale_status": "unknown"}
    codes = lambda f: [w["code"] for w in quality.assess(f)[1]]
    assert "SPARSE-WEAK" in codes({**base, "sift_max_features": 6000})        # 260 points / photo < 300
    assert "SPARSE-WEAK" not in codes({**base, "sift_max_features": 3000})    # floor 150 with 3000 features
    assert "SPARSE-WEAK" in codes({**base, "sift_max_features": 3000, "sparse_points": 15000})
