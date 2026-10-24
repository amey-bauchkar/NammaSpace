"""Repair the typical artefacts of a phone-scan GLB (Polycam etc.) from the mesh alone.

    glb2, stats = repair(glb)

A plain white ceiling has no visual texture, so phone scanners (no LiDAR on an iPhone 14) leave it wavy, with sheets
hanging into the room, loose pieces above it and holes through which the black background shows. The fix uses only
geometry and the model's own texture (the Images export could not be tied to the model: docs/POLYCAM.md):

1. Ceiling plane: parallel to the floor, at the height that explains the most downward-facing surface in the upper
   part of the room.
2. Above the ceiling: triangles lying entirely above the plane (+ tolerance) are removed - nothing real is there.
3. Flatten: vertices of downward-facing surface close to the plane are moved onto it (UVs kept, so the texture
   stays); sheets hanging from the ceiling away from the walls are collapsed onto it too, and the triangles that
   became slivers are dropped.
4. Holes: closed boundary loops that are roughly planar are triangulated (ear clipping in the loop's plane) and
   coloured with the median texture colour around the loop, as a separate vertex-coloured primitive.
   Objects hanging from the ceiling (a fan): surface clearly below the plane and NOT ceiling-coloured is removed
   rather than pressed flat (flattened, its dark texture smeared over the ceiling).
   Plain ceiling: the textured surface lying on the plane afterwards is replaced by one sheet in the ceiling's own
   colour (a white ceiling's scan texture is grey blotches; there is no detail on it to keep).
   Straighten: the outer walls and the floor are snapped onto their planes (corners square), and wall pieces in the
   top 15 cm under the ceiling are cut back (a torn wall top showed as a jagged edge; the wall backing shows there).
5. Backings: sheets just behind the ceiling (2 cm, plain colour), the floor (1 cm below its lowest point) and each
   outer wall (2 cm); floor and wall sheets take their colours from the nearest real surface, so gaps that cannot be
   closed safely continue what surrounds them instead of showing the black background.
6. Texture padding: atlas texels no triangle uses (black in phone-app exports) take the colour of the nearest used
   texel, so filtering no longer bleeds black into patch edges (dark specks and seams).

Positions change only where stated; UVs and material are kept; texels used by triangles keep their colour (the
image is re-encoded once, JPEG quality 95).
"""
import io
from collections import defaultdict

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.spatial import ConvexHull

from namma.studio.mesh_import import _accessor, _parse_glb
from namma.studio.mesh_tidy import _rebuild, glb_bytes

UP = np.array([0.0, 1.0, 0.0])


def _faces_geometry(V, F):
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    N = np.cross(B - A, C - A)
    area = np.linalg.norm(N, axis=1) / 2
    N = N / (2 * area[:, None] + 1e-15)
    return N, area, (A + B + C) / 3


def _floor_normal(V, F, N, area, cen):
    """Up direction from the floor (area-weighted plane through upward-facing triangles near the bottom); +Y if none."""
    m = (N @ UP > 0.9) & (cen @ UP < np.percentile(V @ UP, 1) + 0.15) & (area > 0)
    if area[m].sum() < 0.5:
        return UP.copy()
    c = np.average(cen[m], axis=0, weights=area[m])
    n = np.linalg.svd((cen[m] - c) * np.sqrt(area[m])[:, None], full_matrices=False)[2][-1]
    return n if n @ UP > 0 else -n


def fit_ceiling(V, F, *, tol=0.06, bin_m=0.01):
    """Ceiling plane PARALLEL to the floor (rooms are; a free plane fit was pulled off by the waves), at the height
    where most downward-facing surface of the upper room lies within +-tol.
    -> (unit normal n pointing up, offset d: n.p + d = 0 on the plane, stats) or None."""
    N, area, cen = _faces_geometry(V, F)
    n = _floor_normal(V, F, N, area, cen)
    h = cen @ n
    lo, hi = np.percentile(V @ n, 1), np.percentile(V @ n, 99.5)
    cand = (N @ n < -0.6) & (h > lo + 0.6 * (hi - lo)) & (area > 0)
    if area[cand].sum() < 0.5:
        return None
    hc, w = h[cand], area[cand]
    edges = np.arange(hc.min() - tol, hc.max() + tol + bin_m, bin_m)
    hist = np.histogram(hc, bins=edges, weights=w)[0]
    k = int(round(tol / bin_m))
    window = np.convolve(hist, np.ones(2 * k + 1), mode="same")         # area within +-tol of each height
    h0 = (edges[:-1] + bin_m / 2)[int(np.argmax(window))]
    inl = np.abs(hc - h0) < tol
    order = np.argsort(hc[inl])
    cw = np.cumsum(w[inl][order])
    height = float(hc[inl][order][np.searchsorted(cw, cw[-1] / 2)])     # weighted median of the inliers
    return n, -height, {"tilt_deg": round(float(np.degrees(np.arccos(min(1.0, n @ UP)))), 2),
                        "height": round(height, 3), "inlier_share": round(float(w[inl].sum() / w.sum()), 3),
                        "down_area_m2": round(float(w.sum()), 2), "parallel_to": "floor" if n @ UP < 1 else "+Y"}


