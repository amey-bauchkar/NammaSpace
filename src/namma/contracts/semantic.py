"""Semantic (cross-field and artifact-level) contract checks - beyond JSON Schema.

`validate_document(doc, **context)` = schema + semantic checks. Context-free
rules always run; rules needing files or other artifacts run only when the
context is supplied (base_dir, verify_files, related artifacts).

Error codes are stable; tests assert on them.
"""
import math
from pathlib import Path, PurePosixPath

from namma.contracts.binding import display_decision, is_quantized
from namma.contracts.schemas import schema_issues
from namma.core import frames, timefmt
from namma.core.issues import error, has_errors, warning
from namma.core.jsonio import non_finite_paths, sha256_file
from namma.geometry.model import load_geometry

POLICY_MAX_GOAL_SNAP_M = 1.0   # ARCH-028 (provisional)
MAX_NAV_RESOLUTION_M = 0.05    # ARCH-004
LENGTH_TOL_M = 0.01
REPRESENTATION_FORMATS = {
    "gaussian_splat": {"ply", "spz", "sog", "splat", "ksplat"},
    "textured_mesh": {"glb", "gltf"},
    "point_cloud": {"ply"},
}


def _safe_relpath(p):
    """Relative POSIX path that cannot escape its base directory."""
    if not isinstance(p, str) or not p or "\\" in p:
        return False
    pp = PurePosixPath(p)
    return not pp.is_absolute() and ".." not in pp.parts and ":" not in pp.parts[0]


def file_issues(entries, base_dir, path_prefix):
    """Existence, non-zero size, byte count and SHA-256 of listed files."""
    out = []
    for i, f in enumerate(entries):
        p = f"{path_prefix}[{i}]"
        rel = f.get("path")
        if not _safe_relpath(rel):
            out.append(error("FILE-UNSAFE-PATH", f"path {rel!r} must be relative POSIX without '..'", p))
            continue
        full = Path(base_dir) / rel
        if not full.is_file():
            out.append(error("FILE-MISSING", f"file {rel!r} does not exist", p))
            continue
        size = full.stat().st_size
        if size == 0:
            out.append(error("FILE-EMPTY", f"file {rel!r} is empty", p))
            continue
        if "bytes" in f and f["bytes"] != size:
            out.append(error("FILE-SIZE-MISMATCH", f"{rel!r}: manifest {f['bytes']} B, actual {size} B", p))
        if "sha256" in f:
            actual = sha256_file(full)
            if actual != f["sha256"]:
                out.append(error("FILE-HASH-MISMATCH", f"{rel!r}: manifest sha256 {f['sha256'][:12]}..., "
                                                       f"actual {actual[:12]}...", p))
    return out


def _ts_order(doc, a, b, code):
    if a in doc and b in doc and timefmt.is_rfc3339(doc[a]) and timefmt.is_rfc3339(doc[b]):
        if timefmt.parse(doc[b]) < timefmt.parse(doc[a]):
            return [error(code, f"{b} is earlier than {a}", f"$.{b}")]
    return []


# --------------------------------------------------------------------------- IF-01
def check_if01(doc, base_dir=None, verify_files=False, **_):
    out = []
    regions = {r["region_id"]: r for r in doc.get("regions", [])}
    if len(regions) != len(doc.get("regions", [])):
        out.append(error("IF01-DUPLICATE-REGION", "duplicate region_id"))
    files_per_region = {}
    seen = set()
    for i, f in enumerate(doc.get("files", [])):
        if f["path"] in seen:
            out.append(error("IF01-DUPLICATE-FILE", f"duplicate path {f['path']!r}", f"$.files[{i}]"))
        seen.add(f["path"])
        if f["region_id"] not in regions:
            out.append(error("IF01-UNKNOWN-REGION", f"file {f['path']!r} names unknown region "
                                                    f"{f['region_id']!r}", f"$.files[{i}]"))
        files_per_region.setdefault(f["region_id"], []).append(f)
        if not _safe_relpath(f["path"]):
            out.append(error("FILE-UNSAFE-PATH", f"unsafe path {f['path']!r}", f"$.files[{i}]"))
    for rid, r in regions.items():
        n = len(files_per_region.get(rid, []))
        if r["status"] == "captured" and n == 0:
            out.append(error("IF01-CAPTURED-WITHOUT-FILES",
                             f"region {rid!r} is marked captured but has no files"))
        if r["status"] == "partial" and n == 0:
            out.append(warning("IF01-PARTIAL-WITHOUT-FILES", f"region {rid!r} is partial but lists no files"))
        if r["status"] in ("skipped", "restricted") and n:
            out.append(warning("IF01-FILES-IN-SKIPPED-REGION", f"region {rid!r} is {r['status']} but has files"))
        for c in r.get("connections", []):
            if c not in regions:
                out.append(error("IF01-UNKNOWN-CONNECTION", f"region {rid!r} connects to unknown {c!r}"))
    ids = [m["id"] for m in doc.get("reference_measurements", [])]
    if len(ids) != len(set(ids)):
        out.append(error("IF01-DUPLICATE-MEASUREMENT", "duplicate reference measurement id"))
    if verify_files:
        out += file_issues(doc.get("files", []), base_dir, "$.files")
    return out


