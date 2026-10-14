"""Browser test of the viewer's Studio tools on a REAL reconstruction (studio mode, real clicks):
label a place by clicking the model, search for it and jump there, request a walking path, measure two points and
set the scale. The run's package is rebuilt by the server (calibration) - use a run that may be modified.

    python scripts/browser/viewer_tools_e2e.py --base http://127.0.0.1:8781 --project <id> --run <run_id> --out <dir>
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import Chrome  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="Test desk")
    ap.add_argument("--real-length-m", type=float, default=1.0, help="length typed for the scale test (TEST VALUE)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pkg = f"/files/{a.project}/runs/{a.run}/90_package/scene_package.json"
    url = f"{a.base}/viewer.html?package={pkg}&project={a.project}&run={a.run}"
    res = {"url": url, "checks": []}

    def check(name, ok, detail):
        res["checks"].append({"check": name, "pass": bool(ok), "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)

    with Chrome(headless=True) as c:
        c.navigate(url, wait=0.5)
        c.wait_for("window.__nammaScene && window.__nammaScene.phase === 'ready'", timeout=240)
        c.pump(1.0)
        rect = json.loads(c.eval("JSON.stringify(document.getElementById('view').getBoundingClientRect())"))
        cx, cy = rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
        n0 = c.eval("window.__nammaScene.ctx.pois.length")
        # 1. label a place by clicking the model
        c.eval("document.getElementById('tool-poi').click()")
        hit = False
        for dx, dy in ((0, 60), (0, 0), (-120, 80), (120, 80), (0, 160)):
            c.click(cx + dx, cy + dy)
            c.pump(0.6)
            if c.eval("!document.getElementById('poi-form').hidden"):
                hit = True
                break
        check("click on the model opens the label form", hit, "raycast hit the reconstructed surface" if hit else "no surface hit")
        if hit:
            c.eval(f"document.getElementById('poi-name').value = {json.dumps(a.label)}; document.getElementById('poi-cat').value = 'test';"
                   "document.querySelector('#poi-form-el button[type=submit]').click()")
            c.wait_for(f"window.__nammaScene.ctx.pois.length === {n0 + 1}", timeout=30)
            check("label saved and shown", True, f"{n0} -> {n0 + 1} labels")
            c.screenshot(out / "tools_01_label.png")
            # 2. search + jump
            c.eval("window.__nammaScene.walker.setPose([0,0,0],[1,0,0])")
            c.eval(f"document.getElementById('search').value = {json.dumps(a.label.split()[-1])};"
                   "document.getElementById('search-btn').click()")
            c.wait_for("window.__nammaScene.lastJump", timeout=30)
            st = json.loads(c.eval("JSON.stringify({s: window.__nammaScene.lastSearch.status, j: window.__nammaScene.lastJump})"))
            check("search finds the label and jumps to it", st["s"] == "unique" and st["j"], json.dumps(st)[:160])
            c.pump(1.2)
            c.screenshot(out / "tools_02_jump.png")
            # 3. walking path
            visible = c.eval("!document.getElementById('poi-route').hidden")
            if visible:
                c.eval("document.getElementById('poi-route').click()")
                c.wait_for("window.__nammaScene.lastRoute", timeout=60)
                r = json.loads(c.eval("JSON.stringify(window.__nammaScene.lastRoute)"))
                shown = c.eval("document.getElementById('route-status').innerText")
                honest = r["status"] != "validated" or r.get("scale_basis") not in ("assumed_capture_height", "unknown")
                check("walking-path request answered with an honest status", honest and bool(shown),
                      f"status {r['status']}: {shown[:160]}")
                res["route"] = {k: r.get(k) for k in ("status", "title", "scale_basis", "length_m", "validator_verdict", "checks")}
                c.screenshot(out / "tools_03_route.png")
            else:
                check("walking-path button hidden when navigation is unavailable", True, "navigation unavailable for this run")
        # 4. measure + scale
        c.eval("window.__nammaScene.ctx.startPose()")
        c.pump(0.5)
        c.eval("document.getElementById('tool-measure').click()")
        pts = 0
        for dx, dy in ((-150, 60), (150, 60), (-100, 120), (100, 120), (-60, 0), (60, 0)):
            c.click(cx + dx, cy + dy)
            c.pump(0.5)
            pts = c.eval("(window.__nammaScene.ctx.measure || []).length")
            if pts >= 2:
                break
        units = c.eval("window.__nammaScene.ctx.measureUnits || null")
        check("two points measured on the model", pts >= 2 and units, f"{units} model units")
        if pts >= 2:
            c.eval(f"document.getElementById('measure-real').value = '{a.real_length_m}'; document.getElementById('measure-apply').click()")
            t0 = time.time()
            c.wait_for("document.getElementById('tool-msg').textContent.includes('Reloading') || document.getElementById('tool-msg').textContent.includes('Could not')", timeout=300)
            msg = c.eval("document.getElementById('tool-msg').textContent")
            c.pump(2.0)
            c.wait_for("window.__nammaScene && window.__nammaScene.phase === 'ready'", timeout=240)
            sc = json.loads(c.eval("JSON.stringify(window.__nammaScene.pkg.scale)"))
            check("scale set from a known length (package rebuilt, viewer reloaded)", sc["status"] == "user_distance" and sc["metres_per_unit"],
                  f"{msg[:60]}; scale {sc['status']} {sc['metres_per_unit']}; {round(time.time() - t0, 1)} s")
            c.screenshot(out / "tools_04_scaled.png")
        res["exceptions"] = c.exceptions
        res["console_errors"] = [m for m in c.console if m["type"] in ("error", "log.error")]
    res["passed"] = all(x["pass"] for x in res["checks"]) and not res["exceptions"]
    res["note"] = f"The length typed ({a.real_length_m} m) is a TEST value; the resulting scale of this run is not metric."
    (out / "tools_e2e.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
