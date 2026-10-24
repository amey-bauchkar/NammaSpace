"""Phone capture: a second, HTTPS-only listener for the auto-capture web app (web/capture.html).

    python scripts/start_studio.py --lan            # desktop Studio stays on http://127.0.0.1:8780 only

Why HTTPS: phone browsers only give a page camera access in a secure context. The certificate is self-signed and
made on this computer (config/lan_tls/, git-ignored); the phone shows a one-time "not private" warning.

What the phone side may do (nothing else is served on the network):
  GET  /, /capture.html, the page's own JS / CSS
  POST /api/pair                      {"code": "123456"} -> {"token"}     (code shown in the desktop Studio)
  POST /api/session                   {"title"} -> {"project_id"}         (needs the token)
  PUT  /api/session/<pid>/video/<seq>?type=mp4|webm  a 2-s piece of the phone's video recording (default mode:
                                      the phone only records; frame choice happens here, from the video)
  PUT  /api/session/<pid>/frames/<frame_00001.jpg>   JPEG bytes          (fallback "photo frames" mode)
  POST /api/session/<pid>/done        {"frames": [...], "device": {...}} -> capture check, then Preview
  GET  /api/session/<pid>/status
Safety: a 6-digit pairing code that expires after 10 minutes and is replaced after 8 wrong guesses, after which pairing
pauses (1 min, doubling up to 1 h; a new code in the Studio ends the pause) - without the pause a LAN neighbour could
keep guessing at full speed; random session
tokens (Authorization: Bearer) bound to the project they created; Host must be one of this computer's addresses;
frames must be JPEG, <= 25 MB, at most 400 per session; free-disk check before every frame.
"""
import datetime
import http.server
import ipaddress
import json
import re
import secrets
import shutil
import socket
import ssl
import threading
import time
import traceback
import urllib.parse
from pathlib import Path

from namma import PROJECT_ROOT
from namma.core import timefmt
from namma.core.jsonio import load_strict, write_json
from namma.studio.jobs import JobError
from namma.studio.polycam_bridge import BridgeError
from namma.studio.projects import ProjectError

CERT_DIR = PROJECT_ROOT / "config" / "lan_tls"
WEB = PROJECT_ROOT / "web"
STATIC = {"/capture.html": "capture.html", "/js/capture.js": "js/capture.js",
          "/js/captureLogic.js": "js/captureLogic.js", "/app.css": "app.css", "/favicon.svg": "favicon.svg"}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml"}
FRAME_NAME = re.compile(r"^frame_\d{5}\.jpg$")
MAX_FRAME_BYTES = 25 * 2**20
MAX_FRAMES = 400
MAX_CHUNK_BYTES = 64 * 2**20
SEQ = re.compile(r"^\d{1,5}$")
CODE_TTL_S = 600
TOKEN_TTL_S = 4 * 3600
MAX_WRONG_CODES = 8
LOCKOUT_S = 60                  # after MAX_WRONG_CODES wrong codes pairing pauses; doubles each time, up to an hour
MAX_LOCKOUT_S = 3600
CONN_TIMEOUT_S = 60             # idle connection (incl. a TLS handshake that never comes) is dropped


# ------------------------------------------------------------------------------------------- network + TLS
def lan_ips():
    """IPv4 addresses of this computer (no loopback / link-local). The address of the interface that carries the
    default route comes first: that is the Wi-Fi / hotspot the phone shares; others are often virtual adapters."""
    primary, ips = None, set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))            # no packet is sent: only picks the outgoing interface
        primary = s.getsockname()[0]
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ok = lambda ip: ip and not ip.startswith(("127.", "169.254.", "0."))   # noqa: E731
    rest = sorted(ip for ip in ips if ok(ip) and ip != primary)
    return ([primary] if ok(primary) else []) + rest