# --------------------------------------------------------------------------- IF-02
def check_if02(doc, base_dir=None, verify_files=False, **_):
    out = _ts_order(doc, "started_at", "ended_at", "IF02-TIME-ORDER")
    st = doc.get("status")
    if st in ("failed", "timed_out") and not doc.get("error"):
        out.append(error("IF02-FAILURE-WITHOUT-ERROR", f"status {st!r} requires an error message"))
    if st == "succeeded" and doc.get("error"):
        out.append(error("IF02-SUCCESS-WITH-ERROR", "succeeded job must not carry an error"))
    if st in ("succeeded", "failed", "timed_out") and "ended_at" not in doc:
        out.append(warning("IF02-NO-END-TIME", f"{st} job has no ended_at"))
    if "started_at" in doc and "ended_at" in doc and st == "succeeded":
        dur = (timefmt.parse(doc["ended_at"]) - timefmt.parse(doc["started_at"])).total_seconds()
        if dur > doc.get("timebox_s", math.inf):
            out.append(error("IF02-TIMEBOX-EXCEEDED", f"job ran {dur:.0f} s > timebox {doc['timebox_s']} s "
                                                      "but is marked succeeded"))
    if verify_files and st == "succeeded":
        for i, rel in enumerate(doc.get("outputs", [])):
            if not _safe_relpath(rel) or not (Path(base_dir) / rel).is_file():
                out.append(error("IF02-OUTPUT-MISSING", f"output {rel!r} does not exist", f"$.outputs[{i}]"))
    return out


# --------------------------------------------------------------------------- IF-03
def check_if03(doc, base_dir=None, verify_files=False, **_):
    out = [error("TRANSFORM-INVALID", m, "$.to_site") for m in frames.check_transform(doc["to_site"])]
    out += [error("FRAME-INVALID", m, "$.site_frame") for m in frames.check_site_frame(doc["site_frame"])]
    rep = doc["representation"]
    for i, a in enumerate(doc["assets"]):
        if a["format"] not in REPRESENTATION_FORMATS[rep]:
            out.append(error("IF03-FORMAT-MISMATCH", f"format {a['format']!r} is not a {rep} format",
                             f"$.assets[{i}]"))
        if a.get("gaussian_count") is not None and rep != "gaussian_splat":
            out.append(warning("IF03-GAUSSIANS-ON-NON-SPLAT", "gaussian_count given for a non-splat asset"))
        ext = PurePosixPath(a["path"]).suffix.lower().lstrip(".")
        if ext and ext != a["format"]:
            out.append(error("IF03-EXTENSION-MISMATCH", f"path extension .{ext} != format {a['format']}",
                             f"$.assets[{i}]"))
    b = doc.get("bounds_site")
    if b and any(lo > hi for lo, hi in zip(b["min"], b["max"])):
        out.append(error("IF03-BOUNDS", "bounds_site.min exceeds bounds_site.max", "$.bounds_site"))
    origin = doc["provenance"]["origin"]
    if origin in ("external", "synthetic_fixture") and doc.get("is_test_asset") is not True:
        out.append(error("IF03-TEST-ASSET-FLAG", f"provenance {origin!r} requires is_test_asset: true"))
    if "is_test_asset" not in doc:
        out.append(warning("IF03-TEST-ASSET-UNSET", "is_test_asset not stated"))
    if verify_files:
        out += file_issues(doc["assets"], base_dir, "$.assets")
    return out


