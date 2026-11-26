"""Camera poses for a Polycam LiDAR scan (implementation_plan.md, Phase 2).

Starts from Polycam's corrected poses instead of solving camera placement from scratch (which misplaced a whole
room on the NYC dataset), then refines them with COLMAP:
  1. SIFT on the corrected (undistorted) photos, one fixed pinhole camera (Polycam's intrinsics, never refined);
  2. matching only on chosen pairs: Polycam's `neighbors`, the capture sequence, and revisits - photos far apart in
     time that look at the same place (Phase 0: that is where the corrected poses are weakest);
  3. point_triangulator with the poses held fixed, then bundle_adjuster (poses + points, intrinsics fixed);
  4. a trimmed similarity fit brings the result back into the corrected frame (scale stays Polycam's).
The LiDAR depth - which COLMAP never sees - is the judge: depth agreement between linked photos and between
revisits, for the corrected, the refined and (as a reference only) the phone's raw ARKit poses. The refined poses
are used only if they do better on revisits and no worse between neighbours; otherwise the corrected ones are kept.
Because the GLB was built from the corrected poses, the GLB transform is re-fitted (ICP of the depth) for the
chosen poses.

Writes <project>/capture/poses/: poses.json (what later phases read), report.json, model/ (COLMAP text model of
the chosen poses with triangulated points), logs/; work/ (photo copies, ~300 MB feature database) is deleted unless
keep_work. Nothing else is touched. Not used by the photoreal view (Phase 3: the phone's raw poses scored best
there); used by the LiDAR fusion check when present.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

import numpy as np

from namma.capture import polycam_raw as pr
from namma.capture import polycam_report as rpt
from namma.core.jsonio import write_json

SEQ_WINDOW = 8                       # pairs with the next N keyframes
REVISIT_GAP = 60                     # keyframes apart to count as a revisit (as in the report)
REVISIT_MAX_DIST_M = 1.5
REVISIT_MIN_DOT = 0.5
MAX_REVISITS_PER_IMAGE = 12
MAX_FEATURES = 8192
GATE_REVISIT_GAIN = 0.005            # refined poses must add >= 0.5 points of revisit agreement ...
GATE_NEIGHBOUR_LOSS = 0.003          # ... and lose <= 0.3 points between neighbours
EVAL_NEIGHBOURS, EVAL_REVISITS = 150, 120
MAX_MOVE_M, MAX_TURN_DEG = 0.05, 2.0  # per camera: moved further than this (and 4x the median) = keep Polycam's pose
SEED = 0


# ------------------------------------------------------------------------------------------------ helpers
def _quat(R):
    """Rotation matrix -> unit (w, x, y, z) with w >= 0 (the shared conversion in splat_claude, normalised)."""
    from namma.studio.splat_claude import _rot_to_quat
    q = _rot_to_quat(R)
    return q / np.linalg.norm(q) * (1 if q[0] >= 0 else -1)


def arkit_c2w_to_colmap(T):
    """ARKit camera->world -> COLMAP world->camera (R, t) in OpenCV axes."""
    Tc = pr.opencv_c2w(T)
    R = Tc[:3, :3].T
    return R, -R @ Tc[:3, 3]


def colmap_to_arkit_c2w(R, t):
    T = np.eye(4)
    T[:3, :3] = R.T @ pr.ARKIT_TO_OPENCV
    T[:3, 3] = -R.T @ t
    return T


def similarity(src, dst, trim=0.8, rounds=5):
    """Trimmed Umeyama: dst ~ s R src + t, the worst (1 - trim) left out each round -> (s, R, t, residuals)."""
    from namma.recon.align import umeyama
    keep = np.ones(len(src), bool)
    for _ in range(rounds):
        s, R, t = umeyama(src[keep], dst[keep])
        res = np.linalg.norm(s * src @ R.T + t - dst, axis=1)
        keep = res <= np.percentile(res, 100 * trim)
    s, R, t = umeyama(src[keep], dst[keep])
    return s, R, t, np.linalg.norm(s * src @ R.T + t - dst, axis=1)


def apply_similarity(T, s, R, t):
    """Camera->world pose T moved by world' = s R world + t (rotation part stays a rotation)."""
    out = np.eye(4)
    out[:3, :3] = R @ T[:3, :3]
    out[:3, 3] = s * R @ T[:3, 3] + t
    return out


