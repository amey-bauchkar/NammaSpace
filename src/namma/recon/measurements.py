"""Tape-measurement sheet -> independent reference artifacts (Phase 12 capture pack).

Inputs: the CSV templates in docs/phase12_capture_pack/templates/ filled by hand in the room.
Point coordinates come from two tape distances (from marker M0 and marker M1) plus a side flag,
by trilateration in the floor plane (site frame: origin M0, +X towards M1, +Z = +X x +Y).
Nothing here reads a reconstruction: the outputs are INDEPENDENT ground truth.
"""
import csv
import math
from pathlib import Path

from namma import CONTRACT_VERSION
from namma.core.frames import IDENTITY_ROW_MAJOR


class SheetError(ValueError):
    pass


def _rows(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [{k.strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]


def _num(v, what):
    try:
        x = float(v)
    except ValueError as exc:
        raise SheetError(f"{what}: {v!r} is not a number") from exc
    if not math.isfinite(x):
        raise SheetError(f"{what}: not finite")
    return x


def trilaterate(points_rows):
    """Return id -> (x, y, z), plus issues. M0 = origin, M1 on +X."""
    rows = {r["point_id"]: r for r in points_rows}
    if "M0" not in rows or "M1" not in rows:
        raise SheetError("reference_points.csv must contain M0 and M1")
    L = _num(rows["M1"]["d_from_M0_m"], "M1.d_from_M0_m")
    if L <= 0.5:
        raise SheetError("M0-M1 distance must be > 0.5 m")
    out, issues = {"M0": (0.0, 0.0, 0.0), "M1": (L, 0.0, 0.0)}, []
    for pid, r in rows.items():
        if pid in ("M0", "M1"):
            continue
        a = _num(r["d_from_M0_m"], f"{pid}.d_from_M0_m")
        b = _num(r["d_from_M1_m"], f"{pid}.d_from_M1_m")
        if r.get("side") not in ("+z", "-z"):
            raise SheetError(f"{pid}: side must be +z or -z")
        x = (a * a - b * b + L * L) / (2 * L)
        zz = a * a - x * x
        if zz < -0.0004:  # tape triangle inequality violated by more than ~2 cm equivalent
            issues.append(f"{pid}: distances inconsistent with M0-M1 (a={a}, b={b}, L={L})")
        z = math.sqrt(max(zz, 0.0)) * (1 if r["side"] == "+z" else -1)
        if abs(z) < 0.2:
            issues.append(f"{pid}: within 0.2 m of the M0-M1 line; trilateration is ill-conditioned there")
        out[pid] = (x, _num(r.get("height_m") or 0, f"{pid}.height_m"), z)
    return out, issues


def check_distances(points, dist_rows):
    res = []
    for r in dist_rows:
        tape = _num(r["tape_m"], f"{r['id']}.tape_m")
        unc = _num(r.get("uncertainty_m") or 0.01, f"{r['id']}.uncertainty_m")
        a, b = points.get(r["from_point"]), points.get(r["to_point"])
        if a is None or b is None:
            res.append({"id": r["id"], "status": "unknown_point"})
            continue
        model = math.dist((a[0], a[2]), (b[0], b[2]))
        err = model - tape
        res.append({"id": r["id"], "tape_m": tape, "from_coordinates_m": round(model, 4), "error_m": round(err, 4),
                    "ok": abs(err) <= 2 * unc + 0.01})
    return res


def build_reference(sheet_dir, frame_id, created_at, measured_by_role="CAP"):
    d = Path(sheet_dir)
    pts, issues = trilaterate(_rows(d / "reference_points.csv"))
    checks = check_distances(pts, _rows(d / "distances.csv"))
    prov = {"origin": "human_authored", "method": "tape measurements (two-distance trilateration from M0 / M1)",
            "author_role": measured_by_role, "created_at": created_at}
    outline = [pts[r["point_id"]] for r in _rows(d / "room_outline.csv")]
    if len(outline) < 3:
        raise SheetError("room_outline.csv needs at least 3 vertices")
    obstacles, inventory = [], []
    for r in _rows(d / "obstacles.csv"):
        ring = [[pts[p][0], pts[p][2]] for p in r["corner_point_ids"].split(";")]
        obstacles.append({"obstacle_id": r["obstacle_id"], "floor_id": "F0", "footprint": ring,
                          "min_y_m": _num(r["min_height_m"], "min_height_m"), "max_y_m": _num(r["max_height_m"], "max_height_m"),
                          "kind": r["kind"], "confidence": 1.0, "provenance": dict(prov)})
        inventory.append({"ref_id": r["obstacle_id"], "kind": r["kind"], "footprint": ring, "tolerance_m": 0.10,
                          "major": r.get("major", "yes").lower() == "yes"})
    doorways, door_inv = [], []
    for r in _rows(d / "doorways.csv"):
        a, b = pts[r["side_a_point_id"]], pts[r["side_b_point_id"]]
        doorways.append({"doorway_id": r["doorway_id"], "floor_id": "F0", "segment": [[a[0], a[2]], [b[0], b[2]]],
                         "measured_width_m": _num(r["tape_width_m"], "tape_width_m"), "provenance": dict(prov)})
        door_inv.append({"ref_id": r["doorway_id"], "tape_width_m": _num(r["tape_width_m"], "tape_width_m"),
                         "required": r.get("required", "yes").lower() == "yes"})
    frame = {"frame_id": frame_id, "units": "m", "handedness": "right", "up_axis": "+Y",
             "datum": "centre of floor marker M0; +X towards the centre of marker M1; +Z = +X x +Y"}
    geometry = {"contract": "IF-04", "contract_version": CONTRACT_VERSION, "geometry_id": f"geom-{frame_id}-measured-v1",
                "run_id": frame_id, "site_frame": frame,
                "to_site": {"matrix_row_major": list(IDENTITY_ROW_MAJOR), "source": "tape_scale",
                            "method": "authored directly in the site frame from tape measurements", "residual_m": None},
                "floors": [{"floor_id": "F0", "elevation_m": 0.0, "walkable_regions": [[[p[0], p[2]] for p in outline]]}],
                "obstacles": obstacles, "doorways": doorways, "provenance": dict(prov)}
    inventory_doc = {"reference_type": "obstacle_inventory_v1", "reference_id": f"ref-{frame_id}-tape-v1",
                     "frame_id": frame_id, "source": "tape_sketch", "obstacles": inventory, "doorways": door_inv,
                     "provenance": dict(prov)}
    markers = {k: list(v) for k, v in pts.items() if k.startswith("M")}
    return {"points_site": {k: list(v) for k, v in pts.items()}, "markers_site": markers,
            "marker_fit_ids": [m for m in ("M0", "M1", "M2", "M3") if m in markers],
            "marker_check_ids": [m for m in markers if m not in ("M0", "M1", "M2", "M3")],
            "distance_checks": checks, "issues": issues, "reference_geometry": geometry,
            "reference_inventory": inventory_doc}
