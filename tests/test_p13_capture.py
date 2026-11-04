"""Phase 13 capture ingestion on SYNTHETIC media (generated in a temp folder).

Checks the code paths a real upload takes: validation, blocking errors, warnings, duplicate
handling, deterministic video frame selection and the IF-01 / manifest outputs.
These are not evidence about real captures.
"""
import hashlib
import shutil
import subprocess

import cv2
import numpy as np
import pytest
from PIL import Image

from namma.capture import checks
from namma.capture.ingest import ingest_capture
from namma.capture.media import MediaError, kind_of, probe_image
from namma.contracts.semantic import validate_document
from namma.core.issues import errors_only
from namma.core.jsonio import load_strict

FFMPEG = shutil.which("ffmpeg")


def textured(seed, w=1024, h=768):
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (h // 16, w // 16, 3), dtype=np.uint8)
    img = cv2.resize(base, (w, h), interpolation=cv2.INTER_NEAREST)
    for _ in range(60):
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        cv2.circle(img, (int(rng.integers(0, w)), int(rng.integers(0, h))), int(rng.integers(5, 60)), c, -1)
    return img


def scene_crops(n, w=900, h=675, step=60, seed=3):
    """Overlapping crops of one large synthetic texture: a 'walk' along a wall."""
    big = textured(seed, w + step * n + 10, h + 20)
    return [big[10:10 + h, i * step:i * step + w] for i in range(n)]


def make_project(tmp_path, images=(), extra=()):
    d = tmp_path / "proj"
    (d / "capture" / "originals").mkdir(parents=True)
    (d / "project.json").write_text('{"project_id": "proj", "title": "Test", "runs": []}', encoding="utf-8")
    for i, im in enumerate(images):
        cv2.imwrite(str(d / "capture" / "originals" / f"img_{i:03d}.jpg"), im, [cv2.IMWRITE_JPEG_QUALITY, 92])
    for name, data in extra:
        (d / "capture" / "originals" / name).write_bytes(data)
    return d


def test_kind_of():
    assert kind_of("a.JPG") == "image" and kind_of("b.mov") == "video" and kind_of("c.HEIC") == "heic"
    assert kind_of("d.txt") == "unsupported"


def test_probe_image_reads_exif_orientation(tmp_path):
    p = tmp_path / "rot.jpg"
    im = Image.fromarray(textured(1, 1200, 800)[:, :, ::-1])
    exif = Image.Exif()
    exif[0x0112] = 6          # rotate 90 deg CW on display
    exif[0x010F] = "TestMake"
    im.save(p, exif=exif.tobytes())
    info = probe_image(p)
    assert info["orientation"] == 6 and info["upright_width"] == 800 and info["upright_height"] == 1200
    assert info["make"] == "TestMake"


def test_probe_image_rejects_small_and_corrupt(tmp_path):
    small = tmp_path / "s.jpg"
    Image.new("RGB", (320, 240)).save(small)          # below the 480 px floor
    with pytest.raises(MediaError) as e:
        probe_image(small)
    assert e.value.code == "IMG-TOO-SMALL"
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    with pytest.raises(MediaError) as e:
        probe_image(bad)
    assert e.value.code == "IMG-UNREADABLE"


def test_blur_and_exposure_flags():
    sharp = [textured(s) for s in range(6)]
    blurry = cv2.GaussianBlur(sharp[0], (0, 0), 6)
    dark = (sharp[1] * 0.08).astype(np.uint8)
    metrics = []
    for img in sharp + [blurry, dark]:
        ok, enc = cv2.imencode(".jpg", img)
        tmp = np.frombuffer(enc, np.uint8)
        g = cv2.imdecode(tmp, cv2.IMREAD_GRAYSCALE)
        metrics.append({"sharpness": float(cv2.Laplacian(g, cv2.CV_64F).var()), "mean_luma": float(g.mean()),
                        "dark_clip": float(np.mean(g <= 8)), "bright_clip": float(np.mean(g >= 247)), "orb_keypoints": 900})
    flags, _ = checks.flag_images(metrics)
    assert "blurry" in flags[6] and "too_dark" in flags[7]
    assert all(not f for f in flags[:6])


def test_select_sharpest_is_deterministic():
    scores = [1, 5, 2, 9, 9, 3, 0.5]
    assert checks.select_sharpest(scores, 3) == [1, 3, 6]
    assert checks.select_sharpest(scores, 3) == checks.select_sharpest(list(scores), 3)


def test_ingest_photos_ready_with_overlap_and_duplicate_excluded(tmp_path):
    imgs = scene_crops(14)
    d = make_project(tmp_path, imgs)
    shutil.copy2(d / "capture" / "originals" / "img_000.jpg", d / "capture" / "originals" / "zz_copy.jpg")
    rep = ingest_capture(d)
    assert rep["status"] == "ready", rep["errors"]
    man = load_strict(d / "capture" / "capture_manifest.json")
    dup = [e for e in man["files"] if e["file"] == "zz_copy.jpg"][0]
    assert dup["decision"] == "excluded" and dup["reason"] == "DUPLICATE"
    assert len(man["inputs"]) == 14
    assert rep["stats"]["overlap"]["largest_group"] >= 12          # consecutive crops overlap
    if1 = load_strict(d / "capture" / "capture.if01.json")
    assert not errors_only(validate_document(if1, "IF-01"))
    assert len(list((d / "capture" / "thumbs").glob("*.jpg"))) == 14
    assert any(w["code"] == "CAP-FEW" for w in rep["warnings"])


def test_ingest_blocks_too_few_heic_and_mixed(tmp_path):
    d = make_project(tmp_path, scene_crops(5))
    rep = ingest_capture(d)
    assert rep["status"] == "blocked" and any(e["code"] == "CAP-TOO-FEW" for e in rep["errors"])
    d2 = make_project(tmp_path / "b", scene_crops(12), extra=[("x.HEIC", b"\x00" * 100)])
    rep2 = ingest_capture(d2)
    assert rep2["status"] == "blocked" and any(e["code"] == "CAP-HEIC" for e in rep2["errors"])
    d3 = make_project(tmp_path / "c", scene_crops(12), extra=[("walk.mp4", b"\x00" * 100)])
    rep3 = ingest_capture(d3)
    assert rep3["status"] == "blocked" and any(e["code"] == "CAP-MIXED" for e in rep3["errors"])
    d4 = make_project(tmp_path / "d", [], extra=[])
    assert ingest_capture(d4)["status"] == "blocked"


def test_unsupported_files_are_ignored_with_a_warning(tmp_path):
    d = make_project(tmp_path, scene_crops(12), extra=[("notes.txt", b"hello"), (".gitkeep", b"")])
    rep = ingest_capture(d)
    assert rep["status"] == "ready"
    warn_msgs = [w["message"] for w in rep["warnings"] if w["code"] == "CAP-UNSUPPORTED"]
    assert any("notes.txt" in m for m in warn_msgs)
    assert not any(".gitkeep" in m for m in warn_msgs)

    # If only unsupported files exist without photos or video, capture must be blocked
    d_unsupported_only = make_project(tmp_path / "only_unsupported", [], extra=[(".gitkeep", b""), ("notes.txt", b"hello")])
    rep_blocked = ingest_capture(d_unsupported_only)
    assert rep_blocked["status"] == "blocked"
    assert any(e["code"] == "CAP-EMPTY" for e in rep_blocked["errors"])


def test_polycam_import_archives_and_zip_extraction(tmp_path):
    import io
    import json
    import zipfile

    # Build a project with a zip archive containing keyframe camera photos and depth maps
    d = tmp_path / "polycam_proj"
    orig = d / "capture" / "originals"
    orig.mkdir(parents=True)
    (d / "project.json").write_text('{"project_id": "polycam_proj", "title": "Polycam Room", "runs": []}', encoding="utf-8")

    # Create dummy photo bytes
    crops = scene_crops(12)
    enc_photos = []
    for im in crops:
        _, enc = cv2.imencode(".jpg", im)
        enc_photos.append(enc.tobytes())

    # Build keyframes zip
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as z:
        for i, photo_bytes in enumerate(enc_photos):
            z.writestr(f"keyframes/images/frame_{i:04d}.jpg", photo_bytes)
            z.writestr(f"keyframes/depth/depth_{i:04d}.png", b"dummy_depth_map_data")
            z.writestr(f"keyframes/cameras/frame_{i:04d}.json", b"{}")
    (orig / "keyframes.zip").write_bytes(zip_buf.getvalue())
    (orig / "mesh.zip").write_bytes(b"dummy_mesh_zip")
    (orig / ".gitkeep").write_bytes(b"")

    # Register in polycam_import.json
    (d / "capture" / "polycam_import.json").write_text(json.dumps({
        "imported_at": "2026-11-04T12:00:00",
        "mesh": {"file": "mesh.zip"},
        "raw": [{"file": "keyframes.zip"}]
    }), encoding="utf-8")

    rep = ingest_capture(d)
    assert rep["status"] == "ready", rep["errors"]
    assert rep["stats"]["inputs_accepted"] == 12
    # Ensure neither .gitkeep, mesh.zip, nor keyframes.zip produced CAP-UNSUPPORTED warnings
    unsupported = [w for w in rep["warnings"] if w["code"] == "CAP-UNSUPPORTED"]
    assert len(unsupported) == 0, unsupported
    # Ensure depth maps were NOT extracted
    extracted_depth = list(orig.glob("*depth*"))
    assert len(extracted_depth) == 0


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
def test_video_frames_are_deterministic(tmp_path):
    src = tmp_path / "walk.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=duration=12:size=960x540:rate=30", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(src)],
                   check=True)
    runs = []
    for k in range(2):
        d = tmp_path / f"p{k}"
        (d / "capture" / "originals").mkdir(parents=True)
        (d / "project.json").write_text('{"project_id": "p", "title": "v", "runs": []}', encoding="utf-8")
        shutil.copy2(src, d / "capture" / "originals" / "walk.mp4")
        rep = ingest_capture(d, max_frames=40)
        assert rep["mode"] == "video" and rep["stats"]["inputs_accepted"] >= 20, rep
        man = load_strict(d / "capture" / "capture_manifest.json")
        hashes = [hashlib.sha256((d / "capture" / "frames" / f["name"]).read_bytes()).hexdigest()
                  for f in man["frames"]["frames"]]
        runs.append(([f["candidate_index"] for f in man["frames"]["frames"]], hashes))
    assert runs[0] == runs[1]


def test_mixed_portrait_and_landscape_gets_an_orientation_warning(tmp_path):
    # pre-capture QA: one phone held both ways must not be told to "use one phone"
    imgs = scene_crops(14)
    imgs = [im if i % 2 else np.ascontiguousarray(np.rot90(im)) for i, im in enumerate(imgs)]
    d = make_project(tmp_path, imgs)
    rep = ingest_capture(d)
    codes = [w["code"] for w in rep["warnings"]]
    assert "CAP-MIXED-ORIENTATION" in codes and "CAP-MIXED-CAMERAS" not in codes
    w = [w for w in rep["warnings"] if w["code"] == "CAP-MIXED-ORIENTATION"][0]
    assert w["portrait"] == 7 and w["landscape"] == 7
