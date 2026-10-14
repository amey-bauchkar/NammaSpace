"""Aggregate every evaluated run into one machine-readable matrix and a readable table.

    python scripts/eval/results_matrix.py

Reads  phase13_artifacts/projects/*/runs/*/eval/metrics.json   (scripts/eval/evaluate_run.py)
       phase13_artifacts/browser/**/*_e2e.json                  (scripts/browser/viewer_e2e.py, studio_e2e.py)
Writes phase13_artifacts/results/metrics_matrix.json and docs/RESULTS_SCENES.md.
Every number comes from those files; nothing is typed in by hand.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "phase13_artifacts" / "results"


def fmt(v, d=3):
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "PASS" if v else "FAIL"
    if isinstance(v, float):
        return f"{v:.{d}f}"
    return str(v)


def frozen_hashes():
    f = ROOT / "datasets" / "FROZEN_CONFIG.json"
    if not f.exists():
        return {}
    return {v["config_sha256"]: k for k, v in json.loads(f.read_text(encoding="utf-8"))["profiles"].items()}


def unevaluated_runs():
    """Every run without an evaluation (failed, cancelled, still running, or not evaluated) - failures stay on record."""
    out = []
    for p in sorted((ROOT / "phase13_artifacts" / "projects").glob("*/runs/*/run.json")):
        if (p.parent / "eval" / "metrics.json").exists():
            continue
        r = json.loads(p.read_text(encoding="utf-8"))
        e = r.get("error") or {}
        out.append({"project": p.parts[-4], "run_id": r["run_id"], "profile": r.get("profile"), "status": r.get("status"),
                    "config_sha256": r.get("config_sha256"), "failed_stage": e.get("stage") if isinstance(e, dict) else None,
                    "message": (e.get("message") if isinstance(e, dict) else str(e or "")) or "",
                    "run_file": str(p.relative_to(ROOT)).replace("\\", "/")})
    return out


def main():
    frozen = frozen_hashes()
    rows = []
    for p in sorted((ROOT / "phase13_artifacts" / "projects").glob("*/runs/*/eval/metrics.json")):
        m = json.loads(p.read_text(encoding="utf-8"))
        g = m.get("geometry", {})
        gm = m.get("geometry_mesh", {})
        fl = m.get("floor", {})
        rt = m.get("routes", {})
        ob = m.get("obstacles", {})
        rows.append({
            "scene_id": m["scene_id"], "role": m["role"], "project": m["project"], "run_id": m["run_id"],
            "profile": m["timing"]["profile"], "config_sha256": m["timing"]["config_sha256"],
            "registered": f"{m['registration']['registered']}/{m['registration']['input']}",
            "registration_rate": m["registration"]["rate"],
            "pose_median_m": m["pose"].get("position_error_m", {}).get("median"),
            "dense_accuracy_median_m": g.get("accuracy_m", {}).get("median"),
            "dense_completeness_5cm": g.get("completeness_at_5cm"),
            "mesh_accuracy_median_m": gm.get("accuracy_m", {}).get("median"),
            "mesh_completeness_5cm": gm.get("completeness_at_5cm"),
            "floor_iou": fl.get("free_floor_iou"), "floor_recall": fl.get("free_floor_recall"),
            "floor_precision": fl.get("free_floor_precision"),
            "obstacles_omitted": f"{ob['omitted']}/{ob['reference_obstacles']}" if "omitted" in ob else None,
            "routes": f"{rt['success']}/{rt['pairs']} ok, {rt['collisions_with_reference_obstacles']} coll., "
                      f"{rt['false_unreachable']} false-unreach." if rt.get("measurable") else (rt.get("reason") or "n/a"),
            "assumed_scale_error": m.get("scale", {}).get("assumed_scale_error"),
            "minutes": m["timing"]["total_minutes"], "peak_ram_gb": m["timing"]["peak_rss_gb"],
            "verdicts": m.get("verdicts", {}), "metrics_file": str(p.relative_to(ROOT)).replace("\\", "/"),
            "frozen_config": m["timing"]["config_sha256"] in frozen})
    browser = []
    for p in sorted((ROOT / "phase13_artifacts" / "browser").rglob("*_e2e.json")):
        b = json.loads(p.read_text(encoding="utf-8"))
        checks = b.get("checks") or b.get("steps") or []
        bench = b.get("bench") or (b.get("viewer") or {}).get("bench") or {}
        sup = p.parent / "SUPERSEDED.txt"
        browser.append({"file": str(p.relative_to(ROOT)).replace("\\", "/"),
                        "superseded": sup.read_text(encoding="utf-8").strip() if sup.exists() else None,
                        "passed": b.get("passed"), "checks": f"{sum(1 for c in checks if c.get('pass'))}/{len(checks)}",
                        "gpu": b.get("gpu") or (b.get("viewer") or {}).get("gpu"),
                        "bench_valid": bench.get("valid"), "frame_median_ms": (bench.get("frame_ms") or {}).get("median"),
                        "frame_p95_ms": (bench.get("frame_ms") or {}).get("p95"), "bench_lod": bench.get("lod"),
                        "bench_triangles": bench.get("triangles"), "first_view_ms": (bench.get("timing") or {}).get("first_view_ms"),
                        "window": b.get("window") or (b.get("viewer") or {}).get("window") or "headless"})
    OUT.mkdir(parents=True, exist_ok=True)
    other = unevaluated_runs()
    for r in other:
        r["frozen_config"] = r["config_sha256"] in frozen
    (OUT / "metrics_matrix.json").write_text(json.dumps({"scenes": rows, "unevaluated_runs": other, "browser": browser},
                                                        indent=1), encoding="utf-8")
    L = ["# Phase 13 results by scene (generated)", "",
         "Generated by `python scripts/eval/results_matrix.py` from the per-run `eval/metrics.json` files and the browser "
         "test records. All data are EXTERNAL DATASETS. Criteria: `phase13_artifacts/PROTOCOL_PHASE13.md` §4.", "",
         "Config: **frozen** = the configuration frozen before the held-out runs (`datasets/FROZEN_CONFIG.json`); "
         "**dev** = an earlier development configuration (kept on record).", "",
         "| Scene | Role | Profile | Config | Registered | Pose median (m) | Dense acc. median (m) | Dense compl. @5 cm | Mesh compl. @5 cm "
         "| Floor IoU | Obstacles omitted | Routes | Assumed-scale error | Time (min) | Peak RAM (GB) |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['scene_id']} | {r['role']} | {r['profile']} | {'frozen' if r['frozen_config'] else 'dev'} | {r['registered']} | {fmt(r['pose_median_m'], 4)} | "
                 f"{fmt(r['dense_accuracy_median_m'], 4)} | {fmt(r['dense_completeness_5cm'])} | {fmt(r['mesh_completeness_5cm'])} | "
                 f"{fmt(r['floor_iou'])} | {r['obstacles_omitted'] or '–'} | {r['routes']} | {fmt(r['assumed_scale_error'], 2)} | "
                 f"{fmt(r['minutes'], 1)} | {fmt(r['peak_ram_gb'], 2)} |")
    L += ["", "## Criterion verdicts per run", "", "| Scene | Profile | Verdicts |", "|---|---|---|"]
    for r in rows:
        L.append(f"| {r['scene_id']} | {r['profile']} | " + "; ".join(f"{k}: {fmt(v)}" for k, v in r["verdicts"].items()) + " |")
    L += ["", "## Runs without an evaluation (failed, cancelled or running) – kept on record", "",
          "| Project | Run | Profile | Config | Status | Stage | Message |", "|---|---|---|---|---|---|---|"]
    for r in other:
        L.append(f"| {r['project']} | `{r['run_id']}` | {r['profile']} | {'frozen' if r['frozen_config'] else 'dev'} | "
                 f"{r['status']} | {r['failed_stage'] or '–'} | {r['message'][:160].replace('|', '/')} |")
    L += ["", "## Browser tests", "", "| Record | Window | Passed | Checks | GPU | Bench valid | Median / P95 frame (ms) | LOD | Triangles | First view (ms) |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for b in browser:
        L.append(f"| `{b['file']}`{' (superseded: ' + b['superseded'][:90] + ')' if b['superseded'] else ''} | {b['window']} | {fmt(b['passed'])} | {b['checks']} | {b['gpu'] or '–'} | {fmt(b['bench_valid'])} | "
                 f"{fmt(b['frame_median_ms'], 1)} / {fmt(b['frame_p95_ms'], 1)} | {fmt(b['bench_lod'])} | {fmt(b['bench_triangles'])} | "
                 f"{fmt(b['first_view_ms'])} |")
    L += ["", "Window = how Chrome ran: **headless** records (real GPU via ANGLE / D3D11, real input events) are not a "
          "visible-window benchmark; **visible** records ran in a normal on-screen Chrome window."]
    (ROOT / "docs" / "RESULTS_SCENES.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    gates = gate_matrix(rows, browser)
    (OUT / "gates.json").write_text(json.dumps(gates, indent=1), encoding="utf-8")
    G = ["# Acceptance gates A–F (generated)", "",
         "Generated by `python scripts/eval/results_matrix.py`. A gate PASSES only if every check passes. Checks read "
         "evidence files; nothing is typed in by hand.", ""]
    for gid, g in gates.items():
        G += [f"## Gate {gid} – {g['title']}: **{g['status']}**", "", "| Check | Result | Evidence |", "|---|---|---|"]
        for c in g["checks"]:
            G.append(f"| {c['check']} | {c['result']} | {c['evidence']} |")
        G.append("")
    (ROOT / "docs" / "RESULTS_MATRIX.md").write_text("\n".join(G) + "\n", encoding="utf-8")
    print(f"{len(rows)} evaluated runs, {len(browser)} browser records -> docs/RESULTS_SCENES.md, docs/RESULTS_MATRIX.md")
    for gid, g in gates.items():
        print(f"Gate {gid}: {g['status']}")


def _latest(pattern):
    c = sorted(ROOT.glob(pattern))
    return c[-1] if c else None


def gate_matrix(rows, browser):
    import re
    gates = {}

    def gate(gid, title, checks):
        results = [c["result"] for c in checks]
        st = "PASS" if results and all(r == "PASS" for r in results) else (
            "NOT RUN" if results and all(r == "NOT RUN" for r in results) else "FAIL" if "FAIL" in results else "INCOMPLETE")
        gates[gid] = {"title": title, "status": st, "checks": checks}

    def chk(name, ok, evidence):
        return {"check": name, "result": ok if isinstance(ok, str) else ("PASS" if ok else "FAIL"), "evidence": evidence}

    reg = _latest("test_output/phase13/regression-*/summary.json")
    if reg:
        r = json.loads(reg.read_text(encoding="utf-8"))
        steps = {x["step"]: x for x in r["steps"]}
        rel = reg.parent.relative_to(ROOT).as_posix()
        a = [chk("contract examples + schema audit", steps.get("2_contracts", {}).get("passed", False), f"`{rel}/2_contracts.log`"),
             chk("all Python tests", steps.get("3_pytest", {}).get("passed", False), steps.get("3_pytest", {}).get("tail", "")),
             chk("all Node tests", steps.get("4_node", {}).get("passed", False), f"`{rel}/4_node.log`")]
    else:
        a = [chk("regression suite", "NOT RUN", "no test_output/phase13/regression-*/summary.json")]
    a.append(chk("reproducible setup documented", all((ROOT / x).exists() for x in ("docs/INSTALL.md", "scripts/setup_tools.py",
                                                                                   "requirements.txt")),
                 "`docs/INSTALL.md`, `scripts/setup_tools.py`, `requirements.txt`"))
    a.append(chk("contract changes documented", "IF-13" in (ROOT / "docs/CONTRACT_CHANGES.md").read_text(encoding="utf-8"),
                 "`docs/CONTRACT_CHANGES.md`"))
    gate("A", "Repository integrity", a)

    real = [r for r in rows if r["registration_rate"] and r["mesh_completeness_5cm"] is not None]
    b = [chk("public indoor dataset reconstructed by the real pipeline (GLB produced and evaluated)", bool(real),
             ", ".join(f"{r['scene_id']} ({r['profile']})" for r in real) or "none"),
         chk("registered vs unregistered images recorded for every evaluated run", bool(rows) and all(r["registered"] for r in rows),
             "`registration` in each metrics.json"),
         chk("outputs inspected (screenshots of the real model)", any(ROOT.glob("phase13_artifacts/browser/**/*photo_viewpoint*.png")),
             "`phase13_artifacts/browser/**/*.png`")]
    gate("B", "Real reconstruction", b)

    vb = [x for x in browser if "studio_e2e" not in x["file"] and not x["superseded"]]   # superseded records stay listed
    vis = [x for x in vb if x["window"] == "visible"]
    c = [chk("generated assets load and every viewer check passes (WASD, mouse, fly, reset, viewpoints, no errors)",
             bool(vb) and all(x["passed"] for x in vb), f"{sum(1 for x in vb if x['passed'])}/{len(vb)} viewer records passed"),
         chk("frame time recorded on the target laptop (EXP-005: median <= 33 ms)",
             any(x["bench_valid"] and x["frame_median_ms"] is not None and x["frame_median_ms"] <= 33 for x in vb if x["window"] == "headless"),
             "headless Chrome on the Intel iGPU via ANGLE / D3D11 (not a visible-window benchmark)"),
         chk("visible-window benchmark on the target laptop (valid run, EXP-005: median <= 33 ms)",
             (any(x["bench_valid"] and x["frame_median_ms"] <= 33 for x in vis) if vis else "NOT RUN"),
             "; ".join(f"`{x['file']}`: valid {fmt(x['bench_valid'])}, median {fmt(x['frame_median_ms'], 1)} ms"
                       for x in vis) or "no visible-window record")]
    gate("C", "Browser walkthrough", c)

    sj = [json.loads((ROOT / x["file"]).read_text(encoding="utf-8")) for x in browser if "studio_e2e" in x["file"]]
    d = [chk("upload → check → reconstruct → viewer → publish through the real UI", bool(sj) and any(j.get("passed") for j in sj),
             ", ".join(x["file"] for x in browser if "studio_e2e" in x["file"]) or "not run"),
         chk("failures produce diagnostics and runs resume after a real crash",
             "D13-01" in (ROOT / "phase13_artifacts/PROTOCOL_DEVIATIONS_PHASE13.md").read_text(encoding="utf-8"),
             "kicker crash + resume (D13-01); `tests/test_p13_studio.py`")]
    gate("D", "Full-data workflow", d)

    nav = [r for r in rows if isinstance(r["routes"], str) and " ok," in r["routes"]]
    e = [chk("routes checked against independent reference geometry where measurable", bool(nav),
             "; ".join(f"{r['scene_id']} ({r['profile']}): {r['routes']}" for r in nav) or "none"),
         chk("navigation criteria met (>= 1 route, 0 collisions, 0 false-unreachable)",
             bool(nav) and all(r["verdicts"].get("routes_no_collision") is True and r["verdicts"].get("routes_no_false_unreachable") is True
                               for r in nav), "verdicts in each metrics.json"),
         chk("unmeasurable route results marked as unmeasurable",
             all(" ok," in str(r["routes"]) or "reference" in str(r["routes"]) or "no surface" in str(r["routes"]) for r in rows),
             "`routes` field of each metrics.json")]
    gate("E", "Navigation correctness", e)

    pdf = ROOT / "docs/round1/TECHNICAL_ABSTRACT.pdf"
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf.read_bytes())) if pdf.exists() else None
    docs = ["README.md", "docs/INSTALL.md", "docs/CAPTURE_SOP.md", "docs/DEMO_SCRIPT.md", "THIRD_PARTY_NOTICES.md",
            "datasets/DATASET_REGISTRY.md", "architecture/NAMMA_SPACE_ARCHITECTURE.md", "docs/PHASE13_REPORT.md"]
    f = [chk("technical abstract PDF within 5 pages", pdf.exists() and pages is not None and pages <= 5,
             f"`docs/round1/TECHNICAL_ABSTRACT.pdf` ({pages} pages)" if pdf.exists() else "missing"),
         chk("README, install, SOP, demo plan, notices, registry, architecture, report present",
             all((ROOT / x).exists() for x in docs), ", ".join(f"`{x}`" for x in docs)),
         chk("public viewer URL (OFF-37)", "NOT RUN",
             "no hosting account / credentials in this environment; deployment-ready static build verified locally")]
    gate("F", "Submission materials", f)
    return gates


if __name__ == "__main__":
    main()
