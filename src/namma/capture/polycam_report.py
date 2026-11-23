"""Capture report for a Polycam LiDAR export (implementation_plan.md, Phase 1).

Runs as the Studio's "Check the capture" step for Polycam LiDAR projects (studio worker `ingest`) and after a
Polycam import. Read-only on the capture; writes into <project>/capture/:
  polycam_report.json   everything below (the Studio renders it)
  capture_report.json   the Studio's usual check summary (status / errors / warnings / stats)
  capture_manifest.json flagged photos for the Studio's thumbnail grid
  thumbs/<id>.jpg       thumbnails of the flagged photos

Sections:
  integrity  does this export still line up the way Phase 0 found (depth vs poses, depth vs GLB, RoomPlan vs GLB)?
             A failure means Polycam's format or this scan is off - the pipeline should not trust it blindly.
  photos     per-photo flags from Polycam's own measurements (motion, tracking, features, links to other photos)
  coverage   how much of the floor, ceiling and each RoomPlan wall the LiDAR actually measured, and from how far
  capture    light (ISO), phone temperature, tracking gaps, Polycam's own SfM health, RoomPlan summary
Thresholds are fixed constants below, chosen on the reference capture (docs/polycam_lidar/phase0_results.md).
"""

from __future__ import annotations

import io
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np

from namma.capture import polycam_raw as pr
from namma.core.jsonio import write_json

# integrity (reference capture values in brackets)
MIN_USABLE_SHARE = 0.95            # keyframes with the full corrected set [1.00]
MIN_NEIGHBOUR_AGREE = 0.97         # depth within 5 % between linked photos [0.994]
MIN_REVISIT_AGREE = 0.85           # ... between passes >= 60 keyframes apart [0.941]
MIN_GLB_WITHIN_2CM = 0.60          # depth points within 2 cm of the GLB through alignmentTransform [0.85]
MAX_ROOMPLAN_OFFSET_M = 0.05       # RoomPlan floor / ceiling height vs the GLB's [0.015 / 0.005]
REL_TOL, OCCLUDED = 0.05, 0.12
# photos
FAST_TURN_RAD_S = 0.6              # angular velocity [p95 of the reference: 0.60]
FEW_FEATURES = 50                  # ARKit feature points [p5: 71]
# capture
DIM_ISO_SHARE = 0.5                # ISO >= half the camera's maximum on most photos = dim room
MAX_TRACK_GAP_S = 2.0
# coverage
CELL_M = 0.10
PLANE_TOL_M = 0.05
MIN_CEILING_COVER = 0.70
MIN_FLOOR_COVER = 0.50
SEED = 0


def find_raw_zip(project_dir):
    """The Polycam LiDAR raw export kept with this project, or None."""
    orig = Path(project_dir) / "capture" / "originals"
    for p in sorted(orig.glob("*.zip")) if orig.exists() else []:
        try:
            with pr.PolycamExport(p):
                return p
        except pr.PolycamFormatError:
            continue
    return None


def _msg(code, severity, message, **extra):
    return {"code": code, "severity": severity, "message": message, **extra}


def _pct(a, qs=(50, 90)):
    a = np.asarray(a, float)
    return {f"p{q}": (round(float(np.percentile(a, q)), 4) if len(a) else None) for q in qs}


# ------------------------------------------------------------------------------------------------ frame cache
class Frames:
    """Depth / confidence cache; `poses` (id -> 4x4 camera->world) replaces the export's corrected poses, e.g. to
    score refined poses (namma.capture.polycam_poses)."""

    def __init__(self, ex, poses=None):
        self.ex, self._d, self._c, self.poses = ex, {}, {}, poses

    def pose(self, k):
        return self.poses[k] if self.poses is not None else self.ex.pose(k)

    def depth(self, k):
        if k not in self._d:
            self._d[k] = self.ex.depth(k)
        return self._d[k]

    def conf(self, k):
        if k not in self._c:
            self._c[k] = self.ex.confidence(k)
        return self._c[k]

    def K(self, k):
        return pr.scaled_K(self.ex.intrinsics(k), pr.DEPTH_SHAPE)

    def points(self, k, stride=4, min_conf=2):
        return pr.backproject(self.depth(k), self.K(k), self.pose(k), mask=self.conf(k) >= min_conf, stride=stride)[0]


