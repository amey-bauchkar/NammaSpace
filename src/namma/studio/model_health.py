"""Model health: measured facts about a finished model, for the quality report (no verdict change).

    h = mesh_health(V, F)                       # topology, holes, winding
    t = texture_density(glb)                    # texels per metre on the model's surface
    s = splat_density(P_model, floor_cells)     # splats per m2, per floor block
    g = guidance(h, before=h0)                  # recapture / pipeline-gap text

All counts are on the WELDED mesh (texture-seam duplicates merged at 1 mm), so seams are not reported as holes.
"""
from collections import Counter

import numpy as np

WELD_M = 0.001
LARGE_HOLE_PERIMETER_M = 8.0          # same limit as mesh_repair: bigger is an open side of the scan, never filled


def mesh_health(V, F, *, weld_m=WELD_M):
    """-> {triangles, degenerate_faces, non_manifold_edges, boundary_edges, holes, largest_hole_perimeter_m,
    open_boundary_m, flipped_edge_share}"""
    V = np.asarray(V, float)
    F = np.asarray(F, np.int64)
    area = np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1) / 2
    _, W = np.unique(np.round(V / weld_m).astype(np.int64), axis=0, return_inverse=True)
    W = W.ravel()
    Fw = W[F]
    ok = (Fw[:, 0] != Fw[:, 1]) & (Fw[:, 1] != Fw[:, 2]) & (Fw[:, 0] != Fw[:, 2]) & (area > 1e-12)
    out = {"triangles": int(len(F)), "degenerate_faces": int((~ok).sum())}
    Fw = Fw[ok]
    directed = np.vstack([Fw[:, [0, 1]], Fw[:, [1, 2]], Fw[:, [2, 0]]])
    und = np.sort(directed, axis=1)
    u, inv, cnt = np.unique(und, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    out["non_manifold_edges"] = int((cnt > 2).sum())
    out["boundary_edges"] = int((cnt == 1).sum())
    # winding: on a consistently oriented surface the two triangles at an edge run it in OPPOSITE directions
    two = np.flatnonzero(cnt == 2)
    if len(two):
        fwd = (directed[:, 0] < directed[:, 1]).astype(np.int64)
        s = np.bincount(inv, weights=fwd, minlength=len(u))
        out["flipped_edge_share"] = round(float(np.mean(s[two] != 1)), 4)    # 0 or 2 forward copies = same direction
    else:
        out["flipped_edge_share"] = 0.0
    # holes: closed boundary loops (welded positions); perimeter of each
    rep = np.zeros(int(W.max()) + 1, np.int64)
    rep[W] = np.arange(len(V))
    from namma.studio.mesh_repair import _boundary_loops
    perims = []
    open_len = 0.0
    for order, comp in _boundary_loops(Fw):
        if order is None:
            pts = V[rep[comp]]
            open_len += float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
            continue
        q = V[rep[order]]
        perims.append(float(np.linalg.norm(q - np.roll(q, 1, axis=0), axis=1).sum()))
    out["holes"] = len(perims)
    out["largest_hole_perimeter_m"] = round(max(perims), 3) if perims else 0.0
    out["large_holes"] = int(sum(p > LARGE_HOLE_PERIMETER_M for p in perims))
    out["hole_perimeter_total_m"] = round(float(sum(perims)), 3)
    out["open_boundary_m"] = round(open_len, 3)
    return out


def texture_density(glb):
    """Effective texture resolution: sqrt(texels covered / surface area) per textured primitive, in texels per metre
    (area-weighted median over triangles). -> {"texels_per_m": x or None, "textured_area_m2": a}"""
    from namma.studio.mesh_import import _accessor, _parse_glb
    doc, binary = _parse_glb(glb)
    dens, weights = [], []
    for m in doc.get("meshes", []):
        for prim in m["primitives"]:
            if prim.get("mode", 4) != 4 or "indices" not in prim or "TEXCOORD_0" not in prim["attributes"]:
                continue
            try:
                mat = doc["materials"][prim["material"]]
                img = doc["images"][doc["textures"][mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]]
            except (KeyError, IndexError, TypeError):
                continue
            size = _image_size(doc, binary, img)
            if size is None:
                continue
            V = _accessor(doc, binary, prim["attributes"]["POSITION"]).astype(np.float64)
            UV = _accessor(doc, binary, prim["attributes"]["TEXCOORD_0"]).astype(np.float64)
            F = _accessor(doc, binary, prim["indices"]).reshape(-1, 3).astype(np.int64)
            a3 = np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1) / 2
            uv = UV[F] * np.array(size, float)
            a2 = np.abs(np.cross(uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0])) / 2
            ok = a3 > 1e-9
            dens.append(np.sqrt(a2[ok] / a3[ok]))
            weights.append(a3[ok])
    if not dens:
        return {"texels_per_m": None, "textured_area_m2": 0.0}
    d, w = np.concatenate(dens), np.concatenate(weights)
    o = np.argsort(d)
    cw = np.cumsum(w[o])
    med = float(d[o][np.searchsorted(cw, cw[-1] / 2)])
    p10 = float(d[o][np.searchsorted(cw, cw[-1] * 0.1)])
    return {"texels_per_m": round(med, 1), "texels_per_m_p10": round(p10, 1), "textured_area_m2": round(float(w.sum()), 2)}


