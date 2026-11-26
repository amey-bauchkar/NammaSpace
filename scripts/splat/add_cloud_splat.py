"""Add a splat trained elsewhere (e.g. on Kaggle) to Studio, as a COPY of the project it was trained from.

  python scripts/splat/add_cloud_splat.py --project projects/<id> --ply <room.ply> --title "Scan 1 2M"

Needs room.json (the trainer writes it) beside the .ply. Lines the splat up with the model, as a local run does,
and refuses it when too little of it is within 5 cm.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "qa"))

from make_pose_test_projects import clone  # noqa: E402
from namma.core import timefmt  # noqa: E402
from namma.core.jsonio import load_strict, write_json  # noqa: E402
from namma.studio import splat_lidar as sl  # noqa: E402
from namma.studio.package import build_scene_package  # noqa: E402
from namma.studio.pipeline import RunContext  # noqa: E402
from namma.studio.splat_align import align_splat_to_mesh  # noqa: E402
from namma.studio.splat_large import ALIGN_SAMPLES_PER_M2  # noqa: E402
from namma.studio.splat_claude import MIN_SPLAT_OPACITY, MIN_WITHIN_5CM  # noqa: E402
from namma.studio.splat_spz import ply_to_spz  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--ply", type=Path, required=True)
    ap.add_argument("--title", required=True)
    a = ap.parse_args(argv)
    src = a.project.resolve()
    train = load_strict(a.ply.with_suffix(".json"))
    run = load_strict(src / "project.json")["runs"][-1]["run_id"]
    base, tail = src.name.rsplit("-", 1)
    pid = f"{base}-cloud{train['splats'] // 1000}k-{tail}"
    dst = src.parent / pid
    clone(src, dst)
    doc = json.loads((dst / "project.json").read_text(encoding="utf-8"))
    doc.update(project_id=pid, title=a.title)
    (dst / "project.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tex = dst / "runs" / run / "60_texture"
    for old in ("splat.spz", "splat.json"):
        (tex / old).unlink(missing_ok=True)
    g = tex / "lod0_unrepaired.glb"
    glb = (g if g.exists() else tex / "lod0.glb").read_bytes()
    T, fit = align_splat_to_mesh(a.ply, glb, init_T=sl.glb_from_poses(dst, "arkit"),
                                   samples_per_m2=ALIGN_SAMPLES_PER_M2)
    print(f"line-up: {fit['within_5cm']:.0%} within 5 cm (needs {MIN_WITHIN_5CM:.0%})")
    if fit["within_5cm"] < MIN_WITHIN_5CM:
        print("NOT added: too far from the model")
        return 1
    spz = ply_to_spz(a.ply, tex / "splat.spz", min_opacity=MIN_SPLAT_OPACITY)
    write_json(tex / "splat.json", {
        "file": "splat.spz", "T_mesh_from_splat": T.tolist(), "splats": spz["splats_out"], "compression": spz,
        "fit": fit, "created_at": timefmt.now_iso(), "poses": "Polycam LiDAR scan, arkit poses",
        "method": f"3D Gaussian splat (gsplat, MCMC, {train['iters']} steps) trained on a cloud GPU", "training": train})
    ctx = RunContext(dst, run, load_strict(dst / "runs" / run / "config.json")["config"])
    build_scene_package(ctx, ctx.dir("package"))
    print(f"added to Studio as '{a.title}': {spz['splats_out']} splats, {spz['bytes'] / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
