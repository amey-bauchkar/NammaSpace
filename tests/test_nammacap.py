"""Namma Capture (.nammacap) archives: validation, import into a new project, COLMAP prior adapter, phone upload route.

Archives here are written by a small independent Python builder (not by the iOS app), so the importer is tested against
the format document rather than against one writer. If NAMMACAP_SWIFT_FIXTURE points at an archive produced by the
Swift exporter (NammaCapture/Core: `NAMMACAP_FIXTURE_OUT=... swift test`), it is validated and imported too.
"""
import hashlib
import io
import json
import math
import os
import zipfile
from pathlib import Path

import pytest

from namma.capture import nammacap as nc
from namma.studio.projects import ProjectStore


def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]]


def pose(i):
    R = rot_y(0.2 * i)
    return [R[0] + [0.3 * i], R[1] + [1.4], R[2] + [0.0], [0, 0, 0, 1]]


def frame(i, depth=True, cf=0, room="room_1"):
    fid = f"f{i:06d}"
    k = {"fx": 3000.0, "fy": 3000.0, "cx": 2016.0, "cy": 1512.0, "width": 4032, "height": 3024}
    return {"frame_id": fid, "index": i, "timestamp_s": i * 0.8, "wallclock": "2026-11-22T10:00:00Z",
            "coordinate_frame_id": cf, "room_id": room,
            "image": {"path": f"images/{fid}.jpg", "width": 4032, "height": 3024, "source": "high_resolution_capture",
                      "interface_orientation": "portrait"},
            "camera": {"T_world_from_camera": pose(i), "intrinsics": k, "euler_angles_rad": [0, 0, 0]},
            "tracking": {"state": "normal", "reason": None, "world_mapping": "mapped"},
            "exposure": {"exposure_duration_s": None, "iso": None, "brightness_value": None, "exposure_bias_ev": None,
                         "ambient_intensity_lm": None, "ambient_color_temperature_k": None},
            "quality": {"metrics": {"sharpness": 80, "mean_luma": 120, "dark_fraction": 0, "bright_fraction": 0},
                        "warnings": [], "overridden": False},
            "selection": {"trigger": "auto", "reason": "translation", "translation_since_last_m": 0.3,
                          "rotation_since_last_deg": 11.0},
            "depth": ({"path": f"depth/{fid}.depth.f32", "confidence_path": f"depth/{fid}.conf.u8", "width": 4,
                       "height": 3, "source": "arkit_scene_depth", "intrinsics": k, "timestamp_s": i * 0.8,
                       "T_world_from_camera": pose(i), "pairing": "same_frame",
                       "valid_sample_fraction": 1.0} if depth else None),
            "flags": []}