def _image_size(doc, binary, img):
    import io
    from PIL import Image
    if "bufferView" not in img:
        return None
    bv = doc["bufferViews"][img["bufferView"]]
    o = bv.get("byteOffset", 0)
    try:
        with Image.open(io.BytesIO(binary[o:o + bv["byteLength"]])) as im:
            return im.size
    except Exception:                                          # noqa: BLE001
        return None


def splat_density(points_model, *, block_m=2.0, floor_xz=None, min_share=0.02):
    """Splats per m2 of floor, per block_m x block_m block of the model's XZ plane (blocks with at least min_share of
    the median block count; or only blocks over floor cells floor_xz (k,2) when given).
    -> {"blocks", "per_m2_median", "per_m2_p10", "per_m2_min", "block_m", "worst_block_centre"}"""
    P = np.asarray(points_model, float)
    if not len(P):
        return {"blocks": 0}
    ij = np.floor(P[:, [0, 2]] / block_m).astype(np.int64)
    cnt = Counter(map(tuple, ij))
    keys = list(cnt)
    if floor_xz is not None and len(floor_xz):
        fl = set(map(tuple, np.floor(np.asarray(floor_xz, float) / block_m).astype(np.int64)))
        keys = [k for k in keys if k in fl]
    vals = np.array([cnt[k] for k in keys], float)
    if not len(vals):
        return {"blocks": 0}
    keep = vals >= min_share * np.median(vals)
    vals, keys = vals[keep], [k for k, kk in zip(keys, keep) if kk]
    per = vals / (block_m * block_m)
    w = int(np.argmin(per))
    return {"blocks": int(len(per)), "block_m": block_m, "per_m2_median": round(float(np.median(per)), 1),
            "per_m2_p10": round(float(np.percentile(per, 10)), 1), "per_m2_min": round(float(per.min()), 1),
            "worst_block_centre_xz": [round((keys[w][0] + 0.5) * block_m, 2), round((keys[w][1] + 0.5) * block_m, 2)]}


