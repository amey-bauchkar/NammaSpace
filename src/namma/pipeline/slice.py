"""Synthetic end-to-end vertical slice (Phase 11).

Runs every Track A stage on the EXP-010 synthetic fixture and writes contract
artifacts plus IF-12 run-log entries. Stages that need real capture are logged
`skipped_with_limitation`; the reconstruction stage uses the FIXTURE adapter
(IF-02 concept "test-fixture") and performs no reconstruction.

Outputs are synthetic and must never be reported as evidence about smartphone
capture, reconstruction, unseen venues, Round 3 timing or splat rendering.
"""
import time
from pathlib import Path

from namma import CONTRACT_VERSION
from namma.contracts.semantic import validate_document
from namma.core import timefmt
from namma.core.issues import errors_only
from namma.core.jsonio import load_strict, write_bytes_atomic, write_json
from namma.fixtures import exp010
from namma.geometry.checks import run_geometry_checks
from namma.geometry.model import load_geometry
from namma.navigation.grid import build_navlayer
from namma.navigation.planner import make_request, plan_route
from namma.navigation.validator import finalize, validate_route
from namma.pipeline import meshgen
from namma.pipeline.runlog import RunLog
from namma.poi.endpoints import compute_endpoint
from namma.poi.search import search
from namma.poi.store import POIStore

RES, RADIUS, MARGIN = 0.05, 0.25, 0.0


class SliceError(RuntimeError):
    pass


def _require(issues, what):
    errs = errors_only(issues)
    if errs:
        raise SliceError(f"{what}: " + "; ".join(f"{e.code}: {e.message}" for e in errs))


