"""Deterministic, density-matched initialization for the regional control arm."""
import torch
import torch.nn.functional as F


def uniform_split(rows, target, lower, upper, seed):
    """Allocate children evenly; divide opacity and displace along the longest local axis."""
    means = rows["means"]
    n = len(means)
    if not n <= target <= 2 * n:
        raise ValueError("density control must match a candidate within the 2x regional budget")
    generator = torch.Generator(device=means.device).manual_seed(seed)
    parents = torch.randperm(n, generator=generator, device=means.device)[:target-n]
    indices = torch.cat([torch.arange(n, device=means.device), parents])
    out = {key: value[indices].clone() for key, value in rows.items()}
    if not len(parents):
        return out, indices
    axis = rows["scales"][parents].argmax(dim=1)
    direction = F.one_hot(axis, 3).to(means)
    quaternion = F.normalize(rows["quats"][parents], dim=1)
    w, xyz = quaternion[:, :1], quaternion[:, 1:]
    cross = 2 * torch.cross(xyz, direction, dim=1)
    direction = direction + w * cross + torch.cross(xyz, cross, dim=1)
    spread = rows["scales"][parents].gather(1, axis[:, None]).exp() * .35
    lo = torch.as_tensor(lower, device=means.device, dtype=means.dtype)
    hi = torch.as_tensor(upper, device=means.device, dtype=means.dtype)
    clearance = torch.minimum(means[parents]-lo, hi-means[parents])
    room = torch.where(direction.abs() > 0, clearance / direction.abs(), torch.inf)
    offset = direction * torch.minimum(spread, room.amin(dim=1, keepdim=True).clamp_min(0))
    children = torch.arange(n, target, device=means.device)
    out["means"][parents] -= offset
    out["means"][children] += offset
    # At the original centre, two coincident children retain the parent's transmittance.
    opacity = -torch.expm1(F.logsigmoid(-rows["opacities"][parents]) * .5)
    logits = torch.logit(opacity, eps=torch.finfo(means.dtype).eps)
    out["opacities"][parents] = logits
    out["opacities"][children] = logits
    return out, indices
