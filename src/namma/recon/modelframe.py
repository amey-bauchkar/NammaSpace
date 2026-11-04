"""Scale-free model frame for a photo reconstruction (ARCH-031).

Photos alone do not fix metric scale. The model frame is therefore defined WITHOUT scale:
right-handed, +Y up (gravity), floor plane at y = 0, origin below the capture cameras' centroid,
+X along the dominant horizontal axis of the floor; units = the reconstruction's own units.
The transform from the SfM frame is rigid (rotation + translation only).

Up direction:
  1. prior from camera orientation: hand-held photos are taken upright, so the average image-up
     vector (camera -Y) points roughly against gravity;
  2. refined by the floor plane: the lowest large plane that the cameras are consistently above
     (scale-free tests: relative spread of camera heights), normal within 25 deg of the prior.
If no floor plane qualifies, the prior is used and `floor_detected` is false (reported as a warning).
"""
import math

import numpy as np
from scipy import ndimage

UP_PRIOR_MIN_CONFIDENCE = 0.25      # |mean image-up vector|; 1 = all cameras perfectly upright
FLOOR_MAX_TILT_DEG = 30.0


def camera_axes(R):
    """World directions of a COLMAP camera (R = world->camera): forward (+Z), image-up (-Y), right (+X)."""
    return R.T @ np.array([0.0, 0.0, 1.0]), R.T @ np.array([0.0, -1.0, 0.0]), R.T @ np.array([1.0, 0.0, 0.0])


def up_prior(rotations):
    ups = np.array([camera_axes(R)[1] for R in rotations])
    m = ups.mean(0)
    conf = float(np.linalg.norm(m))
    return (m / conf if conf > 1e-9 else np.array([0.0, 1.0, 0.0])), conf


def _ransac_plane(pts, tol, rng, iters=400, hint=None, max_tilt_deg=None):
    best_n, best_d, best_c = None, None, -1
    cos_lim = math.cos(math.radians(max_tilt_deg)) if max_tilt_deg is not None else None
    for _ in range(iters):
        a, b, c = pts[rng.choice(len(pts), 3, replace=False)]
        n = np.cross(b - a, c - a)
        nn = np.linalg.norm(n)
        if nn < 1e-12:
            continue
        n /= nn
        if hint is not None and cos_lim is not None and abs(float(n @ hint)) < cos_lim:
            continue
        d = -float(n @ a)
        cnt = int(np.count_nonzero(np.abs(pts @ n + d) < tol))
        if cnt > best_c:
            best_n, best_d, best_c = n, d, cnt
    if best_n is None:
        return None
    inl = pts[np.abs(pts @ best_n + best_d) < tol]
    c = inl.mean(0)
    w, v = np.linalg.eigh((inl - c).T @ (inl - c))
    n = v[:, 0] / np.linalg.norm(v[:, 0])
    if hint is not None and n @ hint < 0:
        n = -n
    return n, -float(n @ c), len(inl)


def scene_extent(points):
    med = np.median(points, axis=0)
    return float(np.percentile(np.linalg.norm(points - med, axis=1), 90))