# --------------------------------------------------------------------------- IF-04
def check_if04(doc, **_):
    _, issues = load_geometry(doc)
    return issues


# --------------------------------------------------------------------------- IF-05
def decode_rle_issues(doc):
    out = []
    w, h, rows = doc["width"], doc["height"], doc["occupancy"]
    if len(rows) != h:
        out.append(error("IF05-ROW-COUNT", f"{len(rows)} rows but height is {h}", "$.occupancy"))
    for r, runs in enumerate(rows):
        if sum(runs) != w:
            out.append(error("IF05-RLE-ROW-SUM", f"row {r} decodes to {sum(runs)} cells, width is {w}",
                             f"$.occupancy[{r}]"))
        if any(v == 0 for v in runs[1:]):
            out.append(error("IF05-RLE-NONCANONICAL", f"row {r} has a zero-length run after the first",
                             f"$.occupancy[{r}]"))
        if not runs:
            out.append(error("IF05-RLE-EMPTY-ROW", f"row {r} is empty", f"$.occupancy[{r}]"))
    return out


def check_if05(doc, geometry=None, **_):
    out = decode_rle_issues(doc)
    res = doc["resolution_m"]
    if res > MAX_NAV_RESOLUTION_M + 1e-12:
        out.append(error("IF05-RESOLUTION", f"resolution {res} m exceeds the ARCH-004 limit "
                                            f"{MAX_NAV_RESOLUTION_M} m"))
    lo, hi = doc["body_band_m"]
    if not 0 <= lo < hi:
        out.append(error("IF05-BODY-BAND", f"body band [{lo}, {hi}] is not an increasing non-negative range"))
    if geometry is not None:
        from namma.geometry.model import thinnest_obstacle_m
        if doc["geometry_id"] != geometry.geometry_id:
            out.append(error("IF05-GEOMETRY-ID", "navlayer geometry_id differs from the supplied geometry"))
        if doc.get("geometry_sha256") and geometry.sha256 and doc["geometry_sha256"] != geometry.sha256:
            out.append(error("IF05-GEOMETRY-HASH", "navlayer geometry_sha256 differs from the geometry file"))
        thin = thinnest_obstacle_m(geometry, doc["floor_id"])
        if res > thin + 1e-9:
            out.append(error("IF05-RESOLUTION-THIN", f"resolution {res} m > thinnest obstacle {thin:.3f} m "
                                                     "(ARCH-004)"))
    return out


# --------------------------------------------------------------------------- IF-06
def check_if06(doc, **_):
    out = []
    snap = doc.get("max_goal_snap_m", 1.0)
    if snap > POLICY_MAX_GOAL_SNAP_M:
        out.append(error("IF06-SNAP-ABOVE-POLICY", f"max_goal_snap_m {snap} exceeds policy cap "
                                                   f"{POLICY_MAX_GOAL_SNAP_M} m (ARCH-028)"))
    return out


