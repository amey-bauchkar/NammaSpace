"""LiDAR export inventory / validation, PLY reading and point-cloud fusion.

All inputs are SYNTHETIC files made here. They test parsing, classification and safety rules; they say nothing
about the layout or quality of a real Record3D export (that is inspected when the real capture arrives).
"""
import json
import zipfile

import numpy as np
import pytest
from PIL import Image

from namma.lidar import inventory, ply, pointcloud


def write_ply(path, xyz, rgb=None, fmt="binary_little_endian", normals=None, comments=(), faces=None):
    names = [("x", "f4"), ("y", "f4"), ("z", "f4")]
    if normals is not None:
        names += [("nx", "f4"), ("ny", "f4"), ("nz", "f4")]
    if rgb is not None:
        names += [("red", "u1"), ("green", "u1"), ("blue", "u1")]
    end = ">" if fmt == "binary_big_endian" else "<"
    arr = np.empty(len(xyz), [(n, end + t) for n, t in names])
    for i, n in enumerate("xyz"):
        arr[n] = xyz[:, i]
    if normals is not None:
        for i, n in enumerate(("nx", "ny", "nz")):
            arr[n] = normals[:, i]
    if rgb is not None:
        for i, n in enumerate(("red", "green", "blue")):
            arr[n] = rgb[:, i]
    head = ["ply", f"format {fmt} 1.0", *[f"comment {c}" for c in comments], f"element vertex {len(xyz)}"]
    head += [f"property {'float' if t == 'f4' else 'uchar'} {n}" for n, t in names]
    if faces is not None:
        head += [f"element face {len(faces)}", "property list uchar int vertex_indices"]
    head += ["end_header"]
    with open(path, "wb") as f:
        f.write(("\n".join(head) + "\n").encode())
        if fmt == "ascii":
            for row in arr:
                f.write((" ".join(str(v) for v in row) + "\n").encode())
        else:
            f.write(arr.tobytes())
            if faces is not None:
                for tri in faces:
                    f.write(np.uint8(3).tobytes() + np.asarray(tri, end + "i4").tobytes())


def room_points(n=4000, seed=0, offset=(0, 0, 0)):
    """Points on the walls / floor of a 4 x 2.5 x 3 m box (metres)."""
    rng = np.random.default_rng(seed)
    p = rng.uniform([0, 0, 0], [4, 2.5, 3], (n, 3))
    face = rng.integers(0, 6, n)
    for k in range(3):
        p[face == k, k] = 0
        p[face == k + 3, k] = [4, 2.5, 3][k]
    return (p + np.asarray(offset)).astype(np.float32)


@pytest.mark.parametrize("fmt", ["binary_little_endian", "binary_big_endian", "ascii"])
def test_ply_formats_round_trip(tmp_path, fmt):
    xyz = room_points(500)
    rgb = np.random.default_rng(1).integers(0, 255, (500, 3)).astype(np.uint8)
    nrm = np.tile(np.array([[0, 1, 0]], np.float32), (500, 1))
    write_ply(tmp_path / "a.ply", xyz, rgb, fmt=fmt, normals=nrm, comments=["made by test"])
    h = ply.read_header(tmp_path / "a.ply")
    s = ply.summarize_header(h)
    assert s["vertices"] == 500 and s["has_color"] and s["has_normals"] and s["comments"] == ["made by test"]
    pts = ply.read_points(tmp_path / "a.ply")
    assert np.allclose(pts["xyz"], xyz, atol=1e-5) and (pts["rgb"] == rgb).all() and np.allclose(pts["normals"], nrm)
    assert len(ply.read_points(tmp_path / "a.ply", sample=100)["xyz"]) == 100


