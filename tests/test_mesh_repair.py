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


def _split_in_two(glb):
    """The same room as TWO primitives in two meshes / nodes (like Polycam's multi-atlas exports)."""
    import copy
    from namma.studio.mesh_tidy import _rebuild, glb_bytes
    doc, binary = mi._parse_glb(glb)
    prim = doc["meshes"][0]["primitives"][0]
    F = mi._accessor(doc, binary, prim["indices"]).reshape(-1, 3)
    half = len(F) // 2
    doc, binary, views = _rebuild(doc, binary, add=[F[:half].astype("<u4").tobytes(), F[half:].astype("<u4").tobytes()])
    acc = doc["accessors"]
    for v, n in zip(views, (half, len(F) - half)):
        acc.append({"bufferView": v, "componentType": 5125, "count": int(n * 3), "type": "SCALAR"})
    p2 = copy.deepcopy(prim)
    prim["indices"], p2["indices"] = len(acc) - 2, len(acc) - 1
    doc["meshes"].append({"primitives": [p2]})
    doc["nodes"] = [{"mesh": 0}, {"mesh": 1}, {"children": [0, 1]}]
    doc["scenes"] = [{"nodes": [2]}]
    return glb_bytes(doc, binary)


def test_a_scan_in_several_pieces_is_repaired_like_one(tmp_path):
    """Large Polycam scans come in several pieces (one per texture atlas): they were skipped entirely."""
    scan_like_room(tmp_path / "r.glb")
    one = (tmp_path / "r.glb").read_bytes()
    two = _split_in_two(one)
    out1, s1 = mr.repair(one)
    out2, s2 = mr.repair(two)
    assert s2["applied"] and s2["pieces"] == 2
    for k in ("removed_above_triangles", "plain_ceiling_triangles", "filled_holes"):   # (vertices: shared, so counted twice)
        assert s2[k] == s1[k], k
    V1, F1, _ = mi.mesh_triangles(out1)
    V2, F2, _ = mi.mesh_triangles(out2)
    assert len(F1) == len(F2)
    a1 = np.sort(np.round(V1[F1].mean(1), 4), axis=0)
    a2 = np.sort(np.round(V2[F2].mean(1), 4), axis=0)
    assert np.allclose(a1, a2, atol=1e-4)                        # the same repaired room
    same, st = mr.repair(two, multi_primitive=False)              # the old behaviour stays available
    assert same == two and not st["applied"]


def _two_piece_textured_glb(path, pieces):
    """pieces: [(V (k,3), F (m,3), UV (k,2), rgb)] -> a GLB with one mesh / node / plain-colour texture per piece."""
    import io
    from PIL import Image
    from namma.recon.glb import write_glb
    parts, views, accessors, meshes, materials, textures, images, nodes = [], [], [], [], [], [], [], []
    off = 0

    def add(b):
        nonlocal off
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(b)})
        parts.append(b + b"\0" * (-len(b) % 4))
        off += len(b) + (-len(b) % 4)
        return len(views) - 1
    for i, (V, F, UV, rgb) in enumerate(pieces):
        png = io.BytesIO()
        Image.new("RGB", (64, 64), rgb).save(png, "PNG")
        a0 = len(accessors)
        accessors += [{"bufferView": add(V.astype("<f4").tobytes()), "componentType": 5126, "count": len(V), "type": "VEC3",
                       "min": V.min(0).tolist(), "max": V.max(0).tolist()},
                      {"bufferView": add(UV.astype("<f4").tobytes()), "componentType": 5126, "count": len(V), "type": "VEC2"},
                      {"bufferView": add(F.astype("<u4").tobytes()), "componentType": 5125, "count": F.size, "type": "SCALAR"}]
        images.append({"bufferView": add(png.getvalue()), "mimeType": "image/png"})
        textures.append({"source": i})
        materials.append({"pbrMetallicRoughness": {"baseColorTexture": {"index": i}}})
        meshes.append({"primitives": [{"attributes": {"POSITION": a0, "TEXCOORD_0": a0 + 1}, "indices": a0 + 2, "material": i}]})
        nodes.append({"mesh": i})
    nodes.append({"children": list(range(len(pieces)))})
    binary = b"".join(parts)
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [len(nodes) - 1]}], "nodes": nodes,
           "meshes": meshes, "materials": materials, "textures": textures, "images": images,
           "buffers": [{"byteLength": len(binary)}], "bufferViews": views, "accessors": accessors}
    write_glb(path, doc, binary)


