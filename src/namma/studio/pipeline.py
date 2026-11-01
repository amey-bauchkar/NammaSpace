"""Resumable reconstruction pipeline for one Studio run (photos / video frames -> scene package).

Stages (folder in the run directory):
  prepare   10_images      working copies: EXIF orientation applied, resized (originals untouched)
  sfm       20_sfm         COLMAP features, matching, mapping (+ second registration pass)
  undistort 30_undistorted COLMAP undistortion for MVS
  dense     40_dense       OpenMVS import + DensifyPointCloud         (fallback: sparse points)
  mesh      50_mesh        OpenMVS ReconstructMesh
  texture   60_texture     OpenMVS TextureMesh -> GLB level(s) of detail
  frame     70_frame       scale-free model frame, cameras, start view, roaming grid
  quality   80_quality     diagnostics and plain-language warnings
  navgeom   85_navgeom     navigation geometry candidate (IF-04) at the recorded scale basis
  package   90_package     IF-13 scene package for the viewer (+ IF-02 job, IF-12 run log)

A stage writes <dir>/stage.json when it completes. Re-running the same run skips completed stages
whose configuration digest is unchanged, so an interrupted run resumes at the failed stage.
"""
import os
import shutil
import threading
import time
import traceback
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from namma.contracts.semantic import validate_document
from namma.core import timefmt
from namma.core.issues import errors_only
from namma.core.jsonio import load_strict, write_json
from namma.pipeline.runlog import RunLog
from namma.recon import roomfill, exposure, harmonize, blendtex, colmap, meshclean, modelframe, openmvs, quality
from namma.recon import glb as glb_tools
from namma.recon.io import read_ply
from namma.recon.runner import StepFailed
from namma.recon import hardware
from namma.recon.tools import find_tools, match_threads, mvs_threads, sift_threads
from namma.studio.profiles import config_sha256

STAGES = ["prepare", "sfm", "undistort", "dense", "mesh", "texture", "frame", "quality", "navgeom", "package"]
DIRS = {"prepare": "10_images", "sfm": "20_sfm", "undistort": "30_undistorted", "dense": "40_dense",
        "mesh": "50_mesh", "texture": "60_texture", "frame": "70_frame", "quality": "80_quality",
        "navgeom": "85_navgeom", "package": "90_package"}
WEIGHTS = {"prepare": 3, "sfm": 20, "undistort": 3, "dense": 35, "mesh": 10, "texture": 18, "frame": 4,
           "quality": 2, "navgeom": 2, "package": 3}
LABELS = {"prepare": "Preparing photos", "sfm": "Placing cameras (structure from motion)",
          "undistort": "Correcting lens distortion", "dense": "Building dense 3D geometry",
          "mesh": "Building the surface mesh", "texture": "Painting photo textures onto the mesh",
          "frame": "Finding the floor and orientation", "quality": "Checking reconstruction quality",
          "navgeom": "Extracting walkable floor (unverified)", "package": "Packaging for the 3D viewer"}


class Cancelled(RuntimeError):
    pass


class StageError(RuntimeError):
    def __init__(self, stage, message, hint=None, log=None, record=None):
        super().__init__(message)
        self.stage, self.hint, self.log, self.record = stage, hint, log, record


def friendly(stage, exc):
    """Map a failure to a message and a next step for a non-expert."""
    rec = getattr(exc, "record", None) or {}
    tail = " ".join(rec.get("tail", [])).lower()
    rc = rec.get("return_code")
    if rec.get("status") == "timeout":
        return f"{LABELS[stage]} took longer than allowed.", "Try the Preview profile or fewer photos."
    if rc in (3221225477, -1073741819) or "bad_alloc" in tail or "paging file" in tail or "out of memory" in tail:
        return (f"{LABELS[stage]} ran out of memory.",
                "Close other applications and retry; the run resumes from this step. The Preview profile needs less memory.")
    return f"{LABELS[stage]} failed: {exc}", "Open the technical log for details. Retrying resumes from this step."


class RunContext:
    def __init__(self, project_dir, run_id, cfg):
        self.project_dir = Path(project_dir).resolve()
        self.run_id = run_id
        self.run_dir = self.project_dir / "runs" / run_id
        self.cfg = cfg
        self.cfg_sha = config_sha256(cfg)
        self.cancel_file = self.run_dir / "CANCEL"
        self.status_path = self.run_dir / "run.json"
        self.logs = self.run_dir / "logs"
        self.tools = None
        self.facts = {}
        self.status = None

    # ------------------------------------------------------------------ status
    def load_status(self):
        if self.status_path.exists():
            self.status = load_strict(self.status_path)
        else:
            self.status = {"run_id": self.run_id, "profile": self.cfg["profile"], "config_sha256": self.cfg_sha,
                           "pipeline_version": self.cfg["pipeline_version"], "status": "queued",
                           "created_at": timefmt.now_iso(),
                           "stages": [{"name": s, "label": LABELS[s], "status": "pending"} for s in STAGES],
                           "progress": 0.0, "current": None, "error": None, "attempts": 0}
        return self.status

    def save_status(self):
        write_json(self.status_path, self.status)

    def stage_entry(self, name):
        return next(s for s in self.status["stages"] if s["name"] == name)

    def set_progress(self, stage, sub_label=None, frac=None):
        done = sum(WEIGHTS[s["name"]] for s in self.status["stages"] if s["status"] in ("done", "skipped"))
        cur = WEIGHTS[stage] * (frac or 0.0)
        self.status["progress"] = round(min(0.999, (done + cur) / sum(WEIGHTS.values())), 4)
        self.status["current"] = {"stage": stage, "label": LABELS[stage], "detail": sub_label,
                                  "fraction": round(frac, 3) if frac is not None else None}
        self.status["updated_at"] = timefmt.now_iso()
        self.save_status()

    def progress_cb(self, stage):
        last = [0.0]

        def cb(label, frac):
            now = time.time()
            if now - last[0] > 1.0:
                last[0] = now
                self.set_progress(stage, label, frac)
        return cb

    def check_cancel(self):
        if self.cancel_file.exists():
            raise Cancelled("cancelled by user")

    def dir(self, stage):
        return self.run_dir / DIRS[stage]


# ---------------------------------------------------------------------- stages
def stage_prepare(ctx):
    out = ctx.dir("prepare")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    man = load_strict(ctx.project_dir / "capture" / "capture_manifest.json")
    rep = load_strict(ctx.project_dir / "capture" / "capture_report.json")
    if rep["status"] != "ready":
        raise StageError("prepare", "The capture has blocking problems; fix them before processing.",
                         "Open the capture check results.")
    max_px = ctx.cfg["work_max_px"]
    inputs = [ctx.project_dir / p for p in man["inputs"]]
    records, dims, cams = [], set(), set()
    cb = ctx.progress_cb("prepare")
    for i, src in enumerate(inputs):
        ctx.check_cancel()
        cb("Preparing photos", i / len(inputs))
        dst = out / (src.stem + ".jpg")
        with Image.open(src) as im:
            exif = im.getexif()
            w0, h0 = im.size
            upright = int(exif.get(0x0112, 1)) == 1 if exif else True
            fits = not max_px or max(w0, h0) <= max_px
            if upright and fits and im.format == "JPEG":
                # nothing to change: use the original bytes (no re-encoding loss, EXIF kept)
                size = (w0, h0)
                copy = True
            else:
                copy = False
                im = ImageOps.exif_transpose(im).convert("RGB")
                w1, h1 = im.size
                scale = min(1.0, max_px / max(w1, h1)) if max_px else 1.0
                if scale < 1.0:
                    im = im.resize((round(w1 * scale), round(h1 * scale)), Image.Resampling.LANCZOS)
                if exif:
                    exif[0x0112] = 1          # pixels are now upright
                im.save(dst, "JPEG", quality=93, exif=exif.tobytes() if exif else b"")
                size = im.size
            dims.add(tuple(size))
            cams.add((str(exif.get(0x010F, "")), str(exif.get(0x0110, ""))) if exif else ("", ""))
        if copy:
            shutil.copy2(src, dst)
        records.append({"name": dst.name, "source": str(src.relative_to(ctx.project_dir)).replace("\\", "/"),
                        "source_size": [w0, h0], "size": list(size), "copied_unchanged": copy})
    single = len(dims) == 1 and len(cams) == 1
    ctx.facts.update(images_input=len(records), single_camera=single, mode=man["mode"])
    write_json(out / "images.json", {"images": records, "single_camera": single, "max_px": max_px})
    return {"images": len(records), "single_camera": single}


