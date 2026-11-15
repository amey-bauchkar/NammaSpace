// Round 2: in-browser walking-path planner for published sites (no browser needed).
import assert from 'node:assert/strict';
import test from 'node:test';
import { decodeRoamGrid } from '../js/roam.js';
import { astar, clearanceCells, lineFree, planRoute, pointAlong, polylineLength, remainingFrom } from '../js/routePlan.js';
import { checkScenePackage } from '../js/scenePackage.js';

// 12 x 12 cells of 0.1: free interior, a wall in column 6 with a door at rows 9-10
function grid() {
  const rows = [];
  for (let r = 0; r < 12; r++) {
    if (r === 0 || r === 11) rows.push([12, 0]);
    else if (r === 9 || r === 10) rows.push([1, 10, 1]);
    else rows.push([1, 5, 1, 4, 1]);          // not(0) free(1..5) wall(6) free(7..10) not(11)
  }
  return decodeRoamGrid({ origin_xz: [0, 0], cell: 0.1, width: 12, height: 12, rows });
}
const free = (g, x, z) => g.allowed[Math.floor(z / g.cell) * g.width + Math.floor(x / g.cell)] === 1;

test('path goes through the door, never through the wall, and is shorter after smoothing', () => {
  const g = grid();
  const r = planRoute(g, [0.25, 0.25], [0.95, 0.25], { maxGoalSnap: 0.3 });
  assert.equal(r.status, 'route');
  assert.ok(r.polyline.length >= 3 && r.polyline.length <= 6);
  // every point sampled along the polyline lies in a free cell
  for (let d = 0; d <= r.length; d += 0.02) {
    const { point } = pointAlong(r.polyline, d);
    assert.ok(free(g, point[0], point[1]), `blocked at ${point}`);
  }
  assert.ok(r.polyline.some(([, z]) => z > 0.9), 'must pass the door rows');
  assert.ok(r.length > 1.4 && r.length < 2.2);          // around the wall, not straight across (0.7)
  assert.ok(Math.abs(polylineLength(r.polyline) - r.length) < 1e-9);
});

test('no corner cutting and straight segments are checked', () => {
  const g = grid();
  const at = (r, c) => r * g.width + c;
  assert.equal(lineFree(g, at(2, 2), at(2, 9)), false);      // across the wall
  assert.equal(lineFree(g, at(2, 2), at(8, 5)), true);
  // diagonal squeeze: block two cells so only a corner touches
  const h = grid();
  h.allowed.fill(0);
  h.allowed[at(3, 3)] = 1; h.allowed[at(4, 4)] = 1;
  assert.equal(astar(h, at(3, 3), at(4, 4)), null);
});

test('honest failure states', () => {
  const g = grid();
  assert.equal(planRoute(g, [0.05, 0.05], [0.95, 0.25], { maxGoalSnap: 0.3 }).status, 'start_blocked');
  const moved = planRoute(g, [0.05, 0.25], [0.95, 0.25], { maxGoalSnap: 0.3, maxStartMove: 0.3 });
  assert.equal(moved.status, 'route');
  assert.ok(moved.startMoved > 0.05);                         // reported, never silent
  assert.equal(planRoute(g, [0.25, 0.25], [5, 5], { maxGoalSnap: 0.3 }).status, 'goal_not_snappable');
  const closed = grid();
  for (const r of [9, 10]) closed.allowed[r * closed.width + 6] = 0;     // shut the door
  assert.equal(planRoute(closed, [0.25, 0.25], [0.95, 0.25], { maxGoalSnap: 0.3 }).status, 'unreachable');
  const snap = planRoute(g, [0.25, 0.25], [0.65, 0.25], { maxGoalSnap: 0.3 });   // label on the wall itself
  assert.equal(snap.status, 'route');
  assert.ok(snap.goalSnap > 0);
});

test('guidance helpers: distance remaining and off-path distance', () => {
  const poly = [[0, 0], [1, 0], [1, 1]];
  assert.ok(Math.abs(remainingFrom(poly, 0.5, 0).remaining - 1.5) < 1e-9);
  assert.ok(Math.abs(remainingFrom(poly, 1, 0.75).remaining - 0.25) < 1e-9);
  assert.ok(Math.abs(remainingFrom(poly, 0.5, 0.3).offPath - 0.3) < 1e-9);
  assert.deepEqual(pointAlong(poly, 1.5).point, [1, 0.5]);
  assert.deepEqual(pointAlong(poly, 99).point, [1, 1]);
});

