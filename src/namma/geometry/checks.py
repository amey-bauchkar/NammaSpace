"""Geometry check report (P-06, V-01..V-10; ARCH-007, ARCH-023).

Four separate questions, never merged into one "valid" flag:
1. validity     - is the IF-04 well formed? (model.load_geometry)
2. completeness - does it contain every major obstacle / doorway / connection
                  in an INDEPENDENT reference (sketch, tape inventory, or the
                  synthetic design)? Without a reference: "not_established".
3. alignment    - scale / datum / orientation / tilt vs tape marks (V-05, V-06).
4. inspection   - features a human must look at (glass, unknown_risk, low
                  confidence, human-corrected, unmatched extras).
Route validity (V-08, V-09) is per route: navigation/validator.py.

Acceptance: "accepted" only if nothing failed and nothing is unestablished;
"accepted_with_limitations" only with an explicit LEAD acceptance record that
names every open item; otherwise "not_accepted".
"""
import math

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from namma.core import timefmt
from namma.core.issues import as_dicts, has_errors
from namma.geometry.model import load_geometry, thinnest_obstacle_m

COVERAGE_MIN = 0.5           # fraction of a reference footprint that must be covered (V-01)
SCALE_TOL_ABS, SCALE_TOL_REL = 0.05, 0.01   # V-05: error <= max(0.05 m, 1 %) (ARCH-017, provisional)
DATUM_TOL_M = 0.05           # V-06
YAW_TOL_DEG = 1.0            # V-06 (provisional; same value as tilt)
TILT_TOL_DEG = 1.0           # V-06
DOOR_MUST_CONNECT_M = 0.80   # V-10
LOW_CONFIDENCE = 0.8


def _status(items):
    if any(i == "fail" for i in items):
        return "fail"
    if any(i == "not_established" for i in items):
        return "not_established"
    return "pass"


# ----------------------------------------------------------------------------- completeness (V-01)
def check_completeness(geometry, floor_id, reference):
    if reference is None:
        return {"status": "not_established", "reference_id": None,
                "note": "no independent reference supplied; missing obstacles cannot be detected"}
    if reference.get("frame_id") != geometry.frame_id:
        return {"status": "fail", "reference_id": reference.get("reference_id"),
                "note": f"reference frame {reference.get('frame_id')!r} != geometry frame {geometry.frame_id!r}"}
    obs = [o for o in geometry.obstacles if o.floor_id == floor_id]
    union = unary_union([o.polygon for o in obs]) if obs else Polygon()
    missing, deviating, matched_ids = [], [], set()
    for ref in reference.get("obstacles", []):
        if not ref.get("major", True):
            continue
        rp = Polygon(ref["footprint"])
        coverage = rp.intersection(union).area / rp.area if rp.area > 0 else 0.0
        if coverage < COVERAGE_MIN:
            missing.append({"ref_id": ref["ref_id"], "kind": ref.get("kind"), "coverage": round(coverage, 3)})
            continue
        best = max(obs, key=lambda o: o.polygon.intersection(rp).area)
        matched_ids.add(best.obstacle_id)
        dev = best.polygon.hausdorff_distance(rp)
        if dev > ref.get("tolerance_m", 0.10):
            deviating.append({"ref_id": ref["ref_id"], "obstacle_id": best.obstacle_id,
                              "hausdorff_m": round(dev, 3), "tolerance_m": ref.get("tolerance_m", 0.10)})
    extra = sorted(o.obstacle_id for o in obs if o.obstacle_id not in matched_ids
                   and not any(Polygon(r["footprint"]).intersects(o.polygon) for r in reference.get("obstacles", [])))
    door_ids = {d.doorway_id for d in geometry.doorways}
    missing_doors = [d["ref_id"] for d in reference.get("doorways", []) if d.get("required")
                     and d["ref_id"] not in door_ids]
    status = "fail" if (missing or deviating or missing_doors) else "pass"
    return {"status": status, "reference_id": reference.get("reference_id"), "reference_source": reference.get("source"),
            "missing_obstacles": missing, "deviating_obstacles": deviating, "missing_doorways": missing_doors,
            "unreferenced_obstacles": extra}


