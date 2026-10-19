"""Prototype: build the estimated-surface fill for an existing run (development tool)."""
import json, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.recon.io import read_ply
from namma.recon import roomfill as rf
R, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
t0 = time.time()
pkg = json.loads((R / "90_package/scene_package.json").read_text())
T = np.array(pkg["appearance"]["to_model"]["matrix_row_major"]).reshape(4, 4)
v = read_ply(R / "40_dense/scene_dense.ply")["vertex"]
P = rf.to_model(np.column_stack([v["x"], v["y"], v["z"]]).astype(np.float64), T)
N = np.column_stack([v["nx"], v["ny"], v["nz"]]) @ T[:3, :3].T
N /= np.linalg.norm(N, axis=1, keepdims=True) + 1e-12
C = np.column_stack([v["red"], v["green"], v["blue"]]) / 255.0
box = rf.fit_room_box(P, N, pkg["eye_height_units"])
from namma.recon import colmap
sfm = json.loads((R / "20_sfm/sfm_summary.json").read_text())
txt = R / "20_sfm" / sfm["txt_dir"]
sampler = rf.PhotoSampler(colmap.read_images_txt(txt / "images.txt"), colmap.read_cameras_txt(txt / "cameras.txt"), R / "10_images", T)
V, Cc, F, st = rf.build_fill(P, C, box, N=N, photo_sampler=sampler)
rf.write_fill_glb(out / "fill.glb", V, Cc, F)
(out / "fill.json").write_text(json.dumps({"box": box, "planes": st, "triangles": int(len(F))}, indent=1))
for s in st: print(s["plane"], round(s["covered_share"], 2), s["filled_cells"], "photo-coloured", s["filled_cells_coloured_from_photos"])
print(len(F), "triangles", round(time.time() - t0, 1), "s")