# ------------------------------------------------------------------------------------------------ integrity
def choose_pairs(ex, rng, n_neighbour=80, n_revisit=60):
    ids = ex.ids
    idx = {k: i for i, k in enumerate(ids)}
    nb = sorted({tuple(sorted((idx[a], idx[b]))) for a in ids for b in ex.neighbors(a) if b in idx and b != a})
    C = np.array([ex.pose(k)[:3, 3] for k in ids])
    D = np.array([-ex.pose(k)[:3, 2] for k in ids])
    rev = [(i, j) for i in range(len(ids)) for j in range(i + 60, len(ids))
           if np.linalg.norm(C[i] - C[j]) <= 1.5 and D[i] @ D[j] >= 0.7]
    pick = lambda xs, n: [xs[k] for k in sorted(rng.choice(len(xs), min(n, len(xs)), replace=False))] if xs else []
    return {"neighbours": pick(nb, n_neighbour), "revisits": pick(rev, n_revisit),
            "candidates": {"neighbours": len(nb), "revisits": len(rev)}}


def depth_agreement(fr, a, b, stride=4):
    """High-confidence depth of photo a seen from photo b: (compared, within 5 %), occluded points left out."""
    P = fr.points(a, stride)
    uv, z = pr.project(P, fr.K(b), fr.pose(b))
    h, w = pr.DEPTH_SHAPE
    ui, vi = np.round(uv[:, 0]).astype(int), np.round(uv[:, 1]).astype(int)
    ok = (z > 0.05) & (ui >= 0) & (ui < w) & (vi >= 0) & (vi < h)
    ui, vi, z = ui[ok], vi[ok], z[ok]
    zt = fr.depth(b)[vi, ui]
    keep = np.isfinite(zt) & (fr.conf(b)[vi, ui] == 2)
    rel = (z[keep] - zt[keep]) / zt[keep]
    rel = rel[rel <= OCCLUDED]
    return len(rel), int((np.abs(rel) <= REL_TOL).sum())


def pair_agreement(fr, pairs):
    n = k = 0
    for i, j in pairs:
        for a, b in ((fr.ex.ids[i], fr.ex.ids[j]), (fr.ex.ids[j], fr.ex.ids[i])):
            c, g = depth_agreement(fr, a, b)
            n, k = n + c, k + g
    return {"pairs": len(pairs), "compared": n, "within_5pct": round(k / n, 4) if n else None}


def glb_surface(glb_path, n=1_500_000):
    from namma.studio.mesh_import import mesh_triangles, read_glb_bytes, sample_surface
    V, F, _ = mesh_triangles(read_glb_bytes(glb_path)[0])
    return sample_surface(V, F, n, seed=SEED)[0]


