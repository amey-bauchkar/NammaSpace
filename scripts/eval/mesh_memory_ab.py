"""Development A/B of ReconstructMesh point spacing and face cap (D13-14) on an evaluated ETH3D run.

    python scripts/eval/mesh_memory_ab.py --run-dir <project>/runs/<run> --gt <eth3d scene dir>
        [--configs 1.5:0,1.9:0,2.5:0,3.5:0,1.5:1500000] [--texture 1.5:0,1.5:1500000]

Each config is "<min-point-distance px>:<target faces, 0 = no cap>" (OpenMVS default distance 1.5 px). ReconstructMesh
is run again on the run's existing dense output (into <run>/eval/mesh_memory_ab/<config>/; nothing in the run itself
changes); its peak memory is logged and the mesh surface is compared with the laser scan (Sim(3) from
<run>/eval/metrics.json). Configs listed in --texture are also textured (Standard LOD0 settings) to time the atlas.
Results: <run>/eval/mesh_memory_ab.json. Development scenes only - ground truth never influences the reconstruction.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from namma.evaluation import gt as G  # noqa: E402
from namma.evaluation import metrics as M  # noqa: E402
from namma.recon.io import read_ply  # noqa: E402
from namma.recon.runner import run_step  # noqa: E402
from namma.recon.tools import find_tools, mvs_threads  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--gt", required=True)
    ap.add_argument("--configs", default="1.5:0,1.9:0,2.5:0,3.5:0,1.5:1500000")
    ap.add_argument("--texture", default="1.5:0,1.5:1500000")
    ap.add_argument("--samples", type=int, default=1_500_000)
    a = ap.parse_args()
    run = Path(a.run_dir).resolve()
    m = json.loads((run / "eval" / "metrics.json").read_text(encoding="utf-8"))
    Mx = np.array(m["pose"]["matrix_est_to_gt"])
    g = G.eth3d(a.gt)
    tools = find_tools()
    dense = run / "40_dense"
    base = run / "eval" / "mesh_memory_ab"
    out = {"run": str(run), "dense_points": json.loads((dense / "stage.json").read_text(encoding="utf-8"))["result"].get("dense_points"),
           "dense_completeness_at_5cm": m["geometry"]["completeness_at_5cm"], "rows": []}
    textured = set(a.texture.split(",")) if a.texture else set()
    for cfg in a.configs.split(","):
        d, faces = float(cfg.split(":")[0]), int(cfg.split(":")[1])
        work = base / f"d{d:g}_f{faces}"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        argv = [tools.mvs("ReconstructMesh"), "-i", dense / "scene_dense.mvs", "-p", dense / "scene_dense.ply",
                "-o", work / "mesh.ply", "-w", dense, "--max-threads", mvs_threads(), "--min-point-distance", d]
        if faces:
            argv += ["--target-face-num", faces]
        # working folder = the dense folder: image paths in the scene are relative to a folder at that depth
        rec = run_step(f"ab_reconstruct_mesh_d{d:g}_f{faces}", argv, work, timeout_s=5400, cwd=dense)
        mesh = read_ply(work / "mesh.ply")
        mv = mesh["vertex"]
        verts = M.apply(Mx, np.column_stack([mv["x"], mv["y"], mv["z"]]).astype(np.float64))
        r = M.cloud_metrics(G.sample_mesh(verts, mesh["faces"], a.samples, seed=1), g["points"])
        row = {"config": cfg, "min_point_distance": d, "target_faces": faces, "peak_rss_mb": rec["peak_rss_mb"], "wall_s": rec["wall_s"],
               "faces": int(len(mesh["faces"])), "accuracy_median_m": r["accuracy_m"]["median"],
               "accuracy_at_5cm": r["accuracy_at_5cm"], "completeness_at_5cm": r["completeness_at_5cm"],
               "completeness_at_10cm": r["completeness_at_10cm"]}
        if cfg in textured:
            tex = run_step(f"ab_texture_{cfg.replace(':', '_')}", [
                tools.mvs("TextureMesh"), "-i", dense / "scene_dense.mvs", "-m", work / "mesh.ply", "-o", work / "tex.glb",
                "-w", dense, "--export-type", "glb", "--resolution-level", 0, "--decimate", 1.0, "--max-texture-size", 8192,
                "--max-threads", mvs_threads(), "--global-seam-leveling", 0, "--local-seam-leveling", 0,
                "--empty-color", 0x808080], work, timeout_s=5400, cwd=dense)
            log = max(dense.glob("TextureMesh-*.log"), key=lambda f: f.stat().st_mtime).read_text(encoding="utf-8", errors="replace")
            pat = re.search(r"faces, (\d+) patches", log)
            row.update(texture_wall_s=tex["wall_s"], texture_peak_rss_mb=tex["peak_rss_mb"],
                       texture_patches=int(pat.group(1)) if pat else None)
            for f in work.glob("tex*"):
                f.unlink()
        out["rows"].append(row)
        print(json.dumps(row), flush=True)
        (work / "mesh.ply").unlink()            # keep logs and numbers, not the 100+ MB meshes
    (run / "eval" / "mesh_memory_ab.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
