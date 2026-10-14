"""Team iPhone room experiment orchestrator (EXP-002R on REAL data). Not yet executed: needs user data.

    python scripts/phase12/run_room_experiment.py capture_raw/<run_id> --frame-id site-<room> [--dry-run]

Order (each step stops the run on failure; nothing in capture_raw/ is written):
  1. preflight (IF-01, markers, single camera)           -> must be READY (or --accept-preflight)
  2. tape sheet -> reference IF-04 / inventory / markers  -> no failed distance checks
  3. EXP-014 COLMAP on R01_main only (4 threads)
  4. marker triangulation -> Sim(3) to the TAPE site frame (fit M0-M3; held-out M4/M5 = V-05/V-06 check)
  5. EXP-015 OpenMVS dense + mesh (CPU)
  6. automatic IF-04 from the dense points in the site frame (provenance automatic)
  7. comparison vs the TAPE reference (EXP-016 metrics) + routes for walk trials (walk_trials.csv)
Outputs go to phase12_artifacts/<run_id>/.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def plan(run_dir, frame_id):
    run_id = Path(run_dir).name
    out = ROOT / "phase12_artifacts" / run_id
    py = sys.executable
    return out, [
        ("preflight", [py, HERE / "preflight_capture.py", run_dir]),
        ("reference", [py, HERE / "measurements_to_reference.py", Path(run_dir) / "measurements", "--frame-id", frame_id,
                       "--out", out / "reference"]),
        ("exp014", [py, HERE / "exp014_colmap.py", "--images", Path(run_dir) / "R01_main", "--work", out / "exp014",
                    "--threads", "4"]),
        ("markers", "register_markers"),
        ("exp015", [py, HERE / "exp015_openmvs.py", "--images", Path(run_dir) / "R01_main", "--sparse",
                    out / "exp014" / "sparse" / "0", "--work", out / "exp015"]),
        ("if04_and_compare", "compare_with_tape"),
    ]


def register_markers(run_dir, out):
    from namma.recon.io import read_colmap_cameras_txt, read_colmap_images_txt
    from namma.recon.markers import markers_in_reconstruction, register_to_site
    model = out / "exp014" / "sparse_txt" / "0"
    images = read_colmap_images_txt(model / "images.txt")
    cams = read_colmap_cameras_txt(model / "cameras.txt")
    found = markers_in_reconstruction(images, cams, Path(run_dir) / "R01_main")
    ms = json.loads((out / "reference" / "markers_site.json").read_text(encoding="utf-8"))
    rec = {f"M{k}": v["xyz"] for k, v in found.items() if v.get("xyz") is not None}
    fit = [m for m in ms["fit_ids"] if m in rec]
    if len(fit) < 3:
        raise SystemExit(f"only {len(fit)} fit markers triangulated ({sorted(rec)}); need >= 3 - recapture markers")
    reg = register_to_site(rec, ms["markers_site"], fit, [m for m in ms["check_ids"] if m in rec])
    result = {"fit_ids": fit, "scale": reg["scale"], "fit_residual_m": reg["fit_residual_m"],
              "held_out_error_m": reg["held_out_error_m"],
              "v05_v06_check": "PASS" if reg["held_out_error_m"] and max(reg["held_out_error_m"].values()) <= 0.05
              else ("NOT MEASURABLE (no held-out marker)" if not reg["held_out_error_m"] else "FAIL"),
              "markers": found}
    (out / "marker_registration.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--frame-id", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--accept-preflight", action="store_true")
    a = ap.parse_args()
    a.run_dir = str(Path(a.run_dir).resolve())  # subprocesses run with cwd=scripts/phase12
    out, steps = plan(a.run_dir, a.frame_id)
    if a.dry_run:
        for name, cmd in steps:
            print(name, ":", cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd))
        return 0
    out.mkdir(parents=True, exist_ok=True)
    for name, cmd in steps:
        print(f"== {name}")
        if cmd == "register_markers":
            print(json.dumps({k: v for k, v in register_markers(a.run_dir, out).items() if k != "markers"}, indent=1))
            continue
        if cmd == "compare_with_tape":
            print("Step 6/7: run exp016-style comparison against out/reference/reference_geometry.if04.json "
                  "(see docs/phase12_capture_pack/README.md). Not automated until real data validates step 4.")
            continue
        p = subprocess.run([str(c) for c in cmd], cwd=HERE)
        if p.returncode != 0:
            print(f"STOPPED at {name} (exit {p.returncode})")
            return p.returncode
        if name == "preflight":
            pre = json.loads((Path(a.run_dir).parent / f"{Path(a.run_dir).name}.preflight.json").read_text(encoding="utf-8"))
            if pre["verdict"] != "READY" and not a.accept_preflight:
                print("STOPPED: preflight NOT READY:", pre["problems"])
                return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
