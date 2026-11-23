"""Load a static export with external HTTP(S) routed to a closed local proxy port.

Only localhost bypasses the proxy. Does not alter the user's network settings.
"""
import argparse
import functools
import http.server
import json
import socket
import threading
from pathlib import Path

from cdp import Chrome


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      '.js': 'text/javascript', '.mjs': 'text/javascript'}

    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('site', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(args.site.resolve())))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # Reserve a non-listening port so no unrelated proxy can answer there.
        with socket.socket() as closed_proxy:
            closed_proxy.bind(('127.0.0.1', 0))
            port = closed_proxy.getsockname()[1]
            with Chrome(extra_args=[f'--proxy-server=http://127.0.0.1:{port}',
                                    '--proxy-bypass-list=127.0.0.1;localhost']) as c:
                base = f'http://127.0.0.1:{server.server_port}/viewer.html?package=data/scene_package.json'
                c.navigate(base + '&mesh')
                c.wait_for("window.__nammaScene && window.__nammaScene.phase === 'ready'", timeout=240)
                mesh_ok = c.eval('window.__nammaScene.triangles > 0')
                external_blocked = c.eval("fetch('https://example.com/', {mode: 'no-cors', signal: AbortSignal.timeout(10000)}).then(() => false).catch(() => true)")
                has_splat = c.eval('Boolean(window.__nammaScene.ctx.data.splat)')
                splat_ok = None
                if has_splat:
                    c.navigate(base)
                    c.wait_for('Boolean(window.__nammaScene && window.__nammaScene.splat && window.__nammaScene.splat.shown)', timeout=240)
                    splat_ok = True
                c.screenshot(args.out / 'offline_loaded.png')
                result = {'mesh_loaded': mesh_ok, 'splat_available': has_splat, 'splat_loaded': splat_ok,
                          'external_fetch_failed': external_blocked, 'exceptions': c.exceptions,
                          'network_mode': 'isolated browser proxy; localhost bypass only',
                          'passed': bool(mesh_ok and external_blocked and (not has_splat or splat_ok) and not c.exceptions)}
                (args.out / 'offline_smoke.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
                print(json.dumps(result))
                return 0 if result['passed'] else 1
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    raise SystemExit(main())
