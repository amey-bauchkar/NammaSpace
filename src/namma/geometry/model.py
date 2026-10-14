"""IF-04 geometry model and geometric-VALIDITY checks (WP-03).

Validity means the artifact is well formed: valid polygons, consistent ids,
provenance on every feature, finite numbers, a correct site frame. It does NOT
mean the geometry is complete or correct with respect to the real venue - see
geometry/checks.py (completeness, alignment, human inspection; ARCH-023).

Frame rule (ARCH-020): IF-04 coordinates are already in the site frame; this
module never applies `to_site`.
"""
import math
from dataclasses import dataclass, field

import shapely
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from namma.core import frames
from namma.core.issues import error, has_errors, warning
from namma.core.jsonio import dumps, load_strict, sha256_bytes, sha256_file

MIN_AREA_M2 = 1e-6
DOORWAY_WIDTH_TOL_M = 0.05  # V-03 (provisional, ARCH-017)
DEFAULT_BODY_BAND = (0.05, 1.9)


@dataclass
class Floor:
    floor_id: str
    elevation_m: float
    walkable: object  # shapely (Multi)Polygon: union(walkable_regions) - union(holes)


@dataclass
class Obstacle:
    obstacle_id: str
    floor_id: str
    polygon: Polygon
    min_y_m: float
    max_y_m: float
    kind: str
    confidence: float
    provenance: dict


@dataclass
class Doorway:
    doorway_id: str
    floor_id: str
    segment: LineString
    measured_width_m: object
    provenance: dict


@dataclass
class Geometry:
    geometry_id: str
    frame_id: str
    floors: dict
    obstacles: list
    doorways: list
    doc: dict
    sha256: str = None
    source_path: str = None
    issues: list = field(default_factory=list)

    def floor(self, floor_id=None):
        if floor_id is None:
            if len(self.floors) != 1:
                raise ValueError("floor_id required for multi-floor geometry (ARCH-018)")
            return next(iter(self.floors.values()))
        return self.floors[floor_id]

    def blocking_obstacles(self, floor_id, body_band=DEFAULT_BODY_BAND):
        """Obstacles whose vertical extent overlaps the body band above the floor."""
        elev = self.floors[floor_id].elevation_m
        lo, hi = elev + body_band[0], elev + body_band[1]
        return [o for o in self.obstacles
                if o.floor_id == floor_id and o.max_y_m > lo and o.min_y_m < hi]

    def blocking_union(self, floor_id, body_band=DEFAULT_BODY_BAND):
        obs = self.blocking_obstacles(floor_id, body_band)
        return unary_union([o.polygon for o in obs]) if obs else Polygon()


def ring_to_polygon(ring, path):
    """Build a polygon from an IF polygon2 ring; return (polygon or None, issues)."""
    issues = []
    pts = [tuple(p) for p in ring]
    if any(not math.isfinite(v) for p in pts for v in p):
        return None, [error("GEOM-NONFINITE", "polygon has a non-finite coordinate", path)]
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    distinct = list(dict.fromkeys(pts))
    if len(distinct) < 3:
        return None, [error("GEOM-DEGENERATE", "polygon needs at least 3 distinct points", path)]
    poly = Polygon(pts)
    if poly.convex_hull.area < MIN_AREA_M2:
        return None, [error("GEOM-DEGENERATE", "polygon points are collinear (zero area)", path)]
    if not poly.is_valid:
        return None, [error("GEOM-INVALID-POLYGON", f"invalid polygon: {shapely.is_valid_reason(poly)}", path)]
    if poly.area < MIN_AREA_M2:
        return None, [error("GEOM-DEGENERATE", f"polygon area {poly.area:.3g} m^2 is effectively zero", path)]
    return poly, issues


def _provenance_issues(prov, path, feature):
    if not isinstance(prov, dict):
        return [error("GEOM-PROVENANCE-MISSING", f"{feature} has no provenance (ARCH-007)", path)]
    out = []
    if prov.get("origin") == "human_corrected" and not prov.get("author_role"):
        out.append(error("GEOM-PROVENANCE-ROLE", "human_corrected feature must name author_role", path))
    return out


