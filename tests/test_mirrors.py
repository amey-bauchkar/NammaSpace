"""Mirror regions (namma.studio.mirrors): synthetic room with a phantom "reflected room" behind one wall.
No GPU, no browser: numpy meshes, fake cameras, a hand-written splat .ply."""
import json

import numpy as np
import pytest

from namma.lidar.mesh import write_vertex_color_glb
from namma.studio import mesh_import as mi
from namma.studio import mirrors as mir

W, H, D = 5.0, 2.8, 4.0                     # room x 0..5, y 0..2.8, z 0..4 (mesh frame); mirror on the wall x = 5
MIRROR = [[W, 0.0, 1.0], [W, 0.0, 2.6], [W, 2.0, 2.6], [W, 2.0, 1.0]]      # full length: 1.6 m wide, 2 m high
ROOM_POINT = [2.5, 1.5, 2.0]


def _quads(quads, n=12):
    V, F = [], []
    for p0, du, dv in quads:
        base = len(V)
        for i in range(n + 1):
            for j in range(n + 1):
                V.append(np.asarray(p0, float) + np.asarray(du, float) * i / n + np.asarray(dv, float) * j / n)
        for i in range(n):
            for j in range(n):
                a, b = base + i * (n + 1) + j, base + (i + 1) * (n + 1) + j
                F.extend([[a, b, b + 1], [a, b + 1, a + 1]])
    return np.array(V), np.array(F, np.int64)


def room_with_phantom(path, phantom=True):
    X, Y, Z = np.eye(3)
    q = [([0, 0, 0], X * W, Z * D), ([0, H, 0], X * W, Z * D),                    # floor, ceiling
         ([0, 0, 0], X * W, Y * H), ([0, 0, D], X * W, Y * H), ([0, 0, 0], Z * D, Y * H),
         # wall x = W with the mirror's glass missing (no depth from glass): below / above / beside the mirror
         ([W, 0, 0], Z * 1.0, Y * H), ([W, 0, 2.6], Z * 1.4, Y * H), ([W, 2.0, 1.0], Z * 1.6, Y * 0.8)]
    if phantom:
        # the reflected room as the phone saw it through the glass: a frustum widening behind the wall (45 degrees),
        # with the reflected floor at floor height
        for t0 in np.arange(0.1, 2.7, 0.2):
            half = 0.8 + t0                                            # mirror half-width 0.8 + depth
            q.append(([W + t0, 0, 1.8 - half], X * 0.2, Z * 2 * half))
        q += [([W + 2.7, 0, 1.8 - 3.4], Z * 6.8, Y * 2.2),              # reflected far wall
              ([W + 1.0, 0.75, 1.4], X * 1.0, Z * 0.8)]                 # reflected table top
    V, F = _quads(q)
    write_vertex_color_glb(path, V.astype(np.float32), None, F.astype(np.uint32))
    return V, F


def test_behind_rule_and_orientation():
    g = mir.plane_of(MIRROR, ROOM_POINT)
    assert np.allclose(g["n"], [-1, 0, 0])                      # normal points into the room
    assert abs(mir.polygon_area(g["poly2"]) - 3.2) < 1e-9
    P = np.array([[W + 1.0, 1.0, 1.8],        # straight behind the glass
                  [W + 2.0, 0.0, 3.5],        # behind, outside the outline but inside the 45-degree spread
                  [W + 0.5, 1.0, 5.0],        # behind, far to the side (outside the spread)
                  [W + 0.01, 1.0, 1.8],       # the wall / frame itself (within the margin)
                  [4.0, 1.0, 1.8]])           # in the room
    assert mir.behind(P, g).tolist() == [True, True, False, False, False]
    with pytest.raises(ValueError):
        mir.plane_of([[0, 0, 0], [1, 0, 0], [2, 0, 0]], ROOM_POINT)            # collinear
    with pytest.raises(ValueError):
        mir.plane_of([[W, 0, 1], [W, 0, 2], [W + 0.3, 2, 2], [W, 2, 1]], ROOM_POINT)   # not flat


