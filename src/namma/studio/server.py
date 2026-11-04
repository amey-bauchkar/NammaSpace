"""Namma Space Studio web server (local only).

    python -m namma.studio.server [--port 8780] [--projects projects] [--record test_output/phase13/bench]

Serves the web app (web/) and a JSON API for projects, uploads, capture checks, reconstruction
runs, POIs, scale calibration, routes and publishing. Binds to 127.0.0.1 only.
Protection for a local server: requests must carry Host 127.0.0.1/localhost (DNS-rebinding guard)
and state-changing requests must carry the header X-Namma-Studio: 1 (browsers cannot add it
cross-site without a CORS preflight, which this server never grants).
"""
import argparse
import http.server
import json
import mimetypes
import re
import secrets
import shutil
import sys
import time
import traceback
import urllib.parse
import zipfile
from pathlib import Path

import psutil

from namma import PROJECT_ROOT
from namma.capture.media import (
    HEIC_EXT,
    IMAGE_EXT,
    MAX_IMAGE_BYTES,
    MAX_VIDEO_BYTES,
    VIDEO_EXT,
    ffmpeg_path,
)
from namma.core import timefmt
from namma.core.jsonio import load_strict, write_json
from namma.recon import hardware
from namma.recon.tools import ToolMissing, find_tools
from namma.studio import routing
from namma.studio.jobs import JobError, JobManager
from namma.studio.polycam_bridge import BridgeError
from namma.studio.package import publish_static_site
from namma.studio.profiles import PROFILES
from namma.studio.projects import ProjectError, ProjectStore, safe_filename

WEB = PROJECT_ROOT / "web"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json; charset=utf-8", ".glb": "model/gltf-binary", ".png": "image/png",
         ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".log": "text/plain; charset=utf-8",
         ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8", ".zip": "application/zip"}
ALLOWED_PROJECT_FILES = [re.compile(r"^runs/run-[0-9-a-z]+/90_package/.+$"), re.compile(r"^capture/thumbs/[^/]+\.jpg$"),
                         re.compile(r"^runs/run-[0-9-a-z]+/logs/[^/]+\.(log|txt)$"),
                         re.compile(r"^published/run-[0-9-a-z]+/.+$")]
POI_FIELDS = {"name", "category", "aliases", "position", "view"}
VERSION = "13.0.0"


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def _finite_vec(v, n):
    return isinstance(v, list) and len(v) == n and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                       and x == x and abs(x) < 1e9 for x in v)


