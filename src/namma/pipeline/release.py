"""Local releases, promotion and rollback (WP-08, ARCH-014, ARCH-022).

Layout:
    releases/<release_id>/site/            immutable copy of the built site
    releases/<release_id>/release.if11.json
    releases/CURRENT                       id of the promoted release
    releases/history.jsonl                 promotions, refusals and rollbacks
    public/                                the locally served site (copy of CURRENT)

Rules:
- a release whose regression did not pass is recorded but NEVER promoted;
- promotion and rollback re-hash every artifact first and refuse on mismatch;
- rollback restores a previous LOCAL release; no remote host is touched
  (no deployment integration exists in Phase 11).
"""
import json
import shutil
from pathlib import Path

from namma import RELEASE_CONTRACT_VERSION
from namma.contracts.semantic import validate_document
from namma.core import timefmt
from namma.core.issues import error, errors_only
from namma.core.jsonio import load_strict, sha256_bytes, sha256_file, write_json, write_text_atomic


class ReleaseError(RuntimeError):
    pass


def artifact_list(site_dir):
    site_dir = Path(site_dir)
    out = []
    for p in sorted(site_dir.rglob("*")):
        if p.is_file():
            out.append({"path": p.relative_to(site_dir).as_posix(), "sha256": sha256_file(p),
                        "bytes": p.stat().st_size})
    return out


def tree_sha256(artifacts):
    lines = "".join(f"{a['path']}\t{a['sha256']}\t{a['bytes']}\n" for a in sorted(artifacts, key=lambda a: a["path"]))
    return sha256_bytes(lines.encode("utf-8"))


def _history(releases_dir, event):
    releases_dir.mkdir(parents=True, exist_ok=True)
    event = {"at": timefmt.now_iso(), **event}
    with open(releases_dir / "history.jsonl", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(event) + "\n")
    return event


def current_release(releases_dir):
    p = Path(releases_dir) / "CURRENT"
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def verify_release(releases_dir, release_id):
    """Schema + semantic + full re-hash of a stored release. Returns (manifest, issues)."""
    rdir = Path(releases_dir) / release_id
    mpath = rdir / "release.if11.json"
    if not mpath.is_file():
        raise ReleaseError(f"release {release_id!r} has no release.if11.json")
    manifest = load_strict(mpath)
    issues = validate_document(manifest, "IF-11", base_dir=rdir / "site", verify_files=True)
    listed = {a["path"] for a in manifest["artifacts"]}
    actual = {a["path"] for a in artifact_list(rdir / "site")}
    for extra in sorted(actual - listed):
        issues.append(error("IF11-UNLISTED-FILE", f"{extra!r} is in the release but not in artifacts[]"))
    return manifest, issues


def build_release(site_dir, releases_dir, release_id, regression, notes=(), previous_release_id=None):
    """Snapshot `site_dir` as a release. `regression` = {"passed", "suite_version", "failures", ...}."""
    releases_dir = Path(releases_dir)
    rdir = releases_dir / release_id
    if rdir.exists():
        raise ReleaseError(f"release {release_id!r} already exists (releases are immutable)")
    shutil.copytree(site_dir, rdir / "site")
    arts = artifact_list(rdir / "site")
    manifest = {"contract": "IF-11", "contract_version": RELEASE_CONTRACT_VERSION, "release_id": release_id,
                "built_at": timefmt.now_iso(), "artifacts": arts,
                "regression": dict(regression), "deploy_targets": [],
                "previous_release_id": previous_release_id if previous_release_id is not None
                else current_release(releases_dir),
                "source": {"vcs": "none", "tree_sha256": tree_sha256(arts)},
                "notes": ["local release; the project folder is not under version control (ARCH-022)",
                          *notes]}
    errs = errors_only(validate_document(manifest, "IF-11", base_dir=rdir / "site", verify_files=True))
    if errs:
        shutil.rmtree(rdir)
        raise ReleaseError("; ".join(f"{e.code}: {e.message}" for e in errs))
    write_json(rdir / "release.if11.json", manifest)
    return manifest


def _install(src_site, public_dir):
    public_dir = Path(public_dir)
    staging = public_dir.with_name(public_dir.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(src_site, staging)
    backup = public_dir.with_name(public_dir.name + ".previous")
    if backup.exists():
        shutil.rmtree(backup)
    if public_dir.exists():
        public_dir.rename(backup)
    staging.rename(public_dir)
    if backup.exists():
        shutil.rmtree(backup)


def promote(releases_dir, release_id, public_dir):
    releases_dir = Path(releases_dir)
    manifest, issues = verify_release(releases_dir, release_id)
    errs = errors_only(issues)
    if not manifest["regression"]["passed"]:
        _history(releases_dir, {"event": "promotion_refused", "release_id": release_id,
                                "reason": "regression did not pass"})
        raise ReleaseError(f"release {release_id!r}: regression did not pass; not promoted")
    if errs:
        _history(releases_dir, {"event": "promotion_refused", "release_id": release_id,
                                "reason": "; ".join(e.code for e in errs)})
        raise ReleaseError(f"release {release_id!r} failed verification: " + "; ".join(e.message for e in errs))
    _install(releases_dir / release_id / "site", public_dir)
    write_text_atomic(releases_dir / "CURRENT", release_id + "\n")
    _check_public(public_dir, manifest)
    return _history(releases_dir, {"event": "promoted", "release_id": release_id,
                                   "tree_sha256": manifest["source"]["tree_sha256"]})


def _check_public(public_dir, manifest):
    if tree_sha256(artifact_list(public_dir)) != manifest["source"]["tree_sha256"]:
        raise ReleaseError("installed site does not match the release tree hash")


def rollback(releases_dir, public_dir, to_release_id=None):
    """Restore a previous verified, regression-passed local release into public_dir."""
    releases_dir = Path(releases_dir)
    cur = current_release(releases_dir)
    target = to_release_id
    if target is None:
        if cur is None:
            raise ReleaseError("nothing promoted yet; nothing to roll back")
        target = load_strict(releases_dir / cur / "release.if11.json").get("previous_release_id")
        if not target:
            raise ReleaseError(f"release {cur!r} has no previous_release_id")
    if target == cur:
        raise ReleaseError(f"{target!r} is already current")
    manifest, issues = verify_release(releases_dir, target)
    errs = errors_only(issues)
    if errs or not manifest["regression"]["passed"]:
        _history(releases_dir, {"event": "rollback_refused", "from": cur, "to": target,
                                "reason": "; ".join(e.code for e in errs) or "regression did not pass"})
        raise ReleaseError(f"cannot roll back to {target!r}: release does not verify or did not pass regression")
    _install(releases_dir / target / "site", public_dir)
    write_text_atomic(releases_dir / "CURRENT", target + "\n")
    _check_public(public_dir, manifest)
    return _history(releases_dir, {"event": "rolled_back", "from": cur, "to": target,
                                   "tree_sha256": manifest["source"]["tree_sha256"]})
