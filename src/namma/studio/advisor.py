"""Recommend a processing profile from the capture and the machine (advice only: the user still chooses).

Inputs are measured, not assumed: image count and pixel sizes from the capture manifest, RAM / GPU / disk from
namma.recon.hardware. Estimates use the project's own measurements:
- OpenMVS texturing keeps every MVS view in RAM: 7.0 GB for 30 x 24 MP (720 MP) on kicker  -> ~9.7 MB per MP;
- depth maps on disk: ~65 MB per 1600-px image, scaling with pixel count (pipeline.dense_disk_need_gb);
- Standard on a 44-photo hall needed 11.2 GB while meshing (D13-14). Mesh-step memory is not modelled per image,
  so captures above 40 photos get a warning instead of a number.
Rules, in order: a capture never processed before gets Preview first (fast validation, the tested low-memory
path); High needs a usable GPU for depth maps, >= 11 GB RAM budget and the disk it needs; otherwise Standard if its
estimates fit; otherwise Preview.
"""
from namma.recon import hardware
from namma.studio.pipeline import dense_disk_need_gb, ram_pixel_budget_mp
from namma.studio.profiles import FROZEN_PROFILES, get_profile

GB_PER_MP_TEXTURE = 0.0097
JPEG_GB_PER_MP = 0.4 / 1024
HIGH_MIN_RAM_BUDGET_GB = 11.0


def _mvs_megapixels(cfg, sizes, ram_budget_gb):
    """Megapixels handed to OpenMVS for this profile (same rules as pipeline.stage_undistort, without the I/O)."""
    k = cfg.get("dense_max_images") or len(sizes)
    sub = sorted(sizes, key=lambda s: s[0] * s[1], reverse=True)[:k] if len(sizes) > k else list(sizes)
    cap = cfg.get("work_max_px") or 0
    mp = []
    for w, h in sub:
        f = min(1.0, cap / max(w, h)) if cap else 1.0
        mp.append(w * h * f * f / 1e6)
    total = sum(mp)
    budget = cfg.get("mvs_pixel_budget_mp")
    if budget:
        budget = min(budget, ram_pixel_budget_mp(ram_budget_gb))
        total = min(total, budget)
    return total, len(sub)


def estimate(profile, sizes, ram_budget_gb):
    cfg = get_profile(profile)
    mvs_mp, n_dense = _mvs_megapixels(cfg, sizes, ram_budget_gb)
    longest = max((max(s) for s in sizes), default=0)
    dense_side = min(cfg["dense"]["max_resolution"], longest or cfg["dense"]["max_resolution"])
    return {"profile": profile, "dense_images": n_dense, "mvs_megapixels": round(mvs_mp, 1),
            "texture_ram_gb": round(1.0 + GB_PER_MP_TEXTURE * mvs_mp, 1),
            # depth maps + mesh/texture/package, plus the working and undistorted image copies (~0.4 MB per MP JPEG)
            "disk_gb": round(dense_disk_need_gb(n_dense, dense_side) + 2 * JPEG_GB_PER_MP * sum(w * h for w, h in sizes) / 1e6, 1),
            "frozen_config": profile in FROZEN_PROFILES}


def recommend(sizes, *, machine=None, res=None, tools=None, processed_before=False):
    """sizes: [(w, h), ...] of the accepted inputs. tools: namma.recon.tools.Tools or None (tools missing).
    -> {"profile", "reasons", "warnings", "estimates": {profile: {...}}, "machine": {...}}."""
    res = res or hardware.resources()
    machine = machine or hardware.describe_machine(res=res)
    n = len(sizes)
    disk_free = machine["disk"]["free_gb"]
    est = {p: estimate(p, sizes, res.ram_budget_gb) for p in ("preview", "standard", "high")}
    gpu_dense = bool(tools is not None and getattr(tools, "densify_gpu", False))
    gpu_sfm = bool(tools is not None and getattr(tools, "colmap_gpu", False))
    reasons, warnings = [], []
    if machine["disk"].get("onedrive_synced"):
        warnings.append("The projects folder is inside OneDrive: GBs of intermediate files will be synchronised and "
                        "can be locked mid-step. Use a local folder (start_studio.py --projects C:\\NammaWork).")
    fits = {p: e["texture_ram_gb"] <= res.ram_budget_gb and e["disk_gb"] <= disk_free for p, e in est.items()}
    if not processed_before:
        choice = "preview"
        reasons.append("First run of this capture: Preview validates it quickly with little memory; then run "
                       "Standard or High if the result looks right.")
    elif gpu_dense and res.ram_budget_gb >= HIGH_MIN_RAM_BUDGET_GB and fits["high"] and n <= 250:
        choice = "high"
        reasons.append(f"GPU depth maps available and the estimates fit ({est['high']['texture_ram_gb']} GB RAM for "
                       f"texturing, {est['high']['disk_gb']} GB disk). High is not part of the evaluated configuration.")
    elif fits["standard"]:
        choice = "standard"
        reasons.append("Standard (the evaluated configuration) fits this machine.")
        if not gpu_dense:
            reasons.append("High is not recommended: dense depth maps would run on the CPU "
                           f"({getattr(tools, 'gpu_reason', 'reconstruction tools not found') if tools else 'reconstruction tools not found'}).")
    else:
        choice = "preview"
        reasons.append("Standard's estimates exceed the RAM budget or the free disk space.")
    if n > 40 and choice in ("standard", "high"):
        warnings.append("More than 40 photos: meshing memory is not modelled (a 44-photo hall needed 11.2 GB at "
                        "Standard). Close other programs before starting.")
    if not fits[choice]:
        warnings.append(f"{choice}: estimated {est[choice]['disk_gb']} GB disk / {est[choice]['texture_ram_gb']} GB RAM; "
                        f"{disk_free} GB disk free, RAM budget {res.ram_budget_gb} GB.")
    if machine["ram"]["available_gb"] < 4:
        warnings.append(f"Only {machine['ram']['available_gb']} GB RAM is free right now: other programs are using "
                        "memory, so steps will run with fewer threads (slower). Close browsers / apps for speed.")
    return {"profile": choice, "reasons": reasons, "warnings": warnings, "estimates": est,
            "gpu": {"sfm": gpu_sfm, "dense": gpu_dense, "reason": getattr(tools, "gpu_reason", None) if tools else None},
            "machine": {"ram_budget_gb": res.ram_budget_gb, "ram_available_gb": machine["ram"]["available_gb"],
                        "disk_free_gb": disk_free}}
