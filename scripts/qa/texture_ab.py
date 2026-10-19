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
import numpy as np  # noqa: E402


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
                "--empty-color", 0x808080, *extra]
        if "--global-seam-leveling" not in extra:
            argv += ["--global-seam-leveling", 0]
        if "--local-seam-leveling" not in extra:
            argv += ["--local-seam-leveling", 0]
        rec = run_step(f"texture_{name}", argv, d, timeout_s=1800, cwd=dense)
        from namma.recon.glb import embed_textures
        embed_textures(d / "tex.glb", d / "lod0.glb", max_side=t0["embed_max_side"], jpeg_quality=90)
        for f in d.glob("tex*"):
            f.unlink()
        share = surface_grey_share(d / "lod0.glb")
        doc, binary = read_glb(d / "lod0.glb")
        bv = doc["bufferViews"][doc["images"][0]["bufferView"]]
        im = Image.open(io.BytesIO(binary[bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]]))
        a = np.asarray(im.convert("RGB")).astype(int)
        patch = np.abs(a - 128).max(axis=2) > 6                 # not the grey atlas background
        black = patch & (a.max(axis=2) <= 8)
        black_share = float(black.sum() / max(patch.sum(), 1))
        im.thumbnail((2048, 2048)); im.save(d / "atlas0_preview.png")
        import re as _re
        logf = max((run / "40_dense").glob("TextureMesh-*.log"), key=lambda f: f.stat().st_mtime).read_text(encoding="utf-8", errors="replace")
        m1 = _re.search(r"faces, (\d+) patches", logf); m2 = _re.search(r"atlas and image completed: (\d+) patches", logf)
        rows.append({"variant": name, "patches_assigned": int(m1.group(1)) if m1 else None,
                     "patches_packed": int(m2.group(1)) if m2 else None, "options": extra, "texture_wall_s": rec["wall_s"], "peak_rss_mb": rec["peak_rss_mb"],
                     "surface_grey_share": round(share, 4), "black_texel_share": round(black_share, 4)})
        print(json.dumps(rows[-1]), flush=True)
    (out / "results.json").write_text(json.dumps({"run": str(run), "rows": rows}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
