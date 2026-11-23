"""Experimental pose-spread dense selection; no claim of surface completeness."""
from pathlib import Path

import numpy as np


def pose_spread_subset(images, sharpness, count):
    """Greedy pose coverage with a modest sharpness preference, O(N) working memory.

    Normalize position by camera spread and combine it with forward-direction
    angle. Update distances only from the newest selected camera, avoiding an
    NxN matrix for large captures. Caller must explicitly opt into this method.
    """
    if not isinstance(count, int) or count < 1:
        raise ValueError('count must be a positive integer')
    names = sorted(images)
    if len(names) <= count:
        return names
    centers = np.asarray([images[n]['C'] for n in names], dtype=float)
    forward = np.asarray([images[n]['R'][2] for n in names], dtype=float)
    if not np.isfinite(centers).all() or not np.isfinite(forward).all():
        raise ValueError('Camera poses must be finite')
    radius = max(float(np.quantile(np.linalg.norm(centers - np.median(centers, axis=0), axis=1), .9)), 1e-9)
    sharp = np.array([sharpness.get(Path(n).stem.lower(), 0) for n in names], dtype=float)
    sharp = np.nan_to_num(sharp, nan=0, posinf=0, neginf=0)
    quality = .5 + .5 * np.clip(sharp / max(float(np.quantile(sharp, .9)), 1e-9), 0, 1)
    chosen = []
    closest = np.full(len(names), np.inf)
    index = int(np.argmax(sharp))
    for _ in range(count):
        chosen.append(index)
        spatial = np.linalg.norm(centers - centers[index], axis=1) / radius
        angular = np.arccos(np.clip(forward @ forward[index], -1, 1)) / np.pi
        closest = np.minimum(closest, spatial + .5 * angular)
        scores = closest * quality
        scores[chosen] = -1
        index = int(np.argmax(scores))
    return sorted(names[i] for i in chosen)
