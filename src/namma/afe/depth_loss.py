"""Measured-depth regional supervision on the original sensor grid."""
import torch
import torch.nn.functional as F


def measured_loss(rendered, alpha, sensor, valid):
    if (sensor.ndim!=2 or valid.shape!=sensor.shape or rendered.ndim!=3 or
            rendered.shape[0]!=1 or alpha.shape!=(*rendered.shape,1)):
        raise ValueError("aligned rendered depth, alpha and measured sensor grid required")
    depth = F.interpolate(rendered[:,None],size=sensor.shape,mode="area")[0,0]
    coverage = F.interpolate(alpha.permute(0,3,1,2),size=sensor.shape,mode="area")[0,0]
    mask = valid & torch.isfinite(sensor) & (sensor>.1) & (coverage>.9)
    if not mask.any():
        return rendered.sum()*0
    return ((depth[mask]-sensor[mask]).abs()/sensor[mask]).mean()