def run_synthetic_slice(runs_dir, run_id=None, project_root=None):
    run_id = run_id or "run-synthetic-" + time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path(runs_dir) / run_id
    if run_dir.exists():
        raise SliceError(f"{run_dir} exists; run folders are never overwritten")
    run_dir.mkdir(parents=True)
    log = RunLog(run_dir, run_id)
    summary = {"run_id": run_id, "run_dir": str(run_dir), "synthetic": True, "stages": {}}

    for st, gate, note in (
            ("S0", "QG-00", "synthetic run: no venue and no team readiness check"),
            ("S1", "QG-01", "no reconnaissance: layout is the EXP-010 synthetic fixture"),
            ("S2", "QG-02", "no smartphone capture (real capture BLOCKED: Q-T-06)"),
            ("S3", "QG-03", "no capture gate: no capture exists")):
        log.append(st, "skipped_with_limitation", f"{st}_not_run", gate=gate, gate_result="not_evaluated", note=note)

    # ------------------------------------------------------------------ S4: fixture adapter (no reconstruction)
    s4 = run_dir / "04_reconstruction"
    started = timefmt.now_iso()
    geom_path = s4 / "geometry" / "geometry.if04.json"
    write_json(geom_path, exp010.geometry(run_id=run_id))
    geometry, gissues = load_geometry(geom_path)
    _require(gissues, "fixture geometry")
    glb, bounds = meshgen.build_glb(geometry)
    write_bytes_atomic(s4 / "scene" / "scene.glb", glb)
    scene = meshgen.scene_metadata("scene-exp010-synthetic", run_id, "scene.glb", glb, geometry, bounds,
                                   exp010.T_FIXTURE)
    write_json(s4 / "scene" / "scene.if03.json", scene)
    _require(validate_document(scene, "IF-03", base_dir=s4 / "scene", verify_files=True), "IF-03")
    job = {"contract": "IF-02", "contract_version": CONTRACT_VERSION, "job_id": f"job-{run_id}-fixture",
           "run_id": run_id, "adapter": {"name": "fixture-adapter", "version": "1.0.0", "concept": "test-fixture"},
           "inputs": ["src/namma/fixtures/exp010.py"], "timebox_s": 60, "status": "succeeded",
           "started_at": started, "ended_at": timefmt.now_iso(),
           "outputs": ["scene/scene.if03.json", "geometry/geometry.if04.json"], "error": None}
    write_json(s4 / "job.if02.json", job)
    _require(validate_document(job, "IF-02", base_dir=s4, verify_files=True), "IF-02")
    log.append("S4", "passed", s4.relative_to(run_dir).as_posix(), gate="QG-04", gate_result="pass",
               note="fixture adapter (concept test-fixture): NO reconstruction performed; outputs are synthetic")

    # ------------------------------------------------------------------ S5: geometry checks
    s5 = run_dir / "05_geometry_checks"
    reference, alignment = exp010.reference_inventory(), exp010.alignment_observations()
    write_json(s5 / "reference_inventory.json", reference)
    write_json(s5 / "alignment_observations.json", alignment)
    navlayer = build_navlayer(geometry, "F0", RES, RADIUS, MARGIN)
    gcheck, _ = run_geometry_checks(geom_path, reference=reference, alignment=alignment, navlayer=navlayer)
    write_json(s5 / "geometry_check.json", gcheck)
    accepted = gcheck["acceptance"]["status"] in ("accepted", "accepted_with_limitations")
    log.append("S5", "passed" if accepted else "failed", s5.relative_to(run_dir).as_posix(),
               input_dirs=["04_reconstruction/geometry"], gate="QG-05", gate_result="pass" if accepted else "fail",
               note=f"acceptance {gcheck['acceptance']['status']}; completeness vs reference "
                    f"{gcheck['completeness']['status']} (reference source: synthetic_truth)",
               errors=None if accepted else gcheck["acceptance"].get("blocking"))
    if not accepted:
        raise SliceError("geometry not accepted; routes would not be valid")

    # ------------------------------------------------------------------ S6 / S7: navigation, POIs, routes
    s6, s7 = run_dir / "06_navigation", run_dir / "07_pois"
    nav_doc = navlayer.to_doc()
    write_json(s6 / "navlayer.if05.json", nav_doc)
    _require(validate_document(nav_doc, "IF-05", geometry=geometry), "IF-05")

    origin = exp010.START_POINTS[0]["xz_m"]
    store = POIStore(s7 / "pois.json", geometry.frame_id)
    store.records = exp010.pois()
    for p in store.records:
        compute_endpoint(p, navlayer, origin)
    store.save()

    routes = []
    pois = store.by_id()
    for sp in exp010.START_POINTS:
        for p in store.records:
            if p["verification"]["status"] != "verified":
                continue
            rid = f"rq-{sp['start_id']}-{p['poi_id']}"
            req = make_request(rid, navlayer, sp["xz_m"], poi_id=p["poi_id"])
            res = plan_route(navlayer, req, pois)
            rep = None
            if res["status"] == "route":
                rep = validate_route(res, req, geometry, floor_id="F0", agent_radius_m=RADIUS, resolution_m=RES,
                                     geometry_check=gcheck, poi=p)
                res = finalize(res, rep)
            routes.append({"request": req, "result": res, "report": rep, "kind": "poi",
                           "start_id": sp["start_id"], "poi_id": p["poi_id"]})

    # Negative demonstration (EXP-010 S8): planned on geometry WITHOUT the table,
    # validated against the ACCEPTED geometry, which contains it -> must fail.
    missing, missing_issues = load_geometry(exp010.missing_table_geometry())
    _require(missing_issues, "missing-table geometry")
    nav_missing = build_navlayer(missing, "F0", RES, RADIUS, MARGIN)
    req = make_request("rq-demo-planned-on-missing-table", nav_missing, (1.5, 2.4), goal_xz=(4.2, 2.4))
    res = plan_route(nav_missing, req)
    rep = validate_route(res, req, geometry, floor_id="F0", agent_radius_m=RADIUS, resolution_m=RES,
                         geometry_check=gcheck)
    rep["evidence"]["limitations"].append(
        "DEMO: route planned on deliberately incomplete geometry (table missing) and checked against the accepted "
        "geometry; it must be rejected")
    res = finalize(res, rep)
    routes.append({"request": req, "result": res, "report": rep, "kind": "negative_demo",
                   "start_id": None, "poi_id": None})

    for r in routes:
        base = s6 / "routes" / r["request"]["request_id"]
        write_json(base.with_suffix(".request.json"), r["request"])
        write_json(base.with_suffix(".result.json"), r["result"])
        if r["report"] is not None:
            write_json(base.with_suffix(".report.json"), r["report"])
        _require(validate_document(r["result"], "IF-07", report=r["report"], accepted_geometry_id=geometry.geometry_id,
                                   request=r["request"]), f"IF-07 {r['request']['request_id']}")
        if r["report"] is not None:
            _require(validate_document(r["report"], "IF-08", route=r["result"],
                                       accepted_geometry_id=geometry.geometry_id), f"IF-08 {r['request']['request_id']}")

    poi_routes = [r for r in routes if r["kind"] == "poi"]
    reachable = [r for r in poi_routes if pois[r["poi_id"]]["endpoint_reachable"]]
    unreachable = [r for r in poi_routes if not pois[r["poi_id"]]["endpoint_reachable"]]
    qg08 = (all(r["result"]["status"] == "route" and r["result"]["display_as_valid"] for r in reachable)
            and all(r["result"]["status"] != "route" for r in unreachable)
            and routes[-1]["result"]["display_as_valid"] is False and routes[-1]["report"]["verdict"] == "fail")
    log.append("S6", "passed" if qg08 else "failed", s6.relative_to(run_dir).as_posix(),
               input_dirs=["05_geometry_checks", "04_reconstruction/geometry"], gate="QG-08",
               gate_result="pass" if qg08 else "fail",
               note=f"{len(reachable)} validated POI routes; {len(unreachable)} unreachable refused; negative demo "
                    f"rejected: {routes[-1]['report']['verdict'] == 'fail'} (synthetic fixture only)",
               errors=None if qg08 else ["route gate failed"])

    cases = load_strict(Path(project_root or Path(__file__).resolve().parents[3]) / "fixtures" / "search_cases.json")
    failures = []
    for c in cases["cases"]:
        got = search(store.records, c["query"], include_unverified=c.get("include_unverified", False))
        if got["status"] != c["status"] or sorted(h["poi_id"] for h in got["hits"]) != sorted(c["poi_ids"]):
            failures.append(c["query"])
    write_json(s7 / "search_selftest.json", {"cases": len(cases["cases"]), "failures": failures})
    unverified = [p["poi_id"] for p in store.records if p["verification"]["status"] != "verified"]
    log.append("S7", "passed" if not failures else "failed", s7.relative_to(run_dir).as_posix(),
               input_dirs=["06_navigation"], gate="QG-09", gate_result="pass" if not failures else "fail",
               note=f"{len(cases['cases'])} search cases; unverified POIs excluded from search: {unverified}; "
                    "'verified' here = fixture definition, not a site check",
               errors=failures or None)

    summary.update({"geometry": str(geom_path), "scene": str(s4 / "scene" / "scene.if03.json"),
                    "geometry_check": str(s5 / "geometry_check.json"), "navlayer": str(s6 / "navlayer.if05.json"),
                    "pois": str(s7 / "pois.json"), "routes": [r["request"]["request_id"] for r in routes],
                    "qg08": qg08, "search_failures": failures,
                    "acceptance": gcheck["acceptance"]["status"]})
    write_json(run_dir / "slice_summary.json", summary)
    return summary

