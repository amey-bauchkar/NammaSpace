"""Serve a built site locally (127.0.0.1 only) with explicit MIME types.

    python scripts/serve.py --dir public --port 8765

Windows registries sometimes map .js to text/plain, which breaks ES modules;
the MIME map below is explicit. Caching is disabled so rebuilds show at once.
"""
import argparse
import functools
import http.server
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json; charset=utf-8", ".glb": "model/gltf-binary",
         ".md": "text/markdown; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
         ".jpg": "image/jpeg", ".zip": "application/zip", "": "application/octet-stream"}


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = TYPES
    record_dir = None  # set by --record: POST /__bench saves benchmark JSON there

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        if self.path != "/__bench" or self.record_dir is None:
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0 or n > 1_000_000:
            self.send_error(400)
            return
        body = self.rfile.read(n)
        try:
            data = json.loads(body)
        except ValueError:
            self.send_error(400)
            return
        data["recorded_by_server_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        data["served_dir"] = self.directory
        out = Path(self.record_dir) / f"bench-{time.strftime('%Y%m%d-%H%M%S')}-{data.get('release_id', 'x')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=1), encoding="utf-8")
        self.send_response(204)
        self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="public")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--record", help="directory where POST /__bench benchmark results are saved")
    a = ap.parse_args()
    if a.record:
        Handler.record_dir = str((ROOT / a.record).resolve())
    directory = (ROOT / a.dir).resolve()
    if not (directory / "index.html").is_file():
        raise SystemExit(f"{directory} has no index.html - run scripts/build.py (and scripts/release.py) first")
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), functools.partial(Handler, directory=str(directory)))
    print(f"serving {directory} at http://127.0.0.1:{a.port}/  (Ctrl+C to stop)", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
