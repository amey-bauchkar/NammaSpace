"""Candidate-only overlap ownership reconciliation with conservative duplicate tests."""
import numpy as np


def reconcile(parts, cores, support, *, tolerance_m=.002):
    """Retain evidence-supported halo rows when the nominal owner has no nearby row.

    parts contain means, DC colour and scales in a common metric frame. support
    arrays count independently visible sharp observations. Geometry is selected
    from existing rows, with no interpolation. All outputs still require seam
    render comparisons and independent validation.
    """
    from scipy.spatial import cKDTree
    if len(parts)!=len(cores) or len(parts)!=len(support) or not np.isfinite(tolerance_m) or tolerance_m<=0:
        raise ValueError("invalid overlap reconciliation inputs")
    points = np.concatenate([np.asarray(p["means"],float) for p in parts])
    colours = np.concatenate([np.asarray(p["colour"],float) for p in parts])
    scales = np.concatenate([np.asarray(p["scales"],float) for p in parts])
    counts = np.concatenate([np.asarray(s,float) for s in support])
    if counts.ndim!=1 or not np.isfinite(counts).all() or (counts!=np.floor(counts)).any():
        raise ValueError("overlap support requires measured integer observation counts")
    if points.shape!=(len(counts),3) or colours.shape!=points.shape or scales.shape!=points.shape or not all(
            np.isfinite(a).all() for a in (points,colours,scales)) or (counts<0).any():
        raise ValueError("invalid Gaussian overlap data")
    offsets = np.cumsum([0]+[len(p["means"]) for p in parts])
    owned = np.zeros(len(points),bool)
    for block,rect in enumerate(cores):
        x0,x1,z0,z1 = rect
        region = points[offsets[block]:offsets[block+1]]
        owned[offsets[block]:offsets[block+1]] = ((region[:,0]>=x0)&(region[:,0]<x1)&
                                               (region[:,2]>=z0)&(region[:,2]<z1))
    tree = cKDTree(points)
    rank = sorted(range(len(points)),key=lambda i:(-int(owned[i]),-int(counts[i]),i))
    blocked = np.zeros(len(points),bool)
    kept = []
    for index in rank:
        if blocked[index] or counts[index]<2:
            continue
        kept.append(index)
        for other in tree.query_ball_point(points[index],tolerance_m):
            # Close centres alone do not establish a duplicate. Preserve distinct textures or covariance scales.
            if np.max(np.abs(colours[other]-colours[index]))<=.02 and np.max(
                    np.abs(scales[other]-scales[index]))<=.05:
                blocked[other] = True
    return {"selected_global_rows":np.asarray(sorted(kept),int),
            "part_offsets":offsets,"removed_rows":len(points)-len(kept),"status":"candidate_requires_seam_validation"}
