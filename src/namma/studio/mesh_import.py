"""Import a finished, textured room mesh (e.g. a Polycam GLB export) as a Studio run and IF-13 scene package.

    import_mesh(source, projects_root, title=..., source_note=..., app="Polycam")

The mesh is NOT re-made: geometry, UVs and textures are kept (only floating fragments are dropped and a light level of
detail is added); the original upload is kept byte for byte under capture/originals/
Namma Space adds what its viewer needs and checks the mesh with its own measurements:
- model frame: +Y up (glTF convention; checked: upward-facing area must dominate the horizontal area), floor = the
  lowest large upward-facing surface, origin on the floor, +X along the floor's principal axis (as the photo route);
- start view: the observed floor spot with the most clearance, looking along the longest free direction;
- roaming grid and the unverified navigation candidate from the mesh surface (same functions as the photo route);
- scale: "device_tracking" (IF-13 1.2.0): the units are metres from the phone's motion tracking (ARKit: camera +
  motion sensors, plus LiDAR only on Pro models) - stated as the app's claim, with the measured room size reported
  so a wrong unit is visible at once;
- quality: an honest report - the geometry and textures are the app's, not verified by Namma Space.

Tidying (namma.studio.mesh_tidy): floating fragments are removed and a light level of detail (textures <= 2048 px) is
added; vertices, UVs and materials are otherwise kept. Not done: no camera viewpoints (Polycam's raw keyframe poses
could not be tied reliably to its exported GLB - three methods tried on the 2026-10-21 export, docs/POLYCAM.md), no
hole filling, no re-texturing.
"""
import hashlib
import io
import json
import math
import shutil
import struct
import time
import zipfile
from pathlib import Path

import numpy as np
from scipy import ndimage

from namma.core import timefmt
from namma.core.jsonio import write_json
from namma.recon import modelframe

IMPORT_VERSION = "1.0.0"
GLB_MAGIC = b"glTF"
DEFAULT_EYE_HEIGHT_M = 1.6          # standing eye height: an imported mesh has no capture-camera heights
UP_HORIZONTAL_SHARE_MIN = 0.15      # share of the surface area that must face +Y or -Y for "+Y is up" to hold


