"""Mirrors: regions marked on a wall, and what every stage of the pipeline does with them.

A mirror is photographed (and depth-sensed) like a window into a second, reflected room. Without handling:
- the phone's mesh has phantom surface BEHIND the wall (and a hole where the glass is);
- the reflected floor lies at floor height, so it becomes walkable floor; a full-length mirror is a gap in the wall
  that roaming walks through;
- the splat fits the reflection with Gaussians behind the wall (a "virtual room", floaters, stretched colour).

Design (docs/QUALITY_AUDIT.md): MANUAL FIRST. A region is a polygon picked on the wall in the Studio (>= 3 corners,
model frame), kept in <project>/mirrors.json next to pois.json. Nothing changes until the user applies it. The
automatic detector (suggest_mirrors) only SUGGESTS: surface behind a wall can also be a real window, door or alcove.

One geometric rule is used everywhere ("behind the region"): a point is reflected if it lies more than
DEPTH_MARGIN_M behind the mirror plane, at most `depth_m` behind it, and inside the outline grown by
grow_m + spread x depth (what is seen in a mirror spreads out behind it, as through a window; 1.5 = views up to 56 degrees off the
mirror's normal - grazing views beyond that leave residue, reported as geometry_behind_wall_m2). Everything that is
synthesised in its place (a flat cap on the mirror plane) is flagged INFERRED, and `cap: false` switches caps off.
"""
import json
from pathlib import Path

import numpy as np

DEPTH_MARGIN_M = 0.03        # behind the plane by more than this = not the wall / frame itself
EDGE_MARGIN_M = 0.10         # OUTSIDE the outline only surface deeper than this is reflection: the real wall around
                             # a mirror is scanned wavy (+- several cm) and the frame stands proud of it - with the
                             # 3 cm margin a strip of real wall next to the glass was cut and the cap did not cover it
GROW_M = 0.05                # the outline grows by this (frame edges, picking error)
SPREAD = 1.5                 # lateral growth per metre behind the mirror (56 degrees; per region: "spread")
DEPTH_M = 6.0                # reflected geometry is searched this far behind the plane
MAX_RMS_M = 0.05             # corners must lie on one plane within this
CAP_COLOUR = (190, 190, 190)
MIRRORS_FILE = "mirrors.json"


# ------------------------------------------------------------------------------------------------ regions
def plane_of(polygon, room_point):
    """Corners (k>=3, 3D) -> {"c", "n" (unit, towards room_point), "e1", "e2", "poly2" (k,2), "rms"}."""
    P = np.asarray(polygon, float)
    if P.ndim != 2 or P.shape[1] != 3 or len(P) < 3 or not np.isfinite(P).all():
        raise ValueError("a mirror needs at least 3 corners of 3 numbers each")
    c = P.mean(0)
    _, s, vt = np.linalg.svd(P - c, full_matrices=False)
    if s[1] < 1e-6:
        raise ValueError("the mirror corners lie on one line")
    n = vt[2] / np.linalg.norm(vt[2])
    if (np.asarray(room_point, float) - c) @ n < 0:
        n = -n
    e1 = vt[0] / np.linalg.norm(vt[0])
    e2 = np.cross(n, e1)
    rms = float(np.sqrt(np.mean(((P - c) @ n) ** 2)))
    if rms > MAX_RMS_M:
        raise ValueError(f"the mirror corners are not on one flat surface ({rms * 100:.1f} cm off)")
    return {"c": c, "n": n, "e1": e1, "e2": e2, "poly2": (P - c) @ np.c_[e1, e2], "rms": rms}