def _wall_distance(V, F, below, cell=0.05):
    """Function (x, z) -> horizontal distance (m) to the nearest wall, from a 2D grid. Walls = steep triangles in the
    middle of the room's height (below `below`): sheets hanging from the ceiling are steep too, but higher up."""
    N, area, cen = _faces_geometry(V, F)
    lo = np.percentile(V @ UP, 1)
    h = cen @ UP
    wall = (np.abs(N @ UP) < 0.3) & (area > 0) & (h > lo + 0.3) & (h < below)
    xz = cen[wall][:, [0, 2]]
    lo = V[:, [0, 2]].min(0) - 2 * cell
    shape = tuple(((V[:, [0, 2]].max(0) + 2 * cell - lo) / cell).astype(int) + 1)
    occ = np.zeros(shape, bool)
    ij = ((xz - lo) / cell).astype(int)
    occ[ij[:, 0], ij[:, 1]] = True
    dist = ndimage.distance_transform_edt(~occ) * cell

    def f(p):
        k = np.clip(((p[:, [0, 2]] - lo) / cell).astype(int), 0, np.array(shape) - 1)
        return dist[k[:, 0], k[:, 1]]
    return f


def _boundary_loops(Fw):
    """Closed loops of boundary edges (edges used by one triangle). Where a boundary branches (two holes touching at
    a vertex) it is split into separate closed walks. -> [(ordered vertex list or None, vertices)]"""
    E = np.sort(np.vstack([Fw[:, [0, 1]], Fw[:, [1, 2]], Fw[:, [2, 0]]]), axis=1)
    u, c = np.unique(E, axis=0, return_counts=True)
    adj = defaultdict(set)
    for a, b in u[c == 1]:
        adj[a].add(b)
        adj[b].add(a)
    loops = []
    while adj:
        start = next(iter(adj))
        path, cur, closed = [start], start, False
        while True:
            if not adj[cur]:
                break
            nxt = next(iter(adj[cur]))
            adj[cur].discard(nxt)
            adj[nxt].discard(cur)
            if nxt in path:                          # closed a cycle (at the start, or at an earlier branch point)
                k = path.index(nxt)
                loops.append((path[k:], list(path[k:])))
                del path[k + 1:]
                cur = nxt
                closed = k == 0
                if closed and not adj[cur]:
                    break
                continue
            path.append(nxt)
            cur = nxt
        if len(path) > 1 and not closed:
            loops.append((None, path))               # an open chain: not a hole
        for v in [v for v, nb in adj.items() if not nb]:
            del adj[v]
    return loops


def _inside(poly, q):
    """Even-odd point-in-polygon for points q (m,2) against polygon poly (n,2)."""
    x, y = q[:, 0][:, None], q[:, 1][:, None]
    x1, y1 = poly[:, 0][None], poly[:, 1][None]
    x2, y2 = np.roll(poly[:, 0], -1)[None], np.roll(poly[:, 1], -1)[None]
    cross = ((y1 > y) != (y2 > y)) & (x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-300) + x1)
    return cross.sum(axis=1) % 2 == 1


def _ear_clip(p2):
    """Triangulate a simple polygon (n,2), any orientation -> list of index triples."""
    n = len(p2)
    idx = list(range(n))
    area2 = np.sum(p2[:, 0] * np.roll(p2[:, 1], -1) - np.roll(p2[:, 0], -1) * p2[:, 1])
    sign = 1.0 if area2 > 0 else -1.0
    tris, guard = [], 0
    while len(idx) > 3 and guard < 4 * n * n:
        guard += 1
        m = len(idx)
        for k in range(m):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % m]
            a, b, c = p2[i0], p2[i1], p2[i2]
            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if abs(cross) <= 1e-12 * max(1.0, float(np.ptp(p2))) ** 2:
                idx.pop(k)                              # collinear point: no triangle needed
                break
            if cross * sign < 0:
                continue
            others = [j for j in idx if j not in (i0, i1, i2)]
            if others:
                q = p2[others]
                d1 = (b[0] - a[0]) * (q[:, 1] - a[1]) - (b[1] - a[1]) * (q[:, 0] - a[0])
                d2 = (c[0] - b[0]) * (q[:, 1] - b[1]) - (c[1] - b[1]) * (q[:, 0] - b[0])
                d3 = (a[0] - c[0]) * (q[:, 1] - c[1]) - (a[1] - c[1]) * (q[:, 0] - c[0])
                if np.any((d1 * sign >= 0) & (d2 * sign >= 0) & (d3 * sign >= 0)):
                    continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            return None                         # no ear found: self-intersecting projection
    if len(idx) == 3:
        tris.append(tuple(idx))
    return tris