def _grid(p0, du, dv, n=8):
    V, F = [], []
    for i in range(n + 1):
        for j in range(n + 1):
            V.append(np.asarray(p0, float) + np.asarray(du, float) * i / n + np.asarray(dv, float) * j / n)
    for i in range(n):
        for j in range(n):
            a, b = i * (n + 1) + j, (i + 1) * (n + 1) + j
            F.extend([[a, b, b + 1], [a, b + 1, a + 1]])
    return np.array(V), np.array(F)


def test_whole_flat_scans_keep_beams_and_large_pieces_but_lose_a_fan(tmp_path):
    """Whole-flat scans: a ceiling beam (20 cm deep) is neither pressed onto the ceiling nor removed as debris; a dark
    1.5 m2 underside just under the ceiling is kept (too big for a fan); a small dark fan-sized piece is removed."""
    X, Y, Z = np.eye(3)
    W_, H_, D_ = 5.0, 2.7, 4.0
    shell = [_grid([0, 0, 0], X * W_, Z * D_), _grid([0, H_, 0], X * W_, Z * D_),
             _grid([0, 0, 0], X * W_, Y * H_), _grid([0, 0, D_], X * W_, Y * H_),
             _grid([0, 0, 0], Z * D_, Y * H_), _grid([W_, 0, 0], Z * D_, Y * H_),
             # a beam across the room: bottom 20 cm under the ceiling, 30 cm wide, with its two sides
             _grid([1.0, H_ - 0.2, 0.0], X * 0.3, Z * D_), _grid([1.0, H_ - 0.2, 0.0], Y * 0.2, Z * D_),
             _grid([1.3, H_ - 0.2, 0.0], Y * 0.2, Z * D_)]
    Vs, Fs = [], []
    for V, F in shell:
        Fs.append(F + sum(len(v) for v in Vs))
        Vs.append(V)
    Vr, Fr = np.vstack(Vs), np.vstack(Fs)
    top = np.isclose(Vr[:, 1], H_)
    Vr[top, 1] += 0.04 * np.sin(Vr[top, 0] * 3)                    # a wavy ceiling: the ceiling is repaired
    beam = (Vr[:, 1] < H_ - 0.05) & (Vr[:, 1] > H_ - 0.25) & (Vr[:, 0] >= 1.0) & (Vr[:, 0] <= 1.3)
    under = _grid([3.0, H_ - 0.08, 0.2], X * 1.5, Z * 1.0)          # big dark underside (down-facing)
    fan = _grid([3.5, H_ - 0.08, 2.8], X * 0.5, Z * 0.5)            # fan-sized dark piece
    Vd = np.vstack([under[0], fan[0]])
    Fd = np.vstack([under[1], fan[1] + len(under[0])])
    _two_piece_textured_glb(tmp_path / "flat.glb", [(Vr, Fr, np.full((len(Vr), 2), 0.5), (235, 235, 230)),
                                                     (Vd, Fd, np.full((len(Vd), 2), 0.5), (60, 45, 30))])
    out, st = mr.repair((tmp_path / "flat.glb").read_bytes())
    assert st["applied"] and st["pieces"] == 2 and st["whole_flat_limits"]["flatten_below_m"] == 0.10
    assert abs(st["protected_large_pieces_m2"] - 1.5) < 1e-6
    assert st["removed_hanging_object_triangles"] == len(fan[1])
    doc, binary = mi._parse_glb(out)
    p0 = doc["meshes"][0]["primitives"][0]
    V0 = mi._accessor(doc, binary, p0["attributes"]["POSITION"])
    F0 = mi._accessor(doc, binary, p0["indices"]).reshape(-1, 3)
    assert np.allclose(V0[beam], Vr[beam], atol=1e-6)               # the beam is not pressed onto the ceiling
    kept = np.isin(F0, np.flatnonzero(beam)).all(axis=1).sum()
    beam_faces = np.isin(Fr, np.flatnonzero(beam)).all(axis=1).sum()
    assert kept == beam_faces                                        # ... nor removed as ceiling debris
    p1 = doc["meshes"][1]["primitives"][0]
    F1 = mi._accessor(doc, binary, p1["indices"]).reshape(-1, 3)
    assert len(F1) == len(under[1])                                  # the big underside stays, the fan is gone