def build(path, n=5, depth=True, mutate=None, members_mutate=None, schema="1.0", coordinate_frames=(0,)):
    frames = [frame(i + 1, depth=depth, cf=coordinate_frames[min(i, len(coordinate_frames) - 1)]) for i in range(n)]
    members = {}
    for f in frames:
        members[f["image"]["path"]] = b"\xff\xd8\xff\xe0" + bytes([f["index"]]) * 500
        if f["depth"]:
            members[f["depth"]["path"]] = b"\x00\x00\x80\x3f" * 12
            members[f["depth"]["confidence_path"]] = b"\x02" * 12
    man = {"format": "nammacap", "schema_version": schema, "min_reader_version": "1.0",
           "created_by": {"app": "Namma Capture", "app_version": "0.1.0", "build": "1"},
           "session": {"id": "s1", "name": "Test flat", "started_at": "2026-11-22T10:00:00Z",
                       "ended_at": "2026-11-22T10:05:00Z", "status": "complete"},
           "device": {"model_identifier": "iPhone15,4", "system_name": "iOS", "system_version": "18.0"},
           "capabilities": {"world_tracking": True, "scene_depth": depth},
           "conventions": {}, "depth": {"available": depth, "source": "arkit_scene_depth" if depth else None,
                                        "unavailable_reason": None if depth else "no LiDAR"},
           "capture_settings": {}, "scale": {"provenance": "arkit_visual_inertial", "status": "device_tracked_unverified",
                                             "validations": []},
           "coordinate_frames": [{"id": c, "started_at": "x", "started_at_timestamp_s": None, "reason": "r"}
                                 for c in sorted(set(coordinate_frames))],
           "rooms": [{"id": "room_1", "label": "Living", "created_at": "x"}],
           "counts": {"frames_accepted": n, "frames_rejected": 0, "frames_with_depth": n if depth else 0},
           "files": {}, "warnings": [], "recovery": None}
    records = {"frames.jsonl": "".join(json.dumps(f) + "\n" for f in frames).encode(),
               "rejections.jsonl": b"", "trajectory.jsonl": b"", "events.jsonl": b"",
               "annotations.json": b'{"annotations": []}', "coverage.json": b'{"rooms": [], "available": true}'}
    if mutate:
        mutate(man, frames, records)
        records["frames.jsonl"] = "".join(json.dumps(f) + "\n" for f in frames).encode()
    members = {"manifest.json": json.dumps(man).encode(), **records, **members}
    if members_mutate:
        members_mutate(members)
    integ = [{"path": p, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()} for p, b in members.items()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for p, b in members.items():
            z.writestr(p, b)
        z.writestr("integrity.json", json.dumps({"algorithm": "sha256", "file_count": len(integ),
                                                 "total_bytes": sum(i["bytes"] for i in integ), "files": integ}))
    return path


def test_valid_archive(tmp_path):
    r = nc.validate(build(tmp_path / "a.nammacap"))
    assert r["ok"], r["errors"]
    assert (r["frames"], r["depth_frames"], r["schema_version"]) == (5, 5, "1.0")


def test_non_lidar_archive_is_valid_without_depth(tmp_path):
    r = nc.validate(build(tmp_path / "a.nammacap", depth=False))
    assert r["ok"], r["errors"]
    assert r["depth_frames"] == 0


@pytest.mark.parametrize("case, expect", [
    ("truncated", "not a readable ZIP"),
    ("flipped_byte", "CRC"),
    ("extra_member", "not covered by integrity"),
    ("missing_image", "image images/f000002.jpg missing"),
    ("future_major", "incompatible schema 2.0"),
    ("count_mismatch", "manifest counts"),
    ("nan_in_manifest", "manifest.json"),
    ("bad_pose", "invalid pose"),
    ("depth_wrong_size", "depth size mismatch"),
    ("unknown_coordinate_frame", "unknown coordinate_frame_id"),
    ("no_integrity", "integrity.json missing"),
    ("mesh_missing", "names mesh"),
])
def test_bad_archives_are_rejected(tmp_path, case, expect):
    p = tmp_path / "a.nammacap"
    if case == "truncated":
        build(p)
        p.write_bytes(p.read_bytes()[:-400])
    elif case == "flipped_byte":
        build(p)
        b = bytearray(p.read_bytes())
        i = b.find(b"\xff\xd8\xff\xe0") + 10
        b[i] ^= 0xFF
        p.write_bytes(bytes(b))
    elif case == "extra_member":
        build(p)
        with zipfile.ZipFile(p, "a") as z:
            z.writestr("images/sneaky.jpg", b"x")
    elif case == "missing_image":
        build(p, members_mutate=lambda m: m.pop("images/f000002.jpg"))
    elif case == "future_major":
        build(p, schema="2.0")
    elif case == "count_mismatch":
        build(p, mutate=lambda man, fr, rec: man["counts"].update(frames_accepted=99))
    elif case == "nan_in_manifest":
        build(p, members_mutate=lambda m: m.update({"manifest.json": m["manifest.json"].replace(b'"status": "device_tracked_unverified"', b'"status": NaN')}))
    elif case == "bad_pose":
        build(p, mutate=lambda man, fr, rec: fr[0]["camera"]["T_world_from_camera"][0].__setitem__(0, 5.0))
    elif case == "depth_wrong_size":
        build(p, members_mutate=lambda m: m.update({"depth/f000001.depth.f32": b"\x00" * 10}))
    elif case == "unknown_coordinate_frame":
        build(p, mutate=lambda man, fr, rec: fr[0].update(coordinate_frame_id=7))
    elif case == "mesh_missing":
        build(p, mutate=lambda man, fr, rec: man["files"].update(mesh="mesh/arkit_scene_mesh.glb"))
    elif case == "no_integrity":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("manifest.json", json.dumps({"format": "nammacap", "schema_version": "1.0", "session": {},
                       "device": {}, "capabilities": {}, "conventions": {}, "depth": {}, "capture_settings": {},
                       "scale": {}, "coordinate_frames": [], "rooms": [], "counts": {}, "warnings": []}))
        p.write_bytes(buf.getvalue())
    r = nc.validate(p)
    assert not r["ok"]
    assert any(expect in e for e in r["errors"]), r["errors"]


def test_unsafe_member_names_are_rejected(tmp_path):
    for bad in ("../x", "/abs", "a//b", "a\\b", "c:x", "a/./b", ""):
        assert not nc.safe_member(bad), bad
    p = tmp_path / "a.nammacap"
    build(p, members_mutate=lambda m: m.update({"../evil.txt": b"x"}))
    r = nc.validate(p)
    assert not r["ok"] and "unsafe member path" in r["errors"][0]


def test_newer_minor_version_is_accepted_with_warning(tmp_path):
    r = nc.validate(build(tmp_path / "a.nammacap", schema="1.3"))
    assert r["ok"], r["errors"]
    assert any("newer than this importer" in w for w in r["warnings"])


def test_import_creates_project_with_originals_records_depth_and_priors(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    res = nc.import_archive(store, build(tmp_path / "a.nammacap", n=4), title="My flat")
    pid = res["project_id"]
    cap = store.path(pid) / "capture"
    assert sorted(p.name for p in (cap / "originals").iterdir()) == [f"f{i:06d}.jpg" for i in range(1, 5)]
    for name in ("manifest.json", "integrity.json", "frames.jsonl", "events.jsonl", "import_report.json"):
        assert (cap / "nammacap" / name).exists(), name
    assert (cap / "nammacap" / "depth" / "f000001.depth.f32").stat().st_size == 48
    images = (cap / "nammacap" / "colmap_priors" / "coordinate_frame_0" / "images.txt").read_text().splitlines()
    data = [l for l in images if l and not l.startswith("#")]
    assert len(data) == 4 and data[0].endswith("f000001.jpg")
    assert store.get(pid)["title"] == "My flat"
    assert any("does not read them yet" in n for n in store.get(pid)["notices"])
    assert not list(store.root.glob("_nammacap-staging-*"))          # staging removed


def test_invalid_archive_creates_no_project(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    p = build(tmp_path / "a.nammacap", members_mutate=lambda m: m.pop("images/f000003.jpg"))
    with pytest.raises(nc.NammaCapError) as e:
        nc.import_archive(store, p)
    assert e.value.code == "NAMMACAP-INVALID"
    assert store.list() == []
    assert not list(store.root.glob("_nammacap-staging-*"))


def test_separate_coordinate_frames_get_separate_prior_models(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    res = nc.import_archive(store, build(tmp_path / "a.nammacap", n=4, coordinate_frames=(0, 0, 1, 1)))
    assert sorted(res["colmap_priors"]) == [0, 1]
    assert res["coordinate_frames"] == 2


def test_photo_route_gets_at_most_its_limit_with_user_frames_first(tmp_path, monkeypatch):
    def mark(man, fr, rec):
        fr[7]["selection"]["trigger"] = "doorway"
        fr[18]["selection"]["trigger"] = "annotation"
    store = ProjectStore(tmp_path / "projects")
    monkeypatch.setattr(nc, "photo_route_limit", lambda: 6)
    res = nc.import_archive(store, build(tmp_path / "a.nammacap", n=20, depth=False, mutate=mark))
    cap = store.path(res["project_id"]) / "capture"
    used = sorted(p.name for p in (cap / "originals").iterdir())
    assert len(used) == 6 and res["frames_in_photo_route"] == 6
    assert "f000008.jpg" in used and "f000019.jpg" in used                  # doorway + annotation frames kept
    assert len(list((cap / "nammacap" / "images").iterdir())) == 20            # nothing discarded
    assert any("6 of 20 keyframes" in n for n in store.get(res["project_id"])["notices"])


def test_a_stray_lower_resolution_frame_stays_out_of_the_photo_route(tmp_path):
    """First real capture: one 1920x1440 video-stream fallback among 4032x3024 stills -> CAP-MIXED-CAMERAS."""
    def fallback(man, fr, rec):
        fr[-1]["image"].update(width=1920, height=1440, source="video_stream")
        fr[-1]["camera"]["intrinsics"].update(width=1920, height=1440)
        fr[-1]["flags"].append("high_resolution_capture_failed: Capturing a high resolution frame failed.")
    store = ProjectStore(tmp_path / "projects")
    res = nc.import_archive(store, build(tmp_path / "a.nammacap", n=12, depth=False, mutate=fallback))
    cap = store.path(res["project_id"]) / "capture"
    assert res["frames_other_size"] == ["f000012"]
    assert "f000012.jpg" not in {p.name for p in (cap / "originals").iterdir()}
    assert (cap / "nammacap" / "images" / "f000012.jpg").exists()          # kept, not discarded
    assert any("different image size" in n for n in store.get(res["project_id"])["notices"])


def test_imported_photos_pass_the_existing_capture_check(tmp_path):
    """The real photo-route ingest accepts what the importer lays out (decodable JPEGs, one camera size)."""
    from PIL import Image
    from namma.capture.ingest import ingest_capture

    def real_jpegs(m):
        for p in list(m):
            if p.startswith("images/"):
                buf = io.BytesIO()
                Image.effect_noise((1600, 1200), 60).convert("RGB").save(buf, "JPEG", quality=90)
                m[p] = buf.getvalue()
    store = ProjectStore(tmp_path / "projects")
    res = nc.import_archive(store, build(tmp_path / "a.nammacap", n=12, depth=False, members_mutate=real_jpegs))
    rep = ingest_capture(store.path(res["project_id"]), max_frames=300)
    assert rep["mode"] == "photos"
    assert rep["errors"] == [], rep["errors"]
    assert rep["status"] == "ready", (rep["status"], rep["warnings"])


def test_arkit_to_colmap_recovers_camera_centre_and_view_direction():
    for i in range(6):
        T = pose(i)
        qw, qx, qy, qz, tx, ty, tz = nc.arkit_to_colmap(T)
        assert qw >= 0 and abs(qw * qw + qx * qx + qy * qy + qz * qz - 1) < 1e-9
        R = [[1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
             [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
             [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)]]
        t = (tx, ty, tz)
        C = [-sum(R[r][c] * t[r] for r in range(3)) for c in range(3)]          # C = -R^T t
        assert all(abs(C[k] - T[k][3]) < 1e-9 for k in range(3))
        view = [R[2][c] for c in range(3)]                                     # COLMAP +Z in world = R^T e_z
        fwd = [-T[k][2] for k in range(3)]                                     # ARKit -Z column
        assert all(abs(view[k] - fwd[k]) < 1e-9 for k in range(3))


@pytest.mark.skipif(not os.environ.get("NAMMACAP_SWIFT_FIXTURE"), reason="set NAMMACAP_SWIFT_FIXTURE to a Swift-exported archive")
def test_swift_exported_archive_validates_and_imports(tmp_path):
    p = Path(os.environ["NAMMACAP_SWIFT_FIXTURE"])
    r = nc.validate(p)
    assert r["ok"], r["errors"]
    res = nc.import_archive(ProjectStore(tmp_path / "projects"), p)
    assert res["frames_imported"] == r["frames"]
    if res["mesh"]:          # the Swift-written GLB is readable by the existing mesh route's parser
        from namma.studio.mesh_import import mesh_triangles
        store = ProjectStore(tmp_path / "projects")
        glb = (store.path(res["project_id"]) / "capture" / "nammacap" / res["mesh"]).read_bytes()
        V, F, stats = mesh_triangles(glb)
        assert len(F) > 0 and V.shape[1] == 3


# ------------------------------------------------------------------------------------------- phone upload route
from tests.test_phone_capture import phone, req  # noqa: E402,F401  (reuse the HTTPS listener fixture)


def pair(port, pairing):
    return req(port, "POST", "/api/pair", {"code": pairing.info()["code"]})[1]["token"]


def test_upload_resume_and_import(phone, tmp_path):
    port, pairing, studio, calls, _ = phone
    tok = pair(port, pairing)
    data = build(tmp_path / "a.nammacap", n=6).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    st, up = req(port, "POST", "/api/nammacap", {"bytes": len(data), "sha256": sha, "title": "Upload test"}, token=tok)
    assert st == 201 and up["parts"] == 1
    uid = up["upload_id"]
    assert req(port, "POST", f"/api/nammacap/{uid}/finish", {}, token=tok)[0] == 409       # part missing
    assert req(port, "GET", f"/api/nammacap/{uid}", token=tok)[1]["received"] == []
    assert req(port, "PUT", f"/api/nammacap/{uid}/part/0", data[:-1], token=tok, ctype="application/octet-stream")[0] == 400
    assert req(port, "PUT", f"/api/nammacap/{uid}/part/0", data, token=tok, ctype="application/octet-stream")[0] == 201
    assert req(port, "GET", f"/api/nammacap/{uid}", token=tok)[1]["received"] == [0]
    st, res = req(port, "POST", f"/api/nammacap/{uid}/finish", {}, token=tok)
    assert st == 201, res
    assert res["frames_imported"] == 6
    assert studio.store.get(res["project_id"])["title"] == "Upload test"
    assert calls == []                      # an upload never starts a reconstruction by itself
    assert not (studio.store.root / "_nammacap_uploads" / uid).exists()


def test_upload_checksum_mismatch_and_ownership(phone, tmp_path):
    port, pairing, studio, *_ = phone
    tok = pair(port, pairing)
    data = build(tmp_path / "a.nammacap", n=3).read_bytes()
    st, up = req(port, "POST", "/api/nammacap", {"bytes": len(data), "sha256": "0" * 64}, token=tok)
    uid = up["upload_id"]
    req(port, "PUT", f"/api/nammacap/{uid}/part/0", data, token=tok, ctype="application/octet-stream")
    st, body = req(port, "POST", f"/api/nammacap/{uid}/finish", {}, token=tok)
    assert st == 422 and body["error"] == "NAMMACAP-CHECKSUM"
    assert req(port, "GET", f"/api/nammacap/{uid}", token=tok)[1]["received"] == []      # must be re-sent
    tok2 = pair(port, pairing)
    assert req(port, "GET", f"/api/nammacap/{uid}", token=tok2)[0] == 403                # another phone
    assert req(port, "POST", "/api/nammacap", {"bytes": 10, "sha256": "x"}, token=tok)[0] == 400
    assert req(port, "POST", "/api/nammacap", {"bytes": 10, "sha256": "0" * 64})[0] == 401   # unpaired
    assert studio.store.list() == []


def test_upload_of_invalid_archive_reports_errors_and_imports_nothing(phone, tmp_path):
    port, pairing, studio, *_ = phone
    tok = pair(port, pairing)
    data = build(tmp_path / "a.nammacap", schema="2.0").read_bytes()
    up = req(port, "POST", "/api/nammacap", {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}, token=tok)[1]
    req(port, "PUT", f"/api/nammacap/{up['upload_id']}/part/0", data, token=tok, ctype="application/octet-stream")
    st, body = req(port, "POST", f"/api/nammacap/{up['upload_id']}/finish", {}, token=tok)
    assert st == 422 and body["error"] == "NAMMACAP-INVALID"
    assert "incompatible schema 2.0" in body["message"]
    assert studio.store.list() == []
