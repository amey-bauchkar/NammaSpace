"""Conservative development-view observability for sampled surface points."""
import numpy as np
from namma.afe.calibration import camera


def assess_surface(points, views, *, max_mm_per_px=2.0, min_views=4, min_angle_deg=15.0):
    """Use aligned measured depth and pose-qualified images to assess each point.

    A view supplies a capture pass ID, camera-to-world OpenCV transform, K,
    measured depth grid at those intrinsics, confidence mask and blur estimate.
    Retrieval and depth agreement use optical-axis metres.
    """
    xyz = np.asarray(points, dtype=float)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or not len(xyz) or not np.isfinite(xyz).all():
        raise ValueError("surface points must be a finite Nx3 array")
    if max_mm_per_px <= 0 or min_views < 2 or not 0 < min_angle_deg < 180:
        raise ValueError("invalid observability thresholds")
    counts = np.zeros(len(xyz), dtype=int)
    max_angle = np.zeros(len(xyz))
    has_independent_pair = np.zeros(len(xyz), dtype=bool)
    previous = []
    seen = set()
    for view in views:
        if view["role"] != "dev":
            raise ValueError("observability accepts development views only")
        if view["id"] in seen:
            raise ValueError("duplicate development view")
        seen.add(view["id"])
        blur = view.get("blur_px")
        if (not view.get("pose_consistent") or blur is None or not 0 <= blur < 1.5
                or view.get("depth_source") != "measured"):
            continue
        T, K = (np.asarray(view[k], float) for k in ("camera_to_world", "K"))
        depth = np.asarray(view["depth_m"], float)
        confidence = np.asarray(view["high_confidence"], dtype=bool)
        noise = float(view["depth_noise_m"])
        if (T.shape != (4, 4) or K.shape != (3, 3) or depth.ndim != 2 or not depth.size
                or depth.shape != confidence.shape or not np.isfinite(T).all() or not np.isfinite(K).all()
                or not np.isfinite(noise) or noise <= 0 or min(K[0, 0], K[1, 1]) <= 0):
            raise ValueError("invalid measured view geometry")
        K,T = camera(K,T)
        rays = xyz - T[:3, 3]
        cam = rays @ T[:3, :3]
        z = cam[:, 2]
        image = cam @ K.T
        h, w = depth.shape
        valid = z > 0
        xy = np.divide(image[:, :2], z[:, None], out=np.zeros_like(image[:, :2]), where=valid[:, None])
        x, y = np.rint(np.clip(xy, [-1, -1], [w, h])).astype(int).T
        valid &= (x >= 0) & (x < w) & (y >= 0) & (y < h)
        x, y = x.clip(0, w-1), y.clip(0, h-1)
        measured = depth[y, x]
        valid &= (confidence[y, x] & (measured > 0) & (np.abs(z-measured) <= noise)
                  & (1000*z / min(K[0, 0], K[1, 1]) <= max_mm_per_px))
        direction = rays / np.maximum(np.linalg.norm(rays, axis=1, keepdims=True), 1e-12)
        for other, other_valid, pass_id in previous:
            pair = valid & other_valid
            angle = np.degrees(np.arccos(np.clip(np.sum(direction*other, axis=1), -1, 1)))
            max_angle = np.maximum(max_angle, np.where(pair, angle, 0))
            if pass_id is not None and view.get("pass_id") is not None and pass_id != view["pass_id"]:
                has_independent_pair |= pair & (angle >= min_angle_deg)
        previous.append((direction, valid, view.get("pass_id")))
        counts += valid
    return {"sharp_views": counts, "angle_spread_deg": max_angle,
            "sufficient": (counts >= min_views) & has_independent_pair}
