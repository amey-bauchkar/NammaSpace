"""Synthetic EXP-010 fixture as contract artifacts (WP-03).

Source: the manually specified 12 m x 6 m layout in
phase8_artifacts/nav_experiments.py (build_layout). Every artifact produced
here is marked `synthetic_fixture` / `is_test_asset: true`. None of it comes
from a smartphone capture or a reconstruction.

Differences from the Phase 8 layout (documented, deliberate):
- the closed room ("box minus box" in Phase 8) is expressed as four wall
  obstacles, because IF-04 footprints are single rings without holes;
- one overhead duct (min_y 2.1 m) is added to exercise the body band; it does
  not block walking and does not change any EXP-010 route.
"""
import copy

from namma import CONTRACT_VERSION
from namma.core.frames import IDENTITY_ROW_MAJOR

T_FIXTURE = "2026-10-10T12:00:00+05:30"   # fixed so fixture files are reproducible
W, H, WALL_T = 12.0, 6.0, 0.10
DOOR_CENTRE_Z = 2.95
FRAME = {"frame_id": "site-synthetic-exp010", "units": "m", "handedness": "right", "up_axis": "+Y",
         "datum": "SYNTHETIC: origin at the outer corner of the 12 m x 6 m test layout; +X along the 12 m "
                  "wall; +Z along the 6 m wall (+Z = +X cross +Y); +Y up. Not a physical venue."}
IDENT = {"matrix_row_major": list(IDENTITY_ROW_MAJOR), "source": "identity",
         "method": "synthetic fixture authored directly in the site frame", "residual_m": None}
PROV = {"origin": "synthetic_fixture",
        "method": "EXP-010 layout, manually specified (phase8_artifacts/nav_experiments.py build_layout)",
        "tool": "namma.fixtures.exp010", "tool_version": "1.0.0", "author_role": "SCRIPT",
        "created_at": T_FIXTURE}


def box(x0, z0, x1, z1):
    return [[x0, z0], [x1, z0], [x1, z1], [x0, z1]]


def _obstacle(oid, ring, kind, max_y, min_y=0.0, prov=PROV):
    return {"obstacle_id": oid, "floor_id": "F0", "footprint": ring, "min_y_m": min_y, "max_y_m": max_y,
            "kind": kind, "confidence": 1.0, "provenance": copy.deepcopy(prov)}


def obstacles(door_width=0.90, include_table=True, include_duct=True):
    lo, hi = DOOR_CENTRE_Z - door_width / 2, DOOR_CENTRE_Z + door_width / 2
    wall_h = 2.6
    obs = [
        _obstacle("outer_S", box(0, 0, W, WALL_T), "wall", wall_h),
        _obstacle("outer_N", box(0, H - WALL_T, W, H), "wall", wall_h),
        _obstacle("outer_W", box(0, 0, WALL_T, H), "wall", wall_h),
        _obstacle("outer_E", box(W - WALL_T, 0, W, H), "wall", wall_h),
        _obstacle("divider_0", box(6.0 - WALL_T / 2, 0.0, 6.0 + WALL_T / 2, lo), "wall", wall_h),
        _obstacle("divider_1", box(6.0 - WALL_T / 2, hi, 6.0 + WALL_T / 2, H), "wall", wall_h),
    ]
    if include_table:
        obs.append(_obstacle("table", box(2.0, 2.0, 3.6, 2.8), "furniture", 0.75))
    obs += [
        _obstacle("sofa", box(0.5, 4.5, 2.5, 5.4), "furniture", 0.85),
        _obstacle("cabinet_1", box(7.2, 3.6, 8.0, 5.9), "furniture", 1.9),
        _obstacle("cabinet_2", box(8.7, 3.6, 9.5, 5.9), "furniture", 1.9),
        _obstacle("cabinet_back", box(7.2, 5.4, 9.5, 5.9), "furniture", 1.9),
        _obstacle("closed_room_S", box(9.5, 0.5, 11.5, 0.6), "wall", wall_h),
        _obstacle("closed_room_N", box(9.5, 1.9, 11.5, 2.0), "wall", wall_h),
        _obstacle("closed_room_W", box(9.5, 0.6, 9.6, 1.9), "wall", wall_h),
        _obstacle("closed_room_E", box(11.4, 0.6, 11.5, 1.9), "wall", wall_h),
    ]
    if include_duct:
        obs.append(_obstacle("overhead_duct", box(4.0, 3.0, 5.0, 3.3), "other", 2.4, min_y=2.1))
    return obs