def _texture(doc, binary, prim):
    try:
        tex = doc["materials"][prim["material"]]["pbrMetallicRoughness"]["baseColorTexture"]["index"]
        im = doc["images"][doc["textures"][tex]["source"]]
        bv = doc["bufferViews"][im["bufferView"]]
        o = bv.get("byteOffset", 0)
        return np.asarray(Image.open(io.BytesIO(binary[o:o + bv["byteLength"]])).convert("RGB"))
    except (KeyError, IndexError, TypeError, OSError):
        return None


def _face_colours(tex, UV, F):
    """Mean texture colour at the three corners of each triangle (sRGB 0-255)."""
    h, w = tex.shape[:2]
    px = np.clip((UV[:, 0] * w).astype(int), 0, w - 1)
    py = np.clip((UV[:, 1] * h).astype(int), 0, h - 1)
    return tex[py, px].astype(np.float64)[F].mean(axis=1)


def _median_colour(tex, UV, idx, default):
    """The surface's own colour: median of the texels between the 50th and 90th brightness percentile. A plain
    median came out dull grey on white walls (door, wardrobe and shadows lie on the same plane)."""
    if tex is None or not len(idx):
        return np.array(default, np.float64)
    h, w = tex.shape[:2]
    uv = UV[idx]
    c = tex[np.clip((uv[:, 1] * h).astype(int), 0, h - 1), np.clip((uv[:, 0] * w).astype(int), 0, w - 1)]
    c = c.astype(np.float64)
    lum = c @ [0.299, 0.587, 0.114]
    lo, hi = np.percentile(lum, [50, 90])
    sel = (lum >= lo) & (lum <= hi)
    return np.median(c[sel] if sel.sum() >= 3 else c, axis=0)


def _hull_ring(q, grow):
    """Convex hull of 2D points, grown outward by `grow` -> ring of corners."""
    ring = q[ConvexHull(q).vertices]
    out = ring - ring.mean(0)
    return ring + grow * out / (np.linalg.norm(out, axis=1)[:, None] + 1e-12)


def _vertex_colours(tex, UV, idx):
    h, w = tex.shape[:2]
    uv = UV[idx]
    return tex[np.clip((uv[:, 1] * h).astype(int), 0, h - 1), np.clip((uv[:, 0] * w).astype(int), 0, w - 1)] \
        .astype(np.float64)


def _grid_sheet(origin, ax1, ax2, len1, len2, cell, src2d=None, src_col=None, k=12):
    """Rectangle origin + a*ax1 + b*ax2 (0 <= a <= len1, 0 <= b <= len2) as a grid of `cell` metres. Each grid vertex
    takes the inverse-distance mean colour of the k nearest real surface points (src2d in the same (a, b) frame), so
    a gap continues what surrounds it: dark beside a window, white beside a white wall, tile colour on a floor.
    -> (vertices, triangles, colours or None)"""
    na, nb = max(1, int(np.ceil(len1 / cell))), max(1, int(np.ceil(len2 / cell)))
    a, b = np.meshgrid(np.linspace(0, len1, na + 1), np.linspace(0, len2, nb + 1), indexing="ij")
    ab = np.c_[a.ravel(), b.ravel()]
    verts = origin + ab[:, :1] * ax1 + ab[:, 1:] * ax2
    i, j = np.meshgrid(np.arange(na), np.arange(nb), indexing="ij")
    v0 = (i * (nb + 1) + j).ravel()
    tris = np.r_[np.c_[v0, v0 + nb + 1, v0 + nb + 2], np.c_[v0, v0 + nb + 2, v0 + 1]]
    cols = None
    if src2d is not None and len(src2d) >= k:
        from scipy.spatial import cKDTree
        dist, nn = cKDTree(src2d).query(ab, k=k)
        wgt = 1.0 / (dist + 0.02)
        cols = (src_col[nn] * wgt[:, :, None]).sum(axis=1) / wgt.sum(axis=1)[:, None]
    return verts, tris, cols


def _room_frame(V, F, n, d, used):
    """Shared geometry of the room: in-plane axes, floor height (up-facing triangles near the bottom), centre."""
    N, area, cen = _faces_geometry(V, F)
    e1 = np.cross(n, [0.0, 0.0, 1.0] if abs(n[2]) < 0.9 else [1.0, 0.0, 0.0])
    e1 /= np.linalg.norm(e1)
    h = V[used] @ n
    up = (N @ n > 0.9) & (cen @ n < np.percentile(h, 1) + 0.15)
    floor_h = float(np.median(cen[up] @ n)) if up.sum() > 10 else float(np.percentile(h, 1))
    return {"N": N, "area": area, "cen": cen, "e1": e1, "e2": np.cross(n, e1), "up": up, "floor_h": floor_h,
            "ceil_h": -d, "centre": V[used].mean(0)}