def detect_floor(points, centres, rotations, seed=0, sample=200_000):
    """Return dict with up vector, floor plane (n, d) or None, diagnostics. Scale-free."""
    rng = np.random.default_rng(seed)
    pts = np.asarray(points, float)
    if len(pts) > sample:
        pts = pts[rng.choice(len(pts), sample, replace=False)]
    cams = np.asarray(centres, float)
    prior, conf = up_prior(rotations)
    extent = scene_extent(pts)
    tol = 0.01 * extent
    hint = prior if conf >= UP_PRIOR_MIN_CONFIDENCE else None
    candidates, remaining = [], pts
    for _ in range(8):
        if len(remaining) < 500:
            break
        res = _ransac_plane(remaining, tol, rng, hint=hint, max_tilt_deg=FLOOR_MAX_TILT_DEG if hint is not None else None)
        if res is None:
            break
        n, d, cnt = res
        if hint is None and np.mean(cams @ n + d) < 0:
            n, d = -n, -d
        h = cams @ n + d
        above = float(np.mean(h > 0))
        med = float(np.median(h))
        iqr = float(np.percentile(h, 75) - np.percentile(h, 25))
        tilt = math.degrees(math.acos(min(1.0, abs(float(n @ prior)))))
        if tilt > FLOOR_MAX_TILT_DEG:
            remaining = remaining[np.abs(remaining @ n + d) >= 2 * tol]
            continue
        if above >= 0.70 and med > 0 and cnt >= 0.02 * len(pts):
            candidates.append({"n": n, "d": d, "inliers": cnt, "median_height": med, "rel_iqr": (iqr / med if med > 0 else 0.0),
                               "tilt_deg": tilt})
        remaining = remaining[np.abs(remaining @ n + d) >= 2 * tol]
    diag = {"up_prior": prior.tolist(), "up_prior_confidence": round(conf, 3), "ransac_tol_units": tol,
            "scene_extent_units": extent, "floor_candidates": len(candidates)}
    if candidates:
        top = max(c["inliers"] for c in candidates)
        good = [c for c in candidates if c["inliers"] >= 0.25 * top]
        floor = max(good, key=lambda c: c["median_height"])        # the LOWEST qualifying plane
        diag.update({"floor_inliers_sampled": floor["inliers"], "floor_tilt_from_prior_deg": round(floor["tilt_deg"], 2),
                     "camera_height_rel_iqr": round(floor["rel_iqr"], 3)})
        return {"up": floor["n"], "plane": (floor["n"], floor["d"]), "floor_detected": True,
                "up_source": "floor_plane", "diagnostics": diag}
    up = prior
    low = float(np.percentile(pts @ up, 2))
    diag["note"] = "no floor-like plane; up from camera orientation, floor height = 2nd percentile of points along up"
    return {"up": up, "plane": (up, -low), "floor_detected": False, "up_source": "camera_orientation",
            "diagnostics": diag}


def model_frame(points, centres, rotations, seed=0, normals=None):
    """Rigid 4x4 T (SfM -> model frame) and metadata."""
    fl = detect_floor(points, centres, rotations, seed)
    n, d = fl["plane"]
    y_floor = n / np.linalg.norm(n)
    d_floor = d
    pts = np.asarray(points, float)
    cams = np.asarray(centres, float)
    c = cams.mean(0)
    eye_est = float(np.median(cams @ y_floor + d_floor))

    # Refine Up vector to architectural plumb vertical if wall normals are available
    y = y_floor
    if normals is not None and len(normals) == len(pts) and eye_est > 0:
        rng = np.random.default_rng(seed)
        sample_size = min(len(pts), 150_000)
        idx = rng.choice(len(pts), sample_size, replace=False)
        p_sub = pts[idx]
        n_sub = np.asarray(normals, float)[idx]
        norm_len = np.linalg.norm(n_sub, axis=1, keepdims=True)
        n_sub = n_sub / (norm_len + 1e-12)

        h_sub = p_sub @ y_floor + d_floor
        wall_mask = (h_sub > 0.25 * eye_est) & (h_sub < 1.75 * eye_est)
        if np.count_nonzero(wall_mask) >= 500:
            w_n = n_sub[wall_mask]
            vert_mask = np.abs(w_n @ y_floor) < 0.25
            if np.count_nonzero(vert_mask) >= 500:
                v_n = w_n[vert_mask]
                cov = v_n.T @ v_n / len(v_n)
                w, vecs = np.linalg.eigh(cov)
                plumb = vecs[:, 0]
                if np.dot(plumb, y_floor) < 0:
                    plumb = -plumb
                tilt_deg = math.degrees(math.acos(min(1.0, max(-1.0, float(np.dot(plumb, y_floor))))))
                if tilt_deg <= 5.0:
                    y = plumb / np.linalg.norm(plumb)
                    fl["diagnostics"]["plumb_refinement"] = {
                        "source": "wall_normals_pca",
                        "tilt_from_floor_deg": round(float(tilt_deg), 3)
                    }

    denom = float(np.dot(y, y_floor))
    t = - (float(np.dot(c, y_floor)) + d_floor) / (denom if abs(denom) > 1e-6 else 1.0)
    origin = c + t * y

    hts = (pts - origin) @ y
    near_floor = pts[np.abs(hts) < 0.02 * scene_extent(pts)]
    base = near_floor if len(near_floor) >= 100 else pts
    proj = base - np.outer((base - origin) @ y, y)
    w, v = np.linalg.eigh(np.cov((proj - origin).T))
    x = v[:, int(np.argmax(w))]
    x = x - (x @ y) * y
    x /= np.linalg.norm(x)
    z = np.cross(x, y)
    R = np.vstack([x, y, z])
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = -R @ origin
    cam_h = (cams - origin) @ y
    eye = float(np.median(cam_h))
    # a real floor has (almost) nothing below it; a table top or shelf found as "floor" does (Phase 13: kicker 0 %,
    # TUM desk 20 %, ETH3D pipes 21 %)
    below = float(np.mean(hts < -0.1 * eye)) if eye > 0 else 0.0
    meta = {"floor_detected": fl["floor_detected"], "up_source": fl["up_source"],
            "below_floor_fraction": round(below, 4), "floor_suspect": bool(fl["floor_detected"] and below > 0.15),
            "eye_height_units": float(np.median(cam_h)),
            "camera_height_p10_p90_units": [float(np.percentile(cam_h, 10)), float(np.percentile(cam_h, 90))],
            "diagnostics": fl["diagnostics"]}
    return T, meta


