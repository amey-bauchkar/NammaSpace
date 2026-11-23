"""namma.capture.polycam_report: the Polycam LiDAR capture report, on a synthetic export (no GLB, no RoomPlan)."""

import json

import numpy as np

from namma.capture import polycam_report as rpt
from tests.test_polycam_raw import write_export


def _project(tmp_path, **kw):
    d = tmp_path / "proj"
    (d / "capture" / "originals").mkdir(parents=True)
    write_export(d / "capture" / "originals" / "Images.zip", **kw)
    return d


def test_finds_the_raw_export(tmp_path):
    d = _project(tmp_path)
    assert rpt.find_raw_zip(d).name == "Images.zip"
    assert rpt.find_raw_zip(tmp_path / "nothing") is None


def test_report_files_for_the_studio(tmp_path):
    d = _project(tmp_path)
    rep = rpt.build_report(d)
    assert rep["status"] == "ready" and rep["summary"]["usable"] == 2
    ids = {c["id"]: c for c in rep["checks"]}
    assert ids["usable_keyframes"]["level"] == "ok"
    assert ids["depth_neighbours"]["level"] == "ok"              # flat wall, exact poses: depth agrees
    assert ids["glb_fit"]["level"] == "warning"                 # no GLB / mesh_info next to the export
    cap = json.loads((d / "capture" / "capture_report.json").read_text(encoding="utf-8"))
    assert cap["stats"]["mode"] == "polycam_lidar" and cap["stats"]["inputs_accepted"] == 2
    assert (d / "capture" / "polycam_report.json").exists()
    assert json.loads((d / "capture" / "capture_manifest.json").read_text(encoding="utf-8"))["mode"] == "polycam_lidar"


def test_report_never_mentions_the_location(tmp_path):
    d = _project(tmp_path)
    rpt.build_report(d)
    for f in ("polycam_report.json", "capture_report.json", "capture_manifest.json"):
        assert "secret street" not in (d / "capture" / f).read_text(encoding="utf-8")


def test_photo_flags():
    q = {"angular_velocity": 0.9, "blur_score": 50, "world_mapping_status": "limited", "feature_point_count": 10,
         "neighbors": 0}
    assert rpt.photo_flags(q, median_blur=100) == ["turned fast (possible motion blur)", "tracking limited",
                                                  "few tracking features", "not linked to other photos"]
    assert rpt.photo_flags({"angular_velocity": 0.9, "blur_score": 200, "neighbors": 3}, median_blur=100) == []
    assert rpt.photo_flags({"neighbors": 0}, median_blur=100, has_links=False) == []    # export without links


def test_surface_coverage_cells():
    s = rpt.Surface("floor", "floor", np.eye(4), 0, 1, 0, 1)       # 1 m x 1 m in x, y; z = normal
    P = np.c_[np.random.default_rng(0).random((5000, 2)) * [0.5, 1.0], np.zeros(5000)]   # left half measured
    s.add(P, np.array([0, 0, 1.0]))
    r = s.result()
    assert abs(r["covered"] - 0.5) < 0.01 and r["covered_within_2m"] == r["covered"]
    s.exclude_boxes([(np.array([[1, 0, 0, 0.75], [0, 0, 1, 0.5], [0, -1, 0, 0], [0, 0, 0, 1.0]]), [0.5, 1, 1.0])])
    assert s.result()["area_m2"] == 0.5 and s.result()["covered"] == 1.0    # right half under an object