def _outer_walls(n, fr, *, wall_tol=0.05, min_wall_m2=0.8):
    """The room's OUTER walls: the outermost wall-sized vertical plane on each side of the two main directions.
    (Inner planes - a wardrobe front, the side of a bed, a curtain - are not walls of the room.)
    -> [{"u" (normal), "s0" (offset along u), "side", "t0", "t1", "inl" (triangle mask), "facing_deg", "behind"}]"""
    N, area, cen, e1, e2 = fr["N"], fr["area"], fr["cen"], fr["e1"], fr["e2"]
    steep = (np.abs(N @ n) < 0.25) & (area > 0) & (cen @ n > fr["floor_h"] + 0.1) & (cen @ n < fr["ceil_h"] - 0.1)
    hz = N - np.outer(N @ n, n)
    az = np.degrees(np.arctan2(hz @ e2, hz @ e1)) % 180          # normal sign ignored: scans flip it
    hist = np.histogram(az[steep], bins=36, range=(0, 180), weights=area[steep])[0]
    hist = hist + np.roll(hist, 1) + np.roll(hist, -1)
    axes = []
    for k in np.argsort(hist)[::-1]:
        a = (int(k) + 0.5) * 5
        if hist[k] >= min_wall_m2 and all(abs((a - b + 90) % 180 - 90) > 30 for b in axes):
            axes.append(a)
        if len(axes) == 2:
            break
    k_tol = int(round(wall_tol / 0.01))
    walls = []
    for a0 in axes:
        dirn = steep & (np.abs((az - a0 + 90) % 180 - 90) < 12)
        # exact direction: area-weighted mean of the facing angles (doubled, as the sign is ignored); the 5-degree
        # bin centre alone tilts a 4 m wall by ~17 cm at its ends
        r2 = np.radians(2 * az[dirn])
        a0 = float(np.degrees(np.arctan2((area[dirn] * np.sin(r2)).sum(), (area[dirn] * np.cos(r2)).sum())) / 2) % 180
        u = np.cos(np.radians(a0)) * e1 + np.sin(np.radians(a0)) * e2
        s_ = cen @ u
        edges = np.arange(s_[dirn].min() - wall_tol, s_[dirn].max() + 2 * wall_tol, 0.01)
        win = np.convolve(np.histogram(s_[dirn], bins=edges, weights=area[dirn])[0], np.ones(2 * k_tol + 1),
                          mode="same")
        ok = np.flatnonzero(win >= min_wall_m2)
        if not len(ok):
            continue
        sides = []
        for idx in (ok[0], ok[-1]):                              # outermost wall-sized plane on each side
            near = dirn & (np.abs(s_ - (edges[idx] + 0.005)) < 2 * wall_tol)
            order = np.argsort(s_[near])
            cw = np.cumsum(area[near][order])
            sides.append(float(s_[near][order][np.searchsorted(cw, cw[-1] / 2)]))   # area-weighted median
        for s0 in sorted(set(round(x, 3) for x in sides)):
            inl = dirn & (np.abs(s_ - s0) < wall_tol)
            if area[inl].sum() < min_wall_m2:
                continue
            side = np.cross(n, u)
            t = cen[inl] @ side
            walls.append({"u": u, "s0": s0, "side": side, "t0": float(t.min()) - 0.05, "t1": float(t.max()) + 0.05,
                          "inl": inl, "facing_deg": round(a0, 1),
                          "behind": 1.0 if (s0 - fr["centre"] @ u) > 0 else -1.0})   # away from the room's middle
    return walls