def test_ply_errors_are_explicit(tmp_path):
    (tmp_path / "x.ply").write_bytes(b"not a ply")
    with pytest.raises(ply.PlyError, match="magic"):
        ply.read_header(tmp_path / "x.ply")
    write_ply(tmp_path / "t.ply", room_points(100))
    data = (tmp_path / "t.ply").read_bytes()
    (tmp_path / "t.ply").write_bytes(data[:-50])
    with pytest.raises(ply.PlyError, match="truncated"):
        ply.read_points(tmp_path / "t.ply")


def make_export(root, frames=5, world=True, rgb_frames=12, meta=True, depth=True):
    root.mkdir(parents=True)
    (root / "pc").mkdir()
    for i in range(frames):
        pts = room_points(3000, seed=i)
        if world:                      # each frame sees one side of the same room in a shared frame
            pts = pts[(pts[:, 0] >= i * 0.6) & (pts[:, 0] <= i * 0.6 + 2.0)]
        else:                          # sensor-relative: every frame in front of its own origin
            pts = pts[pts[:, 0] < 2.0] - np.array([0, 1.25, -0.5], np.float32)
        write_ply(root / "pc" / f"{i:04d}.ply", pts, np.full((len(pts), 3), 128, np.uint8))
    (root / "rgb").mkdir()
    for i in range(rgb_frames):
        Image.new("RGB", (192, 256), (i * 10, 50, 50)).save(root / "rgb" / f"{i}.jpg")
    if depth:
        (root / "depth").mkdir()
        for i in range(3):
            Image.fromarray(np.full((48, 64), 1000 + i, np.uint16)).save(root / "depth" / f"{i}.png")
    if meta:
        (root / "metadata.json").write_text(json.dumps({"K": [1, 0, 0, 0, 1, 0, 0, 0, 1], "w": 192, "h": 256,
                                                        "poses": [[0] * 7] * frames, "frameTimestamps": [0.0] * frames}))
    return root


def test_inventory_classifies_and_never_overclaims(tmp_path):
    exp = make_export(tmp_path / "export")
    before = sorted((p.relative_to(exp).as_posix(), p.stat().st_mtime_ns, p.stat().st_size) for p in exp.rglob("*"))
    rep = inventory.inspect_export(exp, measure=True)
    after = sorted((p.relative_to(exp).as_posix(), p.stat().st_mtime_ns, p.stat().st_size) for p in exp.rglob("*"))
    assert before == after                                    # read-only: nothing added, changed or removed
    c = rep["capabilities"]
    assert c["point_cloud"]["status"] == "available" and c["point_color"]["status"] == "available"
    assert c["point_sequence"]["status"] == "available"
    assert rep["point_clouds"]["sequences"][0]["files"] == 5 and rep["point_clouds"]["sequences"][0]["pattern"] == "pc/#.ply"
    assert c["rgb_frames"]["status"] == "available" and rep["images"]["rgb_like"] == 12
    assert rep["images"]["depth_like_16bit_or_float"] == 3
    # metadata keys that LOOK like intrinsics / poses are candidates, never "available"
    assert c["camera_intrinsics"]["status"] == "candidate" and "K" in c["camera_intrinsics"]["evidence"]
    assert c["camera_poses"]["status"] == "candidate" and c["depth_maps"]["status"] == "candidate"
    assert not rep["paths"]["lidar_rgbd_textured"]["possible_with_this_export"]
    assert rep["paths"]["lidar_point_mesh"]["possible_with_this_export"]
    assert rep["recommendation"]["runnable_now"][0] in ("photo_colmap", "lidar_point_mesh")
    assert c["shared_coordinate_frame"]["status"] == "candidate"
    assert rep["measured"]["extent_plausible_for_a_room_in_metres"] is True


def test_sensor_relative_sequence_is_not_called_fusable(tmp_path):
    rep = inventory.inspect_export(make_export(tmp_path / "e", world=False), measure=True)
    assert rep["capabilities"]["shared_coordinate_frame"]["status"] == "missing"
    assert not rep["paths"]["lidar_point_mesh"]["possible_with_this_export"]          # never fuse such frames
    assert "shared_coordinate_frame" in rep["paths"]["lidar_point_mesh"]["missing"]
    assert rep["paths"]["lidar_sequence_registration"]["implemented"].startswith("designed")


