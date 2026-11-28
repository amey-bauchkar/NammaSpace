"""Mask uncertain depth discontinuities on the original supervision grid."""
import torch
import torch.nn.functional as F


def reliable_depth(depth, relative_jump=.15, dilation=2):
    valid = torch.isfinite(depth) & (depth > .1)
    edge = ~valid
    for axis in (0, 1):
        a = depth[:-1, :] if axis == 0 else depth[:, :-1]
        b = depth[1:, :] if axis == 0 else depth[:, 1:]
        jump = (a-b).abs() > relative_jump * torch.minimum(a, b).clamp_min(.1)
        if axis == 0:
            edge[:-1, :] |= jump
            edge[1:, :] |= jump
        else:
            edge[:, :-1] |= jump
            edge[:, 1:] |= jump
    if dilation:
        edge = F.max_pool2d(edge.float()[None, None], 2*dilation+1, stride=1, padding=dilation)[0,0] > 0
    return valid & ~edge
