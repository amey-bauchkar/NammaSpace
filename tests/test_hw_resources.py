"""Hardware detection, configurable resource limits, GPU decisions and GPU->CPU fallback (no tools or GPU needed)."""
import collections
import json

import pytest

from namma.recon import hardware, tools
from namma.recon.runner import StepFailed

GB = 2**30
VM = collections.namedtuple("VM", "total available percent")


@pytest.fixture
def machine16(monkeypatch):
    """A 16 GB, 10-core / 16-thread machine (the HP Victus) regardless of where the tests run."""
    state = {"available": 9 * GB}
    monkeypatch.setattr(hardware.psutil, "virtual_memory", lambda: VM(16 * GB, state["available"], 40.0))
    monkeypatch.setattr(hardware.psutil, "cpu_count", lambda logical=True: 16 if logical else 10)
    monkeypatch.setattr(tools.psutil, "virtual_memory", lambda: VM(16 * GB, state["available"], 40.0))
    monkeypatch.setattr(tools.psutil, "cpu_count", lambda logical=True: 16 if logical else 10)
    for k in list(__import__("os").environ):
        if k.startswith("NAMMA_") and k.upper() not in ("NAMMA_COLMAP", "NAMMA_OPENMVS_DIR"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("NAMMA_RESOURCES", "__no_such_file__.json")
    hardware.resources.cache_clear()
    yield state
    hardware.resources.cache_clear()


def test_defaults_for_16gb_machine_keep_headroom_and_lift_old_caps(machine16):
    res, src = hardware.load_resources()
    assert res.ram_reserve_gb == pytest.approx(3.2)              # 20 % of 16 GB, >= 3 GB
    assert res.ram_budget_gb == pytest.approx(12.8)              # never the whole 16 GB
    assert res.cpu_threads == 14                                 # 2 logical CPUs left for the UI
    assert res.sift_max_threads == 10 and res.match_max_threads == 10   # were fixed at 4 / 6
    assert res.gpu == "auto" and res.max_images == 300 and res.max_video_gb == 4.0
    assert set(src.values()) == {"default"}


def test_file_and_environment_overrides(machine16, tmp_path, monkeypatch):
    f = tmp_path / "resources.json"
    f.write_text(json.dumps({"_comment": "x", "ram_reserve_gb": 5, "gpu": "OFF", "sift_max_threads": 3}), encoding="utf-8")
    monkeypatch.setenv("NAMMA_SIFT_MAX_THREADS", "6")
    res, src = hardware.load_resources(f)
    assert res.ram_reserve_gb == 5 and res.ram_budget_gb == pytest.approx(11.0)   # budget follows the reserve
    assert res.gpu == "off" and src["gpu"].startswith("file:")
    assert res.sift_max_threads == 6 and src["sift_max_threads"] == "env"         # env beats file


@pytest.mark.parametrize("doc,msg", [({"ram_budget_gb": 40}, "exceeds the installed"), ({"bogus": 1}, "unknown resource"),
                                     ({"gpu": "maybe"}, "auto, on or off")])
def test_invalid_settings_are_rejected_with_the_key(machine16, tmp_path, doc, msg):
    f = tmp_path / "r.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match=msg):
        hardware.load_resources(f)


def test_thread_counts_follow_free_ram_and_configured_ceilings(machine16, monkeypatch):
    machine16["available"] = 15 * GB                 # fit = min(free - reserve, budget 12.8 GB) / per-thread GB
    assert tools.sift_threads(3200) == 10            # RAM fits 12; ceiling = physical cores (old code: 4)
    assert tools.match_threads(8192) == 10           # RAM fits 10 (1.2 GB each); old code: 6
    machine16["available"] = 4 * GB                  # busy laptop: fewer threads instead of a crash
    assert tools.sift_threads(3200) == 2
    assert tools.sift_threads(3200, gpu=True) == 5   # GPU SIFT: threads only decode images
    assert tools.match_threads(8192) == 1 and tools.match_threads(8192, gpu=True) == 10   # GPU: verification only
    machine16["available"] = int(0.5 * GB)
    assert tools.sift_threads(3200) == 1 and tools.match_threads(8192) == 1
    monkeypatch.setenv("NAMMA_SIFT_MAX_THREADS", "3")
    hardware.resources.cache_clear()
    machine16["available"] = 14 * GB
    assert tools.sift_threads(3200) == 3
    assert tools.mvs_threads() == 14


def test_ram_budget_caps_threads_even_when_more_is_free(machine16, monkeypatch):
    monkeypatch.setenv("NAMMA_RAM_BUDGET_GB", "4")
    hardware.resources.cache_clear()
    machine16["available"] = 14 * GB
    assert tools.sift_threads(3200) == 3             # 4 GB budget // 1.05 GB per thread (14 GB are free)


def test_parse_nvidia_smi_and_gpu_decision(machine16):
    rows = hardware.parse_nvidia_smi_csv("0, NVIDIA GeForce RTX 4050 Laptop GPU, 592.82, 6141, 83, 5900, 8.9\n")
    assert rows == [{"index": 0, "name": "NVIDIA GeForce RTX 4050 Laptop GPU", "driver": "592.82", "vram_total_mb": 6141.0,
                     "vram_used_mb": 83.0, "vram_free_mb": 5900.0, "compute_capability": "8.9"}]
    info = {"available": True, "gpus": rows, "reason": None}
    res = hardware.resources()
    assert hardware.gpu_decision(res, info)[0] is True
    busy = {"available": True, "gpus": [{**rows[0], "vram_free_mb": 1500.0}], "reason": None}
    use, why = hardware.gpu_decision(res, busy)
    assert not use and "VRAM" in why
    assert hardware.gpu_decision(res, {"available": False, "gpus": [], "reason": "nvidia-smi not found"}) == \
        (False, "no usable NVIDIA GPU: nvidia-smi not found")
    off = hardware.Resources(**{**res.as_dict(), "gpu": "off"})
    assert hardware.gpu_decision(off, info) == (False, "GPU disabled by configuration (gpu=off)")
    forced = hardware.Resources(**{**res.as_dict(), "gpu": "on"})
    assert hardware.gpu_decision(forced, busy)[0] is True


def test_detect_gpus_never_raises_without_driver(monkeypatch):
    monkeypatch.setattr(hardware, "_nvidia_smi", lambda: None)
    hardware._GPU_CACHE["value"] = None
    r = hardware.detect_gpus()
    assert r["available"] is False and "nvidia-smi" in r["reason"]
    hardware._GPU_CACHE["value"] = None


def test_find_tools_prefers_cuda_build_only_when_it_reports_cuda(machine16, tmp_path, monkeypatch):
    cpu = tmp_path / "cpu" / "colmap.exe"
    cuda = tmp_path / "cuda" / "bin" / "colmap.exe"
    mvs = tmp_path / "mvs"
    mvs_cuda = tmp_path / "mvs_cuda" / "x"
    for p in (cpu, cuda):
        p.parent.mkdir(parents=True)
        p.write_text("x")
    mvs.mkdir()
    for t in tools.OPENMVS_TOOLS:
        (mvs / f"{t}{tools.EXE}").write_text("x")
    mvs_cuda.mkdir(parents=True)
    (mvs_cuda / f"DensifyPointCloud{tools.EXE}").write_text("x")
    monkeypatch.setattr(tools, "DEFAULT_COLMAP", cpu)
    monkeypatch.setattr(tools, "CUDA_COLMAP_ROOT", tmp_path / "cuda")
    monkeypatch.setattr(tools, "CUDA_OPENMVS_ROOT", tmp_path / "mvs_cuda")
    monkeypatch.delenv("NAMMA_COLMAP", raising=False)
    monkeypatch.setenv("NAMMA_OPENMVS_DIR", str(mvs))
    monkeypatch.setattr(tools, "colmap_has_cuda", lambda exe: "cuda" in str(exe))
    monkeypatch.setattr(tools, "densify_has_cuda", lambda exe: True)
    gpu = {"available": True, "gpus": [{"index": 0, "name": "G", "driver": "1", "vram_total_mb": 6000.0,
                                        "vram_used_mb": 0.0, "vram_free_mb": 6000.0, "compute_capability": "8.9"}], "reason": None}
    t = tools.find_tools(gpu_info=gpu)
    assert t.colmap == cuda and t.colmap_gpu and t.densify_gpu
    assert t.densify_exe(True).parent == mvs_cuda and t.densify_exe(False).parent == mvs
    assert set(t.describe()["gpu_steps"]) == {"colmap_features", "colmap_matching", "openmvs_densify"}
    t = tools.find_tools(gpu_info={"available": False, "gpus": [], "reason": "none"})
    assert t.colmap == cpu and not t.colmap_gpu and not t.densify_gpu and t.describe()["gpu_steps"] == []
    monkeypatch.setattr(tools, "colmap_has_cuda", lambda exe: False)      # a "cuda" folder whose binary lacks CUDA
    t = tools.find_tools(gpu_info=gpu)
    assert t.colmap == cpu and not t.colmap_gpu and any("CUDA" in n for n in t.notes)


def test_gpu_failure_falls_back_to_cpu_once_but_not_on_cancel():
    from namma.studio.pipeline import gpu_then_cpu

    class Ctx:
        facts, status = {}, None

        def set_progress(self, *a):
            pass
    calls, cleaned = [], []

    def run(on_gpu):
        calls.append(on_gpu)
        if on_gpu:
            raise StepFailed({"status": "error", "log": "x.log"}, "cuda error")
        return {"ok": True}
    ctx = Ctx()
    rec, dev = gpu_then_cpu(ctx, "sfm", "feature extraction", run, True, before_retry=lambda: cleaned.append(1))
    assert dev == "cpu" and calls == [True, False] and cleaned == [1] and "GPU attempt failed" in ctx.facts["gpu_fallbacks"][0]
    assert gpu_then_cpu(Ctx(), "sfm", "x", lambda g: {"ok": g}, False) == ({"ok": False}, "cpu")
    assert gpu_then_cpu(Ctx(), "sfm", "x", lambda g: {"ok": g}, True) == ({"ok": True}, "gpu")

    def cancelled(on_gpu):
        raise StepFailed({"status": "cancelled"}, "cancelled")
    with pytest.raises(StepFailed):
        gpu_then_cpu(Ctx(), "sfm", "x", cancelled, True)


def test_colmap_and_openmvs_argv_only_change_when_gpu_is_requested(monkeypatch, tmp_path):
    from namma.recon import colmap, openmvs
    seen = []
    monkeypatch.setattr(colmap, "run_step", lambda step, argv, *a, **k: seen.append([str(x) for x in argv]))
    monkeypatch.setattr(openmvs, "run_step", lambda step, argv, *a, **k: seen.append([str(x) for x in argv]))
    T = tools.Tools(tmp_path / "colmap", tmp_path)
    kw = dict(camera_model="OPENCV", single_camera=True, max_image_px=1600, max_features=100, threads=2, timeout_s=1)
    colmap.extract_features(T, "db", "img", tmp_path, **kw)
    colmap.extract_features(T, "db", "img", tmp_path, **kw, use_gpu=True, gpu_index=0)
    cpu, gpu = seen[0], seen[1]
    assert cpu[cpu.index("--FeatureExtraction.use_gpu") + 1] == "0" and "--FeatureExtraction.gpu_index" not in cpu
    assert gpu[gpu.index("--FeatureExtraction.use_gpu") + 1] == "1" and gpu[gpu.index("--FeatureExtraction.gpu_index") + 1] == "0"
    colmap.match_features(T, "db", tmp_path, mode="exhaustive", timeout_s=1, use_gpu=True)
    assert seen[2][seen[2].index("--FeatureMatching.use_gpu") + 1] == "1"
    dkw = dict(resolution_level=0, max_resolution=1600, number_views=5, threads=4, timeout_s=1)
    openmvs.densify(T, "s.mvs", "d.mvs", tmp_path, tmp_path, **dkw)
    openmvs.densify(T, "s.mvs", "d.mvs", tmp_path, tmp_path, **dkw, exe=tmp_path / "cuda" / "D.exe", cuda_device=0)
    assert "--cuda-device" not in seen[3] and seen[4][0].endswith("D.exe") and seen[4][-2:] == ["--cuda-device", "0"]


def test_ram_pixel_budget_only_lowers_profile_budgets():
    from namma.studio.pipeline import ram_pixel_budget_mp
    assert ram_pixel_budget_mp(12.5) > 1000            # Victus default budget: Standard 750 / High 1000 unchanged
    assert ram_pixel_budget_mp(6.0) < 750              # a small budget lowers Standard's
    assert ram_pixel_budget_mp(0.5) == 100


def test_windows_path_limit_is_checked_before_tools_run(tmp_path, monkeypatch):
    # a 262-character undistorted-image path made COLMAP abort with a bare 0xC0000409 during this work
    from namma.core.jsonio import write_json
    from namma.studio import pipeline
    monkeypatch.setattr(pipeline.os, "name", "nt")
    short = pipeline.RunContext(tmp_path / "p", "run-1", {"profile": "preview", "pipeline_version": "x"})
    write_json(short.project_dir / "capture" / "capture_manifest.json", {"inputs": ["capture/originals/IMG_0001.png"]})
    pipeline.check_path_lengths(short)                       # fine
    deep = tmp_path / ("d" * 200)      # not created: Windows itself refuses such paths without long-path support
    ctx = pipeline.RunContext(deep, "run-1", {"profile": "preview", "pipeline_version": "x"})
    with pytest.raises(pipeline.StageError, match="too long for Windows"):
        pipeline.check_path_lengths(ctx)


def test_onedrive_detection(monkeypatch, tmp_path):
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive"))
    assert hardware.onedrive_synced(tmp_path / "OneDrive" / "Desktop" / "x")
    assert not hardware.onedrive_synced(tmp_path / "Local" / "x")
