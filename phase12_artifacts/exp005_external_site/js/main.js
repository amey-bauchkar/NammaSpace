// Namma Space foundation viewer (Phase 11 synthetic vertical slice).
// URL parameters: ?manifest=<path> (default data/viewer_manifest.json), ?dev=1, ?selftest=1
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { loadAppearance } from './appearance.js';
import { loadRelease } from './manifest.js';
import { buildRouteRequest, resolveRequest } from './requests.js';
import { displayDecision } from './routeGate.js';
import { search } from './search.js';
import { Viewer } from './viewer.js';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const manifestUrl = new URL(params.get('manifest') || 'data/viewer_manifest.json', location.href).href;
const selftest = params.has('selftest');
const bench = params.has('bench');
const benchSeconds = Number(params.get('seconds') || 20);
const T0 = performance.now();
const devMode = params.has('dev') || selftest;

function showFatal(kind, message) {
  $('fatal').hidden = false;
  $('fatal-kind').textContent = kind;
  $('fatal-message').textContent = message;
  $('loading').hidden = true;
  window.__nammaState = { error: { kind, message } };
}

function setRouteStatus(state, title, detail = '') {
  const el = $('route-status');
  el.dataset.state = state;
  $('route-status-title').textContent = title;
  $('route-status-detail').textContent = detail;
}

class App {
  constructor(viewer, release) {
    this.viewer = viewer;
    this.release = release;
    this.selected = null;
    this.acceptedGeometryId = release.geometry ? release.geometry.geometry_id : null;
    // Requests use the release's primary navigation layer (IF-10 navlayers[0]),
    // never an id borrowed from a packaged route (the demo route uses another layer).
    this.navlayerId = release.navlayers.length ? release.navlayers[0].navlayer_id : null;
  }

  poi(id) {
    return this.release.pois.find((p) => p.poi_id === id);
  }

  renderSearch(query) {
    const res = search(this.release.pois, query);
    const list = $('results');
    list.innerHTML = '';
    const msg = { empty_query: 'Type a name to search.', not_found: `No verified POI matches "${query}".`,
      ambiguous: `${res.hits.length} matches - choose one:`, unique: '1 match:' }[res.status];
    $('search-msg').textContent = msg;
    for (const h of res.hits) {
      const li = document.createElement('li');
      const b = document.createElement('button');
      const room = h.room_poi_id ? this.poi(h.room_poi_id) : null;
      b.textContent = h.display_name + (room ? ` (${room.display_name})` : '');
      b.dataset.poiId = h.poi_id;
      b.addEventListener('click', () => this.selectPoi(h.poi_id));
      li.appendChild(b);
      list.appendChild(li);
    }
    if (res.status === 'unique') this.selectPoi(res.hits[0].poi_id);
    return res;
  }

  selectPoi(id) {
    const p = this.poi(id);
    this.selected = p;
    $('poi-card').hidden = false;
    $('poi-title').textContent = p.display_name;
    const reach = p.endpoint_reachable === true ? 'reachable' : p.endpoint_reachable === false ? 'NOT reachable from Start A' : 'unknown';
    $('poi-meta').textContent = `${p.kind}${p.category ? ' / ' + p.category : ''} | ${p.verification.status} | endpoint ${reach}`;
    $('poi-coords').textContent = devMode
      ? `marker ${p.marker_site_m.map((v) => v.toFixed(2)).join(', ')} | endpoint ${p.route_endpoint_xz_m ? p.route_endpoint_xz_m.map((v) => v.toFixed(3)).join(', ') : 'none'} | view ${p.view.position_site_m.join(', ')}`
      : '';
    this.viewer.clearRoute();
    setRouteStatus('idle', 'No route requested', '');
  }

  jumpToSelected() {
    if (this.selected) this.viewer.jumpTo(this.selected.view);
  }

