"""Independently compare regional PLY artifacts against a sealed source checkpoint."""
import hashlib
import numpy as np

from namma.core.jsonio import load_strict, sha256_file
from namma.evidence.bundle import verify_checkpoint


def verify_candidate(checkpoint, candidate, ledger):
    from plyfile import PlyData
    verify_checkpoint(checkpoint)
    record = load_strict(ledger)
    if (record["base_sha256"] != sha256_file(checkpoint/"manifest.json") or
            record["candidate_sha256"] != sha256_file(candidate)):
        raise ValueError("regional candidate is not bound to the source checkpoint")
    source = PlyData.read(str(checkpoint/"splat.ply"))["vertex"].data
    target = PlyData.read(str(candidate))["vertex"].data
    if source.dtype != target.dtype or "row_id" not in source.dtype.names:
        raise ValueError("regional candidate requires identical schemas and stable row IDs")
    if any(not np.isfinite(target[name]).all() for name in target.dtype.names):
        raise ValueError("nonfinite candidate Gaussian attributes")
    def index(rows):
        ids = rows["row_id"]
        if (not np.isfinite(ids).all() or (ids<0).any() or (ids>=2**24).any() or
                (ids != np.floor(ids)).any() or len(np.unique(ids)) != len(ids)):
            raise ValueError("invalid or duplicated stable Gaussian row IDs")
        return {int(value):i for i,value in enumerate(ids)}
    original_ids, candidate_ids = index(source), index(target)
    lower, upper = np.asarray(record["region"]["lower"]), np.asarray(record["region"]["upper"])
    if (lower.shape!=(3,) or upper.shape!=(3,) or not np.isfinite([lower,upper]).all() or
            (upper<=lower).any()):
        raise ValueError("invalid candidate region bounds")
    def centres(rows):
        xyz = np.column_stack([rows[key] for key in ("x","y","z")])
        if not np.isfinite(xyz).all():
            raise ValueError("nonfinite candidate geometry")
        return xyz
    inside = lambda xyz: ((xyz>=lower)&(xyz<=upper)).all(-1)
    source_inside, target_inside = inside(centres(source)), inside(centres(target))
    frozen_indices = np.flatnonzero(~source_inside)
    matched = []
    for i in frozen_indices:
        identity = int(source["row_id"][i])
        if identity not in candidate_ids or source[i].tobytes()!=target[candidate_ids[identity]].tobytes():
            raise ValueError("frozen Gaussian changed or disappeared")
        matched.append(candidate_ids[identity])
    if set(np.flatnonzero(~target_inside)) != set(matched):
        raise ValueError("candidate introduced geometry outside its trainable region")
    added = set(candidate_ids)-set(original_ids)
    if added and min(added)<=max(original_ids):
        raise ValueError("candidate reused a retired Gaussian row ID")
    digest = hashlib.sha256(source[frozen_indices].tobytes()).hexdigest()
    if digest != record["frozen_rows_sha256"]:
        raise ValueError("candidate ledger's frozen-row digest is incorrect")
    if not source_inside.any():
        raise ValueError("source region is empty")
    changed = sorted(identity for identity, i in candidate_ids.items()
                     if identity not in original_ids or target[i].tobytes() != source[original_ids[identity]].tobytes())
    return {"integrity_exact":True,"base_sha256":record["base_sha256"],
            "candidate_sha256":record["candidate_sha256"],"frozen_rows":len(frozen_indices),
            "region_count_ratio":int(target_inside.sum())/int(source_inside.sum()),
            "added_rows":len(added), "changed_ids": changed,
            "region_rows_before": int(source_inside.sum()), "region_rows_after": int(target_inside.sum()),"appearance_pixels_verified":False,
            "unsupported_content_verified":False,"acceptance_evaluated":False}
