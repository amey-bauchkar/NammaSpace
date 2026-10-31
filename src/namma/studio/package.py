"""Build the IF-13 scene package of a run and publish static viewer sites.

Package layout (<run>/90_package/):
  scene_package.json        IF-13 manifest (validated with verify_files before it is written)
  scene/lod<k>.glb          textured meshes, SfM frame; to_model is applied once by the viewer
  views/cameras.json        registered capture viewpoints (model frame)
  nav/roam_grid.json        roaming permission grid (viewing only)
  nav/geometry.if04.json    unverified navigation geometry candidate (when available)
  poi/pois.json             human-authored POIs (copied from the project at packaging time)
  reports/quality_report.json, reports/capture_report.json
"""
import shutil
from pathlib import Path

import numpy as np

from namma import PROJECT_ROOT
from namma.contracts.semantic import validate_document
from namma.core import timefmt
from namma.core.issues import errors_only
from namma.core.jsonio import load_strict, sha256_file, write_json

IF13_VERSION = "1.2.0"         # 1.2.0: scale status "device_tracking" (imported phone-scan meshes)
IF13_VERSION_SPLAT = "1.3.0"   # 1.3.0: optional appearance.splat (photoreal Gaussian splat); written only when present
WEB_FILES = ["viewer.html", "app.css", "favicon.svg", "logo.png", "css", "js", "vendor"]


class PackageError(RuntimeError):
    pass


