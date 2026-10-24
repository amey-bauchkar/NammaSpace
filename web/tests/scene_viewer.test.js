// Phase 13 viewer logic (no browser needed): roaming grid, first-person controller, scene-package checks.
import assert from 'node:assert/strict';
import test from 'node:test';
import { canOccupyGrid, decodeRoamGrid, isAllowed, nearestAllowed } from '../js/roam.js';
import { checkScenePackage, PackageLoadError, formatLength } from '../js/scenePackage.js';
import { levelStartTarget, Walker } from '../js/walker.js';

// 10 x 10 grid, cell 0.1; allowed except column 7 (a wall) and the outer ring
function gridDoc() {
  const rows = [];
  for (let r = 0; r < 10; r++) {
    if (r === 0 || r === 9) rows.push([10, 0]);
    else rows.push([1, 6, 1, 1, 1]);        // not(0) allowed(1..6) not(7) allowed(8) not(9)
  }
  return { origin_xz: [0, 0], cell: 0.1, width: 10, height: 10, encoding: 'rle_rows_v1', value: 'allowed', rows };
}

test('roam grid decodes run lengths and answers occupancy', () => {
  const g = decodeRoamGrid(gridDoc());
  assert.equal(isAllowed(g, 0.35, 0.45), true);
  assert.equal(isAllowed(g, 0.75, 0.45), false);     // wall column
  assert.equal(isAllowed(g, 0.05, 0.45), false);     // outer ring
  assert.equal(isAllowed(g, -1, 0.45), false);       // outside the grid
  assert.equal(canOccupyGrid(g, 0.35, 0.45, 0.12), true);
  assert.equal(canOccupyGrid(g, 0.62, 0.45, 0.12), false); // disc touches the wall
  assert.deepEqual(nearestAllowed(g, 0.75, 0.45, 1).map((v) => +v.toFixed(2)), [0.65, 0.45]);
  assert.throws(() => decodeRoamGrid({ ...gridDoc(), rows: [[5]] }));
  const bad = gridDoc(); bad.rows[3] = [1, 6, 1, 3];
  assert.throws(() => decodeRoamGrid(bad), /overflows/);
});

function fakeCamera() {
  return { position: { x: 0, y: 0, z: 0, set(x, y, z) { this.x = x; this.y = y; this.z = z; } },
    rotation: { order: 'YXZ', set() {} }, updateMatrixWorld() {} };
}

test('walker walks, slides along walls and stays at eye height', () => {
  const g = decodeRoamGrid(gridDoc());
  const cam = fakeCamera();
  const w = new Walker(cam, { eyeHeight: 0.4, grid: g });
  w.radius = 0.05;
  w.setPose([0.3, 0.4, 0.45], [1.3, 0.4, 0.45]);     // facing +x toward the wall at x = 0.7
  w.keys.add('KeyW');
  for (let i = 0; i < 240; i++) w.update(1 / 60);
  assert.ok(cam.position.x < 0.7 - 0.05 + 1e-9, `stopped before the wall, x=${cam.position.x}`);
  assert.ok(cam.position.x > 0.55, `walked up to the wall, x=${cam.position.x}`);
  assert.equal(cam.position.y, 0.4);
  assert.ok(w.blockedMoves > 0);
  // fly mode ignores the grid and moves vertically
  w.keys.clear();
  w.setMode('fly');
  w.keys.add('KeyE');
  for (let i = 0; i < 30; i++) w.update(1 / 60);
  assert.ok(cam.position.y > 0.45);
  w.keys.clear();
  w.setMode('walk');
  assert.equal(cam.position.y, 0.4);
});

test('walker recovers a camera that starts outside the walkable area', () => {
  const g = decodeRoamGrid(gridDoc());
  const cam = fakeCamera();
  const w = new Walker(cam, { eyeHeight: 0.4, grid: g });
  w.setPose([0.75, 0.4, 0.45], [1, 0.4, 0.45]);
  assert.equal(w.recover(), true);
  assert.equal(isAllowed(g, cam.position.x, cam.position.z), true);
});

function pkg() {
  return {
    contract: 'IF-13', contract_version: '1.0.0', model_frame: { units: 'model', handedness: 'right', up_axis: '+Y' },
    scale: { status: 'unknown', metres_per_unit: null }, eye_height_units: 1.6,
    appearance: { to_model: { matrix_row_major: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] }, lods: [{ lod: 0, path: 'scene/lod0.glb' }] },
    start_view: {}, roam: 'nav/roam_grid.json', cameras: 'views/cameras.json', pois: 'poi/pois.json',
    files: ['scene/lod0.glb', 'nav/roam_grid.json', 'views/cameras.json', 'poi/pois.json'].map((path) => ({ path })),
  };
}

test('scene package structural checks refuse unsafe or inconsistent packages', () => {
  checkScenePackage(pkg());
  const cases = [
    (p) => { p.contract = 'IF-10'; },
    (p) => { p.model_frame.units = 'm'; },
    (p) => { p.scale.metres_per_unit = 0.5; },
    (p) => { p.eye_height_units = 0; },
    (p) => { p.appearance.to_model.matrix_row_major[3] = NaN; },
    (p) => { p.files = p.files.slice(1); },
    (p) => { p.appearance.lods[0].path = '../x.glb'; p.files.push({ path: '../x.glb' }); },
  ];
  for (const mutate of cases) {
    const p = pkg();
    mutate(p);
    assert.throws(() => checkScenePackage(p), PackageLoadError);
  }
});

test('imported phone-scan meshes carry a device_tracking scale (IF-13 1.2.0); other statuses stay refused', () => {
  const p = pkg();
  p.contract_version = '1.2.0';
  p.scale = { status: 'device_tracking', metres_per_unit: 1, method: 'Polycam export' };
  checkScenePackage(p);
  assert.equal(formatLength(2, p.scale), '2.00 m');
  p.scale.status = 'guessed';
  assert.throws(() => checkScenePackage(p), PackageLoadError);
});

test('lengths are labelled with their scale status', () => {
  assert.equal(formatLength(2, { status: 'unknown', metres_per_unit: null }), '2.00 model units (scale unknown)');
  assert.equal(formatLength(2, { status: 'user_distance', metres_per_unit: 0.5 }), '1.00 m');
});

test('start view keeps the photo heading but levels a steep pitch', () => {
  const eye = 2;
  const pitchOf = (p, tg) => Math.atan2(tg[1] - p[1], Math.hypot(tg[0] - p[0], tg[2] - p[2]));
  const up = levelStartTarget([0, 2, 0], [0, 12, -5], eye);          // photo looking steeply at the ceiling
  assert.ok(Math.abs(pitchOf([0, eye, 0], up) - 0.09) < 1e-9);
  assert.deepEqual([up[0], up[2]], [0, -5]);                           // heading unchanged
  const down = levelStartTarget([1, 2, 1], [1, -10, 2], eye);
  assert.ok(Math.abs(pitchOf([1, eye, 1], down) + 0.35) < 1e-9);
  const level = levelStartTarget([0, 2, 0], [3, 1.9, 4], eye);         // a gentle pitch is kept as is
  assert.ok(Math.abs(pitchOf([0, eye, 0], level) - Math.atan2(-0.1, 5)) < 1e-9);
  assert.deepEqual(levelStartTarget([0, 2, 0], [0, 9, 0], eye), [0, eye, -eye]);
});
