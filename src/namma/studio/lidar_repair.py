"""Re-apply the gentle LiDAR repair to an already imported Polycam LiDAR scan (implementation_plan.md, Phase 4).

New imports of a Polycam LiDAR scan get mesh_repair.LIDAR_OPTIONS automatically (polycam_bridge). Scans imported
before that were repaired with the photogrammetry settings, which replace the real ceiling with a plain sheet and
move floor and walls. This rebuilds such a run from its 60_texture/lod0_unrepaired.glb (Polycam's model with only
the floating fragments removed):
  lod0.glb / lod1.glb       repaired with LIDAR_OPTIONS (the old ones kept once as lod*_before_lidar_repair.glb)
  mirrors                   if mirrors were marked, their base copies are replaced too and the cut is re-applied
  walking maps, quality report, package   rebuilt by namma.studio.mirrors.apply_to_run (with or without mirrors)
The model's frame does not change, so the photoreal view and labels stay where they are.

  python -m namma.studio.lidar_repair --project projects/<id> [--run <run id>]
"""

from __future__ import annotations

from pathlib import Path


def apply_to_run(project_dir, run_id, *, log=None):
    from namma.core.jsonio import load_strict, write_json
    from namma.studio import mirrors
    from namma.studio.mesh_repair import LIDAR_OPTIONS, repair
    from namma.studio.mesh_tidy import downscale_textures
    log = log or (lambda *_: None)
    tex = Path(project_dir) / "runs" / run_id / "60_texture"
    base = tex / "lod0_unrepaired.glb"
    if not base.exists():
        raise FileNotFoundError(f"{base} missing: this run was imported without the repair step")
    for name in ("lod0.glb", "lod1.glb"):                         # what the photogrammetry repair made, once
        src, bak = tex / name, tex / name.replace(".glb", "_before_lidar_repair.glb")
        if src.exists() and not bak.exists():
            bak.write_bytes(src.read_bytes())
    glb, stats = repair(base.read_bytes(), **LIDAR_OPTIONS)
    stats["options"] = dict(LIDAR_OPTIONS)
    lod1, _ = downscale_textures(glb, 2048)
    for name, data in (("lod0.glb", glb), ("lod1.glb", lod1)):
        (tex / name).write_bytes(data)
        before_mirrors = tex / name.replace(".glb", "_before_mirrors.glb")
        if before_mirrors.exists():                               # mirror cuts start from these: give them the new base
            before_mirrors.write_bytes(data)
    summ = load_strict(tex / "texture_summary.json")
    summ["repair"] = stats
    write_json(tex / "texture_summary.json", summ)
    res = mirrors.apply_to_run(project_dir, run_id, log=log)     # lods' sizes, walking maps, health, package
    log(f"LiDAR repair applied: {stats.get('filled_holes')} hole(s) closed, ceiling kept")
    return {"repair": {k: stats.get(k) for k in ("filled_holes", "plain_ceiling_triangles", "flattened_vertices",
                                                 "max_vertex_move_m")}, "mirrors": res}


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Re-apply the LiDAR repair to an imported Polycam LiDAR scan")
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--run")
    a = ap.parse_args()
    run = a.run or sorted(p.name for p in (a.project / "runs").iterdir() if (p / "60_texture").is_dir())[-1]
    print(json.dumps(apply_to_run(a.project, run, log=print), indent=1, default=str))
