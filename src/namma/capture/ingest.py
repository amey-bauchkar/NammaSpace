"""Capture ingestion for a Studio project (photos or one video) -> manifests and a readable report.

    ingest_capture(project_dir, max_frames=200, progress=cb) -> report dict

Inputs:   <project>/capture/originals/*   (uploaded files, never modified)
Outputs:  <project>/capture/frames/      (video only: deterministic frame selection)
          <project>/capture/thumbs/      (320 px previews for the UI)
          <project>/capture/capture_manifest.json   (every file, its checks and the accept / exclude decision)
          <project>/capture/capture.if01.json       (IF-01 contract document for the accepted inputs)
          <project>/capture/capture_report.json     (status ready / blocked, errors, warnings, statistics)
Blocking errors stop processing; warnings never do.
"""
import hashlib
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

from namma import CONTRACT_VERSION
from namma.capture import checks
from namma.capture.media import (
    LOW_RES_SIDE,
    MediaError,
    ffmpeg_path,
    kind_of,
    probe_image,
    probe_video,
)
from namma.contracts.semantic import validate_document
from namma.core import timefmt
from namma.core.issues import errors_only
from namma.core.jsonio import write_json
from namma.recon import hardware

MIN_IMAGES = 10
RECOMMENDED_IMAGES = 40
MAX_IMAGES = hardware.resources().max_images      # configurable (namma.recon.hardware); default 300
THUMB = 320


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _thumb(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        im.draft("RGB", (THUMB, THUMB))
        im = im.convert("RGB")
        im.thumbnail((THUMB, THUMB))
        im.save(dst, "JPEG", quality=82)


def _msg(code, severity, message, **extra):
    return {"code": code, "severity": severity, "message": message, **extra}


def extract_video_frames(video, out_dir, info, max_frames, max_side, progress=None, cancel=None):
    """Deterministic: fixed-rate candidates (ffmpeg fps filter), then the sharpest of each window."""
    exe = ffmpeg_path()
    if not exe:
        raise MediaError("VID-NO-FFMPEG", "FFmpeg is required for video input")
    cand = out_dir.parent / "frame_candidates"
    if cand.exists():
        shutil.rmtree(cand)
    cand.mkdir(parents=True)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    target = checks.target_frames_for(info["duration_s"], hi=max_frames)
    rate, window = checks.plan_video_sampling(info["duration_s"], info["fps"], target)
    vf = f"fps={rate},scale='if(gte(iw,ih),min({max_side},iw),-2)':'if(gte(iw,ih),-2,min({max_side},ih))'"
    # -map 0:v:0: phone files can hold several video streams (e.g. ARKit .mov: wide + ultra-wide); use the first
    argv = [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(video), "-map", "0:v:0", "-vf", vf,
            "-fps_mode", "passthrough", "-q:v", "2", str(cand / "c%05d.jpg")]
    p = subprocess.run(argv, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    files = sorted(cand.glob("c*.jpg"))
    if p.returncode != 0 or not files:
        raise MediaError("VID-DECODE", f"frame extraction failed: {p.stderr.strip()[:200]}")
    scores = []
    for i, f in enumerate(files):
        scores.append(checks.sharpness_only(f))
        if progress and i % 10 == 0:
            progress("Selecting sharp video frames", i / len(files))
    chosen = checks.select_sharpest(scores, window)
    frames = []
    for k, idx in enumerate(chosen):
        dst = out_dir / f"frame_{k:04d}.jpg"
        shutil.copy2(files[idx], dst)
        frames.append({"name": dst.name, "candidate_index": idx, "time_s": round(idx / rate, 3),
                       "sharpness": round(scores[idx], 1)})
    shutil.rmtree(cand)
    return frames, {"candidate_rate_fps": rate, "window": window, "candidates": len(files), "selected": len(frames),
                    "command": argv[:-1] + ["c%05d.jpg"]}


def ingest_capture(project_dir, *, max_frames=300, frame_max_side=1920, venue_label="", progress=None):
    project_dir = Path(project_dir)
    cap = project_dir / "capture"
    originals = cap / "originals"
    t0 = time.perf_counter()
    files = sorted(p for p in originals.iterdir() if p.is_file()) if originals.exists() else []
    errors, warnings, entries = [], [], []
    kinds = {}
    for f in files:
        kinds.setdefault(kind_of(f.name), []).append(f)
    if not files:
        errors.append(_msg("CAP-EMPTY", "error", "No files were uploaded."))
    for f in kinds.get("heic", []):
        entries.append({"file": f.name, "kind": "heic", "decision": "excluded", "reason": "HEIC_NOT_SUPPORTED"})
    if kinds.get("heic"):
        errors.append(_msg("CAP-HEIC", "error", f"{len(kinds['heic'])} HEIC photos cannot be read. On the iPhone set "
                           "Settings > Camera > Formats > Most Compatible (JPEG) and capture again, or export as JPEG."))
    for f in kinds.get("unsupported", []):
        entries.append({"file": f.name, "kind": "unsupported", "decision": "excluded", "reason": "UNSUPPORTED_TYPE"})
        warnings.append(_msg("CAP-UNSUPPORTED", "warning", f"{f.name} is not a photo or video and was ignored."))
    images, videos = kinds.get("image", []), kinds.get("video", [])
    mode = None
    if images and videos:
        errors.append(_msg("CAP-MIXED", "error", "Upload either photos or one video for a project, not both."))
    elif len(videos) > 1:
        errors.append(_msg("CAP-MULTI-VIDEO", "error", "Upload one video per project (record the whole room in one "
                           "continuous take)."))
    elif videos:
        mode = "video"
    elif images:
        mode = "photos"

    inputs = []          # accepted image paths used for reconstruction
    device = ""
    video_info = frame_info = None
    seen_hash = {}
    file_problems = {}
    if mode == "photos":
        for i, f in enumerate(images):
            if progress and i % 5 == 0:
                progress("Checking photos", i / len(images))
            e = {"file": f.name, "kind": "image", "bytes": f.stat().st_size, "sha256": _sha256(f)}
            try:
                e["info"] = probe_image(f)
            except MediaError as err:
                e.update(decision="excluded", reason=err.code, detail=str(err))
                entries.append(e)
                file_problems.setdefault(err.code, []).append(str(err))
                continue
            if e["sha256"] in seen_hash:
                e.update(decision="excluded", reason="DUPLICATE", duplicate_of=seen_hash[e["sha256"]])
                entries.append(e)
                continue
            seen_hash[e["sha256"]] = f.name
            e["decision"] = "accepted"
            entries.append(e)
            inputs.append(f)
        sizes = {(e["info"]["upright_width"], e["info"]["upright_height"]) for e in entries if e.get("decision") == "accepted"}
        models = {(e["info"]["make"], e["info"]["model"]) for e in entries if e.get("decision") == "accepted"}
        rotated_only = len(models) == 1 and len(sizes) == 2 and {(w, h) for w, h in sizes} == {(h, w) for w, h in sizes}
        if rotated_only:
            # one phone, held upright for some photos and sideways for others (common on phones): the advice
            # "use one phone" would be wrong; each photo is then calibrated separately, which is less stable
            portrait = sum(1 for e in entries if e.get("decision") == "accepted"
                           and e["info"]["upright_height"] > e["info"]["upright_width"])
            n_acc = sum(1 for e in entries if e.get("decision") == "accepted")
            warnings.append(_msg("CAP-MIXED-ORIENTATION", "warning",
                                 f"{portrait} photos are upright (portrait) and {n_acc - portrait} sideways (landscape). Hold "
                                 "the phone the same way for every photo (sideways is best): mixing them makes the "
                                 "camera calibration less stable.", portrait=portrait, landscape=n_acc - portrait))
        elif len(sizes) > 1 or len(models) > 1:
            warnings.append(_msg("CAP-MIXED-CAMERAS", "warning", "Photos come from different cameras or sizes; each is "
                                 "calibrated separately, which is less stable. Use one phone and one zoom level.",
                                 sizes=sorted(map(list, sizes))[:5]))
        device = " ".join(next(iter(models))).strip() if len(models) == 1 else "mixed"
        low = [e["file"] for e in entries if e.get("decision") == "accepted"
               and min(e["info"]["width"], e["info"]["height"]) < LOW_RES_SIDE]
        if low:
            warnings.append(_msg("CAP-LOW-RES", "warning", f"{len(low)} photos are low resolution (short side under "
                                 f"{LOW_RES_SIDE} px); fine detail and texture sharpness will be limited.", images=low[:30]))
    elif mode == "video":
        v = videos[0]
        e = {"file": v.name, "kind": "video", "bytes": v.stat().st_size, "sha256": _sha256(v)}
        try:
            video_info = probe_video(v)
            e["info"] = video_info
            if progress:
                progress("Extracting video frames", 0.0)
            frames, frame_info = extract_video_frames(v, cap / "frames", video_info, max_frames, frame_max_side, progress)
            e.update(decision="accepted", frames=frame_info)
            inputs = [cap / "frames" / fr["name"] for fr in frames]
            frame_info["frames"] = frames
            device = video_info.get("device") or ""
            if video_info.get("hdr"):
                warnings.append(_msg("VID-HDR", "warning", "This is an HDR video; colours may look washed out. Turn off "
                                     "HDR video in the phone's camera settings."))
        except MediaError as err:
            e.update(decision="excluded", reason=err.code, detail=str(err))
            errors.append(_msg(err.code, "error", str(err)))
        entries.append(e)

    stats = {"mode": mode, "files_uploaded": len(files), "inputs_accepted": len(inputs)}
    if mode and not errors:
        if len(inputs) < MIN_IMAGES:
            errors.append(_msg("CAP-TOO-FEW", "error", f"Only {len(inputs)} usable photos; at least {MIN_IMAGES} are "
                               f"needed and {RECOMMENDED_IMAGES}+ are recommended for a room."))
        elif len(inputs) < RECOMMENDED_IMAGES:
            warnings.append(_msg("CAP-FEW", "warning", f"{len(inputs)} photos is few for a whole room; "
                                 f"{RECOMMENDED_IMAGES}-150 work better."))
        if len(inputs) > MAX_IMAGES:
            errors.append(_msg("CAP-TOO-MANY", "error", f"{len(inputs)} photos exceed the {MAX_IMAGES} limit for this "
                               "laptop; split the space into areas or remove duplicates."))
    if inputs and not errors:
        metrics, feats = [None] * len(inputs), [None] * len(inputs)

        def work(i):
            metrics[i], feats[i] = checks.image_metrics(inputs[i])
        with ThreadPoolExecutor(max_workers=hardware.resources().io_workers) as ex:
            for k, _ in enumerate(ex.map(work, range(len(inputs)))):
                if progress and k % 5 == 0:
                    progress("Measuring sharpness, exposure and detail", k / len(inputs))
        flags, flag_stats = checks.flag_images(metrics)
        if progress:
            progress("Checking that photos overlap", 0.0)
        graph = checks.overlap_graph(feats, ordered=(mode == "video"))
        neardup = checks.near_duplicates([m["dhash"] for m in metrics])
        by_name = {e["file"]: e for e in entries}
        per_input = []
        for i, path in enumerate(inputs):
            rec = {"name": path.name, **metrics[i], "flags": flags[i], "overlap_degree": graph["degree"][i]}
            rec.pop("dhash")
            per_input.append(rec)
            if mode == "photos":
                by_name[path.name]["checks"] = rec
        n = len(inputs)
        count = lambda flag: sum(1 for f in flags if flag in f)   # noqa: E731
        blurry, dark, bright, low = count("blurry"), count("too_dark"), count("too_bright"), count("low_detail")
        if blurry / n > 0.15:
            warnings.append(_msg("CAP-BLUR", "warning", f"{blurry} of {n} images look blurry. Move slowly, hold the phone "
                                 "steady and use good light.", images=[p["name"] for p in per_input if "blurry" in p["flags"]][:30]))
        if dark / n > 0.15:
            warnings.append(_msg("CAP-DARK", "warning", f"{dark} images are too dark. Turn on the lights.",
                                 images=[p["name"] for p in per_input if "too_dark" in p["flags"]][:30]))
        if bright / n > 0.15:
            warnings.append(_msg("CAP-BRIGHT", "warning", f"{bright} images are over-exposed (windows, lamps).",
                                 images=[p["name"] for p in per_input if "too_bright" in p["flags"]][:30]))
        if low / n > 0.25:
            warnings.append(_msg("CAP-LOW-DETAIL", "warning", f"{low} images show few details (plain walls, floor or "
                                 "glass); include furniture and edges in every photo.",
                                 images=[p["name"] for p in per_input if "low_detail" in p["flags"]][:30]))
        isolated = [inputs[i].name for i in range(n) if graph["degree"][i] == 0]
        if isolated:
            warnings.append(_msg("CAP-ISOLATED", "warning", f"{len(isolated)} images share few matching details with the "
                                 "others in this quick check and might not be placed. The check is cautious (on test rooms "
                                 "it also flagged photos that were placed): after processing, look at 'Photos placed' and "
                                 "re-shoot around the missing ones.", images=isolated[:30]))
        comps = [c for c in graph["components"] if len(c) > 1]
        if len(comps) > 1:
            warnings.append(_msg("CAP-GROUPS", "warning", f"In this quick check the images form {len(comps)} groups "
                                 "that do not overlap. If the final model is split or photos are missing, take extra photos "
                                 "while walking between those areas so they connect.",
                                 group_sizes=[len(c) for c in comps][:10]))
        if mode == "photos" and len(neardup) / n > 0.2:
            warnings.append(_msg("CAP-NEAR-DUP", "warning", f"{len(neardup)} photo pairs are almost identical; move a "
                                 "step between photos instead of re-shooting from the same spot."))
        stats.update({"flags": flag_stats, "blurry": blurry, "too_dark": dark, "too_bright": bright,
                      "low_detail": low, "near_duplicate_pairs": len(neardup),
                      "overlap": {"pairs_tested": graph["pairs_tested"], "verified_pairs": graph["edges"],
                                  "isolated": len(isolated), "groups": len(comps),
                                  "largest_group": len(graph["components"][0]) if graph["components"] else 0}})
        thumbs = cap / "thumbs"
        for i, path in enumerate(inputs):
            _thumb(path, thumbs / (path.stem + ".jpg"))
    else:
        per_input = []

    titles = {"IMG-TOO-SMALL": "photos are smaller than the minimum size and were left out",
              "IMG-UNREADABLE": "files could not be read as images and were left out",
              "IMG-TOO-LARGE": "photos are larger than the 60 MB limit and were left out",
              "IMG-FORMAT": "photos have an unsupported format and were left out"}
    for code, msgs in file_problems.items():         # one grouped warning per problem type, not one per file
        warnings.insert(0, _msg(code, "warning", f"{len(msgs)} {titles.get(code, 'files were left out')}.",
                                examples=msgs[:5]))
    status = "blocked" if errors else "ready"
    created = timefmt.now_iso()
    manifest = {"schema": "namma-capture-manifest/1", "project_dir": project_dir.name, "created_at": created,
                "mode": mode, "files": entries, "inputs": [str(p.relative_to(project_dir)).replace("\\", "/") for p in inputs],
                "per_input": per_input, "video": video_info, "frames": frame_info}
    write_json(cap / "capture_manifest.json", manifest)
    if not errors and inputs:
        if1 = {"contract": "IF-01", "contract_version": CONTRACT_VERSION, "run_id": project_dir.name,
               "venue_label": venue_label or project_dir.name, "created_at": created,
               "files": [{"path": "capture/originals/" + e["file"], "sha256": e["sha256"], "bytes": e["bytes"],
                          "region_id": "R1", "kind": "video" if e["kind"] == "video" else "image_set",
                          "device": device or "unknown",
                          "duration_s": (e.get("info") or {}).get("duration_s"),
                          "issues": [w["code"] for w in warnings][:20]}
                         for e in entries if e.get("decision") == "accepted"],
               "regions": [{"region_id": "R1", "status": "captured", "note": "single-region Studio capture"}]}
        errs = errors_only(validate_document(if1, "IF-01"))
        if errs:
            raise RuntimeError("IF-01 invalid: " + "; ".join(e.message for e in errs))
        write_json(cap / "capture.if01.json", if1)
    report = {"status": status, "mode": mode, "errors": errors, "warnings": warnings, "stats": stats,
              "seconds": round(time.perf_counter() - t0, 1), "created_at": created}
    write_json(cap / "capture_report.json", report)
    return report
