"""Block-wise splat training for large rooms (namma.studio.splat_large, splat_options.block_mode).

One splat has a fixed budget (600 k splats fit the 6 GB RTX 4050), so over a large floor every splat grows to
cover more surface and fine texture blurs (audit B1). Block mode splits the floor into 2-6 blocks, each sized so
that the budget gives about a small room's density (block_target_per_m2 splats per m2 of FLOOR), trains each
block separately (one at a time: same peak GPU memory as one room) with

  - only the cameras that see the block,
  - start points and sparse points cropped to the block plus an overlap,
  - its own splat cap, start-size cap and size clamp,
  - a position learning rate damped to the block's radius (--means-lr-scene-cap),

then keeps from every block only the splats whose centre lies inside the block's own (non-overlapping) cell and
merges them into one splat. The overlap gives each block's border correct context; the crop removes duplicates.

All functions are pure numpy / text-file work (no GPU); frames: the training frame of the cameras, +Y up.
"""
import math
from pathlib import Path

import numpy as np

OVERLAP_M = 1.0              # training region = the block's cell grown by this on every inner side
MIN_BLOCK_CAMERAS = 12
WORLD = (-1e6, 1e6, -1e6, 1e6)      # outer block sides (finite so plans stay valid JSON)
FLOOR_CELL_M = 0.25
BLOCK_AUTO_MIN_FLOOR_M2 = 100.0     # "auto": blocks only for floors at least this large (a room: one splat)


# ------------------------------------------------------------------------------------------------ floor + plan
def floor_cells(P, N, *, cell=FLOOR_CELL_M, up=(0.0, 1.0, 0.0), band_m=0.15):
    """Floor-cell centres (k,2) on the XZ plane from surface samples P (n,3) with normals N: upward-or-downward
    horizontal surface in the lowest band (exporters flip normals)."""
    P, N = np.asarray(P, float), np.asarray(N, float)
    up = np.asarray(up, float)
    h = P @ up
    horiz = np.abs(N @ up) > 0.9
    if horiz.sum() < 10:
        return np.zeros((0, 2))
    lo = np.percentile(h[horiz], 2)
    m = horiz & (h < lo + band_m)
    ij = np.unique(np.floor(P[m][:, [0, 2]] / cell).astype(np.int64), axis=0)
    return (ij + 0.5) * cell


def _split(xz, k, rect):
    """Area-balanced recursive split of the cells into k blocks; rect = (x0, x1, z0, z1), outer sides at WORLD."""
    if k == 1 or len(xz) < 2:
        return [rect]
    ext = xz.max(0) - xz.min(0)
    ax = 0 if ext[0] >= ext[1] else 1
    k1 = k // 2
    cut = float(np.quantile(xz[:, ax], k1 / k))
    a, b = xz[xz[:, ax] < cut], xz[xz[:, ax] >= cut]
    r1, r2 = list(rect), list(rect)
    if ax == 0:
        r1[1], r2[0] = cut, cut
    else:
        r1[3], r2[2] = cut, cut
    return _split(a, k1, tuple(r1)) + _split(b, k - k1, tuple(r2))


