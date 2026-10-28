"""Build docs/round1/TECHNICAL_ABSTRACT.md (+ .html, .pdf) from the template and the recorded results.

    python scripts/docs/build_abstract.py

The results table is generated from phase13_artifacts/results/metrics_matrix.json (written by
scripts/eval/results_matrix.py); the limitations text is in this script. Fails if the PDF exceeds 5 pages.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TPL = ROOT / "docs" / "round1" / "TECHNICAL_ABSTRACT.template.md"
OUT = ROOT / "docs" / "round1" / "TECHNICAL_ABSTRACT.md"


def f(v, d=3):
    return "–" if v is None else (f"{v:.{d}f}" if isinstance(v, float) else str(v))


def mm(v):
    return "–" if v is None else f"{v * 1000:.1f} mm"


def results():
    m = json.loads((ROOT / "phase13_artifacts" / "results" / "metrics_matrix.json").read_text(encoding="utf-8"))
    rows = sorted(m["scenes"], key=lambda r: (r["role"] != "heldout_test", r["scene_id"], r["profile"]))
    sys.path.insert(0, str(ROOT / "src"))
    from namma.studio.profiles import PROFILES, config_sha256, get_profile
    final = {config_sha256(get_profile(n)) for n in PROFILES}
    L = ["Ground truth: ETH3D laser scans and DSLR poses; ARKitScenes ARKit trajectory, LiDAR mesh and annotated "
         "furniture boxes; TUM motion capture. Development scenes were used for tuning; **held-out scenes were "
         "processed once with the frozen configuration** (protocol `phase13_artifacts/PROTOCOL_PHASE13.md`). "
         "Criteria are the Phase 12 pre-registered ones, unchanged.", "",
         "| Scene (role, profile, config) | Photos placed | Camera error median | Dense accuracy median | Completeness @5 cm (dense / mesh) | Floor IoU | Routes OK (false-unreachable) | Minutes | Peak RAM (GB) |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        routes = r["routes"]
        if " ok," in str(routes):
            ok = routes.split(" ok,")[0]
            fu = routes.split(",")[-1].strip().split(" ")[0]
            routes = f"{ok} ({fu})"
        else:
            routes = "not measurable"
        cfg = "frozen" if r.get("frozen_config") else ("final" if r["config_sha256"] in final else "earlier")
        L.append(f"| {r['scene_id']} ({'held-out' if r['role'] == 'heldout_test' else 'dev'}, {r['profile']}, {cfg}) | {r['registered']} | "
                 f"{mm(r['pose_median_m'])} | {mm(r['dense_accuracy_median_m'])} | "
                 f"{f(r['dense_completeness_5cm'], 2)} / {f(r['mesh_completeness_5cm'], 2)} | {f(r['floor_iou'], 2)} | {routes} | "
                 f"{f(r['minutes'], 1)} | {f(r['peak_ram_gb'], 1)} |")
    failed = [u for u in m.get("unevaluated_runs", []) if u["status"] == "failed"]
    if failed:
        L.append("")
        L.append(f"**Failed runs (kept on record, not hidden):** {len(failed)} runs stopped – "
                 + "; ".join(f"{u['project'].rsplit('-', 2)[0]} ({u['profile']}, in the {u['failed_stage']} stage)"
                             for u in failed)
                 + ". Causes: a missing intermediate file, an implausible scene size, texturing over the time limit and two "
                 "COLMAP crashes (exit 0xC0000409); full messages in `phase13_artifacts/results/metrics_matrix.json`.")
    L.append("")
    L.append("Config: *frozen* = configuration frozen before the held-out runs; *final* = the submitted code (frozen + "
             "post-freeze face cap D13-14); *earlier* = development configuration.")
    L.append("")
    L.append("Criteria (Phase 12, unchanged): all photos placed; camera error ≤ 50 mm; dense accuracy ≤ 50 mm; dense "
             "completeness @5 cm ≥ 0.50; ≥ 1 route with 0 collisions and 0 false-unreachable pairs; preview ≤ 30 min, "
             "standard ≤ 90 min; peak RAM ≤ 12 GB. Per-criterion verdicts: `docs/RESULTS_SCENES.md`; gates A–F: "
             "`docs/RESULTS_MATRIX.md`.")
    b = [x for x in m["browser"] if x.get("bench_valid") and not x.get("superseded")]
    vis = [x for x in b if x.get("window") == "visible"]
    head = [x for x in b if x.get("window") != "visible"]
    parts = [f"**visible Chrome window** ({Path(x['file']).parent.name}): median {f(x['frame_median_ms'], 1)} ms / "
             f"P95 {f(x['frame_p95_ms'], 1)} ms at {x['bench_triangles'] / 1e6:.2f} M textured triangles" for x in vis]
    if head:
        med = sorted(x["frame_median_ms"] for x in head)
        parts.append(f"headless Chrome, {len(head)} records on {len({Path(x['file']).parent.name for x in head})} packages: "
                     f"median {med[0]:.1f}–{med[-1]:.1f} ms (not a visible-window benchmark)")
    if parts:
        L.append("")
        L.append("**Viewer frame time on the laptop's Intel iGPU (real key / mouse events):** " + "; ".join(parts) + ".")
    return "\n".join(L)


LIMITS = """- **Own home via Polycam.** The own-home model uses Polycam's LiDAR mesh (third-party app; our code repairs it and adds
  the photoreal view, walking, routes and measuring). Splat quality is limited by capture coverage: a wall the phone
  only saw from far away smears up close; the SOP now asks for wall passes at 1–1.5 m.
- **Not yet measured:** viewer frame rate on phones and integrated-graphics laptops with the photoreal view, and
  measuring-tool accuracy against a tape measure.
- **Photoreal training needs an NVIDIA GPU** (~20–25 min on an RTX 4050 Laptop for 218 photos); without one, the mesh
  view is used.
- **Completeness depends on the room.** Textured, cluttered spaces reconstruct well (held-out delivery_area 0.61);
  plain white walls, desks and dark floors stay partly missing (held-out office 0.30–0.33). Missing floor is shown
  by a labelled guide grid and reported, never filled with invented geometry.
- **Fast handheld video fails.** On both held-out iPad videos CPU structure-from-motion placed only 12–46 % of the
  frames; the result is labelled "poor". The SOP asks for photos or a slow video.
- **Scale.** Photos do not fix real-world size: the model is labelled "scale unknown" until the user enters one
  known length. The fallback assumption (1.4 m phone height) was off by −5 % to +90 %, so it is never shown as metric.
- **Navigation is not validated.** No scene meets the route criteria (best: 4 routes, 0 collisions, 8 pairs missed);
  walking paths are shown only as *unverified* (Round 2: floor completion with human verification, ARCH-030).
- **Compute (photo route).** CPU only: Preview 4–40 s per image, Standard 80–100 s per 24 MP photo. A 44-photo hall at Standard
  needed 11.2 GB and exceeded the time limit (failed, kept); Standard is now capped at 4 M faces and the Studio
  advises Preview first above 40 photos.
- **Licences.** OpenMVS is AGPL-3.0 (used unmodified as an external program, never bundled); ETH3D and ARKitScenes
  are non-commercial: their derived models are not published."""


def main():
    t = TPL.read_text(encoding="utf-8").replace("<!-- RESULTS -->", results()).replace("<!-- LIMITATIONS -->", LIMITS)
    OUT.write_text(t, encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "docs" / "render_pdf.py"), str(OUT), "--max-pages", "5"])
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
