// Python <-> JavaScript parity: search rules, route hashes, transforms.
import assert from 'node:assert/strict';
import path from 'node:path';
import { test } from 'node:test';
import * as THREE from '../vendor/three/three.module.js';
import { applyRowMajor, forwardFromAngles, lookAngles, angleBetween } from '../js/frames.js';
import { canonicalPolyline, routeSha256 } from '../js/hash.js';
import { osaDistance, search, tokens } from '../js/search.js';
import { ROOT, readJson } from './helpers.js';

const pois = readJson(ROOT, 'fixtures', 'exp010', 'pois_seed.json');

test('search: every hand-written case in fixtures/search_cases.json', () => {
  const { cases } = readJson(ROOT, 'fixtures', 'search_cases.json');
  assert.ok(cases.length >= 25);
  for (const c of cases) {
    const got = search(pois, c.query, c.include_unverified || false);
    assert.equal(got.status, c.status, JSON.stringify(c));
    assert.deepEqual(got.hits.map((h) => h.poi_id).sort(), [...c.poi_ids].sort(), JSON.stringify(c));
  }
});

test('search: tokeniser and OSA distance', () => {
  assert.deepEqual(tokens('  Meeting   Table! '), ['meeting', 'table']);
  assert.deepEqual(tokens('ಸಭೆ ಮೇಜು'), ['ಸಭೆ', 'ಮೇಜು']);
  assert.equal(osaDistance('chiar', 'chair'), 1);
  assert.equal(osaDistance('recepton', 'reception'), 1);
});

test('route hash: identical canonical string and SHA-256 to Python', async () => {
  const { vectors } = readJson(ROOT, 'fixtures', 'route_hash_vectors.json');
  for (const v of vectors) {
    assert.equal(canonicalPolyline(v.polyline), v.canonical);
    assert.equal(await routeSha256(v.polyline), v.sha256);
  }
});

test('transforms: row-major application equals Python; three.js Matrix4.set is row-major', () => {
  const { vectors } = readJson(ROOT, 'fixtures', 'transform_vectors.json');
  for (const v of vectors) {
    const m4 = new THREE.Matrix4().set(...v.matrix_row_major);
    v.points.forEach((p, i) => {
      const a = applyRowMajor(v.matrix_row_major, p);
      const b = new THREE.Vector3(...p).applyMatrix4(m4).toArray();
      for (let k = 0; k < 3; k++) {
        assert.ok(Math.abs(a[k] - v.expected[i][k]) < 1e-9, `applyRowMajor ${a} vs ${v.expected[i]}`);
        assert.ok(Math.abs(b[k] - v.expected[i][k]) < 1e-9, `three ${b} vs ${v.expected[i]}`);
      }
    });
  }
});

test('camera: lookAngles / forwardFromAngles agree with a three.js camera (YXZ)', () => {
  const cam = new THREE.PerspectiveCamera();
  for (const [pos, target] of [[[2.8, 1.6, 0.8], [2.8, 0.75, 2.4]], [[1, 1.6, 1], [5, 1.6, 1]], [[0, 2, 0], [-3, 0, -4]]]) {
    const { yaw, pitch } = lookAngles(pos, target);
    const want = target.map((v, i) => v - pos[i]);
    assert.ok(angleBetween(forwardFromAngles(yaw, pitch), want) < 1e-9);
    cam.position.set(...pos);
    cam.rotation.set(pitch, yaw, 0, 'YXZ');
    cam.updateMatrixWorld(true);
    const d = new THREE.Vector3();
    cam.getWorldDirection(d);
    assert.ok(angleBetween(d.toArray(), want) < 1e-6);
  }
});

test('three.js vendored version is r186', () => {
  assert.equal(THREE.REVISION, '186');
  assert.ok(path.isAbsolute(ROOT));
});