# --------------------------------------------------------------------------- IF-07
def check_if07(doc, request=None, report=None, accepted_geometry_id=None, **_):
    out = []
    st = doc["status"]
    if doc.get("display_as_valid") is True and st != "route":
        out.append(error("IF07-DISPLAY-WITHOUT-ROUTE", f"display_as_valid true with status {st!r}"))
    if st == "route":
        pts = doc["polyline_xz_m"]
        length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        if abs(length - doc["length_m"]) > LENGTH_TOL_M:
            out.append(error("IF07-LENGTH-MISMATCH", f"length_m {doc['length_m']} but polyline is {length:.3f} m"))
        if math.dist(pts[-1], doc["goal_used_xz_m"]) > 1e-6:
            out.append(error("IF07-GOAL-NOT-ENDPOINT", "polyline does not end at goal_used_xz_m"))
        if not all(is_quantized(v) for p in pts for v in p):
            out.append(warning("IF07-NOT-QUANTIZED", "polyline not quantised to 1e-6 m; hash may differ across "
                                                     "implementations"))
        if doc["goal_snap_m"] > POLICY_MAX_GOAL_SNAP_M + 1e-9:
            out.append(error("IF07-SNAP-ABOVE-POLICY", f"goal_snap_m {doc['goal_snap_m']} > policy cap"))
    elif doc.get("validation", {}).get("verdict") == "pass":
        out.append(error("IF07-VERDICT-WITHOUT-ROUTE", f"validation verdict pass with status {st!r}"))
    if request is not None:
        if request.get("request_id") != doc["request_id"]:
            out.append(error("IF07-REQUEST-MISMATCH", "request_id differs from the IF-06 request"))
        if doc.get("navlayer_id") and request.get("navlayer_id") != doc["navlayer_id"]:
            out.append(error("IF07-NAVLAYER-MISMATCH", "navlayer_id differs from the IF-06 request"))
        if st == "route":
            if math.dist(doc["polyline_xz_m"][0], request["start_xz_m"]) > 1e-6:
                out.append(error("IF07-START-MISMATCH", "polyline does not start at the requested start"))
            if doc["goal_snap_m"] > request.get("max_goal_snap_m", 1.0) + 1e-9:
                out.append(error("IF07-SNAP-ABOVE-REQUEST", "goal_snap_m exceeds the request's max_goal_snap_m"))
    if doc.get("display_as_valid") is True and report is None:
        out.append(warning("IF07-DISPLAY-UNCONFIRMED", "display_as_valid true; confirm with the IF-08 report "
                                                       "(consumers must run binding.display_decision)"))
    elif doc.get("display_as_valid") is True:
        state, reasons = display_decision(doc, report, accepted_geometry_id)
        if state != "valid":
            out.append(error("IF07-DISPLAY-NOT-AUTHORISED", f"display_as_valid true but decision is {state}: "
                                                            + "; ".join(reasons)))
    return out


# --------------------------------------------------------------------------- IF-08
def check_if08(doc, route=None, accepted_geometry_id=None, **_):
    out = []
    c = doc["checks"]
    if c.get("clearance_ok") and "evidence" in doc:
        need = doc["evidence"]["agent_radius_m"] - doc["evidence"]["clearance_tolerance_m"]
        if c["min_clearance_m"] < need - 1e-9:
            out.append(error("IF08-CLEARANCE-INCONSISTENT",
                             f"clearance_ok true but min_clearance_m {c['min_clearance_m']} < {need:.4f}"))
    if c.get("intersects_obstacle") and c.get("min_clearance_m", 0) > 0:
        out.append(error("IF08-INTERSECTION-INCONSISTENT", "intersects_obstacle true but min_clearance_m > 0"))
    if "evidence" not in doc:
        out.append(warning("IF08-UNBOUND", "1.0.0 report without evidence: it cannot authorise valid display "
                                           "(ARCH-021)"))
    if route is not None:
        state, reasons = display_decision(route, doc, accepted_geometry_id)
        if state == "binding_mismatch":
            out.append(error("IF08-BINDING-MISMATCH", "; ".join(reasons)))
    return out


# --------------------------------------------------------------------------- IF-09
def check_if09(doc, site_frame_id=None, current_geometry_id=None, current_navlayer_id=None, **_):
    out = []
    ver = doc["verification"]
    if ver["status"] == "verified" and not (ver.get("by_role") and ver.get("at")):
        out.append(error("IF09-VERIFIED-WITHOUT-REVIEWER", "verified POI must record by_role and at"))
    if doc.get("endpoint_reachable") is True and doc.get("route_endpoint_xz_m") is None:
        out.append(error("IF09-REACHABLE-WITHOUT-ENDPOINT", "endpoint_reachable true but no route endpoint"))
    ep = doc.get("route_endpoint_xz_m")
    if ep is not None:
        snap = math.dist(ep, (doc["marker_site_m"][0], doc["marker_site_m"][2]))
        if snap > POLICY_MAX_GOAL_SNAP_M + 1e-6:
            out.append(error("IF09-ENDPOINT-TOO-FAR", f"endpoint {snap:.3f} m from marker > policy cap"))
        if "endpoint_snap_m" in doc and abs(doc["endpoint_snap_m"] - snap) > 1e-3:
            out.append(error("IF09-SNAP-INCONSISTENT", "endpoint_snap_m does not match marker-endpoint distance"))
    if site_frame_id is not None and doc["frame_id"] != site_frame_id:
        out.append(error("IF09-FRAME", f"frame_id {doc['frame_id']!r} != site frame {site_frame_id!r}"))
    if current_geometry_id is not None and ep is not None:
        if doc.get("endpoint_geometry_id") != current_geometry_id or (
                current_navlayer_id is not None and doc.get("endpoint_navlayer_id") != current_navlayer_id):
            out.append(error("IF09-ENDPOINT-STALE", "route endpoint was not computed against the current "
                                                    "geometry / navigation layer; recompute it"))
    if doc["label_source"] == "automatic_suggestion" and ver["status"] == "verified" and \
            doc["provenance"]["origin"] == "automatic":
        out.append(warning("IF09-AUTO-LABEL", "automatic label: confirm the human verification step"))
    return out


