"""Screenshots of a scene package from several photo viewpoints + an overview from above (visual inspection).

    python scripts/qa/inspect_views.py --url "<viewer url>" --out <dir> [--views 0,30,60,90,120]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "browser"))
from cdp import Chrome  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--views", default="0,30,60,90,120")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with Chrome(headless=True) as c:
        c.navigate(a.url, wait=0.5)
        c.wait_for("window.__nammaScene && window.__nammaScene.phase === 'ready'", timeout=300)
        c.wait_for("window.__nammaScene.lodShown === 0", timeout=240)
        n = c.eval("window.__nammaScene.ctx.data.cameras.length")
        shots = []
        for v in [int(x) for x in a.views.split(",") if int(x) < n]:
            c.eval(f"window.__nammaScene.ctx.showCamera({v})")    # same function as the ] / [ buttons
            c.pump(1.5)
            shots.append(c.screenshot(out / f"view_{v:03d}.png"))
        # overview: fly mode, rise to 3 eye heights above the start and look down at the room centre
        c.eval("(() => { const s = window.__nammaScene, w = s.walker, e = s.pkg.eye_height_units, b = s.pkg.bounds;"
               "const cx = (b.min[0] + b.max[0]) / 2, cz = (b.min[2] + b.max[2]) / 2; w.setMode('fly');"
               "w.setPose([cx + 0.01, 3.2 * e, cz + 2.2 * e], [cx, 0, cz]); })()")
        c.pump(1.5)
        shots.append(c.screenshot(out / "overview_above.png"))
        print(json.dumps({"views": n, "screens": [str(s) for s in shots]}))


if __name__ == "__main__":
    main()
