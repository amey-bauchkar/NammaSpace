# Independent physical measurement sheet (fill BEFORE looking at any reconstruction)

The tape measurements are the **ground truth**. They must come from the room, never from the 3-D model. Fill the CSV templates in `templates/` (one row per item) and keep a phone photo of this sheet as filled on paper.

## 1. Frame (ARCH-002 / ARCH-020)

- **Origin:** centre of marker **M0** on the floor (x = 0, z = 0, y = 0).
- **+X:** from M0 towards the centre of **M1** (place M1 along a straight wall).
- **+Y:** straight up.
- **+Z:** on the floor, **90° clockwise from +X when you look down from above**. This follows from the right-handed rule +Z = +X × +Y, so you cannot choose it freely.

**Practical rule:** stand on M0 facing M1. +Z points to your **right**.

| Field | Value |
|---|---|
| Room name / location | |
| Date, time | |
| Measured by (role) | |
| Checked by (second person) | |
| Tape type and length | |
| Estimated uncertainty | default ±0.01 m for ≤ 3 m; ±0.02 m beyond |

## 2. How to get (x, z) for any point — two tapes

For a point P on the floor:
1. measure **a = distance M0 → P**;
2. measure **b = distance M1 → P**;
3. record which side of the M0–M1 line P is on (**right = +z**, left = −z).

The converter `scripts/phase12/measurements_to_reference.py` computes x and z by trilateration. You never compute coordinates by hand. Measure from marker **centres** (tape cross) and along the floor.

## 3. Tables to fill (CSV files in `templates/`)

| File | One row per | Columns |
|---|---|---|
| `reference_points.csv` | marker or floor point | `point_id, description, d_from_M0_m, d_from_M1_m, side (+z / -z), height_m, uncertainty_m, notes` |
| `distances.csv` | independent check distance (≥ 6, including the room's length and width) | `id, from_point, to_point, tape_m, uncertainty_m, notes` |
| `room_outline.csv` | floor corner of the room, in walking order | `vertex_id, point_id, notes` (each vertex refers to a reference point) |
| `obstacles.csv` | piece of furniture, wall pier or column | `obstacle_id, kind (wall/furniture/glass/column/other), corner_point_ids (e.g. "P10;P11;P12;P13"), min_height_m, max_height_m, major (yes/no), notes` |
| `doorways.csv` | doorway | `doorway_id, side_a_point_id, side_b_point_id, tape_width_m, required (yes/no), leads_to, notes` |
| `walk_trials.csv` | route walked **after** routes are computed | `route_id, walked_by, contact (none / obstacle_id), blocked (yes/no), notes` |

`room_outline.csv` traces where the floor meets the walls.

## 4. Checks you do on paper (independence and quality)

- [ ] Every value comes from the tape, not from the model or any app.
- [ ] M0–M1 distance measured twice (difference ≤ 0.01 m).
- [ ] At least 6 check distances that are **not** used to place points (for example M2–M3, room width, table length).
- [ ] Furniture has not moved since the photos were taken.
- [ ] Glass, mirrors, low-hanging objects and anything unsafe are noted.
- [ ] Areas that could not be captured or measured are listed: ____________________
