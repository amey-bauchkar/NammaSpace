"""Model health checks (namma.studio.model_health) on synthetic meshes and splat positions."""
import numpy as np

from namma.studio import model_health as mh
from namma.studio.splat_large import LARGE_MAX_INIT_POINTS, MESH_INIT_POINTS, init_point_count


def cube():
    V = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float)
    F = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                  [2, 3, 7], [2, 7, 6], [1, 2, 6], [1, 6, 5], [0, 4, 7], [0, 7, 3]])
    return V, F


def test_closed_consistent_cube_is_healthy():
    h = mh.mesh_health(*cube())
    assert h["holes"] == 0 and h["boundary_edges"] == 0 and h["non_manifold_edges"] == 0
    assert h["flipped_edge_share"] == 0 and h["degenerate_faces"] == 0


def test_hole_flip_non_manifold_and_degenerate_are_counted():
    V, F = cube()
    h = mh.mesh_health(V, F[2:])                                  # bottom face removed: one 4 m square hole
    assert h["holes"] == 1 and abs(h["largest_hole_perimeter_m"] - 4.0) < 1e-9 and h["large_holes"] == 0
    G = F.copy()
    G[4] = G[4][::-1]                                             # one triangle wound the other way
    assert mh.mesh_health(V, G)["flipped_edge_share"] > 0
    V2 = np.vstack([V, [[0.5, 2.0, 0.5]]])
    h = mh.mesh_health(V2, np.vstack([F, [[2, 3, 8]]]))           # a fin on edge 2-3: three triangles share it
    assert h["non_manifold_edges"] == 1
    h = mh.mesh_health(V, np.vstack([F, [[0, 0, 1]]]))
    assert h["degenerate_faces"] == 1
    # texture-seam duplicates (same position, different vertex) are welded: still closed
    Vd = np.vstack([V, V[0:1]])
    Fd = F.copy()
    Fd[0, 0] = 8
    assert mh.mesh_health(Vd, Fd)["holes"] == 0


def test_large_open_side_gives_recapture_guidance_and_pipeline_gaps_are_named():
    a = {"large_holes": 1, "largest_hole_perimeter_m": 12.0, "hole_perimeter_total_m": 14.0, "triangles": 1000,
         "non_manifold_edges": 0, "flipped_edge_share": 0.0}
    codes = {w["code"]: w for w in mh.guidance(a, before={"hole_perimeter_total_m": 12.0})}
    assert codes["LARGE-OPEN-AREAS"]["cause"] == "capture" and "Recapture" in codes["LARGE-OPEN-AREAS"]["message"]
    assert codes["PIPELINE-ADDED-OPENINGS"]["cause"] == "pipeline"
    assert all(w["severity"] == "info" for w in codes.values())   # never changes the verdict


def test_splat_density_per_block():
    rng = np.random.default_rng(0)
    room = np.c_[rng.uniform(0, 4, 16000), rng.uniform(0, 2.5, 16000), rng.uniform(0, 4, 16000)]   # 1000 / m2
    hall = np.c_[rng.uniform(4, 12, 6400), rng.uniform(0, 2.5, 6400), rng.uniform(0, 4, 6400)]     # 200 / m2
    d = mh.splat_density(np.vstack([room, hall]), block_m=2.0)
    assert d["blocks"] == 12
    assert 150 < d["per_m2_median"] < 260                          # 4 room blocks at ~1000, 8 hall blocks at ~200
    assert 150 < d["per_m2_min"] < 260 and d["worst_block_centre_xz"][0] > 4


def test_large_captures_get_more_start_points_rooms_do_not():
    assert init_point_count(120, 500.0) == MESH_INIT_POINTS                 # a room: unchanged
    assert init_point_count(300, 100.0) == 400_000                          # 4,000 per m2
    assert init_point_count(300, 1000.0) == LARGE_MAX_INIT_POINTS           # bounded


def test_verify_quality_script_reads_a_synthetic_project(tmp_path):
    import importlib.util
    import json
    import sys
    from pathlib import Path

    from namma.studio import mesh_import
    from test_mesh_import import box_room
    box_room(tmp_path / "scan.glb")
    mesh_import.import_mesh(tmp_path / "scan.glb", tmp_path / "projects", title="Box", source_note="synthetic", log=None)
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location("verify_quality", scripts / "verify_quality.py")
    vq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vq)
    out = tmp_path / "q.md"
    assert vq.main(["--projects", str(tmp_path / "projects"), "--out", str(out), "--save-baseline", str(tmp_path / "b.json")]) == 0
    rows = json.loads((tmp_path / "b.json").read_text())
    (r,) = rows.values()
    assert r["holes"] is not None and r["mirror_regions"] == 0 and "| holes |" in out.read_text()
    assert vq.main(["--projects", str(tmp_path / "projects"), "--out", str(out), "--baseline", str(tmp_path / "b.json")]) == 0
    assert "| holes |" in out.read_text() and "before" in out.read_text()


def test_room_geometry_measures_bent_walls_and_skewed_corners(tmp_path):
    from namma.studio.mesh_import import mesh_triangles
    from test_mesh_import import box_room
    V = box_room(tmp_path / "r.glb", offset=(0, 0, 0), table=False)
    V0, F0, _ = mesh_triangles((tmp_path / "r.glb").read_bytes())
    g = mh.room_geometry(V0, F0)
    assert abs(g["ceiling_height_m"] - 2.8) < 0.02 and g["floor_rms_m"] < 1e-3 and g["corner_deviation_deg"] < 0.5
    assert len(g["walls"]) == 4 and max(w["p95_m"] for w in g["walls"]) < 1e-3
    # bend the wall x = 5 inwards by up to 6 cm in the middle (drift-warped scan)
    Vb = V0.copy()
    on = np.isclose(Vb[:, 0], Vb[:, 0].max())
    Vb[on, 0] -= 0.06 * np.sin(np.pi * (Vb[on, 2] - Vb[:, 2].min()) / np.ptp(Vb[:, 2]))
    gb = mh.room_geometry(Vb, F0)
    assert max(w["p95_m"] for w in gb["walls"]) > 0.02
