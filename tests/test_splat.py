"""Photoreal view (Gaussian splat of an imported Polycam scan): keyframe conversion, line-up, packaging. Synthetic only
(training itself runs in the separate GPU environment and is not part of this suite)."""
import io
import json
import zipfile

import numpy as np
from PIL import Image

from namma.studio import mesh_import as mi
from namma.studio import splat as sp
from namma.studio.splat_align import align_splat_to_mesh, read_splat_ply

from test_mesh_import import box_room


def _keyframes_zip(path, poses, depth_m=2.0, w=64, h=48, f=50.0):
    """Polycam-style 'Images' export: ARKit camera->world poses, constant depth (mm), confidence 255."""
    with zipfile.ZipFile(path, "w") as z:
        for k, T in enumerate(poses):
            stem = f"{1000 + k}"
            cam = {"width": w, "height": h, "fx": f, "fy": f, "cx": w / 2, "cy": h / 2,
                   **{f"t_{r}{c}": float(T[r][c]) for r in range(3) for c in range(4)}}
            z.writestr(f"keyframes/cameras/{stem}.json", json.dumps(cam))
            b = io.BytesIO(); Image.new("RGB", (w, h), (200, 100, 50)).save(b, "JPEG"); z.writestr(f"keyframes/images/{stem}.jpg", b.getvalue())
            b = io.BytesIO(); Image.fromarray(np.full((h // 4, w // 4), depth_m * 1000, np.uint16)).save(b, "PNG")
            z.writestr(f"keyframes/depth/{stem}.png", b.getvalue())
            b = io.BytesIO(); Image.fromarray(np.full((h // 4, w // 4), 255, np.uint8)).save(b, "PNG")
            z.writestr(f"keyframes/confidence/{stem}.png", b.getvalue())


def _quat_to_rot(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def test_keyframes_become_opencv_cameras_and_depth_points(tmp_path):
    # ARKit cameras at height 1.5 m looking along -Z (identity rotation), 12 of them along X
    poses = [np.c_[np.eye(3), [0.2 * k, 1.5, 0.0]] for k in range(12)]
    _keyframes_zip(tmp_path / "images.zip", poses)
    res = sp.keyframes_to_colmap(tmp_path / "images.zip", tmp_path / "out")
    assert res["keyframes"] == 12 and res["start_points"] > 100
    # depth 2 m straight ahead of an ARKit camera looking down -Z: every point on the plane z = -2
    P = np.array([[float(v) for v in ln.split()[1:4]] for ln in (tmp_path / "out/sparse/points3D.txt").read_text().splitlines()])
    assert np.allclose(P[:, 2], -2.0, atol=1e-4) and np.allclose(np.median(P[:, 1]), 1.5, atol=0.25)   # (every 2nd depth row: off-centre on 12 rows)
    # the written world->camera pose (OpenCV: +Z forward, +Y down) puts those points in front, at depth 2
    line = (tmp_path / "out/sparse/images.txt").read_text().splitlines()[0].split()
    R, t = _quat_to_rot([float(v) for v in line[1:5]]), np.array([float(v) for v in line[5:8]])
    Xc = P @ R.T + t
    first = np.abs(Xc[:, 0]) < 0.5
    assert np.allclose(Xc[first, 2], 2.0, atol=1e-3)
    # a world point above the camera appears ABOVE the image centre (smaller row: OpenCV y down)
    up = R @ np.array([0.0, 2.5, -2.0]) + t
    assert up[1] < 0
    assert (tmp_path / "out/images/1000.jpg").exists()


def _write_ply(path, P, opacity_logit=4.0):
    n = len(P)
    fields = ["x", "y", "z", "opacity"]
    head = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n" % n + "".join(f"property float {f}\n" for f in fields) + "end_header\n"
    data = np.c_[P, np.full(n, opacity_logit)].astype("<f4")
    path.write_bytes(head.encode("ascii") + data.tobytes())


def test_splat_is_lined_up_with_the_mesh_by_its_surface(tmp_path):
    box_room(tmp_path / "room.glb", size=(4.0, 2.7, 3.2), offset=(-2.0, -1.35, -1.6))
    glb = (tmp_path / "room.glb").read_bytes()
    V, F, _ = mi.mesh_triangles(glb)
    S, _, _ = mi.sample_surface(V, F, 30000, seed=3)
    S = S + np.random.default_rng(1).normal(0, 0.005, S.shape)          # a splat's surface is not perfect
    a = np.radians(117.0)                                               # the splat's own frame: turned and moved
    R = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    P = S @ R.T + [3.0, 0.4, -1.2]
    _write_ply(tmp_path / "s.ply", P)
    assert np.allclose(read_splat_ply(tmp_path / "s.ply")["x"], P[:, 0], atol=1e-5)
    T, fit = align_splat_to_mesh(tmp_path / "s.ply", glb, samples=60000)
    back = P @ T[:3, :3].T + T[:3, 3]
    assert np.median(np.linalg.norm(back - S, axis=1)) < 0.03          # every splat back where it came from
    assert fit["within_5cm"] > 0.9 and fit["tilt_deg"] < 1.0

    # Refining from a prior transform (e.g. pretrain T0) skips 0-tilt yaw search and directly refines
    T_perturbed = T.copy()
    T_perturbed[:3, 3] += [0.03, -0.02, 0.01]
    T2, fit2 = align_splat_to_mesh(tmp_path / "s.ply", glb, init_T=T_perturbed, samples=60000)
    assert fit2["within_5cm"] > 0.9


def test_package_carries_the_splat_with_its_transform(tmp_path):
    from namma.contracts.semantic import validate_document
    from namma.studio.package import build_scene_package
    from namma.studio.pipeline import RunContext
    from namma.core.jsonio import load_strict, write_json
    box_room(tmp_path / "room.glb")
    r = mi.import_mesh(tmp_path / "room.glb", tmp_path / "projects", title="Splat room", source_note="", log=None)
    pdir, rid = mi.Path(r["project_dir"]), r["run_id"]
    run = pdir / "runs" / rid
    _write_ply(run / "60_texture" / "splat.ply", np.random.default_rng(0).uniform(-1, 1, (500, 3)))
    Tsp = np.eye(4); Tsp[:3, 3] = [0.5, 0.0, -0.25]
    write_json(run / "60_texture" / "splat.json", {"file": "splat.ply", "T_mesh_from_splat": Tsp.tolist(), "splats": 500,
                                                   "fit": {"within_5cm": 0.9}, "method": "synthetic"})
    cfg = load_strict(run / "config.json")["config"]
    ctx = RunContext(pdir, rid, cfg)
    build_scene_package(ctx, ctx.dir("package"))
    pkg = load_strict(run / "90_package" / "scene_package.json")
    assert pkg["contract_version"] == "1.3.0"
    s = pkg["appearance"]["splat"]
    assert s["format"] == "3dgs-ply" and s["splats"] == 500 and (run / "90_package" / s["path"]).exists()
    T_model = np.array(pkg["appearance"]["to_model"]["matrix_row_major"]).reshape(4, 4)
    assert np.allclose(np.array(s["to_model"]["matrix_row_major"]).reshape(4, 4), T_model @ Tsp)
    assert not [i for i in validate_document(pkg) if i.severity == "error"]
    # without a splat the package stays 1.2.0 (unchanged for every other space)
    (run / "60_texture" / "splat.json").unlink()
    build_scene_package(ctx, ctx.dir("package"))
    assert load_strict(run / "90_package" / "scene_package.json")["contract_version"] == "1.2.0"


def test_mesh_depth_guide_sees_the_known_wall_distance(tmp_path):
    # room x in [-2, 2], z in [-1.6, 1.6]; a camera at the origin, 1 m up, looking along -Z (OpenCV world->camera:
    # camera +Z = world -Z, camera +Y = world -Y): the wall at z = -1.6 is 1.6 m away straight ahead
    box_room(tmp_path / "room.glb", size=(4.0, 2.7, 3.2), offset=(-2.0, -0.0, -1.6), table=False)
    R = np.diag([1.0, -1.0, -1.0])
    t = -R @ np.array([0.0, 1.0, 0.0])
    model = tmp_path / "sparse"
    model.mkdir()
    (model / "cameras.txt").write_text("1 PINHOLE 64 48 40 40 32 24\n")
    q = sp._rot_to_quat(R)
    (model / "images.txt").write_text(f"1 {' '.join(map(str, q))} {' '.join(map(str, t))} 1 cam.jpg\n\n")
    n = sp.mesh_depth_maps(model, (tmp_path / "room.glb").read_bytes(), np.eye(4), tmp_path / "depth",
                           scale=0.5, samples=400_000)
    assert n == 1
    D = np.load(tmp_path / "depth" / "cam.npy").astype(np.float32)
    assert D.shape == (24, 32) and abs(float(np.median(D[10:14, 14:18])) - 1.6) < 0.03


def test_mesh_start_points_lie_on_the_scanned_surface(tmp_path):
    """make_photoreal starts training from points spread over the mesh (24.6 vs 23.9 dB, half the time)."""
    box_room(tmp_path / "room.glb", size=(5.0, 2.8, 4.0), offset=(0.0, 0.0, 0.0), table=False)
    P, C = sp.mesh_points((tmp_path / "room.glb").read_bytes(), 5000)
    assert P.shape == (5000, 3) and C.shape == (5000, 3) and 0 <= C.min() and C.max() <= 1
    # every point is on a wall, the floor or the ceiling of the 5 x 2.8 x 4 m box
    near = lambda v, *ts: np.any([np.isclose(v, t, atol=1e-3) for t in ts], axis=0)
    on = near(P[:, 0], 0.0, 5.0) | near(P[:, 2], 0.0, 4.0) | near(P[:, 1], 0.0, 2.8)
    assert on.all()


def test_photoreal_recipe_is_bounded_for_a_6gb_rtx_4050():
    """Claude's original small-room recipe is preserved verbatim from the golden release."""
    from namma.studio import splat_claude
    assert splat_claude.MIN_WITHIN_5CM == 0.30
    assert splat_claude.MIN_SPLAT_OPACITY == 0.02
    assert sp.MIN_WITHIN_5CM == 0.30


def test_large_photoreal_recipe():
    """The enhanced large-space recipe bounds scale and guards against dropped keyframes."""
    from namma.studio import splat_large
    assert splat_large.MAX_SPLAT_SCALE_M == 0.20
    assert splat_large.RTX_4050_SAFE_CAP == 600_000
    assert splat_large.MIN_COLMAP_KEYFRAME_RATIO == 0.95
    assert splat_large.MAX_COLMAP_RESIDUAL_M == 0.20
    assert splat_large.MIN_PRETRAIN_WITHIN_5CM == 0.20
    assert splat_large.MAX_PRETRAIN_TILT_DEG == 10.0


def test_classify_room_profile(tmp_path):
    """The dispatcher correctly routes small rooms to Claude and large spaces to the enhanced engine."""
    from namma.core.jsonio import write_json
    # 1. Small room: 3.5 x 2.8 x 3.2 m -> ~11 m^2 footprint, 5.5 m diagonal, 12 photos -> "claude"
    p_small = tmp_path / "small_proj"
    r_small = p_small / "runs" / "run_01"
    (r_small / "10_mesh").mkdir(parents=True)
    box_room(r_small / "10_mesh" / "model.glb", size=(3.5, 2.8, 3.2))
    prof_small, met_small = sp.classify_room_profile(p_small, "run_01")
    assert prof_small == "claude"
    assert met_small["footprint_area_m2"] <= 20.0
    assert met_small["bounding_box_diagonal_m"] <= 7.0

    # 2. Large space: 8.0 x 3.0 x 6.0 m -> 48 m^2 footprint, 10.4 m diagonal -> "large"
    p_large = tmp_path / "large_proj"
    r_large = p_large / "runs" / "run_01"
    (r_large / "10_mesh").mkdir(parents=True)
    box_room(r_large / "10_mesh" / "model.glb", size=(8.0, 3.0, 6.0))
    prof_large, met_large = sp.classify_room_profile(p_large, "run_01")
    assert prof_large == "large"
    assert met_large["footprint_area_m2"] > 20.0

    # 3. Manual override in config.json
    write_json(r_small / "config.json", {"config": {"splat_profile": "large"}})
    prof_ov, met_ov = sp.classify_room_profile(p_small, "run_01")
    assert prof_ov == "large" and met_ov["override"] is True

    write_json(r_large / "config.json", {"config": {"splat_profile": "claude"}})
    prof_ov2, met_ov2 = sp.classify_room_profile(p_large, "run_01")
    assert prof_ov2 == "claude" and met_ov2["override"] is True


