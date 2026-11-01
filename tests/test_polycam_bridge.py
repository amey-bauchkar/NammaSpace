"""Polycam bridge: recognise exports, stage uploads, import (desktop drag-and-drop and phone). Synthetic files only."""
import json
import zipfile

import pytest

from namma.studio import phone_capture as pc
from namma.studio import polycam_bridge as pb

from test_mesh_import import box_room
from test_p13_studio import W, req, server  # noqa: F401  (fixture)
from test_phone_capture import free_port, req as preq


@pytest.fixture
def exports(tmp_path):
    glb = tmp_path / "room.glb"
    box_room(glb)
    gltf_zip = tmp_path / "GLTF polycam.zip"
    with zipfile.ZipFile(gltf_zip, "w") as z:
        z.write(glb, "21_10_2026.glb")
    raw = tmp_path / "Images Polycam.zip"
    with zipfile.ZipFile(raw, "w") as z:
        for i in range(3):
            z.writestr(f"keyframes/cameras/{i}.json", json.dumps({"fx": 766, "exif": {"LensModel": "iPhone 14 back camera 5.7mm f/1.5"}}))
            z.writestr(f"keyframes/images/{i}.jpg", b"\xff\xd8\xff\xe0jpg")
            z.writestr(f"keyframes/depth/{i}.png", b"png")
    loose = tmp_path / "loose.zip"
    with zipfile.ZipFile(loose, "w") as z:
        z.writestr("scene.gltf", "{}")
        z.writestr("scene.bin", b"0")
    junk = tmp_path / "notes.zip"
    with zipfile.ZipFile(junk, "w") as z:
        z.writestr("readme.txt", "hi")
    return {"glb": glb, "gltf_zip": gltf_zip, "raw": raw, "loose": loose, "junk": junk}


def test_files_are_recognised_by_content(exports, tmp_path):
    assert pb.classify(exports["glb"])[0] == "mesh"
    assert pb.classify(exports["gltf_zip"]) == ("mesh", {"glb": "21_10_2026.glb"})
    kind, info = pb.classify(exports["raw"])
    assert kind == "raw" and info["keyframes"] == 3
    with pytest.raises(pb.BridgeError, match="separate files"):
        pb.classify(exports["loose"])
    with pytest.raises(pb.BridgeError, match="no .glb model"):
        pb.classify(exports["junk"])
    (tmp_path / "x.txt").write_text("hello")
    with pytest.raises(pb.BridgeError, match="not a GLB or a zip"):
        pb.classify(tmp_path / "x.txt")
    assert pb.raw_summary(exports["raw"])["device"] == "iPhone 14"


def stage(staging, files):
    iid = staging.start()
    for f in files:
        with open(f, "rb") as fh:
            staging.save(iid, f.name, fh, f.stat().st_size)
    return iid


def test_import_keeps_raw_and_names_the_device(exports, tmp_path):
    st = pb.PolycamStaging(tmp_path / "projects")
    iid = stage(st, [exports["gltf_zip"], exports["raw"]])
    r = st.finish(iid, title="Living room")
    p = tmp_path / "projects" / r["project_id"]
    assert r["device"] == "iPhone 14" and r["raw"][0]["keyframes"] == 3
    assert (p / "capture" / "originals" / "Images_Polycam.zip").exists()
    assert (p / "capture" / "originals" / "GLTF_polycam.zip").exists()
    meta = json.loads((p / "capture" / "polycam_import.json").read_text())
    assert meta["mesh"]["glb"] == "21_10_2026.glb"
    assert (p / "runs" / r["run_id"] / "90_package" / "scene_package.json").exists()
    assert not (tmp_path / "projects" / "_incoming" / iid).exists()        # staging cleaned
    notices = json.loads((p / "project.json").read_text())["notices"]
    assert "3D scan (iPhone 14)" in notices[0]


def test_raw_only_or_bad_names_are_refused(exports, tmp_path):
    st = pb.PolycamStaging(tmp_path / "projects")
    iid = stage(st, [exports["raw"]])
    with pytest.raises(pb.BridgeError, match="Images export alone has no model"):
        st.finish(iid)
    with pytest.raises(pb.BridgeError):
        with open(exports["glb"], "rb") as fh:
            st.save(iid, "model.exe", fh, 10)
    with pytest.raises(pb.BridgeError, match="unknown or expired"):
        st.finish("../../etc")


