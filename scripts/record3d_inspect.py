"""Inspect a Record3D (or other LiDAR) export before anything is processed. The source is only read.

    python scripts/record3d_inspect.py <export folder | .zip | .r3d | file>            # report -> runs/lidar_inspect/
    python scripts/record3d_inspect.py <export> --measure                              # + sampled point statistics
    python scripts/record3d_inspect.py <export> --copy D:/NammaWork/room1_export       # verified working copy
    python scripts/record3d_inspect.py <export> --mesh preview                         # EXPERIMENTAL Poisson mesh

Writes inventory.json (everything) and inventory.md (readable summary) into --out (default:
runs/lidar_inspect/<export name>-<time>/, git-ignored). See docs/RECORD3D_LIDAR.md.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from _bootstrap import ROOT

from namma.lidar.inventory import inspect_export, make_working_copy


def markdown(rep):
    L = [f"# LiDAR export inventory: `{rep['source']['path']}`", "",
         f"Source: {rep['source']['kind']}, {rep['source']['files']} files, {rep['source']['bytes'] / 2**30:.2f} GB "
         f"(opened {rep['source']['opened']}). Created {rep['created_at']}.", "", "## Files by kind", "",
         "| Kind | Files | GB |", "|---|---|---|"]
    L += [f"| {k} | {v['files']} | {v['bytes'] / 2**30:.3f} |" for k, v in sorted(rep["by_kind"].items())]
    pc = rep["point_clouds"]
    L += ["", "## Point clouds", "", f"- PLY files: {pc['ply_files']} (unreadable {pc['unreadable']}), "
          f"vertices total {pc['total_vertices']:,}, per file min/median/max {pc['vertices_per_file_min_median_max']}",
          f"- With colour: {pc['with_color']}, with normals: {pc['with_normals']}, formats: {pc['formats']}",
          f"- Property sets: {pc['property_sets']}", f"- Sequences: {pc['sequences'][:3]}",
          f"- Header comments: {pc['header_comments'][:5]}"]
    im = rep["images"]
    L += ["", "## Images, video, depth, metadata", "",
          f"- Images: {im['files']} ({im['rgb_like']} RGB-like, {im['depth_like_16bit_or_float']} 16-bit/float), sizes {im['sizes']}",
          f"- Videos: {[{k: v.get(k) for k in ('file', 'width', 'height', 'fps', 'duration_s', 'error')} for v in rep['videos']]}",
          f"- Depth-like files: {rep['depth_candidates']}",
          f"- Metadata files: {[m['file'] for m in rep['metadata']]}"]
    if rep.get("measured"):
        L += ["", "## Measured (samples)", "", "```", json.dumps(rep["measured"], indent=1)[:4000], "```"]
    L += ["", "## Capabilities", "", "| Capability | Status | Evidence |", "|---|---|---|"]
    L += [f"| {k} | **{v['status']}** | {v['evidence']} |" for k, v in rep["capabilities"].items()]
    L += ["", "## Reconstruction routes", "", "| Route | Implemented | Possible with this export | Missing |", "|---|---|---|---|"]
    L += [f"| {k} | {v['implemented']} | {'yes' if v['possible_with_this_export'] else 'no'} | {', '.join(v['missing']) or '-'} |"
          for k, v in rep["paths"].items()]
    L += ["", f"**Recommendation:** {rep['recommendation']['explanation']}", "", "## Problems", ""]
    L += [f"- [{p['severity']}] {p['code']}: {p['message']}" for p in rep["problems"]] or ["- none"]
    L += ["", "## Storage", "", f"- {rep['storage']}"]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--out")
    ap.add_argument("--measure", action="store_true", help="read sampled points (bounded memory)")
    ap.add_argument("--copy", help="make a verified working copy here (new folder; the source is never changed)")
    ap.add_argument("--mesh", choices=["preview", "standard", "high"],
                    help="EXPERIMENTAL: fuse the PLY files + Poisson mesh (only after the frame check)")
    a = ap.parse_args()
    src = Path(a.source)
    out = Path(a.out) if a.out else ROOT / "runs" / "lidar_inspect" / f"{src.stem}-{time.strftime('%Y%m%d-%H%M%S')}"
    if src.is_dir() and (out.resolve() == src.resolve() or src.resolve() in out.resolve().parents):
        sys.exit("--out must not be inside the export (the source is read-only)")
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    rep = inspect_export(src, measure=a.measure)
    rep["inspect_seconds"] = round(time.perf_counter() - t0, 2)
    (out / "inventory.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    (out / "inventory.md").write_text(markdown(rep), encoding="utf-8")
    print(markdown(rep))
    print(f"wrote {out / 'inventory.json'} and inventory.md ({rep['inspect_seconds']} s)")
    if a.copy:
        doc = make_working_copy(src, a.copy)
        print(f"working copy: {a.copy} ({len(doc['files'])} files, SHA-256 manifest next to it)")
    if a.mesh:
        from namma.lidar.mesh import build_preview_mesh
        base = Path(a.copy) if a.copy else src
        if not base.is_dir():
            sys.exit("--mesh needs a folder (use --copy to extract an archive first)")
        plys = sorted(base.rglob("*.ply"))
        chk = rep["capabilities"].get("shared_coordinate_frame")
        if len(plys) > 1 and (chk is None or chk["status"] != "candidate"):
            sys.exit(f"--mesh refused: {len(plys)} PLY files but no evidence that they share one coordinate frame "
                     f"({chk['evidence'] if chk else 'run with --measure'}). Fusing sensor-relative frames would "
                     "produce garbage.")
        rec = build_preview_mesh(plys, out / f"mesh_{a.mesh}", profile=a.mesh)
        print(json.dumps(rec, indent=1))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