def test_cut_mesh_removes_the_reflected_room_and_caps_the_mirror(tmp_path):
    V, F = room_with_phantom(tmp_path / "r.glb")
    glb = (tmp_path / "r.glb").read_bytes()
    g = {**mir.plane_of(MIRROR, ROOM_POINT), "mirror_id": "mirror-1", "corners": MIRROR, "cap": True}
    out, st = mir.cut_mesh(glb, [g])
    assert st["applied"] and st["removed_triangles"] > 0
    assert abs(st["inferred_area_m2"] - 3.2) < 1e-6
    V2, F2, _ = mi.mesh_triangles(out)
    cen = V2[F2].mean(1)
    assert not (cen[:, 0] > W + 0.05).any()                     # nothing left behind the wall
    inside_room = (V[F].mean(1)[:, 0] <= W + 1e-6).sum()
    assert (cen[:, 0] <= W + 1e-6).sum() == inside_room + 2      # the real room untouched; + the 2-triangle cap
    doc, _ = mi._parse_glb(out)
    assert doc["materials"][-1]["name"] == "namma-inferred-mirror"
    assert doc["materials"][-1]["extras"]["inferred"] is True
    # cap off: removed, not closed
    out2, st2 = mir.cut_mesh(glb, [{**g, "cap": False}])
    assert st2["inferred_area_m2"] == 0 and len(mi.mesh_triangles(out2)[1]) == inside_room


def allowed_cells_model(rg):
    """Centres (model frame, y = 0) of the roam grid's allowed cells (rle_rows_v1: runs alternate False / True)."""
    out = []
    for r, runs in enumerate(rg["rows"]):
        c, val = 0, False
        for n in runs:
            if val:
                out += [[rg["origin_xz"][0] + (k + 0.5) * rg["cell"], 0.0, rg["origin_xz"][1] + (r + 0.5) * rg["cell"]]
                        for k in range(c, c + n)]
            c, val = c + n, not val
    return np.array(out)


def test_apply_to_run_removes_reflected_floor_and_restores(tmp_path):
    room_with_phantom(tmp_path / "scan.glb")
    r = mi.import_mesh(tmp_path / "scan.glb", tmp_path / "projects", title="Mirror room", source_note="synthetic",
                       log=None, repair=False)
    pdir = tmp_path / "projects" / r["project_id"]
    run = pdir / "runs" / r["run_id"]
    frame = json.loads((run / "70_frame" / "frame.json").read_text())
    T = np.array(frame["T_model_from_sfm"])
    Ti = np.linalg.inv(T)

    def walk_max_x():
        geo = json.loads((run / "85_navgeom" / "geometry.if04.json").read_text())
        pts = np.array([[p[0], 0.0, p[1]] for wr in geo["floors"][0]["walkable_regions"] for p in wr])
        return float((pts @ Ti[:3, :3].T + Ti[:3, 3])[:, 0].max())

    q = json.loads((run / "80_quality" / "quality_report.json").read_text())
    assert q["facts"]["model_health"]["mirror_suggestions"]           # the detector noticed (suggestion only)
    assert any(w["code"] == "MIRROR-SUSPECTED" for w in q["warnings"])
    assert walk_max_x() > W + 0.5                                     # BEFORE: reflected floor is "walkable"
    original = (run / "60_texture" / "lod0.glb").read_bytes()

    corners = (np.array(MIRROR) @ T[:3, :3].T + T[:3, 3]).tolist()  # marked in the viewer: model frame
    (pdir / "mirrors.json").write_text(json.dumps([{"mirror_id": "mirror-1", "corners": corners, "run_id": r["run_id"]}]))
    st = mir.apply_to_run(pdir, r["run_id"])
    assert st["regions"] == 1 and st["removed_area_m2"] > 5 and st["geometry_behind_after_m2"] == 0
    assert walk_max_x() < W + 0.05                                    # AFTER: nothing walkable behind the mirror
    # the mirror is a wall for roaming: no allowed roam cell behind it
    rg = json.loads((run / "70_frame" / "roam_grid.json").read_text())
    xs = allowed_cells_model(rg) @ Ti[:3, :3].T + Ti[:3, 3]
    assert xs[:, 0].max() < W + 0.05
    pkg = json.loads((run / "90_package" / "scene_package.json").read_text())
    assert pkg["quality"]["inferred"]["mirror_regions"] == 1
    assert abs(pkg["quality"]["inferred"]["inferred_area_m2"] - 3.2) < 1e-3
    q = json.loads((run / "80_quality" / "quality_report.json").read_text())
    assert q["facts"]["mirrors"]["regions"] == 1 and any(w["code"] == "MIRROR-REGIONS" for w in q["warnings"])
    # switching off: no regions -> the original model and no "inferred" block
    (pdir / "mirrors.json").write_text("[]")
    mir.apply_to_run(pdir, r["run_id"])
    assert (run / "60_texture" / "lod0.glb").read_bytes() == original
    pkg = json.loads((run / "90_package" / "scene_package.json").read_text())
    assert "inferred" not in pkg["quality"]


