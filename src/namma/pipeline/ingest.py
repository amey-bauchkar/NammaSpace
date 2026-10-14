"""Capture ingestion -> IF-01 manifest (WP-01, P-02).

Input layout (no phone-specific format is assumed):
    <input_dir>/capture_plan.json        regions, connections, devices, tape references
    <input_dir>/<region_id>/<files...>   files captured for that region (any depth)

Supported kinds are decided by extension only. Files that no processor in
this project can read are still recorded (kind "other") and preserved as
source artifacts, with an explicit issue - nothing is silently dropped except
OS metadata files (listed in the report).

Two separate results:
- integrity: every recorded file exists, is non-empty, and its SHA-256 / size
  were computed from the actual bytes;
- coverage:  which planned regions have files. Correct checksums do NOT prove
  that the venue was captured completely; coverage is only relative to the plan.
"""
from pathlib import Path

from namma import CONTRACT_VERSION
from namma.contracts.semantic import validate_document
from namma.core import timefmt
from namma.core.issues import as_dicts, error, errors_only, warning
from namma.core.jsonio import load_strict, sha256_file

KINDS = {".mp4": "video", ".mov": "video", ".m4v": "video",
         ".jpg": "image_set", ".jpeg": "image_set", ".png": "image_set", ".heic": "image_set",
         ".dng": "image_set"}
POSE_LOG_NAMES = {"poses.json", "poses.csv", "trajectory.txt", "trajectory.csv"}
IGNORED = {"thumbs.db", ".ds_store", "desktop.ini"}
PLAN_NAME = "capture_plan.json"


def classify(path):
    name = path.name.lower()
    if name in POSE_LOG_NAMES:
        return "pose_log"
    return KINDS.get(path.suffix.lower(), "other")


def ingest(input_dir, run_id=None, created_at=None, ignore_dirs=()):
    """Return (manifest_or_None, report). The manifest is None if the plan is unusable.

    `ignore_dirs`: top-level folders that are not capture regions (e.g. "measurements");
    their files are listed in the report as ignored, never recorded as capture files.
    """
    input_dir = Path(input_dir)
    report = {"report_type": "ingest_report_v1", "input_dir": str(input_dir), "integrity": {}, "coverage": {},
              "ignored_files": [], "issues": []}
    issues = []
    plan_path = input_dir / PLAN_NAME
    if not plan_path.is_file():
        issues.append(error("INGEST-NO-PLAN", f"{PLAN_NAME} not found in {input_dir}"))
        report["issues"] = as_dicts(issues)
        return None, report
    plan = load_strict(plan_path)
    regions = {r["region_id"]: r for r in plan.get("regions", [])}
    files, per_region = [], {rid: 0 for rid in regions}
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file() or p == plan_path:
            continue
        rel = p.relative_to(input_dir).as_posix()
        if p.name.lower() in IGNORED:
            report["ignored_files"].append(rel)
            continue
        parts = p.relative_to(input_dir).parts
        if parts[0] in ignore_dirs:
            report["ignored_files"].append(rel)
            continue
        if len(parts) < 2:
            issues.append(error("INGEST-UNASSIGNED-FILE", f"{rel!r} is not inside a region folder", rel))
            continue
        rid = parts[0]
        if rid not in regions:
            issues.append(error("INGEST-UNKNOWN-REGION", f"{rel!r} is in folder {rid!r}, not a planned region", rel))
            continue
        size = p.stat().st_size
        if size == 0:
            issues.append(error("INGEST-EMPTY-FILE", f"{rel!r} is empty (0 bytes); not recorded", rel))
            continue
        kind = classify(p)
        entry_issues = []
        if kind == "other":
            entry_issues.append("preserved as a source artifact; no processor for this format in Track A")
            issues.append(warning("INGEST-UNSUPPORTED-KIND", f"{rel!r}: kind 'other' (preserved only)", rel))
        files.append({"path": rel, "sha256": sha256_file(p), "bytes": size, "region_id": rid, "kind": kind,
                      "device": regions[rid].get("device", "unknown"), "duration_s": None,
                      "issues": entry_issues})
        per_region[rid] += 1

    out_regions = []
    for rid, r in regions.items():
        status = r.get("status", "captured")
        entry = {"region_id": rid, "status": status, "connections": list(r.get("connections", []))}
        if r.get("note"):
            entry["note"] = r["note"]
        if status in ("captured", "partial") and per_region[rid] == 0:
            issues.append(error("INGEST-CLAIMED-WITHOUT-FILES",
                                f"plan marks region {rid!r} {status} but no files were found"))
            entry["status"] = "partial"
            entry["note"] = (entry.get("note", "") + " | INGEST: no files found although the plan said "
                             f"{status}; NOT captured").strip(" |")
        for c in entry["connections"]:
            if c not in regions:
                issues.append(error("INGEST-UNKNOWN-CONNECTION", f"region {rid!r} connects to unplanned {c!r}"))
        out_regions.append(entry)

    manifest = {"contract": "IF-01", "contract_version": CONTRACT_VERSION,
                "run_id": run_id or plan.get("run_id", "run-unnamed"),
                "venue_label": plan.get("venue_label", "unlabelled"),
                "created_at": created_at or timefmt.now_iso(), "files": files, "regions": out_regions}
    if plan.get("reference_measurements"):
        manifest["reference_measurements"] = plan["reference_measurements"]
    if not files:
        issues.append(error("INGEST-NO-FILES", "no capture files found"))
    else:
        issues += validate_document(manifest, "IF-01", base_dir=input_dir, verify_files=True)
    report["integrity"] = {"files_recorded": len(files), "bytes_total": sum(f["bytes"] for f in files),
                           "hashes": "computed from file contents (SHA-256)"}
    captured = [r["region_id"] for r in out_regions if r["status"] == "captured"]
    report["coverage"] = {"planned_regions": len(regions), "regions_with_files": sum(1 for v in per_region.values() if v),
                          "captured": captured,
                          "not_captured": [r["region_id"] for r in out_regions if r["status"] != "captured"],
                          "statement": "Coverage is relative to the capture plan only; it does not prove the "
                                       "venue was captured completely."}
    report["issues"] = as_dicts(issues)
    report["ok"] = not errors_only(issues)
    return (manifest if files else None), report


def verify_manifest(manifest, base_dir):
    """Re-check an existing IF-01 manifest against the files on disk."""
    return validate_document(manifest, "IF-01", base_dir=base_dir, verify_files=True)
