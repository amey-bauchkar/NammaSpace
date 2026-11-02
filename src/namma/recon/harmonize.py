"""Texture seam harmonisation (CPU): removes the colour / brightness patchwork between texture patches.

OpenMVS paints each face from ONE photo. Neighbouring patches come from different photos, so on glossy or plain
surfaces (a varnished door, white paint) their brightness differs and the seams show. OpenMVS's seam levelling
blackens texels in the 2.4.0 build (C-02; re-measured: 49 % black texels), so we correct the atlas ourselves:

1. target colour of every mesh vertex = visibility-checked, angle-weighted mean over all photos that see it
   (depth test against a per-photo z-buffer splatted from the mesh vertices);
2. patch colour at every glTF vertex (glTF duplicates a vertex per patch) = the atlas around its UV;
3. per-vertex gain = target / patch colour, spread over the patch's texels (low-resolution splat, nearest fill,
   blur) and multiplied into the atlas - texture detail stays, only the patch-level colour moves.
"""
import base64
import io
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from namma.recon.glb import read_glb, write_glb


def _buffers(doc, binary):
    return [binary if k == 0 and "uri" not in b else base64.b64decode(b["uri"].split(",", 1)[1])
            for k, b in enumerate(doc.get("buffers", []))]


_CT = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}


def _accessor(doc, bufs, i):
    a = doc["accessors"][i]
    bv = doc["bufferViews"][a["bufferView"]]
    dt, c = np.dtype(_CT[a["componentType"]]), _NC[a["type"]]
    off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    return np.frombuffer(bufs[bv.get("buffer", 0)], dtype=dt, count=a["count"] * c, offset=off).reshape(a["count"], c)


def _project(views, X):
    """Yield (view, u, w, z, ok) for points X (SfM frame)."""
    for v in views:
        Xc = X @ v["R"].T + v["t"]
        z = Xc[:, 2]
        zs = np.where(z > 1e-6, z, 1.0)
        x, y = Xc[:, 0] / zs, Xc[:, 1] / zs
        p = v["cam"]["params"]
        fx, fy, cx, cy = p[:4]
        if v["cam"]["model"] == "OPENCV":
            k1, k2, p1, p2 = p[4:8]
            r2 = x * x + y * y
            rad = 1 + k1 * r2 + k2 * r2 * r2
            x, y = x * rad + 2 * p1 * x * y + p2 * (r2 + 2 * x * x), y * rad + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
        u, w = (fx * x + cx) * v["s"], (fy * y + cy) * v["s"]
        H, W = v["img"].shape[:2]
        ok = (z > 1e-6) & (u >= 0) & (u < W - 1) & (w >= 0) & (w < H - 1)
        yield v, u, w, z, ok


def vertex_targets(P, Nrm, views, zbuf_scale=0.5, depth_tol=0.02):
    """Visibility-checked, angle-weighted mean photo colour per vertex (SfM frame)."""
    acc = np.zeros((len(P), 3)); wsum = np.zeros(len(P))
    for v, u, w, z, ok in _project(views, P):
        H, W = v["img"].shape[:2]
        zh, zw = max(1, int(H * zbuf_scale)), max(1, int(W * zbuf_scale))
        zi = np.clip((w[ok] * zbuf_scale).astype(int), 0, zh - 1); zj = np.clip((u[ok] * zbuf_scale).astype(int), 0, zw - 1)
        zbuf = np.full((zh, zw), np.inf)
        np.minimum.at(zbuf, (zi, zj), z[ok])
        zbuf = ndimage.minimum_filter(zbuf, size=3)            # close splat gaps
        vis = np.zeros(len(P), bool)
        idx = np.nonzero(ok)[0]
        vis[idx] = z[idx] <= zbuf[zi, zj] * (1 + depth_tol)
        ray = P[vis] - v["C"]
        cosang = np.abs(np.einsum("ij,ij->i", ray, Nrm[vis])) / (np.linalg.norm(ray, axis=1) + 1e-12)
        # Incidence angle falloff: taper views where incidence angle theta > 65 deg to 75 deg, zero weight if theta >= 75 deg
        cos_fade = np.clip((cosang - 0.25881905) / (0.42261827 - 0.25881905), 0.0, 1.0)
        wt = (np.clip(cosang, 0, 1) ** 2) * cos_fade
        col = v["img"][w[vis].astype(int), u[vis].astype(int)] * v["gain"]
        acc[vis] += col * wt[:, None]; wsum[vis] += wt
    return acc / np.maximum(wsum, 1e-9)[:, None], wsum


