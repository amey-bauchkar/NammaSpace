"""Estimated room surfaces ("fill") for holes in floor, walls and ceiling - clearly separate from scanned geometry.

Photo reconstruction leaves holes where surfaces have no visible detail (plain paint, glossy tiles). Most rooms are
boxes, so the room's planes can be found from the points that WERE reconstructed and the holes closed with flat
surfaces. The fill is a separate layer (fill.glb) and is always labelled "estimated" in the viewer.

Method (all in the model frame: +Y up, floor at y = 0, units = model units, eye = capture eye height):
1. Room orientation: vertical surfaces vote with the horizontal direction of their normals (angle mod 90 deg).
2. Wall positions: in the rotated frame, the outermost strong peak of the point histogram on each side.
3. Ceiling height: robust top of the points inside the footprint.
4. For each of the 6 planes: a grid of cells; a cell is covered when reconstructed points lie within a thin slab
   of the plane there. Only uncovered cells become fill quads, slightly behind the plane so real geometry wins.
5. Colour: per-cell mean of nearby real points on that plane (nearest covered cell), else the plane median.
Assumes one rectangular room; `fit` reports how well the box explains the points (low = do not trust the fill).
"""
from pathlib import Path

import numpy as np
from scipy import ndimage

from namma.recon.glb import write_glb


def to_model(points, T):
    T = np.asarray(T, dtype=np.float64).reshape(4, 4)
    return points @ T[:3, :3].T + T[:3, 3]


def room_orientation(normals_model, step_deg=0.5):
    """Angle (rad) of the room's wall directions from horizontal normals of vertical surfaces."""
    n = normals_model
    vert = np.abs(n[:, 1]) < 0.3
    a = np.arctan2(n[vert, 2], n[vert, 0])
    a = np.mod(a, np.pi / 2)
    bins = np.arange(0, np.pi / 2 + 1e-9, np.radians(step_deg))
    h, e = np.histogram(a, bins=bins)
    h = ndimage.uniform_filter1d(h.astype(float), 5, mode="wrap")
    return float((e[np.argmax(h)] + e[np.argmax(h) + 1]) / 2), int(vert.sum())


def _outer_peak(vals, lo_side, cell, frac=0.3, window=0.35):
    lo, hi = np.percentile(vals, [0.5, 99.5])
    edges = np.arange(lo - 2 * cell, hi + 2 * cell, cell)
    h, e = np.histogram(vals, bins=edges)
    span = hi - lo
    if lo_side:
        sel = np.where(e[:-1] <= lo + window * span)[0]
    else:
        sel = np.where(e[1:] >= hi - window * span)[0]
    if not len(sel):
        return lo if lo_side else hi
    peak = h[sel].max()
    strong = sel[h[sel] >= frac * peak]
    k = strong.min() if lo_side else strong.max()
    return float((e[k] + e[k + 1]) / 2)


def fit_room_box(P, N, eye):
    """P, N: points / normals in the model frame. Returns the box description."""
    theta, n_vert = room_orientation(N)
    c, s = np.cos(theta), np.sin(theta)
    R = np.array([[c, s], [-s, c]])                          # model xz -> room uv
    band = (P[:, 1] > 0.15 * eye) & (P[:, 1] < 1.6 * eye)
    uv = P[band][:, [0, 2]] @ R.T
    cell = 0.03 * eye
    u0, u1 = _outer_peak(uv[:, 0], True, cell), _outer_peak(uv[:, 0], False, cell)
    v0, v1 = _outer_peak(uv[:, 1], True, cell), _outer_peak(uv[:, 1], False, cell)
    inside = P[(P[:, 1] > 0.5 * eye)]
    uvi = inside[:, [0, 2]] @ R.T
    m = (uvi[:, 0] > u0) & (uvi[:, 0] < u1) & (uvi[:, 1] > v0) & (uvi[:, 1] < v1)
    height = float(np.percentile(inside[m, 1], 99.5)) if m.sum() > 100 else 2.0 * eye
    height = float(np.clip(height, 1.3 * eye, 3.0 * eye))
    # fit: share of band points within 0.1 eye of a wall plane, among points near the box boundary
    d = np.minimum.reduce([np.abs(uv[:, 0] - u0), np.abs(uv[:, 0] - u1), np.abs(uv[:, 1] - v0), np.abs(uv[:, 1] - v1)])
    outside = ((uv[:, 0] < u0 - 0.15 * eye) | (uv[:, 0] > u1 + 0.15 * eye) |
               (uv[:, 1] < v0 - 0.15 * eye) | (uv[:, 1] > v1 + 0.15 * eye)).mean()
    return {"theta": theta, "R": R.tolist(), "u": [u0, u1], "v": [v0, v1], "height": height,
            "eye": eye, "vertical_normals": n_vert, "near_wall_share": float((d < 0.1 * eye).mean()),
            "outside_share": float(outside)}