def stage_sfm(ctx):
    out = ctx.dir("sfm")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    img_dir = ctx.dir("prepare")
    meta = load_strict(img_dir / "images.json")
    n = len(meta["images"])
    cfg, T = ctx.cfg, ctx.cfg["timeouts_s"]
    db = out / "database.db"
    gpu = bool(getattr(ctx.tools, "colmap_gpu", False))
    gpu_index = hardware.resources().gpu_index
    threads = sift_threads(cfg["sift"]["max_image_px"], gpu=gpu)
    n_features = sift_feature_budget(cfg, n)
    model = cfg["camera_model_single"] if meta["single_camera"] else cfg["camera_model_mixed"]
    cb = ctx.progress_cb("sfm")

    def extract(on_gpu):
        return colmap.extract_features(ctx.tools, db, img_dir, ctx.logs, camera_model=model,
                                       single_camera=meta["single_camera"], max_image_px=cfg["sift"]["max_image_px"],
                                       max_features=n_features,
                                       threads=threads if on_gpu == gpu else sift_threads(cfg["sift"]["max_image_px"]),
                                       timeout_s=T["features"], cancel_file=ctx.cancel_file, progress=cb,
                                       peak_threshold=cfg["sift"].get("peak_threshold", 0.00667),
                                       use_gpu=on_gpu, gpu_index=gpu_index)

    def fresh_db():
        for f in out.glob("database.db*"):
            f.unlink()
    rec, dev_features = gpu_then_cpu(ctx, "sfm", "feature extraction", extract, gpu, before_retry=fresh_db)
    recs = [rec]
    ctx.check_cancel()
    # For photo captures, exhaustive matching connects loops and turns across the room;
    # sequential matching easily fragments unless the frames are from continuous video.
    max_exhaustive = cfg["matching"]["exhaustive_max_images"] if ctx.facts.get("mode") == "video" \
        else max(cfg["matching"]["exhaustive_max_images"], 400)
    mode = "sequential" if n > max_exhaustive else "exhaustive"
    ctx.set_progress("sfm", f"Matching photos ({mode})", 0.0)
    match_gpu = gpu and dev_features == "gpu"
    mthreads = match_threads(n_features, gpu=match_gpu)
    with _MatchProgress(db, n if mode == "exhaustive" else None,
                        lambda f: ctx.set_progress("sfm", f"Matching photos ({mode})", f)):
        rec, dev_matching = gpu_then_cpu(
            ctx, "sfm", "feature matching",
            lambda on_gpu: colmap.match_features(ctx.tools, db, ctx.logs, mode=mode,
                                                 overlap=cfg["matching"]["sequential_overlap"],
                                                 guided=cfg["matching"]["guided"], timeout_s=T["matching"],
                                                 cancel_file=ctx.cancel_file, progress=cb,
                                                 threads=mthreads if on_gpu == match_gpu else match_threads(n_features),
                                                 use_gpu=on_gpu, gpu_index=gpu_index),
            match_gpu)
        recs.append(rec)
    ctx.check_cancel()
    ctx.set_progress("sfm", "Placing cameras", 0.0)
    engine = cfg["mapper"].get("engine", "incremental")
    best = None
    if engine == "global":
        # global SfM is ~7x faster; keep it only if it placed at least half the frames, else the incremental mapper
        try:
            recs.append(colmap.map_global(ctx.tools, db, img_dir, out / "sparse", ctx.logs, timeout_s=T["mapper"],
                                          cancel_file=ctx.cancel_file, progress=cb, use_gpu=gpu, gpu_index=gpu_index))
            best = colmap.best_model(ctx.tools, out / "sparse", out / "sparse_txt", ctx.logs)
        except StepFailed as e:
            if e.record.get("status") == "cancelled":
                raise Cancelled("cancelled by user") from e
            best = None
        if best is not None and len(best[2]) >= max(3, n // 2):
            # Trajectory sanity check: global rotation averaging can diverge on uncalibrated consumer photos,
            # projecting cameras thousands of units away into wild disconnected clusters.
            c_poses = np.array([best[2][k]["C"] for k in sorted(best[2])])
            span = float(np.ptp(c_poses, axis=0).max())
            diffs = np.linalg.norm(np.diff(c_poses, axis=0), axis=1)
            max_jump = float(diffs.max()) if len(diffs) else 0.0
            if span > 200.0 or max_jump > 50.0:
                ctx.facts["mapper_fallback"] = f"global mapper trajectory exploded (span={span:.1f}, jump={max_jump:.1f})"
                best = None
        if best is None or len(best[2]) < max(3, n // 2):
            if "mapper_fallback" not in ctx.facts:
                ctx.facts["mapper_fallback"] = f"global mapper placed {len(best[2]) if best else 0}/{n} frames"
            for d in ("sparse", "sparse_txt"):
                shutil.rmtree(out / d, ignore_errors=True)
            engine, best = "incremental", None
    if best is None:
        recs.append(colmap.map_sparse(ctx.tools, db, img_dir, out / "sparse", ctx.logs, timeout_s=T["mapper"],
                                      cancel_file=ctx.cancel_file, progress=cb,
                                      abs_pose_min_inliers=cfg["mapper"]["abs_pose_min_inliers"]))
        best = colmap.best_model(ctx.tools, out / "sparse", out / "sparse_txt", ctx.logs)
    if best is None:
        raise StageError("sfm", "No 3D model could be built: the photos do not overlap enough.",
                         "Capture again with more overlap (each spot in 3+ photos).")
    model_dir, txt_dir, imgs, model_scores = best
    others = [p for p in (out / "sparse_txt").iterdir() if p.is_dir() and p != txt_dir]
    # images that ended up ONLY in a separate (smaller) model: the capture split into groups
    other_images = len({nm for p in others for nm in colmap.read_images_txt(p / "images.txt")} - set(imgs))
    rescue = {"attempted": False}
    if cfg["mapper"]["rescue"] and len(imgs) < n:
        rescue = {"attempted": True, "before": len(imgs)}
        try:
            colmap.register_remaining(ctx.tools, db, model_dir, out / "rescued", ctx.logs, timeout_s=T["mapper"],
                                      cancel_file=ctx.cancel_file,
                                      abs_pose_min_inliers=cfg["mapper"]["rescue_min_inliers"],
                                      abs_pose_min_inlier_ratio=cfg["mapper"]["rescue_min_ratio"])
            colmap.to_text(ctx.tools, out / "rescued", out / "rescued_txt", ctx.logs, step="colmap_model_converter_rescued")
            imgs2 = colmap.read_images_txt(out / "rescued_txt" / "images.txt")
            rescue["after"] = len(imgs2)
            if len(imgs2) > len(imgs):
                model_dir, txt_dir, imgs = out / "rescued", out / "rescued_txt", imgs2
                rescue["used"] = True
        except StepFailed as e:
            if e.record.get("status") == "cancelled":
                raise Cancelled("cancelled by user") from e
            rescue["error"] = str(e)
    xyz, rgb, err, track = colmap.read_points3d_txt(txt_dir / "points3D.txt")
    kp = colmap.keypoint_counts(db)
    names = [r["name"] for r in meta["images"]]
    unreg = [nm for nm in names if nm not in imgs]
    low_tex = [nm for nm in names if kp.get(nm, 0) < quality.THRESHOLDS["low_texture_keypoints"]]
    ctx.facts.update(images_registered=len(imgs), unregistered=unreg, other_models=other_images,
                     sparse_points=int(len(xyz)), mean_reprojection_px=float(err.mean()) if len(err) else None,
                     mean_track_length=float(track.mean()) if len(track) else None, low_texture_images=low_tex,
                     matching_mode=mode, sift_threads=threads, match_threads=mthreads, camera_model=model,
                     mapper_engine=engine)
    write_json(out / "sfm_summary.json", {"model_dir": model_dir.relative_to(out).as_posix(),
                                          "txt_dir": txt_dir.relative_to(out).as_posix(),
                                          "registered": len(imgs), "input": n, "unregistered": unreg,
                                          "other_model_images": other_images, "rescue": rescue,
                                          "keypoints": kp, "matching_mode": mode, "threads": threads,
                                          "match_threads": mthreads, "sift_max_features": n_features, "mapper_engine": engine,
                                          "mapper_fallback": ctx.facts.get("mapper_fallback"),
                                          "camera_model": model, "sparse_points": int(len(xyz)),
                                          "compute": {"features": dev_features, "matching": dev_matching,
                                                      "gpu_reason": getattr(ctx.tools, "gpu_reason", ""),
                                                      "fallbacks": ctx.facts.get("gpu_fallbacks", [])},
                                          "models": model_scores,
                                          "mean_reprojection_px": ctx.facts["mean_reprojection_px"],
                                          "mean_track_length": ctx.facts["mean_track_length"]})
    if len(imgs) < 3:
        raise StageError("sfm", f"Only {len(imgs)} photos could be placed in 3D.",
                         "Capture again with more overlap and steady, well-lit photos.")
    if len(xyz) < 10 * len(imgs):
        raise StageError("sfm", f"Camera placement produced only {len(xyz)} 3D points for {len(imgs)} photos; the "
                                "result is not reliable enough to build a surface.",
                         "Move more slowly, keep more overlap between photos / video frames, and avoid plain surfaces.")
    return {"registered": len(imgs), "input": n, "rescue": rescue, "commands": len(recs)}


def sift_feature_budget(cfg, n_images):
    """SIFT features per photo. Large photo sets use fewer: exhaustive matching cost grows with n^2 x features^2
    (experiment R150-4b: 150 photos, 6000 -> 3000 features: matching 34.5 -> 12.8 min, 150/150 placed, camera
    agreement with a reference model 0.022 % -> 0.026 % of the camera spread)."""
    s = cfg["sift"]
    if s.get("max_features_large_set") and n_images >= s.get("large_set_min_images", 10 ** 9):
        return min(s["max_features"], s["max_features_large_set"])
    if n_images > 150:
        return min(s["max_features"], 4096)
    return s["max_features"]


class _MatchProgress:
    """COLMAP's matcher prints nothing while it works; count finished pairs in its database instead (read-only)."""

    def __init__(self, db, n_images, report, every_s=10):
        self.db, self.total, self.report, self.every = db, (n_images * (n_images - 1) // 2 if n_images else None), report, every_s
        self.stop = threading.Event()

    def _loop(self):
        import sqlite3
        while not self.stop.wait(self.every):
            try:
                con = sqlite3.connect(f"file:{Path(self.db).as_posix()}?mode=ro", uri=True, timeout=1)
                try:
                    done = con.execute("select count(*) from two_view_geometries").fetchone()[0]
                finally:
                    con.close()
                self.report(min(0.99, done / self.total))
            except Exception:     # progress must never break a run
                pass

    def __enter__(self):
        if self.total:
            threading.Thread(target=self._loop, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        return False


def _sfm_model(ctx):
    s = load_strict(ctx.dir("sfm") / "sfm_summary.json")
    return ctx.dir("sfm") / s["model_dir"], ctx.dir("sfm") / s["txt_dir"], s


def dense_subset(names, sharpness, k):
    """Evenly spread subset of k registered images (in name order = capture order), sharpest in each bin."""
    names = sorted(names)
    if len(names) <= k:
        return names
    bins = np.array_split(np.arange(len(names)), k)
    return [max((names[i] for i in b), key=lambda n: (sharpness.get(Path(n).stem.lower(), 0.0), n)) for b in bins]


def undistort_side(sizes, max_px, budget_mp):
    """max_image_size for COLMAP's undistorter (None = keep the original size).

    max_px is the profile's cap (0 = none) and is passed on unchanged; budget_mp caps the total pixel count of the
    MVS images (memory guard: OpenMVS texturing keeps every view in RAM - 7.0 GB peak for 30 x 24 MP on kicker)."""
    side = max_px or None
    if budget_mp:
        longest = max(max(s) for s in sizes)
        ref = min(side or longest, longest)
        total = sum(w * h * min(1.0, ref / max(w, h)) ** 2 for w, h in sizes) / 1e6
        if total > budget_mp:
            side = int(ref * (budget_mp / total) ** 0.5)
    return side


def stage_undistort(ctx):
    out = ctx.dir("undistort")
    if out.exists():
        shutil.rmtree(out)
    model_dir, txt_dir, _ = _sfm_model(ctx)
    imgs = colmap.read_images_txt(txt_dir / "images.txt")
    k = ctx.cfg.get("dense_max_images") or len(imgs)
    result = {"registered": len(imgs), "dense_images": len(imgs), "subset": False}
    if len(imgs) > k:
        # MVS cost grows with every image; camera tracking benefits from dense frames, surfaces do not need all
        man = load_strict(ctx.project_dir / "capture" / "capture_manifest.json")
        sharp = {Path(x["name"]).stem.lower(): x.get("sharpness", 0.0) for x in man.get("per_input", [])}
        keep = set(dense_subset(list(imgs), sharp, k))
        sub = ctx.dir("sfm") / "dense_model"
        if sub.exists():
            shutil.rmtree(sub)
        colmap.delete_images(ctx.tools, model_dir, sub, sorted(set(imgs) - keep), ctx.logs)
        model_dir = sub
        result.update(dense_images=len(keep), subset=True)
        imgs = {n: v for n, v in imgs.items() if n in keep}
    sizes = []
    for n in imgs:
        with Image.open(ctx.dir("prepare") / n) as im:
            sizes.append(im.size)
    budget = ctx.cfg.get("mvs_pixel_budget_mp")
    if budget:
        ram_mp = ram_pixel_budget_mp(hardware.resources().ram_budget_gb)
        if ram_mp < budget:
            result["pixel_budget_clamped_by_ram"] = {"profile_mp": budget, "ram_mp": ram_mp}
            budget = ram_mp
    side = undistort_side(sizes, ctx.cfg["undistort_max_px"], budget)
    result.update(max_side=side, input_megapixels=round(sum(w * h for w, h in sizes) / 1e6, 1))
    colmap.undistort(ctx.tools, ctx.dir("prepare"), model_dir, out, ctx.logs, max_image_px=side or -1,
                     timeout_s=ctx.cfg["timeouts_s"]["undistort"], cancel_file=ctx.cancel_file)
    result["undistorted"] = len(list((out / "images").glob("*")))
    return result


def stage_dense(ctx):
    out = ctx.dir("dense")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    T, d = ctx.cfg["timeouts_s"], ctx.cfg["dense"]
    undist_imgs = [p for p in (ctx.dir("undistort") / "images").glob("*") if p.is_file() and not p.name.startswith(".")]
    n_imgs = len(undist_imgs)
    # Cap target resolution to the actual photo size (an image cannot produce depth maps larger than itself)
    longest_side = d["max_resolution"]
    if undist_imgs:
        try:
            from PIL import Image
            with Image.open(undist_imgs[0]) as im:
                longest_side = max(im.size)
        except Exception:
            pass
    target_res = min(d["max_resolution"], longest_side)
    free_gb = shutil.disk_usage(ctx.run_dir).free / 2**30
    max_res, clamped_info = clamp_dense_resolution(free_gb, n_imgs, target_res)
    if clamped_info:
        ctx.facts["dense_resolution_clamped_by_disk"] = clamped_info
    require_disk(ctx, "dense", dense_disk_need_gb(n_imgs, max_res))
    openmvs.interface_colmap(ctx.tools, ctx.dir("undistort"), out / "scene.mvs", out, ctx.logs,
                             timeout_s=T["interface"], cancel_file=ctx.cancel_file)
    ctx.check_cancel()
    gpu = bool(getattr(ctx.tools, "densify_gpu", False))
    dev = "cpu"
    num_views = min(d["number_views"], 4) if ctx.facts.get("mode") != "video" else d["number_views"]

    def densify(on_gpu):
        return openmvs.densify(ctx.tools, out / "scene.mvs", out / "scene_dense.mvs", out, ctx.logs,
                               resolution_level=d["resolution_level"], max_resolution=max_res,
                               number_views=num_views, threads=mvs_threads(), timeout_s=T["densify"],
                               cancel_file=ctx.cancel_file, progress=ctx.progress_cb("dense"), n_images=n_imgs,
                               exe=ctx.tools.densify_exe(on_gpu) if hasattr(ctx.tools, "densify_exe") else None,
                               cuda_device=hardware.resources().gpu_index if on_gpu else None)

    def clear_partial():
        for f in list(out.glob("*.dmap")) + list(out.glob("scene_dense*")):
            f.unlink()
    try:
        _, dev = gpu_then_cpu(ctx, "dense", "dense depth maps", densify, gpu, before_retry=clear_partial)
        n_dense = _ply_vertex_count(out / "scene_dense.ply")
        if n_dense < 1000:
            raise _NoDense(f"dense reconstruction produced {n_dense} points")
        ctx.facts["dense_fallback"] = False
        result = {"dense": True, "dense_points": n_dense}
    except _NoDense as e:
        ctx.facts.update(dense_fallback=True, dense_fallback_reason=str(e))
        result = {"dense": False, "fallback_reason": str(e)}
    except StepFailed as e:
        if e.record.get("status") == "cancelled":
            raise Cancelled("cancelled by user") from e
        if shutil.disk_usage(out).free < 0.5 * 2**30:
            # a full disk is not a reconstruction problem: say so instead of falling back to a rough mesh
            raise StageError("dense", "The disk became full while building the dense 3D geometry.",
                             "Free several GB (old projects, Recycle Bin), then press Resume.", log=e.record.get("log"))
        # genuine fallback: the coarse SfM points in scene.mvs are meshed instead
        ctx.facts.update(dense_fallback=True, dense_fallback_reason=f"{e} (see logs/{e.record.get('log')})")
        result = {"dense": False, "fallback_reason": str(e)}
    result["compute"] = {"densify": dev, "fallbacks": [f for f in ctx.facts.get("gpu_fallbacks", []) if "dense" in f]}
    if clamped_info:
        result["resolution_clamped_by_disk"] = clamped_info
    write_json(out / "dense_summary.json", {**result, "dense_fallback": ctx.facts["dense_fallback"],
                                            "dense_fallback_reason": ctx.facts.get("dense_fallback_reason")})
    return result


def dense_disk_need_gb(n_images, max_resolution):
    """Free disk needed for dense + mesh + texture. Measured: 246-photo capture, 90 dense images at 1600 px wrote
    5.5 GB of depth maps (~61 MB per image); scales with the pixel count; +1.5 GB for mesh, texture, package."""
    return n_images * 0.065 * (max_resolution / 1600) ** 2 + 1.5


def clamp_dense_resolution(free_gb, n_images, max_resolution, reserve_gb=10.0):
    """Dynamically clamp max_resolution if free disk space cannot accommodate dense depth maps.
    Returns (clamped_resolution, clamped_info_dict or None)."""
    usable_gb = max(2.0, free_gb - reserve_gb)
    need_gb = dense_disk_need_gb(n_images, max_resolution)
    if need_gb <= usable_gb:
        return max_resolution, None
    for cand_res in (2560, 2048, 1600, 1280, 960):
        if cand_res < max_resolution:
            cand_gb = dense_disk_need_gb(n_images, cand_res)
            if cand_gb <= usable_gb or cand_res == 960:
                info = {
                    "original_px": max_resolution,
                    "clamped_px": cand_res,
                    "original_need_gb": round(need_gb, 1),
                    "clamped_need_gb": round(cand_gb, 1),
                    "free_disk_gb": round(free_gb, 1),
                }
                return cand_res, info
    return max_resolution, None


def require_disk(ctx, stage, need_gb):
    free = shutil.disk_usage(ctx.run_dir).free / 2**30
    if free < need_gb:
        raise StageError(stage, f"Not enough free disk space: about {need_gb:.1f} GB is needed for this step, "
                                f"{free:.1f} GB is free on this drive.",
                         "Free some space (delete old projects or empty the Recycle Bin), then press Resume.")


WINDOWS_MAX_PATH = 259


def check_path_lengths(ctx):
    """Fail early with a clear message when output paths would exceed Windows' 260-character limit: COLMAP and
    OpenMVS then abort with a bare 0xC0000409 / 0xC0000005 (seen on a 262-character undistorted-image path).
    The deepest files are the undistorted images: <run>/30_undistorted/images/<photo name>.jpg"""
    if os.name != "nt":
        return
    man = ctx.project_dir / "capture" / "capture_manifest.json"
    names = [Path(p).stem + ".jpg" for p in (load_strict(man).get("inputs") or [])] if man.exists() else []
    names = names or ["x" * 24 + ".jpg"]          # typical phone / video-frame name length
    worst = ctx.dir("undistort") / "images" / max(names, key=len)
    if len(str(worst)) > WINDOWS_MAX_PATH:
        raise StageError("prepare", f"The project folder path is too long for Windows ({len(str(worst))} characters "
                                    f"for the deepest file, limit {WINDOWS_MAX_PATH}).",
                         "Use a shorter projects folder (e.g. start_studio.py --projects C:\\NammaWork\\projects) "
                         "or shorter photo names.")


def gpu_then_cpu(ctx, stage, what, run, gpu, before_retry=None):
    """run(on_gpu) -> record. A GPU attempt that fails (not cancelled, not timed out) is retried once on the CPU,
    the validated path: a CUDA / driver / VRAM problem then costs time, not the run. -> (record, "gpu" | "cpu")."""
    if gpu:
        try:
            return run(True), "gpu"
        except StepFailed as e:
            if e.record.get("status") in ("cancelled", "timeout"):
                raise
            ctx.facts.setdefault("gpu_fallbacks", []).append(
                f"{what}: GPU attempt failed ({e}; see logs/{e.record.get('log')}), retried on the CPU")
            ctx.set_progress(stage, f"GPU {what} failed; retrying on the CPU", None)
            if before_retry:
                before_retry()
    return run(False), "cpu"


def ram_pixel_budget_mp(ram_budget_gb):
    """Undistorted megapixels OpenMVS texturing can hold in `ram_budget_gb` (it keeps every view in RAM: 7.0 GB
    peak for 30 x 24 MP = 720 MP on kicker, i.e. ~9.7 MB per MP). Only ever lowers a profile's own budget."""
    return int(max(100.0, (ram_budget_gb - 1.0) / 0.0097))


class _NoDense(RuntimeError):
    pass


def _ply_vertex_count(path):
    """Vertex count from a PLY header (0 if the file is missing)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096).split(b"end_header")[0].decode("ascii", "replace")
    except OSError:
        return 0
    for line in head.splitlines():
        if line.startswith("element vertex"):
            return int(line.split()[2])
    return 0


def _dense_state(ctx):
    s = load_strict(ctx.dir("dense") / "dense_summary.json")
    ctx.facts["dense_fallback"] = s["dense_fallback"]
    if s.get("dense_fallback_reason"):
        ctx.facts["dense_fallback_reason"] = s["dense_fallback_reason"]
    return s


def stage_mesh(ctx):
    out = ctx.dir("mesh")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    dense = ctx.dir("dense")
    s = _dense_state(ctx)
    if s["dense_fallback"]:
        argv_in, ply = dense / "scene.mvs", None
    else:
        argv_in, ply = dense / "scene_dense.mvs", dense / "scene_dense.ply"
    raw = out / "mesh_openmvs.ply"
    rec = openmvs.reconstruct_mesh(ctx.tools, argv_in, ply, raw, out, ctx.logs,
                                   target_faces=ctx.cfg["mesh"]["target_faces"], threads=mvs_threads(),
                                   timeout_s=ctx.cfg["timeouts_s"]["mesh"], cancel_file=ctx.cancel_file) \
        if ply else _mesh_from_sparse(ctx, argv_in, out, raw)
    if not raw.exists() or _ply_vertex_count(raw) == 0:
        raise StageError("mesh", "No surface could be built from the reconstructed points.",
                         "The capture did not give enough 3D detail; see the quality warnings and capture again.",
                         log=rec.get("log"))
    result = {"wall_s": rec["wall_s"]}
    ef = ctx.cfg["mesh"].get("clean_edge_factor")
    if ef:
        # remove gap-bridging faces and floating fragments (no photo supports them); see namma.recon.meshclean
        result["clean"] = meshclean.clean_mesh_file(raw, out / "mesh.ply", edge_factor=ef,
                                                    min_component_share=ctx.cfg["mesh"].get("clean_min_component_share", 0.002))
    else:
        shutil.copy2(raw, out / "mesh.ply")
    return result


def _mesh_from_sparse(ctx, scene_mvs, out, dst):
    from namma.recon.runner import run_step
    argv = [ctx.tools.mvs("ReconstructMesh"), "-i", scene_mvs, "-o", dst, "-w", out,
            "--max-threads", mvs_threads()]
    return run_step("openmvs_reconstruct_mesh_sparse", argv, ctx.logs, timeout_s=ctx.cfg["timeouts_s"]["mesh"],
                    cwd=out, cancel_file=ctx.cancel_file)


def stage_texture(ctx):
    out = ctx.dir("texture")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    s = _dense_state(ctx)
    scene = ctx.dir("dense") / ("scene.mvs" if s["dense_fallback"] else "scene_dense.mvs")
    balance = _balance_exposure(ctx)
    lods = []
    for t in ctx.cfg["textures"]:
        ctx.check_cancel()
        ctx.set_progress("texture", f"Texture level {t['lod']}", t["lod"] / max(1, len(ctx.cfg["textures"])))
        raw = out / f"lod{t['lod']}_openmvs.glb"
        glb = out / f"lod{t['lod']}.glb"
        # -w must be the folder the .mvs was written in: it stores image paths relative to it
        rec = openmvs.texture_mesh(ctx.tools, scene, ctx.dir("mesh") / "mesh.ply", raw, ctx.dir("dense"), ctx.logs,
                                   resolution_level=t["resolution_level"], decimate=t["decimate"],
                                   max_texture_size=t["max_texture_size"], threads=mvs_threads(),
                                   timeout_s=ctx.cfg["timeouts_s"]["texture"], cancel_file=ctx.cancel_file,
                                   step=f"openmvs_texture_lod{t['lod']}", seam_leveling=t.get("seam_leveling", False))
        if not raw.exists():
            raise StageError("texture", f"TextureMesh did not write {raw.name}", log=rec["log"])
        # OpenMVS references its textures as external PNGs: embed them (JPEG) so the GLB is self-contained
        emb = glb_tools.embed_textures(raw, glb, max_side=t.get("embed_max_side"), jpeg_quality=t.get("jpeg_quality", 90))
        if not emb["textures"]:
            raise StageError("texture", f"{raw.name} has no textures to embed", log=rec["log"])
        emb["harmonize"] = _blend_texture(ctx, glb) if (ctx.cfg.get("blend") or {}).get("enabled") else _harmonize_seams(ctx, glb)
        lods.append({"lod": t["lod"], "file": glb.name, "bytes": glb.stat().st_size, "wall_s": rec["wall_s"],
                     "triangles": emb["triangles"], "textures": emb["textures"], "harmonize": emb["harmonize"]})
    write_json(out / "texture_summary.json", {"lods": lods, "exposure": balance})
    return {"lods": lods, "exposure": balance}


def _blend_texture(ctx, glb):
    """Recompute every texel from all photos that see it (namma.recon.blendtex); never fails the run."""
    cfg = ctx.cfg.get("blend") or {}
    try:
        ctx.set_progress("texture", "Blending all photos into the texture", None)
        _, txt_dir, _ = _sfm_model(ctx)
        imgs = colmap.read_images_txt(txt_dir / "images.txt")
        gains, gstats = exposure.estimate_gains(exposure.read_observations(txt_dir / "images.txt"), ctx.dir("prepare"),
                                                sorted(imgs), max_gain=(ctx.cfg.get("exposure") or {}).get("max_gain", 2.0))
        originals = ctx.project_dir / "capture" / "originals" if cfg.get("use_originals") else None
        views = blendtex.make_views(imgs, colmap.read_cameras_txt(txt_dir / "cameras.txt"), ctx.dir("prepare"), gains,
                                    originals_dir=originals)
        total = len(views)
        tmp = glb.with_name(glb.stem + "_blend.glb")

        def log(msg):
            if "photos blended" in msg:
                done = int(msg.split("/")[0].split()[-1])
                part = msg.split(":")[0].strip()
                # the blend is most of the texture stage on CPU (Phase 13 + HP Victus runs): let the bar move with it
                ctx.set_progress("texture", f"Blending all photos into the texture, {part} ({done}/{total})",
                                 0.3 + 0.65 * done / max(1, total))
        st = blendtex.blend_texture_glb(glb, tmp, views, power=cfg.get("power", 4), scale=cfg.get("scale", 1),
                                        max_side=cfg.get("max_side", 8192), log=log)
        tmp.replace(glb)
        return {"status": "ok", "method": "multi-view blend", "exposure": gstats, **st}
    except Exception as e:
        return {"status": "error", "method": "multi-view blend", "reason": f"{e.__class__.__name__}: {e}"}


def _harmonize_seams(ctx, glb):
    """Even out colour / brightness between texture patches (namma.recon.harmonize); never fails the run."""
    cfg = ctx.cfg.get("harmonize") or {}
    if not cfg.get("enabled"):
        return {"status": "disabled"}
    try:
        ctx.set_progress("texture", "Smoothing colour seams between photos", None)
        _, txt_dir, _ = _sfm_model(ctx)
        gf = ctx.dir("texture") / "exposure_gains.json"
        gains = load_strict(gf)["gains"] if gf.exists() else None
        if not hasattr(ctx, "_harm_views"):
            ctx._harm_views = harmonize.load_views(colmap.read_images_txt(txt_dir / "images.txt"),
                                                   colmap.read_cameras_txt(txt_dir / "cameras.txt"), ctx.dir("prepare"), gains)
        tmp = glb.with_name(glb.stem + "_harm.glb")
        st = harmonize.harmonize_glb(glb, tmp, ctx._harm_views, max_gain=cfg.get("max_gain", 1.8))
        tmp.replace(glb)
        return {"status": "ok", **st}
    except Exception as e:
        return {"status": "error", "reason": f"{e.__class__.__name__}: {e}"}


def _balance_exposure(ctx):
    """Equalise the photos' exposure before texturing (namma.recon.exposure); never fails the run."""
    cfg = ctx.cfg.get("exposure") or {}
    und = ctx.dir("undistort") / "images"
    backup = ctx.dir("undistort") / "images_original"
    if not cfg.get("enabled"):
        if backup.exists():                       # a previous attempt balanced them: restore the originals
            for f in backup.iterdir():
                shutil.copy2(f, und / f.name)
        return {"status": "disabled"}
    try:
        ctx.set_progress("texture", "Matching the brightness of the photos", 0.0)
        _, txt_dir, _ = _sfm_model(ctx)
        obs = exposure.read_observations(txt_dir / "images.txt")
        names = sorted(f.name for f in und.iterdir() if f.is_file())
        gains, stats = exposure.estimate_gains(obs, ctx.dir("prepare"), names, max_gain=cfg.get("max_gain", 2.0))
        exposure.apply_gains(und, gains, backup)
        write_json(ctx.dir("texture") / "exposure_gains.json", {"stats": stats, "gains": gains})
        return {"status": "ok", **stats}
    except Exception as e:                       # better unbalanced textures than no model
        return {"status": "error", "reason": f"{e.__class__.__name__}: {e}"}


def stage_frame(ctx):
    out = ctx.dir("frame")
    out.mkdir(parents=True, exist_ok=True)
    _, txt_dir, _ = _sfm_model(ctx)
    imgs = colmap.read_images_txt(txt_dir / "images.txt")
    cams_info = colmap.read_cameras_txt(txt_dir / "cameras.txt")
    s = _dense_state(ctx)
    if s["dense_fallback"]:
        pts, _, _, _ = colmap.read_points3d_txt(txt_dir / "points3D.txt")
        source = "sparse_points"
    else:
        v = read_ply(ctx.dir("dense") / "scene_dense.ply")["vertex"]
        pts = np.column_stack([v["x"], v["y"], v["z"]]).astype(np.float64)
        source = "dense_points"
    names = sorted(imgs)
    centres = np.array([imgs[n]["C"] for n in names])
    rots = [imgs[n]["R"] for n in names]
    # sanity gate: a valid indoor reconstruction spreads its points over a region comparable to the camera layout;
    # Phase 13 (ARKit fast-sweep video): 90 % of 1.09 M dense points within 0.0003 units of one spot while the
    # cameras spanned ~10 units, and camera poses were 0.45 m (median) off the ARKit trajectory
    consistent, ratio = modelframe.geometry_consistency(pts, centres)
    if not consistent:
        raise StageError("frame", "The reconstruction is geometrically inconsistent (the 3D points collapse while the "
                                  "cameras spread out), so it cannot be shown as a model.",
                         "Typical for fast camera sweeps or turning on the spot. Capture again moving slowly along the "
                         "walls (photos work better than video), keeping the floor and furniture in view.")
    T, meta = modelframe.model_frame(pts, centres, rots)
    pm = modelframe.transform_points(T, pts)
    cm = modelframe.transform_points(T, centres)
    eye = meta["eye_height_units"]
    if not np.isfinite(eye) or eye <= 0:
        raise StageError("frame", "Could not determine the camera height above the floor.")
    # proportions check: a room is a few to ~30 eye heights across. Far more means the camera positions are not
    # trustworthy (first own phone capture, 2026-10-21: turning on the spot -> scene ~90 eye heights wide, cameras
    # spread over 30+ units, 44 % of the points "below the floor")
    span = float(max(np.ptp(np.percentile(pm[:, 0], [0.5, 99.5])), np.ptp(np.percentile(pm[:, 2], [0.5, 99.5])))) / eye
    if span > 60:
        raise StageError("frame", f"The camera positions could not be worked out reliably: the model came out about "
                                  f"{span:.0f} times wider than the camera height (a room is 3-30 times), and "
                                  f"{meta['below_floor_fraction']:.0%} of it lies below the detected floor.",
                         "This happens when the phone turns on the spot instead of moving. Capture again WALKING slowly "
                         "around the room along the walls, so every spot is seen from several positions.")
    roam_cfg = ctx.cfg["roam"]
    grid, gstats, masks = modelframe.roam_grid(pm, cm, eye, cells_per_eye=roam_cfg["cells_per_eye"],
                                               band=tuple(roam_cfg["band"]), min_pts=roam_cfg["min_pts"],
                                               min_obstacle_eye=roam_cfg.get("min_obstacle_eye", 0.12))
    np.save(out / "points_model_sample.npy", pm[np.random.default_rng(0).choice(len(pm), min(len(pm), 400_000),
                                                                                  replace=False)].astype(np.float32))
    cameras = []
    for n, c in zip(names, cm):
        fwd, up, _ = modelframe.camera_axes(imgs[n]["R"])
        cam = cams_info[imgs[n]["camera_id"]]
        f = cam["params"][0]
        cameras.append({"name": n, "position": [round(float(v), 5) for v in c],
                        "forward": [round(float(v), 5) for v in modelframe.transform_dirs(T, fwd)],
                        "up": [round(float(v), 5) for v in modelframe.transform_dirs(T, up)],
                        "vfov_deg": round(float(np.degrees(2 * np.arctan(cam["height"] / (2 * f)))), 2),
                        "n_triangulated": imgs[n]["n_triangulated"]})
    start = choose_start(cameras, pm, eye)
    floor_cov = gstats["observed_floor_in_footprint_cells"] / max(1, gstats["footprint_cells"])
    lo, hi = pm.min(0), pm.max(0)
    p1, p99 = np.percentile(pm, 1, axis=0), np.percentile(pm, 99, axis=0)
    frame = {"T_model_from_sfm": T.tolist(), "eye_height_units": eye, "points_source": source,
             "points": int(len(pm)), "bounds_p1_p99": [p1.tolist(), p99.tolist()], "bounds_minmax": [lo.tolist(), hi.tolist()],
             "floor_detected": meta["floor_detected"], "up_source": meta["up_source"],
             "floor_suspect": meta["floor_suspect"], "below_floor_fraction": meta["below_floor_fraction"],
             "camera_height_p10_p90_units": meta["camera_height_p10_p90_units"], "diagnostics": meta["diagnostics"],
             "roam_stats": gstats, "floor_coverage": round(floor_cov, 4), "start": start}
    write_json(out / "frame.json", frame)
    write_json(out / "cameras.json", {"cameras": cameras})
    write_json(out / "roam_grid.json", grid)
    np.save(out / "floor_observed.npy", masks["floor_observed"])
    ctx.facts.update(floor_detected=meta["floor_detected"], floor_coverage=round(floor_cov, 4))
    fill = _estimated_surfaces(ctx, None if s["dense_fallback"] else v, T, eye, meta, txt_dir, out)
    return {"floor_detected": meta["floor_detected"], "eye_height_units": eye, "floor_coverage": floor_cov,
            "estimated_surfaces": fill}


def _estimated_surfaces(ctx, v, T, eye, meta, txt_dir, out):
    """Estimated flat surfaces for holes (namma.recon.roomfill). Never fails the run: problems are recorded."""
    cfg = ctx.cfg.get("fill") or {}
    res = {"status": "skipped"}
    try:
        if not cfg.get("enabled"):
            res["reason"] = "disabled"
        elif v is None or "nx" not in v:
            res["reason"] = "no dense points with normals"
        elif not meta.get("floor_detected") or meta.get("floor_suspect"):
            res["reason"] = "no trustworthy floor plane"
        else:
            T = np.asarray(T, float)
            P = roomfill.to_model(np.column_stack([v["x"], v["y"], v["z"]]).astype(np.float64), T)
            N = np.column_stack([v["nx"], v["ny"], v["nz"]]) @ T[:3, :3].T
            N /= np.linalg.norm(N, axis=1, keepdims=True) + 1e-12
            C = np.column_stack([v["red"], v["green"], v["blue"]]) / 255.0
            box = roomfill.fit_room_box(P, N, eye)
            fit = {k: box[k] for k in ("near_wall_share", "outside_share", "height", "u", "v", "theta")}
            if box["near_wall_share"] < cfg["min_fit_near_wall"] or box["outside_share"] > cfg["max_outside_share"]:
                res.update(reason="the points do not look like one box-shaped room", fit=fit)
            else:
                ctx.set_progress("frame", "Estimating missing walls and floor", 0.5)
                imgs = colmap.read_images_txt(txt_dir / "images.txt")
                cams = colmap.read_cameras_txt(txt_dir / "cameras.txt")
                if (ctx.cfg.get("blend") or {}).get("enabled"):
                    gains, _ = exposure.estimate_gains(exposure.read_observations(txt_dir / "images.txt"), ctx.dir("prepare"), sorted(imgs))
                    mesh_v = read_ply(ctx.dir("mesh") / "mesh.ply")["vertex"]
                    sampler = roomfill.BlendSampler(blendtex.make_views(imgs, cams, ctx.dir("prepare"), gains),
                                                    np.column_stack([mesh_v["x"], mesh_v["y"], mesh_v["z"]]), T)
                else:
                    sampler = roomfill.PhotoSampler(imgs, cams, ctx.dir("prepare"), T)
                V, Cc, F, planes = roomfill.build_fill(P, C, box, N=N, photo_sampler=sampler, cell_eye=cfg["cell_eye"],
                                                        slab_eye=cfg["slab_eye"], min_pts=cfg["min_pts"],
                                                        offset_eye=cfg.get("offset_eye", 0.01))
                roomfill.write_fill_glb(out / "fill.glb", V, Cc, F)
                res = {"status": "ok", "triangles": int(len(F)), "fit": fit, "planes": planes}
    except Exception as e:                  # an estimate must never break a reconstruction
        res = {"status": "error", "reason": f"{e.__class__.__name__}: {e}"}
    if res["status"] != "ok":
        (out / "fill.glb").unlink(missing_ok=True)
    write_json(out / "fill.json", res)
    return {k: res.get(k) for k in ("status", "reason", "triangles")}


def choose_start(cameras, pts, eye):
    """Capture viewpoint that overlooks most of the scene: near-horizontal, farthest median view depth."""
    best, best_score = None, -1.0
    sample = pts[:: max(1, len(pts) // 50_000)]
    for c in cameras:
        f = np.array(c["forward"])
        if abs(f[1]) > 0.6:          # looking steeply up / down
            continue
        rel = sample - np.array(c["position"])
        depth = rel @ f
        dist = np.linalg.norm(rel, axis=1) + 1e-9
        visible = (depth > 0.2 * eye) & (depth / dist > np.cos(np.radians(35)))
        if visible.sum() < 50:
            continue
        score = float(np.median(depth[visible])) * np.sqrt(visible.mean())
        if score > best_score:
            best, best_score = c, score
    if best is None:
        best = cameras[0]
    pos = np.array(best["position"])
    fwd = np.array(best["forward"])
    return {"camera": best["name"], "position": pos.round(5).tolist(), "target": (pos + fwd * eye).round(5).tolist()}


def stage_quality(ctx):
    out = ctx.dir("quality")
    out.mkdir(parents=True, exist_ok=True)
    mesh = read_ply(ctx.dir("mesh") / "mesh.ply")
    nv = len(mesh["vertex"]["x"]) if mesh["vertex"] is not None else 0
    faces = mesh["faces"] if mesh["faces"] is not None else np.zeros((0, 3), int)
    comps, largest, tiny = quality.mesh_components(faces, nv)
    _dense_state(ctx)
    sfm = load_strict(ctx.dir("sfm") / "sfm_summary.json")
    frame = load_strict(ctx.dir("frame") / "frame.json")
    prep = load_strict(ctx.dir("prepare") / "images.json")
    scale = current_scale(ctx)
    facts = {"images_input": len(prep["images"]), "images_registered": sfm["registered"],
             "unregistered": sfm["unregistered"], "other_models": sfm["other_model_images"],
             "sparse_points": sfm["sparse_points"], "mean_reprojection_px": sfm["mean_reprojection_px"],
             "sift_max_features": sfm.get("sift_max_features"),
             "mean_track_length": sfm["mean_track_length"],
             "low_texture_images": [n for n, k in sfm["keypoints"].items() if k < quality.THRESHOLDS["low_texture_keypoints"]],
             "dense_fallback": ctx.facts.get("dense_fallback", False),
             "dense_fallback_reason": ctx.facts.get("dense_fallback_reason"),
             "floor_detected": frame["floor_detected"], "floor_coverage": frame["floor_coverage"],
             "floor_suspect": frame.get("floor_suspect", False), "below_floor_fraction": frame.get("below_floor_fraction"),
             "mesh_vertices": nv, "mesh_faces": int(len(faces)), "mesh_components": comps,
             "mesh_largest_component_share": round(largest, 4), "mesh_tiny_components": tiny,
             "dense_points": frame["points"] if frame["points_source"] == "dense_points" else 0,
             "scale_status": scale["status"]}
    verdict, warnings = quality.assess(facts)
    report = {"verdict": verdict, "warnings": warnings, "facts": facts, "thresholds": quality.THRESHOLDS,
              "registration_rate": round(facts["images_registered"] / max(1, facts["images_input"]), 4),
              "created_at": timefmt.now_iso()}
    write_json(out / "quality_report.json", report)
    return {"verdict": verdict, "warnings": len(warnings)}


def current_scale(ctx):
    """Scale basis for this project. Order: user calibration > none (unknown)."""
    p = ctx.project_dir / "calibration.json"
    if p.exists():
        cal = load_strict(p)
        if cal.get("run_id") == ctx.run_id and cal.get("metres_per_unit"):
            return {"status": cal.get("status", "user_distance"), "metres_per_unit": cal["metres_per_unit"],
                    "relative_uncertainty": cal.get("relative_uncertainty"), "method": cal.get("method", ""),
                    "evaluation_only": bool(cal.get("evaluation_only", False))}
    return {"status": "unknown", "metres_per_unit": None, "relative_uncertainty": None,
            "method": "photos only: scale not observable", "evaluation_only": False}


def nav_scale(ctx, frame):
    """Scale used for navigation geometry: calibrated if available, else ASSUMED capture height."""
    s = current_scale(ctx)
    if s["metres_per_unit"]:
        return s["metres_per_unit"], s["status"], s.get("relative_uncertainty")
    n = ctx.cfg["nav"]
    return (n["assumed_capture_height_m"] / frame["eye_height_units"], "assumed_capture_height",
            n["assumed_capture_height_rel_uncertainty"])


def stage_navgeom(ctx):
    from namma.recon.floorplan import extract_if04
    out = ctx.dir("navgeom")
    out.mkdir(parents=True, exist_ok=True)
    frame = load_strict(ctx.dir("frame") / "frame.json")
    if not frame["floor_detected"]:
        write_json(out / "navgeom.json", {"status": "unavailable", "reason": "no floor plane detected"})
        return {"status": "unavailable"}
    if frame.get("floor_suspect"):
        write_json(out / "navgeom.json", {"status": "unavailable",
                                          "reason": f"the detected floor is probably a table or other raised surface "
                                                    f"({frame['below_floor_fraction']:.0%} of the points lie below it)"})
        return {"status": "unavailable"}
    mpu, basis, unc = nav_scale(ctx, frame)
    pm = np.load(ctx.dir("frame") / "points_model_sample.npy").astype(np.float64) * mpu
    n = ctx.cfg["nav"]
    prov = {"origin": "automatic", "method": f"namma.recon.floorplan.extract_if04 on {frame['points_source']}; "
            f"scale basis {basis} ({mpu:.6f} m per model unit)", "tool": "namma-studio",
            "tool_version": ctx.cfg["pipeline_version"], "author_role": "SCRIPT", "created_at": timefmt.now_iso(),
            "note": "UNVERIFIED automatic geometry (ARCH-030): unobserved floor is not walkable"}
    frame_doc = {"frame_id": f"site:{ctx.run_id}", "units": "m", "handedness": "right", "up_axis": "+Y",
                 "datum": f"model frame of run {ctx.run_id} scaled by {basis}; origin on the floor below the cameras"}
    doc, diag = extract_if04(pm, geometry_id=f"geom-{ctx.run_id}", run_id=ctx.run_id, frame=frame_doc,
                             provenance=prov, res=n["res_m"], floor_tol=n["floor_tol_m"],
                             obstacle_min_pts=n["obstacle_min_pts"])
    write_json(out / "geometry.if04.json", doc)
    errs = errors_only(validate_document(doc, "IF-04"))
    status = "unverified" if not errs and doc["floors"][0]["walkable_regions"] else "unavailable"
    write_json(out / "navgeom.json", {"status": status, "scale_basis": basis, "metres_per_unit": mpu,
                                      "relative_uncertainty": unc, "diagnostics": diag,
                                      "if04_errors": [e.message for e in errs][:5],
                                      "note": "Routes on this geometry are never shown as validated: no independent "
                                              "reference and no human verification (ARCH-030)."})
    return {"status": status, "walkable_m2": diag["walkable_area_m2"], "obstacles": diag["obstacles"]}


def stage_package(ctx):
    from namma.studio.package import build_scene_package
    out = ctx.dir("package")
    res = build_scene_package(ctx, out)
    log = RunLog(ctx.run_dir, ctx.run_id)
    log.append("S8", "passed", DIRS["package"], note=f"IF-13 scene package {res['package_id']}; "
               f"{res['files']} files; quality {res['verdict']}")
    return res


STAGE_FUNCS = {"prepare": stage_prepare, "sfm": stage_sfm, "undistort": stage_undistort, "dense": stage_dense,
               "mesh": stage_mesh, "texture": stage_texture, "frame": stage_frame, "quality": stage_quality,
               "navgeom": stage_navgeom, "package": stage_package}
ALWAYS_RERUN = {"quality", "navgeom", "package"}   # cheap, and depend on project files (calibration, POIs)


def run_pipeline(project_dir, run_id, cfg, *, stop_after=None):
    ctx = RunContext(project_dir, run_id, cfg)
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    ctx.load_status()
    if ctx.status.get("config_sha256") != ctx.cfg_sha:
        raise RuntimeError("this run was created with a different configuration; start a new run")
    ctx.cancel_file.unlink(missing_ok=True)
    ctx.status.update(status="running", error=None, started_at=ctx.status.get("started_at") or timefmt.now_iso(),
                      attempts=ctx.status.get("attempts", 0) + 1, pid=os.getpid())
    ctx.save_status()
    write_json(ctx.run_dir / "config.json", {"config": cfg, "config_sha256": ctx.cfg_sha})
    log = RunLog(ctx.run_dir, run_id)
    try:
        check_path_lengths(ctx)
        ctx.tools = find_tools()
        ctx.status["tools"] = ctx.tools.describe()
        ctx.status["resources"] = {**hardware.resources().as_dict(), "ram_at_start": hardware.ram_snapshot()}
        for name in STAGES:
            entry = ctx.stage_entry(name)
            marker = ctx.dir(name) / "stage.json"
            if name not in ALWAYS_RERUN and entry["status"] == "done" and marker.exists() \
                    and load_strict(marker).get("config_sha256") == ctx.cfg_sha:
                try:
                    _restore_facts(ctx, name)
                    continue
                except (OSError, ValueError, KeyError):
                    entry["status"] = "pending"          # outputs missing / damaged: run the stage again
            ctx.check_cancel()
            entry.update(status="running", started_at=timefmt.now_iso())
            ctx.set_progress(name, None, 0.0)
            t0 = time.perf_counter()
            result = STAGE_FUNCS[name](ctx)
            entry.update(status="done", ended_at=timefmt.now_iso(), seconds=round(time.perf_counter() - t0, 1),
                         result=result)
            write_json(marker, {"stage": name, "config_sha256": ctx.cfg_sha, "at": timefmt.now_iso(), "result": result})
            ctx.save_status()
            if name == "texture":
                log.append("S4", "fallback" if ctx.facts.get("dense_fallback") else "passed", DIRS["texture"],
                           note="CPU photogrammetry (COLMAP + OpenMVS)"
                                + ("; dense stage failed, sparse-point mesh used" if ctx.facts.get("dense_fallback") else ""))
            if stop_after == name:
                break
        ctx.status.update(status="succeeded", ended_at=timefmt.now_iso(), progress=1.0, current=None)
    except Cancelled:
        _mark_failed(ctx, "cancelled", None, "Processing was cancelled.", "Press Resume to continue from this step.")
    except (StageError, StepFailed) as e:
        stage = next((s["name"] for s in ctx.status["stages"] if s["status"] == "running"), "unknown")
        if isinstance(e, StepFailed) and e.record.get("status") == "cancelled":
            _mark_failed(ctx, "cancelled", stage, "Processing was cancelled.", "Press Resume to continue.")
        else:
            msg, hint = (str(e), e.hint) if isinstance(e, StageError) and e.hint else friendly(stage, e)
            _mark_failed(ctx, "failed", stage, msg, hint, log=getattr(e, "record", {}) and e.record.get("log"))
            log.append("S4" if stage in ("prepare", "sfm", "undistort", "dense", "mesh", "texture") else "S8",
                       "failed", DIRS.get(stage, "."), errors=[msg[:300]])
    except Exception as e:          # unexpected: keep the traceback for diagnosis
        stage = next((s["name"] for s in ctx.status["stages"] if s["status"] == "running"), "unknown")
        (ctx.logs).mkdir(parents=True, exist_ok=True)
        (ctx.logs / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        _mark_failed(ctx, "failed", stage, f"Unexpected error in {LABELS.get(stage, stage)}: {e}",
                     "Open logs/traceback.txt; retrying resumes from this step.", log="traceback.txt")
    ctx.save_status()
    return ctx.status


def _mark_failed(ctx, status, stage, message, hint, log=None):
    if stage:
        e = ctx.stage_entry(stage) if stage in STAGES else None
        if e is not None:
            e.update(status="failed" if status == "failed" else "cancelled", ended_at=timefmt.now_iso())
    ctx.status.update(status=status, ended_at=timefmt.now_iso(), current=None,
                      error={"stage": stage, "message": message, "hint": hint, "log": log})


def _restore_facts(ctx, name):
    d = ctx.dir(name)
    if name == "prepare":
        m = load_strict(d / "images.json")
        cm = load_strict(ctx.project_dir / "capture" / "capture_manifest.json")
        ctx.facts.update(images_input=len(m["images"]), single_camera=m["single_camera"], mode=cm["mode"])
    elif name == "dense":
        _dense_state(ctx)