def geometry(geometry_id="geom-exp010-truth-v1", door_width=0.90, include_table=True, run_id="run-synthetic-exp010"):
    lo, hi = DOOR_CENTRE_Z - door_width / 2, DOOR_CENTRE_Z + door_width / 2
    return {"contract": "IF-04", "contract_version": CONTRACT_VERSION, "geometry_id": geometry_id,
            "run_id": run_id, "site_frame": copy.deepcopy(FRAME), "to_site": copy.deepcopy(IDENT),
            "floors": [{"floor_id": "F0", "elevation_m": 0.0, "walkable_regions": [box(0, 0, W, H)]}],
            "obstacles": obstacles(door_width, include_table),
            "doorways": [{"doorway_id": "D1", "floor_id": "F0", "segment": [[6.0, round(lo, 6)], [6.0, round(hi, 6)]],
                          "measured_width_m": door_width, "provenance": copy.deepcopy(PROV)}],
            "provenance": copy.deepcopy(PROV)}


def missing_table_geometry():
    """EXP-010 S8: the same layout with the meeting table omitted (simulated missing geometry)."""
    g = geometry("geom-exp010-missing-table-v1", include_table=False)
    g["provenance"]["note"] = "deliberately omits the table to test missing-obstacle handling (EXP-010 S8)"
    return g


def reference_inventory():
    """Independent reference for completeness checks (V-01, V-04, V-10).

    For the synthetic fixture the reference is the designed layout itself
    ("synthetic_truth"). At a real venue it would come from the S1 sketch and
    tape sheet - never from the geometry being checked.
    """
    return {
        "reference_type": "obstacle_inventory_v1", "reference_id": "ref-exp010-truth-v1",
        "frame_id": FRAME["frame_id"], "source": "synthetic_truth",
        "obstacles": [{"ref_id": o["obstacle_id"], "kind": o["kind"], "footprint": o["footprint"],
                       "tolerance_m": 0.10, "major": o["obstacle_id"] != "overhead_duct"}
                      for o in obstacles()],
        "doorways": [{"ref_id": "D1", "tape_width_m": 0.90, "required": True}],
        "required_points": [{"id": "room-a-sw", "xz_m": [1.0, 1.0]}, {"id": "room-a-n", "xz_m": [1.0, 4.0]},
                            {"id": "room-b", "xz_m": [9.0, 3.0]}, {"id": "alcove", "xz_m": [8.35, 5.0]}],
        "walk_pairs": [
            {"id": "S1", "start": [1.0, 1.0], "goal": [4.5, 1.2]},
            {"id": "S2", "start": [2.0, 3.6], "goal": [9.0, 3.0]},
            {"id": "S3", "start": [1.5, 2.4], "goal": [4.2, 2.4]},
            {"id": "S4", "start": [5.5, 1.0], "goal": [6.6, 5.5]},
            {"id": "S5", "start": [8.35, 3.0], "goal": [8.35, 5.2]}],
        "provenance": copy.deepcopy(PROV),
    }


