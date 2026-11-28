"""Map an unpacked gsplat render back to the regional DefaultStrategy row order."""


def regional_strategy_info(info, frozen_count, regional_count):
    """Call after backward, with frozen context preceding all regional rows."""
    projected = info["means2d"]
    radii = info["radii"]
    if (frozen_count < 0 or regional_count < 1 or projected.ndim != 3
            or projected.shape[1] != frozen_count + regional_count
            or radii.shape[:2] != projected.shape[:2]):
        raise ValueError("unexpected unpacked Gaussian row layout")
    local = projected.detach()[:, frozen_count:]
    local.absgrad = projected.absgrad.detach()[:, frozen_count:]
    return {**info, "means2d": local, "radii": radii[:, frozen_count:]}
