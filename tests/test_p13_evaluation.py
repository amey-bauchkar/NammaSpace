"""Phase 13 evaluation metrics on SYNTHETIC inputs with known answers (the reported numbers depend on them)."""
import math

import numpy as np
import pytest

from namma.evaluation import gt, metrics
from namma.fixtures import exp010
from namma.geometry.model import load_geometry


def rot(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])


def test_align_cameras_recovers_scale_and_reports_errors():
    rng = np.random.default_rng(0)
    gt_c = {f"c{i}": rng.normal(size=3) * 3 for i in range(12)}
    s, R, t = 0.25, rot(40), np.array([1.0, 2.0, -3.0])
    est = {k: (v - t) @ R / s for k, v in gt_c.items()}          # inverse similarity
    est["c0"] = est["c0"] + np.array([0.4, 0, 0])                   # one bad camera (in est units = 0.1 m)
    a = metrics.align_cameras(est, gt_c)
    assert a["scale"] == pytest.approx(s, rel=0.05)
    assert a["position_error_m"]["max"] == pytest.approx(a["per_camera"]["c0"], abs=1e-4)
    assert a["position_error_m"]["median"] < 0.02
    assert metrics.align_cameras({"a": np.zeros(3)}, {"a": np.zeros(3)}) is None


def test_cloud_metrics_known_offset():
    rng = np.random.default_rng(1)
    ref = rng.uniform(0, 2, size=(40000, 3))
    ref[:, 1] = 0.0                                                  # a 2 x 2 m plane
    rec = ref[ref[:, 0] < 1.0] + np.array([0, 0.03, 0])             # the x < 1 half of the plane, lifted 3 cm
    m = metrics.cloud_metrics(rec, ref)
    assert m["accuracy_m"]["median"] == pytest.approx(0.03, abs=0.003)
    assert m["accuracy_at_5cm"] == pytest.approx(1.0)
    assert m["accuracy_at_2cm"] < 0.05
    assert m["completeness_at_5cm"] == pytest.approx(0.5, abs=0.06)  # only half the reference is covered


def test_floor_metrics_identity_and_missing_candidate():
    g, _ = load_geometry(exp010.geometry())
    same = metrics.floor_metrics(g, g)
    assert same["free_floor_iou"] == pytest.approx(1.0, abs=1e-6)
    none = metrics.floor_metrics(None, g)
    assert none["free_floor_iou"] == 0.0 and none["free_floor_recall"] == 0.0


def test_route_metrics_self_reference_and_no_candidate():
    g, _ = load_geometry(exp010.geometry())
    r = metrics.route_metrics(g, g, n_pairs=6)
    assert r["measurable"] and r["false_unreachable"] == 0 and r["collisions_with_reference_obstacles"] == 0
    assert r["success"] == r["candidate_routes"] > 0
    r0 = metrics.route_metrics(None, g, n_pairs=6)
    assert r0["candidate_routes"] == 0 and r0["false_unreachable"] == r0["pairs"]


def test_obstacle_omission_and_box_footprint():
    from shapely.geometry import box
    g, _ = load_geometry(exp010.geometry())
    obs = g.blocking_union("F0")
    inside = obs.representative_point()
    fps = [("present", box(inside.x - 0.05, inside.y - 0.05, inside.x + 0.05, inside.y + 0.05)),
           ("absent", box(100, 100, 101, 101))]
    o = metrics.obstacle_omission(g, fps)
    assert [r["omitted"] for r in o["rows"]] == [False, True] and o["omission_rate"] == 0.5
    b = {"centroid": np.array([1.0, 2.0, 0.5]), "axes_lengths": np.array([2.0, 1.0, 1.0]), "axes": np.eye(3)}
    corners = gt.box_corners_xy_footprint(b)
    assert corners[:, 0].min() == pytest.approx(0.0) and corners[:, 0].max() == pytest.approx(2.0)
    assert corners[:, 2].min() == pytest.approx(0.0) and corners[:, 2].max() == pytest.approx(1.0)


def test_interp_centres_and_mesh_sampling():
    t = np.array([0.0, 1.0, 2.0])
    C = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0]], float)
    out = gt.interp_centres(np.array([0.5, 1.5, 3.0]), t, C)
    assert np.allclose(out[0], [0.5, 0, 0]) and np.allclose(out[1], [1, 0.5, 0]) and np.isnan(out[2]).all()
    verts = np.array([[0, 0, 0], [1, 0, 0], [0, 0, 1], [1, 0, 1]], float)
    faces = np.array([[0, 1, 2], [1, 3, 2]])
    s = gt.sample_mesh(verts, faces, 5000)
    assert len(s) == 5000 and np.allclose(s[:, 1], 0) and s[:, 0].min() >= 0 and s[:, 0].max() <= 1
