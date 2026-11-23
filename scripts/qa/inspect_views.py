"""Screenshots of a scene package from several photo viewpoints + an overview from above (visual inspection).

    python scripts/qa/inspect_views.py --url "<viewer url>" --out <dir> [--views 0,30,60,90,120]
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "browser"))
from cdp import Chrome  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--views", default="0,30,60,90,120")
    ap.add_argument("--camera-names", type=Path, help="JSON array of capture filenames; stable across camera reorderings")
    ap.add_argument("--representation", choices=("mesh", "splat"), default="mesh")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    parts = urlsplit(a.url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != 'mesh']
    if a.representation == 'mesh':
        query.append(('mesh', ''))
    url = urlunsplit(parts._replace(query=urlencode(query)))
    with Chrome(headless=True) as c:
        c.navigate(url, wait=0.5)
        c.wait_for("window.__nammaScene && window.__nammaScene.phase === 'ready'", timeout=300)
        c.wait_for("window.__nammaScene.lodShown === 0", timeout=240)
        if a.representation == 'splat':
            c.wait_for("window.__nammaScene.splat && window.__nammaScene.splat.shown", timeout=300)
        cameras = c.eval("window.__nammaScene.ctx.data.cameras")
        n = len(cameras)
        if a.camera_names:
            requested = json.loads(a.camera_names.read_text(encoding='utf-8'))
            if not isinstance(requested, list) or not all(isinstance(x, str) for x in requested):
                raise ValueError('camera-names must be a JSON array of strings')
            by_name = {cam['name']: i for i, cam in enumerate(cameras)}
            missing = [name for name in requested if name not in by_name]
            if missing:
                raise ValueError(f'Requested comparison cameras are missing: {missing}')
            indices = [by_name[name] for name in requested]
        else:
            indices = [int(x) for x in a.views.split(',') if 0 <= int(x) < n]
        shots = []
        records = []
        for order, v in enumerate(indices):
            c.eval(f"window.__nammaScene.ctx.showCamera({v})")    # same function as the ] / [ buttons
            c.pump(1.5)
            filename = f'view_{order:03d}.png'
            shots.append(c.screenshot(out / filename))
            records.append({'image': filename, 'camera_index': v, 'camera': cameras[v]})
        # overview: fly mode, rise to 3 eye heights above the start and look down at the room centre
        c.eval("(() => { const s = window.__nammaScene, w = s.walker, e = s.pkg.eye_height_units, b = s.pkg.bounds;"
               "const cx = (b.min[0] + b.max[0]) / 2, cz = (b.min[2] + b.max[2]) / 2; w.setMode('fly');"
               "w.setPose([cx + 0.01, 3.2 * e, cz + 2.2 * e], [cx, 0, cz]); })()")
        c.pump(1.5)
        shots.append(c.screenshot(out / "overview_above.png"))
        manifest = {'url': url, 'window': 'headless', 'representation': a.representation,
                    'views': records, 'note': 'Matched capture viewpoints; geometry is not independently verified.',
                    'errors': c.exceptions, 'console': c.console}
        (out / 'view_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        (out / 'camera_names.json').write_text(json.dumps([cameras[i]['name'] for i in indices], indent=2), encoding='utf-8')
        print(json.dumps({"views": n, "screens": [str(s) for s in shots]}))


if __name__ == "__main__":
    main()
