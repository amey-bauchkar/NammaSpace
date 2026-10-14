"""Run log: IF-12 entries appended to <run_dir>/runlog.jsonl (WP-08).

Every entry is schema- and semantically validated BEFORE it is written; an
invalid entry raises instead of being logged.
"""
import json
from pathlib import Path

from namma import CONTRACT_VERSION
from namma.contracts.semantic import validate_document
from namma.core import timefmt
from namma.core.issues import errors_only
from namma.core.jsonio import loads_strict

STAGE_LABELS = {"S0": "Readiness", "S1": "Reconnaissance", "S2": "Capture", "S3": "Capture gate",
                "S4": "Reconstruction", "S5": "Geometry validation", "S6": "Route verification",
                "S7": "POIs", "S8": "Deploy + browser check", "S9": "Acceptance"}


class RunLogError(ValueError):
    pass


class RunLog:
    def __init__(self, run_dir, run_id):
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.path = self.run_dir / "runlog.jsonl"

    def append(self, stage, status, output_dir, *, gate=None, gate_result=None, note=None, input_dirs=None,
               errors=None, manual_minutes=None, manual_intervention=False):
        entry = {"contract": "IF-12", "contract_version": CONTRACT_VERSION, "run_id": self.run_id,
                 "stage": stage, "status": status, "at": timefmt.now_iso(), "output_dir": str(output_dir),
                 "manual_intervention": manual_intervention}
        for k, v in (("gate", gate), ("gate_result", gate_result), ("note", note), ("input_dirs", input_dirs),
                     ("errors", errors), ("manual_minutes", manual_minutes)):
            if v is not None:
                entry[k] = v
        errs = errors_only(validate_document(entry, "IF-12"))
        if errs:
            raise RunLogError("; ".join(f"{e.code}: {e.message}" for e in errs))
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")
        return entry

    def entries(self):
        if not self.path.exists():
            return []
        return [loads_strict(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def latest_passed(self, stage):
        """IF-12 rule: the latest valid output of a stage is its newest `passed` entry."""
        passed = [e for e in self.entries() if e["stage"] == stage and e["status"] == "passed"]
        return passed[-1] if passed else None
