"""Phone auto-capture listener (HTTPS): pairing, tokens, frame upload rules, auto check -> Preview hand-off."""
import http.client
import json
import socket
import ssl
import time

import pytest

from namma.studio import phone_capture as pc


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def phone(tmp_path, monkeypatch):
    from namma.studio.server import Studio
    studio = Studio(tmp_path / "projects")
    calls = []
    monkeypatch.setattr(pc, "auto_process", lambda st, pid: calls.append(pid))
    monkeypatch.setattr(pc, "lan_ips", lambda: ["192.168.50.7"])
    port = free_port()
    pairing, httpd = pc.start_phone_server(studio, port=port, bind="127.0.0.1", cert_dir=tmp_path / "tls")
    yield port, pairing, studio, calls, tmp_path
    httpd.shutdown()


def req(port, method, path, body=None, token=None, host="192.168.50.7", ctype="application/json"):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE                      # self-signed, as on the phone
    c = http.client.HTTPSConnection("127.0.0.1", port, context=ctx, timeout=20)
    h = {"Host": f"{host}:{port}"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    if data is not None:
        h["Content-Type"] = ctype
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 2000


def test_certificate_names_the_lan_address(tmp_path):
    from cryptography import x509
    crt, key = pc.ensure_cert(["192.168.50.7"], tmp_path / "tls")
    cert = x509.load_pem_x509_certificate(crt.read_bytes())
    ips = [str(i) for i in cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)]
    assert "192.168.50.7" in ips and "127.0.0.1" in ips
    assert pc.ensure_cert(["192.168.50.7"], tmp_path / "tls")[0].read_bytes() == crt.read_bytes()   # reused, not remade


def test_page_is_served_and_only_whitelisted_files(phone):
    port, *_ = phone
    st, body = req(port, "GET", "/")
    assert st == 200 and b"Namma Space Capture" in body
    assert req(port, "GET", "/js/captureLogic.js")[0] == 200
    for p in ("/studio.html", "/viewer.html", "/files/x/project.json", "/api/projects", "/../src/namma/__init__.py"):
        assert req(port, "GET", p)[0] == 404, p
    assert req(port, "GET", "/", host="evil.example")[0] == 403        # DNS-rebinding guard


def test_pairing_code_is_single_use_and_guesses_are_limited(phone):
    port, pairing, *_ = phone
    code = pairing.info()["code"]
    st, body = req(port, "POST", "/api/pair", {"code": "nope"})
    assert st == 403 and body["error"] == "CODE-WRONG"
    st, body = req(port, "POST", "/api/pair", {"code": code})
    assert st == 200 and len(body["token"]) > 30
    assert req(port, "POST", "/api/pair", {"code": code})[0] == 403       # used once, then replaced
    c2 = pairing.info()["code"]
    for _ in range(pc.MAX_WRONG_CODES):
        req(port, "POST", "/api/pair", {"code": "000000" if c2 != "000000" else "111111"})
    assert pairing.info()["code"] != c2                                    # too many guesses: code replaced


def test_session_frames_done(phone):
    port, pairing, studio, calls, _ = phone
    _, tok = req(port, "POST", "/api/pair", {"code": pairing.info()["code"]})
    tok = tok["token"]
    assert req(port, "POST", "/api/session", {"title": "Hall"})[0] == 401   # no token
    st, s = req(port, "POST", "/api/session", {"title": "Hall"}, token=tok)
    assert st == 201
    pid = s["project_id"]
    assert req(port, "POST", "/api/session", {"title": "again"}, token=tok)[1]["project_id"] == pid   # one per token
    base = f"/api/session/{pid}/frames/"
    assert req(port, "PUT", base + "../x.jpg", JPEG, token=tok, ctype="image/jpeg")[0] in (400, 404)
    assert req(port, "PUT", base + "photo.jpg", JPEG, token=tok, ctype="image/jpeg")[1]["error"] == "FRAME-NAME"
    assert req(port, "PUT", base + "frame_00001.jpg", b"GIF89a" + b"0" * 100, token=tok, ctype="image/jpeg")[1]["error"] == "FRAME-NOT-JPEG"
    st, body = req(port, "POST", f"/api/session/{pid}/done", {"frames": []}, token=tok)
    assert st == 400 and body["error"] == "TOO-FEW"
    for i in range(1, 13):
        assert req(port, "PUT", f"{base}frame_{i:05d}.jpg", JPEG, token=tok, ctype="image/jpeg")[0] == 201
    assert len(list(studio.store.originals(pid).glob("frame_*.jpg"))) == 12
    assert not list(studio.store.originals(pid).glob(".*part"))
    # another paired phone cannot touch this project
    _, t2 = req(port, "POST", "/api/pair", {"code": pairing.info()["code"]})
    assert req(port, "PUT", base + "frame_00099.jpg", JPEG, token=t2["token"], ctype="image/jpeg")[0] == 403
    st, body = req(port, "POST", f"/api/session/{pid}/done", {"frames": [{"name": "frame_00001.jpg"}], "stats": {"kept": 12}}, token=tok)
    assert st == 202 and body["frames"] == 12
    time.sleep(0.2)
    assert calls == [pid]                                                  # check + Preview were handed off
    meta = json.loads((studio.store.path(pid) / "capture" / "phone_capture.json").read_text())
    assert meta["frames_on_disk"] == 12 and meta["client"]["stats"]["kept"] == 12
    st, body = req(port, "GET", f"/api/session/{pid}/status", token=tok)
    assert st == 200 and body["frames"] == 12