# ----------------------------------------------------------------------------- alignment (V-05, V-06)
def check_alignment(alignment):
    if alignment is None:
        return {"status": "not_established", "note": "no tape / datum observations supplied"}
    marks = {m["mark_id"]: m for m in alignment["marks"]}
    dist_rows, results = [], []
    for d in alignment.get("distances", []):
        a, b = marks[d["mark_a"]]["observed_site_m"], marks[d["mark_b"]]["observed_site_m"]
        model = math.dist(a, b)
        err = abs(model - d["tape_m"])
        tol = max(SCALE_TOL_ABS, SCALE_TOL_REL * d["tape_m"])
        ok = err <= tol
        dist_rows.append({"id": d["id"], "tape_m": d["tape_m"], "model_m": round(model, 4),
                          "error_m": round(err, 4), "tolerance_m": round(tol, 4), "ok": ok})
        results.append("pass" if ok else "fail")
    v05 = _status(results) if results else "not_established"

    v06_items, v06 = {}, []
    datum = marks.get(alignment.get("datum_mark"))
    if datum:
        off = math.dist(datum["observed_site_m"], datum["expected_site_m"])
        v06_items["datum_offset_m"] = round(off, 4)
        v06.append("pass" if off <= DATUM_TOL_M else "fail")
    xm = marks.get(alignment.get("x_axis_mark"))
    if datum and xm:
        e = np.subtract(xm["expected_site_m"], datum["expected_site_m"])
        o = np.subtract(xm["observed_site_m"], datum["observed_site_m"])
        yaw = math.degrees(math.atan2(o[2], o[0]) - math.atan2(e[2], e[0]))
        yaw = (yaw + 180) % 360 - 180
        v06_items["yaw_error_deg"] = round(abs(yaw), 4)
        v06.append("pass" if abs(yaw) <= YAW_TOL_DEG else "fail")
    samples = np.asarray(alignment.get("floor_samples_observed_site_m", []), dtype=float)
    if len(samples) >= 3:
        centred = samples - samples.mean(axis=0)
        normal = np.linalg.svd(centred, full_matrices=False)[2][-1]
        tilt = math.degrees(math.acos(min(1.0, abs(normal[1]) / np.linalg.norm(normal))))
        v06_items["floor_tilt_deg"] = round(tilt, 4)
        v06.append("pass" if tilt <= TILT_TOL_DEG else "fail")
    v06_status = _status(v06) if v06 else "not_established"
    return {"status": _status([v05, v06_status]), "reference_id": alignment.get("reference_id"),
            "V-05": {"status": v05, "distances": dist_rows},
            "V-06": {"status": v06_status, **v06_items,
                     "tolerances": {"datum_m": DATUM_TOL_M, "yaw_deg": YAW_TOL_DEG, "tilt_deg": TILT_TOL_DEG}}}


# ----------------------------------------------------------------------------- connectivity (V-03, V-04, V-10)
def _free_component(navlayer, labels, xz, snap_m):
    cell = navlayer.cell_of(xz)
    if cell is not None and navlayer.free[cell]:
        return int(labels[cell])
    cell, _ = navlayer.nearest_free(xz, snap_m)
    return int(labels[cell]) if cell is not None else 0


def check_connectivity(geometry, navlayer, reference=None):
    labels = navlayer.components()
    probe = navlayer.agent_radius_m + navlayer.extra_margin_m + 3 * navlayer.res
    doors, v03, v10 = [], [], []
    for d in geometry.doorways:
        (x0, z0), (x1, z1) = d.segment.coords
        mx, mz = (x0 + x1) / 2, (z0 + z1) / 2
        nx, nz = -(z1 - z0) / d.segment.length, (x1 - x0) / d.segment.length
        ca = _free_component(navlayer, labels, (mx + nx * probe, mz + nz * probe), 0.3)
        cb = _free_component(navlayer, labels, (mx - nx * probe, mz - nz * probe), 0.3)
        connected = ca != 0 and ca == cb
        width = d.measured_width_m if d.measured_width_m is not None else d.segment.length
        must = width >= DOOR_MUST_CONNECT_M
        doors.append({"doorway_id": d.doorway_id, "width_m": width, "connected": connected,
                      "must_connect": must,
                      "note": None if must else f"narrow (< {DOOR_MUST_CONNECT_M} m): reported, not required"})
        v03.append("pass" if connected or not must else "fail")
        v10.append("pass" if connected or not must else "fail")
    comps = np.bincount(labels.ravel())[1:]
    out = {"components": int(len(comps)),
           "component_cells": sorted((int(c) for c in comps), reverse=True)[:10],
           "doorways": doors}
    if reference is None:
        out.update({"V-03": _status(v03) if v03 else "not_established", "V-04": "not_established",
                    "V-10": _status(v10) if v10 else "not_established",
                    "note": "required regions / walk pairs need an independent reference"})
    else:
        req = {p["id"]: _free_component(navlayer, labels, p["xz_m"], 0.5) for p in reference.get("required_points", [])}
        nonzero = {v for v in req.values() if v}
        v04 = "pass" if req and all(req.values()) and len(nonzero) == 1 else "fail"
        pairs = []
        for p in reference.get("walk_pairs", []):
            a = _free_component(navlayer, labels, p["start"], 0.0)
            b = _free_component(navlayer, labels, p["goal"], 1.0)
            ok = a != 0 and a == b
            pairs.append({"id": p["id"], "connected": ok})
            v10.append("pass" if ok else "fail")
        out.update({"V-03": _status(v03) if v03 else "not_established", "V-04": v04,
                    "required_points": req, "walk_pairs": pairs, "V-10": _status(v10) if v10 else "not_established"})
    out["status"] = _status([out["V-03"], out["V-04"], out["V-10"]])
    return out