class Studio:
    phone = None                    # namma.studio.phone_capture.Pairing when started with --lan

    def __init__(self, projects_root, record_dir=None):
        self.store = ProjectStore(projects_root)
        self.jobs = JobManager(self.store)
        from namma.studio.polycam_bridge import PolycamStaging
        self.polycam = PolycamStaging(self.store.root)
        self.record_dir = Path(record_dir) if record_dir else None

    # ------------------------------------------------------------------ helpers
    def project_summary(self, doc):
        d = self.store.root / doc["project_id"]
        rep = d / "capture" / "capture_report.json"
        out = dict(doc)
        out["uploads"] = sum(1 for p in (d / "capture" / "originals").iterdir() if p.is_file()) \
            if (d / "capture" / "originals").exists() else 0
        out["capture_status"] = load_strict(rep)["status"] if rep.exists() else None
        runs = []
        for r in doc.get("runs", []):
            try:
                st = self.jobs.run_status(doc["project_id"], r["run_id"])
            except ProjectError:
                continue
            pkg = d / "runs" / r["run_id"] / "90_package" / "scene_package.json"
            runs.append({**r, "status": st.get("status"), "progress": st.get("progress"),
                         "error": st.get("error"), "has_package": pkg.exists()})
        out["runs"] = runs
        return out

    def health(self):
        tools = {}
        try:
            t = find_tools()
            tools = {"colmap": True, "openmvs": True, "paths": t.describe()}
        except ToolMissing as e:
            tools = {"colmap": False, "openmvs": False, "error": str(e)}
        vm = psutil.virtual_memory()
        gpu = hardware.detect_gpus()
        return {"ok": True, "version": VERSION, "tools": tools, "ffmpeg": bool(ffmpeg_path()),
                "ram_total_gb": round(vm.total / 2**30, 1), "ram_available_gb": round(vm.available / 2**30, 1),
                "disk_free_gb": round(shutil.disk_usage(self.store.root).free / 2**30, 1),
                "active_job": self.jobs.active(), "profiles": list(PROFILES),
                "hardware": {"gpu": gpu["gpus"][0]["name"] if gpu["available"] else None,
                             "gpu_reason": gpu["reason"], "gpu_steps": (tools.get("paths") or {}).get("gpu_steps", []),
                             "ram_budget_gb": hardware.resources().ram_budget_gb,
                             "projects_onedrive_synced": hardware.onedrive_synced(self.store.root)}}

    def recommendation(self, pid):
        """Profile advice for this capture on this machine (namma.studio.advisor); needs a finished capture check."""
        from PIL import Image

        from namma.studio.advisor import recommend
        d = self.store.path(pid)
        man = d / "capture" / "capture_manifest.json"
        if not man.exists():
            raise ApiError(409, "CAPTURE-NOT-CHECKED", "Check the capture first.")
        sizes = []
        for rel in load_strict(man).get("inputs", []):
            with Image.open(d / rel) as im:       # header only: no pixel decoding
                sizes.append(im.size)
        try:
            tools = find_tools()
        except ToolMissing:
            tools = None
        processed = any(r.get("has_package") for r in self.project_summary(self.store.get(pid))["runs"])
        return recommend(sizes, machine=hardware.describe_machine(self.store.root), tools=tools,
                         processed_before=processed)

    def pois_path(self, pid):
        return self.store.path(pid) / "pois.json"

    def list_pois(self, pid, rid):
        p = self.pois_path(pid)
        pois = load_strict(p) if p.exists() else []
        return [x for x in pois if x.get("run_id") in (None, rid)]

    def add_poi(self, pid, rid, body):
        self.store.run_dir(pid, rid)
        extra = set(body) - POI_FIELDS
        if extra:
            raise ApiError(400, "POI-FIELDS", f"unknown fields {sorted(extra)}")
        name = str(body.get("name", "")).strip()
        if not name or len(name) > 80:
            raise ApiError(400, "POI-NAME", "a name of 1-80 characters is required")
        if not _finite_vec(body.get("position"), 3):
            raise ApiError(400, "POI-POSITION", "position must be 3 finite numbers")
        view = body.get("view")
        if view is not None and not (isinstance(view, dict) and _finite_vec(view.get("position"), 3)
                                     and _finite_vec(view.get("target"), 3)):
            raise ApiError(400, "POI-VIEW", "view needs position and target (3 numbers each)")
        aliases = [str(a).strip()[:80] for a in (body.get("aliases") or []) if str(a).strip()][:10]
        rec = {"poi_id": re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "place",
               "name": name, "category": str(body.get("category", "")).strip()[:40], "aliases": aliases,
               "position": [float(v) for v in body["position"]], "created_at": timefmt.now_iso(),
               "origin": "human_authored", "run_id": rid}
        if view:
            rec["view"] = {"position": [float(v) for v in view["position"]], "target": [float(v) for v in view["target"]]}
        if not rec["category"]:
            rec.pop("category")
        p = self.pois_path(pid)
        pois = load_strict(p) if p.exists() else []
        ids = {x["poi_id"] for x in pois}
        base, k = rec["poi_id"], 2
        while rec["poi_id"] in ids:
            rec["poi_id"] = f"{base}-{k}"
            k += 1
        pois.append(rec)
        write_json(p, pois)
        return self.list_pois(pid, rid)

    def delete_poi(self, pid, rid, poi_id):
        p = self.pois_path(pid)
        pois = [x for x in (load_strict(p) if p.exists() else []) if not (x["poi_id"] == poi_id and x.get("run_id") in (None, rid))]
        write_json(p, pois)
        return self.list_pois(pid, rid)

    def calibrate(self, pid, rid, body):
        run_dir = self.store.run_dir(pid, rid)
        if not (_finite_vec(body.get("p1"), 3) and _finite_vec(body.get("p2"), 3)):
            raise ApiError(400, "CAL-POINTS", "p1 and p2 must be 3 numbers each")
        metres = body.get("metres")
        if not isinstance(metres, (int, float)) or not (0.05 <= metres <= 200):
            raise ApiError(400, "CAL-LENGTH", "the real length must be between 0.05 m and 200 m")
        d = sum((a - b) ** 2 for a, b in zip(body["p1"], body["p2"])) ** 0.5
        if d <= 1e-6:
            raise ApiError(400, "CAL-ZERO", "the two points are the same")
        st = self.jobs.run_status(pid, rid)
        if st.get("status") != "succeeded":
            raise ApiError(409, "CAL-RUN", "calibrate a finished run")
        cal = {"run_id": rid, "status": "user_distance", "metres_per_unit": metres / d, "relative_uncertainty": None,
               "method": f"user-entered length {metres} m between two picked surface points ({d:.5f} model units)",
               "p1": body["p1"], "p2": body["p2"], "created_at": timefmt.now_iso(), "evaluation_only": False}
        write_json(self.store.path(pid) / "calibration.json", cal)
        res = self.repackage(pid, rid)
        if res["status"] != "succeeded":
            raise ApiError(500, "CAL-REPACKAGE", f"re-packaging failed: {res.get('error')}")
        return {"calibration": cal}

    def repackage(self, pid, rid):
        """Rebuild a finished run's scene package (POIs, scale, splat). Photo runs go through run_pipeline (cheap
        stages only re-run); an IMPORTED run has no pipeline stages - run_pipeline refused it ("created with a
        different configuration"), so publishing and "Set scale" failed for every Polycam space - and is packaged
        directly."""
        from namma.studio.pipeline import RunContext, run_pipeline
        run_dir = self.store.run_dir(pid, rid)
        cfg = load_strict(run_dir / "config.json")["config"]      # the run's own (possibly older) configuration
        if cfg.get("profile") != "import":
            return run_pipeline(self.store.path(pid), rid, cfg)
        from namma.studio.package import build_scene_package
        ctx = RunContext(self.store.path(pid), rid, cfg)
        try:
            build_scene_package(ctx, ctx.dir("package"))
        except Exception as e:                                    # noqa: BLE001 - reported to the caller
            return {"status": "failed", "error": f"{e.__class__.__name__}: {e}"}
        return {"status": "succeeded"}

    def route(self, pid, rid, body):
        run_dir = self.store.run_dir(pid, rid)
        if not _finite_vec(body.get("start"), 2):
            raise ApiError(400, "ROUTE-START", "start must be [x, z]")
        poi = next((p for p in self.list_pois(pid, rid) if p["poi_id"] == body.get("poi_id")), None)
        if poi is None:
            raise ApiError(404, "ROUTE-POI", "unknown place label")
        cfg = load_strict(run_dir / "config.json")["config"]
        return routing.route(run_dir, body["start"], poi["position"], cfg["nav"], request_id=f"studio-{secrets.token_hex(3)}")

    def publish(self, pid, rid, lods=None):
        run_dir = self.store.run_dir(pid, rid)
        pkg = run_dir / "90_package"
        if not (pkg / "scene_package.json").exists():
            raise ApiError(409, "PUBLISH-NO-PACKAGE", "this run has no scene package yet")
        if self.jobs.run_status(pid, rid).get("status") != "succeeded":
            raise ApiError(409, "PUBLISH-RUN", "publish a finished run")
        res = self.repackage(pid, rid)                            # refresh POIs / scale into the package first
        if res["status"] != "succeeded":
            raise ApiError(500, "PUBLISH-REPACKAGE", f"re-packaging failed: {res.get('error')}")
        out = self.store.path(pid) / "published" / rid
        info = publish_static_site(pkg, out, include_lods=lods)
        zpath = out.parent / f"{rid}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(out.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(out).as_posix())
        info.update(zip=f"/files/{pid}/published/{rid}.zip", site=f"/files/{pid}/published/{rid}/index.html",
                    zip_bytes=zpath.stat().st_size)
        return info

    def run_report(self, pid, rid):
        run_dir = self.store.run_dir(pid, rid)
        out = {"status": self.jobs.run_status(pid, rid)}
        for key, rel in (("quality", "80_quality/quality_report.json"), ("sfm", "20_sfm/sfm_summary.json"),
                         ("frame", "70_frame/frame.json"), ("navigation", "85_navgeom/navgeom.json"),
                         ("config", "config.json")):
            p = run_dir / rel
            if p.exists():
                doc = load_strict(p)
                if key == "sfm":
                    doc.pop("keypoints", None)
                if key == "frame":
                    doc = {k: doc[k] for k in ("eye_height_units", "floor_detected", "up_source", "floor_coverage",
                                               "points_source", "points", "roam_stats", "start") if k in doc}
                out[key] = doc
        cmds = run_dir / "logs" / "commands.jsonl"
        if cmds.exists():
            out["commands"] = [{k: r[k] for k in ("step", "status", "wall_s", "peak_rss_mb")}
                               for r in map(json.loads, cmds.read_text(encoding="utf-8").splitlines())]
        return out


