"""Development residual back-projection and bounded regional proposal clustering."""
import numpy as np
from namma.afe.calibration import camera as validate_camera


def regions(render, observation, depth, K, camera_to_world, *, role="dev", threshold=.08,
            voxel_m=.2, margin_m=.25, min_extent_m=.5, max_extent_m=2.):
    if role!="dev":
        raise ValueError("iterative localisation requires development observations")
    if (render.shape!=observation.shape or render.ndim!=3 or render.shape[-1]!=3
            or depth.shape!=render.shape[:2] or render.dtype!=np.uint8 or observation.dtype!=np.uint8):
        raise ValueError("aligned RGB and depth grids required")
    if not 0<threshold<=1 or not 0<voxel_m or not 0<min_extent_m<=max_extent_m<=2.5 or margin_m<0:
        raise ValueError("invalid localisation limits")
    K,T = validate_camera(K,camera_to_world)
    residual = np.mean(np.abs(render.astype(float)-observation.astype(float)),axis=-1)/255
    valid = (residual>=threshold)&np.isfinite(depth)&(depth>0)
    y,x = np.nonzero(valid)
    if not len(x):
        return []
    z = depth[y,x]
    camera = np.c_[(x-K[0,2])/K[0,0]*z,(y-K[1,2])/K[1,1]*z,z]
    world = camera@T[:3,:3].T+T[:3,3]
    cells = np.floor(world/voxel_m).astype(np.int64)
    unique = {tuple(cell) for cell in cells}
    groups = []
    while unique:
        todo = [min(unique)]
        group = set()
        while todo:
            cell = todo.pop()
            if cell not in unique:
                continue
            unique.remove(cell)
            group.add(cell)
            for axis in range(3):
                for delta in (-1,1):
                    neighbour = list(cell)
                    neighbour[axis]+=delta
                    todo.append(tuple(neighbour))
        groups.append(group)
    proposals = []
    for group in groups:
        low = np.min(list(group),axis=0)*voxel_m-margin_m
        high = (np.max(list(group),axis=0)+1)*voxel_m+margin_m
        size = np.maximum(high-low,min_extent_m)
        low -= (size-(high-low))/2
        steps = np.maximum(1,np.ceil(size/max_extent_m).astype(int))
        # Tile large defect clusters while retaining explicit context in each trial's metadata.
        for index in np.ndindex(*steps):
            start = low+np.asarray(index)*size/steps
            end = start+size/steps
            centre = (start+end)/2
            extent = np.maximum(end-start,min_extent_m)
            proposals.append({"lower":(centre-extent/2).tolist(),"upper":(centre+extent/2).tolist(),
                              "context_margin_m":margin_m,"source":"development_photometric_residual",
                              "observability":"requires_assessment"})
    return proposals