class ImportError_(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------------- GLB reading
MAX_GLB_BYTES = 2 * 2**30          # same as the largest accepted upload (namma.studio.polycam_bridge.MAX_FILE_BYTES)
def read_glb_bytes(source):
    """-> (glb bytes, name). source: a .glb file, or a .zip holding exactly one .glb (Polycam's GLTF export)."""
    source = Path(source)
    data = source.read_bytes()
    if data[:4] == GLB_MAGIC:
        return data, source.name
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as z:
            glbs = [i for i in z.infolist() if i.filename.lower().endswith(".glb")]
            if len(glbs) != 1:
                raise ImportError_(f"{source.name}: expected exactly one .glb inside, found {[i.filename for i in glbs]}")
            if glbs[0].file_size > MAX_GLB_BYTES:      # zip bomb: a small upload claiming gigabytes when unpacked
                raise ImportError_(f"{source.name}: the model inside is {glbs[0].file_size / 2**30:.1f} GB unpacked "
                                   f"(limit {MAX_GLB_BYTES // 2**30} GB)")
            return z.read(glbs[0]), Path(glbs[0].filename).name
    raise ImportError_(f"{source.name}: not a GLB file or a zip containing one (.gltf with separate files: export GLB)")


def _parse_glb(b):
    if b[:4] != GLB_MAGIC or struct.unpack("<I", b[4:8])[0] != 2:
        raise ImportError_("not a glTF 2.0 binary")
    jl = struct.unpack("<I", b[12:16])[0]
    doc = json.loads(b[20:20 + jl])
    binary = b[20 + jl + 8:] if len(b) > 20 + jl else b""
    return doc, binary


def _accessor(doc, binary, i):
    a = doc["accessors"][i]
    if a.get("sparse"):
        raise ImportError_("sparse accessors are not supported")
    bv = doc["bufferViews"][a["bufferView"]]
    n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[a["type"]]
    dt = np.dtype({5126: "<f4", 5125: "<u4", 5123: "<u2", 5121: "u1"}[a["componentType"]])
    off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride", 0)
    if stride and stride != n * dt.itemsize:
        raw = np.frombuffer(binary, np.uint8, a["count"] * stride, off).reshape(a["count"], stride)[:, : n * dt.itemsize]
        return np.ascontiguousarray(raw).view(dt).reshape(a["count"], n)
    return np.frombuffer(binary, dt, a["count"] * n, off).reshape(a["count"], n)


def _node_matrix(n):
    if "matrix" in n:
        return np.array(n["matrix"], float).reshape(4, 4).T          # glTF stores column-major
    T = np.eye(4)
    x, y, z, w = n.get("rotation", [0, 0, 0, 1])
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    T[:3, :3] = R * np.array(n.get("scale", [1, 1, 1]))
    T[:3, 3] = n.get("translation", [0, 0, 0])
    return T


def mesh_triangles(glb):
    """World-space triangles of every mesh in the default scene (node transforms applied, as a viewer shows them).
    -> (vertices (N,3), faces (M,3), stats)."""
    doc, binary = _parse_glb(glb)
    Vs, Fs, base = [], [], 0
    scene = doc.get("scenes", [{}])[doc.get("scene", 0)]

    def walk(ni, parent):
        nonlocal base
        n = doc["nodes"][ni]
        M = parent @ _node_matrix(n)
        if "mesh" in n:
            for p in doc["meshes"][n["mesh"]]["primitives"]:
                if p.get("mode", 4) != 4:
                    continue
                V = _accessor(doc, binary, p["attributes"]["POSITION"]).astype(np.float64)
                F = (_accessor(doc, binary, p["indices"]).reshape(-1, 3).astype(np.int64) if "indices" in p
                     else np.arange(len(V)).reshape(-1, 3))
                Vs.append(V @ M[:3, :3].T + M[:3, 3])
                Fs.append(F + base)
                base += len(V)
        for c in n.get("children", []):
            walk(c, M)
    for ni in scene.get("nodes", range(len(doc.get("nodes", [])))):
        walk(ni, np.eye(4))
    if not Fs:
        raise ImportError_("the GLB contains no triangle mesh")
    textures = []
    for im in doc.get("images", []):
        try:
            from PIL import Image
            bv = doc["bufferViews"][im["bufferView"]]
            with Image.open(io.BytesIO(binary[bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]])) as t:
                textures.append(list(t.size))
        except Exception:                                       # noqa: BLE001 - informational only
            textures.append(None)
    V, F = np.vstack(Vs), np.vstack(Fs)
    return V, F, {"vertices": int(len(V)), "triangles": int(len(F)), "textures": textures,
                  "materials": len(doc.get("materials", [])), "generator": doc.get("asset", {}).get("generator")}


def sample_surface(V, F, n, seed=0):
    """Area-weighted surface samples -> (points (n,3), unit normals (n,3), total area)."""
    rng = np.random.default_rng(seed)
    tri = V[F]
    cr = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area = np.linalg.norm(cr, axis=1) / 2
    ok = area > 0
    tri, cr, area = tri[ok], cr[ok], area[ok]
    pick = rng.choice(len(tri), n, p=area / area.sum())
    r1, r2 = rng.random((2, n))
    s = np.sqrt(r1)
    P = tri[pick, 0] * (1 - s)[:, None] + tri[pick, 1] * (s * (1 - r2))[:, None] + tri[pick, 2] * (s * r2)[:, None]
    N = cr[pick] / np.linalg.norm(cr[pick], axis=1, keepdims=True)
    return P, N, float(area.sum())