# --------------------------------------------------------------------------- IF-10
def check_if10(doc, base_dir=None, verify_files=False, **_):
    out = []
    n = len(doc["routes_precomputed"])
    for key in ("route_requests", "route_reports"):
        if key in doc and len(doc[key]) != n:
            out.append(error("IF10-ROUTE-LISTS", f"{key} has {len(doc[key])} entries, routes_precomputed {n}"))
    if n and "route_reports" not in doc:
        out.append(warning("IF10-NO-REPORTS", "routes without IF-08 report paths cannot be drawn as valid"))
    referenced = [doc["scene"], doc["pois"], *doc["navlayers"], *doc["routes_precomputed"],
                  *doc.get("route_requests", []), *(r for r in doc.get("route_reports", []) if r is not None)]
    if doc.get("geometry"):
        referenced.append(doc["geometry"])
    for rel in referenced:
        if not _safe_relpath(rel):
            out.append(error("FILE-UNSAFE-PATH", f"unsafe path {rel!r}"))
    listed = {f["path"] for f in doc.get("files", [])}
    if "files" in doc:
        for rel in referenced:
            if rel not in listed:
                out.append(error("IF10-UNLISTED-FILE", f"{rel!r} is referenced but not in files[]"))
    if verify_files:
        out += file_issues(doc.get("files", []), base_dir, "$.files")
        for rel in referenced:
            if _safe_relpath(rel) and not (Path(base_dir) / rel).is_file():
                out.append(error("FILE-MISSING", f"referenced file {rel!r} does not exist"))
    return out


# --------------------------------------------------------------------------- IF-11
def check_if11(doc, base_dir=None, verify_files=False, **_):
    out = []
    reg = doc["regression"]
    if not reg["passed"] and doc.get("deploy_targets"):
        out.append(error("IF11-FAILED-REGRESSION-DEPLOY", "regression failed: deploy_targets must be empty"))
    if reg["passed"] and reg.get("failures"):
        out.append(error("IF11-PASSED-WITH-FAILURES", "regression.passed true but failures listed"))
    paths = [a["path"] for a in doc["artifacts"]]
    if len(paths) != len(set(paths)):
        out.append(error("IF11-DUPLICATE-ARTIFACT", "duplicate artifact path"))
    if doc["contract_version"].startswith("2.") and verify_files:
        from namma.pipeline.release import tree_sha256
        if tree_sha256(doc["artifacts"]) != doc["source"]["tree_sha256"]:
            out.append(error("IF11-TREE-HASH", "source.tree_sha256 does not match the artifact list"))
    if verify_files:
        out += file_issues(doc["artifacts"], base_dir, "$.artifacts")
    return out


# --------------------------------------------------------------------------- IF-12
def check_if12(doc, **_):
    out = []
    if doc["status"] == "failed" and not (doc.get("errors") or doc.get("note")):
        out.append(error("IF12-FAILED-WITHOUT-REASON", "failed stage must record errors or a note"))
    if doc.get("gate_result") == "pass" and doc["status"] == "failed":
        out.append(error("IF12-GATE-STATUS-CONFLICT", "gate_result pass with status failed"))
    if doc.get("gate_result") and not doc.get("gate"):
        out.append(error("IF12-GATE-RESULT-WITHOUT-GATE", "gate_result given without a gate id"))
    if doc.get("manual_minutes", 0) > 0 and doc.get("manual_intervention") is False:
        out.append(error("IF12-MANUAL-CONFLICT", "manual_minutes > 0 but manual_intervention false"))
    return out


