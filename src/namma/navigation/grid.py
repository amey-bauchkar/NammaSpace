"""Navigation layer (IF-05): conservative occupancy grid (WP-04, ARCH-004, ARCH-024).

Rasterisation is EXACTLY conservative: a cell is blocked if its closed square
intersects (or touches) any blocking obstacle footprint, or is not fully
covered by the walkable region (floor minus holes). The Phase 8 prototype
approximated this with 5 x 5 point samples per cell, which can miss features
thinner than a quarter cell (tests/test_prototype_regressions.py).

Grid convention (ARCH-020): row r spans z in [oz + r*res, oz + (r+1)*res),
column c spans x in [ox + c*res, ox + (c+1)*res); `blocked[r, c]`.
Stored occupancy is configuration space: blocked after inflating by
agent radius + extra margin (distance between cell centres, as in Phase 8).
"""
import math
from dataclasses import dataclass

import numpy as np
import shapely
from scipy import ndimage

from namma import CONTRACT_VERSION
from namma.contracts.semantic import MAX_NAV_RESOLUTION_M, decode_rle_issues
from namma.core.issues import has_errors
from namma.geometry.model import DEFAULT_BODY_BAND, thinnest_obstacle_m

BUILDER = {"name": "namma-navlayer", "version": "1.0.0"}
FOUR_CONNECTED = ndimage.generate_binary_structure(2, 1)


@dataclass
class NavLayer:
    navlayer_id: str
    geometry_id: str
    geometry_sha256: str
    floor_id: str
    origin: tuple
    res: float
    blocked: np.ndarray            # configuration space (inflated)
    agent_radius_m: float
    extra_margin_m: float
    body_band: tuple
    raw_blocked: np.ndarray = None  # before inflation (not serialised)

    @property
    def height(self):
        return self.blocked.shape[0]

    @property
    def width(self):
        return self.blocked.shape[1]

    @property
    def free(self):
        return ~self.blocked

    def cell_of(self, xz):
        """(row, col) containing site point [x, z], or None if outside the grid."""
        c = math.floor((xz[0] - self.origin[0]) / self.res)
        r = math.floor((xz[1] - self.origin[1]) / self.res)
        if 0 <= r < self.height and 0 <= c < self.width:
            return r, c
        return None

    def centre(self, cell):
        r, c = cell
        return (self.origin[0] + (c + 0.5) * self.res, self.origin[1] + (r + 0.5) * self.res)

    def components(self):
        """4-connected labels of free cells.

        With 8-connected moves and no corner cutting, a diagonal step requires
        both orthogonal neighbours to be free, so reachability equals
        4-connectivity (the Phase 8 prototype used 8-connected labels).
        """
        labels, _ = ndimage.label(self.free, structure=FOUR_CONNECTED)
        return labels

    def nearest_free(self, xz, max_dist_m):
        """Nearest free cell centre to a site point within max_dist_m (Euclidean, point to centre)."""
        free_idx = np.argwhere(self.free)
        if free_idx.size == 0:
            return None, math.inf
        cx = self.origin[0] + (free_idx[:, 1] + 0.5) * self.res
        cz = self.origin[1] + (free_idx[:, 0] + 0.5) * self.res
        d = np.hypot(cx - xz[0], cz - xz[1])
        k = int(np.argmin(d))
        if d[k] > max_dist_m:
            return None, float(d[k])
        return (int(free_idx[k, 0]), int(free_idx[k, 1])), float(d[k])

    def to_doc(self):
        return {"contract": "IF-05", "contract_version": CONTRACT_VERSION, "navlayer_id": self.navlayer_id,
                "geometry_id": self.geometry_id, "geometry_sha256": self.geometry_sha256,
                "builder": dict(BUILDER), "floor_id": self.floor_id,
                "origin_xz_m": [float(self.origin[0]), float(self.origin[1])], "resolution_m": self.res,
                "width": self.width, "height": self.height, "rasterization": "conservative",
                "agent": {"radius_m": self.agent_radius_m, "extra_margin_m": self.extra_margin_m},
                "body_band_m": list(self.body_band), "occupancy_encoding": "rle_rows_v1",
                "occupancy": encode_rle(self.blocked)}


def encode_rle(blocked):
    rows = []
    for row in blocked:
        runs, cur, n = [], False, 0
        for v in row:
            v = bool(v)
            if v == cur:
                n += 1
            else:
                runs.append(n)
                cur, n = v, 1
        runs.append(n)
        rows.append(runs)
    return rows


def decode_rle(rows, width, height):
    doc = {"width": width, "height": height, "occupancy": rows}
    issues = decode_rle_issues(doc)
    if has_errors(issues):
        raise ValueError("; ".join(i.message for i in issues))
    out = np.zeros((height, width), dtype=bool)
    for r, runs in enumerate(rows):
        c, val = 0, False
        for n in runs:
            out[r, c:c + n] = val
            c += n
            val = not val
    return out