# ------------------------------------------------------------------------------------------------- analysis
def find_floor(P, N, up=np.array([0.0, 1.0, 0.0]), bin_m=0.02, min_share=0.2):
    """Floor height along `up`: the LOWEST height band of horizontal surface whose area is at least `min_share`
    of the largest such band (a table top is smaller than the floor; a floor seen only in parts is still large).
    Horizontal = normal within ~25 deg of `up` in EITHER direction: triangle winding varies between exporters."""
    h = P @ up
    facing = N @ up
    horiz_share = float(np.mean(np.abs(facing) > 0.9))
    upward = np.abs(facing) > 0.9
    if upward.sum() < 100:
        return None, {"horizontal_share": horiz_share, "horizontal_samples": int(upward.sum())}
    hu = h[upward]
    lo, hi = np.percentile(hu, [0.5, 99.5])
    bins = np.arange(lo - bin_m, hi + 2 * bin_m, bin_m)
    cnt, edges = np.histogram(hu, bins)
    cnt = np.convolve(cnt, [1, 1, 1], "same")                    # floors are not perfectly flat
    k = int(np.argmax(cnt >= min_share * cnt.max()))
    band = (hu >= edges[max(0, k - 1)]) & (hu <= edges[min(len(edges) - 1, k + 2)])
    floor_h = float(np.median(hu[band]))
    return floor_h, {"horizontal_share": round(horiz_share, 3), "horizontal_samples": int(upward.sum()),
                     "floor_share_of_upward": round(float(band.mean()), 3), "floor_height_raw": round(floor_h, 4)}


def model_transform(P, up, floor_h, floor_mask):
    """Rigid T (mesh -> model frame): +Y = up, floor at y = 0, origin below the floor footprint's centre,
    +X along the floor's principal horizontal axis (same datum as namma.recon.modelframe.model_frame)."""
    y = up / np.linalg.norm(up)
    base = P[floor_mask] if floor_mask.sum() >= 100 else P
    proj = base - np.outer(base @ y - floor_h, y)
    c = proj.mean(0)
    w, v = np.linalg.eigh(np.cov((proj - c).T))
    x = v[:, int(np.argmax(w))]
    x = x - (x @ y) * y
    x /= np.linalg.norm(x)
    z = np.cross(x, y)
    R = np.vstack([x, y, z])
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = -R @ c
    return T


def floor_relative(pm, nm, band_m=0.2, max_tilt_deg=5.0, iters=300, tol=0.01, seed=0):
    """Heights above the actual floor plane, for the walkable-floor map ONLY (the mesh, splat and view are untouched).
    Polycam exports were 1.4-1.65 deg off +Y: across a 4 m room one side of the floor sits 10-15 cm up - in the
    obstacle band, so the map had no clear floor and no route. -> (points with y above the floor plane, tilt_deg)"""
    m = (np.abs(pm[:, 1]) < band_m) & (np.abs(nm[:, 1]) > 0.9)
    if m.sum() < 200:
        return pm, 0.0
    A = np.c_[pm[m][:, 0], pm[m][:, 2], np.ones(m.sum())]
    y = pm[m][:, 1]
    rng = np.random.default_rng(seed)
    best, best_n = None, -1
    for _ in range(iters):                                  # RANSAC plane y = a x + b z + c
        k = rng.choice(len(y), 3, replace=False)
        try:
            c = np.linalg.solve(A[k], y[k])
        except np.linalg.LinAlgError:
            continue
        n_in = int((np.abs(A @ c - y) < tol).sum())
        if n_in > best_n:
            best, best_n = c, n_in
    inl = np.abs(A @ best - y) < tol
    c = np.linalg.lstsq(A[inl], y[inl], rcond=None)[0]
    tilt = float(np.degrees(np.arctan(np.hypot(c[0], c[1]))))
    if tilt > max_tilt_deg:
        return pm, 0.0
    out = pm.copy()
    out[:, 1] -= c[0] * pm[:, 0] + c[1] * pm[:, 2] + c[2]
    return out, round(tilt, 3)


