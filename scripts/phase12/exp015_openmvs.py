"""EXP-015: CPU-only dense reconstruction + mesh (pre-registered: PROTOCOL_PHASE12_PREREGISTERED.md §2).

    python scripts/phase12/exp015_openmvs.py --images <dir> --sparse <sparse/0> --work <dir> [--budget-s 5400]

Steps:
- COLMAP image_undistorter (CPU), full resolution;
- OpenMVS InterfaceCOLMAP -> DensifyPointCloud --resolution-level 1 (CPU) -> ReconstructMesh
  -> TextureMesh (--resolution-level 1, --export-type glb);
- RefineMesh is skipped (time budget).
Deviation D-03 (recorded before running): --tower-mode 0 --estimate-roi 0 --crop-to-roi 0.
Note: OpenMVS's default --max-resolution 2560 also caps the processed image size.
"""
import argparse
import sys
import time
from pathlib import Path

from runlogged import run

ROOT = Path(__file__).resolve().parents[2]
COLMAP = ROOT / "tools" / "colmap-4.2.1-nocuda" / "bin" / "colmap.exe"
MVS = ROOT / "tools" / "openmvs-2.4.0" / "vc17" / "x64" / "Release"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True)
    ap.add_argument("--sparse", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--budget-s", type=int, default=5400)
    a = ap.parse_args()
    work = Path(a.work).resolve()
    if work.exists():
        sys.exit(f"{work} exists - runs are never overwritten")
    work.mkdir(parents=True)
    logs = work / "logs"
    t0 = time.perf_counter()

    def remaining():
        return max(1, int(a.budget_s - (time.perf_counter() - t0)))

    dense = work / "dense"
    steps = [
        ("1_image_undistorter", [COLMAP, "image_undistorter", "--image_path", Path(a.images).resolve(),
                                 "--input_path", Path(a.sparse).resolve(), "--output_path", dense,
                                 "--output_type", "COLMAP"], None),
        ("2_InterfaceCOLMAP", [MVS / "InterfaceCOLMAP.exe", "-i", dense, "-o", work / "scene.mvs",
                               "--image-folder", dense / "images", "-w", work], work),
        ("3_DensifyPointCloud", [MVS / "DensifyPointCloud.exe", "-i", work / "scene.mvs", "-o", work / "scene_dense.mvs",
                                 "--resolution-level", 1, "--tower-mode", 0, "--estimate-roi", 0, "--crop-to-roi", 0,
                                 "-w", work], work),
        ("4_ReconstructMesh", [MVS / "ReconstructMesh.exe", "-i", work / "scene_dense.mvs", "-p",
                               work / "scene_dense.ply", "-o", work / "scene_dense_mesh.ply", "-w", work], work),
        ("5_TextureMesh", [MVS / "TextureMesh.exe", "-i", work / "scene_dense.mvs", "-m", work / "scene_dense_mesh.ply",
                           "-o", work / "scene_textured.glb", "--export-type", "glb", "--resolution-level", 1,
                           "-w", work], work),
    ]
    for name, argv, cwd in steps:
        rec = run(name, argv, logs, remaining(), cwd=cwd)
        if rec["status"] != "ok":
            print("STOPPED at", name, rec["status"])
            return 1
    print(f"total wall {time.perf_counter() - t0:.1f}s")
    for p in sorted(work.glob("scene*")):
        print(f"{p.name}: {p.stat().st_size / 2**20:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