def from_doc(doc):
    blocked = decode_rle(doc["occupancy"], doc["width"], doc["height"])
    return NavLayer(doc["navlayer_id"], doc["geometry_id"], doc.get("geometry_sha256"), doc["floor_id"],
                    tuple(doc["origin_xz_m"]), doc["resolution_m"], blocked, doc["agent"]["radius_m"],
                    doc["agent"]["extra_margin_m"], tuple(doc["body_band_m"]))


TOUCH_EPS_M = 1e-9  # exact-touch robustness: cell edges computed in floating point


def _cell_boxes(origin, res, rows, cols, grow=0.0):
    """Vectorised shapely boxes for row / column index arrays, grown by `grow` metres."""
    x0 = origin[0] + cols * res
    z0 = origin[1] + rows * res
    return shapely.box(x0 - grow, z0 - grow, x0 + res + grow, z0 + res + grow)


def rasterize(geometry, floor_id, res, body_band=DEFAULT_BODY_BAND, origin=None, shape=None):
    """Raw (uninflated) conservative occupancy. Returns (blocked, origin)."""
    floor = geometry.floors[floor_id]
    obs = geometry.blocking_obstacles(floor_id, body_band)
    if origin is None:
        parts = [floor.walkable] + [o.polygon for o in obs]
        minx = min(p.bounds[0] for p in parts)
        minz = min(p.bounds[1] for p in parts)
        maxx = max(p.bounds[2] for p in parts)
        maxz = max(p.bounds[3] for p in parts)
        origin = (math.floor(minx / res + 1e-9) * res, math.floor(minz / res + 1e-9) * res)
        shape = (math.ceil((maxz - origin[1]) / res - 1e-9), math.ceil((maxx - origin[0]) / res - 1e-9))
    h, w = shape
    rows, cols = np.indices((h, w))
    # Walkable test uses slightly SHRUNK cells and the obstacle test slightly GROWN
    # cells, so a cell that exactly touches an obstacle is always blocked and a
    # cell exactly on the floor edge is never blocked by rounding alone.
    boxes = _cell_boxes(origin, res, rows.ravel(), cols.ravel(), grow=-TOUCH_EPS_M)
    shapely.prepare(floor.walkable)
    blocked = ~shapely.covered_by(boxes, floor.walkable)
    for o in obs:
        bx0, bz0, bx1, bz1 = o.polygon.bounds
        c0 = max(0, math.floor((bx0 - origin[0]) / res) - 1)
        c1 = min(w, math.floor((bx1 - origin[0]) / res) + 2)
        r0 = max(0, math.floor((bz0 - origin[1]) / res) - 1)
        r1 = min(h, math.floor((bz1 - origin[1]) / res) + 2)
        if c0 >= c1 or r0 >= r1:
            continue
        rr, cc = np.indices((r1 - r0, c1 - c0))
        rr, cc = rr.ravel() + r0, cc.ravel() + c0
        hit = shapely.intersects(_cell_boxes(origin, res, rr, cc, grow=TOUCH_EPS_M), o.polygon)
        blocked[rr[hit] * w + cc[hit]] = True
    return blocked.reshape(h, w), origin


def inflate(raw_blocked, res, distance_m):
    if distance_m <= 0:
        return raw_blocked.copy()
    dist = ndimage.distance_transform_edt(~raw_blocked) * res
    return dist <= distance_m  # identical rule to the Phase 8 prototype (cell-centre distance)


def navlayer_id_for(geometry, floor_id, res, radius, margin):
    tag = (geometry.sha256 or "nohash")[:12]
    return f"nav-{floor_id}-{tag}-r{res:g}-a{radius:g}-m{margin:g}"


def build_navlayer(geometry, floor_id="F0", res=0.05, agent_radius_m=0.25, extra_margin_m=0.0,
                   body_band=DEFAULT_BODY_BAND, navlayer_id=None, enforce_resolution=True):
    """Build an IF-05 navigation layer from validated IF-04 geometry."""
    if agent_radius_m <= 0:
        raise ValueError("agent radius must be > 0 (explicit agent clearance, ARCH-004)")
    if extra_margin_m < 0:
        raise ValueError("extra margin must be >= 0")
    if enforce_resolution:
        if res > MAX_NAV_RESOLUTION_M + 1e-12:
            raise ValueError(f"resolution {res} m exceeds the ARCH-004 limit {MAX_NAV_RESOLUTION_M} m")
        thin = thinnest_obstacle_m(geometry, floor_id)
        if res > thin + 1e-9:
            raise ValueError(f"resolution {res} m exceeds the thinnest obstacle {thin:.3f} m (ARCH-004)")
    raw, origin = rasterize(geometry, floor_id, res, body_band)
    blocked = inflate(raw, res, agent_radius_m + extra_margin_m)
    nid = navlayer_id or navlayer_id_for(geometry, floor_id, res, agent_radius_m, extra_margin_m)
    return NavLayer(nid, geometry.geometry_id, geometry.sha256, floor_id, origin, res, blocked,
                    agent_radius_m, extra_margin_m, tuple(body_band), raw_blocked=raw)
