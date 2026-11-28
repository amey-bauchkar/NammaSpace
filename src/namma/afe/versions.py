"""Immutable private regional version records and serialised reversible selection."""
from pathlib import Path
import shutil

from namma.core.jsonio import load_strict,sha256_file,write_json
from namma.afe.controller import decision


def register(root, candidate, evaluation):
    root, candidate = Path(root), Path(candidate)
    result = decision(evaluation["metrics"],evaluation["thresholds"],stage=evaluation.get("stage","development"))
    if evaluation.get("candidate_sha256")!=sha256_file(candidate):
        raise ValueError("evaluation belongs to a different candidate")
    # Every attempt is retained even when rejected. It never alters the source checkpoint.
    root.mkdir(parents=True,exist_ok=True)
    version = root/evaluation["candidate_sha256"]
    version.mkdir(exist_ok=True)
    retained = version/"candidate.ply"
    if (version/"ledger.json").exists():
        old = load_strict(version/"ledger.json")
        if old["evaluation"]!=evaluation or sha256_file(retained)!=evaluation["candidate_sha256"]:
            raise ValueError("version already registered with different evidence or content")
        return version
    if not retained.exists():
        with candidate.open("rb") as source, retained.open("xb") as destination:
            shutil.copyfileobj(source,destination)
    if sha256_file(retained)!=evaluation["candidate_sha256"]:
        raise ValueError("retained candidate differs from evaluation")
    write_json(version/"ledger.json",{"candidate":str(retained.resolve()),"evaluation":evaluation,"decision":result})
    return version


def select(root, version, *, expected_current=None):
    root, version = Path(root), Path(version).resolve()
    if version.parent!=root.resolve():
        raise ValueError("version outside the private registry")
    ledger = load_strict(version/"ledger.json")
    if decision(ledger["evaluation"]["metrics"],ledger["evaluation"]["thresholds"],
                stage=ledger["evaluation"].get("stage","development"))["accepted"] is not True:
        raise ValueError("candidate has not passed acceptance")
    if sha256_file(ledger["candidate"])!=ledger["evaluation"]["candidate_sha256"]:
        raise ValueError("candidate digest changed")
    lock = root/".selection-lock"
    lock.mkdir()
    try:
        pointer = root/"current.json"
        current = load_strict(pointer) if pointer.exists() else {"version":None,"history":[]}
        if current["version"]!=expected_current:
            raise ValueError("active version changed concurrently")
        write_json(pointer,{"version":version.name,"history":current["history"]+[current["version"]]})
    finally:
        lock.rmdir()


def rollback(root, *, expected_current):
    root = Path(root)
    lock = root/".selection-lock"
    lock.mkdir()
    try:
        pointer = root/"current.json"
        current = load_strict(pointer)
        if current["version"]!=expected_current or not current["history"]:
            raise ValueError("rollback state mismatch")
        target = current["history"][-1]
        if target is not None:
            old = load_strict(root/target/"ledger.json")
            if (sha256_file(old["candidate"])!=old["evaluation"]["candidate_sha256"] or
                    decision(old["evaluation"]["metrics"],old["evaluation"]["thresholds"],
                             stage=old["evaluation"].get("stage","development"))["accepted"] is not True):
                raise ValueError("rollback version failed integrity or acceptance")
        write_json(pointer,{"version":current["history"][-1],"history":current["history"][:-1]})
    finally:
        lock.rmdir()