def choose_start(pm, eye, res):
    """Observed floor cell with the largest clearance from anything in the body band -> (xz, look direction)."""
    x0, z0 = pm[:, 0].min() - res, pm[:, 2].min() - res
    w = int((pm[:, 0].max() - x0) / res) + 3
    h = int((pm[:, 2].max() - z0) / res) + 3
    ci = ((pm[:, 0] - x0) / res).astype(int)
    ri = ((pm[:, 2] - z0) / res).astype(int)
    floor = np.zeros((h, w), bool)
    block = np.zeros((h, w), bool)
    yy = pm[:, 1]
    floor[ri[np.abs(yy) < 0.06 * eye], ci[np.abs(yy) < 0.06 * eye]] = True
    band = (yy > 0.15 * eye) & (yy < 1.2 * eye)
    np.add.at(block, (ri[band], ci[band]), True)
    floor = ndimage.binary_closing(floor, iterations=2)
    free = floor & ~ndimage.binary_dilation(block, iterations=1)
    if not free.any():
        raise ImportError_("no free floor area found to start the walkthrough")
    dist = ndimage.distance_transform_edt(free)
    r, c = np.unravel_index(int(np.argmax(dist)), dist.shape)
    start = np.array([x0 + (c + 0.5) * res, z0 + (r + 0.5) * res])
    best, best_len = np.array([1.0, 0.0]), -1
    for a in np.linspace(0, 2 * math.pi, 32, endpoint=False):
        d = np.array([math.cos(a), math.sin(a)])
        steps = 0
        while True:
            q = start + d * res * (steps + 1)
            cc, rr = int((q[0] - x0) / res), int((q[1] - z0) / res)
            if not (0 <= rr < h and 0 <= cc < w) or block[rr, cc]:
                break
            steps += 1
        if steps > best_len:
            best, best_len = d, steps
    return start, best, {"clearance_m": round(float(dist[r, c]) * res, 3), "view_free_m": round(best_len * res, 2)}


