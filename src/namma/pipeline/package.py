"""Packager: run folder + web viewer -> static site with an IF-10 manifest (WP-08, P-12).

Every referenced artifact is re-validated (schema, semantics, file hashes,
route/report binding, POI endpoint freshness) BEFORE the manifest is written;
any error aborts packaging. The site is written to a fresh build directory.
"""
import shutil
from pathlib import Path

from namma import CONTRACT_VERSION
from namma.contracts.binding import display_decision
from namma.contracts.semantic import validate_document
from namma.core.issues import errors_only
from namma.core.jsonio import load_strict, sha256_file, write_json
from namma.geometry.model import load_geometry
from namma.navigation.grid import from_doc

WEB_EXCLUDE = {"tests", "data", "node_modules"}
NOTICES = [
    "SYNTHETIC TEST DATA: layout, appearance mesh and POIs are synthetic fixtures (EXP-010). Nothing here was "
    "captured with a smartphone or reconstructed.",
    "Geometry completeness was checked only against the synthetic design reference, not a real venue.",
    "Routes are precomputed for named start points; the static viewer does not plan routes live (ARCH-025).",
    "Gaussian-splat rendering is not selected (ARCH-010 open); no frame-rate claim is made.",
]


class PackageError(RuntimeError):
    pass


def _fail(errs, what):
    if errs:
        raise PackageError(f"{what}: " + "; ".join(f"{e.code}: {e.message}" for e in errs))


def build_site(run_dir, web_dir, out_dir, release_id, start_points, notices=NOTICES):
    run_dir, web_dir, out_dir = Path(run_dir), Path(web_dir), Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    for item in web_dir.iterdir():
        if item.name in WEB_EXCLUDE:
            continue
        if item.is_dir():
            shutil.copytree(item, out_dir / item.name)
        else:
            shutil.copy2(item, out_dir / item.name)
    data = out_dir / "data"
    s4, s5, s6, s7 = (run_dir / d for d in ("04_reconstruction", "05_geometry_checks", "06_navigation", "07_pois"))
    shutil.copytree(s4 / "scene", data / "scene")
    (data / "geometry").mkdir(parents=True)
    shutil.copy2(s4 / "geometry" / "geometry.if04.json", data / "geometry" / "geometry.if04.json")
    shutil.copy2(s5 / "geometry_check.json", data / "geometry" / "geometry_check.json")
    (data / "nav").mkdir()
    shutil.copy2(s6 / "navlayer.if05.json", data / "nav" / "navlayer.if05.json")
    (data / "poi").mkdir()
    shutil.copy2(s7 / "pois.json", data / "poi" / "pois.json")
    shutil.copytree(s6 / "routes", data / "routes")

    # ---------------------------------------------------------------- re-validate everything
    scene = load_strict(data / "scene" / "scene.if03.json")
    _fail(errors_only(validate_document(scene, "IF-03", base_dir=data / "scene", verify_files=True)), "IF-03")
    geometry, gi = load_geometry(data / "geometry" / "geometry.if04.json")
    _fail(errors_only(gi), "IF-04")
    gcheck = load_strict(data / "geometry" / "geometry_check.json")
    if gcheck.get("geometry_sha256") != geometry.sha256:
        raise PackageError("geometry check report does not match the packaged geometry file")
    nav_doc = load_strict(data / "nav" / "navlayer.if05.json")
    _fail(errors_only(validate_document(nav_doc, "IF-05", geometry=geometry)), "IF-05")
    navlayer = from_doc(nav_doc)
    pois = load_strict(data / "poi" / "pois.json")
    for p in pois:
        ctx = {"site_frame_id": geometry.frame_id}
        if p["verification"]["status"] == "verified":
            ctx.update(current_geometry_id=geometry.geometry_id, current_navlayer_id=navlayer.navlayer_id)
        _fail(errors_only(validate_document(p, "IF-09", **ctx)), f"IF-09 {p.get('poi_id')}")

    requests, results, reports = [], [], []
    states = {}
    for res_path in sorted((data / "routes").glob("*.result.json")):
        stem = res_path.name[:-len(".result.json")]
        req_path = res_path.with_name(stem + ".request.json")
        rep_path = res_path.with_name(stem + ".report.json")
        req, res = load_strict(req_path), load_strict(res_path)
        rep = load_strict(rep_path) if rep_path.exists() else None
        _fail(errors_only(validate_document(req, "IF-06")), f"IF-06 {stem}")
        _fail(errors_only(validate_document(res, "IF-07", request=req, report=rep,
                                            accepted_geometry_id=geometry.geometry_id)), f"IF-07 {stem}")
        if rep is not None:
            _fail(errors_only(validate_document(rep, "IF-08", route=res,
                                                accepted_geometry_id=geometry.geometry_id)), f"IF-08 {stem}")
        if res["status"] == "route" and rep is None:
            raise PackageError(f"{stem}: route without an IF-08 report cannot be packaged")
        states[stem] = display_decision(res, rep, geometry.geometry_id)[0]
        requests.append(req_path.relative_to(data).as_posix())
        results.append(res_path.relative_to(data).as_posix())
        reports.append(rep_path.relative_to(data).as_posix() if rep is not None else None)

    files = []
    for p in sorted(data.rglob("*")):
        if p.is_file():
            files.append({"path": p.relative_to(data).as_posix(), "sha256": sha256_file(p),
                          "bytes": p.stat().st_size})
    manifest = {"contract": "IF-10", "contract_version": CONTRACT_VERSION, "release_id": release_id,
                "site_frame": geometry.doc["site_frame"], "scene": "scene/scene.if03.json",
                "geometry": "geometry/geometry.if04.json", "navlayers": ["nav/navlayer.if05.json"],
                "pois": "poi/pois.json", "routes_precomputed": results, "route_requests": requests,
                "route_reports": reports, "renderer_hint": "three.js 0.186.1 (provisional, ARCH-009); "
                                                          "appearance = textured_mesh test asset",
                "files": files, "start_points": start_points, "notices": list(notices)}
    _fail(errors_only(validate_document(manifest, "IF-10", base_dir=data, verify_files=True)), "IF-10")
    write_json(data / "viewer_manifest.json", manifest)
    return {"manifest": manifest, "route_states": states, "out_dir": str(out_dir)}
