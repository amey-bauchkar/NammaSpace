from pathlib import Path
# 1. blendtex: colour arbitrary points exactly like the blended texture
p = Path("src/namma/recon/blendtex.py"); t = p.read_text(encoding="utf-8")
t += '''

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
        wt = (cos ** power) / (dist * dist) * edge
        c = img[ww.astype(int), uu.astype(int)] * (v["gain"] / 255.0)
        acc[idx] += (c * wt[:, None]).astype(np.float32); wsum[idx] += wt; nv[idx] += 1
    out = np.where(wsum[:, None] > 0, acc / np.maximum(wsum, 1e-12)[:, None], 0.0)
    return out, nv
'''
p.write_text(t, encoding="utf-8")

# 2. roomfill: a sampler adapter (model frame -> SfM frame)
p = Path("src/namma/recon/roomfill.py"); t = p.read_text(encoding="utf-8")
t += '''

class BlendSampler:
    """Fill colours computed exactly like the blended texture (namma.recon.blendtex.blend_points)."""

    def __init__(self, views, mesh_vertices_sfm, T_model_from_sfm):
        self.views, self.mesh = views, mesh_vertices_sfm
        self.Tinv = np.linalg.inv(np.asarray(T_model_from_sfm, float).reshape(4, 4))

    def __call__(self, pts_model, normal_model):
        from namma.recon import blendtex
        X = pts_model @ self.Tinv[:3, :3].T + self.Tinv[:3, 3]
        n = self.Tinv[:3, :3] @ normal_model
        n = n / np.linalg.norm(n)
        return blendtex.blend_points(X, np.broadcast_to(n, X.shape), self.views, self.mesh)
'''
p.write_text(t, encoding="utf-8")

# 3. pipeline: use the blend sampler when blending is on; offset / cell from config
p = Path("src/namma/studio/pipeline.py"); t = p.read_text(encoding="utf-8")
old = '''                sampler = roomfill.PhotoSampler(colmap.read_images_txt(txt_dir / "images.txt"),
                                                colmap.read_cameras_txt(txt_dir / "cameras.txt"), ctx.dir("prepare"), T)
                V, Cc, F, planes = roomfill.build_fill(P, C, box, N=N, photo_sampler=sampler, cell_eye=cfg["cell_eye"],
                                                        slab_eye=cfg["slab_eye"], min_pts=cfg["min_pts"])'''
assert t.count(old) == 1
new = '''                imgs = colmap.read_images_txt(txt_dir / "images.txt")
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
                                                        offset_eye=cfg.get("offset_eye", 0.01))'''
t = t.replace(old, new)
p.write_text(t, encoding="utf-8")

# 4. config: finer cells, deeper offset
p = Path("src/namma/studio/profiles.py"); t = p.read_text(encoding="utf-8")
old = '''    "fill": {"enabled": True, "cell_eye": 0.06, "slab_eye": 0.12, "min_pts": 3, "min_fit_near_wall": 0.25,
             "max_outside_share": 0.15},'''
assert t.count(old) == 1
t = t.replace(old, '''    "fill": {"enabled": True, "cell_eye": 0.03, "slab_eye": 0.12, "min_pts": 3, "min_fit_near_wall": 0.25,
             "max_outside_share": 0.15, "offset_eye": 0.035},''')
t = t.replace('PIPELINE_VERSION = "13.9.0"', 'PIPELINE_VERSION = "13.10.0"')
p.write_text(t, encoding="utf-8")
print("ok")