def test_camera_frustum_frames_with_varying_content_are_sensor_relative():
    # mirrors the measured pattern of a real Record3D "Zipped PLY" export (see pointcloud.sequence_frame_check):
    # each frame a different view in front of the origin along -Z; extents vary, centroids stay on the axis
    rng = np.random.default_rng(3)
    frames = []
    for i in range(10):
        w, d = rng.uniform(0.5, 1.2), rng.uniform(0.9, 1.6)
        n = 5000
        z = -rng.uniform(d, d + rng.uniform(0.3, 1.2), n)
        frames.append(np.column_stack([rng.uniform(-w, w, n) * -z / 1.5, rng.uniform(-w, w, n) * -z / 1.5, z]))
    r = pointcloud.sequence_frame_check(frames, workers=1)
    assert "sensor-relative" in r["interpretation"] and r["frames_on_one_viewing_axis"] >= 0.9
    # the same frames placed along a walk through a room (shared frame) are not called sensor-relative
    walked = [f + np.array([0.8 * i, 0, -0.5 * i]) for i, f in enumerate(frames)]
    assert "sensor-relative" not in pointcloud.sequence_frame_check(walked, workers=1)["interpretation"]


def test_truncated_zip_is_reported_and_complete_members_are_inventoried(tmp_path):
    exp = make_export(tmp_path / "e", frames=4, rgb_frames=0, meta=False, depth=False)
    z = tmp_path / "cut.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted((exp / "pc").glob("*.ply")):
            zf.write(p, p.name)
    data = z.read_bytes()
    cut = tmp_path / "truncated.zip"
    cut.write_bytes(data[: int(len(data) * 0.8)])           # central directory and the last member's tail lost
    rep = inventory.inspect_export(cut, measure=True)
    assert rep["source"]["kind"] == "zip_damaged"
    d = rep["damaged_archive"]
    assert d["members_complete"] == 3 and d["member_cut_off"] == "0003.ply"
    assert any(p["code"] == "ZIP-DAMAGED" and p["severity"] == "error" for p in rep["problems"])
    assert rep["point_clouds"]["ply_files"] == 3 and rep["capabilities"]["point_cloud"]["status"] == "available"
    assert len(rep["measured"]["files"]) == 3 and "error" not in rep["measured"]["files"][0]   # measured in memory
    assert not (tmp_path / "0000.ply").exists()


def test_coloured_point_cloud_alone_is_not_an_rgbd_dataset(tmp_path):
    d = tmp_path / "only_ply"
    d.mkdir()
    write_ply(d / "scan.ply", room_points(2000), np.full((2000, 3), 200, np.uint8))
    rep = inventory.inspect_export(d)
    c = rep["capabilities"]
    assert c["point_sequence"]["status"] == "single"
    for k in ("rgb_frames", "camera_intrinsics", "camera_poses", "depth_maps", "video"):
        assert c[k]["status"] == "missing", k
    assert rep["recommendation"]["best_feasible"] == "lidar_point_mesh"
    assert set(rep["paths"]["lidar_rgbd_textured"]["missing"]) == {"rgb_frames", "camera_intrinsics", "camera_poses", "depth_maps"}
    assert "cannot be photo-textured" in rep["recommendation"]["explanation"]


def test_archive_and_r3d_like_container_are_read_in_place(tmp_path):
    exp = make_export(tmp_path / "e", frames=2, rgb_frames=0, meta=False, depth=False)
    for name in ("export.zip", "capture.r3d"):           # any zip container is opened by content, not extension
        z = tmp_path / name
        with zipfile.ZipFile(z, "w") as zf:
            for p in exp.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(exp).as_posix())
        rep = inventory.inspect_export(z)
        assert rep["source"]["kind"] == "zip" and rep["point_clouds"]["ply_files"] == 2
        assert rep["capabilities"]["rgb_frames"]["status"] == "missing"


