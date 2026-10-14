"""Re-texture an existing run's mesh with TextureMesh option variants (pre-capture QA, QA-03).

    python scripts/qa/texture_ab.py --run-dir <run> --out <dir> --variant default: --variant nooutlier:--outlier-threshold=0

Uses the run's own LOD0 settings; writes <out>/<variant>/lod0.glb, an atlas preview PNG and results.json. The run is not changed.
"""
import argparse
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from namma.core.jsonio import load_strict  # noqa: E402
from namma.recon.glb import read_glb  # noqa: E402
from namma.recon.runner import run_step  # noqa: E402
from namma.recon.tools import find_tools, mvs_threads  # noqa: E402
from texture_coverage import surface_grey_share  # noqa: E402
from PIL import Image  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--variant", action="append", required=True, help="name:--opt=value,--opt2=value")
    a = ap.parse_args()
    run, out = Path(a.run_dir).resolve(), Path(a.out).resolve()
    cfg = load_strict(run / "config.json")["config"]
    t0 = cfg["textures"][0]
    dense, mesh = run / "40_dense", run / "50_mesh" / "mesh.ply"
    tools = find_tools()
    rows = []
    for v in a.variant:
        name, _, opts = v.partition(":")
        extra = [x for o in opts.split(",") if o for x in o.split("=", 1)]
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        argv = [tools.mvs("TextureMesh"), "-i", dense / "scene_dense.mvs", "-m", mesh, "-o", d / "tex.glb", "-w", dense,
                "--export-type", "glb", "--resolution-level", t0["resolution_level"], "--decimate", t0["decimate"],
                "--max-texture-size", t0["max_texture_size"], "--max-threads", mvs_threads(),
                "--global-seam-leveling", 0, "--local-seam-leveling", 0, "--empty-color", 0x808080, *extra]
        rec = run_step(f"texture_{name}", argv, d, timeout_s=1800, cwd=dense)
        from namma.recon.glb import embed_textures
        embed_textures(d / "tex.glb", d / "lod0.glb", max_side=t0["embed_max_side"], jpeg_quality=90)
        for f in d.glob("tex*"):
            f.unlink()
        share = surface_grey_share(d / "lod0.glb")
        doc, binary = read_glb(d / "lod0.glb")
        bv = doc["bufferViews"][doc["images"][0]["bufferView"]]
        im = Image.open(io.BytesIO(binary[bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]]))
        im.thumbnail((2048, 2048)); im.save(d / "atlas0_preview.png")
        rows.append({"variant": name, "options": extra, "texture_wall_s": rec["wall_s"], "peak_rss_mb": rec["peak_rss_mb"],
                     "grey_share": round(share, 4), "patch_pixels": n})
        print(json.dumps(rows[-1]), flush=True)
    (out / "results.json").write_text(json.dumps({"run": str(run), "rows": rows}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