def guidance(after, before=None):
    """Plain-language findings from mesh_health (after = the shown model, before = the app's export), with the cause:
    a gap the CAPTURE left (recapture helps) or one the PIPELINE added (report it)."""
    out = []
    if after.get("large_holes"):
        out.append({"code": "LARGE-OPEN-AREAS", "severity": "info", "cause": "capture",
                    "message": f"{after['large_holes']} large opening(s) in the scan (largest outline "
                               f"{after['largest_hole_perimeter_m']:.1f} m) are left open, not invented. Recapture: walk "
                               "slowly along that wall / floor area and point the phone at it from 1-2 m."})
    if before is not None and after.get("hole_perimeter_total_m", 0) > before.get("hole_perimeter_total_m", 0) + 1.0:
        out.append({"code": "PIPELINE-ADDED-OPENINGS", "severity": "info", "cause": "pipeline",
                    "message": f"Repair left {after['hole_perimeter_total_m'] - before['hole_perimeter_total_m']:.1f} m "
                               "more hole outline than the app's export (removed debris or ceiling scraps); backing "
                               "sheets cover them from inside the room."})
    if after.get("non_manifold_edges", 0) > 0.001 * max(1, after.get("triangles", 0)):
        out.append({"code": "NON-MANIFOLD", "severity": "info", "cause": "capture",
                    "message": f"{after['non_manifold_edges']} edges are shared by more than two triangles (overlapping "
                               "scan layers). Usually invisible; walking maps are not affected."})
    if after.get("flipped_edge_share", 0) > 0.05:
        out.append({"code": "MIXED-WINDING", "severity": "info", "cause": "capture",
                    "message": f"{after['flipped_edge_share']:.0%} of shared edges join triangles facing opposite ways "
                               "(the exporter's winding). The viewer draws both sides, so nothing is hidden."})
    return out


def room_geometry(V, F):
    """Geometric distortion of a room scan (metres / degrees): how flat the floor and the main outer walls are, how
    square the walls are to each other, how level the ceiling is. Straight rooms score near 0; a warped scan (tracking
    drift) shows bent walls (large wall p95) and non-square corners.
    -> {"ceiling_height_m", "ceiling_tilt_deg", "floor_rms_m", "floor_p95_m", "walls": [...], "corner_deviation_deg"}"""
    from namma.studio.mesh_repair import _outer_walls, _room_frame, fit_ceiling
    V = np.asarray(V, float)
    F = np.asarray(F, np.int64)
    fit = fit_ceiling(V, F)
    if fit is None:
        return {"error": "no ceiling plane found"}
    n, d, cst = fit
    fr = _room_frame(V, F, n, d, np.unique(F))
    N, area, cen = fr["N"], fr["area"], fr["cen"]

    def spread(x, w):
        if not len(x) or w.sum() <= 0:
            return None, None
        o = np.argsort(np.abs(x))
        cw = np.cumsum(w[o])
        p95 = float(np.abs(x[o])[np.searchsorted(cw, 0.95 * cw[-1])])
        return round(float(np.sqrt(np.average(x ** 2, weights=w))), 4), round(p95, 4)
    fl = (np.abs(N @ n) > 0.9) & (np.abs(cen @ n - fr["floor_h"]) < 0.10) & (area > 0)
    frms, fp95 = spread(cen[fl] @ n - fr["floor_h"], area[fl])
    walls = []
    for w in _outer_walls(n, fr):
        m = w["inl"]
        rms, p95 = spread(cen[m] @ w["u"] - w["s0"], area[m])
        walls.append({"facing_deg": w["facing_deg"], "width_m": round(w["t1"] - w["t0"], 2),
                      "area_m2": round(float(area[m].sum()), 2), "rms_m": rms, "p95_m": p95})
    dirs = sorted({round(w["facing_deg"] % 180, 1) for w in walls})
    corner = None
    if len(dirs) >= 2:
        diff = abs((dirs[1] - dirs[0]) % 180)
        corner = round(abs(min(diff, 180 - diff) - 90.0), 2)
    return {"ceiling_height_m": round(fr["ceil_h"] - fr["floor_h"], 3), "ceiling_tilt_deg": cst.get("tilt_deg"),
            "floor_rms_m": frms, "floor_p95_m": fp95, "walls": walls, "corner_deviation_deg": corner}
