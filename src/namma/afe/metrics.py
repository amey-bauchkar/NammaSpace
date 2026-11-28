"""Image structure diagnostics. Repeated-edge correlation is a proxy requiring scene validation."""
import cv2
import numpy as np
from namma.afe.calibration import camera as validate_camera


def image_structure(render, observation):
    if render.shape!=observation.shape or render.ndim!=3 or render.shape[-1]!=3:
        raise ValueError("matching RGB grids required")
    def gray(image):
        return cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)
    def edges(image):
        return cv2.Canny(gray(image),60,120)>0
    a,b = edges(render),edges(observation)
    def near(edge):
        return cv2.distanceTransform((~edge).astype(np.uint8),cv2.DIST_L2,5)<=2
    precision = float((a&near(b)).sum()/max(1,a.sum()))
    recall = float((b&near(a)).sum()/max(1,b.sum()))
    def duplicate(edge):
        scores = []
        for offset in range(2,min(31,min(edge.shape))):
            for axis in (0,1):
                first = edge[:-offset,:] if axis==0 else edge[:,:-offset]
                second = edge[offset:,:] if axis==0 else edge[:,offset:]
                scores.append(float((first&second).sum()/max(1,min(first.sum(),second.sum()))))
        return max(scores,default=0.)
    energy = lambda image:float(cv2.Laplacian(gray(image),cv2.CV_64F).var())
    observed_energy = energy(observation)
    return {"edge_precision_2px":precision,"edge_recall_2px":recall,
            "edge_fscore_2px":2*precision*recall/(precision+recall) if precision+recall else 0.,
            "render_duplicate_edge_proxy":duplicate(a),"observation_duplicate_edge_proxy":duplicate(b),
            "high_frequency_energy_ratio":energy(render)/observed_energy if observed_energy else None}


def evidence_support(points, observations, *, require_edge=False):
    """Project proposed detail into real, independently captured development images.

    Observations supply calibrated world-to-camera matrices, K and RGB. Optional
    measured depth with a noise bound tests visibility. Returned support is a
    necessary check, with outline and colour tests still required separately.
    """
    points = np.asarray(points,float)
    if points.ndim!=2 or points.shape[1]!=3 or not np.isfinite(points).all():
        raise ValueError("finite world points required")
    supported = [set() for _ in points]
    coverage = [set() for _ in points]
    for view in observations:
        if view["role"]!="dev":
            raise ValueError("iterative proposal support requires development observations")
        image = np.asarray(view["rgb"])
        if image.ndim!=3 or image.shape[2]!=3 or image.dtype!=np.uint8 or min(image.shape[:2])<1:
            raise ValueError("nonempty uint8 RGB evidence required")
        K,T = validate_camera(view["K"],view["world_to_camera"])
        camera = points@T[:3,:3].T+T[:3,3]
        z = camera[:,2]
        pixel = camera@K.T
        uv = np.divide(pixel[:,:2],z[:,None],out=np.zeros_like(pixel[:,:2]),where=z[:,None]>0)
        height,width = image.shape[:2]
        valid = (z>0)&(uv[:,0]>=0)&(uv[:,0]<width)&(uv[:,1]>=0)&(uv[:,1]<height)
        xy = np.rint(np.clip(uv,[0,0],[width-1,height-1])).astype(int)
        for index in valid.nonzero()[0]:
            if view.get("pass_id") is not None:
                coverage[index].add(view["pass_id"])
        if view.get("measured_depth") is not None:
            depth = np.asarray(view["measured_depth"],float)
            if depth.shape!=(height,width):
                raise ValueError("support depth must share image calibration")
            noise = view.get("depth_noise_m")
            if type(noise) not in (int,float) or not np.isfinite(noise) or noise<0:
                raise ValueError("finite nonnegative measured-depth noise required")
            expected = depth[xy[:,1],xy[:,0]]
            valid &= (expected>0)&np.isfinite(expected)&(np.abs(expected-z)<=noise)
        else:
            # Frustum coverage and edge coincidence alone cannot establish visibility.
            valid[:] = False
        if require_edge:
            edge = cv2.Canny(cv2.cvtColor(image,cv2.COLOR_RGB2GRAY),60,120)>0
            distance = cv2.distanceTransform((~edge).astype(np.uint8),cv2.DIST_L2,5)
            valid &= distance[xy[:,1],xy[:,0]]<=2
        for index in valid.nonzero()[0]:
            if view.get("pass_id") is not None:
                supported[index].add(view["pass_id"])
    counts = np.asarray([len(passes) for passes in supported])
    return {"independent_pass_counts":counts,"supported":counts>=(2 if require_edge else 1),
            "frustum_pass_counts":np.asarray([len(passes) for passes in coverage]),
            "visibility_protocol":"measured_depth_only"}
