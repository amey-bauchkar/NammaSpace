import json, sys
from pathlib import Path
sys.path.insert(0, "scripts/browser")
from cdp import Chrome
runs = {"1600px": "run-20261019-010810-reuse", "final": "run-20261019-025147-reuse"}
out = Path("own_capture/hall_report/closeups"); out.mkdir(parents=True, exist_ok=True)
with Chrome(headless=True) as c:
    for label, rid in runs.items():
        c.navigate(f"http://127.0.0.1:8780/viewer.html?package=/files/hall-246-photos-20261018-29fa2c/runs/{rid}/90_package/scene_package.json", wait=0.5)
        c.wait_for("window.__nammaScene && window.__nammaScene.phase === 'ready'", timeout=300)
        c.wait_for("window.__nammaScene.lodShown === 0", timeout=300)
        for v, f in ((150, 0.55), (185, 0.6), (45, 0.55)):
            c.eval(f"""(() => {{ const s = window.__nammaScene, cam = s.ctx.data.cameras[{v}], w = s.walker;
                w.setMode('fly'); const p = cam.position, d = cam.forward;
                // move along the photo's viewing direction toward the surface it looks at
                const k = {f} * s.pkg.eye_height_units;
                w.setPose([p[0] + d[0] * k, p[1] + d[1] * k, p[2] + d[2] * k], [p[0] + d[0] * (k + 1), p[1] + d[1] * (k + 1), p[2] + d[2] * (k + 1)]); }})()""")
            c.pump(1.5)
            c.screenshot(out / f"{label}_view{v}.png")
print("ok")
