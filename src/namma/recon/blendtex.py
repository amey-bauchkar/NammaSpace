"""Multi-view blended texturing - replaces OpenMVS's one-photo-per-patch colours.

OpenMVS TextureMesh paints every face from ONE photo. On glossy floors, plain walls and varnished doors the photos
disagree (reflections, exposure), so the result is a patchwork of shards. Here every texel of the existing atlas
(OpenMVS's mesh and UV layout are kept) is recomputed as a weighted mean over ALL photos that see its 3D point:

    weight = (cos of viewing angle)^4 / distance^2  x  edge fade        (frontal, close views dominate)

with a per-photo z-buffer for visibility and per-photo exposure gains. View-dependent reflections average out and
patch seams disappear because neighbouring texels use the same photos. Texels no photo sees keep their colour.

`scale` > 1 enlarges the atlas (same UV layout) and the photos can be the full-resolution originals: the camera
calibration (made on the smaller working copies) is rescaled, EXIF orientation applied. Atlases are processed one
at a time so memory stays bounded.

Device: with CuPy installed (requirements-gpu.txt) and a usable NVIDIA GPU (namma.recon.hardware), the per-photo
projection / visibility / weighting runs on the GPU (_blend_views_gpu, same formulas in float32, texels in batches so
VRAM stays bounded); otherwise, or if the GPU path fails, the original CPU loop runs unchanged. The device used is
returned in the stats ("device").
"""
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage
from scipy.spatial import cKDTree

from namma.recon.glb import read_glb, write_glb
from namma.recon.harmonize import _accessor, _buffers

COS_MAX_ANGLE = float(np.cos(np.radians(75.0)))   # ~0.25881905: glancing-angle cutoff (incidence <= 75 deg)
COS_FADE_ANGLE = float(np.cos(np.radians(65.0)))  # ~0.42261827: smooth falloff onset


