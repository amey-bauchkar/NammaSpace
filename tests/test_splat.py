"""Photoreal view (Gaussian splat of an imported Polycam scan): keyframe conversion, line-up, packaging. Synthetic only
(training itself runs in the separate GPU environment and is not part of this suite)."""
import io
import json
import zipfile
from pathlib import Path

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


def test_splat_from_recon_dispatch_and_availability(tmp_path, monkeypatch):
    """Reconstructed photo runs (without Polycam) can generate a photoreal splat from COLMAP."""
    from namma.core.jsonio import write_json
    from namma.studio.jobs import JobManager
    from namma.studio.projects import ProjectStore

    store = ProjectStore(tmp_path / "projects")
    doc = store.create(title="SfM Space")
    pid = doc["project_id"]
    rid = "run-20261102-120000"
    rdir = store.path(pid) / "runs" / rid
    rdir.mkdir(parents=True)
    write_json(rdir / "config.json", {"config": {"profile": "standard"}})

    jobs = JobManager(store)
    # 1. Before reconstruction stages complete: splat is unavailable
    st = jobs.splat_status(pid, rid)
    assert not st["available"]

    # 2. When 30_undistorted and 70_frame exist: splat becomes available
    (rdir / "30_undistorted" / "images").mkdir(parents=True)
    (rdir / "30_undistorted" / "images" / "im0.jpg").write_bytes(b"dummy")
    (rdir / "70_frame").mkdir(parents=True)
    write_json(rdir / "70_frame" / "frame.json", {
        "T_model_from_sfm": np.eye(4).tolist(),
        "eye_height_units": 1.6
    })

    # Mock sp.available() to (True, None) in case tools/splat-env is checked
    monkeypatch.setattr(sp, "available", lambda: (True, None))
    st2 = jobs.splat_status(pid, rid)
    assert st2["available"] is True

    # 3. Test make_photoreal dispatches to make_photoreal_from_recon on non-import profiles
    called = []
    def mock_recon(pdir, r_id, **kw):
        called.append((pdir, r_id))
        return {"package_id": "test_pkg", "splats": 1000}

    monkeypatch.setattr(sp, "make_photoreal_from_recon", mock_recon)
    res = sp.make_photoreal(store.path(pid), rid)
    assert len(called) == 1 and called[0] == (store.path(pid), rid)
    assert res["package_id"] == "test_pkg"





def test_large_capture_training_plan_leaves_rooms_unchanged():
    """Large spaces train longer with capped start sizes and a room-sized position learning rate; a normal room's
    recipe (scored on held-out photos) must not change."""
    from namma.studio import splat_large as sl
    assert sl.training_plan(80, 20000) == (20000, [])
    assert sl.training_plan(sl.LARGE_MIN_PHOTOS, 20000) == (20000, [])
    iters, extra = sl.training_plan(300, 20000)
    assert iters == 30000 and extra[0] == "--init-scale-max-mult" and extra[2] == "--means-lr-scene-cap"
    assert sl.training_plan(900, 20000)[0] == sl.LARGE_MAX_ITERS            # bounded: fits the 3 h time limit
    assert sl.training_plan(160, 20000)[0] == 20000                         # never fewer steps than asked for
    trainer = (sl.TRAINER).read_text(encoding="utf-8")
    assert all(flag in trainer for flag in extra[::2])                       # the trainer knows every flag passed


def test_depth_guide_drops_pixels_that_see_through_a_near_surface(tmp_path):
    """A z-buffer of surface points leaks: through gaps in a near surface a pixel takes the far wall's depth."""
    from namma.studio import splat_large as sl
    D = np.full((12, 16), 1.0, np.float32)                   # a near surface 1 m away ...
    D[3, 4] = D[7, 9] = 3.0                                  # ... with two gaps showing the wall 3 m behind
    D[0:2, :] = 0                                            # and nothing at the top
    out = sl.drop_see_through_depth(D)
    assert out[3, 4] == 0 and out[7, 9] == 0                 # leaks are not used as a guide
    keep = np.ones_like(D, bool); keep[3, 4] = keep[7, 9] = False
    assert np.array_equal(out[keep], D[keep])                # everything else untouched (incl. "nothing")
    # a smooth slanted surface (floor seen at a low angle, 3 % per pixel) is kept
    ramp = (1.03 ** np.arange(16, dtype=np.float32))[None, :].repeat(12, 0)
    assert np.array_equal(sl.drop_see_through_depth(ramp), ramp)
    # a real depth edge: the near side is kept, only the first far pixels next to it are dropped
    edge = np.full((12, 16), 4.0, np.float32); edge[:, :8] = 1.5
    e = sl.drop_see_through_depth(edge)
    assert (e[:, :8] == 1.5).all() and (e[:, 8] == 0).all() and (e[:, 9:] == 4.0).all()