def build_scene_package(ctx, out):
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    (out / "scene").mkdir(parents=True)
    frame = load_strict(ctx.dir("frame") / "frame.json")
    quality = load_strict(ctx.dir("quality") / "quality_report.json")
    tex = load_strict(ctx.dir("texture") / "texture_summary.json")
    project = load_strict(ctx.project_dir / "project.json") if (ctx.project_dir / "project.json").exists() else {}
    from namma.studio.pipeline import current_scale
    scale = current_scale(ctx)
    lods = []
    for t in tex["lods"]:
        dst = out / "scene" / t["file"]
        shutil.copy2(ctx.dir("texture") / t["file"], dst)
        lods.append({"lod": t["lod"], "path": f"scene/{t['file']}", "format": "glb", "bytes": dst.stat().st_size,
                     "sha256": sha256_file(dst), "faces": t.get("triangles")})
    # photoreal splat (namma.studio.splat): 60_texture/splat.json + the .ply, in the MESH frame's terms
    splat = None
    sj = ctx.dir("texture") / "splat.json"
    if sj.exists():
        sd = load_strict(sj)
        src = ctx.dir("texture") / sd["file"]
        if src.exists():
            dst = out / "scene" / sd["file"]
            shutil.copy2(src, dst)
            Tm = np.asarray(frame["T_model_from_sfm"], float) @ np.asarray(sd["T_mesh_from_splat"], float)
            splat = {"path": f"scene/{sd['file']}", "format": "spz" if sd["file"].endswith(".spz") else "3dgs-ply",
                     "bytes": dst.stat().st_size,
                     "sha256": sha256_file(dst), "splats": int(sd["splats"]), "method": sd["method"],
                     "to_model": {"matrix_row_major": [float(v) for v in Tm.ravel()],
                                  "method": "splat frame -> mesh frame (fitted, see fit) -> model frame (as the mesh)",
                                  "fit": sd.get("fit", {})}}
    (out / "views").mkdir()
    shutil.copy2(ctx.dir("frame") / "cameras.json", out / "views" / "cameras.json")
    (out / "nav").mkdir()
    shutil.copy2(ctx.dir("frame") / "roam_grid.json", out / "nav" / "roam_grid.json")
    navg = ctx.dir("navgeom") / "navgeom.json"
    nav = {"status": "unavailable", "geometry": None, "scale_basis": "unknown", "metres_per_unit": None,
           "note": "No navigation geometry."}
    if navg.exists():
        ng = load_strict(navg)
        if ng["status"] == "unverified":
            shutil.copy2(ctx.dir("navgeom") / "geometry.if04.json", out / "nav" / "geometry.if04.json")
            nav = {"status": "unverified", "geometry": "nav/geometry.if04.json", "scale_basis": ng["scale_basis"],
                   "metres_per_unit": ng["metres_per_unit"],
                   "note": "Automatic floor / obstacle map from the reconstruction. NOT verified: unobserved floor is "
                           "treated as blocked, missing obstacles cannot be detected without a reference, and "
                           + ("the scale is ASSUMED from a typical phone height." if ng["scale_basis"] == "assumed_capture_height"
                              else f"the scale comes from {ng['scale_basis']}.")}
        else:
            nav["note"] = f"Navigation geometry unavailable: {ng.get('reason', 'no walkable floor extracted')}."
    (out / "poi").mkdir()
    pois_src = ctx.project_dir / "pois.json"
    pois = load_strict(pois_src) if pois_src.exists() else []
    pois = [p for p in pois if p.get("run_id") in (None, ctx.run_id)]
    write_json(out / "poi" / "pois.json", [{k: v for k, v in p.items() if k != "run_id"} for p in pois])
    (out / "reports").mkdir()
    shutil.copy2(ctx.dir("quality") / "quality_report.json", out / "reports" / "quality_report.json")
    cap_rep = ctx.project_dir / "capture" / "capture_report.json"
    if cap_rep.exists():
        shutil.copy2(cap_rep, out / "reports" / "capture_report.json")
    notices = list(project.get("notices", []))
    if scale["status"] == "unknown":
        notices.append("Scale unknown: photos alone do not fix real-world size. Distances are in model units.")
    elif scale["status"] == "device_tracking":
        notices.append(f"Scale from the capture device's tracking ({scale['method']}); not checked against a "
                       "measured length - use 'Set scale' in the viewer with one known distance to verify it.")
    elif scale.get("evaluation_only"):
        notices.append(f"Scale from dataset ground truth ({scale['method']}); evaluation use only.")
    if quality["verdict"] != "good":
        notices.append(f"Reconstruction quality: {quality['verdict'].replace('_', ' ')} "
                       f"({sum(1 for w in quality['warnings'] if w['severity'] != 'info')} warnings).")
    fill_src = ctx.dir("frame") / "fill.glb"
    fill_meta_src = ctx.dir("frame") / "fill.json"
    estimated = None
    if fill_src.exists() and fill_meta_src.exists():
        fm = load_strict(fill_meta_src)
        if fm.get("status") == "ok":
            shutil.copy2(fill_src, out / "scene" / "fill.glb")
            f = out / "scene" / "fill.glb"
            estimated = {"path": "scene/fill.glb", "format": "glb", "bytes": f.stat().st_size, "sha256": sha256_file(f),
                         "faces": fm["triangles"], "frame": "model",
                         "method": "flat room planes fitted to the reconstructed points; holes only; colours from the "
                                   "photos (median over views) - ESTIMATED, not scanned",
                         "fit": fm["fit"], "planes": fm["planes"]}
            notices.append("Holes in walls, floor and ceiling are closed with ESTIMATED flat surfaces "
                           "(toggle 'Estimated surfaces'); they are not scanned geometry.")
    files = []
    for p in sorted(out.rglob("*")):
        if p.is_file():
            files.append({"path": p.relative_to(out).as_posix(), "sha256": sha256_file(p), "bytes": p.stat().st_size})
    T = frame["T_model_from_sfm"]
    p1, p99 = frame["bounds_p1_p99"]
    pkg = {"contract": "IF-13", "contract_version": IF13_VERSION_SPLAT if splat else IF13_VERSION,
           "package_id": f"pkg-{ctx.project_dir.name}-{ctx.run_id}",
           "project": {"project_id": ctx.project_dir.name, "title": project.get("title", ctx.project_dir.name)},
           "run_id": ctx.run_id, "created_at": timefmt.now_iso(),
           "model_frame": {"frame_id": f"model:{ctx.run_id}", "units": "model", "handedness": "right", "up_axis": "+Y",
                           "floor_detected": frame["floor_detected"], "up_source": frame["up_source"],
                           "datum": "origin on the floor plane below the centroid of the capture cameras; +X along the "
                                    "principal horizontal axis of the floor; units = reconstruction units"},
           "scale": {k: scale[k] for k in ("status", "metres_per_unit", "relative_uncertainty", "method", "evaluation_only")},
           "appearance": {"representation": "textured_mesh",
                          "to_model": {"matrix_row_major": [float(v) for row in T for v in row],
                                       "method": "rigid SfM -> model frame (floor plane / camera orientation), "
                                                 "see 70_frame/frame.json"},
                          "lods": lods, **({"estimated_surfaces": estimated} if estimated else {}),
                          **({"splat": splat} if splat else {})},
           "eye_height_units": frame["eye_height_units"],
           "bounds": {"min": p1, "max": p99},
           "start_view": {"position": frame["start"]["position"], "target": frame["start"]["target"],
                          "source": frame["start"].get("source") or f"capture camera {frame['start']['camera']}"},
           "cameras": "views/cameras.json", "roam": "nav/roam_grid.json", "navigation": nav,
           "pois": "poi/pois.json",
           "quality": {"verdict": quality["verdict"], "report": "reports/quality_report.json",
                       "capture_report": "reports/capture_report.json" if cap_rep.exists() else None,
                       "warnings": [w["code"] for w in quality["warnings"]]},
           "notices": notices, "files": files}
    errs = errors_only(validate_document(pkg, "IF-13", base_dir=out, verify_files=True))
    if errs:
        raise PackageError("IF-13 package invalid: " + "; ".join(f"{e.code}: {e.message}" for e in errs))
    write_json(out / "scene_package.json", pkg)
    return {"package_id": pkg["package_id"], "files": len(files), "verdict": quality["verdict"],
            "lods": [x["path"] for x in lods], "bytes": sum(f["bytes"] for f in files)}