def alignment_observations():
    """Tape / datum references for V-05 and V-06 (site_alignment_v1).

    `expected_site_m` comes from the tape plan; `observed_site_m` is where the
    mark appears in the artifact being checked (for this synthetic artifact the
    two coincide exactly; tests perturb `observed_site_m`).
    """
    marks = {"datum": [0.0, 0.0, 0.0], "x_mark": [12.0, 0.0, 0.0], "z_mark": [0.0, 0.0, 6.0],
             "far_corner": [12.0, 0.0, 6.0]}
    return {
        "reference_type": "site_alignment_v1", "reference_id": "align-exp010-v1", "frame_id": FRAME["frame_id"],
        "marks": [{"mark_id": k, "expected_site_m": v, "observed_site_m": list(v)} for k, v in marks.items()],
        "distances": [{"id": "M1", "mark_a": "datum", "mark_b": "x_mark", "tape_m": 12.0},
                      {"id": "M2", "mark_a": "datum", "mark_b": "z_mark", "tape_m": 6.0},
                      {"id": "M3", "mark_a": "x_mark", "mark_b": "far_corner", "tape_m": 6.0}],
        "datum_mark": "datum", "x_axis_mark": "x_mark",
        "floor_samples_observed_site_m": [[x, 0.0, z] for x in (0.5, 6.0, 11.5) for z in (0.5, 3.0, 5.5)],
        "provenance": copy.deepcopy(PROV),
    }


def _poi(poi_id, name, kind, marker, view_pos, room=None, aliases=(), category=None, verified=True):
    ver = ({"status": "verified", "by_role": "SCRIPT", "at": T_FIXTURE} if verified
           else {"status": "unverified"})
    prov = copy.deepcopy(PROV)
    prov["method"] = "POI from EXP-012 list (synthetic); 'verified' = fixture definition, not a site check"
    d = {"contract": "IF-09", "contract_version": CONTRACT_VERSION, "poi_id": poi_id, "display_name": name,
         "aliases": list(aliases), "kind": kind, "room_poi_id": room, "marker_site_m": list(marker),
         "frame_id": FRAME["frame_id"], "route_endpoint_xz_m": None, "endpoint_reachable": None,
         "view": {"position_site_m": list(view_pos), "target_site_m": list(marker)},
         "label_source": "human", "verification": ver, "provenance": prov}
    if category:
        d["category"] = category
    return d


def pois():
    """EXP-012 POIs (+1 unverified) with separate marker / endpoint / view (ARCH-008)."""
    return [
        _poi("poi-reception-desk", "Reception Desk", "zone", (1.2, 1.0, 1.0), (3.0, 1.6, 1.0), "poi-room-a",
             ("front desk",)),
        _poi("poi-meeting-table", "Meeting Table", "item", (2.8, 0.75, 2.4), (2.8, 1.6, 0.8), "poi-room-a",
             ("conference table", "ಸಭೆ ಮೇಜು"), "furniture"),
        _poi("poi-sofa", "Sofa", "item", (1.5, 0.45, 4.95), (1.5, 1.6, 3.4), "poi-room-a", ("couch",), "furniture"),
        _poi("poi-room-a", "Room A", "room", (3.0, 1.0, 3.5), (1.0, 1.6, 0.6)),
        _poi("poi-room-b", "Room B", "room", (9.0, 1.0, 3.0), (6.8, 1.6, 1.0)),
        _poi("poi-storage-alcove", "Storage Alcove", "zone", (8.35, 1.0, 5.0), (8.35, 1.6, 2.8), "poi-room-b"),
        _poi("poi-server-room", "Server Room", "room", (10.5, 1.0, 1.25), (10.5, 1.6, 3.2)),
        _poi("poi-chair", "Chair", "item", (4.5, 0.45, 1.5), (4.5, 1.6, 3.2), "poi-room-a", category="furniture"),
        _poi("poi-chair-2", "Chair", "item", (10.0, 0.45, 4.5), (10.0, 1.6, 2.6), "poi-room-b",
             category="furniture"),
        _poi("poi-projector-screen", "Projector Screen", "item", (3.0, 1.5, 5.85), (3.0, 1.6, 4.0), "poi-room-a",
             verified=False),
    ]


START_POINTS = [
    {"start_id": "start-a", "label": "Start A - Room A, south-west", "xz_m": [1.0, 1.0], "poi_id": None},
    {"start_id": "start-b", "label": "Start B - Room B, north-west", "xz_m": [6.6, 5.5], "poi_id": None},
]