def _camera_looking_plus_x(c):
    R = np.array([[0, 0, 1], [0, -1, 0], [1, 0, 0]], float)         # OpenCV: camera z = world +x, y = world -y
    return R, -R @ np.asarray(c, float)


def test_masks_cover_the_mirror_only_from_the_room_side(tmp_path):
    g = {**mir.plane_of(MIRROR, ROOM_POINT), "corners": MIRROR}
    K = np.array([[100, 0, 100], [0, 100, 75], [0, 0, 1]], float)
    R, t = _camera_looking_plus_x([2.5, 1.5, 1.8])
    m, hit = mir.mirror_mask(R, t, K, 200, 150, [g])
    assert hit and m[75, 100] == 0 and m[0, 0] == 255 and 0.05 < (m == 0).mean() < 0.9
    Rb, tb = _camera_looking_plus_x([W + 1.0, 1.5, 1.8])              # behind the mirror: the glass is not seen
    assert not mir.mirror_mask(Rb, tb, K, 200, 150, [g])[1]
    # COLMAP text model -> mask files for the photos that see it
    sp = tmp_path / "sparse"
    sp.mkdir()
    (sp / "cameras.txt").write_text("1 PINHOLE 200 150 100 100 100 75\n")
    from namma.studio.splat_large import _rot_to_quat
    q1, q2 = _rot_to_quat(R), _rot_to_quat(np.array([[0, 0, -1], [0, -1, 0], [-1, 0, 0]], float))
    t2 = -np.array([[0, 0, -1], [0, -1, 0], [-1, 0, 0]], float) @ np.array([2.5, 1.5, 1.8])
    (sp / "images.txt").write_text(f"1 {' '.join(map(str, q1))} {' '.join(map(str, t))} 1 a.jpg\n\n"
                                   f"2 {' '.join(map(str, q2))} {' '.join(map(str, t2))} 1 b.jpg\n\n")
    st = mir.write_masks(sp, [g], tmp_path / "masks")
    assert st["photos"] == 2 and st["masked_photos"] == 1                # b looks away from the mirror
    assert (tmp_path / "masks" / "a.png").exists() and not (tmp_path / "masks" / "b.png").exists()


def _write_ply(path, P, rgb_dc, op_logit=3.0):
    props = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2", "f_rest_0", "f_rest_1", "f_rest_2",
             "opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    n = len(P)
    arr = np.zeros((n, len(props)), "<f4")
    arr[:, :3] = P
    arr[:, 6:9] = rgb_dc
    arr[:, 12] = op_logit
    arr[:, 13:16] = np.log(0.01)
    arr[:, 16] = 1
    head = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n" % n + "".join(f"property float {p}\n" for p in props) + "end_header\n"
    path.write_bytes(head.encode("ascii") + arr.tobytes())
    return props


