"""Contiguous Polycam timestamp blocks for development and reserved evaluation."""
from math import ceil


def time_blocks(frame_ids, excluded=()):
    ids = sorted(frame_ids, key=int)
    if len({int(k) for k in ids}) != len(ids):
        raise ValueError("duplicate capture timestamp")
    n = ceil(len(ids) / 10)
    if len(ids) - 2 * n < 3:
        raise ValueError("insufficient training frames after reserving two time blocks")
    return {"method": "chronological_timestamp_blocks", "train": ids[:-2*n],
            "dev": ids[-2*n:-n], "final_test": ids[-n:],
            "excluded": [{"photo": k, "reason": "incomplete capture"} for k in excluded]}
