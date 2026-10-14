"""POI route endpoints (WP-06, ARCH-008, V-07).

Three distinct locations per POI:
1. marker_site_m       - where the object physically is (3-D);
2. route_endpoint_xz_m - where a person can stand to reach it (free cell centre);
3. view                - where the camera goes to inspect it.

Endpoints are computed against a specific navigation layer and recorded with
endpoint_geometry_id / endpoint_navlayer_id (contract 1.1.0). After any
geometry change the endpoint is stale and must be recomputed (IF09-ENDPOINT-STALE).
"""
import math

from namma.contracts.binding import quantize
from namma.contracts.semantic import POLICY_MAX_GOAL_SNAP_M


def is_stale(poi, navlayer):
    return (poi.get("route_endpoint_xz_m") is None or poi.get("endpoint_navlayer_id") != navlayer.navlayer_id
            or poi.get("endpoint_geometry_id") != navlayer.geometry_id)


def compute_endpoint(poi, navlayer, origin_xz, max_snap_m=POLICY_MAX_GOAL_SNAP_M):
    """Set route_endpoint_xz_m / endpoint_reachable / endpoint_* ids on a POI (in place).

    Reachability is relative to `origin_xz` (the release's primary start point).
    """
    marker_xz = (poi["marker_site_m"][0], poi["marker_site_m"][2])
    cell = navlayer.cell_of(marker_xz)
    poi["endpoint_geometry_id"] = navlayer.geometry_id
    poi["endpoint_navlayer_id"] = navlayer.navlayer_id
    if poi["verification"]["status"] == "rejected":
        poi["route_endpoint_xz_m"], poi["endpoint_reachable"] = None, None
        poi.pop("endpoint_snap_m", None)
        return poi
    if cell is not None and navlayer.free[cell]:
        end_cell, snap = cell, 0.0
        endpoint = [quantize(marker_xz[0]), quantize(marker_xz[1])]
    else:
        end_cell, snap = navlayer.nearest_free(marker_xz, max_snap_m)
        if end_cell is None:
            poi["route_endpoint_xz_m"], poi["endpoint_reachable"] = None, False
            poi.pop("endpoint_snap_m", None)
            return poi
        c = navlayer.centre(end_cell)
        endpoint = [quantize(c[0]), quantize(c[1])]
    labels = navlayer.components()
    o = navlayer.cell_of(origin_xz)
    reachable = bool(o is not None and navlayer.free[o] and labels[o] == labels[end_cell])
    poi["route_endpoint_xz_m"] = endpoint
    poi["endpoint_snap_m"] = round(math.dist(endpoint, marker_xz), 6)
    poi["endpoint_reachable"] = reachable
    return poi


def recompute_all(pois, navlayer, origin_xz):
    changed = []
    for p in pois:
        if is_stale(p, navlayer):
            before = (p.get("route_endpoint_xz_m"), p.get("endpoint_reachable"))
            compute_endpoint(p, navlayer, origin_xz)
            changed.append({"poi_id": p["poi_id"], "before": before,
                            "after": (p.get("route_endpoint_xz_m"), p.get("endpoint_reachable"))})
    return changed
