"""Repair of phone-scan artefacts on an imported mesh: wavy ceiling, loose pieces above it, holes. Synthetic only."""
import numpy as np

from namma.lidar.mesh import write_vertex_color_glb
from namma.studio import mesh_import as mi
from namma.studio import mesh_repair as mr


def scan_like_room(path, n=24, W=4.0, H=2.6, D=3.5):
    V, F = [], []

    def quad(p0, du, dv, keep=lambda i, j: True):
        base = len(V)
        for i in range(n + 1):
            for j in range(n + 1):
                V.append(np.asarray(p0, float) + du * i / n + dv * j / n)
        for i in range(n):
            for j in range(n):
                if keep(i, j):
                    a, b = base + i * (n + 1) + j, base + (i + 1) * (n + 1) + j
                    F.extend([[a, b, b + 1], [a, b + 1, a + 1]])
        return base
    X, Y, Z = np.eye(3)
    quad([0, 0, 0], X * W, Z * D)
    hole = lambda i, j: not (10 <= i < 13 and 10 <= j < 13)                 # a 0.5 x 0.44 m hole in the ceiling
    c0 = quad([0, H, 0], X * W, Z * D, hole)
    for P0, du, dv in (([0, 0, 0], X * W, Y * H), ([0, 0, D], X * W, Y * H), ([0, 0, 0], Z * D, Y * H), ([W, 0, 0], Z * D, Y * H)):
        quad(P0, du, dv)
    quad([1.0, H + 0.5, 1.0], X * 0.6, Z * 0.6)                              # a loose sheet above the ceiling
    V = np.array(V)
    ceil = np.arange(c0, c0 + (n + 1) ** 2)
    V[ceil, 1] += 0.08 * np.sin(V[ceil, 0] * 3) * np.cos(V[ceil, 2] * 2)   # wavy (+-8 cm)
    write_vertex_color_glb(path, V.astype(np.float32), None, np.array(F, np.uint32))
    return V, ceil


def test_ceiling_is_flattened_floater_removed_and_hole_closed(tmp_path):
    V0, ceil = scan_like_room(tmp_path / "r.glb")
    glb = (tmp_path / "r.glb").read_bytes()
    out, st = mr.repair(glb, plain_ceiling=False)
    assert st["applied"] and abs(st["ceiling"]["height"] - 2.6) < 0.03 and st["ceiling"]["tilt_deg"] < 1.0
    doc, binary = mi._parse_glb(out)
    prims = doc["meshes"][0]["primitives"]
    V = mi._accessor(doc, binary, prims[0]["attributes"]["POSITION"])
    used = np.intersect1d(ceil, mi._accessor(doc, binary, prims[0]["indices"]))   # (vertices inside the hole are unused)
    assert np.ptp(V[used, 1]) < 0.01                                         # was 16 cm of waves
    assert st["removed_above_triangles"] == 2 * 24 * 24                      # the loose sheet
    # floor and walls untouched, except the walls' top edge that shares the ceiling's border (moves with it: 1 mm)
    others = np.setdiff1d(np.arange(len(V0)), ceil)[:-(25 * 25)]
    moved = np.abs(V[others] - V0[others]).max(axis=1) > 1e-5
    assert np.abs(V[others] - V0[others]).max() < 0.01 and np.allclose(V0[others][moved, 1], 2.6)
    # the ceiling hole is closed by a second, vertex-coloured primitive (the viewer shows vertex colours unlit)
    filled = [h for h in st["holes"] if h["filled"]]
    assert len(filled) == 1 and abs(filled[0]["perimeter_m"] - (2 * 0.5 + 2 * 3.5 * 3 / 24)) < 0.05
    assert len(prims) == 2 and "COLOR_0" in prims[1]["attributes"]
    Vf = mi._accessor(doc, binary, prims[1]["attributes"]["POSITION"])
    assert np.allclose(Vf[:12, 1], 2.6, atol=0.01)                          # the hole patch, in the ceiling plane
    # backings 2 cm behind ceiling, floor and the 4 walls, so gaps show those surfaces instead of the background
    bk = {b["surface"]: b for b in st["backings"] if b["surface"] != "wall"}
    walls = [b for b in st["backings"] if b["surface"] == "wall"]
    assert abs(bk["ceiling"]["height"] - 2.6) < 0.01 and abs(bk["floor"]["height"]) < 0.01 and len(walls) == 4
    assert sorted(round(w["width_m"], 1) for w in walls) == [3.5, 3.5, 4.0, 4.0]       # from triangle centres (+ 5 cm each side)
    rest = Vf[12:]
    assert rest[:, 0].min() < 0 and rest[:, 0].max() > 4.0                   # the sheets cover the whole footprint
    assert np.isclose(rest[:, 1].max(), 2.62, atol=0.01) and np.isclose(rest[:, 1].min(), -0.02, atol=0.01)
    assert rest[:, 0].min() > -0.08 and rest[:, 0].max() < 4.08              # walls sit just behind the real walls


def test_open_sides_are_not_closed_and_odd_meshes_are_left_alone(tmp_path):
    V0, _ = scan_like_room(tmp_path / "r.glb")
    out, st = mr.repair((tmp_path / "r.glb").read_bytes(), max_hole_perimeter=1.0)
    assert st["filled_holes"] == 0 and any(h.get("reason", "").startswith("too large") for h in st["holes"])
    # a node transform: not touched (positions would be in another frame)
    doc, binary = mi._parse_glb((tmp_path / "r.glb").read_bytes())
    doc["nodes"][0]["scale"] = [2, 2, 2]
    from namma.studio.mesh_tidy import glb_bytes
    g = glb_bytes(doc, binary)
    same, st2 = mr.repair(g)
    assert same == g and not st2["applied"] and "node transforms" in st2["skipped"]


def test_a_repaired_ceiling_becomes_one_plain_sheet_and_a_clean_one_keeps_its_texture(tmp_path):
    scan_like_room(tmp_path / "r.glb")
    out, st = mr.repair((tmp_path / "r.glb").read_bytes())
    # the wavy ceiling (24 x 24 cells, 3 x 3 hole) is replaced by the ceiling backing sheet in its colour
    assert st["plain_ceiling_triangles"] == 2 * (24 * 24 - 9)
    ceiling = [b for b in st["backings"] if b["surface"] == "ceiling"][0]
    assert abs(ceiling["height"] - 2.6) < 0.01
    doc, binary = mi._parse_glb(out)
    F = mi._accessor(doc, binary, doc["meshes"][0]["primitives"][0]["indices"]).reshape(-1, 3)
    V = mi._accessor(doc, binary, doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"])
    assert (V[F][:, :, 1] > 2.5).all(axis=1).sum() == 0                     # no textured ceiling triangle left
    # a flat ceiling without artefacts is not touched at all
    from test_mesh_import import box_room
    box_room(tmp_path / "clean.glb")
    g = (tmp_path / "clean.glb").read_bytes()
    same, st2 = mr.repair(g)
    assert same == g and not st2.get("plain_ceiling_triangles")
