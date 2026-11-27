"""Deterministic training-only pair proposals. COLMAP must verify every match."""
import numpy as np


def propose_pairs(priors, *, window=8, radius_m=2.0, max_revisits=12):
    ids = list(priors)
    if window < 1 or max_revisits < 1 or not np.isfinite(radius_m) or radius_m <= 0:
        raise ValueError("invalid pair proposal limits")
    transforms = np.asarray([priors[k] for k in ids], dtype=float)
    if transforms.shape != (len(ids), 4, 4) or not np.isfinite(transforms).all():
        raise ValueError("expected finite camera poses")
    centres = transforms[:, :3, 3]
    directions = -transforms[:, :3, 2]
    pairs = {(i, j) for i in range(len(ids)) for j in range(i+1, min(len(ids), i+window+1))}
    sequential = len(pairs)
    for i in range(len(ids)):
        distance = np.linalg.norm(centres - centres[i], axis=1)
        eligible = [j for j in range(len(ids)) if abs(i-j) > window and distance[j] <= radius_m
                    and directions[j] @ directions[i] > 0]
        for j in sorted(eligible, key=lambda j: (distance[j], j))[:max_revisits]:
            pairs.add(tuple(sorted((i, j))))
    return [(ids[i], ids[j]) for i, j in sorted(pairs)], {
        "sequential": sequential, "revisit_candidates": len(pairs)-sequential,
        "total": len(pairs), "radius_m": radius_m, "window": window,
        "limitation": "ARKit proximity can miss drifted loops; visual retrieval still required"}
