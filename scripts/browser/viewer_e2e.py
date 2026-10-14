"""Browser test of the walkthrough viewer on a REAL scene package, with real input events.

    python scripts/browser/viewer_e2e.py --url "<viewer.html?package=...>" --out <dir> [--visible] [--bench-seconds 15]

Steps (each recorded in <out>/viewer_e2e.json, screenshots as PNG):
 1. load: package verified, model shown, timings (first view, full detail);
 2. mouse drag turns the view; W walks; F toggles fly, E rises; R resets to the start view;
    ] jumps to a photo viewpoint (all via CDP Input events, i.e. the real handlers);
 3. in-page self-test (?selftest=1);
 4. frame-time benchmark (?bench=1) - headless numbers are labelled as such.
Console errors and uncaught exceptions are collected and fail the run.
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import Chrome  # noqa: E402

STATE = "JSON.stringify((() => { const s = window.__nammaScene || {}; const c = s.app && s.app.camera; const w = s.walker; " \
        "return {phase: s.phase, timing: s.timing, lod: s.lodShown, tris: s.triangles, errors: s.errors, " \
        "pos: c ? [c.position.x, c.position.y, c.position.z] : null, yaw: w ? w.yaw : null, mode: w ? w.mode : null, " \
        "eye: s.pkg ? s.pkg.eye_height_units : null}; })())"


def state(c):
    return json.loads(c.eval(STATE))


def dist(a, b):
    return math.dist(a, b)


def run_checks(c, url, out, bench_seconds=15, label="viewer"):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    res = {"url": url, "label": label, "checks": [], "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "window": "headless" if c.headless else "visible"}

    def check(cid, name, ok, detail):
        res["checks"].append({"id": cid, "name": name, "pass": bool(ok), "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'} {cid} {name}: {detail}", flush=True)

    t0 = time.time()
    c.navigate(url, wait=0.5)
    c.wait_for("window.__nammaScene && (window.__nammaScene.phase === 'ready' || window.__nammaScene.phase === 'error')", timeout=300)
    s = state(c)
    res["load_wall_s"] = round(time.time() - t0, 2)
    check("V-01", "package verified and model displayed", s["phase"] == "ready" and s["tris"] > 0,
          f"phase {s['phase']}, {s['tris']} triangles, LOD {s['lod']}, timing {s['timing']}")
    res["gpu"] = c.eval("(() => { const gl = document.createElement('canvas').getContext('webgl2'); const d = gl && gl.getExtension('WEBGL_debug_renderer_info'); return d ? gl.getParameter(d.UNMASKED_RENDERER_WEBGL) : (gl ? gl.getParameter(gl.RENDERER) : 'no webgl2'); })()")
    c.pump(1.0)
    res["screens"] = [c.screenshot(out / f"{label}_01_start_view.png")]
    eye = s["eye"]
    start = s["pos"]
    check("V-02", "starts at eye height", abs(start[1] - eye) < 1e-6, f"y {start[1]:.3f} eye {eye:.3f}")
    # mouse drag look (real pointer events)
    y0 = s["yaw"]
    c.mouse_drag(500, 450, 220, 0)
    c.pump(0.3)
    s = state(c)
    check("V-03", "mouse drag turns the view", abs(s["yaw"] - y0) > 0.2, f"yaw {y0:.3f} -> {s['yaw']:.3f}")
    c.mouse_drag(500, 450, -220, 0)
    c.pump(0.3)
    # W walk
    p0 = state(c)["pos"]
    c.key("KeyW", down_ms=1200)
    c.pump(0.5)
    s = state(c)
    walked = dist(p0, s["pos"])
    check("V-04", "W key walks forward", walked > 0.1 * eye, f"moved {walked:.3f} units ({walked / eye:.2f} eye heights)")
    res["screens"].append(c.screenshot(out / f"{label}_02_after_walk.png"))
    # fly
    c.key("KeyF")
    c.pump(0.2)
    y_before = state(c)["pos"][1]
    c.key("KeyE", down_ms=800)
    c.pump(0.4)
    s = state(c)
    check("V-05", "F switches to fly mode and E rises", s["mode"] == "fly" and s["pos"][1] > y_before + 0.1 * eye,
          f"mode {s['mode']}, dy {s['pos'][1] - y_before:.3f}")
    res["screens"].append(c.screenshot(out / f"{label}_03_fly_up.png"))
    c.key("KeyF")
    c.pump(0.2)
    # reset
    c.key("KeyR")
    c.pump(0.4)
    s = state(c)
    check("V-06", "R resets to the start view in walk mode", s["mode"] == "walk" and dist(s["pos"], start) < 1e-3 * eye,
          f"mode {s['mode']}, distance to start {dist(s['pos'], start):.4f}")
    # photo viewpoint
    c.key("BracketRight", key="]")
    c.pump(1.5)
    s = state(c)
    check("V-07", "] jumps to a photo viewpoint", dist(s["pos"], start) > 1e-3, f"moved to {[round(v, 3) for v in s['pos']]}")
    res["screens"].append(c.screenshot(out / f"{label}_04_photo_viewpoint.png"))
    c.key("KeyR")
    # full detail (if the package has it and the viewer upgrades automatically)
    try:
        c.wait_for("window.__nammaScene.lodShown === 0", timeout=240)
        s = state(c)
        res["full_detail"] = {"lod": s["lod"], "tris": s["tris"], "timing": s["timing"]}
        c.pump(1.0)
        res["screens"].append(c.screenshot(out / f"{label}_05_full_detail.png"))
    except Exception as e:  # noqa: BLE001 - recorded, not hidden
        res["full_detail"] = {"error": str(e)}
    check("V-08", "no uncaught exceptions", not c.exceptions, c.exceptions[:3] or "none")
    errors = [m for m in c.console if m["type"] in ("error", "log.error")]
    check("V-09", "no console errors", not errors, errors[:3] or "none")
    # in-page self test
    sep = "&" if "?" in url else "?"
    c.navigate(url + sep + "selftest=1", wait=0.5)
    st = c.wait_for("window.__nammaSelftest", timeout=300)
    res["selftest"] = st
    check("V-10", "in-page self-test", st["allPassed"], f"{st['passed']}/{st['total']}")
    if bench_seconds:
        c.navigate(url + sep + f"bench=1&seconds={bench_seconds}", wait=0.5)
        b = c.wait_for("window.__nammaBench", timeout=600)
        res["bench"] = b
        print(f"  bench: valid={b['valid']} median={b['frame_ms']['median']:.1f} p95={b['frame_ms']['p95']:.1f} "
              f"LOD {b['lod']} gpu={b['gpu']}", flush=True)
    res["console"] = c.console[-50:]
    res["exceptions"] = c.exceptions
    res["passed"] = all(x["pass"] for x in res["checks"])
    (out / f"{label}_e2e.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--visible", action="store_true", help="show the browser window (default headless)")
    ap.add_argument("--bench-seconds", type=int, default=15)
    ap.add_argument("--label", default="viewer")
    a = ap.parse_args()
    with Chrome(headless=not a.visible) as c:
        r = run_checks(c, a.url, a.out, a.bench_seconds, a.label)
    print(json.dumps({"passed": r["passed"], "gpu": r.get("gpu"), "load_wall_s": r["load_wall_s"]}))
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