  async requestRoute(startId) {
    if (!this.selected) return null;
    this.viewer.clearRoute();
    const start = startId === 'here'
      ? { xz_m: [this.viewer.camera.position.x, this.viewer.camera.position.z], label: 'current position' }
      : this.release.manifest.start_points.find((s) => s.start_id === startId);
    const request = buildRouteRequest(this.navlayerId, start.xz_m, this.selected.poi_id);
    const found = resolveRequest(request, this.release.routes);
    if (found.status === 'unavailable') {
      setRouteStatus('unavailable', 'Route unavailable', found.reason);
      return { state: 'unavailable', request, reasons: [found.reason] };
    }
    const { result, report } = found.route;
    if (result.status !== 'route') {
      const title = { unreachable: 'Destination unreachable', goal_not_snappable: 'No standing point near the destination',
        start_blocked: 'Start position is blocked', error: 'Route error' }[result.status] || result.status;
      setRouteStatus('no_route', title, result.note || '');
      return { state: 'no_route', result, report, reasons: [result.note || result.status] };
    }
    const d = await displayDecision(result, report, this.acceptedGeometryId);
    if (d.state === 'valid') {
      this.viewer.drawRoute(result.polyline_xz_m, 'valid');
      const lim = (report.evidence.limitations || []).join('; ');
      setRouteStatus('valid', `Validated route - ${result.length_m.toFixed(2)} m`,
        `Independent check passed against geometry ${report.geometry_id} (completeness: ${report.evidence.completeness}; min clearance ${report.checks.min_clearance_m} m).${lim ? ' Limitations: ' + lim : ''}`);
    } else {
      if (d.state === 'rejected' && devMode) this.viewer.drawRoute(result.polyline_xz_m, 'rejected');
      setRouteStatus(d.state, d.state === 'rejected' ? 'Route REJECTED by the validator - not shown as valid'
        : 'Route withheld - validation evidence does not match', d.reasons.join('; '));
    }
    return { state: d.state, reasons: d.reasons, result, report, request };
  }

  async showDemoRejected() {
    const r = this.release.routes.find((x) => x.result.request_id === 'rq-demo-planned-on-missing-table');
    this.viewer.clearRoute();
    const d = await displayDecision(r.result, r.report, this.acceptedGeometryId);
    if (d.state === 'valid') throw new Error('demo route unexpectedly valid');
    if (devMode) this.viewer.drawRoute(r.result.polyline_xz_m, 'rejected');
    setRouteStatus(d.state, 'Demo: route planned on geometry WITHOUT the table - REJECTED',
      `${d.reasons.join('; ')}. Validator: intersects_obstacle=${r.report.checks.intersects_obstacle}, min clearance ${r.report.checks.min_clearance_m} m.`);
    return { state: d.state, result: r.result, report: r.report };
  }
}

function wireInput(viewer, app) {
  const typing = (e) => ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target && e.target.tagName);
  window.addEventListener('keydown', (e) => { if (!typing(e)) viewer.controller.keys.add(e.code); });
  window.addEventListener('keyup', (e) => viewer.controller.keys.delete(e.code));
  window.addEventListener('blur', () => viewer.controller.keys.clear());
  let drag = null;
  viewer.canvas.addEventListener('pointerdown', (e) => { drag = [e.clientX, e.clientY]; viewer.canvas.setPointerCapture(e.pointerId); });
  viewer.canvas.addEventListener('pointermove', (e) => {
    if (!drag) return;
    viewer.controller.look(e.clientX - drag[0], e.clientY - drag[1]);
    drag = [e.clientX, e.clientY];
  });
  viewer.canvas.addEventListener('pointerup', () => { drag = null; });
  window.addEventListener('resize', () => viewer.resize());
  $('search-form').addEventListener('submit', () => app.renderSearch($('search').value));
  $('search').disabled = false;
  $('search-btn').disabled = false;
  $('jump').addEventListener('click', () => app.jumpToSelected());
  $('route').addEventListener('click', () => app.requestRoute($('from').value));
  $('demo-rejected').addEventListener('click', () => app.showDemoRejected());
  $('toggle-axes').addEventListener('change', (e) => { viewer.dev.visible = e.target.checked; });
  $('toggle-geometry').addEventListener('change', (e) => { viewer.geometryOverlay.visible = e.target.checked; });
  $('toggle-collision').addEventListener('change', (e) => { viewer.controller.collision = e.target.checked; });
  setInterval(() => {
    const p = viewer.camera.position;
    $('cam').textContent = `camera x ${p.x.toFixed(2)}  y ${p.y.toFixed(2)}  z ${p.z.toFixed(2)} m`;
  }, 250);
}

