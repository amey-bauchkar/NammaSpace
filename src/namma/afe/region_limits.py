"""Bounded experimental regional extents, separate from generic tensor operations."""
import numpy as np


def check(region, max_extent=2.):
    lower, upper = np.asarray(region["lower"],float),np.asarray(region["upper"],float)
    if (not np.isfinite(max_extent) or not 0<max_extent<=2.5 or
            lower.shape!=(3,) or upper.shape!=(3,) or not np.isfinite([lower,upper]).all() or
            (upper<=lower).any() or (upper-lower).max()>max_extent):
        raise ValueError("regional crop exceeds the explicit experiment extent limit")
    return lower,upper
