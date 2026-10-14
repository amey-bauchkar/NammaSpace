"""Write the synthetic fixtures and cross-language parity vectors to fixtures/.

Deterministic: running it twice produces identical files.
Run:  python scripts/make_fixtures.py
"""
import _bootstrap  # noqa: F401
from _bootstrap import ROOT

from namma.contracts.binding import canonical_polyline, route_sha256
from namma.core import frames
from namma.core.jsonio import write_json
from namma.fixtures import exp010

OUT = ROOT / "fixtures"


def main():
    d = OUT / "exp010"
    write_json(d / "geometry_truth.if04.json", exp010.geometry())
    write_json(d / "geometry_missing_table.if04.json", exp010.missing_table_geometry())
    write_json(d / "reference_inventory.json", exp010.reference_inventory())
    write_json(d / "alignment_observations.json", exp010.alignment_observations())
    write_json(d / "pois_seed.json", exp010.pois())
    write_json(d / "start_points.json", exp010.START_POINTS)

    # Only QUANTISED polylines (<= 6 decimals) are valid hash inputs; the planner guarantees it.
    # (A non-quantised value such as 0.0078125 is an exact binary tie that Python rounds half-even
    # and JavaScript toFixed rounds up; such routes would hash differently and be withheld.)
    polylines = [[[1.0, 1.0], [1.025, 1.025], [2.775, 1.675]],
                 [[0.0, -0.0], [-1.5, 2.25]],
                 [[0.007812, 3.000001], [12.0, 5.999999]],
                 [[123.456789, -0.000001], [0.1, 0.2]]]
    write_json(OUT / "route_hash_vectors.json", {
        "description": "Python reference values for web/tests/hash.test.js (namma.contracts.binding). "
                       "Inputs are quantised to 1e-6 m, as the contract requires.",
        "vectors": [{"polyline": p, "canonical": canonical_polyline(p), "sha256": route_sha256(p)} for p in polylines]})

    cases = [frames.IDENTITY_ROW_MAJOR, frames.make(translation=(6.0, 0.0, 3.0)),
             frames.make(translation=(1.0, 2.0, -3.0), yaw_deg=30.0),
             frames.make(scale=1.02, yaw_deg=-90.0), frames.make(tilt_x_deg=2.0, translation=(0.5, 0, 0))]
    pts = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.8, 0.75, 2.4], [-6.0, 1.6, -3.0]]
    write_json(OUT / "transform_vectors.json", {
        "description": "Python reference values (namma.core.frames.apply) for web/tests/frames.test.js.",
        "vectors": [{"matrix_row_major": m, "points": pts, "expected": frames.apply(m, pts).tolist()} for m in cases]})
    print("fixtures written to", OUT)


if __name__ == "__main__":
    main()