def choose_pairs(ex):
    """Image pairs to match: Polycam neighbours, the next SEQ_WINDOW keyframes, and revisits."""
    ids = ex.ids
    idx = {k: i for i, k in enumerate(ids)}
    pairs = set()
    for a in ids:
        for b in ex.neighbors(a):
            if b in idx and b != a:
                pairs.add(tuple(sorted((idx[a], idx[b]))))
    for i in range(len(ids)):
        for j in range(i + 1, min(len(ids), i + 1 + SEQ_WINDOW)):
            pairs.add((i, j))
    n_local = len(pairs)
    C = np.array([ex.pose(k)[:3, 3] for k in ids])
    D = np.array([-ex.pose(k)[:3, 2] for k in ids])
    rev = 0
    for i in range(len(ids)):
        far = np.arange(len(ids))
        far = far[np.abs(far - i) >= REVISIT_GAP]
        if not len(far):
            continue
        dist = np.linalg.norm(C[far] - C[i], axis=1)
        dot = D[far] @ D[i]
        ok = (dist <= REVISIT_MAX_DIST_M) & (dot >= REVISIT_MIN_DOT)
        cand = far[ok][np.argsort(dist[ok] - dot[ok])][:MAX_REVISITS_PER_IMAGE]
        for j in cand:
            p = tuple(sorted((i, int(j))))
            if p not in pairs:
                pairs.add(p)
                rev += 1
    return sorted(pairs), {"local": n_local, "revisit": rev, "total": len(pairs)}


def _db_images(db):
    con = sqlite3.connect(str(db))
    try:
        rows = con.execute("SELECT image_id, name, camera_id FROM images").fetchall()
    finally:
        con.close()
    return {name: (int(iid), int(cid)) for iid, name, cid in rows}


def _set_db_cameras(db, db_ids, intr):
    """Give every image's camera in the COLMAP database its own known pinhole intrinsics (the phone's autofocus
    changes the focal length by up to 3.5 % between photos; one shared camera would bend the solution)."""
    con = sqlite3.connect(str(db))
    try:
        for k, c in intr.items():
            _, cid = db_ids[f"{k}.jpg"]
            params = np.array([c["fx"], c["fy"], c["cx"], c["cy"]], np.float64).tobytes()
            con.execute("UPDATE cameras SET model=1, width=?, height=?, params=?, prior_focal_length=1 WHERE camera_id=?",
                        (int(c["width"]), int(c["height"]), params, cid))
        con.commit()
    finally:
        con.close()


def _write_model(folder, intr, db_ids, poses):
    """COLMAP text model with known poses and no points. intr: keyframe id -> intrinsics (one camera per photo)."""
    folder.mkdir(parents=True, exist_ok=True)
    cams = {}
    for k in poses:
        _, cid = db_ids[f"{k}.jpg"]
        c = intr[k]
        cams[cid] = f"{cid} PINHOLE {c['width']} {c['height']} {c['fx']} {c['fy']} {c['cx']} {c['cy']}\n"
    (folder / "cameras.txt").write_text("".join(cams[c] for c in sorted(cams)), encoding="utf-8")
    lines = []
    for k, T in poses.items():
        iid, cid = db_ids[f"{k}.jpg"]
        R, t = arkit_c2w_to_colmap(T)
        q = _quat(R)
        lines.append(f"{iid} {' '.join(f'{v:.12g}' for v in q)} {' '.join(f'{v:.12g}' for v in t)} {cid} {k}.jpg\n\n")
    (folder / "images.txt").write_text("".join(lines), encoding="utf-8")
    (folder / "points3D.txt").write_text("", encoding="utf-8")