def test_splat_behind_the_mirror_is_removed_and_capped(tmp_path):
    rng = np.random.default_rng(0)
    wall = np.c_[np.full(4000, W), rng.uniform(0, 2.6, 4000), rng.uniform(0.3, 3.3, 4000)]
    in_glass = (wall[:, 1] < 2.0) & (wall[:, 2] > 1.0) & (wall[:, 2] < 2.6)
    wall = wall[~in_glass]
    phantom = np.c_[rng.uniform(W + 0.3, W + 2.5, 3000), rng.uniform(0, 2, 3000), rng.uniform(0.8, 2.8, 3000)]
    P = np.vstack([wall, phantom])
    dc = np.vstack([np.tile([0.8, 0.2, -0.4], (len(wall), 1)), np.tile([-1.0, -1.0, -1.0], (len(phantom), 1))])
    props = _write_ply(tmp_path / "in.ply", P, dc)
    g = {**mir.plane_of(MIRROR, ROOM_POINT), "mirror_id": "mirror-1", "corners": MIRROR, "cap": True}
    st = mir.cull_and_cap_splat(tmp_path / "in.ply", tmp_path / "out.ply", [g])
    assert st["removed_splats"] == 3000 and st["cap_splats"] > 1000
    from namma.studio.splat_spz import _ply_fields
    f = _ply_fields(tmp_path / "out.ply")
    assert list(f) == props
    x = np.asarray(f["x"])
    assert not (x > W + 0.05).any()
    cap = np.isclose(x, W, atol=1e-4) & (np.asarray(f["y"]) <= 2.0 + 1e-4) & (np.asarray(f["z"]) >= 1.0 - 1e-4)         & (np.asarray(f["z"]) <= 2.6 + 1e-4)
    assert cap.sum() >= st["cap_splats"]
    # the cap takes the colour of the wall around the mirror, and is flat along the wall normal
    assert np.allclose(np.asarray(f["f_dc_0"])[cap].mean(), 0.8, atol=0.05)
    assert np.isclose(np.exp(np.asarray(f["scale_2"])[cap]).max(), 0.002, rtol=1e-3)


def test_detector_suggests_but_a_plain_room_gets_nothing(tmp_path):
    V, F = room_with_phantom(tmp_path / "a.glb")
    s = mir.suggest_mirrors(V, F)
    assert s and all(x["kind"] == "suggestion" for x in s)
    assert any(abs(abs(x["wall_offset_m"]) - W) < 0.1 for x in s)
    V0, F0 = room_with_phantom(tmp_path / "b.glb", phantom=False)
    assert mir.suggest_mirrors(V0, F0) == []


def test_wall_points_close_a_full_length_mirror():
    g = mir.plane_of(MIRROR, ROOM_POINT)
    P = mir.wall_points(g)
    assert np.allclose(P[:, 0], W) and P[:, 2].min() <= 1.0 and P[:, 2].max() >= 2.6
    assert P[:, 1].min() >= 0.2 and P[:, 1].max() <= 1.7 + 1e-9


def test_masked_pixels_get_no_gradient():
    """The trainer's masking rule (train_splat_large.py): out = out * keep + gt * (1 - keep)."""
    torch = pytest.importorskip("torch")
    render = torch.rand(1, 8, 8, 3, requires_grad=True)
    gt = torch.rand(1, 8, 8, 3)
    keep = torch.ones(8, 8)
    keep[2:5, 2:5] = 0
    out = render * keep[None, ..., None] + gt * (1 - keep[None, ..., None])
    (out - gt).abs().mean().backward()
    assert torch.all(render.grad[0, 2:5, 2:5] == 0) and torch.all(render.grad[0, 0, 0] != 0)


def test_wavy_real_wall_beside_the_mirror_is_not_cut():
    """Scan noise: the real wall next to the glass sits 5 cm behind the picked plane (frame proud of the wall)."""
    g = mir.plane_of(MIRROR, ROOM_POINT)
    beside = np.array([[W + 0.05, 1.0, 0.9],       # real wall, 10 cm left of the outline, 5 cm behind the plane
                       [W + 0.05, 2.1, 1.8]])      # real wall just above the mirror
    assert not mir.behind(beside, g).any()
    assert mir.behind(np.array([[W + 0.05, 1.0, 1.8]]), g).all()      # behind the GLASS: reflection, removed


def test_checks_flag_a_cut_that_may_have_hit_something_real(tmp_path):
    X, Y, Z = np.eye(3)
    # a REAL room behind the wall, through a doorway right next to the mirror (wide floor + walls behind the plane)
    V, F = room_with_phantom(tmp_path / "r.glb", phantom=False)
    V2, F2 = _quads([([W + 0.2, 0, -2.0], X * 3.0, Z * 8.0), ([W + 3.2, 0, -2.0], Z * 8.0, Y * 2.8)])
    Vall, Fall = np.vstack([V, V2]), np.vstack([F, F2 + len(V)])
    write_vertex_color_glb(tmp_path / "r2.glb", Vall.astype(np.float32), None, Fall.astype(np.uint32))
    g = {**mir.plane_of(MIRROR, ROOM_POINT), "mirror_id": "mirror-1", "corners": MIRROR}
    _, st = mir.cut_mesh((tmp_path / "r2.glb").read_bytes(), [g])
    codes = [c["code"] for c in mir.mirror_checks(st)]
    assert "MIRROR-CUT-WIDE" in codes and "MIRROR-NO-WALL" not in codes
    # corners picked on the reflection (1 m behind the wall): no scanned wall around the outline
    back = [[x + 1.0, y, z] for x, y, z in MIRROR]
    gb = {**mir.plane_of(back, ROOM_POINT), "mirror_id": "mirror-2", "corners": back}
    _, st = mir.cut_mesh((tmp_path / "r.glb").read_bytes(), [gb])
    assert "MIRROR-NO-WALL" in [c["code"] for c in mir.mirror_checks(st)]
    assert mir.mirror_checks({"per_region": []}, {"seed_dropped_m2": 4.0})[0]["code"] == "MIRROR-FLOOR-DROPPED"