def test_large_depth_guide_matches_the_wall_and_edge_pixels_are_not_folded(tmp_path):
    from namma.studio import splat_large as sl
    box_room(tmp_path / "room.glb", size=(4.0, 2.7, 3.2), offset=(-2.0, -0.0, -1.6), table=False)
    R = np.diag([1.0, -1.0, -1.0])
    t = -R @ np.array([0.0, 1.0, 0.0])
    model = tmp_path / "sparse"
    model.mkdir()
    (model / "cameras.txt").write_text("1 PINHOLE 64 48 40 40 32 24\n")
    q = sp._rot_to_quat(R)
    (model / "images.txt").write_text(f"1 {' '.join(map(str, q))} {' '.join(map(str, t))} 1 cam.jpg\n\n")
    n = sl.mesh_depth_maps(model, (tmp_path / "room.glb").read_bytes(), np.eye(4), tmp_path / "depth",
                           scale=0.5, samples=400_000, samples_per_m2=0)
    assert n == 1
    D = np.load(tmp_path / "depth" / "cam.npy").astype(np.float32)
    assert D.shape == (24, 32) and abs(float(np.median(D[10:14, 14:18])) - 1.6) < 0.03
    assert (D[8:16, 8:24] > 0).mean() > 0.98                 # the wall straight ahead is still fully guided


def test_line_up_sample_count_follows_the_mesh_area():
    from namma.studio.splat_align import MAX_ALIGN_SAMPLES, surface_sample_count
    def floor(side):                                         # a square floor of side x side metres (2 triangles)
        V = np.array([[0, 0, 0], [side, 0, 0], [side, 0, side], [0, 0, side]], float)
        return V, np.array([[0, 1, 2], [0, 2, 3]])
    assert surface_sample_count(*floor(4.0), 200_000) == 200_000                       # default: unchanged
    assert surface_sample_count(*floor(4.0), 200_000, 10_000) == 200_000               # 16 m2: the floor value
    assert surface_sample_count(*floor(15.0), 200_000, 10_000) == 2_250_000            # 225 m2 hall: 1 cm spacing
    assert surface_sample_count(*floor(40.0), 200_000, 10_000) == MAX_ALIGN_SAMPLES    # bounded


