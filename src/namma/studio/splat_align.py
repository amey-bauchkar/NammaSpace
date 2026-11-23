"""Line a trained splat (scripts/splat/train_splat.py, Polycam ARKit frame) up with the imported Polycam mesh.

    T, stats = align_splat_to_mesh(ply_path, glb_bytes)     # 4x4: splat frame -> mesh (GLB) frame

Both frames are metric and gravity-up (ARKit / glTF +Y); Polycam re-centres and turns the exported model about the
vertical. The splat's SOLID splats (opacity > 0.5) lie on the room's surfaces, fixed by all keyframes together - far
better geometry than one keyframe's depth map (iPhone 14: estimated depth; aligning those reached 16-35 % within
5 cm). Search: turn about +Y in 2-degree steps with a short yaw-only ICP each, then a full rigid ICP from the best.
"""
import numpy as np
from scipy.spatial import cKDTree

from namma.studio import mesh_import as mi


def read_splat_ply(path, fields=("x", "y", "z", "opacity")):
    """Binary little-endian 3DGS .ply -> {field: array} (float32 vertex properties only)."""
    with open(path, "rb") as f:
        head = b""
        while not head.endswith(b"end_header\n"):
            head += f.readline()
        lines = head.decode("ascii").splitlines()
        n = int(next(ln.split()[2] for ln in lines if ln.startswith("element vertex")))
        props = [ln.split()[2] for ln in lines if ln.startswith("property float")]
        data = np.fromfile(f, dtype="<f4", count=n * len(props)).reshape(n, len(props))
    return {k: data[:, props.index(k)] for k in fields}


def _rot_y(a):
    return np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])


def regional_discrepancy(points, distances, *, bins=4, min_points=50):
    """XZ grid diagnostics in the mesh frame, not an accuracy or coverage score.

    Distances are to sampled mesh points, not exact triangle distances. Empty
    cells and cells with few points remain explicitly unassessed. Report all
    cells so a dense room cannot conceal a poorly supported adjacent room.
    """
    points = np.asarray(points, dtype=float)
    distances = np.asarray(distances, dtype=float)
    if (points.ndim != 2 or points.shape[1] != 3 or
            distances.shape != (len(points),) or not len(points) or
            not np.isfinite(points).all() or not np.isfinite(distances).all() or
            (distances < 0).any()):
        raise ValueError('Expected finite Nx3 points and N nonnegative distances')
    if not isinstance(bins, int) or bins < 1 or not isinstance(min_points, int) or min_points < 1:
        raise ValueError('bins and min_points must be positive integers')
    xz = points[:, [0, 2]]
    low, high = xz.min(axis=0), xz.max(axis=0)
    span = high - low
    indices = np.minimum((bins * (xz - low) / np.where(span > 0, span, 1)).astype(int), bins - 1)
    cells = []
    for ix in range(bins):
        for iz in range(bins):
            dd = distances[(indices[:, 0] == ix) & (indices[:, 1] == iz)]
            supported = len(dd) >= min_points
            cells.append({'cell_xz': [ix, iz], 'points': len(dd),
                          'status': 'sampled' if supported else 'insufficient_samples',
                          'median_scene_units': float(np.median(dd)) if supported else None,
                          'p90_scene_units': float(np.percentile(dd, 90)) if supported else None})
    return {'grid_bins': bins, 'minimum_points': min_points,
            'bounds_xz': [low.tolist(), high.tolist()], 'cells': cells,
            'limitation': 'Fit residuals against sampled imported mesh, not independent accuracy. '
                          'Grid cells are not identified rooms. Unsampled areas and baked-in repairs are unknown. '
                          'Scene units depend on imported scale; distances include mesh sampling error.'}


def _icp(X, Q, tree, iters, keep, yaw_only, radius):
    R_tot, t_tot = np.eye(3), np.zeros(3)
    for _ in range(iters):
        dd, nn = tree.query(X, distance_upper_bound=radius, workers=-1)
        ok = np.isfinite(dd)
        if ok.sum() < 50:
            break
        if keep is not None:                      # trimmed: worst pairs dropped. keep=None: distance (radius) only -
            ok &= dd <= np.percentile(dd[ok], keep)   # trimming can drop exactly the minority of points (an end wall)
                                                      # that pins a slide along the main walls
        a, b = X[ok], Q[nn[ok]]
        ma, mb = a.mean(0), b.mean(0)
        H = (a - ma).T @ (b - mb)
        if yaw_only:
            R = _rot_y(np.arctan2(H[2, 0] - H[0, 2], H[0, 0] + H[2, 2]))
        else:
            U, _, Vt = np.linalg.svd(H)
            R = Vt.T @ np.diag([1, 1, np.sign(np.linalg.det(Vt.T @ U.T))]) @ U.T
        t = mb - ma @ R.T
        X = X @ R.T + t
        R_tot, t_tot = R @ R_tot, R @ t_tot + t
    return X, R_tot, t_tot


