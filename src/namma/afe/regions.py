"""Plan metric regional crops with disjoint cores and overlapping frozen context."""
from itertools import product
from math import ceil, isfinite

import numpy as np


def tile_defect(lower, upper, *, margin=.25, minimum=.5, maximum=2.5,
                anchor=False, experimental_extent=None):
    lo, hi = np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)
    if (lo.shape != (3,) or hi.shape != (3,) or not np.isfinite([lo, hi]).all()
            or np.any(hi < lo)):
        raise ValueError("expected finite ordered defect bounds")
    if not all(isfinite(v) for v in (minimum, maximum, margin)) or not .5 <= minimum <= maximum <= 2.5 or margin < 0:
        raise ValueError("invalid crop configuration")
    cap = min(maximum, 2.) if anchor else maximum
    if experimental_extent is not None:
        if not minimum <= experimental_extent <= maximum:
            raise ValueError("experimental crop extent outside permitted range")
        cap = experimental_extent
    if cap < minimum or cap <= 2 * margin:
        raise ValueError("crop extent leaves no trainable core")
    counts = [max(1, ceil(span / (cap - 2*margin))) for span in hi-lo]
    boundaries = [np.linspace(a,b,n+1) for a,b,n in zip(lo,hi,counts)]
    result = []
    for index in product(*(range(n) for n in counts)):
        core_lo = np.array([axis[i] for axis,i in zip(boundaries,index)])
        core_hi = np.array([axis[i+1] for axis,i in zip(boundaries,index)])
        extent = experimental_extent or min(cap,max(minimum,float(max(core_hi-core_lo))+2*margin))
        result.append({"center": ((core_lo+core_hi)/2).tolist(), "extent_m": extent,
                       "core_min": core_lo.tolist(), "core_max": core_hi.tolist(),
                       "include_upper": [i == n-1 for i,n in zip(index,counts)],
                       "context_margin_m": margin, "experimental_extent": experimental_extent,
                       "anchor_offset_m": .015*extent if anchor else None})
    return result