# ------------------------------------------------------------------------------------------------- import
def import_mesh(source, projects_root, *, title, source_note, app="Polycam", device="iPhone",
                eye_height_m=DEFAULT_EYE_HEIGHT_M, units_m=1.0, samples=600_000, log=print, repair=True):
    """Create a Studio project + run from a finished mesh. Returns {"project_dir", "run_id", "package", ...}."""
    from namma.studio.package import build_scene_package
    from namma.studio.pipeline import RunContext
    from namma.studio.projects import ProjectStore

    log = log or (lambda *_: None)
    t0 = time.perf_counter()
    source = Path(source)
    glb, glb_name = read_glb_bytes(source)
    # tidy without re-making: drop floating fragments (none in the 2026-10-21 Polycam scan: it is 3 pieces once the
    # texture-seam splits are welded) and add a light level of detail (same geometry, textures <= 2048 px)
    from namma.studio.mesh_tidy import downscale_textures, remove_fragments
    glb, frag = remove_fragments(glb)
    unrepaired = glb
    if repair:                                      # wavy ceiling, hanging sheets, holes (namma.studio.mesh_repair)
        from namma.studio.mesh_repair import repair as repair_mesh
        glb, repair_stats = repair_mesh(glb)
    else:
        repair_stats = {"applied": False, "skipped": "turned off"}
    lod1, lod1_stats = downscale_textures(glb, 2048)
    V, F, mstats = mesh_triangles(glb)
    V = V * units_m
    P, N, area = sample_surface(V, F, samples)
    up = np.array([0.0, 1.0, 0.0])
    floor_h, fdiag = find_floor(P, N, up)
    if fdiag["horizontal_share"] < UP_HORIZONTAL_SHARE_MIN:
        raise ImportError_(f"only {fdiag['horizontal_share']:.0%} of the surface is horizontal along +Y (no floor or "
                           "ceiling): this does not look like a room in a +Y-up (glTF) export")
    floor_detected = floor_h is not None
    if not floor_detected:
        floor_h = float(np.percentile(P @ up, 2))
    floor_mask = (np.abs(P @ up - floor_h) < 0.03) & (np.abs(N @ up) > 0.9)
    T = model_transform(P, up, floor_h, floor_mask)
    pm = modelframe.transform_points(T, P)
    pn, fdiag["nav_floor_tilt_deg"] = floor_relative(pm, N @ T[:3, :3].T)   # walking maps: heights above the floor
    eye = float(eye_height_m)
    res = eye / 32
    start_xz, look, sdiag = choose_start(pn, eye, res)
    cams_model = np.array([[start_xz[0], eye, start_xz[1]]])
    grid, gstats, masks = modelframe.roam_grid(pn, cams_model, eye)
    floor_cov = gstats["observed_floor_in_footprint_cells"] / max(1, gstats["footprint_cells"])
    log(f"mesh: {mstats['triangles']:,} triangles, {area:.1f} m2 surface; floor detected {floor_detected}; "
        f"start clearance {sdiag['clearance_m']} m")

    # ---- project + run
    store = ProjectStore(projects_root)
    proj = store.create(title, notices=[source_note] if source_note else None)
    pid = proj["project_id"]
    pdir = store.path(pid)
    orig = store.originals(pid)
    shutil.copy2(source, orig / source.name)                       # the user's file, byte for byte
    run_id = store.new_run_id(pid)
    from namma.studio.profiles import COMMON
    # "nav" / "roam": the same walking settings as photo runs (the route planner reads cfg["nav"])
    cfg = {"profile": "import", "pipeline_version": f"import-{IMPORT_VERSION}", "app": app,
           "eye_height_m": eye, "units_m": units_m, "samples": samples, "nav": COMMON["nav"], "roam": COMMON["roam"]}
    cfg_sha = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
    proj = store.get(pid)
    proj["runs"].append({"run_id": run_id, "profile": "import", "created_at": timefmt.now_iso(), "config_sha256": cfg_sha})
    store.save(proj)
    ctx = RunContext(pdir, run_id, cfg)
    ctx.run_dir.mkdir(parents=True, exist_ok=True)

    # 60_texture: the app's GLB unchanged
    tex = ctx.dir("texture")
    tex.mkdir(parents=True)
    (tex / "lod0.glb").write_bytes(glb)
    if repair_stats.get("applied"):
        (tex / "lod0_unrepaired.glb").write_bytes(unrepaired)        # for comparison; not shown by the viewer
    lods = [{"lod": 0, "file": "lod0.glb", "bytes": len(glb), "triangles": mstats["triangles"],
             "textures": mstats["textures"], "source": f"{app} export {glb_name}"
             + (f", {frag['removed_pieces']} floating fragment(s) removed" if frag["removed_pieces"] else "")
             + (f", repaired (ceiling flattened, {repair_stats['filled_holes']} hole(s) closed)"
                if repair_stats.get("applied") else "")}]
    if lod1_stats["textures"]:                      # light level for fast first display
        (tex / "lod1.glb").write_bytes(lod1)
        lods.append({"lod": 1, "file": "lod1.glb", "bytes": len(lod1), "triangles": mstats["triangles"],
                     "textures": [t[1] for t in lod1_stats["textures"]], "source": "lod0 with textures <= 2048 px"})
    write_json(tex / "texture_summary.json", {"lods": lods, "fragments": frag, "repair": repair_stats})
    # 70_frame
    fr = ctx.dir("frame")
    fr.mkdir(parents=True)
    p1, p99 = np.percentile(pm, 1, axis=0), np.percentile(pm, 99, axis=0)
    start_pos = [round(float(start_xz[0]), 4), round(eye, 4), round(float(start_xz[1]), 4)]
    target = [round(start_pos[0] + float(look[0]) * eye, 4), round(eye * 0.95, 4), round(start_pos[2] + float(look[1]) * eye, 4)]
    frame = {"T_model_from_sfm": T.tolist(), "eye_height_units": eye, "points_source": "imported_mesh_surface",
             "points": int(len(pm)), "bounds_p1_p99": [p1.tolist(), p99.tolist()],
             "bounds_minmax": [pm.min(0).tolist(), pm.max(0).tolist()], "floor_detected": floor_detected,
             "up_source": "floor_plane" if floor_detected else "camera_orientation", "floor_suspect": False,
             "below_floor_fraction": round(float(np.mean(pm[:, 1] < -0.05)), 4), "diagnostics": {**fdiag, **sdiag},
             "roam_stats": gstats, "floor_coverage": round(floor_cov, 4),
             "start": {"camera": "start", "position": start_pos, "target": target,
                       "source": "imported mesh: floor spot with the most clearance, looking along the longest free line"},
             "eye_height_source": f"assumed standing eye height {eye} m (an imported mesh has no camera heights)"}
    write_json(fr / "frame.json", frame)
    write_json(fr / "cameras.json", {"cameras": []})
    write_json(fr / "roam_grid.json", grid)
    np.save(fr / "points_model_sample.npy", pm[:400_000].astype(np.float32))

    # project calibration: metric from the device's tracking (IF-13 1.2.0 "device_tracking")
    write_json(pdir / "calibration.json", {"run_id": run_id, "status": "device_tracking", "metres_per_unit": units_m,
                                            "relative_uncertainty": None,
                                            "method": f"{app} export ({device}); units metres from the phone's motion tracking, "
                                                      "not verified by Namma Space",
                                            "evaluation_only": False})
    # 80_quality: honest, measured, never claims Namma reconstructed it
    ext = (p99 - p1)
    warnings = [{"code": "IMPORTED-MESH", "severity": "warning",
                 "message": f"Geometry and textures were made by {app}, not by Namma Space; Namma Space only measured "
                            "them. Holes, merged furniture and texture seams are the app's."},
                {"code": "EYE-HEIGHT-ASSUMED", "severity": "info",
                 "message": f"Walking eye height is an assumed {eye} m (the export has no camera heights)."},
                {"code": "NO-PHOTO-VIEWPOINTS", "severity": "info",
                 "message": "The export has no photo positions, so 'jump to photo viewpoint' is unavailable."}]
    if not floor_detected:
        warnings.append({"code": "FLOOR-NOT-FOUND", "severity": "warning",
                         "message": "No large upward-facing floor surface was found; the floor height is estimated."})
    if ext[1] > 4.5 and ext[1] > min(ext[0], ext[2]):
        warnings.append({"code": "UP-AXIS-SUSPECT", "severity": "warning",
                         "message": f"The model is {ext[1]:.1f} m tall but only {min(ext[0], ext[2]):.1f} m across: the "
                                    "export may not be +Y-up (glTF convention). Check the orientation in the viewer."})
    if not (1.5 <= ext[1] <= 6 and 1.0 <= max(ext[0], ext[2]) <= 60):
        warnings.append({"code": "SIZE-IMPLAUSIBLE", "severity": "warning",
                         "message": f"The model measures {ext.round(2).tolist()} m (x, height, z); check the export's units."})
    facts = {"source": "imported_mesh", "app": app, "device": device, "file": source.name, "glb": glb_name,
             "mesh_faces": mstats["triangles"], "mesh_vertices": mstats["vertices"], "textures": mstats["textures"],
             "surface_area_m2": round(area, 2), "extent_m_p1_p99": ext.round(3).tolist(),
             "floor_detected": floor_detected, "floor_coverage": round(floor_cov, 4),
             "scale_status": "device_tracking", "fragments_removed": frag["removed_pieces"],
             "fragments_removed_area_m2": frag["removed_area"], "lods": len(lods)}
    verdict = "usable_with_warnings"
    qd = ctx.dir("quality")
    qd.mkdir(parents=True)
    write_json(qd / "quality_report.json", {"verdict": verdict, "warnings": warnings, "facts": facts, "thresholds": {},
                                             "created_at": timefmt.now_iso()})
    # 85_navgeom: unverified candidate, metric basis from the device
    from namma.contracts.semantic import validate_document
    from namma.core.issues import errors_only
    from namma.recon.floorplan import extract_if04
    nd = ctx.dir("navgeom")
    nd.mkdir(parents=True)
    nav_cfg = {"res_m": 0.05, "floor_tol_m": 0.05, "obstacle_min_pts": 3}
    prov = {"origin": "automatic", "method": f"namma.recon.floorplan.extract_if04 on the surface of a {app} mesh; "
            "scale basis device_tracking (1 m per unit)", "tool": "namma-studio", "tool_version": cfg["pipeline_version"],
            "author_role": "SCRIPT", "created_at": timefmt.now_iso(),
            "note": "UNVERIFIED automatic geometry (ARCH-030): unobserved floor is not walkable"}
    frame_doc = {"frame_id": f"site:{run_id}", "units": "m", "handedness": "right", "up_axis": "+Y",
                 "datum": f"model frame of run {run_id} (metres from {app}); origin on the floor"}
    try:
        doc, diag = extract_if04(pn, geometry_id=f"geom-{run_id}", run_id=run_id, frame=frame_doc, provenance=prov,
                                 res=nav_cfg["res_m"], floor_tol=nav_cfg["floor_tol_m"], obstacle_min_pts=nav_cfg["obstacle_min_pts"])
        write_json(nd / "geometry.if04.json", doc)
        errs = errors_only(validate_document(doc, "IF-04"))
        status = "unverified" if not errs and doc["floors"][0]["walkable_regions"] else "unavailable"
        write_json(nd / "navgeom.json", {"status": status, "scale_basis": "device_tracking", "metres_per_unit": units_m,
                                         "relative_uncertainty": None, "diagnostics": diag,
                                         "if04_errors": [e.message for e in errs][:5],
                                         "note": "Routes on this geometry are never shown as validated (ARCH-030)."})
    except Exception as e:                                        # noqa: BLE001 - navigation is optional
        write_json(nd / "navgeom.json", {"status": "unavailable", "reason": f"{e.__class__.__name__}: {e}"})

    # run status (as the Studio shows it) + package through the normal, validating builder
    stages = [{"name": n, "label": lbl, "status": "done"} for n, lbl in
              (("import", f"Reading the {app} mesh"), ("frame", "Finding the floor and orientation"),
               ("quality", "Recording what was imported"), ("navgeom", "Extracting walkable floor (unverified)"),
               ("package", "Packaging for the 3D viewer"))]
    res_pkg = build_scene_package(ctx, ctx.dir("package"))
    secs = round(time.perf_counter() - t0, 1)
    stages[0]["seconds"] = secs
    write_json(ctx.run_dir / "config.json", {"config": cfg, "config_sha256": cfg_sha})
    write_json(ctx.status_path, {"run_id": run_id, "profile": "import", "config_sha256": cfg_sha,
                                 "pipeline_version": cfg["pipeline_version"], "status": "succeeded",
                                 "created_at": timefmt.now_iso(), "ended_at": timefmt.now_iso(), "stages": stages,
                                 "progress": 1.0, "current": None, "error": None, "attempts": 1})
    log(f"package {res_pkg['package_id']}: {res_pkg['files']} files ({secs} s)")
    return {"project_dir": str(pdir), "project_id": pid, "run_id": run_id, "package": res_pkg, "facts": facts,
            "frame": {k: frame[k] for k in ("floor_detected", "floor_coverage", "start", "diagnostics")}, "seconds": secs}
