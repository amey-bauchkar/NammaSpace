"""Measure whether candidate Gaussian centres are supported by reserved views."""
import numpy as np

from namma.capture.polycam_raw import opencv_c2w


def project_points(points, pose, K, height, width, *, near=.01):
    camera = opencv_c2w(pose)
    local = (points - camera[:3, 3]) @ camera[:3, :3]
    z = local[:, 2]
    pixel = local @ np.asarray(K).T
    front = z >= near
    xy = np.divide(pixel[:, :2], z[:, None], out=np.zeros_like(pixel[:, :2]), where=front[:, None])
    x, y = np.rint(np.clip(xy, [-1, -1], [width, height])).astype(np.intp).T
    inside = front & (x >= 0) & (x < width) & (y >= 0) & (y < height)
    return inside, z, x, y


def point_support(points, views, depth_noise_m):
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError("invalid candidate centre array")
    if not np.isfinite(depth_noise_m) or depth_noise_m <= 0:
        raise ValueError("requires a positive metric depth tolerance")
    frustum = np.zeros(len(points), dtype=int)
    measured = np.zeros(len(points), dtype=int)
    supported = np.zeros(len(points), dtype=int)
    for view in views:
        inside, _, _, _ = project_points(points, view["pose"], view["K"], view["height"], view["width"])
        frustum += inside
        if "depth" not in view:
            continue
        depth = np.asarray(view["depth"])
        if depth.ndim != 2 or not depth.size or not np.isfinite(depth).all() or (depth < 0).any():
            raise ValueError("invalid measured depth grid")
        valid, z, x, y = project_points(points, view["pose"], view["depth_K"], *depth.shape)
        ids = np.flatnonzero(valid & inside)
        samples = depth[y[ids], x[ids]]
        measured[ids[samples > 0]] += 1
        supported[ids[(samples > 0) & (np.abs(samples - z[ids]) <= depth_noise_m)]] += 1
    return {"frustum_views": frustum, "measured_views": measured, "supporting_views": supported}