def test_desktop_drag_and_drop_api(server, exports):  # noqa: F811
    port, studio = server
    st, body = req(port, "POST", "/api/polycam", {}, W)
    assert st == 201
    iid = body["import_id"]
    data = exports["gltf_zip"].read_bytes()
    st, body = req(port, "PUT", f"/api/polycam/{iid}/files/GLTF%20polycam.zip", data,
                   {**W, "Content-Type": "application/octet-stream"})
    assert st == 201 and body["kind"] == "mesh"
    assert req(port, "PUT", f"/api/polycam/{iid}/files/x.zip", data, {"Content-Type": "application/octet-stream"})[0] == 403   # CSRF header
    st, body = req(port, "POST", f"/api/polycam/{iid}/import", {"title": "Dropped"}, W)
    assert st == 201 and body["project_id"].startswith("dropped-")
    assert any(p["title"] == "Dropped" for p in req(port, "GET", "/api/projects")[1]["projects"])
    assert req(port, "POST", "/api/polycam/0123456789abcdef/import", {}, W)[0] == 404


def test_phone_import_needs_the_phones_own_session(exports, tmp_path, monkeypatch):
    from namma.studio.server import Studio
    studio = Studio(tmp_path / "projects")
    monkeypatch.setattr(pc, "lan_ips", lambda: ["192.168.50.7"])
    port = free_port()
    pairing, httpd = pc.start_phone_server(studio, port=port, bind="127.0.0.1", cert_dir=tmp_path / "tls")
    try:
        assert preq(port, "POST", "/api/polycam", {})[0] == 401                     # not paired
        tok = preq(port, "POST", "/api/pair", {"code": pairing.info()["code"]})[1]["token"]
        iid = preq(port, "POST", "/api/polycam", {}, token=tok)[1]["import_id"]
        other = preq(port, "POST", "/api/pair", {"code": pairing.info()["code"]})[1]["token"]
        data = exports["glb"].read_bytes()
        assert preq(port, "PUT", f"/api/polycam/{iid}/files/room.glb", data, token=other, ctype="application/octet-stream")[0] == 403
        assert preq(port, "PUT", f"/api/polycam/{iid}/files/room.glb", data, token=tok, ctype="application/octet-stream")[0] == 201
        st, body = preq(port, "POST", f"/api/polycam/{iid}/import", {"title": "From phone"}, token=tok)
        assert st == 201 and body["facts"]["mesh_faces"] > 0
        assert preq(port, "POST", f"/api/polycam/{iid}/import", {}, token=tok)[0] == 403                     # done: gone
    finally:
        httpd.shutdown()


def test_raw_photos_zip_and_images_import(tmp_path):
    from PIL import Image

    # Create synthetic test photos
    photos_dir = tmp_path / "raw_photos"
    photos_dir.mkdir()
    photo_files = []
    for i in range(3):
        p = photos_dir / f"photo_{i}.jpg"
        im = Image.new("RGB", (64, 48), color=(i * 40, 100, 150))
        im.save(p, "JPEG")
        photo_files.append(p)

    # 1. Test classify on standalone photo
    kind, info = pb.classify(photo_files[0])
    assert kind == "photos" and info["file"] == "photo_0.jpg"

    # 2. Test classify on ZIP of photos
    zip_path = tmp_path / "my_photos.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        for p in photo_files:
            z.write(p, f"subfolder/{p.name}")
        z.writestr("__MACOSX/._photo_0.jpg", b"junk")

    kind, info = pb.classify(zip_path)
    assert kind == "photos" and info["photos"] == 3

    # 3. Test staging and finish with ZIP of photos
    st = pb.PolycamStaging(tmp_path / "projects")
    iid = stage(st, [zip_path])
    res = st.finish(iid, title="My Photo Room")

    assert res["kind"] == "photos"
    assert res["photos"] == 3
    assert res["project_id"].startswith("my-photo-room-")

    p = tmp_path / "projects" / res["project_id"]
    assert (p / "capture" / "originals" / "photo_0.jpg").exists()
    assert (p / "capture" / "originals" / "photo_1.jpg").exists()
    assert (p / "capture" / "originals" / "photo_2.jpg").exists()
    assert not (tmp_path / "projects" / "_incoming" / iid).exists()

