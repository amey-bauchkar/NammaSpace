"""Shared pytest fixtures. Everything here is SYNTHETIC (EXP-010 fixture)."""
import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from namma.fixtures import exp010  # noqa: E402
from namma.geometry.checks import run_geometry_checks  # noqa: E402
from namma.geometry.model import load_geometry  # noqa: E402
from namma.navigation.grid import build_navlayer  # noqa: E402
from namma.poi.endpoints import compute_endpoint  # noqa: E402

RES, RADIUS = 0.05, 0.25


@pytest.fixture(scope="session")
def root():
    return ROOT


@pytest.fixture(scope="session")
def truth():
    g, issues = load_geometry(exp010.geometry())
    assert g is not None, issues
    return g


@pytest.fixture(scope="session")
def nav(truth):
    return build_navlayer(truth, "F0", RES, RADIUS)


@pytest.fixture(scope="session")
def gcheck(truth, nav):
    report, _ = run_geometry_checks(exp010.geometry(), reference=exp010.reference_inventory(),
                                    alignment=exp010.alignment_observations(), navlayer=nav)
    assert report["acceptance"]["status"] == "accepted", report["acceptance"]
    return report


@pytest.fixture(scope="session")
def pois_with_endpoints(nav):
    pois = exp010.pois()
    for p in pois:
        compute_endpoint(p, nav, exp010.START_POINTS[0]["xz_m"])
    return pois


@pytest.fixture()
def pois(pois_with_endpoints):
    return copy.deepcopy(pois_with_endpoints)


@pytest.fixture(scope="session")
def proto(tmp_path_factory):
    """The ORIGINAL Phase 8 prototype, imported read-only (its module code writes only to a temp dir)."""
    out = tmp_path_factory.mktemp("proto_out")
    argv, no_pyc = sys.argv, sys.dont_write_bytecode
    sys.argv = ["nav_experiments.py", str(out)]
    sys.dont_write_bytecode = True  # never write __pycache__ into phase8_artifacts/
    try:
        spec = importlib.util.spec_from_file_location("nav_experiments_phase8",
                                                      ROOT / "phase8_artifacts" / "nav_experiments.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.argv, sys.dont_write_bytecode = argv, no_pyc
    return mod
