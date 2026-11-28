"""Build a private candidate PLY by replacing regional rows in a sealed parent."""
import hashlib
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement


def _vertices(path):
    ply = PlyData.read(str(path))
    if len(ply.elements) != 1 or ply.elements[0].name != "vertex":
        raise ValueError("expected a Gaussian vertex table")
    rows = ply["vertex"].data
    if not len(rows) or any(rows.dtype[k].kind != "f" or rows.dtype[k].itemsize != 4
                            for k in rows.dtype.names):
        raise ValueError("requires nonempty float32 Gaussian rows")
    if not all(np.isfinite(rows[k]).all() for k in rows.dtype.names):
        raise ValueError("nonfinite Gaussian parameters")
    return rows


def merge_region(parent, regional, output, replaced_ids, lower, upper):
    """Write a candidate; retain original order and exact bytes for every frozen row."""
    original = _vertices(parent)
    proposal = _vertices(regional)
    fields = original.dtype.names
    if "row_id" not in fields or not {"x", "y", "z"} <= set(fields):
        raise ValueError("parent lacks stable IDs or positions")
    if set(proposal.dtype.names) - {"row_id"} != set(fields) - {"row_id"}:
        raise ValueError("regional parameter layout differs from parent")
    ids = original["row_id"]
    if (ids.min() < 0 or ids.max() >= 2**24 or (ids != np.floor(ids)).any()
            or len(np.unique(ids)) != len(ids)):
        raise ValueError("invalid parent IDs")
    replaced = np.asarray(replaced_ids)
    if replaced.ndim != 1 or not replaced.size or replaced.dtype.kind not in "iu" or len(np.unique(replaced)) != len(replaced):
        raise ValueError("requires distinct integer replacement IDs")
    mask = np.isin(ids, replaced)
    if mask.sum() != len(replaced):
        raise ValueError("unknown replacement ID")
    lo, hi = np.asarray(lower), np.asarray(upper)
    if (lo.shape != (3,) or hi.shape != (3,) or not np.isfinite([lo, hi]).all()
            or (lo >= hi).any()):
        raise ValueError("invalid regional bounds")
    for axis, key in enumerate(("x", "y", "z")):
        for coords in (original[key][mask], proposal[key]):
            if (coords < lo[axis]).any() or (coords > hi[axis]).any():
                raise ValueError("Gaussian outside regional bounds")
    first = int(ids.max()) + 1
    if first + len(proposal) > 2**24:
        raise ValueError("insufficient exact row IDs")
    frozen = original[~mask]
    merged = np.empty(len(frozen) + len(proposal), dtype=original.dtype)
    merged[:len(frozen)] = frozen
    for key in fields:
        merged[key][len(frozen):] = (np.arange(first, first + len(proposal))
                                     if key == "row_id" else proposal[key])
    with Path(output).open("xb") as stream:
        PlyData([PlyElement.describe(merged, "vertex")]).write(stream)
    saved = _vertices(output)
    if saved.tobytes() != merged.tobytes() or saved[:len(frozen)].tobytes() != frozen.tobytes():
        raise ValueError("serialized candidate failed row integrity")
    return {"replaced_ids": ids[mask].astype(int).tolist(), "added_ids": list(range(first, first + len(proposal))),
            "frozen_rows": len(frozen), "frozen_sha256": hashlib.sha256(frozen.tobytes()).hexdigest()}