# ----------------------------------------------------------------------------- human inspection
def inspection_items(geometry, completeness):
    """One entry per feature; reasons from every rule are merged."""
    items = []
    for o in geometry.obstacles:
        reasons = []
        if o.kind in ("glass", "unknown_risk"):
            reasons.append(f"kind {o.kind}")
        if o.confidence < LOW_CONFIDENCE:
            reasons.append(f"confidence {o.confidence:.2f}")
        if (o.provenance or {}).get("origin") == "human_corrected":
            reasons.append("human_corrected")
        if reasons:
            items.append({"feature_id": o.obstacle_id, "reasons": reasons})
    for oid in completeness.get("unreferenced_obstacles", []):
        items.append({"feature_id": oid, "reasons": ["not in the reference inventory (possible phantom)"]})
    for d in geometry.doorways:
        if d.measured_width_m is None:
            items.append({"feature_id": d.doorway_id, "reasons": ["doorway width not tape-measured"]})
    merged = {}
    for it in items:
        merged.setdefault(it["feature_id"], []).extend(r for r in it["reasons"]
                                                       if r not in merged.get(it["feature_id"], []))
    return [{"feature_id": k, "reasons": v} for k, v in merged.items()]


# ----------------------------------------------------------------------------- report
def run_geometry_checks(source, *, floor_id="F0", reference=None, alignment=None, navlayer=None,
                        acceptance_record=None, checked_at=None):
    geometry, validity_issues = load_geometry(source)
    report = {"report_type": "geometry_check_v1", "checked_at": checked_at or timefmt.now_iso(),
              "validity": {"status": "fail" if has_errors(validity_issues) else "pass",
                           "issues": as_dicts(validity_issues)}}
    if geometry is None:
        report.update(report_id="gc-invalid", geometry_id=(source.get("geometry_id") if isinstance(source, dict)
                                                          else None), geometry_sha256=None,
                      acceptance={"status": "not_accepted", "limitations": [], "blocking": ["validity"]})
        return report, None
    report["report_id"] = f"gc-{geometry.geometry_id}-{geometry.sha256[:8]}"
    report["geometry_id"] = geometry.geometry_id
    report["geometry_sha256"] = geometry.sha256
    completeness = check_completeness(geometry, floor_id, reference)
    alignment_r = check_alignment(alignment)
    conn = check_connectivity(geometry, navlayer, reference) if navlayer is not None else \
        {"status": "not_established", "note": "no navigation layer supplied"}
    v02 = "not_established"
    if navlayer is not None:
        thin = thinnest_obstacle_m(geometry, floor_id)
        v02 = "pass" if navlayer.res <= thin + 1e-9 else "fail"
    insp = inspection_items(geometry, completeness)
    report.update({
        "completeness": completeness, "alignment": alignment_r, "connectivity": conn,
        "human_inspection": {"required": insp},
        "checks": {"V-01": completeness["status"], "V-02": v02,
                   "V-03": conn.get("V-03", "not_established"), "V-04": conn.get("V-04", "not_established"),
                   "V-05": alignment_r.get("V-05", {}).get("status", "not_established"),
                   "V-06": alignment_r.get("V-06", {}).get("status", "not_established"),
                   "V-07": "per POI (poi/endpoints.py, QG-09)", "V-08": "per route (IF-08)",
                   "V-09": "per route (IF-08)", "V-10": conn.get("V-10", "not_established")}})
    blocking, open_items = [], []
    for name, st in (("V-01 completeness", completeness["status"]), ("V-02 thin walls", v02),
                     ("alignment", alignment_r["status"]), ("connectivity", conn["status"])):
        if st == "fail":
            blocking.append(name)
        elif st == "not_established":
            open_items.append(f"{name}: not established")
    open_items += [f"inspect {i['feature_id']}: {', '.join(i['reasons'])}" for i in insp]
    if blocking:
        acc = {"status": "not_accepted", "blocking": blocking, "limitations": open_items}
    elif not open_items:
        acc = {"status": "accepted", "blocking": [], "limitations": []}
    elif acceptance_record and acceptance_record.get("role") == "LEAD" and \
            set(open_items) <= set(acceptance_record.get("accepted_limitations", [])):
        acc = {"status": "accepted_with_limitations", "blocking": [], "limitations": open_items,
               "accepted_by_role": "LEAD", "accepted_at": acceptance_record.get("at")}
    else:
        acc = {"status": "not_accepted", "blocking": [],
               "limitations": open_items, "note": "open items need a LEAD acceptance record naming each one"}
    report["acceptance"] = acc
    return report, geometry