def ensure_cert(ips, cert_dir=CERT_DIR):
    """Self-signed certificate for these IPs (+ localhost); regenerated when the IPs change or it nears expiry."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    cert_dir = Path(cert_dir)
    cert_dir.mkdir(parents=True, exist_ok=True)
    crt, key, meta = cert_dir / "cert.pem", cert_dir / "key.pem", cert_dir / "meta.json"
    want = sorted(set(ips) | {"127.0.0.1"})
    if crt.exists() and key.exists() and meta.exists():
        m = json.loads(meta.read_text(encoding="utf-8"))
        if m.get("ips") == want and m.get("not_after", 0) > time.time() + 7 * 86400:
            return crt, key
    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Namma Space Studio (local)")])
    now = datetime.datetime.now(datetime.timezone.utc)
    san = [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in want]
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(k, hashes.SHA256()))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    meta.write_text(json.dumps({"ips": want, "not_after": (now + datetime.timedelta(days=365)).timestamp()}),
                    encoding="utf-8")
    return crt, key


# ------------------------------------------------------------------------------------------- pairing
class Pairing:
    """Pairing code + session tokens, shared by the desktop Studio (shows the code) and the phone listener."""

    def __init__(self, urls=()):
        self.lock = threading.Lock()
        self.urls = list(urls)
        self.tokens = {}            # token -> {"created", "project_id"}
        self.new_code()

    def new_code(self):
        with self.lock:
            self.code = f"{secrets.randbelow(10**6):06d}"
            self.code_expires = time.time() + CODE_TTL_S
            self.wrong = 0
            self.locked_until, self.lockouts = 0.0, 0         # the person at the computer asked for a fresh code
        return self.code

    def info(self):
        with self.lock:
            if time.time() > self.code_expires:
                self.code, self.code_expires, self.wrong = f"{secrets.randbelow(10**6):06d}", time.time() + CODE_TTL_S, 0
            # the QR / link carries the code in the URL FRAGMENT: browsers never send it to the server or log it
            return {"enabled": True, "urls": self.urls, "code": self.code,
                    "link": f"{self.urls[0]}#c={self.code}" if self.urls else None,
                    "expires_in_s": int(self.code_expires - time.time()), "sessions": len(self.tokens)}

    def pair(self, code):
        with self.lock:
            now = time.time()
            if now < self.locked_until:
                raise PhoneError(429, "PAIR-PAUSED", f"Too many wrong codes; try again in {int(self.locked_until - now) + 1} s "
                                                     "or show a new code in the Studio.")
            if time.time() > self.code_expires:
                raise PhoneError(403, "CODE-EXPIRED", "The pairing code expired; read the new one in the Studio.")
            if not secrets.compare_digest(str(code), self.code):
                self.wrong += 1
                if self.wrong >= MAX_WRONG_CODES:          # too many guesses: the code is replaced and pairing pauses
                    self.code, self.code_expires, self.wrong = f"{secrets.randbelow(10**6):06d}", time.time() + CODE_TTL_S, 0
                    self.locked_until = now + min(MAX_LOCKOUT_S, LOCKOUT_S * 2 ** self.lockouts)
                    self.lockouts += 1
                raise PhoneError(403, "CODE-WRONG", "Wrong pairing code.")
            token = secrets.token_urlsafe(32)
            self.tokens = {t: s for t, s in self.tokens.items() if now - s["created"] <= TOKEN_TTL_S}   # drop expired
            self.tokens[token] = {"created": now, "project_id": None}
            self.lockouts = 0
            self.code, self.code_expires, self.wrong = f"{secrets.randbelow(10**6):06d}", time.time() + CODE_TTL_S, 0
            return token

    def session(self, token):
        with self.lock:
            s = self.tokens.get(token or "")
            if not s or time.time() - s["created"] > TOKEN_TTL_S:
                raise PhoneError(401, "UNPAIRED", "Pair this phone again (enter the code shown in the Studio).")
            return s


class PhoneError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


# ------------------------------------------------------------------------------------------- auto pipeline
def auto_process(studio, pid, poll_s=2.0, timeout_s=3600):
    """Capture check, then (if ready) a Preview run. Progress in capture/phone_auto.json for the phone to show."""
    d = studio.store.path(pid)
    st_path = d / "capture" / "phone_auto.json"

    def state(**kw):
        write_json(st_path, {"updated_at": timefmt.now_iso(), **kw})
    try:
        state(stage="check", message="Checking the capture")
        studio.jobs.start_ingest(pid, max_frames=MAX_FRAMES)
        t0 = time.time()
        while True:
            s = studio.jobs.ingest_status(pid)
            if s.get("status") in ("done", "error"):
                break
            if time.time() - t0 > timeout_s:
                return state(stage="error", message="The capture check took too long.")
            time.sleep(poll_s)
        if s["status"] == "error":
            return state(stage="error", message=f"The capture check failed: {s.get('error')}")
        rep = s.get("report") or {}
        if rep.get("status") != "ready":
            errs = [e["message"] for e in rep.get("errors", [])][:3]
            return state(stage="blocked", message="The capture has blocking problems: " + " ".join(errs))
        # one reconstruction at a time (RAM): if another is running, wait for it instead of giving up
        while True:
            try:
                run = studio.jobs.start_run(pid, "preview")
                break
            except JobError as e:
                if e.code != "JOB-BUSY" or time.time() - t0 > 6 * 3600:
                    raise
                state(stage="queued", message=f"Waiting for the running reconstruction to finish ({e})")
                time.sleep(max(poll_s, 15.0))
        state(stage="reconstruct", message="Building the Preview model", run_id=run["run_id"])
    except JobError as e:
        state(stage="error", message=str(e))
    except Exception as e:                                   # noqa: BLE001 - shown to the user, kept in the log
        traceback.print_exc()
        state(stage="error", message=f"{e.__class__.__name__}: {e}")


# ------------------------------------------------------------------------------------------- handler
class PhoneHandler(http.server.BaseHTTPRequestHandler):
    studio = None
    pairing: Pairing = None
    allowed_hosts = set()
    server_version = "NammaPhoneCapture/1.0"
    protocol_version = "HTTP/1.1"
    timeout = CONN_TIMEOUT_S

    def setup(self):
        # the TLS handshake happens here, in this connection's own thread and under its timeout - not in the
        # listener's accept(), where one client that never sends a TLS hello stalled pairing for everyone
        self.request.settimeout(self.timeout)
        try:
            self.request.do_handshake()
            self._tls_ok = True
        except (ssl.SSLError, OSError):             # not TLS, or silent until the timeout: drop it quietly
            self._tls_ok = False
        super().setup()

    def handle(self):
        if self._tls_ok:
            super().handle()

    def log_message(self, fmt, *args):
        print("[phone] %s %s" % (time.strftime("%H:%M:%S"), fmt % args), flush=True)

    def _send(self, status, body=b"", ctype="application/json; charset=utf-8"):
        if not getattr(self, "_body_read", True):
            # a refused upload: read and discard its body (up to one chunk) so the phone receives this answer instead
            # of a broken connection, which it would retry forever; anything larger is cut off
            n = int(self.headers.get("Content-Length") or 0)
            if 0 < n <= MAX_CHUNK_BYTES:
                left = n
                while left > 0:
                    got = self.rfile.read(min(1 << 20, left))
                    if not got:
                        break
                    left -= len(got)
                self._body_read = left == 0
        if not getattr(self, "_body_read", True):
            self.close_connection = True                     # never parse an unread body as the next request
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if not getattr(self, "_body_read", True):
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _body_json(self, limit=200_000):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise PhoneError(413, "BODY-TOO-LARGE", "request body too large")
        raw = self.rfile.read(n) if n else b"{}"
        self._body_read = True
        try:
            v = json.loads(raw or b"{}")
        except ValueError:
            raise PhoneError(400, "BAD-JSON", "request body is not valid JSON")
        if not isinstance(v, dict):
            raise PhoneError(400, "BAD-JSON", "request body must be a JSON object")
        return v

    def _token(self):
        h = self.headers.get("Authorization") or ""
        return h[7:] if h.startswith("Bearer ") else None

    def _owned(self, pid):
        s = self.pairing.session(self._token())
        if s["project_id"] != pid:
            raise PhoneError(403, "NOT-YOUR-SESSION", "This phone did not start that capture.")
        return s

    def _dispatch(self, method):
        self._body_read = int(self.headers.get("Content-Length") or 0) == 0
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host not in self.allowed_hosts:
            return self._json(403, {"error": "HOST", "message": "unexpected Host"})
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        try:
            if path.startswith("/api/"):
                return self._api(method, [urllib.parse.unquote(x) for x in url.path.split("/")[2:]])
            if method not in ("GET", "HEAD"):
                return self._json(405, {"error": "METHOD", "message": "method not allowed"})
            if path in ("", "/"):
                path = "/capture.html"
            rel = STATIC.get(path)
            if rel is None:
                return self._json(404, {"error": "NOT-FOUND", "message": "not found"})
            f = WEB / rel
            return self._send(200, f.read_bytes(), TYPES[f.suffix])
        except PhoneError as e:
            return self._json(e.status, {"error": e.code, "message": str(e)})
        except (ProjectError, JobError, BridgeError) as e:
            return self._json(e.status, {"error": e.code, "message": str(e)})
        except Exception as e:                               # noqa: BLE001
            traceback.print_exc()
            return self._json(500, {"error": "INTERNAL", "message": f"internal error: {e.__class__.__name__}"})

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def _api(self, method, seg):
        st = self.studio
        if seg == ["pair"] and method == "POST":
            return self._json(200, {"token": self.pairing.pair(self._body_json().get("code", ""))})
        # Polycam export uploaded from the phone (Files app): start -> files -> import; only by the phone that started it
        if seg == ["polycam"] and method == "POST":
            s = self.pairing.session(self._token())
            iid = st.polycam.start()
            s.setdefault("imports", set()).add(iid)
            return self._json(201, {"import_id": iid})
        if len(seg) >= 3 and seg[0] == "polycam":
            s = self.pairing.session(self._token())
            if seg[1] not in s.get("imports", set()):
                raise PhoneError(403, "NOT-YOUR-IMPORT", "This phone did not start that import.")
            if len(seg) == 4 and seg[2] == "files" and method == "PUT":
                n = int(self.headers.get("Content-Length") or -1)
                res = st.polycam.save(seg[1], seg[3], self.rfile, n)
                self._body_read = True
                return self._json(201, res)
            if seg[2:] == ["import"] and method == "POST":
                res = st.polycam.finish(seg[1], title=self._body_json().get("title", ""))
                s["imports"].discard(seg[1])
                return self._json(201, res)
        if seg == ["session"] and method == "POST":
            s = self.pairing.session(self._token())
            if s["project_id"]:
                return self._json(200, {"project_id": s["project_id"]})
            title = " ".join(str(self._body_json().get("title") or "").split())[:80] or \
                f"Phone capture {time.strftime('%Y-%m-%d %H:%M')}"
            doc = st.store.create(title, notices=["Captured with the Namma Space phone auto-capture page"])
            s["project_id"] = doc["project_id"]
            return self._json(201, {"project_id": doc["project_id"]})
        if len(seg) >= 3 and seg[0] == "session":
            pid = seg[1]
            self._owned(pid)
            if len(seg) == 4 and seg[2] == "frames" and method == "PUT":
                return self._json(201, self._frame(pid, seg[3]))
            if len(seg) == 4 and seg[2] == "video" and method == "PUT":
                return self._json(201, self._chunk(pid, seg[3]))
            if seg[2:] == ["done"] and method == "POST":
                return self._json(202, self._done(pid))
            if seg[2:] == ["status"] and method == "GET":
                return self._json(200, self._status(pid))
        return self._json(404, {"error": "NOT-FOUND", "message": "unknown endpoint"})

    def _frame(self, pid, name):
        if not FRAME_NAME.match(name):
            raise PhoneError(400, "FRAME-NAME", "frames must be named frame_00001.jpg")
        n = int(self.headers.get("Content-Length") or -1)
        if not 0 < n <= MAX_FRAME_BYTES:
            raise PhoneError(413, "FRAME-SIZE", f"frame must be 1 byte to {MAX_FRAME_BYTES // 2**20} MB")
        dst_dir = self.studio.store.originals(pid)
        if sum(1 for _ in dst_dir.glob("frame_*.jpg")) >= MAX_FRAMES and not (dst_dir / name).exists():
            raise PhoneError(409, "FRAMES-FULL", f"at most {MAX_FRAMES} frames per capture")
        if shutil.disk_usage(dst_dir).free < n + 2 * 2**30:
            raise PhoneError(507, "DISK", "not enough free disk space on the computer")
        data = self.rfile.read(n)
        self._body_read = True
        if len(data) != n or data[:3] != b"\xff\xd8\xff":
            raise PhoneError(400, "FRAME-NOT-JPEG", "incomplete upload or not a JPEG")
        tmp = dst_dir / f".{name}.{secrets.token_hex(4)}.part"
        tmp.write_bytes(data)
        tmp.replace(dst_dir / name)                          # a retried upload of the same frame overwrites it
        return {"frame": name, "bytes": n}

    def _chunk(self, pid, seq):
        if not SEQ.match(seq):
            raise PhoneError(400, "CHUNK-SEQ", "chunk number must be 0-99999")
        kind = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("type", ["mp4"])[0]
        if kind not in ("mp4", "webm"):
            raise PhoneError(400, "CHUNK-TYPE", "video type must be mp4 or webm")
        n = int(self.headers.get("Content-Length") or -1)
        if not 0 < n <= MAX_CHUNK_BYTES:
            raise PhoneError(413, "CHUNK-SIZE", f"chunk must be 1 byte to {MAX_CHUNK_BYTES // 2**20} MB")
        from namma.capture.media import MAX_VIDEO_BYTES
        cdir = self.studio.store.path(pid) / "capture" / "phone_chunks"
        cdir.mkdir(parents=True, exist_ok=True)
        if sum(f.stat().st_size for f in cdir.glob("*.part")) + n > MAX_VIDEO_BYTES:
            raise PhoneError(413, "VIDEO-TOO-LARGE", "the recording exceeds the video size limit")
        if shutil.disk_usage(cdir).free < n + 2 * 2**30:
            raise PhoneError(507, "DISK", "not enough free disk space on the computer")
        data = self.rfile.read(n)
        self._body_read = True
        if len(data) != n:
            raise PhoneError(400, "CHUNK-INCOMPLETE", "incomplete upload")
        tmp = cdir / f".{int(seq):05d}.{secrets.token_hex(4)}.tmp"
        tmp.write_bytes(data)
        tmp.replace(cdir / f"{int(seq):05d}.{kind}.part")     # a retried chunk overwrites itself
        return {"chunk": int(seq), "bytes": n}

    def _assemble_video(self, pid):
        """Join the chunks in order into capture/originals/phone_video.<ext>, then re-wrap it losslessly
        (ffmpeg -c copy): recorder output is a stream without a duration in its header."""
        import subprocess
        from namma.capture.media import ffmpeg_path
        cdir = self.studio.store.path(pid) / "capture" / "phone_chunks"
        parts = sorted(cdir.glob("*.part"))
        if not parts:
            return None
        seqs = [int(p.name.split(".")[0]) for p in parts]
        if seqs != list(range(len(seqs))):
            missing = sorted(set(range(max(seqs) + 1)) - set(seqs))[:10]
            raise PhoneError(409, "CHUNKS-MISSING", f"video pieces missing: {missing}; the phone is still sending")
        ext = parts[0].name.split(".")[1]
        raw = cdir / f"recording.{ext}"
        with open(raw, "wb") as out:
            for p in parts:
                out.write(p.read_bytes())
        exe = ffmpeg_path()
        if not exe:
            raise PhoneError(500, "NO-FFMPEG", "FFmpeg is needed for video capture: python scripts/setup_tools.py --ffmpeg")
        dst = self.studio.store.originals(pid) / f"phone_video.{ext}"
        r = subprocess.run([exe, "-y", "-v", "error", "-fflags", "+genpts", "-i", str(raw), "-map", "0:v:0", "-c", "copy",
                            *(["-movflags", "+faststart"] if ext == "mp4" else []), str(dst)],
                           capture_output=True, text=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0 or not dst.exists():
            raise PhoneError(500, "VIDEO-REWRAP", f"could not read the recording: {r.stderr.strip()[-300:]}")
        shutil.rmtree(cdir)                                   # the re-wrapped video is the kept original
        return {"video": dst.name, "bytes": dst.stat().st_size, "chunks": len(parts)}

    def _done(self, pid):
        body = self._body_json(limit=4_000_000)
        d = self.studio.store.path(pid)
        video = self._assemble_video(pid)
        n = sum(1 for _ in self.studio.store.originals(pid).glob("frame_*.jpg"))
        write_json(d / "capture" / "phone_capture.json", {"received_at": timefmt.now_iso(), "frames_on_disk": n,
                                                          "video": video, "mode": "video" if video else "frames",
                                                          "client": {k: body.get(k) for k in ("frames", "device", "stats",
                                                                                              "sensors")}})
        if not video and n < 10:
            raise PhoneError(400, "TOO-FEW", f"only {n} frames arrived; at least 10 are needed")
        threading.Thread(target=auto_process, args=(self.studio, pid), daemon=True).start()
        return {"frames": n, "video": video, "next": "check, then Preview"}

    def _status(self, pid):
        d = self.studio.store.path(pid)
        auto_p = d / "capture" / "phone_auto.json"
        out = {"project_id": pid, "frames": sum(1 for _ in self.studio.store.originals(pid).glob("frame_*.jpg")),
               "auto": load_strict(auto_p) if auto_p.exists() else None}
        if out["auto"] and out["auto"].get("stage") == "check":
            s = self.studio.jobs.ingest_status(pid)
            out["check"] = {k: s.get(k) for k in ("status", "label", "fraction")}
        if out["auto"] and out["auto"].get("run_id"):
            r = self.studio.jobs.run_status(pid, out["auto"]["run_id"])
            out["run"] = {"status": r.get("status"), "progress": r.get("progress"),
                          "current": (r.get("current") or {}).get("label"), "error": r.get("error")}
        return out


def start_phone_server(studio, port=8443, bind="0.0.0.0", cert_dir=CERT_DIR):
    """Start the HTTPS phone listener in a daemon thread. -> (Pairing, server)."""
    ips = lan_ips()
    crt, key = ensure_cert(ips, cert_dir)
    urls = [f"https://{ip}:{port}/" for ip in ips]
    pairing = Pairing(urls)
    handler = type("BoundPhoneHandler", (PhoneHandler,), {"studio": studio, "pairing": pairing,
                                                           "allowed_hosts": set(ips) | {"127.0.0.1", "localhost"}})
    httpd = http.server.ThreadingHTTPServer((bind, port), handler)
    httpd.daemon_threads = True
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(crt, key)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return pairing, httpd
