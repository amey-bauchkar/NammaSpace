"""Before / after quality table for existing Studio scenes (run on the RTX 4050 machine; reads files only).

    python scripts/verify_quality.py --projects projects --save-baseline docs/quality_baseline.json
    ... make the changes / re-run photoreal / apply mirrors ...
    python scripts/verify_quality.py --projects projects --baseline docs/quality_baseline.json

For every project's latest finished run it collects:
  - held-out photo scores of the photoreal splat (PSNR raw / after colour fit / after pose + colour fit, SSIM;
    LPIPS when the training summary has it) from 60_texture/splat.json -> training.heldout;
  - splats per m2 of floor per 2 m block (median, worst) from splat.json -> density;
  - mesh health (holes, largest hole, non-manifold edges, flipped edges, texels per metre) - recomputed from
    60_texture/lod0.glb with --recompute, else from the quality report;
  - mirrors: regions, geometry removed, inferred area, splats excluded, masked photos, residue behind the wall.
Writes docs/QUALITY_RESULTS.md (table) and docs/QUALITY_RESULTS.json. Nothing in the projects is changed.
"""
import argparse
import json
import sys
from pathlib import Path

from _bootstrap import ROOT

sys.path.insert(0, str(ROOT / "src"))

COLUMNS = [("psnr_raw", "held-out PSNR raw", "+"), ("psnr_colour", "PSNR colour fit", "+"),
           ("psnr_pose_colour", "PSNR pose+colour", "+"), ("ssim", "SSIM", "+"), ("lpips", "LPIPS", "-"),
           ("splats_per_m2_median", "splats/m2 median", "+"), ("splats_per_m2_min", "splats/m2 worst block", "+"),
           ("holes", "holes", "-"), ("largest_hole_m", "largest hole outline m", "-"),
           ("non_manifold", "non-manifold edges", "-"), ("flipped_share", "flipped edge share", "-"),
           ("texels_per_m", "texels per m", "+"), ("mirror_regions", "mirrors", ""),
           ("mirror_removed_m2", "reflected geometry removed m2", ""), ("mirror_inferred_m2", "inferred m2", ""),
           ("mirror_residue_m2", "residue behind wall m2", "-"), ("splats_excluded", "splats removed behind mirrors", ""),
           ("masked_photos", "masked photos", "")]


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8")) if Path(p).exists() else None


def latest_run(pdir):
    runs = sorted(p for p in (pdir / "runs").glob("*") if (p / "90_package" / "scene_package.json").exists())
    return runs[-1] if runs else None


def collect(run, recompute=False):
    row = {}
    sj = _load(run / "60_texture" / "splat.json") or {}
    ho = (sj.get("training") or {}).get("heldout") or {}
    row.update(psnr_raw=ho.get("raw"), psnr_colour=ho.get("colour"), psnr_pose_colour=ho.get("pose_colour"),
               ssim=ho.get("ssim"), lpips=ho.get("lpips"))
    den = sj.get("density") or {}
    row.update(splats_per_m2_median=den.get("per_m2_median"), splats_per_m2_min=den.get("per_m2_min"))
    q = _load(run / "80_quality" / "quality_report.json") or {}
    facts = q.get("facts") or {}
    mh = facts.get("model_health") or {}
    if recompute and (run / "60_texture" / "lod0.glb").exists():
        from namma.studio import model_health
        from namma.studio.mesh_import import mesh_triangles
        glb = (run / "60_texture" / "lod0.glb").read_bytes()
        V, F, _ = mesh_triangles(glb)
        mh = {**mh, "mesh": model_health.mesh_health(V, F), "texture": model_health.texture_density(glb)}
    mesh = mh.get("mesh") or {}
    row.update(holes=mesh.get("holes"), largest_hole_m=mesh.get("largest_hole_perimeter_m"),
               non_manifold=mesh.get("non_manifold_edges"), flipped_share=mesh.get("flipped_edge_share"),
               texels_per_m=(mh.get("texture") or {}).get("texels_per_m"))
    mi = facts.get("mirrors") or {}
    sm = sj.get("mirrors") or {}
    row.update(mirror_regions=mi.get("regions", 0), mirror_removed_m2=mi.get("geometry_removed_m2"),
               mirror_inferred_m2=mi.get("inferred_area_m2"), mirror_residue_m2=mh.get("geometry_behind_wall_m2"),
               splats_excluded=sm.get("removed_splats"), masked_photos=sm.get("masked_photos"))
    row["mirror_suggestions"] = len(mh.get("mirror_suggestions") or [])
    return row


def fmt(v):
    if v is None:
        return "–"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", default=str(ROOT / "projects"))
    ap.add_argument("--baseline", help="JSON written earlier with --save-baseline: adds a 'before' column per value")
    ap.add_argument("--save-baseline", help="write this run's values as the baseline JSON")
    ap.add_argument("--recompute", action="store_true", help="recompute mesh health from lod0.glb (CPU, minutes)")
    ap.add_argument("--out", default=str(ROOT / "docs" / "QUALITY_RESULTS.md"))
    a = ap.parse_args(argv)
    rows = {}
    for pdir in sorted(Path(a.projects).iterdir()):
        if not (pdir / "project.json").exists():
            continue
        run = latest_run(pdir)
        if run is None:
            continue
        title = (_load(pdir / "project.json") or {}).get("title", pdir.name)
        rows[pdir.name] = {"title": title, "run": run.name, **collect(run, a.recompute)}
    base = _load(a.baseline) if a.baseline else None
    lines = ["# Quality results", "", f"Projects: `{a.projects}`" + (f"; baseline `{a.baseline}`" if base else ""), ""]
    for pid, r in rows.items():
        lines += [f"## {r['title']} (`{pid}`, run `{r['run']}`)", "", "| value | " + ("before | after | change |" if base else "now |"),
                  "|---|" + ("---|---|---|" if base else "---|")]
        b = (base or {}).get(pid, {})
        for key, label, better in COLUMNS:
            v = r.get(key)
            if base:
                bv = b.get(key)
                ch = ""
                if isinstance(v, (int, float)) and isinstance(bv, (int, float)) and better:
                    ok = (v >= bv) if better == "+" else (v <= bv)
                    ch = ("better" if ok and v != bv else "same" if v == bv else "WORSE")
                lines.append(f"| {label} | {fmt(bv)} | {fmt(v)} | {ch} |")
            else:
                lines.append(f"| {label} | {fmt(v)} |")
        if r.get("mirror_suggestions"):
            lines.append(f"\n{r['mirror_suggestions']} possible mirror area(s) suggested: check them in the viewer.")
        lines.append("")
    Path(a.out).write_text("\n".join(lines), encoding="utf-8")
    Path(a.out).with_suffix(".json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    if a.save_baseline:
        Path(a.save_baseline).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"{len(rows)} project(s) -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