// Never let the form navigate (e.g. Enter pressed while the release is still loading).
$('search-form').addEventListener('submit', (e) => e.preventDefault());

async function boot() {
  document.body.classList.toggle('dev', devMode);
  $('toggle-axes').checked = devMode;
  let release;
  try {
    release = await loadRelease(manifestUrl);
    var tLoaded = performance.now();
  } catch (e) {
    showFatal(e.kind || 'error', e.message);
    return;
  }
  const viewer = new Viewer($('view'));
  viewer.dev.visible = devMode;
  try {
    const root = await loadAppearance(release.scene, release.assets, { THREE, GLTFLoader });
    viewer.setAppearance(root);
  } catch (e) {
    showFatal(e.kind || 'appearance_error', e.message);
    return;
  }
  if (release.geometry) viewer.setGeometry(release.geometry);
  viewer.setPois(release.pois);
  const app = new App(viewer, release);
  $('release-id').textContent = release.manifest.release_id;
  $('banner').textContent = release.manifest.notices?.[0] || '';
  const notices = $('notices');
  for (const n of release.manifest.notices || []) {
    const li = document.createElement('li'); li.textContent = n; notices.appendChild(li);
  }
  if (release.geometryCheck) {
    const a = release.geometryCheck.acceptance;
    const li = document.createElement('li');
    li.textContent = `Geometry acceptance: ${a.status}; completeness ${release.geometryCheck.completeness.status} (reference: ${release.geometryCheck.completeness.reference_source || 'none'})`;
    notices.appendChild(li);
  }
  const from = $('from');
  for (const s of release.manifest.start_points || []) {
    const o = document.createElement('option'); o.value = s.start_id; o.textContent = s.label; from.appendChild(o);
  }
  const here = document.createElement('option'); here.value = 'here'; here.textContent = 'My current position'; from.appendChild(here);
  wireInput(viewer, app);
  const sp = (release.manifest.start_points || [])[0];
  const p0 = sp ? [sp.xz_m[0], 1.6, sp.xz_m[1]] : [1.0, 1.6, 1.0];
  viewer.release_start_pose = { position: p0 };
  viewer.controller.setPose(p0, [p0[0] + 2.0, 1.2, p0[2] + 2.0]);
  viewer.render();
  const tFirstView = performance.now();
  $('loading').hidden = true;
  window.__namma = { app, viewer, release, THREE };
  window.__nammaState = { loaded: true, release_id: release.manifest.release_id };
  viewer.start();
  const timing = { manifest_and_assets_loaded_ms: Math.round(tLoaded - T0), first_view_ms: Math.round(tFirstView - T0),
    interactive_ms: Math.round(performance.now() - T0), bytes_verified: release.manifest.files ? release.manifest.files.reduce((a, f) => a + f.bytes, 0) : null };
  window.__nammaTiming = timing;
  if (bench) {
    const { runBenchmark } = await import('./bench.js');
    const r = await runBenchmark(app, benchSeconds, timing);
    $('selftest').hidden = false;
    $('selftest-summary').textContent = r.valid ? `benchmark VALID: median ${r.frame_ms.median.toFixed(1)} ms, p95 ${r.frame_ms.p95.toFixed(1)} ms` : `benchmark INVALID: ${r.invalid_reason}`;
    $('selftest-summary').dataset.ok = String(r.valid);
  }
  if (selftest) {
    const { runSelftest } = await import('./selftest.js');
    const s = await runSelftest(app);
    $('selftest').hidden = false;
    $('selftest-summary').textContent = `${s.passed}/${s.total} checks passed`;
    $('selftest-summary').dataset.ok = String(s.allPassed);
    const ul = $('selftest-results');
    for (const r of s.results) {
      const li = document.createElement('li');
      li.textContent = `${r.pass ? 'PASS' : 'FAIL'} ${r.id} ${r.name} - ${r.detail}`;
      li.className = r.pass ? 'pass' : 'fail';
      ul.appendChild(li);
    }
  }
}

boot();