def _read_model(txt):
    from namma.recon.colmap import read_images_txt, read_points3d_txt
    imgs = read_images_txt(txt / "images.txt")
    xyz, _, err, track = read_points3d_txt(txt / "points3D.txt")
    poses = {Path(n).stem: colmap_to_arkit_c2w(v["R"], v["t"]) for n, v in imgs.items()}
    return poses, {"points": int(len(xyz)), "reproj_error_px": rpt._pct(err, (50, 90)),
                   "track_length": rpt._pct(track, (50, 90))}


# ------------------------------------------------------------------------------------------------ evaluation
def evaluate(ex, poses_by_name, pairs):
    """LiDAR depth agreement for each pose set on the same pairs (COLMAP never saw the depth)."""
    out = {}
    for name, poses in poses_by_name.items():
        fr = rpt.Frames(ex, poses)
        nb, rv = rpt.pair_agreement(fr, pairs["neighbours"]), rpt.pair_agreement(fr, pairs["revisits"])
        out[name] = {"neighbours": nb, "revisits": rv}
    return out


def refit_glb(ex, poses, glb, M, n_frames=80):
    """ICP of the depth (with these poses) onto the GLB, starting from alignmentTransform -> (T, fit before, after).
    Uses the same ICP as the splat line-up (namma.studio.splat_align)."""
    from namma.studio.mesh_import import read_glb_bytes
    from namma.studio.splat_align import align_points_to_mesh
    fr = rpt.Frames(ex, poses)
    P = np.vstack([fr.points(k, stride=8) for k in ex.ids[:: max(1, len(ex.ids) // n_frames)]])
    from scipy.spatial import cKDTree
    data = read_glb_bytes(glb)[0]
    d, _ = cKDTree(rpt.glb_surface(glb)).query(P @ M[:3, :3].T + M[:3, 3], workers=-1)
    before = {"within_2cm": round(float(np.mean(d <= 0.02)), 4), "median_m": round(float(np.median(d)), 4)}
    T, after = align_points_to_mesh(P, data, init_T=M, seed=SEED)
    return T, before, {k: after[k] for k in ("within_2cm", "within_5cm", "median_m")}


# ------------------------------------------------------------------------------------------------ main entry
def refine(project_dir, *, progress=None, timeout_s=3600, cancel_file=None, keep_work=False):
    from namma.recon.runner import run_step
    from namma.recon.tools import find_tools
    progress = progress or (lambda *_: None)
    t0 = time.time()
    d = Path(project_dir)
    zp = rpt.find_raw_zip(d)
    if zp is None:
        raise pr.PolycamFormatError("no Polycam LiDAR raw export (Images .zip) in this project")
    orig = d / "capture" / "originals"
    side = pr.load_sidecars(orig)
    glb = next(iter(sorted(orig.glob("*.glb"))), None)
    out = d / "capture" / "poses"
    work = out / "work"
    shutil.rmtree(work, ignore_errors=True)
    (work / "images").mkdir(parents=True)
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    colmap = find_tools(require_openmvs=False).colmap
    kw = {"timeout_s": timeout_s, "cancel_file": cancel_file}
    rep = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "zip": zp.name, "colmap": str(Path(colmap).parent.parent.name)}
    with pr.PolycamExport(zp) as ex:
        progress("Writing the photos", 0.02)
        for k in ex.ids:
            (work / "images" / f"{k}.jpg").write_bytes(ex.photo_bytes(k))
        has_corrected = ex.corrected
        intr = {k: ex.intrinsics(k) for k in ex.ids}
        corrected = {k: ex.pose(k) for k in ex.ids}

        progress("Finding features (COLMAP)", 0.05)
        db = work / "database.db"
        run_step("poses_features", [colmap, "feature_extractor", "--database_path", db, "--image_path", work / "images",
                                    "--ImageReader.camera_model", "PINHOLE", "--ImageReader.single_camera", 0,
                                    "--FeatureExtraction.use_gpu", 1, "--SiftExtraction.max_num_features", MAX_FEATURES],
                 logs, **kw)
        _set_db_cameras(db, _db_images(db), intr)                   # each photo's own known intrinsics
        progress("Matching chosen photo pairs (COLMAP)", 0.2)
        pairs, pair_stats = choose_pairs(ex)
        rep["pairs"] = pair_stats
        (work / "pairs.txt").write_text("".join(f"{ex.ids[i]}.jpg {ex.ids[j]}.jpg\n" for i, j in pairs), encoding="utf-8")
        run_step("poses_matching", [colmap, "matches_importer", "--database_path", db, "--match_list_path",
                                    work / "pairs.txt", "--match_type", "pairs", "--FeatureMatching.use_gpu", 1,
                                    "--FeatureMatching.guided_matching", 1], logs, **kw)

        progress("Triangulating with Polycam's poses fixed (COLMAP)", 0.45)
        _write_model(work / "known", intr, _db_images(db), corrected)
        (work / "tri").mkdir()
        run_step("poses_triangulate", [colmap, "point_triangulator", "--database_path", db, "--image_path",
                                       work / "images", "--input_path", work / "known", "--output_path", work / "tri",
                                       "--clear_points", 1, "--refine_intrinsics", 0], logs, **kw)
        progress("Refining the poses (COLMAP bundle adjustment)", 0.6)
        (work / "ba").mkdir()
        run_step("poses_bundle_adjust", [colmap, "bundle_adjuster", "--input_path", work / "tri", "--output_path",
                                         work / "ba", "--BundleAdjustment.refine_focal_length", 0,
                                         "--BundleAdjustment.refine_principal_point", 0,
                                         "--BundleAdjustment.refine_extra_params", 0,
                                         "--BundleAdjustmentCeres.max_num_iterations", 200], logs, **kw)
        for m in ("tri", "ba"):
            (work / f"{m}_txt").mkdir()
            run_step(f"poses_convert_{m}", [colmap, "model_converter", "--input_path", work / m, "--output_path",
                                            work / f"{m}_txt", "--output_type", "TXT"], logs, timeout_s=600)
        _, tri_stats = _read_model(work / "tri_txt")
        ba_raw, ba_stats = _read_model(work / "ba_txt")
        rep["triangulated"], rep["adjusted"] = tri_stats, ba_stats
        rep["adjusted"]["registered"] = len(ba_raw)

        # back into the corrected frame (bundle adjustment fixes its own gauge)
        names = [k for k in ex.ids if k in ba_raw]
        s, R, t, res = similarity(np.array([ba_raw[k][:3, 3] for k in names]),
                                  np.array([corrected[k][:3, 3] for k in names]))
        refined = dict(corrected)
        for k in names:
            refined[k] = apply_similarity(ba_raw[k], s, R, t)
        moved = np.array([np.linalg.norm(refined[k][:3, 3] - corrected[k][:3, 3]) for k in names])
        turned = np.array([np.degrees(np.arccos(np.clip((np.trace(refined[k][:3, :3].T @ corrected[k][:3, :3]) - 1) / 2,
                                                         -1, 1))) for k in names])
        # a camera the adjustment threw far off (weakly linked photos) keeps Polycam's pose
        wild = (moved > max(MAX_MOVE_M, 4 * np.median(moved))) | (turned > max(MAX_TURN_DEG, 4 * np.median(turned)))
        for k, w in zip(names, wild):
            if w:
                refined[k] = corrected[k]
        rep["refinement"] = {"similarity_scale": round(float(s), 5), "fit_residual_m": rpt._pct(res),
                             "moved_m": rpt._pct(moved, (50, 90, 99)), "turned_deg": rpt._pct(turned, (50, 90, 99)),
                             "kept_polycam_pose": [k for k, w in zip(names, wild) if w]}
        # the phone's raw tracking, as a reference only (Phase 0: better than corrected between distant passes)
        sa, Ra, ta, _ = similarity(np.array([ex.raw_pose(k)[:3, 3] for k in ex.ids]),
                                   np.array([corrected[k][:3, 3] for k in ex.ids]))
        arkit = {k: apply_similarity(ex.raw_pose(k), sa, Ra, ta) for k in ex.ids}

        progress("Judging the poses with the LiDAR depth", 0.75)
        ev_pairs = rpt.choose_pairs(ex, np.random.default_rng(SEED), EVAL_NEIGHBOURS, EVAL_REVISITS)
        ev = evaluate(ex, {"corrected": corrected, "refined": refined, "arkit_reference": arkit}, ev_pairs)
        rep["evaluation"] = ev
        c, r_ = ev["corrected"], ev["refined"]
        gain = (r_["revisits"]["within_5pct"] or 0) - (c["revisits"]["within_5pct"] or 0)
        loss = (c["neighbours"]["within_5pct"] or 0) - (r_["neighbours"]["within_5pct"] or 0)
        use_refined = gain >= GATE_REVISIT_GAIN and loss <= GATE_NEIGHBOUR_LOSS
        chosen = refined if use_refined else corrected
        rep["chosen"] = "refined" if use_refined else "corrected"
        rep["gate"] = {"revisit_gain": round(gain, 4), "neighbour_loss": round(loss, 4),
                       "need_gain": GATE_REVISIT_GAIN, "max_loss": GATE_NEIGHBOUR_LOSS}

        glb_from_frame = None
        if side.get("mesh_info") and glb:
            M = pr.glb_from_camera(side["mesh_info"])
            if use_refined:
                progress("Re-fitting the 3D model to the refined poses", 0.9)
                glb_from_frame, before, after = refit_glb(ex, chosen, glb, M)
                rep["glb_fit"] = {"alignmentTransform": before, "refitted": after}
            else:
                glb_from_frame = M

        # the chosen poses as a COLMAP model with points (for the splat trainer): triangulated with them held fixed,
        # so poses and points agree by construction; read back and compared
        progress("Writing the camera model", 0.95)
        model = out / "model"
        shutil.rmtree(model, ignore_errors=True)
        shutil.rmtree(work / "final", ignore_errors=True)
        _write_model(work / "chosen", intr, _db_images(db), chosen)
        (work / "final").mkdir()
        run_step("poses_final_triangulate", [colmap, "point_triangulator", "--database_path", db, "--image_path",
                                             work / "images", "--input_path", work / "chosen", "--output_path",
                                             work / "final", "--clear_points", 1, "--refine_intrinsics", 0], logs, **kw)
        model.mkdir()
        run_step("poses_final_convert", [colmap, "model_converter", "--input_path", work / "final", "--output_path",
                                         model, "--output_type", "TXT"], logs, timeout_s=600)
        check, final_stats = _read_model(model)
        worst = max(np.abs(check[k] - chosen[k]).max() for k in chosen) if len(check) == len(chosen) else np.inf
        if worst > 1e-5:
            raise RuntimeError(f"saved COLMAP model differs from the chosen poses by {worst:g}")
        rep["model"] = final_stats

    write_json(out / "poses.json", {
        "frame": "polycam_corrected" if has_corrected else "arkit", "axes": "ARKit camera->world (+X right, +Y up, -Z view)", "source": rep["chosen"],
        "glb_from_frame": glb_from_frame.tolist() if glb_from_frame is not None else None,
        "intrinsics": intr, "images": "corrected_images" if has_corrected else "images", "depth": "corrected_depth .Clean" if has_corrected else "depth",
        "poses": {k: np.round(T, 9).tolist() for k, T in chosen.items()}})
    rep["seconds"] = round(time.time() - t0, 1)
    write_json(out / "report.json", rep)
    if not keep_work:                       # photo copies + ~300 MB feature database: model/ and poses.json are the result
        shutil.rmtree(work, ignore_errors=True)
    progress("Done", 1.0)
    return rep


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Polycam LiDAR camera poses (Phase 2)")
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--keep-work", action="store_true")
    a = ap.parse_args(argv)
    rep = refine(a.project, keep_work=a.keep_work, progress=lambda lbl, f: print(f"[{f:4.0%}] {lbl}", flush=True))
    print(json.dumps({k: rep[k] for k in ("pairs", "triangulated", "adjusted", "refinement", "evaluation", "chosen",
                                          "gate", "glb_fit", "model", "seconds") if k in rep}, indent=1))


if __name__ == "__main__":
    main()
