"""Route <-> validation-report binding and the valid-display rule (ARCH-021).

Canonical polyline string (identical in web/js/routeGate.js):
    each vertex -> f"{x:.6f},{z:.6f}" ; vertices joined by ";"
with every coordinate first quantised to 6 decimals and -0.0 normalised to 0.0.
Planner output is already quantised (navigation/planner.py), so formatting
cannot hit a rounding tie and Python / JavaScript produce the same string.
"""
import math

from namma.core.jsonio import sha256_bytes

QUANTUM_DECIMALS = 6


def quantize(v):
    return round(float(v), QUANTUM_DECIMALS) + 0.0


def is_quantized(v):
    return math.isfinite(v) and abs(v - round(v, QUANTUM_DECIMALS)) <= 1e-9


def canonical_polyline(polyline):
    return ";".join(f"{quantize(x):.6f},{quantize(z):.6f}" for x, z in polyline)


def route_sha256(polyline):
    return sha256_bytes(canonical_polyline(polyline).encode("ascii"))


def display_decision(route, report, accepted_geometry_id=None):
    """Decide whether a route may be DRAWN AS VALID.

    Returns (state, reasons) with state in:
      "valid"            - route + matching, bound, passing report
      "rejected"         - matching report says fail
      "unvalidated"      - no report, or report not bound (1.0.0) / verdict missing
      "binding_mismatch" - report exists but belongs to another route / request,
                           or checked a geometry other than the accepted one
      "no_route"         - IF-07 status is not "route"
    The report may have checked a geometry other than the planning geometry
    (e.g. a reference geometry, M-07); what matters for display is that it
    checked the release's ACCEPTED geometry (`accepted_geometry_id`).
    Never trusts `display_as_valid` on its own.
    """
    reasons = []
    if route.get("status") != "route":
        return "no_route", [f"route status is {route.get('status')!r}"]
    if report is None:
        return "unvalidated", ["no IF-08 report supplied"]
    link = route.get("validation") or {}
    if link.get("report_id") != report.get("report_id"):
        reasons.append("IF-07 validation.report_id does not match the IF-08 report_id")
    if report.get("request_id") != route.get("request_id"):
        reasons.append("IF-08 request_id does not match IF-07 request_id")
    if accepted_geometry_id is not None and report.get("geometry_id") != accepted_geometry_id:
        reasons.append(f"IF-08 checked geometry {report.get('geometry_id')!r}, not the accepted "
                       f"geometry {accepted_geometry_id!r}")
    evidence = report.get("evidence")
    if evidence is None:
        return ("binding_mismatch" if reasons else "unvalidated",
                reasons + ["IF-08 has no evidence.route_sha256 (1.0.0 report): cannot prove which route was checked"])
    if evidence.get("route_sha256") != route_sha256(route.get("polyline_xz_m", [])):
        reasons.append("IF-08 evidence.route_sha256 does not match the IF-07 polyline")
    if reasons:
        return "binding_mismatch", reasons
    if report.get("verdict") != "pass":
        return "rejected", ["independent validation verdict is fail"]
    if link.get("verdict") != "pass" or route.get("display_as_valid") is not True:
        return "unvalidated", ["IF-07 does not record the passing verdict / display_as_valid"]
    return "valid", []