def _backings(V, F, used, n, d, tex, UV, offset, *, floor_offset=0.01, ceiling_colour=None, cell=0.06):
    """Backing sheets for the ceiling (n.p + d = 0), the floor (parallel to it) and the outer walls (up to 4).
    -> [{"surface", "verts" (k,3), "tris", "colours" (k,3) sRGB or None (= colour_rgb), "colour_rgb", ...}]"""
    fr = _room_frame(V, F, n, d, used)
    area, cen, e1, e2, up = fr["area"], fr["cen"], fr["e1"], fr["e2"], fr["up"]
    floor_h, ceil_h = fr["floor_h"], fr["ceil_h"]
    P = V[used]
    h = P @ n
    out = []
    foot = _hull_ring(np.c_[P @ e1, P @ e2], 0.05)
    # ceiling: one plain sheet (its texture is unusable where it needed repair)
    quad = foot[:, :1] * e1 + foot[:, 1:] * e2 + n * (ceil_h + offset)
    col = ceiling_colour if ceiling_colour is not None else \
        _median_colour(tex, UV, used[np.abs(h - ceil_h) < 0.02], (220, 220, 220))
    out.append({"surface": "ceiling", "verts": quad, "tris": np.array([(0, i, i + 1) for i in range(1, len(quad) - 1)]),
                "colours": None, "colour_rgb": [int(x) for x in col], "height": round(float(ceil_h), 3)})
    # floor: 1 cm below the LOWEST real floor (a scanned floor dips: at the median height the sheet z-fought with it
    # and covered it in the dips), colours from the real floor
    lo, hi = foot.min(0), foot.max(0)
    fl = np.unique(F[up].ravel())
    sheet_h = float(np.percentile(cen[up] @ n, 2)) - floor_offset if up.sum() > 10 else floor_h - floor_offset
    src = np.c_[V[fl] @ e1 - lo[0], V[fl] @ e2 - lo[1]] if tex is not None else None
    verts, tris, cols = _grid_sheet(e1 * lo[0] + e2 * lo[1] + n * sheet_h, e1, e2, hi[0] - lo[0],
                                    hi[1] - lo[1], cell, src, _vertex_colours(tex, UV, fl) if tex is not None else None)
    col = _median_colour(tex, UV, fl, (150, 140, 130))
    out.append({"surface": "floor", "verts": verts, "tris": tris, "colours": cols,
                "colour_rgb": [int(x) for x in col], "height": round(float(floor_h), 3)})
    # outer walls, colours from the nearest real wall surface
    for w in _outer_walls(n, fr):
        side, t0, t1 = w["side"], w["t0"], w["t1"]
        # behind ALL real geometry on that side, not just behind the wall surface: a window recess (and a curtain
        # hanging in it) lies beyond the wall plane, and a sheet 2 cm behind the wall hid the curtain
        beyond = np.percentile(w["behind"] * (P @ w["u"]), 99.5)
        base = w["u"] * w["behind"] * (max(w["behind"] * w["s0"], beyond) + offset)
        wv = np.unique(F[w["inl"]].ravel())
        z0 = floor_h - 0.02
        src = np.c_[V[wv] @ side - t0, V[wv] @ n - z0] if tex is not None else None
        verts, tris, cols = _grid_sheet(base + side * t0 + n * z0, side, n, t1 - t0, ceil_h + 0.02 - z0, cell, src,
                                        _vertex_colours(tex, UV, wv) if tex is not None else None)
        col = _median_colour(tex, UV, wv, (200, 200, 200))
        out.append({"surface": "wall", "verts": verts, "tris": tris, "colours": cols, "colour_rgb": [int(x) for x in col],
                    "facing_deg": w["facing_deg"], "area_m2": round(float(area[w["inl"]].sum()), 2),
                    "width_m": round(t1 - t0, 2)})
    return out


