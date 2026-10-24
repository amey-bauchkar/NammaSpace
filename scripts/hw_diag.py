"""Hardware / GPU diagnostic: what this machine has, what Namma Space will actually use, and why.

    python scripts/hw_diag.py                 # readable summary
    python scripts/hw_diag.py --json out.json # also write everything as JSON
    python scripts/hw_diag.py --gpu-smoke     # run COLMAP's CUDA SIFT on 2 sample images (proves the GPU path)

Nothing here claims the GPU is used unless a CUDA build is installed AND the driver reports a usable GPU; the
--gpu-smoke run is the proof that a CUDA step actually executes on this machine.
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from _bootstrap import ROOT

from namma.recon import hardware
from namma.recon.tools import ToolMissing, find_tools


def gpu_smoke(tools):
    """Feature extraction with use_gpu=1 on two sample images; reports the GPU memory seen during the run."""
    from namma.recon import colmap
    imgs = sorted((ROOT / "samples" / "tum_fr3_office" / "images").glob("*.jpg"))[:2]
    if len(imgs) < 2:
        return {"status": "skipped", "reason": "sample images not found"}
    with tempfile.TemporaryDirectory(prefix="namma_gpu_smoke_") as td:
        td = Path(td)
        (td / "images").mkdir()
        for p in imgs:
            shutil.copy2(p, td / "images" / p.name)
        before = hardware._detect_gpus()
        t0 = time.perf_counter()
        try:
            rec = colmap.extract_features(tools, td / "db.db", td / "images", td / "logs", camera_model="OPENCV",
                                          single_camera=True, max_image_px=1600, max_features=4000, threads=2,
                                          timeout_s=300, use_gpu=True, gpu_index=hardware.resources().gpu_index)
        except Exception as e:        # noqa: BLE001 - diagnostic: report every failure
            log = next(iter((td / "logs").glob("*.log")), None)
            return {"status": "failed", "error": str(e), "log_tail": log.read_text(errors="replace")[-1500:] if log else ""}
        log = (td / "logs" / "colmap_feature_extractor.log").read_text(errors="replace")
        n = colmap.keypoint_counts(td / "db.db")
        return {"status": "ok", "seconds": round(time.perf_counter() - t0, 2), "keypoints": n,
                "peak_rss_mb": rec["peak_rss_mb"],
                "log_mentions_gpu": any(w in log for w in ("GPU", "CUDA", "SiftGPU")),
                "vram_used_mb_before": before["gpus"][0]["vram_used_mb"] if before["available"] else None,
                "log_excerpt": [ln for ln in log.splitlines() if any(w in ln for w in ("GPU", "CUDA", "Sift"))][:8]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--gpu-smoke", action="store_true")
    ap.add_argument("--projects", default=str(ROOT / "projects"), help="folder where projects / runs are written")
    a = ap.parse_args()
    res, sources = hardware.load_resources()
    m = hardware.describe_machine(Path(a.projects) if Path(a.projects).exists() else ROOT, res=res)
    out = {"machine": m, "resource_sources": sources}
    try:
        t = find_tools()
        out["tools"] = t.describe()
    except ToolMissing as e:
        t = None
        out["tools"] = {"error": str(e)}
    use, why = hardware.gpu_decision(res, m["gpu"])
    out["gpu_decision"] = {"use_gpu": use, "reason": why}
    if a.gpu_smoke:
        out["gpu_smoke"] = gpu_smoke(t) if t is not None and t.colmap_cuda else {
            "status": "skipped", "reason": "no CUDA build of COLMAP found (python scripts/setup_tools.py --cuda)"}

    c, r, g, d = m["cpu"], m["ram"], m["gpu"], m["disk"]
    print(f"CPU   {c['physical_cores']} cores / {c['logical_cpus']} threads")
    print(f"RAM   {r['total_gb']} GB total, {r['available_gb']} GB free now; job budget {res.ram_budget_gb} GB "
          f"(reserve {res.ram_reserve_gb} GB for Windows / other programs)")
    if g["available"]:
        for x in g["gpus"]:
            print(f"GPU   [{x['index']}] {x['name']}  driver {x['driver']}  VRAM {x['vram_free_mb']:.0f}/{x['vram_total_mb']:.0f} MB free"
                  f"  compute {x['compute_capability']}")
    else:
        print(f"GPU   none usable: {g['reason']}")
    print(f"Disk  {d['free_gb']} GB free of {d['total_gb']} GB at {d['path']}"
          + ("  (OneDrive-synchronised folder: use a local --projects folder for runs)" if d["onedrive_synced"] else ""))
    print(f"Limits  threads: cpu {res.cpu_threads}, sift <= {res.sift_max_threads}, match <= {res.match_max_threads}, "
          f"io {res.io_workers}; gpu={res.gpu} (index {res.gpu_index}); max images {res.max_images}; "
          f"max video {res.max_video_gb} GB")
    changed = {k: v for k, v in sources.items() if v != "default"}
    print(f"        overrides: {changed or 'none (defaults)'}; config file: {m['config_file'] or 'none'}")
    if t is not None:
        print(f"Tools   COLMAP {t.colmap}  ({'CUDA build' if t.colmap_cuda else 'CPU build'})")
        print(f"        DensifyPointCloud {t.densify_exe(t.densify_gpu)}  ({'CUDA' if t.densify_gpu else 'CPU'})")
        steps = out["tools"]["gpu_steps"]
        print(f"        GPU will run: {', '.join(steps) if steps else 'nothing'}  ({why})")
        for n in t.notes:
            print(f"        note: {n}")
    else:
        print(f"Tools   {out['tools']['error']}")
    print("        always CPU: COLMAP mapping / bundle adjustment, OpenMVS meshing + texturing, Python stages")
    if "gpu_smoke" in out:
        print(f"GPU smoke test: {json.dumps(out['gpu_smoke'], indent=1)}")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"wrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
