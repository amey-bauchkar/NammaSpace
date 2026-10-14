"""Browser test of the viewer's failure handling (integrity errors and missing packages).

    python scripts/browser/viewer_failure_e2e.py --corrupt-url <viewer url of a package with a modified file>
        --missing-url <viewer url pointing at a non-existent package> --out <dir>

Expected: the viewer shows its error panel with a plain message, renders nothing from the bad package, throws
no uncaught exception, and offers "Try again".
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import Chrome  # noqa: E402

PROBE = ("JSON.stringify({phase: (window.__nammaScene||{}).phase, errors: (window.__nammaScene||{}).errors, "
         "panel: !document.getElementById('error').hidden, msg: document.getElementById('error-message').textContent, "
         "detail: document.getElementById('error-detail').textContent, retry: !!document.getElementById('error-retry'), "
         "tris: (window.__nammaScene||{}).triangles})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corrupt-url", required=True)
    ap.add_argument("--missing-url", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = {"checks": []}
    with Chrome(headless=True) as c:
        for label, url, expect in (("corrupt", a.corrupt_url, "hash_mismatch"), ("missing", a.missing_url, "missing_file")):
            c.navigate(url, wait=0.5)
            c.wait_for("window.__nammaScene && window.__nammaScene.phase === 'error'", timeout=120)
            st = json.loads(c.eval(PROBE))
            shot = c.screenshot(out / f"failure_{label}.png")
            ok = st["panel"] and st["retry"] and expect in st["detail"] and not st["tris"] and not c.exceptions
            res["checks"].append({"case": label, "pass": ok, "state": st, "screenshot": shot})
            print(f"  {'PASS' if ok else 'FAIL'} {label}: {st['msg']} | {st['detail'][:120]}")
        res["exceptions"] = c.exceptions
    res["passed"] = all(x["pass"] for x in res["checks"])
    (out / "failure_e2e.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
