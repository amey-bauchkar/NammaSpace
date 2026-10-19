from pathlib import Path
p = Path("src/namma/recon/blendtex.py"); t = p.read_text(encoding="utf-8")
old = "def blend_texture_glb(src, dst, views, *, power=4, depth_tol=0.015, edge_frac=0.05, scale=1, zbuf_px=400, log=print):"
assert t.count(old) == 1
t = t.replace(old, "def blend_texture_glb(src, dst, views, *, power=4, depth_tol=0.015, edge_frac=0.05, scale=1, max_side=8192,\n                      zbuf_px=400, log=print):")
old = "        W, H = A0.size[0] * scale, A0.size[1] * scale"
assert t.count(old) == 1
t = t.replace(old, "        sc = max(1, min(scale, max_side // max(A0.size)))         # never beyond max_side (browser GPU memory)\n        W, H = A0.size[0] * sc, A0.size[1] * sc")
t = t.replace("        out = np.asarray(A0.resize((W, H), Image.LANCZOS) if scale != 1 else A0, np.float32) / 255",
              "        out = np.asarray(A0.resize((W, H), Image.LANCZOS) if sc != 1 else A0, np.float32) / 255")
t = t.replace("        near = ndimage.binary_dilation(mask, iterations=2 * scale) & ~mask", "        near = ndimage.binary_dilation(mask, iterations=2 * sc) & ~mask")
p.write_text(t, encoding="utf-8")

p = Path("src/namma/studio/pipeline.py"); t = p.read_text(encoding="utf-8")
old = '''        views = blendtex.make_views(imgs, colmap.read_cameras_txt(txt_dir / "cameras.txt"), ctx.dir("prepare"), gains)'''
assert t.count(old) == 1
t = t.replace(old, '''        originals = ctx.project_dir / "capture" / "originals" if cfg.get("use_originals") else None
        views = blendtex.make_views(imgs, colmap.read_cameras_txt(txt_dir / "cameras.txt"), ctx.dir("prepare"), gains,
                                    originals_dir=originals)''')
old = '''        st = blendtex.blend_texture_glb(glb, tmp, views, power=cfg.get("power", 4), log=log)'''
assert t.count(old) == 1
t = t.replace(old, '''        st = blendtex.blend_texture_glb(glb, tmp, views, power=cfg.get("power", 4), scale=cfg.get("scale", 1),
                                        max_side=cfg.get("max_side", 8192), log=log)''')
old = '''            if "photos blended" in msg:
                done = int(msg.split("/")[0].split()[-1])
                ctx.set_progress("texture", f"Blending all photos into the texture ({done}/{total})", None)'''
assert t.count(old) == 1
t = t.replace(old, '''            if "photos blended" in msg:
                done = int(msg.split("/")[0].split()[-1])
                part = msg.split(":")[0].strip()
                ctx.set_progress("texture", f"Blending all photos into the texture, {part} ({done}/{total})", None)''')
p.write_text(t, encoding="utf-8")

p = Path("src/namma/studio/profiles.py"); t = p.read_text(encoding="utf-8")
old = '''    "blend": {"enabled": True, "power": 4},'''
assert t.count(old) == 1
t = t.replace(old, '''    "blend": {"enabled": True, "power": 4, "scale": 2, "max_side": 8192, "use_originals": True},''')
t = t.replace('PIPELINE_VERSION = "13.8.0"', 'PIPELINE_VERSION = "13.9.0"')
p.write_text(t, encoding="utf-8")
print("ok")