def load_geometry(source):
    """Load and validity-check an IF-04 document (dict or path).

    Returns (Geometry or None, issues). A Geometry is returned only if there are
    no errors. Schema validation is done by the caller (contracts.semantic) or
    by `validate_geometry_document`.
    """
    path = None
    if not isinstance(source, dict):
        path = str(source)
        doc = load_strict(source)
    else:
        doc = source
    issues = []
    frame = doc.get("site_frame", {})
    for msg in frames.check_site_frame(frame):
        issues.append(error("GEOM-FRAME", msg, "$.site_frame"))
    for msg in frames.check_transform(doc.get("to_site", {})):
        issues.append(error("GEOM-TRANSFORM", msg, "$.to_site"))

    floors = {}
    for i, f in enumerate(doc.get("floors", [])):
        p = f"$.floors[{i}]"
        fid = f.get("floor_id")
        if fid in floors:
            issues.append(error("GEOM-DUPLICATE-ID", f"duplicate floor_id {fid!r}", p))
            continue
        regions = []
        for j, ring in enumerate(f.get("walkable_regions", [])):
            poly, iss = ring_to_polygon(ring, f"{p}.walkable_regions[{j}]")
            issues += iss
            if poly is not None:
                regions.append(poly)
        holes = []
        for j, ring in enumerate(f.get("holes", []) or []):
            poly, iss = ring_to_polygon(ring, f"{p}.holes[{j}]")
            issues += iss
            if poly is not None:
                holes.append(poly)
        walk = unary_union(regions) if regions else Polygon()
        for j, h in enumerate(holes):
            if not h.intersects(walk):
                issues.append(warning("GEOM-HOLE-OUTSIDE", "hole does not intersect any walkable region",
                                      f"{p}.holes[{j}]"))
        if holes:
            walk = walk.difference(unary_union(holes))
        if walk.is_empty:
            issues.append(error("GEOM-NO-WALKABLE", f"floor {fid!r} has no walkable area", p))
        floors[fid] = Floor(fid, float(f.get("elevation_m", 0.0)), walk)

    obstacles, seen = [], set()
    for i, o in enumerate(doc.get("obstacles", [])):
        p = f"$.obstacles[{i}]"
        oid = o.get("obstacle_id")
        if oid in seen:
            issues.append(error("GEOM-DUPLICATE-ID", f"duplicate obstacle_id {oid!r}", p))
        seen.add(oid)
        if o.get("floor_id") not in floors:
            issues.append(error("GEOM-UNKNOWN-FLOOR", f"obstacle {oid!r} references unknown floor", p))
        issues += _provenance_issues(o.get("provenance"), f"{p}.provenance", f"obstacle {oid!r}")
        lo, hi = o.get("min_y_m"), o.get("max_y_m")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and not lo < hi:
            issues.append(error("GEOM-HEIGHT", f"obstacle {oid!r}: min_y_m must be < max_y_m", p))
        poly, iss = ring_to_polygon(o.get("footprint", []), f"{p}.footprint")
        issues += iss
        if poly is not None and o.get("floor_id") in floors:
            obstacles.append(Obstacle(oid, o["floor_id"], poly, float(lo), float(hi), o.get("kind"),
                                      float(o.get("confidence", 1.0)), o.get("provenance")))

    doorways, seen = [], set()
    for i, d in enumerate(doc.get("doorways", []) or []):
        p = f"$.doorways[{i}]"
        did = d.get("doorway_id")
        if did in seen:
            issues.append(error("GEOM-DUPLICATE-ID", f"duplicate doorway_id {did!r}", p))
        seen.add(did)
        if d.get("floor_id") not in floors:
            issues.append(error("GEOM-UNKNOWN-FLOOR", f"doorway {did!r} references unknown floor", p))
        issues += _provenance_issues(d.get("provenance"), f"{p}.provenance", f"doorway {did!r}")
        seg = d.get("segment", [])
        if len(seg) != 2 or any(not math.isfinite(v) for pt in seg for v in pt):
            issues.append(error("GEOM-DOORWAY-SEGMENT", "doorway segment must be 2 finite points", p))
            continue
        line = LineString(seg)
        if line.length <= 0:
            issues.append(error("GEOM-DOORWAY-SEGMENT", "doorway segment has zero length", p))
            continue
        w = d.get("measured_width_m")
        if w is None:
            issues.append(warning("GEOM-DOORWAY-UNMEASURED", f"doorway {did!r} has no tape width", p))
        elif abs(line.length - w) > DOORWAY_WIDTH_TOL_M:
            issues.append(error("GEOM-DOORWAY-WIDTH",
                                f"doorway {did!r}: segment {line.length:.3f} m vs tape {w:.3f} m "
                                f"(> {DOORWAY_WIDTH_TOL_M} m, V-03)", p))
        doorways.append(Doorway(did, d.get("floor_id"), line, w, d.get("provenance")))

    issues += _provenance_issues(doc.get("provenance"), "$.provenance", "geometry artifact")
    if has_errors(issues):
        return None, issues
    # In-memory documents are hashed exactly as jsonio.write_json would write them,
    # so the hash equals the SHA-256 of the file the pipeline later writes.
    digest = sha256_file(path) if path else sha256_bytes(dumps(doc).encode("utf-8"))
    geom = Geometry(doc.get("geometry_id"), frame.get("frame_id"), floors, obstacles, doorways, doc,
                    sha256=digest, source_path=path, issues=issues)
    return geom, issues


def thinnest_obstacle_m(geometry, floor_id):
    """Smallest obstacle thickness (2 x maximum inscribed circle radius)."""
    best = math.inf
    for o in geometry.obstacles:
        if o.floor_id != floor_id:
            continue
        mic = shapely.maximum_inscribed_circle(o.polygon, tolerance=1e-4)
        best = min(best, 2.0 * mic.length)
    return best
