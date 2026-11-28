"""Small per-image bilateral affine residual grid, sampled in original image coordinates."""
import torch
import torch.nn.functional as F


def apply_grid(rgb, grid, box=(0., 0., 1., 1.)):
    _, height, width, _ = rgb.shape
    x0, y0, x1, y1 = box
    y = torch.linspace(y0*2-1, y1*2-1, height, device=rgb.device)
    x = torch.linspace(x0*2-1, x1*2-1, width, device=rgb.device)
    yy, xx = torch.meshgrid(y, x, indexing="ij")
    guidance = rgb.detach().mean(-1)[0].clamp(0,1)*2-1
    locations = torch.stack([xx, yy, guidance], -1)[None,None]
    coefficients = F.grid_sample(grid[None], locations, mode="bilinear",
                                 padding_mode="border", align_corners=True)[0,:,0].permute(1,2,0)
    affine = coefficients.reshape(height, width, 3, 4)
    return rgb + torch.einsum("hwij,hwj->hwi", affine[..., :3], rgb[0])[None] + affine[...,3][None]


def regularisation(grid):
    smooth = sum((grid.diff(dim=axis)**2).mean() for axis in (1,2,3))
    return smooth + grid.square().mean()
