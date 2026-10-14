"""WP-04: navigation grid construction, RLE integrity, resolution / clearance rules."""
import numpy as np
import pytest

from namma.contracts.semantic import validate_document
from namma.core.issues import errors_only
from namma.fixtures import exp010
from namma.geometry.model import load_geometry
from namma.navigation.grid import build_navlayer, decode_rle, encode_rle, from_doc


def test_rle_roundtrip(nav):
    doc = nav.to_doc()
    assert np.array_equal(decode_rle(doc["occupancy"], doc["width"], doc["height"]), nav.blocked)
    assert all(sum(r) == doc["width"] for r in doc["occupancy"]) and len(doc["occupancy"]) == doc["height"]
    back = from_doc(doc)
    assert np.array_equal(back.blocked, nav.blocked) and back.origin == nav.origin


@pytest.mark.parametrize("rows,w,h", [([[2, 3]], 6, 1), ([[6]], 6, 2), ([[2, 0, 4]], 6, 1), ([[]], 6, 1)])
def test_malformed_rle_rejected(rows, w, h):
    with pytest.raises(ValueError):
        decode_rle(rows, w, h)


def test_encode_starts_with_free_run():
    assert encode_rle(np.array([[True, True, False]])) == [[0, 2, 1]]
    assert encode_rle(np.array([[False, False]])) == [[2]]


def test_navlayer_doc_contract_valid(nav, truth):
    doc = nav.to_doc()
    assert not errors_only(validate_document(doc, "IF-05", geometry=truth))
    assert doc["rasterization"] == "conservative" and doc["geometry_sha256"] == truth.sha256
    assert (doc["width"], doc["height"], doc["origin_xz_m"]) == (240, 120, [0.0, 0.0])


def test_resolution_rules_enforced(truth):
    with pytest.raises(ValueError, match="ARCH-004 limit"):
        build_navlayer(truth, res=0.10)
    g = exp010.geometry()
    g["obstacles"].append({"obstacle_id": "thin_rail", "floor_id": "F0", "footprint": exp010.box(4, 4, 4.03, 5),
                           "min_y_m": 0, "max_y_m": 1.0, "kind": "other", "provenance": exp010.PROV})
    thin, _ = load_geometry(g)
    with pytest.raises(ValueError, match="thinnest obstacle"):
        build_navlayer(thin, res=0.05)
    doc = build_navlayer(truth).to_doc()
    doc["resolution_m"] = 0.08
    assert "IF05-RESOLUTION" in {i.code for i in validate_document(doc)}


def test_agent_clearance_must_be_explicit(truth):
    with pytest.raises(ValueError):
        build_navlayer(truth, agent_radius_m=0.0)
    with pytest.raises(ValueError):
        build_navlayer(truth, extra_margin_m=-0.1)


def test_inflation_grows_with_radius(truth):
    small = build_navlayer(truth, agent_radius_m=0.20)
    big = build_navlayer(truth, agent_radius_m=0.30)
    assert big.blocked.sum() > small.blocked.sum()
    assert np.all(big.blocked >= small.blocked)


def test_geometry_change_changes_navlayer_id(truth):
    other, _ = load_geometry(exp010.missing_table_geometry())
    assert build_navlayer(truth).navlayer_id != build_navlayer(other).navlayer_id


def test_cell_lookup_bounds(nav):
    assert nav.cell_of((0.0, 0.0)) == (0, 0)
    assert nav.cell_of((11.999, 5.999)) == (119, 239)
    assert nav.cell_of((-0.001, 1.0)) is None and nav.cell_of((12.0, 1.0)) is None


def test_components_are_4_connected(nav):
    labels = nav.components()
    a, b = nav.cell_of((1.0, 1.0)), nav.cell_of((10.5, 1.25))
    assert labels[a] != 0 and labels[b] != 0 and labels[a] != labels[b]  # closed room is separate