def glb_fit(fr, tree, M, n_frames=80):
    P = np.vstack([fr.points(k, stride=8) for k in fr.ex.ids[:: max(1, len(fr.ex.ids) // n_frames)]])
    d, _ = tree.query(P @ M[:3, :3].T + M[:3, 3], workers=-1)
    return {"points": int(len(P)), "within_2cm": round(float(np.mean(d <= 0.02)), 4),
            "within_5cm": round(float(np.mean(d <= 0.05)), 4), "median_m": round(float(np.median(d)), 4)}


def roomplan_fit(S, roomplan, M):
    """Does RoomPlan's room (through alignmentTransform) sit on the GLB? Floor and ceiling heights vs the GLB's
    surfaces inside the room outline, and the share of the GLB's floor- / ceiling-height surface that falls inside
    the outline (a shifted outline leaves it outside). Wall-plane distances alone cannot see a shift along a wall."""
    fl = pr.roomplan_floor(roomplan, M)
    walls = pr.roomplan_walls(roomplan)
    if fl is None or not walls:
        return None
    G, poly = fl
    H = max(h for _, _, h in walls)
    L = (S - G[:3, 3]) @ G[:3, :3]
    inside = _in_polygon(L[:, 0], L[:, 1], poly)
    out = {"wall_height_m": round(H, 3)}
    for name, z0 in (("floor", 0.0), ("ceiling", H)):
        zz = L[inside & (np.abs(L[:, 2] - z0) < 0.3), 2] - z0
        near = np.abs(L[:, 2] - z0) < 0.03
        off = None
        if len(zz) >= 200:
            hist, e = np.histogram(zz, bins=60, range=(-0.3, 0.3))
            off = round(float((e[hist.argmax()] + e[hist.argmax() + 1]) / 2), 3)
        out[name] = {"offset_m": off, "glb_points": int(near.sum()),
                     "inside_outline": round(float(np.mean(inside[near])), 4) if near.sum() >= 200 else None}
    return out


def _roomplan_ok(fit):
    if not fit:
        return False
    parts = [fit[k] for k in ("floor", "ceiling") if fit[k]["offset_m"] is not None]
    return bool(parts) and all(abs(p["offset_m"]) <= MAX_ROOMPLAN_OFFSET_M and (p["inside_outline"] or 0) >= 0.75
                               for p in parts)


def _check(cid, ok, value, threshold, good, bad, *, level_if_bad="warning"):
    return {"id": cid, "level": "ok" if ok else level_if_bad, "value": value, "threshold": threshold,
            "message": good if ok else bad}


# ------------------------------------------------------------------------------------------------ photos
def photo_flags(q, median_blur):
    f = []
    if (q.get("angular_velocity") or 0) >= FAST_TURN_RAD_S and (q.get("blur_score") or 0) < median_blur:
        f.append("turned fast (possible motion blur)")
    if q.get("world_mapping_status") == "limited":
        f.append("tracking limited")
    if (q.get("feature_point_count") if q.get("feature_point_count") is not None else 999) < FEW_FEATURES:
        f.append("few tracking features")
    if q.get("neighbors") == 0:
        f.append("not linked to other photos")
    return f


# ------------------------------------------------------------------------------------------------ coverage
def _in_polygon(x, y, poly):
    inside = np.zeros(len(x), bool)
    px, py = poly[:, 0], poly[:, 1]
    j = len(poly) - 1
    for i in range(len(poly)):
        cross = ((py[i] > y) != (py[j] > y)) & (x < (px[j] - px[i]) * (y - py[i]) / (py[j] - py[i] + 1e-12) + px[i])
        inside ^= cross
        j = i
    return inside


class Surface:
    """A planar patch in the GLB frame (local x, y in the plane; z = normal), gridded into CELL_M cells."""

    def __init__(self, name, kind, G, xmin, xmax, ymin, ymax, polygon=None):
        self.name, self.kind, self.G = name, kind, G
        self.x0, self.y0 = xmin, ymin
        self.nx, self.ny = max(1, int(np.ceil((xmax - xmin) / CELL_M))), max(1, int(np.ceil((ymax - ymin) / CELL_M)))
        cx = xmin + (np.arange(self.nx) + 0.5) * CELL_M
        cy = ymin + (np.arange(self.ny) + 0.5) * CELL_M
        X, Y = np.meshgrid(cx, cy, indexing="ij")
        self.valid = _in_polygon(X.ravel(), Y.ravel(), polygon).reshape(X.shape) if polygon is not None \
            else np.ones(X.shape, bool)
        self.near = np.full(X.shape, np.inf)                  # closest camera distance that measured each cell
        self.model = np.zeros(X.shape, bool)                   # cells where Polycam's GLB has a surface

    def _cells(self, P):
        L = (P - self.G[:3, 3]) @ self.G[:3, :3]
        ok = np.abs(L[:, 2]) <= PLANE_TOL_M
        ix = np.floor((L[ok, 0] - self.x0) / CELL_M).astype(int)
        iy = np.floor((L[ok, 1] - self.y0) / CELL_M).astype(int)
        inb = (ix >= 0) & (ix < self.nx) & (iy >= 0) & (iy < self.ny)
        return ok, ix[inb], iy[inb], inb

    def add(self, P, cam):
        ok, ix, iy, inb = self._cells(P)
        if len(ix):
            np.minimum.at(self.near, (ix, iy), np.linalg.norm(P[ok][inb] - cam, axis=1))

    def exclude_boxes(self, boxes):
        """Leave out cells under RoomPlan objects (cabinets, fridge): floor there cannot be seen by anyone."""
        cx = self.x0 + (np.arange(self.nx) + 0.5) * CELL_M
        cy = self.y0 + (np.arange(self.ny) + 0.5) * CELL_M
        X, Y = np.meshgrid(cx, cy, indexing="ij")
        W = np.c_[X.ravel(), Y.ravel(), np.zeros(X.size)] @ self.G[:3, :3].T + self.G[:3, 3]
        under = np.zeros(X.size, bool)
        for B, (bw, _, bd) in boxes:
            L = (W - B[:3, 3]) @ B[:3, :3]
            under |= (np.abs(L[:, 0]) <= bw / 2) & (np.abs(L[:, 2]) <= bd / 2)
        self.excluded = round(float((self.valid.ravel() & under).sum() * CELL_M ** 2), 2)
        self.valid &= ~under.reshape(X.shape)

    def add_model(self, S):
        _, ix, iy, _ = self._cells(S)
        self.model[ix, iy] = True

    def result(self):
        seen = np.isfinite(self.near) & self.valid
        n = int(self.valid.sum())
        d = self.near[seen]
        frac = lambda m: round(float(m.sum() / n), 4) if n else None
        return {"name": self.name, "kind": self.kind, "area_m2": round(n * CELL_M ** 2, 2),
                "excluded_under_objects_m2": getattr(self, "excluded", 0.0),
                "covered": frac(seen), "covered_within_2m": frac(seen & (self.near <= 2.0)),
                "in_polycam_model": frac(self.model & self.valid), "nearest_distance_m": _pct(d)}


def surfaces_from_roomplan(roomplan, M):
    """RoomPlan walls, floor and ceiling as Surfaces in the GLB frame (M = glb_from_camera)."""
    out = []
    walls = pr.roomplan_walls(roomplan, M)
    for i, (G, w, h) in enumerate(walls):
        out.append(Surface(f"wall {i + 1} ({w:.1f} m wide)", "wall", G, -w / 2, w / 2, -h / 2, h / 2))
    fl = pr.roomplan_floor(roomplan, M)
    if fl is not None and walls:
        G, poly = fl
        lo, hi = poly.min(0), poly.max(0)
        floor = Surface("floor", "floor", G, lo[0], hi[0], lo[1], hi[1], poly)
        boxes = [(M @ pr.column_major(o["transform"]), [float(x) for x in o["dimensions"]])
                 for o in (roomplan or {}).get("objects", []) if o.get("transform") and o.get("dimensions")]
        floor.exclude_boxes(boxes)                            # object frames: x width, y up, z depth
        out.append(floor)
        Gc = G.copy()
        Gc[:3, 3] = G[:3, 3] + G[:3, 2] * max(h for _, _, h in walls)   # floor's z axis is up
        out.append(Surface("ceiling", "ceiling", Gc, lo[0], hi[0], lo[1], hi[1], poly))
    return out


def coverage(fr, M, roomplan, S=None, stride=4):
    surfs = surfaces_from_roomplan(roomplan, M)
    if not surfs:
        return None
    if S is not None:
        for s in surfs:
            s.add_model(S)
    for k in fr.ex.ids:
        P = fr.points(k, stride=stride, min_conf=1) @ M[:3, :3].T + M[:3, 3]      # GLB frame
        cam = (M @ fr.pose(k))[:3, 3]
        for s in surfs:
            s.add(P, cam)
    return [s.result() for s in surfs]


# ------------------------------------------------------------------------------------------------ the report
def build_report(project_dir, *, progress=None, write=True):
    progress = progress or (lambda *_: None)
    t0 = time.time()
    d = Path(project_dir)
    cap = d / "capture"
    orig = cap / "originals"
    zp = find_raw_zip(d)
    if zp is None:
        raise pr.PolycamFormatError("no Polycam LiDAR raw export (Images .zip) in this project")
    glb = next(iter(sorted(orig.glob("*.glb"))), None)
    side = pr.load_sidecars(orig)
    rng = np.random.default_rng(SEED)
    errors, warnings, checks = [], [], []
    rep = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "route": "polycam_lidar", "zip": zp.name,
           "glb": glb.name if glb else None}
    with pr.PolycamExport(zp) as ex:
        fr = Frames(ex)
        rep["summary"] = s = ex.summary()
        usable = len(ex.ids) / len(ex.all_ids)
        checks.append(_check("usable_keyframes", usable >= MIN_USABLE_SHARE, round(usable, 4), MIN_USABLE_SHARE,
                             f"{len(ex.ids)} of {len(ex.all_ids)} keyframes have photo, pose and depth.",
                             f"Only {len(ex.ids)} of {len(ex.all_ids)} keyframes have photo, pose and depth.",
                             level_if_bad="error" if usable < 0.8 else "warning"))

        # ---- photos
        progress("Reading Polycam's photo measurements", 0.05)
        Q = {k: ex.quality(k) for k in ex.ids}
        med_blur = float(np.median([q["blur_score"] for q in Q.values() if q.get("blur_score") is not None] or [0]))
        per = [{"name": k, "flags": photo_flags(q, med_blur)} for k, q in Q.items()]
        flagged = [p for p in per if p["flags"]]
        counts = Counter(f for p in per for f in p["flags"])
        rep["photos"] = {"count": len(per), "flagged": len(flagged), "flag_counts": dict(counts),
                         "blur_score": _pct([q["blur_score"] for q in Q.values() if q.get("blur_score") is not None],
                                            (10, 50, 90)),
                         "angular_velocity": _pct([q["angular_velocity"] for q in Q.values()
                                                   if q.get("angular_velocity") is not None], (50, 95)),
                         "world_mapping_status": dict(Counter(str(q.get("world_mapping_status")) for q in Q.values()))}
        for flag, n in counts.most_common():
            if n / len(per) >= 0.10:
                warnings.append(_msg("POLYCAM-PHOTOS-" + flag.split()[0].upper(), "warning",
                                     f"{n} of {len(per)} photos: {flag}.",
                                     images=[p["name"] for p in flagged if flag in p["flags"]][:12]))

        # ---- capture conditions
        iso = np.array([q["iso"] for q in Q.values() if q.get("iso") and q.get("iso_max")], float)
        iso_max = np.array([q["iso_max"] for q in Q.values() if q.get("iso") and q.get("iso_max")], float)
        dim_share = float(np.mean(iso >= 0.5 * iso_max)) if len(iso) else 0.0
        thermal = Counter(str(q.get("thermal_state")) for q in Q.values())
        hot = sum(n for t, n in thermal.items() if t in ("serious", "critical"))
        rep["capture"] = {"iso_median": round(float(np.median(iso)), 1) if len(iso) else None,
                          "dim_share": round(dim_share, 3), "thermal_state": dict(thermal),
                          "sfm": ex.sfm_stats(), "duration_s": s["duration_s"]}
        if dim_share >= DIM_ISO_SHARE:
            warnings.append(_msg("POLYCAM-DIM", "warning", f"Dim light: {dim_share:.0%} of photos at high ISO (median "
                                 f"{np.median(iso):.0f}). Switch on all lights - sharper photos, better colour."))
        if hot:
            warnings.append(_msg("POLYCAM-HOT", "warning", f"The phone was hot ('{thermal.most_common(1)[0][0]}') for "
                                 f"{hot} of {len(Q)} photos; iOS may lower camera quality. Let it cool between scans."))

        # ---- continuous tracking
        track = ex.arkit_track()
        if track:
            ts = np.array([t for t, _, _ in track])
            gaps = np.diff(ts)
            states = Counter(str(st) for _, _, st in track)
            big = [(round(float(ts[i]), 1), round(float(g), 1)) for i, g in enumerate(gaps) if g > MAX_TRACK_GAP_S]
            rep["tracking"] = {"samples": len(track), "rate_hz": round(float(1 / np.median(gaps)), 1),
                               "states": dict(states), "gaps_over_2s": big}
            not_normal = sum(n for k, n in states.items() if k != "Normal")
            checks.append(_check("tracking", not big and not not_normal, {"gaps": len(big), "not_normal": not_normal},
                                 MAX_TRACK_GAP_S, "Phone tracking was continuous and normal.",
                                 f"Phone tracking: {len(big)} gap(s) over {MAX_TRACK_GAP_S:.0f} s"
                                 + (f" (longest {max(g for _, g in big):.1f} s)" if big else "")
                                 + (f", {not_normal} samples not 'Normal'" if not_normal else "")
                                 + ". Check that part of the model."))

        # ---- integrity: depth vs poses
        progress("Checking camera positions against the LiDAR depth", 0.15)
        pairs = choose_pairs(ex, rng)
        nb, rv = pair_agreement(fr, pairs["neighbours"]), pair_agreement(fr, pairs["revisits"])
        rep["depth_agreement"] = {"neighbours": nb, "revisits": rv, "candidates": pairs["candidates"]}
        if nb["within_5pct"] is not None:
            checks.append(_check("depth_neighbours", nb["within_5pct"] >= MIN_NEIGHBOUR_AGREE, nb["within_5pct"],
                                 MIN_NEIGHBOUR_AGREE, f"Neighbouring photos agree: {nb['within_5pct']:.1%} of depth "
                                 "within 5 %.", f"Neighbouring photos disagree: only {nb['within_5pct']:.1%} of depth "
                                 "within 5 % - poses or depth are off.", level_if_bad="error"))
        if rv["within_5pct"] is not None:
            checks.append(_check("depth_revisits", rv["within_5pct"] >= MIN_REVISIT_AGREE, rv["within_5pct"],
                                 MIN_REVISIT_AGREE, f"Revisited areas line up: {rv['within_5pct']:.1%} within 5 %.",
                                 f"Revisited areas line up poorly ({rv['within_5pct']:.1%} within 5 %): drift between "
                                 "passes."))
        else:
            checks.append({"id": "depth_revisits", "level": "warning", "value": None, "threshold": MIN_REVISIT_AGREE,
                           "message": "No revisits found: end the scan where it started so drift can be checked."})

        # ---- integrity: GLB and RoomPlan
        M = None
        if side.get("mesh_info") and glb:
            from scipy.spatial import cKDTree
            progress("Checking the depth against the 3D model", 0.35)
            M = pr.glb_from_camera(side["mesh_info"])
            S = glb_surface(glb)
            tree = cKDTree(S)
            g = glb_fit(fr, tree, M)
            rep["glb_fit"] = g
            checks.append(_check("glb_fit", g["within_2cm"] >= MIN_GLB_WITHIN_2CM, g["within_2cm"], MIN_GLB_WITHIN_2CM,
                                 f"LiDAR depth sits on the 3D model: {g['within_2cm']:.0%} within 2 cm.",
                                 f"LiDAR depth does not sit on the 3D model ({g['within_2cm']:.0%} within 2 cm): "
                                 "mesh_info.json may not belong to this export.", level_if_bad="error"))
            if side.get("roomplan"):
                fit = roomplan_fit(S, side["roomplan"], M)
                rep["roomplan_fit"] = fit
                ok = _roomplan_ok(fit)
                desc = (f"floor {fit['floor']['offset_m']} m, ceiling {fit['ceiling']['offset_m']} m" if fit else "no floor")
                checks.append(_check("roomplan_fit", ok, fit, MAX_ROOMPLAN_OFFSET_M,
                                     f"RoomPlan's room matches the model ({desc}).",
                                     f"RoomPlan's room does not match the model ({desc}): room layout not usable."))
        else:
            checks.append({"id": "glb_fit", "level": "warning", "value": None, "threshold": MIN_GLB_WITHIN_2CM,
                           "message": "No mesh_info.json or .glb next to the raw export: the depth cannot be checked "
                                      "against the 3D model. Export GLTF and import it with the raw data."})

        # ---- coverage
        if M is not None and side.get("roomplan"):
            progress("Measuring which surfaces the LiDAR saw", 0.55)
            cov = coverage(fr, M, side["roomplan"], S)
            rep["coverage"] = cov
            for c in cov or []:
                need = {"ceiling": MIN_CEILING_COVER, "floor": MIN_FLOOR_COVER}.get(c["kind"])
                if need is not None and (c["covered"] or 0) < need:
                    warnings.append(_msg(f"POLYCAM-COVER-{c['kind'].upper()}", "warning",
                                         f"The LiDAR measured only {c['covered']:.0%} of the {c['kind']}. "
                                         + ("Sweep the ceiling from closer, pointing the phone up."
                                            if c["kind"] == "ceiling" else "Point the phone down more while walking.")))
            rep["roomplan"] = roomplan_summary(side["roomplan"])

        # ---- thumbnails of flagged photos
        if write:
            progress("Writing thumbnails", 0.9)
            from PIL import Image
            th = cap / "thumbs"
            th.mkdir(parents=True, exist_ok=True)
            for p in flagged[:240]:
                with Image.open(io.BytesIO(ex.photo_bytes(p["name"]))) as im:
                    im.thumbnail((256, 256))
                    im.convert("RGB").save(th / f"{p['name']}.jpg", quality=80)

    rep["checks"] = checks
    for c in checks:
        if c["level"] == "error":
            errors.append(_msg("POLYCAM-CHECK-" + c["id"].upper(), "error", c["message"]))
        elif c["level"] == "warning":
            warnings.append(_msg("POLYCAM-CHECK-" + c["id"].upper(), "warning", c["message"]))
    rep["status"] = "blocked" if errors else "ready"
    rep["seconds"] = round(time.time() - t0, 1)
    if write:
        write_json(cap / "polycam_report.json", rep)
        write_json(cap / "capture_report.json", {
            "status": rep["status"], "mode": "polycam_lidar", "errors": errors, "warnings": warnings,
            "stats": {"inputs_accepted": len(per), "mode": "polycam_lidar", "flagged": len(flagged)},
            "generated_at": rep["generated_at"]})
        write_json(cap / "capture_manifest.json", {"mode": "polycam_lidar", "inputs": [], "per_input": flagged[:240]})
    progress("Done", 1.0)
    return rep


def roomplan_summary(roomplan):
    walls = pr.roomplan_walls(roomplan)
    objs = Counter(next(iter(o.get("category") or {"unknown": {}})) for o in roomplan.get("objects", []))
    floors = roomplan.get("floors") or []
    area = None
    if floors and floors[0].get("polygonCorners"):
        p = np.array([c[:2] for c in floors[0]["polygonCorners"]], float)
        area = round(float(0.5 * abs(np.dot(p[:, 0], np.roll(p[:, 1], 1)) - np.dot(p[:, 1], np.roll(p[:, 0], 1)))), 2)
    return {"walls": len(walls), "doors": len(roomplan.get("doors", [])), "windows": len(roomplan.get("windows", [])),
            "openings": len(roomplan.get("openings", [])), "objects": dict(objs),
            "floor_area_m2": area, "wall_height_m": round(max((h for _, _, h in walls), default=0), 2) or None,
            "rooms": [s.get("label") for s in roomplan.get("sections", []) if s.get("label")]}


def main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="Polycam LiDAR capture report (Phase 1)")
    ap.add_argument("--project", type=Path, required=True)
    a = ap.parse_args(argv)
    rep = build_report(a.project, progress=lambda lbl, f: print(f"[{f:4.0%}] {lbl}", flush=True))
    sys.stdout.write(json.dumps({k: rep[k] for k in ("status", "checks", "photos", "capture", "coverage", "roomplan")
                                 if k in rep}, indent=1) + "\n")


if __name__ == "__main__":
    main()