def test_whole_flat_torn_ceiling_scraps_go_but_a_beam_stays(tmp_path):
    X, Y, Z = np.eye(3)
    W_, H_, D_ = 5.0, 2.7, 4.0
    parts = [_grid([0, 0, 0], X * W_, Z * D_), _grid([0, H_, 0], X * W_, Z * D_),
             _grid([0, 0, 0], X * W_, Y * H_), _grid([0, 0, D_], X * W_, Y * H_),
             _grid([0, 0, 0], Z * D_, Y * H_), _grid([W_, 0, 0], Z * D_, Y * H_),
             _grid([1.0, H_ - 0.2, 0.0], X * 0.3, Z * D_), _grid([1.0, H_ - 0.2, 0.0], Y * 0.2, Z * D_),
             _grid([1.3, H_ - 0.2, 0.0], Y * 0.2, Z * D_),                       # beam (2.8 m2 with its sides)
             _grid([3.0, H_ - 0.2, 1.5], X * 0.7, Z * 0.8)]                      # torn scrap hanging 20 cm down
    Vs, Fs = [], []
    for V, F in parts:
        Fs.append(F + sum(len(v) for v in Vs))
        Vs.append(V)
    Vr, Fr = np.vstack(Vs), np.vstack(Fs)
    top = np.isclose(Vr[:, 1], H_)
    Vr[top, 1] += 0.04 * np.sin(Vr[top, 0] * 3)
    half = len(Fr) // 2                                                          # two pieces (whole-flat path)
    _two_piece_textured_glb(tmp_path / "f.glb", [(Vr, Fr, np.full((len(Vr), 2), 0.5), (235, 235, 230)),
                                                  (Vr[:3] + [0, 0.5, 0], np.array([[0, 1, 2]]), np.full((3, 2), 0.5), (235, 235, 230))])
    out, st = mr.repair((tmp_path / "f.glb").read_bytes())
    assert st["pieces"] == 2 and st["ceiling_debris_triangles"] >= len(parts[-1][1])
    doc, binary = mi._parse_glb(out)
    p0 = doc["meshes"][0]["primitives"][0]
    V0 = mi._accessor(doc, binary, p0["attributes"]["POSITION"])
    F0 = mi._accessor(doc, binary, p0["indices"]).reshape(-1, 3)
    c = V0[F0].mean(1)
    scrap = (c[:, 0] > 3.0) & (c[:, 0] < 3.7) & (c[:, 2] > 1.5) & (c[:, 2] < 2.3) & (np.abs(c[:, 1] - (H_ - 0.2)) < 0.01)
    in_beam = lambda c: ((c[:, 0] >= 1.0) & (c[:, 0] <= 1.3) & (c[:, 1] < H_ - 0.05) & (c[:, 1] > H_ - 0.25)
                         & (c[:, 2] > 0.01) & (c[:, 2] < D_ - 0.01))
    assert scrap.sum() == 0
    assert in_beam(c).sum() == in_beam(Vr[Fr].mean(1)).sum() > 0     # every beam face kept