test('a package that names a route grid must list the file', () => {
  const pkg = { contract: 'IF-13', contract_version: '1.2.0',
    model_frame: { units: 'model', handedness: 'right', up_axis: '+Y' }, scale: { status: 'unknown', metres_per_unit: null },
    appearance: { to_model: { matrix_row_major: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] }, lods: [{ lod: 0, path: 'scene/lod0.glb' }] },
    eye_height_units: 1.5, start_view: {}, roam: 'nav/roam_grid.json', cameras: 'views/cameras.json', pois: 'poi/pois.json',
    navigation: { status: 'unverified', note: '', route_grid: 'nav/route_grid.json' },
    files: ['scene/lod0.glb', 'nav/roam_grid.json', 'views/cameras.json', 'poi/pois.json'].map((path) => ({ path })) };
  assert.throws(() => checkScenePackage(pkg, 'p'), /route_grid\.json is not listed/);
  pkg.files.push({ path: 'nav/route_grid.json' });
  checkScenePackage(pkg, 'p');
});

// ---- path quality: obstacle-proximity cost (corridor centring)
// a corridor 40 cells long and 9 wide (rows 1..9 free), cell 0.1
function corridor() {
  const rows = [];
  for (let r = 0; r < 11; r++) rows.push(r === 0 || r === 10 ? [40, 0] : [1, 38, 1]);
  return decodeRoamGrid({ origin_xz: [0, 0], cell: 0.1, width: 40, height: 11, rows });
}

test('clearance map: distance to the nearest blocked cell, in cells', () => {
  const g = corridor(), d = clearanceCells(g);
  assert.equal(d[0], 0);                                   // blocked
  assert.equal(d[1 * 40 + 20], 1);                         // touching the wall
  assert.equal(d[5 * 40 + 20], 5);                         // middle of the corridor
  assert.equal(d[5 * 40 + 1], 1);                          // end wall
  assert.equal(clearanceCells(g), d);                      // cached
});

test('paths keep to the middle of a corridor instead of scraping along the wall', () => {
  const g = corridor();
  const a = [0.35, 0.15], b = [3.65, 0.15];                // both ends right beside the same wall (row 1)
  const plain = planRoute(g, a, b, { maxGoalSnap: 0.3, clearanceWeight: 0 });
  const centred = planRoute(g, a, b, { maxGoalSnap: 0.3 });
  assert.equal(plain.status, 'route');
  assert.equal(centred.status, 'route');
  const mid = (r) => pointAlong(r.polyline, r.length / 2).point[1];
  assert.ok(Math.abs(mid(plain) - 0.15) < 1e-9);           // shortest: straight along the wall
  assert.ok(mid(centred) > 0.4 && mid(centred) < 0.7, `mid z ${mid(centred)}`);   // near the centre line (0.55)
  assert.ok(centred.length >= plain.length && centred.length < 1.25 * plain.length);   // a small detour, real length reported
  assert.ok(centred.polyline.length <= 8);                 // still a few straight segments, no grid zig-zag
  for (let d = 0; d <= centred.length; d += 0.02) {
    const { point } = pointAlong(centred.polyline, d);
    assert.ok(free(g, point[0], point[1]), `blocked at ${point}`);
  }
  assert.ok(centred.minClearance >= g.cell);
});

test('open floor: the preferred path is the straight one', () => {
  const rows = [];
  for (let r = 0; r < 40; r++) rows.push([0, 40]);
  const g = decodeRoamGrid({ origin_xz: [0, 0], cell: 0.1, width: 40, height: 40, rows });
  const r = planRoute(g, [1.05, 1.05], [2.95, 2.55], { maxGoalSnap: 0.3 });
  assert.equal(r.status, 'route');
  assert.ok(r.length < 1.03 * Math.hypot(1.9, 1.5), `length ${r.length}`);
});