def _refine(X, Q, tree, robust):
    """Final rigid ICP: as before (trimmed to the best 70 %, 20 cm), or robust: every pair within a shrinking radius
    (30 -> 10 -> 5 cm), so a minority of constraining surface is never discarded."""
    if not robust:
        return _icp(X, Q, tree, 40, 70, False, 0.2)
    R_tot, t_tot = np.eye(3), np.zeros(3)
    for radius, iters in ((0.3, 15), (0.1, 15), (0.05, 15)):
        X, R, t = _icp(X, Q, tree, iters, None, False, radius)
        R_tot, t_tot = R @ R_tot, R @ t_tot + t
    return X, R_tot, t_tot


def align_splat_to_mesh(ply_path, glb, *, init_T=None, min_opacity=0.05, **kw):
    s = read_splat_ply(ply_path)
    op = 1 / (1 + np.exp(-s["opacity"]))
    solid = op > 0.5
    if solid.sum() < 5000:
        solid = op >= min_opacity
    P = np.c_[s["x"][solid], s["y"][solid], s["z"][solid]].astype(np.float64)
    T, stats = align_points_to_mesh(P, glb, init_T=init_T, **kw)
    return T, {"solid_splats": int(solid.sum()), **stats}


MAX_ALIGN_SAMPLES = 4_000_000


def surface_sample_count(V, F, samples, samples_per_m2=0, limit=MAX_ALIGN_SAMPLES):
    """Number of mesh surface samples: `samples`, raised to samples_per_m2 x area (at most `limit`).
    The fit and its "within 2 / 5 cm" figures measure the distance to the nearest SAMPLE: with a fixed count the
    spacing grows with the space (200 k over a 300 m2 hall = 4 cm apart), which inflates every distance, blunts the
    ICP and can fail the line-up gate for a splat that is in fact well placed."""
    if not samples_per_m2:
        return int(samples)
    tri = np.asarray(V, np.float64)[np.asarray(F)]
    area = float(np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1).sum() / 2)
    return int(min(limit, max(samples, samples_per_m2 * area)))


def align_points_to_mesh(P, glb, *, init_T=None, samples=200_000, samples_per_m2=0, max_points=40_000, seed=0,
                         robust=False):
    """Surface points (splat or triangulated photo points) -> (4x4 points frame -> mesh frame, fit stats)."""
    P = np.asarray(P, np.float64)
    rng = np.random.default_rng(seed)
    if len(P) > max_points:
        P = P[rng.choice(len(P), max_points, replace=False)]
    V, F, _ = mi.mesh_triangles(glb)
    samples = surface_sample_count(V, F, samples, samples_per_m2)
    Q, _, _ = mi.sample_surface(V, F, samples)
    tree = cKDTree(Q)

    if init_T is not None:
        init_T = np.asarray(init_T, float)
        R0 = init_T[:3, :3]
        t0 = init_T[:3, 3]
        X, R2, t2 = _refine(P @ R0.T + t0, Q, tree, robust)
        Rf, tf = R2 @ R0, R2 @ t0 + t2
    else:
        def yaw_fit(Ps, deg):
            R0 = _rot_y(np.radians(deg))
            t0 = Q.mean(0) - P.mean(0) @ R0.T
            X, R, t = _icp(Ps @ R0.T + t0, Q, tree, 8, 70, True, 0.5)
            return float((tree.query(X, workers=-1)[0] < 0.05).mean()), deg, R @ R0, R @ t0 + t

        # coarse-to-fine (same answer, ~10x faster): every turn on a 5 k subsample, then the 3 best on all points
        sub = P[rng.choice(len(P), min(len(P), 5000), replace=False)]
        coarse = sorted((yaw_fit(sub, deg) for deg in np.arange(0, 360, 2.0)), key=lambda b: -b[0])
        _, deg, Rb, tb = max((yaw_fit(P, c[1]) for c in coarse[:3]), key=lambda b: b[0])
        X, R2, t2 = _refine(P @ Rb.T + tb, Q, tree, robust)
        Rf, tf = R2 @ Rb, R2 @ tb + t2
    dd = tree.query(X, workers=-1)[0]
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = Rf, tf
    # second-best turn far from the best: how unambiguous the answer is
    # init_T may carry a scale (Polycam LiDAR: phone tracking -> Polycam's frame is 0.97x): angles from the rotation
    # alone, or a 0.963 scale reads as a 15.6 degree tilt
    sc = float(np.cbrt(abs(np.linalg.det(Rf)))) or 1.0
    Rn = Rf / sc
    stats = {"used": int(len(P)), "mesh_samples": int(samples),
             "yaw_deg": round(float(np.degrees(np.arctan2(Rn[0, 2], Rn[0, 0]))), 2),
             "tilt_deg": round(float(np.degrees(np.arccos(np.clip(Rn[1, 1], -1, 1)))), 2),
             "scale": round(sc, 5),
             "within_2cm": round(float((dd < 0.02).mean()), 3), "within_5cm": round(float((dd < 0.05).mean()), 3),
             "median_m": round(float(np.median(dd)), 4),
             "regional_discrepancy": regional_discrepancy(X, dd)}
    return T, stats
