// Viewer logic against the BUILT synthetic site (NAMMA_SITE_DIR, default dist/):
// release loading + integrity errors, the valid-display gate, request resolution, collision.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { canOccupy, collisionModel } from '../js/collision.js';
import { checkManifest, loadRelease, ReleaseLoadError } from '../js/manifest.js';
import { buildRouteRequest, resolveRequest } from '../js/requests.js';
import { displayDecision } from '../js/routeGate.js';
import { HAVE_SITE, ROOT, SITE, SITE_URL, readJson, siteFetch, siteRoutes } from './helpers.js';

const need = { skip: HAVE_SITE ? false : `no built site at ${SITE} (run scripts/build.py)` };

test('release loads and every file verifies', need, async () => {
  const r = await loadRelease(SITE_URL, siteFetch());
  assert.equal(r.manifest.contract, 'IF-10');
  assert.equal(r.routes.length, 19);
  assert.equal(r.pois.length, 10);
  assert.equal(r.assets[0].asset.format, 'glb');
  assert.equal(r.navlayers[0].geometry_id, r.geometry.geometry_id);
  assert.equal(r.geometryCheck.acceptance.status, 'accepted');
});

async function loadError(overlay) {
  try {
    await loadRelease(SITE_URL, siteFetch(overlay));
  } catch (e) {
    assert.ok(e instanceof ReleaseLoadError, String(e));
    return e;
  }
  assert.fail('expected a ReleaseLoadError');
}

test('missing referenced file -> clear error', need, async () => {
  const e = await loadError({ 'poi/pois.json': { status: 404 } });
  assert.equal(e.kind, 'missing_file');
  assert.match(e.message, /poi\/pois\.json/);
});

test('corrupted asset -> hash mismatch, not a silent fallback', need, async () => {
  const glbLen = readJson(SITE, 'data', 'viewer_manifest.json').files.find((f) => f.path === 'scene/scene.glb').bytes;
  const e = await loadError({ 'scene/scene.glb': { body: new Uint8Array(glbLen) } });
  assert.equal(e.kind, 'hash_mismatch');
});

test('truncated file -> size mismatch', need, async () => {
  const e = await loadError({ 'geometry/geometry.if04.json': { body: '{}' } });
  assert.equal(e.kind, 'size_mismatch');
});

test('invalid manifest JSON / wrong contract / missing manifest', need, async () => {
  assert.equal((await loadError({ 'viewer_manifest.json': { body: '{"contract": "IF-10",' } })).kind, 'invalid_json');
  assert.equal((await loadError({ 'viewer_manifest.json': { body: '{"contract": "IF-07", "contract_version": "1.1.0"}' } })).kind, 'contract_mismatch');
  assert.equal((await loadError({ 'viewer_manifest.json': { status: 404 } })).kind, 'missing_file');
  assert.equal((await loadError({ 'viewer_manifest.json': { status: 500 } })).kind, 'http_error');
});

test('manifest structural checks', () => {
  const m = readJson(ROOT, 'architecture', 'examples', 'IF-10_viewer_manifest.valid.json');
  checkManifest(m);
  assert.throws(() => checkManifest({ ...m, site_frame: { ...m.site_frame, up_axis: '+Z' } }), /\+Y up/);
  assert.throws(() => checkManifest({ ...m, route_reports: [] }), /length/);
  assert.throws(() => checkManifest({ ...m, contract_version: '2.0.0' }), /not a supported IF-10/);
});

test('display gate: valid / rejected / tampered / unbound / wrong geometry / no route', need, async () => {
  const routes = siteRoutes();
  const geomId = readJson(SITE, 'data', 'geometry', 'geometry.if04.json').geometry_id;
  const ok = routes.find((r) => r.result.request_id === 'rq-start-a-poi-meeting-table');
  assert.equal((await displayDecision(ok.result, ok.report, geomId)).state, 'valid');
  const demo = routes.find((r) => r.result.request_id === 'rq-demo-planned-on-missing-table');
  assert.equal((await displayDecision(demo.result, demo.report, geomId)).state, 'rejected');
  const tampered = structuredClone(ok.result);
  tampered.polyline_xz_m[1][0] += 0.25;
  assert.equal((await displayDecision(tampered, ok.report, geomId)).state, 'binding_mismatch');
  const unbound = structuredClone(ok.report);
  delete unbound.evidence;
  assert.equal((await displayDecision(ok.result, unbound, geomId)).state, 'unvalidated');
  assert.equal((await displayDecision(ok.result, null, geomId)).state, 'unvalidated');
  assert.equal((await displayDecision(ok.result, ok.report, 'geom-other')).state, 'binding_mismatch');
  const forged = { ...structuredClone(demo.result), display_as_valid: true, validation: { ...demo.result.validation, verdict: 'pass' } };
  assert.equal((await displayDecision(forged, demo.report, geomId)).state, 'rejected');
  const unreachable = routes.find((r) => r.result.status === 'unreachable');
  assert.equal((await displayDecision(unreachable.result, null, geomId)).state, 'no_route');
});

test('every packaged route: valid display only with a passing bound report', need, async () => {
  const geomId = readJson(SITE, 'data', 'geometry', 'geometry.if04.json').geometry_id;
  for (const r of siteRoutes()) {
    const d = await displayDecision(r.result, r.report, geomId);
    assert.equal(d.state === 'valid', r.result.display_as_valid === true && r.report?.verdict === 'pass', r.result.request_id);
  }
});

test('request resolution: found for packaged start + POI, unavailable otherwise', need, () => {
  const routes = siteRoutes();
  const nav = readJson(SITE, 'data', 'nav', 'navlayer.if05.json').navlayer_id;
  const found = resolveRequest(buildRouteRequest(nav, [1.0, 1.0], 'poi-meeting-table'), routes);
  assert.equal(found.status, 'found');
  assert.equal(found.route.result.request_id, 'rq-start-a-poi-meeting-table');
  assert.equal(resolveRequest(buildRouteRequest(nav, [1.2, 1.0], 'poi-meeting-table'), routes).status, 'unavailable');
  assert.equal(resolveRequest(buildRouteRequest('nav-other', [1.0, 1.0], 'poi-meeting-table'), routes).status, 'unavailable');
});

test('walking collision model from IF-04', () => {
  const g = readJson(ROOT, 'fixtures', 'exp010', 'geometry_truth.if04.json');
  const m = collisionModel(g);
  assert.ok(!m.blocking.some((b) => b.id === 'overhead_duct'));
  assert.equal(canOccupy(m, 1.0, 1.0, 0.2), true);
  assert.equal(canOccupy(m, 2.8, 2.4, 0.2), false);   // inside the table
  assert.equal(canOccupy(m, 2.8, 1.9, 0.2), false);   // 0.1 m from the table
  assert.equal(canOccupy(m, 6.0, 1.0, 0.2), false);   // in the divider wall
  assert.equal(canOccupy(m, 6.0, 2.95, 0.2), true);   // in the doorway centre
  assert.equal(canOccupy(m, -1.0, 1.0, 0.2), false);  // off the floor
});
