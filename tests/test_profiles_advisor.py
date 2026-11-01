"""Profiles (frozen ones unchanged, High added) and the machine-aware profile advisor (synthetic machine facts)."""
import json

from PIL import Image

from namma.recon import hardware
from namma.studio import advisor
from namma.studio.profiles import FROZEN_PROFILES, PROFILES, config_sha256, get_profile

from test_p13_studio import W, req, server  # noqa: F401  (fixture)

# config_sha256 of the profiles as they were before the hardware work (HEAD eb75ae6): must not move
HEAD_HASHES = {"preview": "eb9d2271afd75630d91e736bf4676433f010c2875f59ff0a6a079297b7dc11bc",
               "standard": "7340016f758ad35325035cbf464eacb5ad5e7534d5d07da3394d66dd3eddfb9b"}


def machine(ram_avail=9.0, disk=99.0, onedrive=False):
    return {"ram": {"total_gb": 15.6, "available_gb": ram_avail, "used_percent": 40},
            "disk": {"free_gb": disk, "onedrive_synced": onedrive}, "gpu": {"available": True, "gpus": []}}


def res(**kw):
    base = dict(ram_reserve_gb=3.1, ram_budget_gb=12.5, cpu_threads=14, sift_max_threads=10, match_max_threads=10,
                io_workers=5, gpu="auto", gpu_index=0, vram_reserve_mb=768, min_free_vram_mb=1536, max_images=300,
                max_video_gb=4.0)
    return hardware.Resources(**{**base, **kw})


class T:
    def __init__(self, gpu):
        self.densify_gpu = self.colmap_gpu = gpu
        self.gpu_reason = "RTX 4050" if gpu else "no usable NVIDIA GPU"


def test_high_profile_exists_and_frozen_profiles_are_untouched():
    assert set(FROZEN_PROFILES) == {"preview", "standard"} and "high" in PROFILES
    hi, std = get_profile("high"), get_profile("standard")
    assert hi["sift"] == std["sift"] and hi["matching"] == std["matching"]      # same features and matching
    assert hi["mapper"] == {**std["mapper"], "engine": "global"}                # global mapper, incremental fallback
    assert hi["dense"]["max_resolution"] > std["dense"]["max_resolution"]
    assert hi["mvs_pixel_budget_mp"] > std["mvs_pixel_budget_mp"] and hi["dense_max_images"] > std["dense_max_images"]
    for name in ("preview", "standard"):
        cfg = get_profile(name)
        assert config_sha256(cfg) == HEAD_HASHES[name]                    # runs stay comparable with earlier evidence
        assert not any(k in json.dumps(cfg) for k in ("gpu", "threads", "ram_budget"))   # hardware stays out of the hash


def test_advisor_first_run_is_preview_then_high_with_gpu():
    sizes = [(6000, 4000)] * 30
    r = advisor.recommend(sizes, machine=machine(), res=res(), tools=T(True), processed_before=False)
    assert r["profile"] == "preview" and "First run" in r["reasons"][0]
    r = advisor.recommend(sizes, machine=machine(), res=res(), tools=T(True), processed_before=True)
    assert r["profile"] == "high" and r["estimates"]["high"]["texture_ram_gb"] <= 12.5
    assert r["estimates"]["standard"]["mvs_megapixels"] == 720.0         # 30 x 24 MP fits the 750 MP budget
    assert r["estimates"]["preview"]["frozen_config"] and not r["estimates"]["high"]["frozen_config"]


def test_advisor_without_gpu_or_with_small_budget_or_disk():
    sizes = [(6000, 4000)] * 30
    r = advisor.recommend(sizes, machine=machine(), res=res(), tools=T(False), processed_before=True)
    assert r["profile"] == "standard" and any("CPU" in x for x in r["reasons"])
    r = advisor.recommend(sizes, machine=machine(), res=res(ram_budget_gb=8.0), tools=T(True), processed_before=True)
    assert r["profile"] == "standard"                                    # High needs >= 11 GB budget
    r = advisor.recommend(sizes, machine=machine(disk=3.0), res=res(), tools=T(True), processed_before=True)
    assert r["profile"] == "preview" and r["warnings"]


def test_advisor_warns_about_onedrive_low_free_ram_and_large_sets():
    sizes = [(4032, 3024)] * 80
    r = advisor.recommend(sizes, machine=machine(ram_avail=0.9, onedrive=True), res=res(), tools=T(True),
                          processed_before=True)
    text = " ".join(r["warnings"])
    assert "OneDrive" in text and "RAM is free" in text and "More than 40 photos" in text


def test_ram_budget_lowers_mvs_pixels_in_estimates():
    sizes = [(6000, 4000)] * 60                                          # 1440 MP of photos
    small = advisor.estimate("standard", sizes, ram_budget_gb=5.0)
    big = advisor.estimate("standard", sizes, ram_budget_gb=12.5)
    assert big["mvs_megapixels"] == 750 and small["mvs_megapixels"] < 750


def test_recommendation_endpoint(server, tmp_path):  # noqa: F811
    port, studio = server
    st, body = req(port, "POST", "/api/projects", {"title": "Room"}, W)
    pid = body["project_id"]
    st, body = req(port, "GET", f"/api/projects/{pid}/recommendation")
    assert st == 409 and body["error"] == "CAPTURE-NOT-CHECKED"
    d = studio.store.path(pid)
    (d / "capture").mkdir(exist_ok=True)
    for i in range(12):
        Image.new("RGB", (1600, 1200)).save(d / "capture" / f"f{i}.jpg")
    (d / "capture" / "capture_manifest.json").write_text(json.dumps({"inputs": [f"capture/f{i}.jpg" for i in range(12)]}))
    st, body = req(port, "GET", f"/api/projects/{pid}/recommendation")
    assert st == 200 and body["profile"] == "preview" and set(body["estimates"]) == {"preview", "standard", "high"}
    st, h = req(port, "GET", "/api/health")
    assert "hardware" in h and "ram_budget_gb" in h["hardware"] and "high" in h["profiles"]


def test_clamp_dense_resolution_adapts_to_available_disk():
    from namma.studio.pipeline import clamp_dense_resolution

    # Plenty of space: retains original resolution
    res, info = clamp_dense_resolution(free_gb=100.0, n_images=180, max_resolution=3200)
    assert res == 3200 and info is None

    # Constrained disk (e.g. 45.2 GB with 180 images needing 48.3 GB): clamps to 2560
    res, info = clamp_dense_resolution(free_gb=45.2, n_images=180, max_resolution=3200)
    assert res == 2560
    assert info["original_px"] == 3200 and info["clamped_px"] == 2560
    assert info["clamped_need_gb"] < 45.2

    # Lower disk (e.g. 25.0 GB): clamps to 1600
    res, info = clamp_dense_resolution(free_gb=25.0, n_images=180, max_resolution=3200)
    assert res == 1600
    assert info["clamped_need_gb"] <= 15.0