def recorder_like_mp4(path, seconds=6):
    """A fragmented MP4 without a duration in its header, as browser MediaRecorders write them."""
    import subprocess
    from namma.capture.media import ffmpeg_path
    exe = ffmpeg_path()
    if not exe:
        pytest.skip("FFmpeg not installed (python scripts/setup_tools.py --ffmpeg)")
    subprocess.run([exe, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=640x480:rate=30:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "frag_keyframe+empty_moov", str(path)], check=True)
    return path.read_bytes()


def test_video_pieces_are_joined_and_rewrapped(phone):
    port, pairing, studio, calls, tmp = phone
    data = recorder_like_mp4(tmp / "rec.mp4")
    tok = req(port, "POST", "/api/pair", {"code": pairing.info()["code"]})[1]["token"]
    pid = req(port, "POST", "/api/session", {"title": "Video"}, token=tok)[1]["project_id"]
    n = 4
    pieces = [data[i * len(data) // n:(i + 1) * len(data) // n] for i in range(n)]
    base = f"/api/session/{pid}/video/"
    assert req(port, "PUT", base + "x1?type=mp4", pieces[0], token=tok, ctype="video/mp4")[1]["error"] == "CHUNK-SEQ"
    assert req(port, "PUT", base + "0?type=avi", pieces[0], token=tok, ctype="video/mp4")[1]["error"] == "CHUNK-TYPE"
    for i in (0, 1, 3):                                      # piece 2 not (yet) sent
        assert req(port, "PUT", f"{base}{i}?type=mp4", pieces[i], token=tok, ctype="video/mp4")[0] == 201
    st, body = req(port, "POST", f"/api/session/{pid}/done", {}, token=tok)
    assert st == 409 and body["error"] == "CHUNKS-MISSING" and "2" in body["message"]
    assert req(port, "PUT", f"{base}2?type=mp4", pieces[2], token=tok, ctype="video/mp4")[0] == 201
    assert req(port, "PUT", f"{base}2?type=mp4", pieces[2], token=tok, ctype="video/mp4")[0] == 201   # retry: idempotent
    st, body = req(port, "POST", f"/api/session/{pid}/done", {"sensors": [[0, 0.1, 90, 0]], "stats": {"mode": "video"}}, token=tok)
    assert st == 202 and body["video"]["chunks"] == 4, body
    from namma.capture.media import probe_video
    info = probe_video(studio.store.originals(pid) / "phone_video.mp4")
    assert abs(info["duration_s"] - 6) < 0.5 and info["width"] == 640       # re-wrapped: duration now known
    meta = json.loads((studio.store.path(pid) / "capture" / "phone_capture.json").read_text())
    assert meta["mode"] == "video" and meta["client"]["sensors"] == [[0, 0.1, 90, 0]]
    assert not (studio.store.path(pid) / "capture" / "phone_chunks").exists()
    time.sleep(0.2)
    assert calls == [pid]


def test_desktop_studio_exposes_the_code_only_locally(tmp_path):
    from test_p13_studio import W, req as dreq, server   # noqa: F401
    import http.server as hs
    import threading
    from namma.studio.server import Handler, Studio
    Handler.studio = Studio(tmp_path / "p")
    port = free_port()
    httpd = hs.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        st, body = dreq(port, "GET", "/api/phone")
        assert st == 200 and body["enabled"] is False and "--lan" in body["hint"]
        Handler.studio.phone = pc.Pairing(["https://192.168.50.7:8443/"])
        st, body = dreq(port, "GET", "/api/phone")
        assert body["enabled"] and len(body["code"]) == 6
        assert dreq(port, "GET", "/api/phone", host="192.168.50.7")[0] == 403      # never served to the network
        assert body["link"] == f"https://192.168.50.7:8443/#c={body['code']}"        # code in the fragment only
        st, svg = dreq(port, "GET", "/api/phone/qr.svg")
        assert st == 200 and svg.startswith(b"<?xml") and b"<svg" in svg
        assert dreq(port, "GET", "/api/phone/qr.svg", host="192.168.50.7")[0] == 403
        Handler.studio.phone.code = "123456"
        assert dreq(port, "POST", "/api/phone/code", {}, W)[1]["code"] != "123456" or \
            dreq(port, "POST", "/api/phone/code", {}, W)[1]["code"] != "123456"        # 1-in-a-million repeat
    finally:
        httpd.shutdown()