def test_drifted_phone_poses_are_corrected_against_the_mesh(tmp_path):
    """Multi-room walks drift: keyframes whose recorded pose is off by 15 cm / 3 degrees are put back on the mesh
    using their own depth points; good keyframes are left alone."""
    from namma.studio import splat_large as sl
    box_room(tmp_path / "room.glb", size=(6.0, 2.7, 4.0), offset=(0.0, 0.0, 0.0), table=True)
    glb = (tmp_path / "room.glb").read_bytes()
    V, F, _ = mi.mesh_triangles(glb)
    S, _, _ = mi.sample_surface(V, F, 200_000, seed=5)
    rng = np.random.default_rng(3)
    sp = tmp_path / "sparse"
    sp.mkdir()
    (sp / "cameras.txt").write_text("1 PINHOLE 64 48 40 40 32 24\n")
    lines, cam_pts, truth, drifted = [], {}, {}, set()
    for i in range(24):
        C = np.array([1.0 + 4.0 * (i % 6) / 5, 1.4, 1.0 + 2.0 * (i // 6) / 3])
        yaw = np.radians(15 * i)
        Rw = np.array([[np.cos(yaw), 0, -np.sin(yaw)], [0, 1, 0], [np.sin(yaw), 0, np.cos(yaw)]])   # camera -> world
        R = (Rw @ np.diag([1.0, -1.0, -1.0])).T                                                     # world -> camera
        t = -R @ C
        Xc = S @ R.T + t
        ok = (Xc[:, 2] > 0.3)
        Xc = Xc[ok][rng.choice(int(ok.sum()), 1500, replace=False)]
        stem = f"kf{i:02d}"
        cam_pts[stem] = Xc.astype(np.float32)
        truth[stem] = C
        Rr, tr = R, t
        if i % 2:                                              # drifted: the recorded pose is wrong
            d = np.radians(3.0)
            Dz = np.array([[np.cos(d), 0, np.sin(d)], [0, 1, 0], [-np.sin(d), 0, np.cos(d)]])
            Cr = C + [0.15, 0.0, -0.1]
            Rr = R @ Dz.T
            tr = -Rr @ Cr
            drifted.add(stem)
        q = sp_quat(Rr)
        lines.append(f"{i + 1} {' '.join(map(str, q))} {' '.join(map(str, tr))} 1 {stem}.jpg\n\n")
    (sp / "images.txt").write_text("".join(lines))
    (sp / "points3D.txt").write_text("1 1 1 1 100 100 100 0\n")
    np.savez(tmp_path / "depth_points.npz", **cam_pts)
    st = sl.refine_poses_to_mesh(sp, tmp_path / "depth_points.npz", glb, tmp_path / "fixed", samples_per_m2=0)
    assert st["frames"] == 24 and st["corrected"] >= 10
    assert st["within_5cm_after"] > st["within_5cm_before"] + 0.1   # half the keyframes were already right
    from namma.studio.splat_blocks import read_text_model
    _, imgs = read_text_model(tmp_path / "fixed" / "sparse")
    err = {Path(im["name"]).stem: np.linalg.norm(im["C"] - truth[Path(im["name"]).stem]) for im in imgs}
    assert max(err[s] for s in drifted) < 0.03                  # was 18 cm
    assert max(err[s] for s in err if s not in drifted) < 0.03  # good ones stay good
    assert (tmp_path / "fixed" / "sparse" / "points3D.txt").stat().st_size > 1000


def sp_quat(R):
    return sp._rot_to_quat(R)


def test_fast_depth_maps_are_identical_to_the_plain_loop(tmp_path):
    """Speed-up without any change: culling by 0.5 m cubes and parallel processes give the same maps, bit for bit."""
    from namma.studio import splat_large as sl
    box_room(tmp_path / "room.glb", size=(6.0, 2.7, 4.0), offset=(0.0, 0.0, 0.0), table=True)
    glb = (tmp_path / "room.glb").read_bytes()
    sp_dir = tmp_path / "sparse"
    sp_dir.mkdir()
    (sp_dir / "cameras.txt").write_text("1 PINHOLE 320 240 220 220 160 120\n")
    rng = np.random.default_rng(4)
    lines = []
    for i in range(12):
        C = np.array([rng.uniform(0.5, 5.5), rng.uniform(0.8, 1.8), rng.uniform(0.5, 3.5)])
        yaw = rng.uniform(0, 2 * np.pi)
        Rw = np.array([[np.cos(yaw), 0, -np.sin(yaw)], [0, 1, 0], [np.sin(yaw), 0, np.cos(yaw)]])
        R = (Rw @ np.diag([1.0, -1.0, -1.0])).T
        lines.append(f"{i + 1} {' '.join(map(str, sp._rot_to_quat(R)))} {' '.join(map(str, -R @ C))} 1 c{i:02d}.jpg\n\n")
    (sp_dir / "images.txt").write_text("".join(lines))
    kw = dict(scale=0.5, samples=300_000, samples_per_m2=0)
    assert sl.mesh_depth_maps(sp_dir, glb, np.eye(4), tmp_path / "plain", workers=1, cull=False, **kw) == 12
    assert sl.mesh_depth_maps(sp_dir, glb, np.eye(4), tmp_path / "cull", workers=1, **kw) == 12
    assert sl.mesh_depth_maps(sp_dir, glb, np.eye(4), tmp_path / "procs", workers=2, **kw) == 12
    for i in range(12):
        a = np.load(tmp_path / "plain" / f"c{i:02d}.npy")
        assert (a > 0).mean() > 0.1                                       # a real map, not an empty one
        assert np.array_equal(a, np.load(tmp_path / "cull" / f"c{i:02d}.npy"))
        assert np.array_equal(a, np.load(tmp_path / "procs" / f"c{i:02d}.npy"))


def _queue_setup(tmp_path, monkeypatch, run_status):
    from namma.core.jsonio import write_json
    from namma.studio.jobs import JobManager
    from namma.studio.projects import ProjectStore
    store = ProjectStore(tmp_path / "projects")
    pid = store.create(title="Queue Space")["project_id"]
    rid = "run-20261122-120000"
    rdir = store.path(pid) / "runs" / rid
    rdir.mkdir(parents=True)
    write_json(rdir / "config.json", {"config": {"profile": "standard"}})
    write_json(rdir / "run.json", {"run_id": rid, "status": run_status, "stages": []})
    monkeypatch.setattr(sp, "available", lambda: (True, None))
    return store, JobManager(store), pid, rid, rdir


def test_photoreal_can_be_queued_and_unqueued_while_reconstruction_runs(tmp_path, monkeypatch):
    from namma.studio import jobs as jm
    store, jobs, pid, rid, rdir = _queue_setup(tmp_path, monkeypatch, "failed")
    assert jobs.splat_status(pid, rid)["queued"] is False
    assert jobs.queue_splat(pid, rid) == {"queued": True}             # failed run: kept for a later Resume
    assert (rdir / "65_splat" / jm.QUEUE_FILE).exists()
    assert jobs.splat_status(pid, rid)["queued"] is True
    assert jobs.unqueue_splat(pid, rid) == {"queued": False}
    assert jobs.splat_status(pid, rid)["queued"] is False


def test_queueing_a_finished_run_starts_the_photoreal_view_now(tmp_path, monkeypatch):
    store, jobs, pid, rid, rdir = _queue_setup(tmp_path, monkeypatch, "succeeded")
    monkeypatch.setattr(jobs, "start_splat", lambda p, r: {"pid": 123})
    assert jobs.queue_splat(pid, rid) == {"queued": False, "pid": 123}


def test_queueing_refused_without_the_splat_engine(tmp_path, monkeypatch):
    from namma.studio.jobs import JobError
    store, jobs, pid, rid, rdir = _queue_setup(tmp_path, monkeypatch, "running")
    monkeypatch.setattr(sp, "available", lambda: (False, "no GPU environment"))
    try:
        jobs.queue_splat(pid, rid)
        assert False, "expected JobError"
    except JobError as e:
        assert e.code == "SPLAT-UNAVAILABLE"


def test_worker_starts_queued_photoreal_only_after_a_successful_full_run(tmp_path, monkeypatch):
    import argparse
    from namma.core.jsonio import write_json
    from namma.studio import jobs as jm
    from namma.studio import worker
    from namma.studio import pipeline
    from namma.studio import profiles
    store, jobs, pid, rid, rdir = _queue_setup(tmp_path, monkeypatch, "running")
    (rdir / "65_splat").mkdir()
    write_json(rdir / "65_splat" / jm.QUEUE_FILE, {"queued_at": "x"})
    started = []
    monkeypatch.setattr(worker, "task_splat", lambda a: started.append(a.run) or 0)
    monkeypatch.setattr(profiles, "get_profile", lambda name: {})
    a = argparse.Namespace(project_dir=str(store.path(pid)), run=rid, profile="standard", stop_after=None)

    monkeypatch.setattr(pipeline, "run_pipeline", lambda *x, **k: {"status": "failed"})
    assert worker.task_reconstruct(a) == 1 and started == []
    assert (rdir / "65_splat" / jm.QUEUE_FILE).exists()                # kept for Resume

    a.stop_after = "sfm"                                                # partial run: not "finished"
    monkeypatch.setattr(pipeline, "run_pipeline", lambda *x, **k: {"status": "succeeded"})
    assert worker.task_reconstruct(a) == 0 and started == []

    a.stop_after = None
    assert worker.task_reconstruct(a) == 0 and started == [rid]
    assert not (rdir / "65_splat" / jm.QUEUE_FILE).exists()            # consumed once
    assert worker.task_reconstruct(a) == 0 and started == [rid]         # not started twice
