"""End-to-end browser test of the Studio user journey with REAL data and REAL UI events.

    python scripts/browser/studio_e2e.py --base http://127.0.0.1:8781 --inputs samples/tum_fr3_office/images \
        --title "E2E TUM sample" --profile preview --out phase13_artifacts/browser/e2e_tum

Journey (each step screenshotted and recorded in <out>/studio_e2e.json):
 1 open the Studio, type a name, press "Create project"            (DOM input + click)
 2 choose files in the upload area                                (CDP DOM.setFileInputFiles -> real XHR uploads)
 3 press "Check my capture" and wait for the result               (real worker process)
 4 choose the profile, press "Start reconstruction"               (real COLMAP + OpenMVS run)
 5 watch the progress page until the run finishes                 (polls the page, not the API)
 6 open the 3D walkthrough from the result page and run the viewer checks (viewer_e2e.run_checks)
 7 publish / export and verify the static site + ZIP exist
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import Chrome  # noqa: E402
from viewer_e2e import run_checks  # noqa: E402

JS_CLICK = "(() => {{ const el = {sel}; if (!el) return false; el.scrollIntoView(); el.click(); return true; }})()"


def q(sel):
    return f"document.querySelector({json.dumps(sel)})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--inputs", required=True, help="folder of photos, or one video file")
    ap.add_argument("--title", default="E2E test space")
    ap.add_argument("--source", default="", help="photo source / licence typed into the New space form")
    ap.add_argument("--cancel-resume", action="store_true", help="press Cancel during camera placement, then Resume")
    ap.add_argument("--profile", default="preview")
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout-min", type=float, default=90)
    ap.add_argument("--visible", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    src = Path(a.inputs)
    files = [src] if src.is_file() else sorted(p for p in src.iterdir() if p.is_file())
    log = {"base": a.base, "inputs": str(src), "files": len(files), "steps": [], "profile": a.profile}

    def step(name, ok, detail, shot=None):
        log["steps"].append({"step": name, "pass": bool(ok), "detail": detail, "screenshot": shot,
                             "at": time.strftime("%H:%M:%S")})
        print(f"  {'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)
        if not ok:
            raise SystemExit(finish(1))

    def finish(code):
        log["passed"] = code == 0 and all(s["pass"] for s in log["steps"])
        (out / "studio_e2e.json").write_text(json.dumps(log, indent=1, default=str), encoding="utf-8")
        return code

    with Chrome(headless=not a.visible) as c:
        t0 = time.time()
        c.navigate(f"{a.base}/studio.html", wait=1.0)
        c.wait_for(f"{q('#new-title')} !== null", timeout=30)
        c.eval(f"{q('#new-title')}.value = {json.dumps(a.title)}")
        if a.source:
            c.eval(f"{q('#new-source')}.value = {json.dumps(a.source)}")
        c.eval(JS_CLICK.format(sel=q("#new-form button[type=submit]")))
        c.wait_for("location.hash.startsWith('#/project/')", timeout=30)
        pid = c.eval("location.hash.split('/')[2]")
        c.wait_for(f"{q('#dropzone')} !== null", timeout=30)
        notices = json.loads(c.eval(f"(async () => JSON.stringify((await (await fetch('/api/projects/{pid}')).json()).notices || []))()"))
        ok_src = (not a.source) or any(a.source in n for n in notices)
        step("1 create project via the form", bool(pid) and ok_src, f"{pid}; notices {notices}", c.screenshot(out / "01_project_created.png"))
        log["project_id"] = pid

        c.set_files("#file-input", files)
        c.wait_for(f"{q('#upload-msg')}.textContent.includes('uploaded')", timeout=1800)
        msg = c.eval(f"{q('#upload-msg')}.textContent")
        step("2 upload through the file input", "problems" not in msg, msg, c.screenshot(out / "02_uploaded.png"))

        c.eval(JS_CLICK.format(sel=q("#check-btn")))
        c.wait_for(f"{q('#check-result')}.textContent.length > 0", timeout=1800)
        verdict = c.eval(f"{q('#check-result .callout')}.textContent")
        ready = c.eval(f"!{q('#run-btn')}.disabled")
        step("3 capture check", ready, verdict[:200], c.screenshot(out / "03_checked.png"))

        c.eval(f"document.querySelector('input[name=profile][value={a.profile}]').click()")
        c.eval(JS_CLICK.format(sel=q("#run-btn")))
        c.wait_for("location.hash.includes('/run/')", timeout=60)
        rid = c.eval("location.hash.split('/')[4]")
        log["run_id"] = rid
        step("4 start reconstruction", bool(rid), rid, c.screenshot(out / "04_started.png"))

        deadline = time.time() + a.timeout_min * 60
        shots = 0
        last_state = None
        cancel_pending = a.cancel_resume
        while time.time() < deadline:
            state = c.eval(f"(() => {{ const s = {q('[data-f=state]')}; return s ? s.textContent : null; }})()")
            if state != last_state:
                print(f"    progress: {state}", flush=True)
                last_state = state
            if shots < 3 and time.time() - t0 > 60 * (shots + 1):
                c.screenshot(out / f"05_progress_{shots}.png")
                shots += 1
            if cancel_pending and state and "structure from motion" in state:
                c.pump(15)
                c.eval(JS_CLICK.format(sel=q("[data-f=cancel]")))
                c.wait_for(f"!{q('[data-f=resume]')}.hidden", timeout=600)
                before = json.loads(c.eval(f"(async () => JSON.stringify(await (await fetch('/api/projects/{pid}/runs/{rid}')).json()))()"))
                c.screenshot(out / "04b_cancelled.png")
                c.eval(JS_CLICK.format(sel=q("[data-f=resume]")))
                c.wait_for(f"{q('[data-f=resume]')}.hidden", timeout=120)
                kept = [s["name"] for s in before.get("stages", []) if s.get("status") == "done"]
                step("4b cancel during camera placement, then resume (UI buttons)",
                     before.get("status") == "cancelled" and "prepare" in kept,
                     f"status after cancel {before.get('status')}; stages kept {kept}", str(out / "04b_cancelled.png"))
                cancel_pending = False
                continue
            done = c.eval(f"!{q('[data-f=result]')}.hidden")
            failed = c.eval(f"{q('[data-f=error]')}.textContent")
            if done or failed:
                break
            c.pump(5)
        res_text = c.eval(f"{q('[data-f=result]')}.innerText")
        err = c.eval(f"{q('[data-f=error]')}.textContent")
        step("5 reconstruction finished in the UI", not err and "Quality" in (res_text or ""),
             (err or res_text or "")[:400], c.screenshot(out / "05_result.png"))
        log["result_text"] = res_text
        log["journey_minutes"] = round((time.time() - t0) / 60, 2)

        href = c.eval(f"{q('[data-f=open]')}.href")
        viewer = run_checks(c, href, out, bench_seconds=10, label="e2e_viewer")
        step("6 open the walkthrough and drive it", viewer["passed"],
             f"{sum(x['pass'] for x in viewer['checks'])}/{len(viewer['checks'])} viewer checks; GPU {viewer.get('gpu')}")
        log["viewer"] = {k: viewer[k] for k in ("checks", "gpu", "load_wall_s", "selftest") if k in viewer}
        if "bench" in viewer:
            log["viewer"]["bench"] = viewer["bench"]

        c.navigate(f"{a.base}/studio.html#/project/{pid}/run/{rid}", wait=2.0)
        c.wait_for(f"!{q('[data-f=result]')}.hidden", timeout=60)
        c.eval(JS_CLICK.format(sel=q("[data-f=publish]")))
        c.wait_for(f"{q('[data-f=publish-result] .callout')} !== null", timeout=600)
        pub = c.eval(f"{q('[data-f=publish-result]')}.innerText")
        zip_href = c.eval(f"(() => {{ const a = {q('[data-f=publish-result] a[download]')}; return a ? a.href : null; }})()")
        ok_zip = c.eval(f"fetch({json.dumps(zip_href)}, {{method: 'HEAD'}}).then(r => r.ok)") if zip_href else False
        step("7 publish / export", "Published" in pub and ok_zip, pub[:300], c.screenshot(out / "07_published.png"))
        log["console_errors"] = [m for m in c.console if m["type"] in ("error", "log.error")]
        log["exceptions"] = c.exceptions
    return finish(0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
