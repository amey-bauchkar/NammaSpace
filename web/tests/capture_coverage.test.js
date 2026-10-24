// Polycam-style coverage paint: orientation maths, two-level coverage (needs walking), step detection.
import test from 'node:test';
import assert from 'node:assert/strict';
import { AZ_CELLS, CELL_DEG, CoverageMap, StepDetector, cellOf, rotationMatrix, screenDirection } from '../js/captureLogic.js';

const near = (a, b, eps = 1e-6) => a.every((v, i) => Math.abs(v - b[i]) < eps);

test('upright phone (beta 90) looks at the horizon; alpha turns it; beta 0 looks at the floor', () => {
  assert.ok(near(screenDirection(rotationMatrix(0, 90, 0), 0, 0, 60, 60), [0, 1, 0]));        // north, level
  const west = screenDirection(rotationMatrix(90, 90, 0), 0, 0, 60, 60);                       // alpha +90 (ccw)
  assert.ok(near(west, [-1, 0, 0]), JSON.stringify(west));
  assert.ok(near(screenDirection(rotationMatrix(0, 0, 0), 0, 0, 60, 60), [0, 0, -1]));        // flat: floor
  const up = screenDirection(rotationMatrix(0, 90, 0), 0, 1, 60, 60);                          // top of the view
  assert.ok(Math.abs((Math.asin(up[2]) * 180) / Math.PI - 30) < 1e-6);                         // half the vfov above
});

test('cells: 5-degree azimuth / elevation grid', () => {
  assert.equal(cellOf([0, 1, 0]), 18 * AZ_CELLS + 0);
  assert.equal(cellOf([1, 0, 0]), 18 * AZ_CELLS + 90 / CELL_DEG);
  assert.equal(cellOf([0, 0, -1]) < AZ_CELLS, true);
});

test('spinning on the spot only half-covers; looking again after walking completes it', () => {
  const m = new CoverageMap();
  for (let a = 0; a < 360; a += 10) m.mark(rotationMatrix(a, 90, 0), 50, 65, 0);   // full turn, position 0
  const once = m.stats();
  assert.ok(once.seen > 50 && once.covered === 0, JSON.stringify(once));
  for (let a = 0; a < 360; a += 10) m.mark(rotationMatrix(a, 90, 0), 50, 65, 0);   // turning again: still position 0
  assert.equal(m.stats().covered, 0);
  for (let a = 0; a < 180; a += 10) m.mark(rotationMatrix(a, 90, 0), 50, 65, 1);   // after walking: position 1
  const after = m.stats();
  assert.ok(after.covered > 0.3 * after.seen && after.covered < after.seen, JSON.stringify(after));
  assert.equal(m.level(cellOf([0, 1, 0])), 2);
});

test('step detector counts a 2 Hz walk and ignores small jitter', () => {
  const s = new StepDetector();
  for (let t = 0; t < 5000; t += 20) s.update(Math.max(0, 2.2 * Math.sin((2 * Math.PI * 2 * t) / 1000)), t);
  assert.ok(s.steps >= 8 && s.steps <= 11, String(s.steps));
  const q = new StepDetector();
  for (let t = 0; t < 5000; t += 20) q.update(0.4 + 0.3 * Math.sin(t / 37), t);
  assert.equal(q.steps, 0);
});