def heal_coplanar_texels(X, N, col, is_valid, global_ref=None, max_search_r=0.40, chunk_size=100_000):
    """Interpolate starved texels from neighboring coplanar surfaces in 3D.

    For surfaces with low ray-camera incidence angles (theta > 75 deg), near-zero coverage,
    or OpenMVS dark shadow/specular artifacts, this finds neighboring reliably observed surfaces
    in 3D, filters for coplanarity (aligned normals and small plane offset), and computes a distance-weighted
    radiance blend.
    """
    starved_idx = np.nonzero(~is_valid)[0]
    if len(starved_idx) == 0:
        return col

    if global_ref is not None:
        ref_X, ref_N, ref_col = global_ref
    else:
        valid_idx = np.nonzero(is_valid)[0]
        if len(valid_idx) == 0:
            return col
        stride = max(1, len(valid_idx) // 50000)
        sub_valid = valid_idx[::stride]
        ref_X = X[sub_valid]
        ref_N = N[sub_valid]
        ref_col = col[sub_valid]

    if len(ref_X) == 0:
        return col

    tree = cKDTree(ref_X)
    s_X = X[starved_idx]
    s_N = N[starved_idx]
    n_starved = len(starved_idx)

    healed_col = col.copy()
    K = min(10, len(ref_X))

    for start in range(0, n_starved, chunk_size):
        end = min(n_starved, start + chunk_size)
        cur_X = s_X[start:end]
        cur_N = s_N[start:end]
        cur_s_idx = starved_idx[start:end]

        dists, idxs = tree.query(cur_X, k=K, distance_upper_bound=max_search_r)
        valid_mask = idxs < len(ref_X)
        safe_idxs = np.where(valid_mask, idxs, 0)

        cand_X = ref_X[safe_idxs]
        cand_N = ref_N[safe_idxs]
        cand_C = ref_col[safe_idxs]

        # Coplanarity: aligned normals (|N_cand . N_cur| >= 0.707) AND plane offset <= 4 cm
        norm_dot = np.abs(np.einsum("ski,si->sk", cand_N, cur_N))
        plane_dist = np.abs(np.einsum("ski,si->sk", cand_X - cur_X[:, None, :], cur_N))
        coplanar = valid_mask & (norm_dot >= 0.707) & (plane_dist <= 0.04)

        has_coplanar = coplanar.any(axis=1)
        use_mask = np.where(has_coplanar[:, None], coplanar, valid_mask)
        weights = np.where(use_mask, 1.0 / np.maximum(dists, 0.005), 0.0)
        w_sum = weights.sum(axis=1, keepdims=True)
        has_any = (w_sum > 0).ravel()

        cur_healed = np.zeros((len(cur_X), 3), np.float32)
        safe_w_sum = np.where(has_any[:, None], w_sum, 1.0)
        cur_healed[has_any] = (cand_C * weights[:, :, None]).sum(axis=1)[has_any] / safe_w_sum[has_any]

        # For points beyond max_search_r with no neighbor: nearest valid reference
        if not has_any.all():
            missing = np.nonzero(~has_any)[0]
            _, n1 = tree.query(cur_X[missing], k=1)
            cur_healed[missing] = ref_col[n1]

        # Add subtle micro-texture grain (sigma = 1.5/255) to avoid flat plastic appearance
        rng = np.random.default_rng(42 + start)
        grain = rng.normal(0, 1.5 / 255.0, cur_healed.shape).astype(np.float32)
        cur_healed = np.clip(cur_healed + grain, 0.0, 1.0)

        healed_col[cur_s_idx] = cur_healed

    return healed_col


def rasterize_atlas(uv, faces, W, H):
    """Per texel: face index (-1 = none) and barycentric (b1, b2). uv in glTF convention (v down)."""
    fid = -np.ones((H, W), np.int32)
    bary = np.zeros((H, W, 2), np.float32)
    px = uv[:, 0] * W - 0.5
    py = uv[:, 1] * H - 0.5
    x = px[faces]; y = py[faces]                                   # (F, 3)
    x0, x1 = np.floor(x.min(1)).astype(int), np.ceil(x.max(1)).astype(int)
    y0, y1 = np.floor(y.min(1)).astype(int), np.ceil(y.max(1)).astype(int)
    size = np.maximum(x1 - x0, y1 - y0) + 1
    groups = [(s, np.nonzero(size == s)[0]) for s in sorted(set(size[size <= 64].tolist()))]
    groups += [(int(size[k]), np.array([k])) for k in np.nonzero(size > 64)[0]]     # large triangles: full bbox each
    for s, sel in groups:
        if not len(sel):
            continue
        oy, ox = np.mgrid[0:s, 0:s]
        oy, ox = oy.ravel(), ox.ravel()
        for chunk in np.array_split(sel, max(1, len(sel) * len(ox) // 4_000_000 + 1)):
            X = x0[chunk, None] + ox[None, :]
            Y = y0[chunk, None] + oy[None, :]
            ax, ay = x[chunk, 0, None], y[chunk, 0, None]
            bx, by = x[chunk, 1, None] - ax, y[chunk, 1, None] - ay
            cx, cy = x[chunk, 2, None] - ax, y[chunk, 2, None] - ay
            den = bx * cy - by * cx
            den = np.where(np.abs(den) < 1e-12, 1e-12, den)
            px_, py_ = X - ax, Y - ay
            b1 = (px_ * cy - py_ * cx) / den
            b2 = (bx * py_ - by * px_) / den
            inside = (b1 >= -0.01) & (b2 >= -0.01) & (b1 + b2 <= 1.01) & (X >= 0) & (X < W) & (Y >= 0) & (Y < H)
            fi = np.broadcast_to(chunk[:, None], X.shape)[inside]
            fid[Y[inside], X[inside]] = fi
            bary[Y[inside], X[inside], 0] = b1[inside]
            bary[Y[inside], X[inside], 1] = b2[inside]
    return fid, bary


def _views_iter(views):
    for v in views:
        with Image.open(v["path"]) as im:
            img = np.asarray(ImageOps.exif_transpose(im).convert("RGB"), np.uint8)
        k = img.shape[1] / v["cam"]["width"]               # photo used here vs. size the camera was calibrated at
        if abs(img.shape[0] / v["cam"]["height"] - k) > 0.02 * k:
            continue                                        # different image geometry: skip rather than mis-project
        yield v, img, k


def _camera(cam, k):
    p = list(cam["params"])
    m = cam["model"]
    if m == "OPENCV":
        fx, fy, cx, cy = p[:4]; k1, k2, p1, p2 = p[4:8]
    elif m in ("SIMPLE_RADIAL", "RADIAL"):
        fx = fy = p[0]; cx, cy = p[1], p[2]; k1 = p[3]; k2 = p[4] if m == "RADIAL" else 0.0; p1 = p2 = 0.0
    elif m == "PINHOLE":
        fx, fy, cx, cy = p[:4]; k1 = k2 = p1 = p2 = 0.0
    else:                                                   # SIMPLE_PINHOLE
        fx = fy = p[0]; cx, cy = p[1], p[2]; k1 = k2 = p1 = p2 = 0.0
    # COLMAP pixel centres are at +0.5: scale about the pixel corner
    return fx * k, fy * k, (cx + 0.5) * k - 0.5, (cy + 0.5) * k - 0.5, k1, k2, p1, p2


def _texels(parts, W, H):
    pts, nrm, ys, xs = [], [], [], []
    for P, UV, F in parts:
        fid, bary = rasterize_atlas(UV, F, W, H)
        yy, xx = np.nonzero(fid >= 0)
        f = F[fid[yy, xx]]
        b1, b2 = bary[yy, xx, 0, None], bary[yy, xx, 1, None]
        del fid, bary
        X = P[f[:, 0]] * (1 - b1 - b2) + P[f[:, 1]] * b1 + P[f[:, 2]] * b2
        n = np.cross(P[f[:, 1]] - P[f[:, 0]], P[f[:, 2]] - P[f[:, 0]])
        n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
        pts.append(X.astype(np.float32)); nrm.append(n.astype(np.float32)); ys.append(yy); xs.append(xx)
    return np.vstack(pts), np.vstack(nrm), np.concatenate(ys), np.concatenate(xs)


def blend_texture_glb(src, dst, views, *, power=4, depth_tol=0.015, edge_frac=0.05, scale=1, max_side=8192,
                      zbuf_px=400, log=print):
    doc, binary = read_glb(src)
    bufs = _buffers(doc, binary)
    prims = [p for m in doc["meshes"] for p in m["primitives"]]
    allP, per_tex = [], {}
    for prim in prims:
        P = _accessor(doc, bufs, prim["attributes"]["POSITION"]).astype(np.float64)
        UV = _accessor(doc, bufs, prim["attributes"]["TEXCOORD_0"]).astype(np.float64)
        F = _accessor(doc, bufs, prim["indices"]).reshape(-1, 3).astype(np.int64)
        tex = doc["textures"][doc["materials"][prim["material"]]["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]
        per_tex.setdefault(tex, []).append((P, UV, F))
        allP.append(P)
    allP = np.vstack(allP).astype(np.float32)
    stats = {"photos": len(views), "scale": scale}
    buf0 = bytearray(bufs[0])
    # Phase 1: Blend views per atlas, filter glancing angles (theta > 75 deg), and collect clean reference radiance
    atlas_data = []
    global_ref_X, global_ref_N, global_ref_C = [], [], []

    for t, parts in per_tex.items():
        img_info = doc["images"][t]
        if "bufferView" in img_info:
            bv = doc["bufferViews"][img_info["bufferView"]]
            raw = bufs[bv.get("buffer", 0)][bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]]
            A0 = Image.open(io.BytesIO(raw)).convert("RGB")
        elif "uri" in img_info:
            uri_path = (Path(src).parent / img_info["uri"]).resolve()
            A0 = Image.open(uri_path).convert("RGB")
        else:
            raise ValueError(f"Image {t} has neither bufferView nor uri")
        sc = max(1, min(scale, max_side // max(A0.size)))         # never beyond max_side (browser GPU memory)
        W, H = A0.size[0] * sc, A0.size[1] * sc
        out = np.asarray(A0.resize((W, H), Image.LANCZOS) if sc != 1 else A0, np.float32) / 255
        X, N, yy, xx = _texels(parts, W, H)
        acc = np.zeros((len(yy), 3), np.float32)
        wsum = np.zeros(len(yy), np.float32)
        max_cos = np.zeros(len(yy), np.float32)
        log(f"atlas {t}: {W}x{H}, {len(yy) / 1e6:.1f} M texels")
        gpu = _gpu_blend_ok()
        if gpu:
            try:
                acc, wsum, max_cos, n_used = _blend_views_gpu(X, N, allP, views, power=power, depth_tol=depth_tol,
                                                             edge_frac=edge_frac, zbuf_px=zbuf_px, log=log, label=f"atlas {t}")
                stats["device"] = "gpu"
            except Exception as e:                     # noqa: BLE001 - any CUDA / memory problem: CPU path below
                log(f"  GPU blend failed ({e.__class__.__name__}: {e}); blending on the CPU")
                stats["gpu_fallback"] = f"{e.__class__.__name__}: {e}"[:300]
                gpu = False
                acc[:] = 0
                wsum[:] = 0
                max_cos[:] = 0
        if not gpu:
            stats["device"] = "cpu"
        n_used = n_used if gpu else 0
        for k_view, (v, img, k) in enumerate([] if gpu else _views_iter(views)):
            n_used += 1
            Hi, Wi = img.shape[:2]
            fx, fy, cx, cy, k1, k2, p1, p2 = _camera(v["cam"], k)
            R, tv, C = v["R"], v["t"], v["C"]

            def project(Q):
                Xc = Q @ R.T + tv
                z = Xc[:, 2]
                zs = np.where(z > 1e-6, z, 1.0)
                x, y = Xc[:, 0] / zs, Xc[:, 1] / zs
                r2 = x * x + y * y
                rad = 1 + k1 * r2 + k2 * r2 * r2
                xd = x * rad + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
                yd = y * rad + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
                return fx * xd + cx, fy * yd + cy, z
            # visibility: z-buffer splatted from the mesh vertices at about zbuf_px pixels
            zsc = zbuf_px / max(Wi, Hi)
            u, w, z = project(allP)
            ok = (z > 1e-6) & (u >= 0) & (u < Wi) & (w >= 0) & (w < Hi)
            zb = np.full((int(Hi * zsc) + 1, int(Wi * zsc) + 1), np.inf, np.float32)
            np.minimum.at(zb, ((w[ok] * zsc).astype(int), (u[ok] * zsc).astype(int)), z[ok])
            zb = ndimage.minimum_filter(zb, size=3)
            u, w, z = project(X)
            ok = (z > 1e-6) & (u >= 0) & (u < Wi - 1) & (w >= 0) & (w < Hi - 1)
            idx = np.nonzero(ok)[0]
            if len(idx):
                idx = idx[z[idx] <= zb[(w[idx] * zsc).astype(int), (u[idx] * zsc).astype(int)] * (1 + depth_tol)]
                ray = X[idx] - C
                dist = np.linalg.norm(ray, axis=1) + 1e-9
                cos = np.abs(np.einsum("ij,ij->i", ray, N[idx])) / dist
                margin = edge_frac * min(Wi, Hi)
                uu, ww = u[idx], w[idx]
                edge = np.clip(np.minimum.reduce([uu, ww, Wi - 1 - uu, Hi - 1 - ww]) / margin, 0, 1)
                cos_fade = np.clip((cos - COS_MAX_ANGLE) / (COS_FADE_ANGLE - COS_MAX_ANGLE), 0.0, 1.0)
                wt = (cos ** power) / (dist * dist) * edge * cos_fade
                u0, w0 = np.floor(uu).astype(int), np.floor(ww).astype(int)
                fu, fw = (uu - u0)[:, None], (ww - w0)[:, None]
                c = (img[w0, u0] * (1 - fu) * (1 - fw) + img[w0, u0 + 1] * fu * (1 - fw)
                     + img[w0 + 1, u0] * (1 - fu) * fw + img[w0 + 1, u0 + 1] * fu * fw) * (v["gain"] / 255.0)
                acc[idx] += (c * wt[:, None]).astype(np.float32)
                wsum[idx] += wt.astype(np.float32)
                np.maximum.at(max_cos, idx, cos.astype(np.float32))
            if (k_view + 1) % 25 == 0:
                log(f"  atlas {t}: {k_view + 1}/{len(views)} photos blended")

        seen = (wsum > 1e-4) & (max_cos >= COS_MAX_ANGLE)
        col = out[yy, xx].copy()
        col[seen] = acc[seen] / wsum[seen, None]
        col = np.clip(col, 0, 1)

        # Valid direct radiance:
        # Rejects glancing starvation, dark shadow artifacts, and specular blowouts
        is_valid = seen & (col.mean(axis=1) >= 0.35) & (col.min(axis=1) >= 0.20) & (col.max(axis=1) < 0.98)
        valid_idx = np.nonzero(is_valid)[0]
        if len(valid_idx) > 0:
            stride = max(1, len(valid_idx) // 30000)
            global_ref_X.append(X[valid_idx[::stride]])
            global_ref_N.append(N[valid_idx[::stride]])
            global_ref_C.append(col[valid_idx[::stride]])

        atlas_data.append({
            "t": t, "W": W, "H": H, "out": out,
            "X": X, "N": N, "yy": yy, "xx": xx,
            "col": col, "is_valid": is_valid, "seen": seen, "n_used": n_used, "gpu": gpu
        })

    global_ref = None
    if global_ref_X:
        global_ref = (np.vstack(global_ref_X), np.vstack(global_ref_N), np.vstack(global_ref_C))

    # Phase 2: Heal starved / glancing-angle surfaces from coplanar neighbors and inpaint gutters
    for ad in atlas_data:
        t = ad["t"]
        W, H = ad["W"], ad["H"]
        X, N = ad["X"], ad["N"]
        yy, xx = ad["yy"], ad["xx"]
        col = ad["col"]
        is_valid = ad["is_valid"]
        out = ad["out"]
        gpu = ad["gpu"]

        # Coplanar healing of starved texels
        col = heal_coplanar_texels(X, N, col, is_valid, global_ref=global_ref, max_search_r=0.40)
        out[yy, xx] = np.clip(col, 0, 1)

        # Full background gutter fill: extend valid surface texels outward so mipmaps never pull black/stale borders
        surface_mask_np = np.zeros((H, W), bool)
        surface_mask_np[yy, xx] = True
        del X, N, col

        if surface_mask_np.any():
            inpainted = False
            if gpu:
                try:
                    import cupy as cp
                    from cupyx.scipy import ndimage as cnd
                    surf_gpu = cp.asarray(surface_mask_np)
                    _, ind = cnd.distance_transform_edt(~surf_gpu, return_distances=True, return_indices=True)
                    out_gpu = cp.asarray(out)
                    unseen = ~surf_gpu
                    out_gpu[unseen] = out_gpu[ind[0][unseen], ind[1][unseen]]
                    out = cp.asnumpy(out_gpu)
                    del surf_gpu, ind, out_gpu
                    cp.get_default_memory_pool().free_all_blocks()
                    inpainted = True
                except Exception as e:                 # noqa: BLE001 - CPU fallback
                    log(f"  GPU inpaint fallback to CPU ({e.__class__.__name__}: {e})")
            if not inpainted:
                _, (iy, ix) = ndimage.distance_transform_edt(~surface_mask_np, return_indices=True)
                unseen = ~surface_mask_np
                out[unseen] = out[iy[unseen], ix[unseen]]
                del iy, ix
        del surface_mask_np

        bio = io.BytesIO()
        Image.fromarray((out * 255 + 0.5).astype(np.uint8)).save(bio, "JPEG", quality=92, optimize=True)
        data = bio.getvalue()
        del out
        buf0 += b"\x00" * (-len(buf0) % 4)
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(buf0), "byteLength": len(data)})
        buf0 += data
        doc["images"][t] = {"bufferView": len(doc["bufferViews"]) - 1, "mimeType": "image/jpeg", "name": f"texture_{t}"}
        stats[f"atlas_{t}_texels_blended"] = float(ad["seen"].mean())
        stats[f"atlas_{t}_size"] = [W, H]
        stats["photos_used"] = ad["n_used"]
    doc["buffers"][0]["byteLength"] = len(buf0) + (-len(buf0) % 4)
    doc.setdefault("asset", {})["generator"] = doc["asset"].get("generator", "") + " + multi-view blended texture (namma.recon.blendtex)"
    write_glb(dst, doc, bytes(buf0))
    return stats


def make_views(images, cameras, image_dir, gains=None, originals_dir=None):
    """originals_dir: use the full-resolution original photos (matched by file stem) instead of image_dir."""
    orig = {}
    if originals_dir and Path(originals_dir).exists():
        orig = {f.stem.lower(): f for f in Path(originals_dir).iterdir() if f.is_file()}
    views = []
    for name, im in images.items():
        f = orig.get(Path(name).stem.lower()) or Path(image_dir) / name
        if f.exists():
            views.append({"path": f, "R": im["R"].astype(np.float32), "t": im["t"].astype(np.float32),
                          "C": im["C"].astype(np.float32), "cam": cameras[im["camera_id"]],
                          "gain": np.asarray((gains or {}).get(name, [1, 1, 1]), np.float32)})
    return views


def blend_points(X, N, views, mesh_vertices, *, power=4, depth_tol=0.015, edge_frac=0.05, zbuf_px=400):
    """Colour of arbitrary surface points (SfM frame) with the same weights, gains and visibility test as the
    blended texture - used for estimated surfaces so they match the scanned surfaces next to them."""
    X = np.asarray(X, np.float32); N = np.asarray(N, np.float32)
    allP = np.asarray(mesh_vertices, np.float32)
    acc = np.zeros((len(X), 3), np.float32); wsum = np.zeros(len(X), np.float32); nv = np.zeros(len(X), int)
    for v, img, k in _views_iter(views):
        Hi, Wi = img.shape[:2]
        fx, fy, cx, cy, k1, k2, p1, p2 = _camera(v["cam"], k)
        R, tv, C = v["R"], v["t"], v["C"]

        def project(Q):
            Xc = Q @ R.T + tv
            z = Xc[:, 2]
            zs = np.where(z > 1e-6, z, 1.0)
            x, y = Xc[:, 0] / zs, Xc[:, 1] / zs
            r2 = x * x + y * y
            rad = 1 + k1 * r2 + k2 * r2 * r2
            return (fx * (x * rad + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)) + cx,
                    fy * (y * rad + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y) + cy, z)
        zsc = zbuf_px / max(Wi, Hi)
        u, w, z = project(allP)
        ok = (z > 1e-6) & (u >= 0) & (u < Wi) & (w >= 0) & (w < Hi)
        zb = np.full((int(Hi * zsc) + 1, int(Wi * zsc) + 1), np.inf, np.float32)
        np.minimum.at(zb, ((w[ok] * zsc).astype(int), (u[ok] * zsc).astype(int)), z[ok])
        zb = ndimage.minimum_filter(zb, size=3)
        u, w, z = project(X)
        ok = (z > 1e-6) & (u >= 0) & (u < Wi - 1) & (w >= 0) & (w < Hi - 1)
        idx = np.nonzero(ok)[0]
        if not len(idx):
            continue
        # estimated surfaces lie BEHIND missing geometry: only views where nothing scanned is in front count
        idx = idx[z[idx] <= zb[(w[idx] * zsc).astype(int), (u[idx] * zsc).astype(int)] * (1 + 4 * depth_tol)]
        ray = X[idx] - C
        dist = np.linalg.norm(ray, axis=1) + 1e-9
        cos = np.abs(np.einsum("ij,ij->i", ray, N[idx])) / dist
        uu, ww = u[idx], w[idx]
        edge = np.clip(np.minimum.reduce([uu, ww, Wi - 1 - uu, Hi - 1 - ww]) / (edge_frac * min(Wi, Hi)), 0, 1)
        cos_fade = np.clip((cos - COS_MAX_ANGLE) / (COS_FADE_ANGLE - COS_MAX_ANGLE), 0.0, 1.0)
        wt = (cos ** power) / (dist * dist) * edge * cos_fade
        c = img[ww.astype(int), uu.astype(int)] * (v["gain"] / 255.0)
        acc[idx] += (c * wt[:, None]).astype(np.float32); wsum[idx] += wt; nv[idx] += 1
    out = np.where(wsum[:, None] > 0, acc / np.maximum(wsum, 1e-12)[:, None], 0.0)
    return out, nv


# --------------------------------------------------------------------------------------------- GPU (CuPy)
def _gpu_blend_ok():
    """CuPy importable and the configured / detected NVIDIA GPU usable (namma.recon.hardware)."""
    try:
        import cupy  # noqa: F401
        from namma.recon import hardware
        return hardware.gpu_decision(need_mb=1024)[0]
    except Exception:                                  # noqa: BLE001 - no CuPy, no driver: CPU
        return False


def _blend_views_gpu(X, N, allP, views, *, power, depth_tol, edge_frac, zbuf_px, log, label, batch=4_000_000):
    """Same weighted mean as the CPU loop, on the GPU. -> (acc float32 (n,3), wsum float32 (n,), max_cos float32 (n,), photos used).
    The next photo is decoded on a CPU thread while the GPU works on the current one."""
    import queue
    import threading

    import cupy as cp
    from cupyx.scipy import ndimage as cnd
    n = len(X)
    Xg = cp.asarray(X, cp.float32)
    Ng = cp.asarray(N, cp.float32)
    Pg = cp.asarray(allP, cp.float32)
    acc = cp.zeros((n, 3), cp.float32)
    wsum = cp.zeros(n, cp.float32)
    max_cos = cp.zeros(n, cp.float32)
    q = queue.Queue(maxsize=2)

    def producer():
        for item in _views_iter(views):
            q.put(item)
        q.put(None)
    threading.Thread(target=producer, daemon=True).start()
    used = 0
    while True:
        item = q.get()
        if item is None:
            break
        v, img_np, k = item
        used += 1
        Hi, Wi = img_np.shape[:2]
        fx, fy, cx, cy, k1, k2, p1, p2 = (float(x) for x in _camera(v["cam"], k))
        R = cp.asarray(v["R"], cp.float32)
        tv = cp.asarray(v["t"], cp.float32)
        C = cp.asarray(v["C"], cp.float32)
        gain = cp.asarray(np.asarray(v["gain"], np.float32) / 255.0)
        img = cp.asarray(img_np, cp.float32)

        def project(Q):
            Xc = Q @ R.T + tv
            z = Xc[:, 2]
            zs = cp.where(z > 1e-6, z, 1.0)
            x, y = Xc[:, 0] / zs, Xc[:, 1] / zs
            r2 = x * x + y * y
            rad = 1 + k1 * r2 + k2 * r2 * r2
            xd = x * rad + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
            yd = y * rad + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
            return fx * xd + cx, fy * yd + cy, z
        zsc = zbuf_px / max(Wi, Hi)
        u, w, z = project(Pg)
        ok = (z > 1e-6) & (u >= 0) & (u < Wi) & (w >= 0) & (w < Hi)
        zb = cp.full((int(Hi * zsc) + 1, int(Wi * zsc) + 1), cp.inf, cp.float32)
        cp.minimum.at(zb, ((w[ok] * zsc).astype(cp.int32), (u[ok] * zsc).astype(cp.int32)), z[ok])
        zb = cnd.minimum_filter(zb, size=3)
        margin = edge_frac * min(Wi, Hi)
        for s0 in range(0, n, batch):
            sl = slice(s0, min(n, s0 + batch))
            Xb = Xg[sl]
            u, w, z = project(Xb)
            ok = (z > 1e-6) & (u >= 0) & (u < Wi - 1) & (w >= 0) & (w < Hi - 1)
            idx = cp.nonzero(ok)[0]
            if not len(idx):
                continue
            idx = idx[z[idx] <= zb[(w[idx] * zsc).astype(cp.int32), (u[idx] * zsc).astype(cp.int32)] * (1 + depth_tol)]
            if not len(idx):
                continue
            ray = Xb[idx] - C
            dist = cp.linalg.norm(ray, axis=1) + 1e-9
            cos = cp.abs((ray * Ng[sl][idx]).sum(1)) / dist
            uu, ww = u[idx], w[idx]
            edge = cp.clip(cp.minimum(cp.minimum(uu, ww), cp.minimum(Wi - 1 - uu, Hi - 1 - ww)) / margin, 0, 1)
            cos_fade = cp.clip((cos - COS_MAX_ANGLE) / (COS_FADE_ANGLE - COS_MAX_ANGLE), 0.0, 1.0)
            wt = (cos ** power) / (dist * dist) * edge * cos_fade
            u0, w0 = cp.floor(uu).astype(cp.int32), cp.floor(ww).astype(cp.int32)
            fu, fw = (uu - u0)[:, None], (ww - w0)[:, None]
            c = (img[w0, u0] * (1 - fu) * (1 - fw) + img[w0, u0 + 1] * fu * (1 - fw)
                 + img[w0 + 1, u0] * (1 - fu) * fw + img[w0 + 1, u0 + 1] * fu * fw) * gain
            gidx = idx + s0
            acc[gidx] += c * wt[:, None]                 # idx unique within a view: plain fancy-index add is exact
            wsum[gidx] += wt
            cp.maximum.at(max_cos, gidx, cos)
        if used % 25 == 0:
            log(f"  {label}: {used}/{len(views)} photos blended")
    out = cp.asnumpy(acc), cp.asnumpy(wsum), cp.asnumpy(max_cos), used
    del Xg, Ng, Pg, acc, wsum, max_cos
    cp.get_default_memory_pool().free_all_blocks()
    return out