def test_a_narrow_doorway_gets_a_tight_path_not_unreachable(tmp_path):
    """Scanned doorways are often narrower than real ones (the 1BHK: 50 cm). With the 25 cm body radius the planner
    found no way; it now retries with the 18 cm minimum and says "tight passage" (never validated)."""
    from namma.studio import routing
    X, Y, Z = np.eye(3)
    gap0, gap1 = 1.75, 2.30                                         # a 55 cm opening in a partition at x = 3
    q = [([0, 0, 0], X * 6, Z * 4), ([0, 2.8, 0], X * 6, Z * 4), ([0, 0, 0], X * 6, Y * 2.8), ([0, 0, 4], X * 6, Y * 2.8),
         ([0, 0, 0], Z * 4, Y * 2.8), ([6, 0, 0], Z * 4, Y * 2.8),
         ([3, 0, 0], Z * gap0, Y * 2.8), ([3, 0, gap1], Z * (4 - gap1), Y * 2.8)]
    V, F = _quads(q, n=40)
    write_vertex_color_glb(tmp_path / "two.glb", V.astype(np.float32), None, F.astype(np.uint32))
    r = mi.import_mesh(tmp_path / "two.glb", tmp_path / "projects", title="Two rooms", source_note="t", log=None)
    run = tmp_path / "projects" / r["project_id"] / "runs" / r["run_id"]
    T = np.array(json.loads((run / "70_frame" / "frame.json").read_text())["T_model_from_sfm"])
    to_model = lambda p: (np.array(p, float) @ T[:3, :3].T + T[:3, 3]).tolist()
    a, b = to_model([1.2, 0.0, 2.0]), to_model([4.8, 0.8, 2.0])
    cfg = json.loads((run / "config.json").read_text())["config"]["nav"]
    out = routing.route(run, [a[0], a[2]], b, cfg)
    assert out["status"] == "unverified" and out["tight_passage"] is True and "tight passage" in out["title"]
    assert not out["checks"]["intersects_obstacle"]
    xs = (np.array([[p[0], 0, p[1]] for p in out["polyline_model"]]) @ np.linalg.inv(T)[:3, :3].T + np.linalg.inv(T)[:3, 3])
    crossing = xs[np.argmin(np.abs(xs[:, 0] - 3.0))]
    assert gap0 < crossing[2] < gap1                                # through the doorway, not through the wall
    # a wide opening: a normal path, no "tight" flag
    q[-2], q[-1] = ([3, 0, 0], Z * 1.4, Y * 2.8), ([3, 0, 2.6], Z * 1.4, Y * 2.8)
    V, F = _quads(q, n=40)
    write_vertex_color_glb(tmp_path / "wide.glb", V.astype(np.float32), None, F.astype(np.uint32))
    r2 = mi.import_mesh(tmp_path / "wide.glb", tmp_path / "projects", title="Wide", source_note="t", log=None)
    run2 = tmp_path / "projects" / r2["project_id"] / "runs" / r2["run_id"]
    T2 = np.array(json.loads((run2 / "70_frame" / "frame.json").read_text())["T_model_from_sfm"])
    a2 = (np.array([1.2, 0.0, 2.0]) @ T2[:3, :3].T + T2[:3, 3]).tolist()
    b2 = (np.array([4.8, 0.8, 2.0]) @ T2[:3, :3].T + T2[:3, 3]).tolist()
    out2 = routing.route(run2, [a2[0], a2[2]], b2, cfg)
    assert out2["polyline_model"] and out2["tight_passage"] is False