def plan_blocks(floor_xz, *, cell=FLOOR_CELL_M, target_per_m2=12_000, cap_per_block=600_000,
                max_total=3_600_000, min_blocks=2, max_blocks=6):
    """-> {"k", "floor_m2", "needed", "blocks": [{"block", "rect", "floor_m2", "cap"}], "expected_total",
    "expected_per_m2"}. k = 1 means one splat is enough (no blocks)."""
    xz = np.asarray(floor_xz, float)
    floor_m2 = len(xz) * cell * cell
    needed = target_per_m2 * floor_m2
    k = max(1, math.ceil(needed / cap_per_block))
    k = min(k, max_blocks, max(1, int(max_total // cap_per_block)))
    if k >= 2:
        k = max(k, min_blocks)
    rects = _split(xz, k, WORLD) if k >= 2 and len(xz) else [WORLD]
    blocks = []
    for i, r in enumerate(rects):
        fm2 = float(inside(xz, r).sum()) * cell * cell
        blocks.append({"block": i, "rect": [float(v) for v in r], "floor_m2": round(fm2, 2),
                       "cap": int(min(cap_per_block, max(50_000, math.ceil(1.1 * target_per_m2 * fm2))))})
    total = sum(b["cap"] for b in blocks)
    if total > max_total:                                   # the budget wins: scale every block down
        f = max_total / total
        for b in blocks:
            b["cap"] = int(b["cap"] * f)
        total = sum(b["cap"] for b in blocks)
    return {"k": len(blocks), "floor_m2": round(floor_m2, 2), "needed": int(needed), "blocks": blocks,
            "expected_total": int(total), "expected_per_m2": round(total / max(floor_m2, 1e-9), 1),
            "target_per_m2": target_per_m2, "cap_per_block": cap_per_block, "max_total": max_total}


def inside(xz, rect):
    """Half-open cell test (x0 <= x < x1, z0 <= z < z1): every point belongs to exactly one block."""
    xz = np.asarray(xz, float)
    x0, x1, z0, z1 = rect
    return (xz[:, 0] >= x0) & (xz[:, 0] < x1) & (xz[:, 1] >= z0) & (xz[:, 1] < z1)


def grow(rect, m=OVERLAP_M):
    x0, x1, z0, z1 = rect
    return (x0 - m, x1 + m, z0 - m, z1 + m)


def block_radius(rect, floor_xz, m=OVERLAP_M):
    """Half the diagonal of the block's training region, clipped to the floor's extent (outer sides are at WORLD)."""
    xz = np.asarray(floor_xz, float)
    lo, hi = xz.min(0) - m, xz.max(0) + m
    x0, x1, z0, z1 = grow(rect, m)
    w = min(x1, hi[0]) - max(x0, lo[0])
    d = min(z1, hi[1]) - max(z0, lo[1])
    return round(0.5 * math.hypot(max(w, 0.0), max(d, 0.0)), 3)


# ------------------------------------------------------------------------------------------------ cameras
def _quat_to_rot(w, x, y, z):
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def read_text_model(sparse_dir):
    """COLMAP text model -> (camera lines {id: (w, h, K)}, images [{"name", "R", "t", "C", "cam", "lines"}])."""
    d = Path(sparse_dir)
    cams = {}
    for ln in (d / "cameras.txt").read_text(encoding="utf-8").splitlines():
        p = ln.split()
        if p and not p[0].startswith("#"):
            fx, fy, cx, cy = map(float, p[4:8])
            cams[int(p[0])] = (int(p[2]), int(p[3]), np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]]))
    lines = [ln for ln in (d / "images.txt").read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    imgs = []
    for i in range(0, len(lines), 2):
        p = lines[i].split()
        if len(p) < 10:
            continue
        R = _quat_to_rot(*map(float, p[1:5]))
        t = np.array([float(v) for v in p[5:8]])
        imgs.append({"name": " ".join(p[9:]), "R": R, "t": t, "C": -R.T @ t, "cam": int(p[8]),
                     "lines": [lines[i], lines[i + 1] if i + 1 < len(lines) else ""]})
    return cams, imgs


def assign_cameras(cams, imgs, blocks, surface_pts, *, min_visible=0.02, max_depth_m=12.0, seed=0,
                   depth_maps=None, depth_tolerance_m=.05):
    """Cameras per block: those standing in the block's training region, plus those whose view contains at least
    min_visible of the block's surface samples (in front, inside the picture, within max_depth_m; walls are not
    tested, so a camera may be counted through a wall - more context, never less). Every block gets at least
    MIN_BLOCK_CAMERAS (the best-seeing ones). -> [[image names], ...] in block order."""
    if depth_maps is not None and (not np.isfinite(depth_tolerance_m) or depth_tolerance_m <= 0):
        raise ValueError("visibility depth tolerance must be positive and finite")
    rng = np.random.default_rng(seed)
    S = np.asarray(surface_pts, float)
    out = []
    for b in blocks:
        r = tuple(b["rect"])
        pts = S[inside(S[:, [0, 2]], r)]
        if len(pts) > 2000:
            pts = pts[rng.choice(len(pts), 2000, replace=False)]
        score = []
        for im in imgs:
            w, h, K = cams[im["cam"]]
            X = pts @ im["R"].T + im["t"]
            ok = (X[:, 2] > 0.1) & (X[:, 2] < max_depth_m)
            frac = 0.0
            if ok.any():
                uv = X[ok] @ K.T
                uv = uv[:, :2] / uv[:, 2:3]
                vis = (uv[:, 0] >= 0) & (uv[:, 0] < w) & (uv[:, 1] >= 0) & (uv[:, 1] < h)
                if depth_maps is not None:
                    if im["name"] not in depth_maps:
                        vis[:] = False
                    else:
                        depth = np.asarray(depth_maps[im["name"]])
                        if depth.ndim != 2 or not depth.size or depth_tolerance_m <= 0:
                            raise ValueError("invalid block visibility depth")
                        dx = np.clip((uv[:,0]*depth.shape[1]/w).astype(int),0,depth.shape[1]-1)
                        dy = np.clip((uv[:,1]*depth.shape[0]/h).astype(int),0,depth.shape[0]-1)
                        measured = depth[dy,dx]
                        vis &= np.isfinite(measured) & (measured > 0) & (np.abs(measured-X[ok,2]) <= depth_tolerance_m)
                frac = float(vis.sum()) / max(1, len(pts))
            standing = bool(inside(im["C"][None, [0, 2]], grow(r))[0])
            cx = min(max(im["C"][0], r[0]), r[1])
            cz = min(max(im["C"][2], r[2]), r[3])
            dist = float(np.hypot(im["C"][0] - cx, im["C"][2] - cz))           # 0 when standing in the block
            score.append(((standing if depth_maps is None else False) or frac >= min_visible, frac, dist, im["name"]))
        chosen = [n for keep, _, _, n in score if keep]
        if len(chosen) < MIN_BLOCK_CAMERAS and depth_maps is None:
            # top up with the cameras that see most of it, then the nearest ones (never far cameras by list order)
            best = [n for _, _, _, n in sorted(score, key=lambda s: (-s[1], s[2]))[:MIN_BLOCK_CAMERAS]]
            chosen = list(dict.fromkeys(chosen + best))
        out.append(chosen)
    return out


def write_block_model(sparse_dir, out_dir, names, rect):
    """A block's COLMAP text model: all cameras.txt, the chosen images, the sparse points inside the grown region."""
    src, out = Path(sparse_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cameras.txt").write_text((src / "cameras.txt").read_text(encoding="utf-8"), encoding="utf-8")
    _, imgs = read_text_model(src)
    keep = set(names)
    (out / "images.txt").write_text("".join(f"{im['lines'][0]}\n{im['lines'][1]}\n" for im in imgs if im["name"] in keep),
                                    encoding="utf-8")
    gr = grow(tuple(rect))
    n = 0
    with open(out / "points3D.txt", "w", encoding="utf-8") as f:
        for ln in (src / "points3D.txt").read_text(encoding="utf-8").splitlines():
            p = ln.split()
            if not p or p[0].startswith("#"):
                continue
            if inside(np.array([[float(p[1]), float(p[3])]]), gr)[0]:
                f.write(ln + "\n")
                n += 1
    return {"images": len(keep & {im["name"] for im in imgs}), "points": n}


def crop_init(npz_in, npz_out, rect):
    d = np.load(npz_in)
    m = inside(d["xyz"][:, [0, 2]], grow(tuple(rect)))
    np.savez(npz_out, xyz=d["xyz"][m], rgb=d["rgb"][m])
    return int(m.sum())


# ------------------------------------------------------------------------------------------------ merge + budget
def write_splat_ply(path, cols, props):
    n = len(cols[props[0]])
    arr = np.empty(n, dtype=[(p, "<f4") for p in props])
    for p in props:
        arr[p] = cols[p]
    head = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n" % n + \
        "".join(f"property float {p}\n" for p in props) + "end_header\n"
    Path(path).write_bytes(head.encode("ascii") + arr.tobytes())


def merge_blocks(plys, rects, out_ply, *, support_counts=None):
    """Keep from each block's splat the splats whose centre is in the block's own cell; concatenate.
    -> {"per_block": [{"block", "splats_in", "kept"}], "splats": total}"""
    from namma.studio.splat_spz import _ply_fields
    if support_counts is not None:
        from namma.afe.boundaries import reconcile
        fields = [_ply_fields(path) for path in plys]
        if len(fields)!=len(rects) or len(fields)!=len(support_counts) or any(list(f)!=list(fields[0]) for f in fields):
            raise ValueError("block schemas or support arrays differ")
        cropped,support = [],[]
        for f,rect,counts in zip(fields,rects,support_counts):
            if len(counts)!=len(f["x"]):
                raise ValueError("block support count mismatch")
            mask = inside(np.c_[f["x"],f["z"]],grow(tuple(rect)))
            cropped.append({key:value[mask] for key,value in f.items()})
            support.append(np.asarray(counts)[mask])
        parts = [{"means":np.c_[f["x"],f["y"],f["z"]],
                  "colour":np.c_[f["f_dc_0"],f["f_dc_1"],f["f_dc_2"]],
                  "scales":np.c_[f["scale_0"],f["scale_1"],f["scale_2"]]} for f in cropped]
        result = reconcile(parts,rects,support)
        indices = result["selected_global_rows"]
        merged = {key:np.concatenate([f[key] for f in cropped])[indices] for key in fields[0]}
        write_splat_ply(out_ply,merged,list(fields[0]))
        return {"splats":len(indices),"status":result["status"],"removed_rows":result["removed_rows"],
                "per_block":[{"block":i,"splats_in":len(f["x"]),"kept":int(((indices>=a)&(indices<b)).sum())}
                             for i,(f,a,b) in enumerate(zip(cropped,result["part_offsets"][:-1],result["part_offsets"][1:]))]}
    parts, props, per = [], None, []
    for i, (p, r) in enumerate(zip(plys, rects)):
        f = _ply_fields(p)
        if props is None:
            props = list(f)
        elif list(f) != props:
            raise ValueError(f"block {i}: splat fields differ from block 0")
        m = inside(np.c_[f["x"], f["z"]], tuple(r))
        parts.append({k: np.asarray(v)[m] for k, v in f.items()})
        per.append({"block": i, "splats_in": int(len(m)), "kept": int(m.sum())})
    cols = {k: np.concatenate([q[k] for q in parts]) for k in props}
    write_splat_ply(out_ply, cols, props)
    return {"per_block": per, "splats": int(len(cols[props[0]]))}


def enforce_budget(ply_in, ply_out, max_total):
    """At most max_total splats: the least visible ones (opacity x size) are dropped. -> {"splats_in", "splats_out"}"""
    from namma.studio.splat_spz import _ply_fields
    f = _ply_fields(ply_in)
    props = list(f)
    n = len(f["x"])
    if n <= max_total:
        if Path(ply_in) != Path(ply_out):
            Path(ply_out).write_bytes(Path(ply_in).read_bytes())
        return {"splats_in": n, "splats_out": n}
    op = 1 / (1 + np.exp(-np.asarray(f["opacity"], float)))
    size = np.exp(np.asarray(f["scale_0"], float) + np.asarray(f["scale_1"], float) + np.asarray(f["scale_2"], float))
    keep = np.argsort(-(op * np.cbrt(size)))[:max_total]
    keep.sort()
    write_splat_ply(ply_out, {k: np.asarray(v)[keep] for k, v in f.items()}, props)
    return {"splats_in": n, "splats_out": int(max_total)}
