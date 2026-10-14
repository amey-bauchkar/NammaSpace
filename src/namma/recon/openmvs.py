"""OpenMVS (CPU) dense stage: import COLMAP -> densify -> mesh -> textured GLB levels of detail.

OpenMVS is AGPL-3.0 (THIRD_PARTY_NOTICES.md). It is called as unmodified external executables;
nothing from it is linked into this package.
Progress: OpenMVS prints "... (n/N)"-style counters for depth maps; they are parsed when present.
"""
import re
from pathlib import Path

from namma.recon.runner import run_step

DEPTH_PROGRESS = re.compile(r"(?:Estimated|Filtered|Fused) depth-map\s+(\d+)\s*(?:/|of)\s*(\d+)", re.I)
GENERIC = re.compile(r"\((\d+)/(\d+)\)")


def _cb(progress, label):
    def cb(line):
        m = DEPTH_PROGRESS.search(line) or GENERIC.search(line)
        if m and progress is not None and int(m.group(2)):
            progress(label, int(m.group(1)) / int(m.group(2)))
    return cb


def interface_colmap(tools, undistorted_dir, scene_mvs, work, log_dir, *, timeout_s, cancel_file=None):
    return run_step("openmvs_interface_colmap", [tools.mvs("InterfaceCOLMAP"), "-i", undistorted_dir, "-o", scene_mvs,
                                                 "--image-folder", Path(undistorted_dir) / "images", "-w", work],
                    log_dir, timeout_s=timeout_s, cwd=work, cancel_file=cancel_file)


def _densify_progress(progress, n_images):
    seen = {"est": set(), "fil": set()}

    def cb(line):
        if progress is None or not n_images:
            return
        m = re.search(r"Depth-map for image\s+(\d+)\s+(estimated|with image|filtered|fused)", line)
        if m:
            key = "fil" if m.group(2) in ("filtered", "fused") else "est"
            seen[key].add(int(m.group(1)))
            frac = 0.75 * len(seen["est"]) / n_images + 0.15 * len(seen["fil"]) / n_images
            progress("Estimating depth maps" if key == "est" else "Filtering depth maps", min(0.95, frac))
        elif "Depth-maps fused" in line or "fused and filtered" in line:
            progress("Fusing depth maps into a point cloud", 0.95)
    return cb


def densify(tools, scene_mvs, dense_mvs, work, log_dir, *, resolution_level, max_resolution, number_views, threads,
            timeout_s, cancel_file=None, progress=None, n_images=0):
    argv = [tools.mvs("DensifyPointCloud"), "-i", scene_mvs, "-o", dense_mvs, "-w", work,
            "--resolution-level", resolution_level, "--max-resolution", max_resolution,
            "--number-views", number_views, "--max-threads", threads,
            # D-03 (Phase 12): ROI estimation / tower mode cropped indoor scenes; keep them off
            "--tower-mode", 0, "--estimate-roi", 0, "--crop-to-roi", 0,
            "--remove-dmaps", 1]
    return run_step("openmvs_densify", argv, log_dir, timeout_s=timeout_s, cwd=work, cancel_file=cancel_file,
                    on_line=_densify_progress(progress, n_images), follow=(work, "DensifyPointCloud-*.log"))


def reconstruct_mesh(tools, dense_mvs, dense_ply, mesh_ply, work, log_dir, *, target_faces, threads, timeout_s,
                     cancel_file=None):
    argv = [tools.mvs("ReconstructMesh"), "-i", dense_mvs, "-p", dense_ply, "-o", mesh_ply, "-w", work,
            "--max-threads", threads]
    if target_faces:
        argv += ["--target-face-num", int(target_faces)]
    return run_step("openmvs_reconstruct_mesh", argv, log_dir, timeout_s=timeout_s, cwd=work, cancel_file=cancel_file)


def texture_mesh(tools, dense_mvs, mesh_ply, out_glb, work, log_dir, *, resolution_level, decimate, max_texture_size,
                 threads, timeout_s, cancel_file=None, step="openmvs_texture", seam_leveling=False,
                 empty_color=0x808080):
    """seam_leveling=False (default): Phase 13 found that the OpenMVS 2.4.0 Windows build's global / local seam
    leveling writes black texels (34 % / 17 % of the atlas on ETH3D pipes; finding C-02), so both are disabled."""
    argv = [tools.mvs("TextureMesh"), "-i", dense_mvs, "-m", mesh_ply, "-o", out_glb, "-w", work,
            "--export-type", "glb", "--resolution-level", resolution_level, "--decimate", decimate,
            "--max-texture-size", max_texture_size, "--max-threads", threads,
            "--global-seam-leveling", int(seam_leveling), "--local-seam-leveling", int(seam_leveling),
            # faces no photo covers: neutral grey instead of the default orange, so missing data is not mistaken for paint
            "--empty-color", int(empty_color)]
    return run_step(step, argv, log_dir, timeout_s=timeout_s, cwd=work, cancel_file=cancel_file)