class Handler(http.server.BaseHTTPRequestHandler):
    studio: Studio = None
    server_version = "NammaStudio/" + VERSION
    protocol_version = "HTTP/1.1"
    timeout = 300                   # idle keep-alive connections are closed (each one holds a thread)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ------------------------------------------------------------------ plumbing
    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        if host not in ("127.0.0.1", "localhost", "[::1]"):
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed = urllib.parse.urlsplit(origin)
                orig_host = (parsed.hostname or "").lower()
                if orig_host not in ("127.0.0.1", "localhost", "::1"):
                    return False
            except Exception:
                return False
        return True

    def _send(self, status, body=b"", ctype="application/json; charset=utf-8", extra=None):
        # an unread request body would be read as the start of the next request on this keep-alive connection
        # (QA-04: "{}GET"): drain it for EVERY response, not only for refusals
        if not getattr(self, "_body_read", True):
            n = int(self.headers.get("Content-Length") or 0)
            if 0 < n <= 1_000_000:
                self.rfile.read(n)                   # small unread body: drain it so the client sees the reply
                self._body_read = True
            else:
                # large unread body (refused upload): close instead of misreading it as the next request
                self.close_connection = True
                extra = dict(extra or {}, Connection="close")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _error(self, status, code, message):
        self._json(status, {"error": code, "message": message})

    def _body_json(self, limit=1_000_000):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ApiError(413, "BODY-TOO-LARGE", "request body too large")
        raw = self.rfile.read(n) if n else b"{}"
        self._body_read = True
        try:
            v = json.loads(raw or b"{}")
        except ValueError:
            raise ApiError(400, "BAD-JSON", "request body is not valid JSON")
        if not isinstance(v, dict):
            raise ApiError(400, "BAD-JSON", "request body must be a JSON object")
        return v

    def _file(self, path, ctype=None):
        if not path.is_file():
            return self._error(404, "NOT-FOUND", "file not found")
        size = path.stat().st_size
        ctype = ctype or TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 1 << 20)

    def _dispatch(self, method):
        if not self._host_ok():
            return self._error(403, "HOST", "this server only answers on 127.0.0.1 / localhost")
        url = urllib.parse.urlsplit(self.path)
        self._body_read = int(self.headers.get("Content-Length") or 0) == 0
        # split BEFORE decoding so an encoded "/" inside a file name cannot create path segments
        self._segments = [urllib.parse.unquote(x) for x in url.path.split("/")]
        path = urllib.parse.unquote(url.path)
        query = urllib.parse.parse_qs(url.query)
        if method in ("POST", "PUT", "PATCH", "DELETE") and self.headers.get("X-Namma-Studio") != "1" and path != "/__bench":
            return self._error(403, "CSRF", "missing X-Namma-Studio header")
        try:
            if path.startswith("/api/"):
                return self._api(method, path, query)
            if path == "/__bench" and method == "POST":
                return self._bench()
            if method not in ("GET", "HEAD"):
                return self._error(405, "METHOD", "method not allowed")
            if path.startswith("/files/"):
                return self._project_file(path)
            return self._static(path)
        except ApiError as e:
            return self._error(e.status, e.code, str(e))
        except ProjectError as e:
            return self._error(e.status, e.code, str(e))
        except BridgeError as e:
            return self._error(e.status, e.code, str(e))
        except JobError as e:
            return self._error(e.status, e.code, str(e))
        except Exception as e:      # never leak a traceback to the page; keep it in the server log
            traceback.print_exc()
            return self._error(500, "INTERNAL", f"internal error: {e.__class__.__name__}")

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    # ------------------------------------------------------------------ static
    def _static(self, path):
        if path in ("/", ""):
            self.send_response(302)
            self.send_header("Location", "/studio.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        rel = path.lstrip("/")
        target = (WEB / rel).resolve()
        if not target.is_relative_to(WEB.resolve()) or "tests" in Path(rel).parts:
            return self._error(404, "NOT-FOUND", "not found")
        return self._file(target)

    def _project_file(self, path):
        parts = path.split("/", 3)          # '', 'files', pid, rest
        if len(parts) < 4:
            return self._error(404, "NOT-FOUND", "not found")
        pid, rest = parts[2], parts[3]
        # ".." would let an allowed prefix (runs/<run>/90_package/../../..) reach any file of the project
        if ".." in re.split(r"[\\/]", rest):
            return self._error(404, "NOT-FOUND", "not found")
        if not any(p.match(rest) for p in ALLOWED_PROJECT_FILES) and not re.match(r"^published/run-[0-9a-z-]+\.zip$", rest):
            return self._error(404, "NOT-FOUND", "not found")
        base = self.studio.store.path(pid).resolve()
        target = (base / rest).resolve()
        if not target.is_relative_to(base):
            return self._error(404, "NOT-FOUND", "not found")
        return self._file(target)

    def _bench(self):
        data = self._body_json()
        data["recorded_by_server_at"] = timefmt.now_iso()
        rec = self.studio.record_dir
        if rec is None:
            return self._send(204)
        rec.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(data.get("package_id", "x")))[:80]
        (rec / f"bench-{time.strftime('%Y%m%d-%H%M%S')}-{name}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
        return self._send(204)

    # ------------------------------------------------------------------ API
    def _upload(self, pid, name):
        fname = safe_filename(name)
        ext = Path(fname).suffix.lower()
        if ext not in IMAGE_EXT | VIDEO_EXT | HEIC_EXT and ext != ".zip":
            raise ApiError(415, "UPLOAD-TYPE", f"{fname}: only JPEG / PNG photos, MP4 / MOV video, or ZIP archive are accepted")
        n = int(self.headers.get("Content-Length") or -1)
        limit = MAX_VIDEO_BYTES if (ext in VIDEO_EXT or ext == ".zip") else MAX_IMAGE_BYTES
        if n <= 0:
            raise ApiError(411, "UPLOAD-LENGTH", "Content-Length is required")
        if n > limit:
            raise ApiError(413, "UPLOAD-TOO-LARGE", f"{fname}: {n / 2**20:.0f} MB exceeds the {limit / 2**20:.0f} MB limit")
        need = n + 2 * 2**30
        if shutil.disk_usage(self.studio.store.root).free < need:
            raise ApiError(507, "UPLOAD-DISK", "not enough free disk space")
        dst_dir = self.studio.store.originals(pid)
        tmp = dst_dir / f".{fname}.{secrets.token_hex(4)}.part"
        got = 0
        with open(tmp, "wb") as f:
            while got < n:
                chunk = self.rfile.read(min(1 << 20, n - got))
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
        self._body_read = got == n
        if got != n:
            tmp.unlink(missing_ok=True)
            raise ApiError(400, "UPLOAD-INCOMPLETE", f"{fname}: upload interrupted ({got} of {n} bytes)")
        final = dst_dir / fname
        tmp.replace(final)
        if ext == ".zip":
            import zipfile
            from namma.studio.polycam_bridge import IMAGE_EXTS
            extracted = 0
            total_uncompressed = 0
            try:
                with zipfile.ZipFile(final) as z:
                    valid_items = []
                    has_kf_imgs = any(i.filename.startswith("keyframes/images/") and Path(i.filename).suffix.lower() in IMAGE_EXTS
                                      for i in z.infolist())
                    for item in z.infolist():
                        if item.is_dir() or "__MACOSX" in item.filename:
                            continue
                        leaf = Path(item.filename).name
                        if leaf.startswith("."):
                            continue
                        if Path(leaf).suffix.lower() in IMAGE_EXTS:
                            if has_kf_imgs and not item.filename.startswith("keyframes/images/"):
                                continue
                            if not has_kf_imgs and any(bad in item.filename.lower() for bad in ("depth", "segmentation", "mask", "confidence")):
                                continue
                            if item.file_size > MAX_IMAGE_BYTES:
                                raise ApiError(413, "UPLOAD-TOO-LARGE",
                                               f"{leaf}: uncompressed size ({item.file_size / 2**20:.1f} MB) exceeds limit")
                            total_uncompressed += item.file_size
                            if total_uncompressed > limit:
                                raise ApiError(413, "UPLOAD-TOO-LARGE", f"{fname}: total uncompressed size exceeds limit")
                            valid_items.append(item)
                            if len(valid_items) > 1000:
                                raise ApiError(400, "UPLOAD-TOO-MANY-FILES", f"{fname}: archive contains more than 1000 images")
                    for item in valid_items:
                        leaf = Path(item.filename).name
                        target = dst_dir / safe_filename(leaf)
                        idx = 1
                        while target.exists():
                            target = dst_dir / f"{target.stem}_{idx}{target.suffix}"
                            idx += 1
                        target.write_bytes(z.read(item.filename))
                        extracted += 1
            finally:
                final.unlink(missing_ok=True)
            self.studio.store.clear_capture(pid)
            return {"file": fname, "bytes": n, "extracted_photos": extracted}
        self.studio.store.clear_capture(pid)
        return {"file": fname, "bytes": n}

    def _api(self, method, path, query):
        st = self.studio
        seg = [s for s in self._segments[2:] if s != ""]
        if seg == ["health"]:
            return self._json(200, st.health())
        if seg == ["phone"] and method == "GET":
            # served on 127.0.0.1 only: the pairing code must never be readable from the network
            if st.phone is None:
                return self._json(200, {"enabled": False, "hint": "Start the Studio with --lan to capture with a phone: "
                                                                  "python scripts/start_studio.py --lan"})
            return self._json(200, st.phone.info())
        # Polycam exports dropped on the Studio page: start -> files (streamed) -> import
        if seg == ["polycam"] and method == "POST":
            return self._json(201, {"import_id": st.polycam.start()})
        if len(seg) == 4 and seg[0] == "polycam" and seg[2] == "files" and method == "PUT":
            n = int(self.headers.get("Content-Length") or -1)
            res = st.polycam.save(seg[1], seg[3], self.rfile, n)
            self._body_read = True
            return self._json(201, res)
        if len(seg) == 3 and seg[0] == "polycam" and seg[2] == "import" and method == "POST":
            return self._json(201, st.polycam.finish(seg[1], title=self._body_json().get("title", "")))
        if seg == ["phone", "qr.svg"] and method == "GET" and st.phone is not None:
            import io

            import segno
            buf = io.BytesIO()
            segno.make(st.phone.info()["link"], error="m").save(buf, kind="svg", scale=6, border=2, dark="#0f1a2a")
            return self._send(200, buf.getvalue(), "image/svg+xml")
        if seg == ["phone", "code"] and method == "POST" and st.phone is not None:
            st.phone.new_code()
            return self._json(200, st.phone.info())
        if seg == ["bin"] and method == "GET":
            return self._json(200, {"items": st.store.list_trash()})
        if len(seg) == 3 and seg[0] == "bin" and seg[2] == "restore" and method == "POST":
            return self._json(200, {"project_id": st.store.restore(seg[1])})
        if len(seg) == 2 and seg[0] == "bin" and method == "DELETE":
            return self._json(200, {"deleted": st.store.purge(seg[1])})
        if seg == ["projects"]:
            if method == "GET":
                return self._json(200, {"projects": [st.project_summary(p) for p in st.store.list()]})
            if method == "POST":
                body = self._body_json()
                # optional source / licence of photos that are not the user's own: published with the model
                src = " ".join(str(body.get("source") or "").split())[:300]
                return self._json(201, st.store.create(str(body.get("title", "")), notices=[f"Photos: {src}"] if src else None))
        if len(seg) >= 2 and seg[0] == "projects":
            pid = seg[1]
            rest = seg[2:]
            if not rest:
                if method == "GET":
                    return self._json(200, st.project_summary(st.store.get(pid)))
                if method in ("PATCH", "POST"):
                    body = self._body_json()
                    new_title = (body.get("title") or "").strip()
                    if not new_title:
                        raise ApiError(400, "PROJECT-TITLE-EMPTY", "Title cannot be empty")
                    doc = st.store.rename(pid, new_title)
                    return self._json(200, st.project_summary(doc))
                if method == "DELETE":
                    act = st.jobs.active()
                    if act and act[0] == pid:
                        raise ApiError(409, "PROJECT-BUSY", "This space is being reconstructed; cancel the run first.")
                    return self._json(200, {"moved_to": st.store.trash(pid)})
            if rest == ["rename"] and method == "POST":
                body = self._body_json()
                new_title = (body.get("title") or "").strip()
                if not new_title:
                    raise ApiError(400, "PROJECT-TITLE-EMPTY", "Title cannot be empty")
                doc = st.store.rename(pid, new_title)
                return self._json(200, st.project_summary(doc))
            if rest == ["uploads"] and method == "GET":
                orig = st.store.originals(pid)
                files = sorted(p for p in orig.iterdir() if p.is_file() and not p.name.startswith(".")) if orig.exists() else []
                return self._json(200, {"files": [{"name": p.name, "bytes": p.stat().st_size} for p in files]})
            if len(rest) == 2 and rest[0] == "uploads":
                if method == "PUT":
                    return self._json(201, self._upload(pid, rest[1]))
                if method == "DELETE":
                    st.store.remove_upload(pid, rest[1])
                    return self._json(200, {"removed": rest[1]})
            if rest == ["check"]:
                if method == "POST":
                    def_mf = hardware.resources().max_images
                    mf = self._body_json().get("max_frames", def_mf)
                    cap_mf = max(2000, def_mf)
                    if not (isinstance(mf, int) and not isinstance(mf, bool) and 10 <= mf <= cap_mf):
                        raise ApiError(400, "CHECK-MAX-FRAMES", f"max_frames must be a whole number from 10 to {cap_mf}")
                    return self._json(202, st.jobs.start_ingest(pid, mf))
                if method == "GET":
                    s = st.jobs.ingest_status(pid)
                    man = st.store.path(pid) / "capture" / "capture_manifest.json"
                    if s.get("status") == "done" and man.exists():
                        m = load_strict(man)
                        s["inputs"] = [{"name": x["name"], "flags": x["flags"], "thumb": f"/files/{pid}/capture/thumbs/"
                                        + Path(x["name"]).stem + ".jpg"} for x in m.get("per_input", [])]
                    return self._json(200, s)
            if rest == ["recommendation"] and method == "GET":
                return self._json(200, st.recommendation(pid))
            if rest == ["runs"] and method == "POST":
                body = self._body_json()
                profile = body.get("profile", "standard")
                if profile not in PROFILES:
                    raise ApiError(400, "PROFILE", f"profile must be one of {list(PROFILES)}")
                return self._json(202, st.jobs.start_run(pid, profile))
            if len(rest) >= 2 and rest[0] == "runs":
                rid = rest[1]
                sub = rest[2:]
                if not sub and method == "GET":
                    return self._json(200, st.jobs.run_status(pid, rid))
                if sub == ["cancel"] and method == "POST":
                    return self._json(200, st.jobs.cancel_run(pid, rid))
                if sub == ["resume"] and method == "POST":
                    return self._json(202, st.jobs.resume_run(pid, rid))
                if sub == ["log"] and method == "GET":
                    return self._json(200, st.jobs.tail_log(pid, rid))
                if sub == ["report"] and method == "GET":
                    return self._json(200, st.run_report(pid, rid))
                if sub == ["splat"]:
                    if method == "GET":
                        return self._json(200, st.jobs.splat_status(pid, rid))
                    if method == "POST":
                        return self._json(202, st.jobs.start_splat(pid, rid))
                if sub == ["splat", "cancel"] and method == "POST":
                    return self._json(200, st.jobs.cancel_splat(pid, rid))
                if sub == ["pois"]:
                    if method == "GET":
                        return self._json(200, {"pois": st.list_pois(pid, rid)})
                    if method == "POST":
                        return self._json(201, {"pois": st.add_poi(pid, rid, self._body_json())})
                if len(sub) == 2 and sub[0] == "pois" and method == "DELETE":
                    return self._json(200, {"pois": st.delete_poi(pid, rid, sub[1])})
                if sub == ["calibration"] and method == "POST":
                    return self._json(200, st.calibrate(pid, rid, self._body_json()))
                if sub == ["route"] and method == "POST":
                    return self._json(200, st.route(pid, rid, self._body_json()))
                if sub == ["publish"] and method == "POST":
                    body = self._body_json()
                    lods = body.get("lods")
                    if lods is not None and not (isinstance(lods, list) and all(isinstance(x, int) for x in lods)):
                        raise ApiError(400, "PUBLISH-LODS", "lods must be a list of integers")
                    return self._json(200, st.publish(pid, rid, lods))
        raise ApiError(404, "NOT-FOUND", f"no API route {method} {path}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8780)
    ap.add_argument("--projects", default=str(PROJECT_ROOT / "projects"))
    ap.add_argument("--record", help="directory where POST /__bench results are saved")
    ap.add_argument("--lan", dest="lan", action="store_true", default=True,
                    help="also serve the phone auto-capture page over HTTPS on this computer's network addresses (default: enabled)")
    ap.add_argument("--no-lan", dest="lan", action="store_false",
                    help="disable the phone auto-capture page over HTTPS")
    ap.add_argument("--lan-port", type=int, default=8443)
    a = ap.parse_args(argv)
    Handler.studio = Studio(a.projects, (PROJECT_ROOT / a.record) if a.record else None)
    if a.lan:
        try:
            from namma.studio.phone_capture import start_phone_server
            Handler.studio.phone, _ = start_phone_server(Handler.studio, port=a.lan_port)
            info = Handler.studio.phone.info()
            print(f"Phone capture: open {' or '.join(info['urls']) or '(no network address found)'} on the phone "
                  f"(same Wi-Fi / hotspot); the pairing code is shown in the Studio. Accept the one-time certificate "
                  f"warning; allow Python through the Windows firewall on private networks if asked.", flush=True)
        except Exception as e:
            print(f"Warning: Could not start phone capture HTTPS server on port {a.lan_port}: {e}", flush=True)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    httpd.daemon_threads = True
    print(f"Namma Space Studio at http://127.0.0.1:{a.port}/  (projects: {Handler.studio.store.root})", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