def harmonize_glb(src, dst, views, *, grid=512, blur=2.0, max_gain=1.8):
    doc, binary = read_glb(src)
    bufs = _buffers(doc, binary)
    prims = [p for m in doc["meshes"] for p in m["primitives"]]
    P_all, N_all, owner = [], [], []
    per = []
    for k, prim in enumerate(prims):
        P = _accessor(doc, bufs, prim["attributes"]["POSITION"]).astype(np.float64)
        UV = _accessor(doc, bufs, prim["attributes"]["TEXCOORD_0"]).astype(np.float64)
        F = _accessor(doc, bufs, prim["indices"]).reshape(-1, 3).astype(np.int64)
        # vertex normals from faces
        fn = np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]])
        Nv = np.zeros_like(P)
        for c in range(3):
            np.add.at(Nv, F[:, c], fn)
        Nv /= np.linalg.norm(Nv, axis=1, keepdims=True) + 1e-12
        tex = doc["textures"][doc["materials"][prim["material"]]["pbrMetallicRoughness"]["baseColorTexture"]["index"]]["source"]
        per.append({"P": P, "UV": UV, "tex": tex})
        P_all.append(P); N_all.append(Nv)
    P_all, N_all = np.vstack(P_all), np.vstack(N_all)
    target, wsum = vertex_targets(P_all, N_all, views)
    # atlas images
    imgs = {}
    for t in {p["tex"] for p in per}:
        bv = doc["bufferViews"][doc["images"][t]["bufferView"]]
        raw = bufs[bv.get("buffer", 0)][bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]]
        imgs[t] = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"), np.float32) / 255
    # per-atlas gain field
    off = 0
    num = {t: np.zeros((grid, grid, 3)) for t in imgs}
    den = {t: np.zeros((grid, grid)) for t in imgs}
    for p in per:
        n = len(p["P"])
        A = imgs[p["tex"]]
        H, W = A.shape[:2]
        x = np.clip((p["UV"][:, 0] * W).astype(int), 1, W - 2)
        y = np.clip((p["UV"][:, 1] * H).astype(int), 1, H - 2)
        patch = np.zeros((n, 3))
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                patch += A[y + dy, x + dx]
        patch /= 9
        tg, ws = target[off:off + n], wsum[off:off + n]
        ok = (ws > 0) & (patch.min(axis=1) > 0.02)
        g = np.clip(np.where(ok[:, None], (tg + 1e-3) / (patch + 1e-3), 1.0), 1 / max_gain, max_gain)
        gi = np.clip((p["UV"][:, 1] * grid).astype(int), 0, grid - 1); gj = np.clip((p["UV"][:, 0] * grid).astype(int), 0, grid - 1)
        np.add.at(num[p["tex"]], (gi[ok], gj[ok]), g[ok]); np.add.at(den[p["tex"]], (gi[ok], gj[ok]), 1)
        off += n
    stats = {"vertices": int(len(P_all)), "vertices_seen": int((wsum > 0).sum()), "atlases": len(imgs)}
    for t, A in imgs.items():
        has = den[t] > 0
        G = np.where(has[..., None], num[t] / np.maximum(den[t], 1)[..., None], np.nan)
        if has.any():
            _, (ii, jj) = ndimage.distance_transform_edt(~has, return_indices=True)
            G = G[ii, jj]
        else:
            G = np.ones((grid, grid, 3))
        G = np.stack([ndimage.gaussian_filter(G[..., c], blur) for c in range(3)], -1)
        H, W = A.shape[:2]
        Gf = np.asarray(Image.fromarray(np.clip(G * 100, 0, 255).astype(np.uint8)).resize((W, H), Image.BILINEAR), np.float32) / 100
        out = np.clip(A * Gf, 0, 1)
        bio = io.BytesIO()
        Image.fromarray((out * 255).astype(np.uint8)).save(bio, "JPEG", quality=90, optimize=True)
        imgs[t] = bio.getvalue()
        stats.setdefault("gain_median", []).append([round(float(x), 3) for x in np.median(G.reshape(-1, 3), axis=0)])
    # rebuild: images become new bufferViews in buffer 0 (appended)
    buf0 = bytearray(bufs[0])
    for t, data in imgs.items():
        buf0 += b"\x00" * (-len(buf0) % 4)
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(buf0), "byteLength": len(data)})
        buf0 += data
        doc["images"][t] = {"bufferView": len(doc["bufferViews"]) - 1, "mimeType": "image/jpeg", "name": f"texture_{t}"}
    doc["buffers"][0]["byteLength"] = len(buf0) + (-len(buf0) % 4)
    doc.setdefault("asset", {})["generator"] = (doc["asset"].get("generator", "") + " + seams harmonised by namma.recon.harmonize").strip()
    write_glb(dst, doc, bytes(buf0))
    return stats


def load_views(images, cameras, image_dir, gains=None, max_side=600):
    views = []
    for name, im in images.items():
        f = Path(image_dir) / name
        if not f.exists():
            continue
        with Image.open(f) as pil:
            s = max_side / max(pil.size)
            arr = np.asarray(pil.convert("RGB").resize((round(pil.size[0] * s), round(pil.size[1] * s))), np.float32) / 255
        g = np.asarray((gains or {}).get(name, [1, 1, 1]), np.float32)
        views.append({"R": im["R"], "t": im["t"], "C": im["C"], "cam": cameras[im["camera_id"]], "s": s, "img": arr, "gain": g})
    return views
