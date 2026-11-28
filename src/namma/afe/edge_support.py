"""Independent image support for changed edges projected from a candidate."""
import cv2
import numpy as np


def unsupported_edges(edge_count, observations, *, tolerance_px=2.0):
    """Return candidate edge IDs lacking support from two distinct capture passes.

    Each observation supplies a pass ID, detected photo edges and visible
    candidate samples as integer [edge ID, x, y] rows. Visibility and camera
    projection must be established before calling this function.
    """
    if edge_count < 1 or not np.isfinite(tolerance_px) or tolerance_px < 0:
        raise ValueError("invalid edge count or tolerance")
    per_pass = {}
    for pass_id, photo_edges, samples in observations:
        if pass_id is None:
            continue
        photo_edges = np.asarray(photo_edges, dtype=bool)
        samples = np.asarray(samples)
        if (photo_edges.ndim != 2 or not photo_edges.size or samples.ndim != 2
                or samples.shape[1] != 3 or samples.dtype.kind not in "iu"):
            raise ValueError("invalid edge observation")
        ids, x, y = samples.T
        h, w = photo_edges.shape
        if np.any((ids >= edge_count) | (ids < 0) | (x < 0) | (x >= w) | (y < 0) | (y >= h)):
            raise ValueError("edge projection outside observation")
        if not photo_edges.any():
            continue
        distance = cv2.distanceTransform((~photo_edges).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        supported = per_pass.setdefault(pass_id, np.zeros(edge_count, dtype=bool))
        supported[ids[distance[y, x] <= tolerance_px]] = True
    votes = sum(per_pass.values(), start=np.zeros(edge_count, dtype=int))
    return np.flatnonzero(votes < 2)
