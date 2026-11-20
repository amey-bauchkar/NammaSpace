"""Repair an already-imported 3D scan again with the current mesh repair (CPU only, about a minute).

    python scripts/rerepair_import.py projects/large-room-20261030-880c8c [--run RUN_ID] [--dry-run]

Scans imported before multi-piece support (2026-11-17) were not repaired at all when the export came in several
pieces (Polycam splits large scans across texture atlases: the 1BHK "Large room" has 4). This:
  1. keeps the current model as 60_texture/lod0_unrepaired.glb (once; it is the input every time after that);
  2. repairs it (ceiling, holes, backing sheets, straight walls, texture padding: namma.studio.mesh_repair);
  3. writes lod0.glb / lod1.glb and the repair statistics;
  4. rebuilds the walking maps, the quality report (model health) and the scene package (marked mirrors are applied
     again on the repaired model).
Revert: copy lod0_unrepaired.glb back to lod0.glb and run with --undo (or restore the files from git).
The photoreal view is not touched: make it again afterwards (it trains on the unrepaired mesh, as before).
"""
import argparse
import json
import sys
from pathlib import Path

from _bootstrap import ROOT

sys.path.insert(0, str(ROOT / "src"))


def main(argv=None):
    from namma.core.jsonio import load_strict, write_json
    from namma.studio import mirrors as mir
    from namma.studio import model_health as mh
    from namma.studio.mesh_import import mesh_triangles
    from namma.studio.mesh_repair import repair
    from namma.studio.mesh_tidy import downscale_textures
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--run")
    ap.add_argument("--dry-run", action="store_true", help="print what the repair would do; write nothing")
    ap.add_argument("--undo", action="store_true", help="go back to the unrepaired model")
    a = ap.parse_args(argv)
    pdir = Path(a.project).resolve()
    runs = sorted((pdir / "runs").iterdir())
    run = pdir / "runs" / a.run if a.run else runs[-1]
    tex = run / "60_texture"
    cfg = load_strict(run / "config.json")["config"]
    if cfg.get("profile") != "import":
        raise SystemExit("only imported 3D scans are re-repaired")
    src = tex / "lod0_unrepaired.glb"
    base = src.read_bytes() if src.exists() else (tex / "lod0.glb").read_bytes()
    V, F, _ = mesh_triangles(base)
    before = mh.mesh_health(V, F)
    if a.undo:
        if not src.exists():
            raise SystemExit("nothing to undo (no lod0_unrepaired.glb)")
        out, st = base, {"applied": False, "skipped": "undo: the unrepaired model is used"}
    else:
        out, st = repair(base, pad_texture=not a.dry_run)
    print(json.dumps({"pieces": st.get("pieces", 1), "applied": st.get("applied"), "skipped": st.get("skipped"),
                      **{k: st.get(k) for k in ("removed_above_triangles", "removed_hanging_object_triangles",
                                                "flattened_vertices", "max_vertex_move_m", "plain_ceiling_triangles",
                                                "filled_holes")},
                      "ceiling": st.get("ceiling"), "holes_before": before["holes"],
                      "largest_hole_before_m": before["largest_hole_perimeter_m"]}, indent=1))
    if a.dry_run:
        return 0
    if not st.get("applied") and not a.undo:
        print("nothing to repair; files unchanged")
        return 0
    if not src.exists():
        src.write_bytes((tex / "lod0.glb").read_bytes())
    (tex / "lod0.glb").write_bytes(out)
    summ = load_strict(tex / "texture_summary.json")
    V2, F2, ms = mesh_triangles(out)
    for lod in summ["lods"]:
        if lod["lod"] == 0:
            lod.update(bytes=len(out), triangles=ms["triangles"])
            lod["source"] = lod["source"].split(", repaired")[0] + (
                f", repaired ({st.get('pieces', 1)} piece(s), {st['filled_holes']} hole(s) closed)" if st.get("applied") else "")
        elif lod["lod"] == 1:
            lod1, _ = downscale_textures(out, 2048)
            (tex / "lod1.glb").write_bytes(lod1)
            lod.update(bytes=len(lod1), triangles=ms["triangles"])
    summ["repair"] = st
    write_json(tex / "texture_summary.json", summ)
    for stale in tex.glob("*_before_mirrors.glb"):           # mirrors are applied again on the repaired model
        stale.unlink()
    res = mir.apply_to_run(pdir, run.name)                     # walking maps + quality report + package
    after = mh.mesh_health(V2, F2)
    print(json.dumps({"holes_after": after["holes"], "largest_hole_after_m": after["largest_hole_perimeter_m"],
                      "mirrors": res["regions"], "package": str(run / "90_package")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