def geometry_consistency(points, centres, min_ratio=0.05):
    """(ok, ratio): spread of the points (90th-percentile distance from their median) over the spread of the
    camera centres (median distance from their centroid). A valid indoor reconstruction has a ratio of order 1
    (Phase 13: kicker 2.1, TUM 0.71); a collapsed one is near 0 (ARKit fast-sweep video 0.0001)."""
    pts = np.asarray(points, float)
    cams = np.asarray(centres, float)
    if len(cams) < 3 or len(pts) == 0:
        return True, None
    spread_cams = float(np.median(np.linalg.norm(cams - cams.mean(0), axis=1)))
    if spread_cams <= 0:
        return True, None
    ratio = scene_extent(pts[:: max(1, len(pts) // 200_000)]) / spread_cams
    return ratio >= min_ratio, ratio


def transform_points(T, pts):
    return np.asarray(pts, float) @ T[:3, :3].T + T[:3, 3]


def transform_dirs(T, dirs):
    return np.asarray(dirs, float) @ T[:3, :3].T


# --------------------------------------------------------------------------- roaming grid
def encode_rle(mask):
    rows = []
    for row in mask:
        runs, cur, n = [], False, 0
        for v in row:
            v = bool(v)
            if v == cur:
                n += 1
            else:
                runs.append(n)
                cur, n = v, 1
        runs.append(n)
        rows.append(runs)
    return rows


def roam_grid(points_model, cams_model, eye_h, *, cells_per_eye=32, band=(0.15, 1.2), min_pts=3, margin_cells=3,
              min_obstacle_eye=0.12):
    """Walk-permission grid for FIRST-PERSON ROAMING only (never for routes).

    blocked  = observed geometry in the body band [band[0]*eye_h, band[1]*eye_h] (>= min_pts points),
               minus blobs smaller than (min_obstacle_eye * eye_h)^2 (mostly floaters);
    allowed  = free cells CONNECTED to where the photographer stood (camera positions), inside the
               scene's extent: observed walls and furniture bound it, unobserved floor between them does not
               (it is shown with the viewer's floor guide). Roaming is viewing, not route guidance.
    footprint = observed floor, obstacles and camera areas, closed and hole-filled (floor-coverage figures);
               walking is limited to the footprint grown by half an eye height.
    """
    res = eye_h / cells_per_eye
    p = np.asarray(points_model, float)
    cams = np.asarray(cams_model, float)
    xs = np.concatenate([p[:, 0], cams[:, 0]])
    zs = np.concatenate([p[:, 2], cams[:, 2]])
    lo_x, hi_x = np.percentile(xs, [0.5, 99.5])
    lo_z, hi_z = np.percentile(zs, [0.5, 99.5])
    x0 = lo_x - margin_cells * res
    z0 = lo_z - margin_cells * res
    w = int(math.ceil((hi_x - x0) / res)) + margin_cells + 1
    h = int(math.ceil((hi_z - z0) / res)) + margin_cells + 1
    if w * h > 4_000_000:
        raise ValueError(f"roam grid too large ({w} x {h}); scene extent vs eye height looks wrong")
    ci = np.floor((p[:, 0] - x0) / res).astype(int)
    ri = np.floor((p[:, 2] - z0) / res).astype(int)
    ok = (ci >= 0) & (ci < w) & (ri >= 0) & (ri < h)
    y = p[:, 1]
    band_m = ok & (y > band[0] * eye_h) & (y < band[1] * eye_h)
    floor_m = ok & (np.abs(y) <= 0.06 * eye_h)
    obs = np.zeros((h, w), np.int32)
    flo = np.zeros((h, w), np.int32)
    np.add.at(obs, (ri[band_m], ci[band_m]), 1)
    np.add.at(flo, (ri[floor_m], ci[floor_m]), 1)
    blocked = obs >= min_pts
    lab, nlab = ndimage.label(blocked, structure=np.ones((3, 3)))
    if nlab:
        sizes = np.bincount(lab.ravel())
        # walking collision is for comfort: keep obstacles with at least (min_obstacle_eye x eye height)^2 of
        # footprint (about 17 x 17 cm at a 1.4 m capture height); smaller blobs are mostly MVS floaters and
        # would put invisible walls in open floor. Routes never use this grid (they use the conservative IF-04).
        min_cells = max(2, round((min_obstacle_eye * cells_per_eye) ** 2))
        small = sizes < min_cells
        small[0] = False
        blocked &= ~small[lab]
    cam_cells = np.zeros((h, w), bool)
    cc = np.floor((cams[:, 0] - x0) / res).astype(int)
    cr = np.floor((cams[:, 2] - z0) / res).astype(int)
    keep = (cc >= 0) & (cc < w) & (cr >= 0) & (cr < h)
    cam_cells[cr[keep], cc[keep]] = True
    lo = np.percentile(p[:, [0, 2]], 1, axis=0)
    hi = np.percentile(p[:, [0, 2]], 99, axis=0)
    xs_c = x0 + (np.arange(w) + 0.5) * res
    zs_c = z0 + (np.arange(h) + 0.5) * res
    inside = ((zs_c[:, None] >= min(lo[1], cams[:, 2].min())) & (zs_c[:, None] <= max(hi[1], cams[:, 2].max()))
              & (xs_c[None, :] >= min(lo[0], cams[:, 0].min())) & (xs_c[None, :] <= max(hi[0], cams[:, 0].max())))
    free = inside & ~blocked
    flab, _ = ndimage.label(free, structure=ndimage.generate_binary_structure(2, 1))
    seeds = np.unique(flab[cam_cells & free])
    seeds = seeds[seeds > 0]
    connected = np.isin(flab, seeds) if len(seeds) else free
    # reconstructed footprint (observed floor, obstacles, camera positions; closed, hole-filled): used for the
    # floor-coverage figure, and - grown by half an eye height - as the outer limit for walking, so gaps in
    # unreconstructed walls do not let the walker leave the room by more than that
    cam_area = ndimage.binary_dilation(cam_cells, iterations=max(1, cells_per_eye // 6))
    footprint = ndimage.binary_fill_holes(ndimage.binary_closing((flo > 0) | blocked | cam_area,
                                                                 structure=np.ones((3, 3)), iterations=3))
    limit = ndimage.binary_dilation(footprint, iterations=max(1, cells_per_eye // 2))
    allowed = connected & limit
    stats = {"cell_units": res, "width": w, "height": h, "allowed_cells": int(allowed.sum()),
             "min_obstacle_cells": max(2, round((min_obstacle_eye * cells_per_eye) ** 2)),
             "blocked_cells": int(blocked.sum()), "observed_floor_cells": int((flo > 0).sum()),
             "footprint_cells": int(footprint.sum()),
             "observed_floor_in_footprint_cells": int(((flo > 0) & footprint).sum())}
    grid = {"origin_xz": [float(x0), float(z0)], "cell": float(res), "width": w, "height": h,
            "encoding": "rle_rows_v1", "value": "allowed", "rows": encode_rle(allowed)}
    return grid, stats, {"allowed": allowed, "blocked": blocked, "floor_observed": flo > 0, "footprint": footprint}
