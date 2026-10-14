"""M-03: POI creation, persistence, verification, search and endpoint handling."""
import json

import pytest

from namma.fixtures import exp010
from namma.geometry.model import load_geometry
from namma.navigation.grid import build_navlayer
from namma.navigation.planner import make_request, plan_route
from namma.poi.endpoints import compute_endpoint, is_stale, recompute_all
from namma.poi.search import osa_distance, search, tokens
from namma.poi.store import POIError, POIStore

FRAME = exp010.FRAME["frame_id"]


@pytest.fixture(scope="module")
def cases(root):
    return json.loads((root / "fixtures" / "search_cases.json").read_text(encoding="utf-8"))["cases"]


def test_search_cases(pois, cases):
    for c in cases:
        got = search(pois, c["query"], include_unverified=c.get("include_unverified", False))
        assert got["status"] == c["status"], c
        assert sorted(h["poi_id"] for h in got["hits"]) == sorted(c["poi_ids"]), c


def test_exp012_typo_now_found_but_documented(pois):
    """EXP-012 missed "recepton"; token-level fuzzy matching finds it (tier 1 = fuzzy)."""
    r = search(pois, "recepton")
    assert r["status"] == "unique" and r["tier"] == 1


def test_tokeniser_and_distance():
    assert tokens("  Meeting   Table! ") == ["meeting", "table"]
    assert tokens("ಸಭೆ ಮೇಜು") == ["ಸಭೆ", "ಮೇಜು"]
    assert osa_distance("chiar", "chair") == 1 and osa_distance("abc", "abc") == 0 and osa_distance("ab", "ba") == 1


def test_rejected_never_returned(pois):
    for p in pois:
        if p["poi_id"] == "poi-sofa":
            p["verification"] = {"status": "rejected", "by_role": "LEAD", "at": exp010.T_FIXTURE}
    assert search(pois, "sofa", include_unverified=True)["status"] == "not_found"


def test_create_persist_reload_and_stable_ids(tmp_path):
    store = POIStore(tmp_path / "pois.json", FRAME)
    a = store.create("Help Desk", "zone", (5.0, 1.0, 1.0), (5.0, 1.6, 2.5), author_role="NAV", aliases=["info"])
    b = store.create("Help Desk", "zone", (5.2, 1.0, 1.0), (5.2, 1.6, 2.5), author_role="NAV")
    assert (a["poi_id"], b["poi_id"]) == ("poi-help-desk", "poi-help-desk-2")
    store.save()
    back = POIStore.load(tmp_path / "pois.json", FRAME)
    assert [r["poi_id"] for r in back.records] == ["poi-help-desk", "poi-help-desk-2"]
    back.update("poi-help-desk", display_name="Information Desk")
    assert back.get("poi-help-desk")["display_name"] == "Information Desk"  # id unchanged on rename
    with pytest.raises(POIError):
        back.update("poi-help-desk", poi_id="poi-x")


def test_new_poi_is_unverified_and_hidden_until_second_person_verifies(tmp_path):
    store = POIStore(tmp_path / "pois.json", FRAME)
    p = store.create("Fire Exit", "entrance", (11.0, 1.0, 3.0), (10.0, 1.6, 3.0), author_role="NAV")
    assert p["verification"]["status"] == "unverified"
    assert search(store.records, "fire exit")["status"] == "not_found"
    with pytest.raises(POIError, match="second person"):
        store.verify(p["poi_id"], "NAV")
    store.verify(p["poi_id"], "LEAD")
    assert search(store.records, "fire exit")["status"] == "unique"


def test_moving_a_poi_resets_verification_and_endpoint(tmp_path, nav):
    store = POIStore(tmp_path / "pois.json", FRAME)
    store.records = exp010.pois()
    p = store.get("poi-meeting-table")
    compute_endpoint(p, nav, (1.0, 1.0))
    store.update("poi-meeting-table", marker_site_m=[3.0, 0.75, 2.4])
    assert p["verification"]["status"] == "unverified" and p["route_endpoint_xz_m"] is None
    assert p["provenance"]["origin"] == "human_corrected"


def test_invalid_records_rejected_on_load(tmp_path):
    bad = exp010.pois()
    bad[0]["frame_id"] = "some-other-frame"
    (tmp_path / "pois.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(POIError, match="IF09-FRAME"):
        POIStore.load(tmp_path / "pois.json", FRAME)
    dup = exp010.pois() + exp010.pois()[:1]
    (tmp_path / "pois.json").write_text(json.dumps(dup), encoding="utf-8")
    with pytest.raises(POIError, match="duplicate"):
        POIStore.load(tmp_path / "pois.json", FRAME)


# ------------------------------------------------------------------ endpoints
def test_marker_endpoint_view_are_distinct(pois):
    t = next(p for p in pois if p["poi_id"] == "poi-meeting-table")
    marker_xz = (t["marker_site_m"][0], t["marker_site_m"][2])
    assert t["route_endpoint_xz_m"] != list(marker_xz)
    assert 0.5 < t["endpoint_snap_m"] <= 1.0          # EXP-012 reported 0.7-0.8 m for in-furniture POIs
    assert t["view"]["position_site_m"] != t["marker_site_m"]


def test_unreachable_endpoint_flagged(pois):
    s = next(p for p in pois if p["poi_id"] == "poi-server-room")
    assert s["route_endpoint_xz_m"] is not None and s["endpoint_reachable"] is False


def test_endpoint_beyond_snap_limit(nav):
    p = exp010.pois()[1]
    p["marker_site_m"] = [6.0, 1.0, 1.0]   # inside the divider wall but inflated area is 0.3 m wide each side
    compute_endpoint(p, nav, (1.0, 1.0), max_snap_m=0.05)
    assert p["route_endpoint_xz_m"] is None and p["endpoint_reachable"] is False


def test_routing_to_unverified_or_stale_poi_is_refused(nav, pois):
    by_id = {p["poi_id"]: p for p in pois}
    res = plan_route(nav, make_request("rq-u", nav, (1.0, 1.0), poi_id="poi-projector-screen"), by_id)
    assert res["status"] == "error" and "unverified" in res["note"]
    by_id["poi-sofa"]["endpoint_navlayer_id"] = "nav-old"
    res = plan_route(nav, make_request("rq-s", nav, (1.0, 1.0), poi_id="poi-sofa"), by_id)
    assert res["status"] == "error" and "stale" in res["note"]


def test_stale_endpoints_after_geometry_update(pois):
    """Moving the table (a geometry update) makes every endpoint stale; recomputation fixes them."""
    g = exp010.geometry("geom-exp010-table-moved-v2")
    table = next(o for o in g["obstacles"] if o["obstacle_id"] == "table")
    table["footprint"] = exp010.box(2.0, 1.4, 3.6, 2.2)
    moved, _ = load_geometry(g)
    nav2 = build_navlayer(moved)
    assert all(is_stale(p, nav2) for p in pois if p["route_endpoint_xz_m"] is not None)
    from namma.contracts.semantic import validate_document
    t = next(p for p in pois if p["poi_id"] == "poi-meeting-table")
    codes = {i.code for i in validate_document(t, "IF-09", current_geometry_id=moved.geometry_id,
                                               current_navlayer_id=nav2.navlayer_id)}
    assert "IF09-ENDPOINT-STALE" in codes
    before = t["route_endpoint_xz_m"]
    changes = recompute_all(pois, nav2, (1.0, 1.0))
    assert len(changes) == 10 and not any(is_stale(p, nav2) for p in pois if p["route_endpoint_xz_m"])
    assert t["route_endpoint_xz_m"] != before and t["endpoint_geometry_id"] == moved.geometry_id
