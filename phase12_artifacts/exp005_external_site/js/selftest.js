// In-page self-test (M-02 / M-04 browser evidence). Open index.html?selftest=1.
// Results: window.__nammaSelftest and the "Self-test" panel. The test does not
// rely on requestAnimationFrame (hidden tabs suspend it): it renders and steps
// the controller explicitly.
import * as THREE from 'three';
import { AppearanceUnsupported, loadAppearance } from './appearance.js';
import { angleBetween, forwardFromAngles } from './frames.js';
import { resolveRequest, buildRouteRequest } from './requests.js';
import { displayDecision } from './routeGate.js';
import { search } from './search.js';

const TOL_M = 0.01;

export async function runSelftest(app) {
  const { viewer, release } = app;
  const results = [];
  const check = async (id, name, fn) => {
    try {
      const detail = await fn();
      results.push({ id, name, pass: true, detail: detail ?? '' });
    } catch (e) {
      results.push({ id, name, pass: false, detail: String(e && e.message ? e.message : e) });
    }
  };
  const assert = (cond, msg) => { if (!cond) throw new Error(msg); };
  const geometry = release.geometry;
  const accepted = geometry.geometry_id;

  await check('A-01', 'Release, manifest and notices loaded', () => {
    assert(release.manifest.contract === 'IF-10', 'manifest is not IF-10');
    const shown = [...document.querySelectorAll('#notices li')].map((li) => li.textContent);
    assert(release.manifest.notices.every((n) => shown.includes(n)), 'a manifest notice is not displayed');
    return `${release.manifest.release_id}; ${release.routes.length} packaged routes; ${release.pois.length} POIs`;
  });

  await check('A-02', 'Appearance loaded through loadAppearance() with to_site applied once', () => {
    const root = viewer.appearance;
    assert(root && root.userData.toSiteApplied === 1, 'appearance root missing or to_site count != 1');
    assert(root.userData.isTestAsset === true, 'test asset flag not carried');
    let meshes = 0;
    root.traverse((o) => { if (o.isMesh) meshes += 1; });
    assert(meshes > 0, 'no meshes');
    return `${meshes} meshes; scene ${root.userData.sceneId}`;
  });

  await check('A-03', 'Appearance nodes align with IF-04 footprints (site frame, <= 0.01 m)', () => {
    let worst = 0, n = 0;
    const box = new THREE.Box3();
    for (const o of geometry.obstacles) {
      let node = null;
      viewer.appearance.traverse((x) => { if (x.userData && x.userData.feature_id === o.obstacle_id) node = x; });
      assert(node, `appearance node for ${o.obstacle_id} missing`);
      box.setFromObject(node);
      const xs = o.footprint.map((p) => p[0]), zs = o.footprint.map((p) => p[1]);
      const d = [box.min.x - Math.min(...xs), box.max.x - Math.max(...xs), box.min.z - Math.min(...zs),
        box.max.z - Math.max(...zs), box.min.y - o.min_y_m, box.max.y - o.max_y_m].map(Math.abs);
      worst = Math.max(worst, ...d);
      n += 1;
    }
    assert(worst <= TOL_M, `max deviation ${worst.toFixed(4)} m`);
    return `${n} obstacle nodes; max deviation ${worst.toExponential(2)} m`;
  });

  await check('A-04', 'POI markers at marker_site_m (<= 0.01 m)', () => {
    let worst = 0, n = 0;
    const v = new THREE.Vector3();
    for (const p of release.pois.filter((q) => q.verification.status === 'verified')) {
      const m = viewer.markers.getObjectByName(`poi:${p.poi_id}`);
      assert(m, `marker for ${p.poi_id} missing`);
      m.getWorldPosition(v);
      worst = Math.max(worst, v.distanceTo(new THREE.Vector3(...p.marker_site_m)));
      n += 1;
    }
    assert(!viewer.markers.getObjectByName('poi:poi-projector-screen'), 'unverified POI shown as a marker');
    assert(worst <= TOL_M, `max marker error ${worst} m`);
    return `${n} verified markers; max error ${worst.toExponential(2)} m; unverified POI hidden`;
  });

  await check('A-05', 'Search "Meeting Table" finds exactly poi-meeting-table', () => {
    const r = search(release.pois, 'Meeting Table');
    assert(r.status === 'unique' && r.hits[0].poi_id === 'poi-meeting-table', JSON.stringify(r));
    app.selectPoi('poi-meeting-table');
    assert(document.getElementById('poi-title').textContent.includes('Meeting Table'), 'POI panel not updated');
    return 'unique hit; POI panel shows it';
  });

  await check('A-06', 'Jump to POI view: camera pose equals IF-09 view (M-04)', () => {
    const p = release.pois.find((q) => q.poi_id === 'poi-meeting-table');
    app.jumpToSelected();
    const pos = viewer.camera.position.toArray();
    const dp = Math.hypot(...pos.map((v, i) => v - p.view.position_site_m[i]));
    const want = p.view.target_site_m.map((v, i) => v - p.view.position_site_m[i]);
    const ang = angleBetween(forwardFromAngles(viewer.controller.yaw, viewer.controller.pitch), want);
    const camDir = new THREE.Vector3(); viewer.camera.getWorldDirection(camDir);
    const ang3 = angleBetween(camDir.toArray(), want);
    assert(dp <= 1e-9, `camera position error ${dp}`);
    assert(ang <= 1e-6 && ang3 <= 1e-5, `view direction error ${ang} / ${ang3} rad`);
    return `position error ${dp.toExponential(1)} m; direction error ${ang3.toExponential(1)} rad`;
  });

  await check('A-07', 'Route request start-a -> Meeting Table: validated and drawn as valid', async () => {
    const out = await app.requestRoute('start-a');
    assert(out.state === 'valid', `gate state ${out.state}: ${out.reasons.join('; ')}`);
    const drawn = viewer.routeGroup.children[0];
    assert(drawn && drawn.userData.style === 'valid', 'route not drawn with valid style');
    assert(document.getElementById('route-status').dataset.state === 'valid', 'status panel not "valid"');
    return `${out.result.length_m.toFixed(2)} m; report ${out.report.report_id}`;
  });

  await check('A-08', 'Rejected demo route is never shown as valid', async () => {
    const out = await app.showDemoRejected();
    assert(out.state === 'rejected', `gate state ${out.state}`);
    assert(!viewer.routeGroup.children.some((c) => c.userData.style === 'valid'), 'a valid-styled route is drawn');
    return `state rejected; validator: intersects_obstacle=${out.report.checks.intersects_obstacle}`;
  });

  await check('A-09', 'Tampered route (polyline edited after validation) is withheld', async () => {
    const r = release.routes.find((x) => x.result.request_id === 'rq-start-a-poi-meeting-table');
    const tampered = structuredClone(r.result);
    tampered.polyline_xz_m[1][0] += 0.5;
    const d = await displayDecision(tampered, r.report, accepted);
    assert(d.state === 'binding_mismatch', `state ${d.state}`);
    return d.reasons[0];
  });

  await check('A-10', 'Route from the current position is reported unavailable (no live planner)', () => {
    const navId = release.navlayers[0].navlayer_id;
    const req = buildRouteRequest(navId, [viewer.camera.position.x + 0.123, viewer.camera.position.z], 'poi-meeting-table');
    const res = resolveRequest(req, release.routes);
    assert(res.status === 'unavailable', res.status);
    return res.reason;
  });

  await check('A-11', 'Unreachable POI (closed Server Room) reports unreachable and draws nothing', async () => {
    app.selectPoi('poi-server-room');
    const out = await app.requestRoute('start-a');
    assert(out.result, `no packaged result (${out.state}: ${out.reasons})`);
    assert(out.result.status === 'unreachable', `status ${out.result.status}`);
    assert(viewer.routeGroup.children.length === 0, 'something was drawn');
    return out.result.note;
  });

  await check('A-12', 'WASD keyboard movement with collision', () => {
    const c = viewer.controller;
    c.setPose([1.0, 1.6, 1.0], [5.0, 1.6, 1.0]); // facing +X
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyW' }));
    for (let i = 0; i < 10; i++) c.update(0.1);
    window.dispatchEvent(new KeyboardEvent('keyup', { code: 'KeyW' }));
    const p = viewer.camera.position;
    assert(Math.abs(p.x - 2.5) < 1e-6 && Math.abs(p.z - 1.0) < 1e-6 && Math.abs(p.y - 1.6) < 1e-9,
      `after 1 s forward: ${p.toArray().map((v) => v.toFixed(3))}`);
    c.setPose([0.6, 1.6, 3.0], [-5, 1.6, 3.0]); // facing the west wall
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyW' }));
    for (let i = 0; i < 20; i++) c.update(0.1);
    window.dispatchEvent(new KeyboardEvent('keyup', { code: 'KeyW' }));
    assert(p.x >= 0.1 + 0.2 - 1e-9, `walked into the wall: x=${p.x}`);
    return `moved 1.500 m in 1 s; stopped at x=${p.x.toFixed(3)} m before the west wall (inner face 0.10 m)`;
  });

  await check('A-13', 'Development site-frame cues present (axes +X / +Y / +Z)', () => {
    assert(viewer.dev.getObjectByName('site-axes'), 'axes helper missing');
    return 'AxesHelper + labels + 1 m grid in dev group';
  });

  await check('A-14', 'Splat appearance is refused at the loadAppearance() boundary', async () => {
    try {
      await loadAppearance({ representation: 'gaussian_splat', assets: [] }, [], { THREE, GLTFLoader: null });
    } catch (e) {
      assert(e instanceof AppearanceUnsupported, 'wrong error type');
      return e.message;
    }
    throw new Error('splat appearance was accepted');
  });

  await check('A-15', 'Frame rendered with WebGL', () => {
    viewer.render();
    const gl = viewer.renderer.getContext();
    assert(gl && !gl.isContextLost(), 'no WebGL context');
    const tri = viewer.renderer.info.render.triangles;
    assert(tri > 0, 'nothing rendered');
    return `${tri} triangles in the last frame; ${gl.getParameter(gl.VERSION)}`;
  });

  const passed = results.filter((r) => r.pass).length;
  const summary = { done: true, passed, total: results.length, allPassed: passed === results.length, results,
    userAgent: navigator.userAgent, at: new Date().toISOString() };
  window.__nammaSelftest = summary;
  return summary;
}
