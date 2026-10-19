import json, sys, time
sys.path.insert(0, "src")
from pathlib import Path
from namma.recon import colmap, exposure, blendtex
P = Path("projects/hall-246-photos-20261018-29fa2c"); R = P / "runs/run-20261019-010810-reuse"
t0 = time.time()
txt = R / "20_sfm" / json.loads((R / "20_sfm/sfm_summary.json").read_text())["txt_dir"]
imgs = colmap.read_images_txt(txt / "images.txt"); cams = colmap.read_cameras_txt(txt / "cameras.txt")
gains, st = exposure.estimate_gains(exposure.read_observations(txt / "images.txt"), R / "10_images", sorted(imgs))
views = blendtex.make_views(imgs, cams, R / "10_images", gains, originals_dir=P / "capture/originals")
print(len(views), "views;", sum(1 for v in views if v["path"].suffix.upper() == ".JPG"), "from originals", flush=True)
src = Path("own_capture/hall_report/texture_ab/default/lod0.glb")
out = Path("own_capture/hall_report/blend/lod0_blend_fullres_x2.glb")
stats = blendtex.blend_texture_glb(src, out, views, scale=2, log=lambda m: print(m, round(time.time() - t0), "s", flush=True))
print(stats, round(time.time() - t0), "s", out.stat().st_size // 2**20, "MB", flush=True)
