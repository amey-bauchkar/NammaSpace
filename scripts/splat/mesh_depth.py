"""Depth maps of the Polycam mesh from every training camera (namma.studio.splat.mesh_depth_maps).

    python scripts/splat/mesh_depth.py --model <sparse dir> --glb lod0_unrepaired.glb --align align.json --out <dir>
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from namma.studio.splat import mesh_depth_maps  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--glb", required=True)
    ap.add_argument("--align", required=True, help="json with T_mesh_from_splat (cameras' frame -> mesh frame)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scale", type=float, default=0.25)
    a = ap.parse_args()
    T = json.loads(Path(a.align).read_text(encoding="utf-8"))["T_mesh_from_splat"]
    print(json.dumps({"depth_maps": mesh_depth_maps(a.model, Path(a.glb).read_bytes(), T, a.out, scale=a.scale)}))
