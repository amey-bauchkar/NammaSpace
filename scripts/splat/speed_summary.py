"""Summarise the photoreal speed experiments (see master_context.md, "Photoreal speed experiments") into
docs/SPEED_EXPERIMENTS_RESULTS.md.

    python -I scripts/splat/speed_summary.py <experiment folder>

Each run <name>.json is written by train_splat.py when it finishes (held-out score on the same 22 photos, seconds).
Rule used to pick a new default: the fastest run within 0.1 dB (pose_colour PSNR) of r0, the current recipe.
"""
import json
import sys
from pathlib import Path

RUNS = {"r0": "current recipe: 30k steps (reference)", "r1": "30k steps, half-size photos for the first half",
        "r2": "15k steps, start from the Polycam mesh", "r3": "15k steps, normal start"}

folder = Path(sys.argv[1])
out = Path(__file__).resolve().parents[2] / "docs" / "SPEED_EXPERIMENTS_RESULTS.md"
rows, ref = [], None
for name, what in RUNS.items():
    f = folder / f"{name}.json"
    if not f.exists():
        rows.append(f"| {name} | {what} | not finished (see {name}.log) | | |")
        continue
    j = json.loads(f.read_text(encoding="utf-8"))
    h = j.get("heldout", {})
    if name == "r0":
        ref = h.get("pose_colour")
    rows.append(f"| {name} | {what} | {h.get('pose_colour', '?')} | {h.get('ssim', '?')} | {j['seconds'] / 60:.1f} |")
lines = ["# Photoreal speed experiments (New room, 22 held-out photos)", "",
         "| Run | Recipe | Held-out PSNR (dB, pose + colour fit) | SSIM | Training minutes |", "|---|---|---|---|---|", *rows, "",
         f"Rule: keep the fastest run within 0.1 dB of r0 ({ref} dB). Written by scripts/splat/speed_summary.py.", ""]
out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
print("\n".join(lines))
