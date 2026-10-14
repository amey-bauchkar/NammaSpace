"""Render datasets/registry.json + external_data/downloads.jsonl into datasets/DATASET_REGISTRY.md.

    python scripts/datasets/registry_report.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    reg = json.loads((ROOT / "datasets" / "registry.json").read_text(encoding="utf-8"))
    log = {}
    lp = ROOT / "external_data" / "downloads.jsonl"
    if lp.exists():
        for line in lp.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            if e.get("sha256"):
                log[e["url"]] = e
    L = ["# Dataset registry", "",
         f"Generated from `datasets/registry.json` (registry {reg['registry_version']}, updated {reg['updated']}) and the "
         "download log `external_data/downloads.jsonl`. Re-generate with `python scripts/datasets/registry_report.py`.", "",
         f"**Policy.** {reg['policy']}", "",
         "Download any scene (only registry-listed URLs are fetched; sizes and SHA-256 are verified):", "",
         "```bash", "python scripts/datasets/fetch_datasets.py --scene <scene_id> --extract", "```", ""]
    for ds in reg["datasets"]:
        L += [f"## {ds['name']}", "",
              "| Field | Value |", "|---|---|",
              f"| Official source | {ds['official_url']} |", f"| Licence | {ds['license']} |",
              f"| Licence source | {ds['license_source']} |", f"| Access | {ds['access']} |",
              f"| Modality | {ds['modality']} |", f"| Ground truth | {ds['ground_truth']} |",
              f"| Redistribution | {ds['redistribution']} |", f"| Citation | {ds['citation']} |", ""]
        L += ["| Scene | Role | Description | Files (bytes, SHA-256) | Intended experiments | Hardware | Limitations |",
              "|---|---|---|---|---|---|---|"]
        for sc in ds["scenes"]:
            files = "<br>".join(f"`{f['path']}` {f['bytes']:,} B, "
                                f"{(log.get(f['url']) or {}).get('sha256', f.get('sha256', 'not downloaded'))[:16]}…"
                                for f in sc["files"])
            L.append(f"| `{sc['scene_id']}` | {sc['role']} | {sc['description']} | {files} | "
                     f"{'; '.join(sc['intended_experiments'])} | {sc['hardware_feasibility']} | {sc['limitations']} |")
        L.append("")
    L += ["## Candidates investigated but not used", "", "| Dataset | Reason |", "|---|---|"]
    for c in reg["candidates_not_selected"]:
        L.append(f"| [{c['name']}]({c['url']}) | {c['reason']} |")
    L += ["", "## Development / held-out split", "",
          "Scenes marked `development` may be used to tune the pipeline. Scenes marked `heldout_test` are processed "
          "once with the frozen configuration recorded in `datasets/FROZEN_CONFIG.json`; they are never used for tuning.", ""]
    (ROOT / "datasets" / "DATASET_REGISTRY.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("wrote datasets/DATASET_REGISTRY.md")


if __name__ == "__main__":
    main()
