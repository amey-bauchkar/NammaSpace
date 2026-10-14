"""Freeze the evaluation configuration before held-out runs (PROTOCOL_PHASE13.md §6).

    python scripts/eval/freeze_config.py            # writes datasets/FROZEN_CONFIG.json, fills protocol §6
    python scripts/eval/freeze_config.py --verify   # exit 1 if the current code / config differ from the freeze

Recorded: every profile's configuration and SHA-256 (the value stored in each run's run.json), the pipeline
version, a SHA-256 over all pipeline / evaluation source files, and the SHA-256 of the protocol text.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from namma.studio.profiles import PIPELINE_VERSION, PROFILES, config_sha256, get_profile  # noqa: E402

CODE_GLOBS = ["src/namma/capture/*.py", "src/namma/recon/*.py", "src/namma/studio/*.py", "src/namma/evaluation/*.py",
              "scripts/eval/evaluate_run.py"]
FROZEN = ROOT / "datasets" / "FROZEN_CONFIG.json"
PROTOCOL = ROOT / "phase13_artifacts" / "PROTOCOL_PHASE13.md"


def code_digest():
    h = hashlib.sha256()
    files = sorted(p for g in CODE_GLOBS for p in ROOT.glob(g))
    for p in files:
        h.update(p.relative_to(ROOT).as_posix().encode())
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest(), [p.relative_to(ROOT).as_posix() for p in files]


def current():
    digest, files = code_digest()
    return {"pipeline_version": PIPELINE_VERSION,
            "profiles": {name: {"config_sha256": config_sha256(get_profile(name)), "config": get_profile(name)}
                         for name in PROFILES},
            "code_sha256": digest, "code_files": files}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    cur = current()
    if a.verify:
        frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
        same = (frozen["code_sha256"] == cur["code_sha256"] and all(
            frozen["profiles"][k]["config_sha256"] == v["config_sha256"] for k, v in cur["profiles"].items()))
        print("frozen configuration matches" if same else "CHANGED since the freeze")
        return 0 if same else 1
    marker = "## 6. Freeze record"
    text = PROTOCOL.read_text(encoding="utf-8")
    head = text[: text.index(marker)]
    rec = {"frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S+05:30"), **cur,
           "protocol_sha256_sections_1_5": hashlib.sha256(head.replace("\r\n", "\n").encode()).hexdigest()}
    FROZEN.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    lines = [marker, "", f"- Frozen at: {rec['frozen_at']}", f"- Pipeline version: {PIPELINE_VERSION}",
             f"- Code SHA-256 ({len(cur['code_files'])} files, see `datasets/FROZEN_CONFIG.json`): `{cur['code_sha256']}`"]
    for name, v in cur["profiles"].items():
        lines.append(f"- Profile `{name}` config SHA-256: `{v['config_sha256']}`")
    lines.append(f"- SHA-256 of sections 1–5 of this protocol: `{rec['protocol_sha256_sections_1_5']}`")
    lines.append("- Held-out runs must show these config SHA-256 values in their `run.json`; "
                 "`python scripts/eval/freeze_config.py --verify` must pass before each held-out run.")
    PROTOCOL.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