def publish_static_site(package_dir, out_dir, web_dir=None, max_file_mb=None, include_lods=None):
    """Static, zero-install site: viewer files + package under data/. index.html opens the package.

    include_lods: list of LOD levels to publish (e.g. [1] to stay below a host's per-file limit).
    """
    package_dir, out_dir = Path(package_dir), Path(out_dir)
    web_dir = Path(web_dir or PROJECT_ROOT / "web")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    for name in WEB_FILES:
        src = web_dir / name
        if src.is_dir():
            shutil.copytree(src, out_dir / name, ignore=shutil.ignore_patterns("tests"))
        else:
            shutil.copy2(src, out_dir / name)
    data = out_dir / "data"
    data.mkdir()
    pkg = load_strict(package_dir / "scene_package.json")
    for f in pkg["files"]:                      # only what the package lists (and verifies) is published
        dst = data / f["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(package_dir / f["path"], dst)
    shutil.copy2(package_dir / "scene_package.json", data / "scene_package.json")
    if include_lods is not None:
        keep = [lod for lod in pkg["appearance"]["lods"] if lod["lod"] in include_lods]
        if not keep:
            raise PackageError(f"none of the LOD levels {include_lods} exist in the package")
        drop = {lod["path"] for lod in pkg["appearance"]["lods"]} - {lod["path"] for lod in keep}
        for rel in drop:
            (data / rel).unlink()
        pkg["appearance"]["lods"] = keep
        pkg["files"] = [f for f in pkg["files"] if f["path"] not in drop]
        write_json(data / "scene_package.json", pkg)
    errs = errors_only(validate_document(pkg, "IF-13", base_dir=data, verify_files=True))
    if errs:
        raise PackageError("published package invalid: " + "; ".join(e.message for e in errs))
    if max_file_mb:
        big = [f["path"] for f in pkg["files"] if f["bytes"] > max_file_mb * 2**20]
        if big:
            raise PackageError(f"files above the {max_file_mb} MB host limit: {big}")
    (out_dir / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Namma Space</title>'
        '<meta http-equiv="refresh" content="0; url=viewer.html?package=data/scene_package.json">'
        '<a href="viewer.html?package=data/scene_package.json">Open the 3D walkthrough</a>\n', encoding="utf-8")
    return {"out_dir": str(out_dir), "files": sum(1 for p in out_dir.rglob("*") if p.is_file()),
            "bytes": sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file())}