def _planes(box):
    (u0, u1), (v0, v1), H = box["u"], box["v"], box["height"]
    # each plane: origin (u, y, v), axis a, axis b (in uv-y space), lengths, normal coordinate to test
    return [
        {"name": "floor", "fixed": ("y", 0.0), "a": ("u", u0, u1), "b": ("v", v0, v1), "out": -1},
        {"name": "wall_u0", "fixed": ("u", u0), "a": ("v", v0, v1), "b": ("y", 0.0, H), "out": -1},
        {"name": "wall_u1", "fixed": ("u", u1), "a": ("v", v0, v1), "b": ("y", 0.0, H), "out": +1},
        {"name": "wall_v0", "fixed": ("v", v0), "a": ("u", u0, u1), "b": ("y", 0.0, H), "out": -1},
        {"name": "wall_v1", "fixed": ("v", v1), "a": ("u", u0, u1), "b": ("y", 0.0, H), "out": +1},
        {"name": "ceiling", "fixed": ("y", H), "a": ("u", u0, u1), "b": ("v", v0, v1), "out": +1},
    ]


def build_fill(P, C, box, N=None, photo_sampler=None, cell_eye=0.06, slab_eye=0.12, min_pts=3, offset_eye=0.01, min_colour_pts=200):
    """Return vertices (model frame), colours (0-1), triangle indices and per-plane statistics.

    Coverage uses every point near the plane (furniture in front also hides the wall). Colour uses only points
    whose normal faces the plane's direction (the wall / floor surface itself, not curtains or boxes in front)."""
    eye = box["eye"]
    R = np.array(box["R"])
    uv = P[:, [0, 2]] @ R.T
    if N is not None:
        nuv = N[:, [0, 2]] @ R.T
        axis = {"u": np.abs(nuv[:, 0]), "v": np.abs(nuv[:, 1]), "y": np.abs(N[:, 1])}
    plane_med = {}
    coords = {"u": uv[:, 0], "v": uv[:, 1], "y": P[:, 1]}
    cell, slab = cell_eye * eye, slab_eye * eye
    Rinv = R.T                                              # room uv -> model xz
    verts, cols, tris, stats = [], [], [], []
    for pl in _planes(box):
        fk, fval = pl["fixed"]
        ak, a0, a1 = pl["a"]
        bk, b0, b1 = pl["b"]
        na, nb = max(1, int(np.ceil((a1 - a0) / cell))), max(1, int(np.ceil((b1 - b0) / cell)))
        near = np.abs(coords[fk] - fval) < slab
        ia = np.floor((coords[ak][near] - a0) / cell).astype(int)
        ib = np.floor((coords[bk][near] - b0) / cell).astype(int)
        ok = (ia >= 0) & (ia < na) & (ib >= 0) & (ib < nb)
        ia, ib, cc = ia[ok], ib[ok], C[near][ok]
        count = np.zeros((na, nb), int)
        np.add.at(count, (ia, ib), 1)
        covered = count >= min_pts
        # colour from surfaces facing the plane's direction only
        aligned = axis[fk][near][ok] > 0.85 if N is not None else np.ones(len(cc), bool)
        ccount = np.zeros((na, nb), int)
        np.add.at(ccount, (ia[aligned], ib[aligned]), 1)
        csum = np.zeros((na, nb, 3))
        for k in range(3):
            np.add.at(csum[..., k], (ia[aligned], ib[aligned]), cc[aligned][:, k])
        if aligned.sum() >= min_colour_pts:
            median = np.median(cc[aligned], axis=0)
        else:      # e.g. an unseen ceiling: use the walls' paint (filled in after the walls)
            median = None
        plane_med[pl["name"]] = median
        if median is None:
            walls = [m for k, m in plane_med.items() if k.startswith("wall") and m is not None]
            median = np.median(walls, axis=0) if walls else np.array([0.6, 0.6, 0.6])
        has_col = ccount >= min_pts
        colour = np.where(has_col[..., None], csum / np.maximum(ccount, 1)[..., None], np.nan)
        if has_col.any():
            _, (ja, jb) = ndimage.distance_transform_edt(~has_col, return_indices=True)
            colour = colour[ja, jb]
            colour = (colour + median) / 2              # soften: nearest real colour blended with the wall median
        else:
            colour = np.broadcast_to(median, (na, nb, 3)).copy()
        holes = np.argwhere(~covered)
        n_photo = 0
        if photo_sampler is not None and len(holes):
            cen = []
            for i, j in holes:
                p = {fk: fval, ak: a0 + (i + 0.5) * cell, bk: b0 + (j + 0.5) * cell}
                xz = Rinv @ np.array([p["u"], p["v"]])
                cen.append((xz[0], p["y"], xz[1]))
            normal = {"y": (0.0, 1.0, 0.0), "u": tuple(np.r_[Rinv[0, 0], 0, Rinv[1, 0]]), "v": tuple(np.r_[Rinv[0, 1], 0, Rinv[1, 1]])}[fk]
            pc, nv = photo_sampler(np.array(cen), np.array(normal))
            good = nv >= 3
            colour[holes[good, 0], holes[good, 1]] = pc[good]
            n_photo = int(good.sum())
        stats.append({"plane": pl["name"], "cells": int(na * nb), "covered_cells": int(covered.sum()),
                      "filled_cells": int(len(holes)), "covered_share": float(covered.mean()),
                      "filled_cells_coloured_from_photos": n_photo,
                      "median_rgb": [round(float(x), 3) for x in median]})
        f = fval + pl["out"] * offset_eye * eye           # just behind the plane: scanned geometry wins
        # fill = holes grown by one cell (closes the thin cracks at hole borders; the extra ring lies behind the
        # real surface). Vertices are shared on the cell-corner grid; corner colour = mean of its cells -> smooth.
        fillmask = ndimage.binary_dilation(~covered) if covered.any() else ~covered
        corner = np.zeros((na + 1, nb + 1, 3)); cnt = np.zeros((na + 1, nb + 1))
        for da in (0, 1):
            for db in (0, 1):
                corner[da:da + na, db:db + nb] += colour
                cnt[da:da + na, db:db + nb] += 1
        corner /= cnt[..., None]
        index = -np.ones((na + 1, nb + 1), int)
        used = np.zeros((na + 1, nb + 1), bool)
        for i, j in np.argwhere(fillmask):
            used[i:i + 2, j:j + 2] = True
        for i, j in np.argwhere(used):
            index[i, j] = len(verts)
            p = {fk: f, ak: a0 + i * cell, bk: b0 + j * cell}
            xz = Rinv @ np.array([p["u"], p["v"]])
            verts.append((xz[0], p["y"], xz[1]))
            cols.append(corner[i, j])
        # winding: front faces point INTO the room, so from outside (overview) the fill is see-through
        centre = np.array([np.mean(box["u"]), box["height"] / 2, np.mean(box["v"])])
        for i, j in np.argwhere(fillmask):
            q = [index[i, j], index[i + 1, j], index[i + 1, j + 1], index[i, j + 1]]
            a, b, c = (np.array(verts[q[0]]), np.array(verts[q[1]]), np.array(verts[q[2]]))
            n = np.cross(b - a, c - a)
            cm = np.r_[(Rinv @ centre[[0, 2]])[0], centre[1], (Rinv @ centre[[0, 2]])[1]]
            if np.dot(n, cm - a) < 0:
                q = q[::-1]
            tris += [(q[0], q[1], q[2]), (q[0], q[2], q[3])]
    return (np.array(verts, np.float32).reshape(-1, 3), np.clip(np.array(cols, np.float32).reshape(-1, 3), 0, 1),
            np.array(tris, np.uint32).reshape(-1, 3), stats)