def _inside(poly2, q):
    """Even-odd test of 2D points q (m,2) against a polygon (k,2)."""
    x, y = q[:, 0][:, None], q[:, 1][:, None]
    a, b = poly2, np.roll(poly2, -1, axis=0)
    cross = (a[:, 1] > y) != (b[:, 1] > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xi = a[:, 0] + (y - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
    return (np.count_nonzero(cross & (x < xi), axis=1) % 2) == 1


def _outline_distance(poly2, q):
    """Distance of 2D points to the polygon outline (0 inside)."""
    a, b = poly2, np.roll(poly2, -1, axis=0)
    ab = b - a
    t = np.clip(((q[:, None, :] - a) * ab).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-12), 0, 1)
    d = np.linalg.norm(q[:, None, :] - (a + t[..., None] * ab), axis=-1).min(1)
    return np.where(_inside(poly2, q), 0.0, d)


def behind(points, g, *, margin=DEPTH_MARGIN_M, grow=GROW_M, spread=SPREAD, depth=DEPTH_M):
    """Mask of points reflected in region g (plane_of result): behind the plane, inside the growing outline."""
    P = np.asarray(points, float)
    if not len(P):
        return np.zeros(0, bool)
    rel = P - g["c"]
    t = -(rel @ g["n"])                                     # depth behind the plane
    m = (t > margin) & (t < depth)
    out = np.zeros(len(P), bool)
    if m.any():
        q = rel[m] @ np.c_[g["e1"], g["e2"]]
        dist = _outline_distance(g["poly2"], q)
        out[m] = (dist <= grow + spread * t[m]) & ((dist == 0) | (t[m] > EDGE_MARGIN_M))
    return out


def behind_any(points, regions, **kw):
    out = np.zeros(len(points), bool)
    for g in regions:
        out |= behind(points, g, **{k: v for k, v in g.get("params", {}).items() if k in ("spread", "depth")}, **kw)
    return out


def transform_regions(regions, T):
    """Regions (plane_of dicts, with "corners") -> the same in another frame (4x4 rigid T: new = T @ old)."""
    T = np.asarray(T, float)
    R, t = T[:3, :3], T[:3, 3]
    out = []
    for g in regions:
        corners = np.asarray(g["corners"], float) @ R.T + t
        room = np.asarray(g["room_point"], float) @ R.T + t
        h = plane_of(corners, room)
        out.append({**g, **h, "corners": corners.tolist(), "room_point": room.tolist()})
    return out


def load_regions(project_dir, run_id, room_point):
    """Applied mirror regions of a run, in the MODEL frame (room_point: a point inside the room, e.g. the start
    view). Records: {"mirror_id", "corners" (model frame), "cap" (default true), "spread", "depth_m", "run_id"}."""
    p = Path(project_dir) / MIRRORS_FILE
    if not p.exists():
        return []
    recs = [r for r in json.loads(p.read_text(encoding="utf-8")) if r.get("run_id") in (None, run_id)]
    out = []
    for r in recs:
        g = plane_of(r["corners"], room_point)
        params = {"spread": float(r.get("spread", SPREAD)), "depth": float(r.get("depth_m", DEPTH_M))}
        out.append({**g, "mirror_id": r["mirror_id"], "corners": r["corners"], "room_point": list(room_point),
                    "cap": bool(r.get("cap", True)), "params": params})
    return out


# ------------------------------------------------------------------------------------------------ caps
def cap_triangles(g):
    """Flat cap on the mirror plane: (vertices (k,3), triangles (m,3)), or (None, None) if the outline crosses itself."""
    from namma.studio.mesh_repair import _ear_clip
    tris = _ear_clip(g["poly2"])
    if not tris:
        return None, None
    V = g["c"] + g["poly2"][:, :1] * g["e1"] + g["poly2"][:, 1:] * g["e2"]
    return V, np.array(tris, np.int64)


def polygon_area(poly2):
    x, y = poly2[:, 0], poly2[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


def wall_points(g, *, res=0.025, heights=(0.2, 1.7)):
    """Points making the mirror a WALL for the walking maps: the outline's horizontal span (at the floor), repeated at
    heights inside every body band (model frame, metres above the floor; y = up)."""
    V = g["c"] + g["poly2"][:, :1] * g["e1"] + g["poly2"][:, 1:] * g["e2"]
    hz = np.cross([0.0, 1.0, 0.0], g["n"])
    if np.linalg.norm(hz) < 1e-6:                     # a mirror lying flat (on a table): not a wall
        return np.zeros((0, 3))
    hz /= np.linalg.norm(hz)
    s = (V - g["c"]) @ hz
    xs = np.arange(s.min() - GROW_M, s.max() + GROW_M + res, res)
    hs = np.arange(heights[0], heights[1] + 1e-9, 0.1)
    base = g["c"] - g["c"][1] * np.array([0.0, 1.0, 0.0])
    P = (base + xs[:, None] * hz)[:, None, :].repeat(len(hs), 1)
    P[..., 1] = hs[None, :]
    return P.reshape(-1, 3)


# ------------------------------------------------------------------------------------------------ mesh
def cut_mesh(glb, regions, *, cap=True):
    """Remove triangles reflected in the regions (mesh frame) and add a flat cap per region as a separate,
    vertex-coloured primitive (material "namma-inferred-mirror"). Positions and UVs are not changed.
    -> (glb bytes, stats). Unchanged (stats["skipped"]) for meshes with node transforms."""
    from namma.studio.mesh_import import _accessor, _parse_glb
    from namma.studio.mesh_repair import _srgb_to_linear, _texture
    from namma.studio.mesh_tidy import _rebuild, glb_bytes
    stats = {"regions": len(regions), "removed_triangles": 0, "removed_area_m2": 0.0, "inferred_area_m2": 0.0,
             "per_region": []}
    if not regions:
        return glb, stats
    doc, binary = _parse_glb(glb)
    if any(k in n for n in doc.get("nodes", []) for k in ("matrix", "rotation", "scale", "translation")):
        stats["skipped"] = "meshes with node transforms are not cut"
        return glb, stats
    prims = [p for m in doc.get("meshes", []) for p in m["primitives"] if p.get("mode", 4) == 4 and "indices" in p]
    per = [{"mirror_id": g.get("mirror_id"), "removed_triangles": 0, "removed_area_m2": 0.0, "inferred_area_m2": 0.0,
            "removed_outside_outline_m2": 0.0, "wall_support_m2": 0.0} for g in regions]
    add, targets, ring_cols = [], [], [[] for _ in regions]
    for prim in prims:
        V = _accessor(doc, binary, prim["attributes"]["POSITION"]).astype(np.float64)
        F = _accessor(doc, binary, prim["indices"]).reshape(-1, 3).astype(np.int64)
        if not len(F):
            continue
        cen = V[F].mean(1)
        area = np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1) / 2
        drop = np.zeros(len(F), bool)
        tex = None
        if "TEXCOORD_0" in prim["attributes"]:
            try:
                tex = _texture(doc, binary, prim)
            except Exception:                                  # noqa: BLE001 - colour is optional
                tex = None
        for k, g in enumerate(regions):
            b = behind(cen, g, **{kk: v for kk, v in g.get("params", {}).items() if kk in ("spread", "depth")})
            drop |= b
            per[k]["removed_triangles"] += int(b.sum())
            per[k]["removed_area_m2"] += float(area[b].sum())
            rel_ = cen - g["c"]
            q_ = rel_ @ np.c_[g["e1"], g["e2"]]
            out_ = _outline_distance(g["poly2"], q_) > 0
            # removed although its straight projection misses the mirror: grazing reflection - or a REAL room behind
            # the wall (through a door next to the mirror). Reported so a person can check (lower "spread" if real).
            per[k]["removed_outside_outline_m2"] += float(area[b & out_].sum())
            # real wall right around the outline: none means the corners were picked on the reflection, not the wall
            ring_ = (np.abs(rel_ @ g["n"]) < 0.05) & out_ & (_outline_distance(g["poly2"], q_) < 0.3)
            per[k]["wall_support_m2"] += float(area[ring_].sum())
            if tex is not None:                                # colour of the wall right around the mirror
                rel = cen - g["c"]
                on = np.abs(rel @ g["n"]) < 0.05
                if on.any():
                    q = rel[on] @ np.c_[g["e1"], g["e2"]]
                    dist = _outline_distance(g["poly2"], q)
                    ring = np.flatnonzero(on)[(dist > GROW_M) & (dist < 0.3)]
                    if len(ring):
                        from namma.studio.mesh_repair import _face_colours
                        UV = _accessor(doc, binary, prim["attributes"]["TEXCOORD_0"])
                        ring_cols[k].append(_face_colours(tex, UV, F[ring]))
        if drop.any():
            add.append(F[~drop].astype("<u4").tobytes())
            targets.append((prim, int((~drop).sum())))
    fills = []
    for k, g in enumerate(regions):
        if not (cap and g.get("cap", True)):
            continue
        Vc, Tc = cap_triangles(g)
        if Vc is None:
            per[k]["cap"] = "outline crosses itself: not capped"
            continue
        col = np.median(np.vstack(ring_cols[k]), axis=0) if ring_cols[k] else np.array(CAP_COLOUR, float)
        fills.append((Vc, Tc, col))
        per[k]["inferred_area_m2"] = polygon_area(g["poly2"])
        per[k]["cap_colour_rgb"] = [int(x) for x in col]
    for k in range(len(per)):
        per[k] = {kk: (round(v, 3) if isinstance(v, float) else v) for kk, v in per[k].items()}
    stats.update(per_region=per, removed_triangles=sum(p["removed_triangles"] for p in per),
                 removed_area_m2=round(sum(p["removed_area_m2"] for p in per), 3),
                 inferred_area_m2=round(sum(p["inferred_area_m2"] for p in per), 3))
    if not add and not fills:
        return glb, stats
    if fills:
        fv = np.vstack([v for v, _, _ in fills]).astype("<f4")
        fc = np.vstack([np.tile(_srgb_to_linear(c), (len(v), 1)) for v, _, c in fills]).astype("<f4")
        base = np.cumsum([0] + [len(v) for v, _, _ in fills])[:-1]
        fi = np.vstack([t + b for (_, t, _), b in zip(fills, base)]).astype("<u4")
        add += [fv.tobytes(), fc.tobytes(), fi.ravel().tobytes()]
    doc, binary, views = _rebuild(doc, binary, add=add)
    acc = doc["accessors"]
    for (prim, count), v in zip(targets, views):
        acc.append({"bufferView": v, "componentType": 5125, "count": count * 3, "type": "SCALAR"})
        prim["indices"] = len(acc) - 1
    if fills:
        v_pos, v_col, v_idx = views[len(targets):len(targets) + 3]
        acc.append({"bufferView": v_pos, "componentType": 5126, "count": len(fv), "type": "VEC3",
                    "min": fv.min(0).tolist(), "max": fv.max(0).tolist()})
        acc.append({"bufferView": v_col, "componentType": 5126, "count": len(fv), "type": "VEC3"})
        acc.append({"bufferView": v_idx, "componentType": 5125, "count": int(fi.size), "type": "SCALAR"})
        doc.setdefault("materials", []).append({"name": "namma-inferred-mirror", "doubleSided": True,
                                                "extras": {"inferred": True, "reason": "mirror cap"},
                                                "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0}})
        doc["meshes"][0]["primitives"].append({"attributes": {"POSITION": len(acc) - 3, "COLOR_0": len(acc) - 2},
                                               "indices": len(acc) - 1, "material": len(doc["materials"]) - 1,
                                               "extras": {"inferred": True}})
    stats["applied"] = True
    return glb_bytes(doc, binary), stats


# ------------------------------------------------------------------------------------------------ splat masks
def _clip_near(P, near=0.05):
    """Sutherland-Hodgman clip of a camera-frame polygon against z >= near."""
    out = []
    for i in range(len(P)):
        a, b = P[i - 1], P[i]
        ina, inb = a[2] >= near, b[2] >= near
        if ina != inb:
            t = (near - a[2]) / (b[2] - a[2])
            out.append(a + t * (b - a))
        if inb:
            out.append(b)
    return np.array(out)


def mirror_mask(R, t, K, width, height, regions, *, dilate_px=3):
    """Uint8 mask (height, width) for one camera (world -> camera: x_c = R x + t; K 3x3): 0 where a mirror is seen
    (reflections: excluded from training), 255 elsewhere."""
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("L", (int(width), int(height)), 255)
    draw = ImageDraw.Draw(img)
    hit = False
    for g in regions:
        corners = np.asarray(g["corners"], float)
        cc = corners @ np.asarray(R, float).T + np.asarray(t, float)
        # only when the camera is on the room side of the mirror (behind it, the glass is not seen)
        cam = -np.asarray(R, float).T @ np.asarray(t, float)
        if (cam - g["c"]) @ g["n"] <= 0:
            continue
        cc = _clip_near(cc)
        if len(cc) < 3:
            continue
        uv = (cc @ np.asarray(K, float).T)
        uv = uv[:, :2] / uv[:, 2:3]
        uv = np.clip(uv, -4 * width, 5 * width)          # keep PIL's integer maths in range
        draw.polygon([(float(u), float(v)) for u, v in uv], fill=0)
        hit = True
    if hit and dilate_px:
        img = img.filter(ImageFilter.MinFilter(2 * dilate_px + 1))
    return np.asarray(img), hit


def _quat_to_rot(w, x, y, z):
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def write_masks(sparse_dir, regions, out_dir):
    """Masks for every training camera of a COLMAP text model (PINHOLE) -> out_dir/<photo stem>.png, only for photos
    that see a mirror. -> {"photos": n, "masked_photos": m, "masked_pixel_share": s}"""
    from PIL import Image
    sparse_dir, out_dir = Path(sparse_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cams = {}
    for ln in (sparse_dir / "cameras.txt").read_text(encoding="utf-8").splitlines():
        p = ln.split()
        if p and not p[0].startswith("#"):
            w, h, fx, fy, cx, cy = int(p[2]), int(p[3]), *map(float, p[4:8])
            cams[int(p[0])] = (w, h, np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]]))
    n = m = 0
    share = []
    for ln in (sparse_dir / "images.txt").read_text(encoding="utf-8").splitlines():
        p = ln.split()
        if len(p) < 10 or ln.startswith("#"):
            continue
        n += 1
        R = _quat_to_rot(*map(float, p[1:5]))
        t = np.array([float(v) for v in p[5:8]])
        w, h, K = cams[int(p[8])]
        mask, hit = mirror_mask(R, t, K, w, h, regions)
        if hit and (mask == 0).any():
            Image.fromarray(mask).save(out_dir / f"{Path(' '.join(p[9:])).stem}.png")
            m += 1
            share.append(float((mask == 0).mean()))
    return {"photos": n, "masked_photos": m, "masked_pixel_share": round(float(np.mean(share)) if share else 0.0, 4)}


# ------------------------------------------------------------------------------------------------ splat cull + cap
def cull_and_cap_splat(ply_in, ply_out, regions, *, spacing=0.03, cap=True):
    """Remove Gaussians reflected in the regions (splat frame) and lay a flat cap of Gaussians on each mirror plane,
    coloured like the solid splats on the wall around it. -> stats. The cap's splats are listed as INFERRED."""
    from namma.studio.splat_spz import _ply_fields
    f = _ply_fields(ply_in)
    props = list(f.keys())
    P = np.c_[f["x"], f["y"], f["z"]].astype(np.float64)
    op = 1 / (1 + np.exp(-f["opacity"].astype(np.float64)))
    drop = np.zeros(len(P), bool)
    stats = {"splats_in": int(len(P)), "per_region": []}
    caps = []
    C0 = 0.28209479177387814
    for g in regions:
        b = behind(P, g, **{k: v for k, v in g.get("params", {}).items() if k in ("spread", "depth")})
        drop |= b
        info = {"mirror_id": g.get("mirror_id"), "removed_splats": int(b.sum()), "cap_splats": 0}
        if cap and g.get("cap", True):
            rel = P - g["c"]
            on = (np.abs(rel @ g["n"]) < 0.05) & (op > 0.5) & ~b
            col = None
            if on.any():
                q = rel[on] @ np.c_[g["e1"], g["e2"]]
                dist = _outline_distance(g["poly2"], q)
                ring = np.flatnonzero(on)[(dist > GROW_M) & (dist < 0.3)]
                if len(ring) >= 10:
                    col = np.median(np.c_[f["f_dc_0"][ring], f["f_dc_1"][ring], f["f_dc_2"][ring]], axis=0)
            if col is None:
                col = (np.array(CAP_COLOUR, float) / 255 - 0.5) / C0
            lo, hi = g["poly2"].min(0), g["poly2"].max(0)
            us, vs = np.meshgrid(np.arange(lo[0], hi[0] + 1e-9, spacing), np.arange(lo[1], hi[1] + 1e-9, spacing))
            q = np.c_[us.ravel(), vs.ravel()]
            q = q[_inside(g["poly2"], q)]
            if len(q):
                caps.append((g["c"] + q[:, :1] * g["e1"] + q[:, 1:] * g["e2"], g, col))
                info["cap_splats"] = int(len(q))
                info["inferred_area_m2"] = round(polygon_area(g["poly2"]), 3)
        stats["per_region"].append(info)
    keep = ~drop
    cols = {k: np.asarray(v)[keep] for k, v in f.items()}
    if caps:
        from namma.studio.splat_large import _rot_to_quat
        add = {k: [] for k in props}
        for Q, g, col in caps:
            k = len(Q)
            q = _rot_to_quat(np.c_[g["e1"], g["e2"], g["n"]])     # local x, y -> along the wall; z -> normal
            vals = {"x": Q[:, 0], "y": Q[:, 1], "z": Q[:, 2], "opacity": np.full(k, np.log(0.95 / 0.05)),
                    "scale_0": np.full(k, np.log(0.7 * spacing)), "scale_1": np.full(k, np.log(0.7 * spacing)),
                    "scale_2": np.full(k, np.log(0.002)), "rot_0": np.full(k, q[0]), "rot_1": np.full(k, q[1]),
                    "rot_2": np.full(k, q[2]), "rot_3": np.full(k, q[3]),
                    "f_dc_0": np.full(k, col[0]), "f_dc_1": np.full(k, col[1]), "f_dc_2": np.full(k, col[2])}
            for name in props:
                add[name].append(vals.get(name, np.zeros(k)))
        cols = {k: np.concatenate([cols[k], *add[k]]) for k in props}
    n = len(cols["x"])
    arr = np.empty(n, dtype=[(p, "<f4") for p in props])
    for p in props:
        arr[p] = cols[p]
    head = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n" % n + \
        "".join(f"property float {p}\n" for p in props) + "end_header\n"
    Path(ply_out).write_bytes(head.encode("ascii") + arr.tobytes())
    stats.update(removed_splats=int(drop.sum()), cap_splats=sum(c["cap_splats"] for c in stats["per_region"]),
                 splats_out=int(n), inferred_area_m2=round(sum(c.get("inferred_area_m2", 0) for c in stats["per_region"]), 3))
    return stats


# ------------------------------------------------------------------------------------------------ suggestions
def suggest_mirrors(V, F, *, beyond_m=0.15, min_area_m2=0.25, cell=0.1):
    """Conservative hints (never applied): surface lying clearly BEHIND a room's main wall plane, inside that wall's
    span and between floor and ceiling. Each main wall is the plane on its side of the room with the most surface just
    under the ceiling (not the outermost or the largest: a phantom room behind a mirror can be both). Real windows, doors and alcoves also show
    up here - which is why a person decides. -> [{"wall_facing_deg", "centre", "width_m", "height_m", "area_m2",
    "corners" (rectangle on the wall plane, mesh frame), "kind": "suggestion"}]"""
    from scipy import ndimage
    from namma.studio.mesh_repair import _faces_geometry, _outer_walls, _room_frame, fit_ceiling
    V = np.asarray(V, float)
    F = np.asarray(F, np.int64)
    fit = fit_ceiling(V, F)
    if fit is None:
        return []
    n, d, _ = fit
    fr = _room_frame(V, F, n, d, np.unique(F))
    N, area, cen = fr["N"], fr["area"], fr["cen"]
    walls = _outer_walls(n, fr)
    # the room's middle from its CEILING (a reflection is seen below it), not from all vertices (pulled by a phantom)
    ceil = (np.abs(N @ n) > 0.9) & (cen @ n > fr["ceil_h"] - 0.1) & (area > 0)
    middle = np.average(cen[ceil], axis=0, weights=area[ceil]) if area[ceil].sum() > 0.5 else fr["centre"]
    out = []
    for u in {tuple(np.round(w["u"], 6)) for w in walls}:
        u = np.asarray(u)
        steep = (np.abs(N @ n) < 0.25) & (np.abs(np.abs(N @ u) - 1) < 0.1) & (area > 0)
        s = cen @ u
        sc = middle @ u
        edges = np.arange(s.min() - 0.05, s.max() + 0.06, 0.02)
        # a REAL wall reaches the ceiling; a reflection seen through a mirror below the ceiling does not (and may be
        # larger than the real wall), so walls are ranked by their surface in the top 30 cm under the ceiling
        top = steep & (cen @ n > fr["ceil_h"] - 0.3)
        hist = np.convolve(np.histogram(s[top], bins=edges, weights=area[top])[0], np.ones(5), mode="same")
        mids = (edges[:-1] + edges[1:]) / 2
        side_ax = np.cross(n, u)
        for sign in (1.0, -1.0):
            sel = (mids - sc) * sign > 0.5
            if not sel.any() or hist[sel].max() < 0.2:
                continue
            s_wall = float(mids[sel][np.argmax(hist[sel])])
            on_wall = steep & (np.abs(s - s_wall) < 0.05)
            t_wall = cen[on_wall] @ side_ax
            if len(t_wall) < 10:
                continue
            h = cen @ n
            far = ((s - s_wall) * sign > beyond_m) & (h > fr["floor_h"] + 0.05) & (h < fr["ceil_h"] - 0.05) \
                & (cen @ side_ax > t_wall.min()) & (cen @ side_ax < t_wall.max())
            if area[far].sum() < min_area_m2:
                continue
            tq, hq = cen[far] @ side_ax, h[far]
            t0, h0 = tq.min(), hq.min()
            gi = ((tq - t0) / cell).astype(int)
            hi = ((hq - h0) / cell).astype(int)
            grid = np.zeros((hi.max() + 1, gi.max() + 1))
            np.add.at(grid, (hi, gi), area[far])
            lab, k = ndimage.label(grid > 0, structure=np.ones((3, 3)))
            for j in range(1, k + 1):
                a = float(grid[lab == j].sum())
                if a < min_area_m2:
                    continue
                rows, colsj = np.nonzero(lab == j)
                ta, tb = t0 + colsj.min() * cell, t0 + (colsj.max() + 1) * cell
                ha, hb = h0 + rows.min() * cell, h0 + (rows.max() + 1) * cell
                # the rectangle on the wall plane (the phantom spreads behind the glass: the real mirror is smaller)
                base = u * s_wall
                corners = [(base + side_ax * tt + n * hh).tolist() for tt, hh in ((ta, ha), (tb, ha), (tb, hb), (ta, hb))]
                out.append({"kind": "suggestion", "wall_offset_m": round(s_wall, 3), "behind_m_min": beyond_m,
                            "area_m2": round(a, 2), "width_m": round(tb - ta, 2), "height_m": round(hb - ha, 2),
                            "centre": np.round(np.mean(corners, axis=0), 3).tolist(), "corners": corners,
                            "note": "surface behind this wall: a mirror, or a real window / door / alcove - check it"})
    return out


# ------------------------------------------------------------------------------------------------ apply to a run
MIRROR_CODES = ("MIRROR-REGIONS", "SPLAT-PREDATES-MIRRORS")
MIRROR_CHECK_CODES = ("MIRROR-CUT-WIDE", "MIRROR-NO-WALL", "MIRROR-FLOOR-DROPPED")


def mirror_checks(cut, nav_diag=None):
    """Warnings for places where a mirror cut could have hit something REAL (a person must look)."""
    out = []
    for r in cut.get("per_region", []):
        area = r.get("inferred_area_m2") or 0.0
        if r.get("removed_outside_outline_m2", 0) > 2 * area + 1.0:
            out.append({"code": "MIRROR-CUT-WIDE", "severity": "warning", "mirror_id": r.get("mirror_id"),
                        "message": f"{r['mirror_id']}: {r['removed_outside_outline_m2']:.1f} m2 was removed beside the "
                                   "mirror's outline (reflections seen at a steep angle - or a real room behind the wall, "
                                   "e.g. through a door). Check it; if real, lower that mirror's \"spread\" and apply again."})
        if r.get("wall_support_m2", 0) < 0.05:
            out.append({"code": "MIRROR-NO-WALL", "severity": "warning", "mirror_id": r.get("mirror_id"),
                        "message": f"{r['mirror_id']}: there is almost no scanned wall around its outline - the corners "
                                   "may have been picked on the reflection instead of the wall. Re-mark it on the wall."})
    dropped = (nav_diag or {}).get("seed_dropped_m2", 0)
    if dropped > 1.0:
        out.append({"code": "MIRROR-FLOOR-DROPPED", "severity": "warning",
                    "message": f"{dropped:.1f} m2 of floor not connected to the start view was left out of the walking "
                               "map (reflected floor - or a real area reached only through an unscanned doorway)."})
    return out


def apply_to_run(project_dir, run_id, *, log=None):
    """Apply the project's mirror regions to an IMPORTED run (namma.studio.mesh_import): mesh cut + inferred caps,
    walking maps rebuilt without reflected floor and with each mirror as a wall, quality report and package updated.
    Reversible: the first application keeps 60_texture/lod*.glb as lod*_before_mirrors.glb and every application
    starts from those, so removing every region and applying again restores the original model.
    -> {"regions", "removed_triangles", "removed_area_m2", "inferred_area_m2", "geometry_behind_after_m2", ...}"""
    from namma.core import timefmt
    from namma.core.jsonio import load_strict, write_json
    from namma.recon import modelframe
    from namma.studio import mesh_import as mi
    from namma.studio.mesh_tidy import downscale_textures
    from namma.studio.package import build_scene_package
    from namma.studio.pipeline import RunContext
    log = log or (lambda *_: None)
    pdir = Path(project_dir)
    run_dir = pdir / "runs" / run_id
    cfg = load_strict(run_dir / "config.json")["config"]
    if cfg.get("profile") != "import":
        raise ValueError("mirror regions are applied to imported 3D scans only (photo reconstructions: not yet)")
    ctx = RunContext(pdir, run_id, cfg)
    tex, fdir = ctx.dir("texture"), ctx.dir("frame")
    frame = load_strict(fdir / "frame.json")
    T = np.asarray(frame["T_model_from_sfm"], float)
    regions = load_regions(pdir, run_id, frame["start"]["position"])
    for name in ("lod0.glb", "lod1.glb"):                    # originals, once
        src, bak = tex / name, tex / name.replace(".glb", "_before_mirrors.glb")
        if src.exists() and not bak.exists():
            bak.write_bytes(src.read_bytes())
    base = (tex / "lod0_before_mirrors.glb").read_bytes()
    regions_mesh = transform_regions(regions, np.linalg.inv(T))
    glb, cut = cut_mesh(base, regions_mesh, cap=True)
    (tex / "lod0.glb").write_bytes(glb)
    summ = load_strict(tex / "texture_summary.json")
    V, F, mstats = mi.mesh_triangles(glb)
    for lod in summ["lods"]:
        if lod["lod"] == 0:
            lod.update(bytes=len(glb), triangles=mstats["triangles"])
        elif lod["lod"] == 1 and (tex / "lod1_before_mirrors.glb").exists():
            lod1, _ = downscale_textures(glb, 2048)
            (tex / "lod1.glb").write_bytes(lod1)
            lod.update(bytes=len(lod1), triangles=mstats["triangles"])
    if regions:
        summ["mirrors"] = cut
    else:
        summ.pop("mirrors", None)
    write_json(tex / "texture_summary.json", summ)
    # walking maps: reflected surface out (already cut), each mirror is a wall
    Vs_, Fs_, _ = mi.mesh_triangles(glb, scanned_only=True)          # never the repair's backing sheets / caps
    P, N, _ = mi.sample_surface(Vs_, Fs_, int(cfg.get("samples", 600_000)))
    pm = P @ T[:3, :3].T + T[:3, 3]
    nm = N @ T[:3, :3].T
    keep = ~behind_any(pm, regions)
    pn, _tilt = mi.floor_relative(pm[keep], nm[keep])
    walls = [wall_points(g) for g in regions]
    if walls:
        pn = np.vstack([pn, *walls])
    eye = float(frame["eye_height_units"])
    grid, gstats, _ = modelframe.roam_grid(pn, np.array([frame["start"]["position"]], float), eye)
    write_json(fdir / "roam_grid.json", grid)
    frame["roam_stats"] = gstats
    frame["floor_coverage"] = round(gstats["observed_floor_in_footprint_cells"] / max(1, gstats["footprint_cells"]), 4)
    write_json(fdir / "frame.json", frame)
    nd = ctx.dir("navgeom")
    nd.mkdir(parents=True, exist_ok=True)
    (nd / "geometry.if04.json").unlink(missing_ok=True)      # never leave a stale map behind a failed rebuild
    # with mirrors, walkable floor must also be CONNECTED to where the visitor stands (reflected floor next to the
    # glass, beyond the cut, is otherwise a separate "walkable" patch); without mirrors: unchanged
    mi.write_navgeom(nd, pn, run_id=run_id, app=cfg.get("app", "3D scan"), units_m=float(cfg.get("units_m", 1.0)),
                     pipeline_version=cfg.get("pipeline_version", "import"),
                     seed_xz=[frame["start"]["position"][0], frame["start"]["position"][2]] if regions else None)
    # quality report: what was removed and what is INFERRED
    qpath = ctx.dir("quality") / "quality_report.json"
    q = load_strict(qpath)
    facts = q.setdefault("facts", {})
    old_health = facts.get("model_health", {})
    health, hw = mi.health_report(glb, T, floor_tilt_deg=old_health.get("floor_tilt_deg"))
    if "mesh_before_repair" in old_health:
        health["mesh_before_repair"] = old_health["mesh_before_repair"]
    cen = V[F].mean(1)
    tri_area = np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1) / 2
    left = float(tri_area[behind_any(cen, regions_mesh)].sum()) if regions else 0.0
    health["geometry_behind_mirrors_m2"] = round(left, 4)
    # residue check with a much wider reach (grazing reflections; also real rooms behind the wall - read with care)
    wide = np.zeros(len(cen), bool)
    for g in regions_mesh:
        wide |= behind(cen, g, spread=4.0, depth=g.get("params", {}).get("depth", DEPTH_M))
    health["geometry_behind_wall_m2"] = round(float(tri_area[wide].sum()), 4)
    facts["model_health"] = health
    q["warnings"] = [w for w in q.get("warnings", []) if w.get("code") not in MIRROR_CODES + MIRROR_CHECK_CODES + ("MIRROR-SUSPECTED",)
                     and w.get("code") not in {x["code"] for x in hw}] + hw
    splat_json = tex / "splat.json"
    splat_stats = load_strict(splat_json).get("mirrors") if splat_json.exists() else None
    nav_diag = (load_strict(nd / "navgeom.json").get("diagnostics") or {}) if (nd / "navgeom.json").exists() else {}
    checks = mirror_checks(cut, nav_diag) if regions else []
    q["warnings"] += checks
    if regions:
        facts["mirrors"] = {"regions": len(regions), "checks": [c["code"] for c in checks], "removed_triangles": cut["removed_triangles"],
                            "geometry_removed_m2": cut["removed_area_m2"], "inferred_area_m2": cut["inferred_area_m2"],
                            "splats_excluded": (splat_stats or {}).get("removed_splats"),
                            "masked_photos": (splat_stats or {}).get("masked_photos"),
                            "geometry_behind_after_m2": round(left, 4), "applied_at": timefmt.now_iso()}
        q["warnings"].append({"code": "MIRROR-REGIONS", "severity": "info",
                              "message": f"{len(regions)} mirror region(s): {cut['removed_area_m2']:.2f} m2 of reflected "
                                         f"geometry removed; {cut['inferred_area_m2']:.2f} m2 closed with an INFERRED flat "
                                         "surface (not scanned). Walking maps treat each mirror as a wall."})
        if splat_json.exists() and not splat_stats:
            q["warnings"].append({"code": "SPLAT-PREDATES-MIRRORS", "severity": "info",
                                  "message": "The photoreal view was made before the mirrors were marked and still "
                                             "shows the reflected room: make the photoreal view again."})
    else:
        facts.pop("mirrors", None)
    write_json(qpath, q)
    build_scene_package(ctx, ctx.dir("package"))
    log(f"mirrors: {len(regions)} region(s), {cut['removed_area_m2']} m2 removed, {cut['inferred_area_m2']} m2 inferred")
    return {"regions": len(regions), **{k: cut[k] for k in ("removed_triangles", "removed_area_m2", "inferred_area_m2")},
            "geometry_behind_after_m2": round(left, 4), "per_region": cut["per_region"]}