def test_empty_and_unusable_exports_report_errors(tmp_path):
    (tmp_path / "empty").mkdir()
    codes = [p["code"] for p in inventory.inspect_export(tmp_path / "empty")["problems"]]
    assert "EXP-EMPTY" in codes and "EXP-NOTHING-USABLE" in codes
    d = tmp_path / "bad"
    d.mkdir()
    (d / "broken.ply").write_bytes(b"ply\nformat weird 1.0\nend_header\n")
    rep = inventory.inspect_export(d)
    assert rep["point_clouds"]["unreadable"] == 1 and any(p["code"] == "PLY-UNREADABLE" for p in rep["problems"])
    assert rep["capabilities"]["point_cloud"]["status"] == "missing"


def test_working_copy_is_verified_and_refuses_unsafe_targets(tmp_path):
    exp = make_export(tmp_path / "e", frames=2, rgb_frames=1)
    with pytest.raises(ValueError):
        inventory.make_working_copy(exp, exp / "copy")
    doc = inventory.make_working_copy(exp, tmp_path / "work")
    assert len(doc["files"]) == len([p for p in exp.rglob("*") if p.is_file()])
    assert (tmp_path / "work.manifest.json").exists()
    with pytest.raises(FileExistsError):
        inventory.make_working_copy(exp, tmp_path / "work")
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../escape.txt", "x")
    with pytest.raises(ValueError, match="unsafe"):
        inventory.make_working_copy(z, tmp_path / "w2")
    assert not (tmp_path / "escape.txt").exists()


def test_voxel_accumulator_equals_one_shot_downsample():
    a, b = room_points(5000, 1), room_points(5000, 2)
    ca = np.random.default_rng(0).integers(0, 255, (5000, 3)).astype(np.uint8)
    cb = np.random.default_rng(1).integers(0, 255, (5000, 3)).astype(np.uint8)
    acc = pointcloud.VoxelAccumulator(0.05)
    acc.add(a, ca)
    acc.add(b, cb)
    x1, c1, n1 = acc.result()
    x2, c2, n2 = pointcloud.voxel_downsample(np.vstack([a, b]), 0.05, np.vstack([ca, cb]))
    o1, o2 = np.lexsort(x1.T), np.lexsort(x2.T)
    assert np.allclose(x1[o1], x2[o2], atol=1e-5) and (n1[o1] == n2[o2]).all()
    assert np.abs(c1[o1].astype(int) - c2[o2].astype(int)).max() <= 1
    assert acc.points_in == 10000 and acc.frames == 2


def test_outlier_mask_removes_isolated_points():
    pts = np.vstack([room_points(3000), np.array([[20, 20, 20], [-15, 3, 9]], np.float32)])
    keep = pointcloud.statistical_outlier_mask(pts, k=8, std_ratio=2.0, workers=1)
    assert not keep[-1] and not keep[-2] and keep[:-2].mean() > 0.95


def test_poisson_mesh_on_synthetic_room(tmp_path):
    pytest.importorskip("open3d")
    from namma.lidar import mesh
    from namma.recon.glb import read_glb
    d = tmp_path / "pc"
    d.mkdir()
    for i in range(2):
        write_ply(d / f"{i}.ply", room_points(40000, seed=i), np.full((40000, 3), 90 + 60 * i, np.uint8))
    rec = mesh.build_preview_mesh(sorted(d.glob("*.ply")), tmp_path / "out", profile="preview", log=None)
    assert rec["status"] == "experimental" and rec["mesh"]["triangles"] > 1000
    doc, _ = read_glb(tmp_path / "out" / "lidar_mesh.glb")
    prim = doc["meshes"][0]["primitives"][0]
    assert "COLOR_0" in prim["attributes"] and doc["accessors"][0]["max"][1] <= 2.6