def write_fill_glb(path, verts, cols, tris):
    """Double-sided, unlit, vertex-coloured mesh (glTF 2.0 binary)."""
    pos, col, idx = verts.astype("<f4").tobytes(), cols.astype("<f4").tobytes(), tris.astype("<u4").tobytes()
    binary = pos + col + idx
    doc = {"asset": {"version": "2.0", "generator": "namma.recon.roomfill (estimated surfaces)"},
           "extensionsUsed": ["KHR_materials_unlit"],
           "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "estimated_surfaces"}],
           "materials": [{"name": "estimated_surface", "doubleSided": False, "extensions": {"KHR_materials_unlit": {}},
                          "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0, "roughnessFactor": 1}}],
           "meshes": [{"name": "estimated_surfaces", "primitives": [{"attributes": {"POSITION": 0, "COLOR_0": 1},
                                                                       "indices": 2, "material": 0, "mode": 4}]}],
           "buffers": [{"byteLength": len(binary)}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "target": 34962},
                           {"buffer": 0, "byteOffset": len(pos), "byteLength": len(col), "target": 34962},
                           {"buffer": 0, "byteOffset": len(pos) + len(col), "byteLength": len(idx), "target": 34963}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(verts), "type": "VEC3",
                          "min": verts.min(0).tolist() if len(verts) else [0, 0, 0],
                          "max": verts.max(0).tolist() if len(verts) else [0, 0, 0]},
                         {"bufferView": 1, "componentType": 5126, "count": len(cols), "type": "VEC3"},
                         {"bufferView": 2, "componentType": 5125, "count": int(tris.size), "type": "SCALAR"}]}
    return write_glb(path, doc, binary)


