"""The Phase 8 evidence must never be modified (master §13.P8.4 checksums)."""
import pytest

from namma.core.jsonio import sha256_file

EXPECTED = {
    "nav_experiments.py": "e5faa50932e71fc337caac87a064062e45b430298dbbaf6d48ef539f3cf9bcec",
    "results/exp005_browser_attempt.json": "104d068a6e172417b969dbb89eb36f25abd9cf5b99da054cd4fc0da5ab66acbf",
    "results/exp013_environment_audit.txt": "03e9df1a9b9d04c4ad58ec1b61c300434646236e01e1887a2d92580ab577aa57",
    "results/nav_results.json": "44e618d4b0172afa038ba227d58e3ed41f52c7ff5e918fa5ee501cf2de847459",
    "results/nav_results_run1_buggy_baseline.json": "56f9d3432652a7a4e83a213f944d7d1e6d486afdfa40f2552c5ef0f824f6dbc8",
    "results/run2_stdout.txt": "6f1a6ff3fd21457666d088ccdbeda89bf35b0c50a800f40d52a6cf2aa2db7a7e",
    "results/run2_summary.txt": "97821808ac0c555c283b99dd3e07ac23ac1fc9ae9bf3c6692edb054ce7418eb7",
}


@pytest.mark.parametrize("rel", sorted(EXPECTED))
def test_phase8_artifact_unchanged(root, rel):
    assert sha256_file(root / "phase8_artifacts" / rel) == EXPECTED[rel]


def test_no_new_files_in_phase8_results(root):
    names = {p.relative_to(root / "phase8_artifacts").as_posix() for p in (root / "phase8_artifacts").rglob("*")
             if p.is_file() and "__pycache__" not in p.parts}
    assert names == set(EXPECTED)