def _shallow_pieces(Fw, keep, hz, depth):
    """Triangles (among `keep`) of connected pieces whose every vertex lies within `depth` below the ceiling plane
    (hz = signed height above it per triangle corner). Connectivity on welded vertices."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    idx = np.flatnonzero(keep)
    if not len(idx):
        return np.zeros(len(keep), bool)
    f = Fw[idx]
    nv = int(Fw.max()) + 1
    e = np.vstack([f[:, [0, 1]], f[:, [1, 2]]])
    _, lab = connected_components(coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(nv, nv)), directed=False)
    comp = lab[f[:, 0]]
    lowest = np.full(nv, np.inf)
    np.minimum.at(lowest, comp, hz[idx].min(axis=1))
    out = np.zeros(len(keep), bool)
    out[idx] = lowest[comp] > -depth
    return out


def _snap(V, weld, idx, u, s0, tol):
    """Move the vertices idx that lie within tol of the plane u.p = s0 onto it, every welded copy together (texture
    seams stay closed). -> (number moved, largest move)"""
    near = idx[np.abs(V[idx] @ u - s0) < tol]
    if not len(near):
        return 0, 0.0
    wsel = np.zeros(weld.max() + 1, bool)
    wsel[weld[near]] = True
    mv = wsel[weld]
    off = V[mv] @ u - s0
    V[mv] -= np.outer(off, u)
    return int(mv.sum()), float(np.abs(off).max())


def _straighten(V, F, keep, weld, n, d, *, wall_tol=0.05, floor_tol=0.03, top_cut=0.15):
    """Snap the outer walls and the floor onto their planes (in place on V; `keep` loses the cut wall tops).
    A corner vertex near two walls lands on both planes: corners come out square. Wall pieces in the top `top_cut`
    below the ceiling are removed - a torn wall top otherwise shows as a jagged white edge; the wall backing
    (coloured from the wall) shows a clean line there."""
    used = np.unique(F[keep])
    fr = _room_frame(V, F, n, d, used)
    walls = _outer_walls(n, fr)
    out = {"walls": [], "floor": None, "wall_top_triangles": 0}
    for w in walls:
        idx = np.unique(F[keep & (np.abs(fr["cen"] @ w["u"] - w["s0"]) < 2 * wall_tol)].ravel())
        k, mx = _snap(V, weld, idx, w["u"], w["s0"], wall_tol)
        out["walls"].append({"facing_deg": w["facing_deg"], "vertices": k, "max_move_m": round(mx, 3)})
    up = fr["up"] & keep
    if up.sum() > 10:
        fh = fr["floor_h"]
        k, mx = _snap(V, weld, np.unique(F[up].ravel()), n, fh, floor_tol)
        out["floor"] = {"vertices": k, "max_move_m": round(mx, 3), "height": round(fh, 3)}
    if walls and top_cut:
        N, _, cen = _faces_geometry(V, F)
        near_wall = np.min([np.abs(cen @ w["u"] - w["s0"]) for w in walls], axis=0) < 2 * wall_tol
        top = keep & near_wall & (np.abs(N @ n) < 0.5) & ((V[F] @ n + d).min(axis=1) > -top_cut)
        keep &= ~top
        out["wall_top_triangles"] = int(top.sum())
    return out


def _srgb_to_linear(c):
    c = np.asarray(c, np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def repair(glb, *, weld_m=0.001, above_tol=0.3, flatten_below=0.25, sheet_wall_gap=0.3, max_hole_perimeter=8.0,
           max_hole_rms=0.12, backing_m=0.02, pad_texture=True, object_drop_m=0.05, object_colour_diff=45.0,
           plain_ceiling=True, scrap_m=0.08, debris_m=0.3, straighten=True, straighten_tol=0.05,
           wall_top_cut=0.15):
    doc, binary = _parse_glb(glb)
    stats = {"applied": False}
    nodes = doc.get("nodes", [])
    prims = [p for m in doc.get("meshes", []) for p in m["primitives"] if p.get("mode", 4) == 4 and "indices" in p]
    if len(prims) != 1 or any(k in n for n in nodes for k in ("matrix", "rotation", "scale", "translation")):
        stats["skipped"] = "only single-primitive meshes without node transforms are repaired"
        return glb, stats
    prim = prims[0]
    V = _accessor(doc, binary, prim["attributes"]["POSITION"]).astype(np.float64)
    F = _accessor(doc, binary, prim["indices"]).reshape(-1, 3).astype(np.int64)
    UV = _accessor(doc, binary, prim["attributes"]["TEXCOORD_0"]) if "TEXCOORD_0" in prim["attributes"] else None
    _, weld = np.unique(np.round(V / weld_m).astype(np.int64), axis=0, return_inverse=True)
    weld = weld.ravel()

    tex = _texture(doc, binary, prim) if UV is not None else None
    fit = fit_ceiling(V, F)
    keep = np.ones(len(F), bool)
    V2 = V.copy()
    if fit is not None:
        n, d, cst = fit
        stats["ceiling"] = cst
        dist = V @ n + d                                        # > 0: above the ceiling plane
        # 2. above the ceiling
        above = (dist[F] > above_tol).all(axis=1)
        keep &= ~above
        # 3. flatten: vertices of down-facing or hanging surface near the ceiling, on welded groups
        N, area, cen = _faces_geometry(V, F)
        # distance to the room's OUTER walls: a grid of every steep surface counted curtains, the wardrobe and the
        # AC as walls, and in a furnished room almost every point was "near a wall"
        walls = _outer_walls(n, _room_frame(V, F, n, d, np.unique(F)))
        if walls:
            wall_d = np.min([np.abs(cen @ w["u"] - w["s0"]) for w in walls], axis=0)
        else:
            wall_d = _wall_distance(V, F, -d - flatten_below - 0.1)(cen)
        cdist = cen @ n + d
        near = (cdist > -flatten_below) & (cdist <= above_tol) & keep
        down = near & (N @ n < -0.5)
        hanging = near & ~down & (wall_d > sheet_wall_gap) & (N @ n < 0.5)
        # an OBJECT hanging from the ceiling (a fan, a lamp) pressed flat leaves its dark texture smeared over the
        # ceiling: surface that sits clearly below the plane AND does not have the ceiling's colour is removed instead
        # (the ceiling backing shows plain ceiling there)
        obj = np.zeros(len(F), bool)
        if tex is not None:
            fcol = _face_colours(tex, UV, F)
            on = near & (np.abs(cdist) < 0.02) & (N @ n < -0.5)
            if on.sum() > 20:
                ccol = np.median(fcol[on], axis=0)
                low = dist[F].min(axis=1) < -object_drop_m
                # down-facing only: a curtain's top (vertical, patterned) hangs just as low but is a real object
                obj = down & low & (np.linalg.norm(fcol - ccol, axis=1) > object_colour_diff)
                keep &= ~obj
        # flatten only DOWN-facing surface: pulling vertical surface (a curtain, a wall top) up to the plane stretched
        # it; vertical debris is removed as a whole piece below instead
        sel = np.zeros(len(V), bool)
        sel[F[down & ~obj].ravel()] = True
        # move every copy of a welded vertex together (texture seams must stay closed)
        wsel = np.zeros(weld.max() + 1, bool)
        wsel[weld[sel]] = True
        mv = wsel[weld]
        V2[mv] -= np.outer(V2[mv] @ n + d, n)
        _, area2, _ = _faces_geometry(V2, F)
        sliver = keep & (area2 < 0.05 * np.maximum(area, 1e-12)) & (area > 0)
        keep &= ~sliver
        # plain ceiling: the textured surface now lying ON the plane is replaced by the plain ceiling-coloured sheet
        # (step 5). A white ceiling gives the photos nothing to work with: its texture is grey blotches and smears.
        plain = np.zeros(len(F), bool)
        repaired = (mv.any() and np.abs(dist[mv]).max() > 0.01) or above.any() or obj.any()
        if plain_ceiling and repaired:                   # a ceiling that needed no repair keeps its texture
            N2, _, _ = _faces_geometry(V2, F)
            # on the plane, or down-facing scraps hanging just below it (torn ceiling along the wall tops)
            # (either facing: scans flip some triangles, and flipped scraps stayed as dark torn bits)
            band = (V2[F] @ n + d).min(axis=1) > -scrap_m
            plain = keep & (np.abs(N2 @ n) > 0.5) & ((np.abs(V2[F] @ n + d).max(axis=1) < 0.005) | band)
            keep &= ~plain
            # ceiling debris: with the ceiling surface replaced, torn sheets and slivers become separate pieces. A
            # piece lying ENTIRELY within debris_m below the ceiling is debris; a curtain, a wall or the AC continues
            # further down and is kept whole (judging single triangles removed the top of a curtain).
            debris = _shallow_pieces(weld[F], keep, V2[F] @ n + d, debris_m)
            plain |= debris
            keep &= ~debris
        stats.update(removed_above_triangles=int(above.sum()), removed_hanging_object_triangles=int(obj.sum()),
                     flattened_vertices=int(mv.sum()),
                     flattened_triangles=int((down | hanging).sum()), hanging_triangles=int(hanging.sum()),
                     removed_slivers=int(sliver.sum()), plain_ceiling_triangles=int(plain.sum()),
                     ceiling_debris_triangles=int(debris.sum()) if plain_ceiling and repaired else 0,
                     max_vertex_move_m=round(float(np.abs(dist[mv]).max()) if mv.any() else 0.0, 3))
        # 3b. straighten: outer walls and the floor snapped onto their planes, jagged wall tops cut back
        if straighten and repaired:
            stats["straighten"] = _straighten(V2, F, keep, weld, n, d, wall_tol=straighten_tol, top_cut=wall_top_cut)
    F2 = F[keep]

    # 4. holes
    Fw = weld[F2]
    Fw = Fw[(Fw[:, 0] != Fw[:, 1]) & (Fw[:, 1] != Fw[:, 2]) & (Fw[:, 0] != Fw[:, 2])]
    rep = np.zeros(weld.max() + 1, np.int64)
    rep[weld] = np.arange(len(V))                              # one original vertex per welded position
    third = {}
    for a, b, c_ in Fw:
        for e0, e1, e2 in ((a, b, c_), (b, c_, a), (c_, a, b)):
            third[(min(e0, e1), max(e0, e1))] = e2
    fills, holes = [], []
    for order, comp in _boundary_loops(Fw):
        P = V2[rep[comp]]
        perim = float(sum(np.linalg.norm(V2[rep[order[i]]] - V2[rep[order[i - 1]]]) for i in range(len(order)))) \
            if order else None
        info = {"vertices": len(comp), "perimeter_m": round(perim, 2) if perim else None,
                "centre": np.round(P.mean(0), 2).tolist(), "filled": False}
        holes.append(info)
        if order is None or len(order) < 3:
            info["reason"] = "open boundary"
            continue
        if perim > max_hole_perimeter:
            info["reason"] = "too large (open side of the scan)"
            continue
        Q = V2[rep[order]]
        c = Q.mean(0)
        _, s, vt = np.linalg.svd(Q - c, full_matrices=False)
        rms = float(s[2] / np.sqrt(len(Q)))
        info["rms_m"] = round(rms, 3)
        if rms > max_hole_rms:
            info["reason"] = "not flat enough to close safely"
            continue
        p2 = (Q - c) @ vt[:2].T
        # a hole has the surface OUTSIDE its outline; the free edge of a sheet (a table top, a curtain) has it inside
        # - "filling" that would lay a second copy over the sheet
        thirds = [third.get((min(a, b), max(a, b))) for a, b in zip(order, order[1:] + order[:1])]
        thirds = np.array([t for t in thirds if t is not None])
        if len(thirds) and _inside(p2, (V2[rep[thirds]] - c) @ vt[:2].T).mean() > 0.5:
            info["reason"] = "edge of a surface, not a hole"
            continue
        tris = _ear_clip(p2)
        if not tris:
            info["reason"] = "outline crosses itself"
            continue
        colour = np.array([200, 200, 200], np.float64)
        if tex is not None:
            h, w = tex.shape[:2]
            uv = UV[rep[order]]
            px = np.clip((uv[:, 0] * w).astype(int), 0, w - 1)
            py = np.clip((uv[:, 1] * h).astype(int), 0, h - 1)
            colour = np.median(tex[py, px].astype(np.float64), axis=0)
        fills.append((Q, np.array(tris), colour))
        info.update(filled=True, triangles=len(tris), colour_rgb=[int(x) for x in colour])
    stats["holes"] = holes
    stats["filled_holes"] = sum(h["filled"] for h in holes)
    # 5. backings: flat sheets just behind the ceiling, the floor and each main wall, in that surface's colour.
    # Gaps too tangled to triangulate (torn wall tops, a floor hole joined to a wall gap) then show ceiling / floor /
    # wall instead of the black background or the viewer's floor guide; from inside the room they are hidden.
    needs_backing = (any(not h["filled"] and h.get("reason") != "edge of a surface, not a hole" for h in holes)
                     or stats.get("max_vertex_move_m", 0) > 0.01 or bool(stats.get("removed_above_triangles"))
                     or bool(stats.get("removed_hanging_object_triangles")) or bool(stats.get("plain_ceiling_triangles")))
    if fit is not None and backing_m and needs_backing:
        ccol = None
        if plain_ceiling and stats.get("plain_ceiling_triangles"):        # its colour from the surface it replaces
            ccol = _median_colour(tex, UV, np.unique(F[plain]), (220, 220, 220))
        backings = _backings(V2, F2, np.unique(F2), n, d, tex, UV, backing_m, ceiling_colour=ccol)
        for b in backings:
            cols = b.pop("colours")
            fills.append((b.pop("verts"), b.pop("tris"),
                          cols if cols is not None else np.array(b["colour_rgb"], np.float64)))
        stats["backings"] = backings

    # 6. texture padding: texels no triangle uses are black in phone-app atlases; mip-mapping and filtering blend that
    # black into the edges of the patches (dark specks and seams). Give them the colour of the nearest used texel.
    padded = None
    if tex is not None and pad_texture:
        from namma.recon.blendtex import rasterize_atlas
        h_, w_ = tex.shape[:2]
        fid, _ = rasterize_atlas(UV.astype(np.float64), F, w_, h_)
        used_tx = fid >= 0
        if used_tx.any() and not used_tx.all():
            near = ndimage.distance_transform_edt(~ndimage.binary_erosion(used_tx), return_distances=False,
                                                  return_indices=True)
            out = tex[near[0], near[1]]
            img = doc["images"][doc["textures"][doc["materials"][prim["material"]]["pbrMetallicRoughness"]
                                                ["baseColorTexture"]["index"]]["source"]]
            buf = io.BytesIO()
            if img.get("mimeType") == "image/png":
                Image.fromarray(out).save(buf, "PNG", optimize=True)
            else:
                Image.fromarray(out).save(buf, "JPEG", quality=95)
                img["mimeType"] = "image/jpeg"
            padded = (img["bufferView"], buf.getvalue())
            stats["texture_padding"] = {"unused_texel_share": round(float(1 - used_tx.mean()), 3)}

    changed = fit is not None and (not keep.all() or (V2 != V).any())
    if not changed and not fills and padded is None:
        return glb, stats
    pos_acc = doc["accessors"][prim["attributes"]["POSITION"]]
    replace = {pos_acc["bufferView"]: V2.astype("<f4").tobytes()} if (V2 != V).any() else {}
    if padded is not None:
        replace[padded[0]] = padded[1]
    add = [F2.astype("<u4").tobytes()]
    if fills:
        fv = np.vstack([q for q, _, _ in fills]).astype("<f4")
        fc = np.vstack([_srgb_to_linear(col) if np.ndim(col) == 2 else np.tile(_srgb_to_linear(col), (len(q), 1))
                        for q, _, col in fills]).astype("<f4")
        base = np.cumsum([0] + [len(q) for q, _, _ in fills])[:-1]
        fi = np.vstack([t + b for (_, t, _), b in zip(fills, base)]).astype("<u4")
        add += [fv.tobytes(), fc.tobytes(), fi.ravel().tobytes()]
    if replace:
        assert pos_acc.get("byteOffset", 0) == 0, "interleaved positions are not supported"
        pos_acc["min"], pos_acc["max"] = V2.min(0).tolist(), V2.max(0).tolist()
    doc, binary, views = _rebuild(doc, binary, replace=replace, add=add)
    acc = doc["accessors"]
    acc.append({"bufferView": views[0], "componentType": 5125, "count": int(F2.size), "type": "SCALAR"})
    prim["indices"] = len(acc) - 1
    if fills:
        acc.append({"bufferView": views[1], "componentType": 5126, "count": len(fv), "type": "VEC3",
                    "min": fv.min(0).tolist(), "max": fv.max(0).tolist()})
        acc.append({"bufferView": views[2], "componentType": 5126, "count": len(fv), "type": "VEC3"})
        acc.append({"bufferView": views[3], "componentType": 5125, "count": int(fi.size), "type": "SCALAR"})
        doc.setdefault("materials", []).append({"name": "namma-hole-fill", "doubleSided": True,
                                                "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0}})
        doc["meshes"][0]["primitives"].append({"attributes": {"POSITION": len(acc) - 3, "COLOR_0": len(acc) - 2},
                                               "indices": len(acc) - 1, "material": len(doc["materials"]) - 1})
    stats["applied"] = True
    return glb_bytes(doc, binary), stats