class PhotoSampler:
    """Median colour of a model-frame point over the registered photos that see it (pinhole + OpenCV radial /
    tangential distortion). Views at grazing angles (> 70 deg from the surface normal) are ignored; furniture that
    occludes the wall in some views is out-voted by the views where the wall is visible."""

    def __init__(self, images, cameras, image_dir, T_model_from_sfm, max_side=400):
        from PIL import Image
        Tinv = np.linalg.inv(np.asarray(T_model_from_sfm, float).reshape(4, 4))
        self.Tinv = Tinv
        self.views = []
        for name, im in images.items():
            cam = cameras[im["camera_id"]]
            f = Path(image_dir) / name
            if not f.exists():
                continue
            with Image.open(f) as pil:
                s = max_side / max(pil.size)
                arr = np.asarray(pil.convert("RGB").resize((round(pil.size[0] * s), round(pil.size[1] * s))), np.float32) / 255
            self.views.append({"R": im["R"], "t": im["t"], "C": im["C"], "cam": cam, "s": s, "img": arr})

    def __call__(self, pts_model, normal_model):
        X = pts_model @ self.Tinv[:3, :3].T + self.Tinv[:3, 3]           # model -> SfM frame
        nS = self.Tinv[:3, :3] @ normal_model
        nS /= np.linalg.norm(nS)
        samples = [[] for _ in range(len(X))]
        for v in self.views:
            Xc = X @ v["R"].T + v["t"]
            z = Xc[:, 2]
            ray = X - v["C"]
            cosang = np.abs(ray @ nS) / (np.linalg.norm(ray, axis=1) + 1e-12)
            ok = (z > 1e-6) & (cosang > np.cos(np.radians(70)))
            if not ok.any():
                continue
            x, y = Xc[:, 0] / np.where(z > 1e-6, z, 1), Xc[:, 1] / np.where(z > 1e-6, z, 1)
            p = v["cam"]["params"]
            fx, fy, cx, cy = p[:4]
            if v["cam"]["model"] == "OPENCV":
                k1, k2, p1, p2 = p[4:8]
                r2 = x * x + y * y
                rad = 1 + k1 * r2 + k2 * r2 * r2
                x, y = x * rad + 2 * p1 * x * y + p2 * (r2 + 2 * x * x), y * rad + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
            elif v["cam"]["model"] in ("SIMPLE_RADIAL", "RADIAL"):
                fy, cx, cy = fx, p[1], p[2]
                r2 = x * x + y * y
                x, y = x * (1 + p[3] * r2), y * (1 + p[3] * r2)
            u = (fx * x + cx) * v["s"]
            w = (fy * y + cy) * v["s"]
            H, W = v["img"].shape[:2]
            ok &= (u >= 0.03 * W) & (u < 0.97 * W) & (w >= 0.03 * H) & (w < 0.97 * H)
            for k in np.nonzero(ok)[0]:
                samples[k].append(v["img"][int(w[k]), int(u[k])])
        out = np.zeros((len(X), 3), np.float32)
        nv = np.zeros(len(X), int)
        for k, s in enumerate(samples):
            if s:
                out[k] = np.median(np.array(s), axis=0)
                nv[k] = len(s)
        return out, nv


class BlendSampler:
    """Fill colours computed exactly like the blended texture (namma.recon.blendtex.blend_points)."""

    def __init__(self, views, mesh_vertices_sfm, T_model_from_sfm):
        self.views, self.mesh = views, mesh_vertices_sfm
        self.Tinv = np.linalg.inv(np.asarray(T_model_from_sfm, float).reshape(4, 4))

    def __call__(self, pts_model, normal_model):
        from namma.recon import blendtex
        X = pts_model @ self.Tinv[:3, :3].T + self.Tinv[:3, 3]
        n = self.Tinv[:3, :3] @ normal_model
        n = n / np.linalg.norm(n)
        return blendtex.blend_points(X, np.broadcast_to(n, X.shape), self.views, self.mesh)