# --------------------------------------------------------------------------- IF-13 (Phase 13)
def check_if13(doc, base_dir=None, verify_files=False, **_):
    out = []
    referenced = [lod["path"] for lod in doc["appearance"]["lods"]]
    referenced += [doc["cameras"], doc["roam"], doc["pois"], doc["quality"]["report"]]
    if doc["quality"].get("capture_report"):
        referenced.append(doc["quality"]["capture_report"])
    if doc["navigation"].get("geometry"):
        referenced.append(doc["navigation"]["geometry"])
    for rel in referenced:
        if not _safe_relpath(rel):
            out.append(error("FILE-UNSAFE-PATH", f"unsafe path {rel!r}"))
    listed = {f["path"]: f for f in doc["files"]}
    for rel in referenced:
        if rel not in listed:
            out.append(error("IF13-UNLISTED-FILE", f"{rel!r} is referenced but not in files[]"))
    for lod in doc["appearance"]["lods"]:
        f = listed.get(lod["path"])
        if f and (f["sha256"] != lod["sha256"] or f["bytes"] != lod["bytes"]):
            out.append(error("IF13-LOD-MISMATCH", f"{lod['path']}: lods[] and files[] disagree"))
    lods = [lod["lod"] for lod in doc["appearance"]["lods"]]
    if len(lods) != len(set(lods)):
        out.append(error("IF13-DUPLICATE-LOD", "duplicate lod level"))
    m = doc["appearance"]["to_model"]["matrix_row_major"]
    errs = frames.check_transform({"matrix_row_major": m, "source": "sfm_alignment", "method": "-"})
    out += [error("IF13-TO-MODEL", e) for e in errs]
    if not errs and abs(frames.scale_of({"matrix_row_major": m}) - 1.0) > 1e-6:
        out.append(error("IF13-TO-MODEL-NOT-RIGID", "to_model must be rigid: the model frame has no scale of its own"))
    s = doc["scale"]
    nav = doc["navigation"]
    if nav["status"] == "accepted" and nav.get("scale_basis") in (None, "unknown", "assumed_capture_height"):
        out.append(error("IF13-NAV-ACCEPTED-WITHOUT-SCALE", "navigation cannot be accepted on an unknown or assumed scale"))
    if nav["status"] != "unavailable" and not nav.get("geometry"):
        out.append(error("IF13-NAV-WITHOUT-GEOMETRY", f"navigation status {nav['status']} needs a geometry file"))
    if s["status"] == "unknown" and nav.get("scale_basis") not in (None, "unknown", "assumed_capture_height"):
        out.append(error("IF13-SCALE-CONFLICT", "navigation claims a calibrated scale but scale.status is unknown"))
    lo, hi = doc["bounds"]["min"], doc["bounds"]["max"]
    if any(a > b for a, b in zip(lo, hi)):
        out.append(error("IF13-BOUNDS", "bounds.min exceeds bounds.max"))
    if verify_files:
        out += file_issues(doc["files"], base_dir, "$.files")
    return out


CHECKS = {"IF-01": check_if01, "IF-02": check_if02, "IF-03": check_if03, "IF-04": check_if04,
          "IF-05": check_if05, "IF-06": check_if06, "IF-07": check_if07, "IF-08": check_if08,
          "IF-09": check_if09, "IF-10": check_if10, "IF-11": check_if11, "IF-12": check_if12,
          "IF-13": check_if13}


def semantic_issues(doc, **context):
    bad = non_finite_paths(doc)
    if bad:
        return [error("NONFINITE-NUMBER", f"non-finite number at {p}", p) for p in bad]
    return CHECKS[doc["contract"]](doc, **context)


def validate_document(doc, expected=None, **context):
    """Schema first; semantic checks only when the schema passes."""
    issues = schema_issues(doc, expected)
    if has_errors(issues):
        return issues
    return issues + semantic_issues(doc, **context)
