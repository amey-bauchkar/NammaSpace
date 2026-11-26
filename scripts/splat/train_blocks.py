"""Photoreal view of a large scan in floor-plan blocks, each with its own splats, then joined.

All blocks use the phone's camera positions UNCHANGED (no per-block pose correction), so they share one frame and
the join is just keeping, from each block, the splats inside its own floor rectangle and putting them together.

  1. inputs once (photos, LiDAR depth, start points), as for a normal Polycam LiDAR splat;
  2. the floor (x, z) is cut into N rectangles holding equal shares of the LiDAR points;
  3. a block trains on the photos that see into it (rectangle + margin, judged by each photo's own depth), starting
     from the points there plus a thin sample of the rest (so the background is not painted onto the block);
  4. the blocks are cut back to their rectangles, joined, lined up with the model and saved as a COPY of the project.

A finished block is kept: rerunning carries on.

  python scripts/splat/train_blocks.py --project projects/<id> --work <dir> --title "Scan 1 blocks" [--blocks 3]
  --dry-run stops after the split;  --wait-file F --wait-text T starts only once F contains T (GPU in use)
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "qa"))

from namma.capture import polycam_raw as pr  # noqa: E402
from namma.capture.polycam_report import find_raw_zip  # noqa: E402
from namma.studio import splat_lidar as sl  # noqa: E402

MARGIN_M = 0.75
MIN_SEEN = 0.10          # a photo trains a block when this share of its depth falls inside rectangle + margin


def split(points, rect, n):
    """Recursive median cut of the floor: rect = (x0, x1, z0, z1) -> n rectangles with equal point shares."""
    if n == 1:
        return [rect]
    lo = n // 2
    spread = np.percentile(points, 98, axis=0) - np.percentile(points, 2, axis=0)
    axis = 0 if spread[0] >= spread[1] else 1
    cut = float(np.quantile(points[:, axis], lo / n))
    a, b = list(rect), list(rect)
    a[2 * axis + 1] = cut
    b[2 * axis] = cut
    below = points[:, axis] < cut
    return split(points[below], tuple(a), lo) + split(points[~below], tuple(b), n - lo)


def inside(xz, rect, margin=0.0):
    return ((xz[:, 0] >= rect[0] - margin) & (xz[:, 0] < rect[1] + margin)
            & (xz[:, 1] >= rect[2] - margin) & (xz[:, 1] < rect[3] + margin))


def read_ply(path):
    with open(path, "rb") as f:
        header, names = [], []
        while True:
            line = f.readline()
            header.append(line)
            t = line.decode("ascii").split()
            if t[:2] == ["element", "vertex"]:
                n = int(t[2])
            elif t[:1] == ["property"]:
                if t[1] != "float":
                    raise ValueError(f"{path}: expected float properties")
                names.append(t[2])
            elif t == ["end_header"]:
                break
        data = np.fromfile(f, np.float32, n * len(names)).reshape(n, len(names))
    return header, names, data


def write_ply(path, header, data):
    with open(path, "wb") as f:
        for line in header:
            f.write(f"element vertex {len(data)}\n".encode() if line.startswith(b"element vertex") else line)
        data.astype(np.float32).tofile(f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--blocks", type=int, default=3)
    ap.add_argument("--cap", type=int, default=600_000, help="splats per block")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wait-file", type=Path)
    ap.add_argument("--wait-text", default="queue finished")
    a = ap.parse_args(argv)

    def log(m):
        print(time.strftime("%H:%M:%S"), m, flush=True)

    while a.wait_file and not (a.wait_file.exists() and a.wait_text in a.wait_file.read_text(errors="ignore")):
        time.sleep(30)
    if a.wait_file:
        log("GPU free; break 10 min")
        time.sleep(600)

    pdir, work = a.project.resolve(), a.work.resolve()
    base = work / "input"
    if not (base / "done.json").exists():
        log("preparing photos, depth and start points")
        prep = sl.prepare(pdir, base, holdout_every=0, **sl.RECIPE)
        (base / "done.json").write_text(json.dumps({k: v for k, v in prep.items() if k != "glb_from_frame"}))
    prep = json.loads((base / "done.json").read_text())

    lines = (base / "model" / "points3D.txt").read_text(encoding="utf-8").splitlines(keepends=True)
    xz = np.array([[float(p[1]), float(p[3])] for p in (ln.split() for ln in lines)])
    inf = float("inf")
    rects = split(xz, (-inf, inf, -inf, inf), a.blocks)

    seen = []                                     # per photo: (id, camera x z, x z of what it sees)
    with pr.PolycamExport(find_raw_zip(pdir)) as ex:
        for k in ex.ids:
            d = np.load(base / "depth" / f"{k}.npy").astype(np.float32)
            T = ex.raw_pose(k)
            P, _ = pr.backproject(d, pr.scaled_K(ex.raw_intrinsics(k), d.shape), T, mask=d > 0, stride=8)
            seen.append((k, T[:3, 3][[0, 2]], P[:, [0, 2]]))

    blocks = []
    for i, rect in enumerate(rects):
        use = [k for k, c, P in seen if inside(c[None], rect)[0]
               or (len(P) and inside(P, rect, MARGIN_M).mean() >= MIN_SEEN)]
        near = inside(xz, rect, MARGIN_M)
        keep = near | (np.arange(len(xz)) % 8 == 0)
        iters = int(min(30000, max(15000, 20 * len(use))))
        blocks.append({"rect": rect, "photos": len(use), "points_inside": int(inside(xz, rect).sum()), "iters": iters})
        log(f"block {i + 1}: x {rect[0]:.1f}..{rect[1]:.1f}  z {rect[2]:.1f}..{rect[3]:.1f}  {len(use)} photos, "
            f"{blocks[-1]['points_inside']} points, {iters} steps")
        d = work / f"block{i + 1}"
        (d / "model").mkdir(parents=True, exist_ok=True)
        for n in ("cameras.txt", "images.txt"):
            shutil.copy2(base / "model" / n, d / "model" / n)
        (d / "model" / "points3D.txt").write_text("".join(ln for ln, k in zip(lines, keep) if k), encoding="utf-8")
        used = set(use)
        (d / "exclude.txt").write_text("".join(f"{k}.jpg\n" for k, _, _ in seen if k not in used), encoding="utf-8")
    unused = len(seen) - len(set().union(*[{k for k, _, _ in seen} - set(
        x[:-4] for x in (work / f"block{i + 1}" / "exclude.txt").read_text().split()) for i in range(len(rects))]))
    log(f"photos in no block: {unused} of {len(seen)}")
    (work / "blocks.json").write_text(json.dumps(blocks, indent=1))
    if a.dry_run:
        return 0

    for i, b in enumerate(blocks):
        d = work / f"block{i + 1}"
        ply = d / "room.ply"
        if ply.with_suffix(".json").exists() and ply.exists():
            log(f"block {i + 1}: already trained")
            continue
        argv_t = [str(x) for x in sl.train_argv(base, ply, iters=b["iters"], cap=a.cap, depth=True,
                                                depth_weight=sl.DEPTH_WEIGHT, holdout=False)]
        argv_t[argv_t.index("--model") + 1] = str(d / "model")
        argv_t.remove("--pose-opt")                       # blocks must share the camera positions exactly
        argv_t += ["--exclude", str(d / "exclude.txt"), "--reg-visible"]
        log(f"block {i + 1}: training")
        with open(d / "train.log", "w", encoding="utf-8") as f:
            rc = subprocess.run(argv_t, stdout=f, stderr=subprocess.STDOUT).returncode
        if rc != 0:
            log(f"block {i + 1}: FAILED rc={rc}")
            return rc
        log(f"block {i + 1}: done")
        if i + 1 < len(blocks):
            log("break 10 min")
            time.sleep(600)

    parts, header, names = [], None, None
    for i, b in enumerate(blocks):
        h, n, data = read_ply(work / f"block{i + 1}" / "room.ply")
        if names is not None and n != names:
            raise ValueError("blocks have different splat properties")
        header, names = h, n
        mine = data[inside(data[:, [n.index("x"), n.index("z")]], b["rect"])]
        log(f"block {i + 1}: keeping {len(mine)} of {len(data)} splats")
        parts.append(mine)
    merged = work / "room.ply"
    write_ply(merged, header, np.concatenate(parts))
    package(pdir, a.title or f"{pdir.name} blocks", merged, blocks, prep, log)
    return 0


def package(pdir, title, ply, blocks, prep, log):
    from make_pose_test_projects import clone
    from namma.core import timefmt
    from namma.core.jsonio import load_strict, write_json
    from namma.studio.package import build_scene_package
    from namma.studio.pipeline import RunContext
    from namma.studio.splat_align import align_splat_to_mesh
    from namma.studio.splat_claude import MIN_SPLAT_OPACITY, MIN_WITHIN_5CM
    from namma.studio.splat_large import ALIGN_SAMPLES_PER_M2
    from namma.studio.splat_spz import ply_to_spz

    run = load_strict(pdir / "project.json")["runs"][-1]["run_id"]
    pid = pdir.name.rsplit("-", 1)[0] + "-blocks-" + pdir.name.rsplit("-", 1)[1]
    dst = pdir.parent / pid
    clone(pdir, dst)
    doc = json.loads((dst / "project.json").read_text(encoding="utf-8"))
    doc.update(project_id=pid, title=title)
    (dst / "project.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tex = dst / "runs" / run / "60_texture"
    g = tex / "lod0_unrepaired.glb"
    glb = (g if g.exists() else tex / "lod0.glb").read_bytes()
    T, fit = align_splat_to_mesh(ply, glb, init_T=sl.glb_from_poses(dst, "arkit"),
                                   samples_per_m2=ALIGN_SAMPLES_PER_M2)
    log(f"line-up: {fit['within_5cm']:.0%} within 5 cm (needs {MIN_WITHIN_5CM:.0%})")
    if fit["within_5cm"] < MIN_WITHIN_5CM:
        log("NOT added to the project: too far from the model")
        return
    spz = ply_to_spz(ply, tex / "splat.spz", min_opacity=MIN_SPLAT_OPACITY)
    write_json(tex / "splat.json", {
        "file": "splat.spz", "T_mesh_from_splat": T.tolist(), "splats": spz["splats_out"], "compression": spz,
        "fit": fit, "created_at": timefmt.now_iso(), "poses": "Polycam LiDAR scan, arkit poses",
        "method": (f"3D Gaussian splat (gsplat, MCMC) trained in {len(blocks)} floor blocks from {prep['train']} "
                   "Polycam raw keyframes, fixed phone camera positions, LiDAR depth guide, joined"),
        "inputs": prep, "blocks": [{k: (None if k == "rect" else v) for k, v in b.items()} for b in blocks]})
    ctx = RunContext(dst, run, load_strict(dst / "runs" / run / "config.json")["config"])
    build_scene_package(ctx, ctx.dir("package"))
    log(f"added to Studio as '{title}': {spz['splats_out']} splats, {spz['bytes'] / 1e6:.1f} MB")


if __name__ == "__main__":
    raise SystemExit(main())
