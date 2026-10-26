// Namma Space walkthrough viewer for IF-13 scene packages (Phase 13).
// URL parameters:
//   ?package=<url>            scene package (default data/scene_package.json)
//   &project=<id>&run=<id>    Studio mode (local Studio server): POI editing, scale calibration, routes
//   &lod=<n>                  force one level of detail;   &bench=1[&seconds=20]  frame-time benchmark
//   &selftest=1               in-page functional checks (used by the browser test harness)
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { createMinimap } from './minimap.js';
import { canOccupyGrid, decodeRoamGrid, isAllowed } from './roam.js';
import { formatLength, loadScenePackage } from './scenePackage.js';
import { search } from './search.js';
import { levelStartTarget, Walker } from './walker.js';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const pkgUrl = new URL(params.get('package') || 'data/scene_package.json', location.href).href;
const studio = params.get('project') && params.get('run') ? { project: params.get('project'), run: params.get('run') } : null;
const apiBase = studio ? `/api/projects/${encodeURIComponent(studio.project)}/runs/${encodeURIComponent(studio.run)}` : null;
const forcedLod = params.has('lod') ? Number(params.get('lod')) : null;
const T0 = performance.now();
const AUTO_FULL_DETAIL_MAX_BYTES = 160 * 2 ** 20;
const state = { phase: 'loading', timing: {}, errors: [], lodShown: null, triangles: 0, notices: [] };
window.__nammaScene = state;

function toast(msg, ms = 2600) {
  const t = $('toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.h);
  toast.h = setTimeout(() => { t.hidden = true; }, ms);
}

function fatal(kind, message, detail = '') {
  $('loading').hidden = true;
  $('error').hidden = false;
  $('error-message').textContent = message;
  $('error-detail').textContent = `${kind}${detail ? ': ' + detail : ''}`;
  state.phase = 'error';
  state.errors.push({ kind, message });
}

function setLoading(step, frac = null, bytes = '') {
  $('loading-step').textContent = step;
  if (frac !== null) $('loading-bar').style.width = `${Math.round(frac * 100)}%`;
  $('loading-bytes').textContent = bytes;
}

const mb = (b) => `${(b / 2 ** 20).toFixed(1)} MB`;

class SceneApp {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x10151f);
    this.camera = new THREE.PerspectiveCamera(70, 1, 0.01, 1000);
    this.markers = new THREE.Group();
    this.routeGroup = new THREE.Group();
    this.measureGroup = new THREE.Group();
    this.scene.add(this.markers, this.routeGroup, this.measureGroup);
    this.appearance = null;
    this.frameTimes = [];
    this.raycaster = new THREE.Raycaster();
    this.resize();
  }

  resize() {
    const w = this.canvas.clientWidth || 800, h = this.canvas.clientHeight || 600;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  async setAppearance(buf, toModel) {
    const gltf = await new GLTFLoader().parseAsync(buf, '');
    let tris = 0;
    gltf.scene.traverse((o) => {
      if (!o.isMesh) return;
      const old = o.material;
      const map = old && old.map ? old.map : null;
      if (map) map.anisotropy = Math.min(4, this.renderer.capabilities.getMaxAnisotropy());
      // photo textures already contain the room's lighting: show them unlit (no double shading)
      o.material = new THREE.MeshBasicMaterial({ map, side: THREE.DoubleSide, vertexColors: !map && !!o.geometry.attributes.color,
        color: map || o.geometry.attributes.color ? 0xffffff : 0xb8c2d0 });
      // with a photoreal splat shown, the mesh stays for walking and picking but is not drawn (raycasts still hit it)
      o.material.visible = !this.meshHidden;
      if (old) old.dispose();
      tris += o.geometry.index ? o.geometry.index.count / 3 : o.geometry.attributes.position.count / 3;
    });
    const root = new THREE.Group();
    root.name = 'appearance:to_model';
    root.matrixAutoUpdate = false;
    root.matrix.set(...toModel);          // row-major arguments; applied exactly once (ARCH-020 rule)
    root.add(gltf.scene);
    root.updateMatrixWorld(true);
    if (this.appearance) {
      this.scene.remove(this.appearance);
      this.appearance.traverse((o) => {
        if (o.isMesh) {
          o.geometry.dispose();
          if (o.material.map) o.material.map.dispose();
          o.material.dispose();
        }
      });
    }
    this.appearance = root;
    this.scene.add(root);
    return tris;
  }

  pick(clientX, clientY, objects) {
    const r = this.canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    return this.raycaster.intersectObjects(objects, true)[0] || null;
  }

  render() {
    this.renderer.render(this.scene, this.camera);
  }
}

function labelSprite(text, eye) {
  const c = document.createElement('canvas');
  const g = c.getContext('2d');
  g.font = 'bold 44px system-ui, sans-serif';
  const w = Math.ceil(g.measureText(text).width) + 36;
  c.width = w; c.height = 72;
  g.font = 'bold 44px system-ui, sans-serif';
  g.fillStyle = 'rgba(15,26,42,0.85)';
  g.beginPath(); g.roundRect(0, 0, w, 72, 18); g.fill();
  g.fillStyle = '#ffffff';
  g.fillText(text, 18, 51);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  const h = 0.1 * eye;
  s.scale.set((h * w) / 72, h, 1);
  s.renderOrder = 10;
  return s;
}

async function api(path, opts = {}) {
  const r = await fetch(apiBase + path, { headers: { 'Content-Type': 'application/json', 'X-Namma-Studio': '1' }, ...opts });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.message || `HTTP ${r.status}`);
  return body;
}

async function main() {
  const app = new SceneApp($('view'));
  state.app = app;
  if (studio) {
    $('studio-link').hidden = false;
    $('studio-link').href = `studio.html#/project/${encodeURIComponent(studio.project)}`;
    $('studio-tools').hidden = false;
  }
  let data;
  try {
    setLoading('Checking the scene package…', 0.02);
    data = await loadScenePackage(pkgUrl, {});
    state.timing.package_ms = Math.round(performance.now() - T0);
  } catch (e) {
    fatal(e.kind || 'error', 'The scene package could not be loaded or failed its integrity check.', e.message);
    return;
  }
  const { pkg } = data;
  const eye = pkg.eye_height_units;
  state.pkg = pkg;
  document.title = `${pkg.project ? pkg.project.title : 'Namma Space'} – 3D walkthrough`;
  $('title').textContent = pkg.project ? pkg.project.title : pkg.package_id;
  renderAbout(pkg, data.quality);

  // ---- appearance: lightest level first
  const lods = forcedLod !== null ? data.lods.filter((l) => l.lod === forcedLod) : data.lods;
  if (!lods.length) { fatal('lod_missing', `Level of detail ${forcedLod} is not in this package.`); return; }
  const first = lods[0];
  try {
    setLoading(`Downloading the 3D model (${mb(first.bytes)})…`, 0.05);
    const buf = await data.fetchLod(first, (got, total) => setLoading(`Downloading the 3D model…`, 0.05 + 0.8 * (got / (total || first.bytes)),
      `${mb(got)} of ${mb(total || first.bytes)}`));
    state.timing.first_lod_downloaded_ms = Math.round(performance.now() - T0);
    setLoading('Preparing the 3D model…', 0.9);
    state.triangles = await app.setAppearance(buf, pkg.appearance.to_model.matrix_row_major);
    state.lodShown = first.lod;
  } catch (e) {
    const integrity = ['hash_mismatch', 'size_mismatch'].includes(e.kind);
    fatal(e.kind || 'model_error', integrity
      ? 'The 3D model file is damaged or was changed after publishing (integrity check failed). Re-publish or re-download it.'
      : 'The 3D model file could not be read.', e.message);
    return;
  }

  // ---- navigation helpers
  let grid = null;
  try { grid = decodeRoamGrid(data.roam); } catch (e) { state.errors.push({ kind: 'roam_grid', message: e.message }); }
  app.pkgWarnings = (pkg.quality && pkg.quality.warnings) || [];
  if (grid) addFloorGuide(app, grid, eye);
  const walker = new Walker(app.camera, { eyeHeight: eye, grid, bounds: [pkg.bounds.min, pkg.bounds.max] });
  state.walker = walker;
  app.camera.near = 0.02 * eye;
  app.camera.far = 400 * eye;
  app.camera.updateProjectionMatrix();
  const start = pkg.start_view;
  const startTarget = levelStartTarget(start.position, start.target, eye);
  const startPose = () => {
    walker.setMode('walk');
    walker.setPose([start.position[0], eye, start.position[2]], startTarget);
    if (grid && !isAllowed(grid, start.position[0], start.position[2])) walker.recover();
    $('mode-toggle').textContent = 'Fly mode';
    $('hud-mode').textContent = 'walk';
  };
  startPose();
  app.render();
  state.timing.first_view_ms = Math.round(performance.now() - T0);
  $('loading').hidden = true;
  state.phase = 'ready';
  $('hud-lod').hidden = false;
  $('hud-lod').textContent = `LOD ${first.lod} · ${(state.triangles / 1e6).toFixed(2)} M tris`;

  // ---- render loop
  let last = performance.now(), fpsAcc = 0, fpsN = 0;
  const tick = (now) => {
    const dt = Math.min(0.1, (now - last) / 1000);
    app.frameTimes.push(now - last);
    if (app.frameTimes.length > 600) app.frameTimes.shift();
    last = now;
    fpsAcc += dt; fpsN += 1;
    if (fpsAcc > 0.5) { $('hud-fps').textContent = `${Math.round(fpsN / fpsAcc)} fps`; fpsAcc = 0; fpsN = 0; }
    if (app.tween) app.tween(now); else walker.update(dt);
    if (app.minimap && ++app.frameNo % 4 === 0) {
      if (state.measure) app.minimap.setMeasures(state.measure.chains.map((c) => (c.closed ? [...c.points, c.points[0]] : c.points)));
      app.minimap.update();
    }
    app.render();
    app.raf = requestAnimationFrame(tick);
  };
  app.frameNo = 0;
  app.raf = requestAnimationFrame(tick);
  wireControls(app, walker, startPose, data);
  wireMeasure({ app, pkg, eye });

  // ---- POIs
  const ctx = { app, walker, data, pkg, eye, pois: data.pois, selected: null };
  state.ctx = ctx;
  if (studio) {
    try { ctx.pois = (await api('/pois')).pois; } catch (e) { toast(`Could not load live labels: ${e.message}`); }
  }
  renderPois(ctx);
  wirePlaces(ctx);
  wireViewpoints(ctx, startPose);
  wireMinimap(ctx);
  if (studio) wireStudioTools(ctx);

  // ---- estimated surfaces (holes in walls / floor / ceiling closed with flat planes; never scanned geometry)
  if (data.estimated) {
    loadEstimated(ctx).catch((e) => { state.errors.push({ kind: 'estimated_surfaces', message: e.message }); toast(`Estimated surfaces not shown: ${e.message}`); });
  }

  // ---- photoreal splat (IF-13 1.3.0): drawn instead of the mesh, which stays for walking, picking and routes
  if (data.splat) {
    // shown by default (held-out photos: 26.3 dB, SSIM 0.86 on "New room"); ?mesh starts on the mesh and downloads
    // the splat (~150 MB) only when asked for
    const go = () => loadSplat(ctx).catch((e) => { state.errors.push({ kind: 'splat', message: e.message }); toast(`Photoreal view not available: ${e.message}`); });
    if (!params.has('mesh')) go();
    else {
      $('splat-toggle').hidden = false;
      $('splat-toggle').textContent = `Show photoreal (${mb(data.splat.bytes)})`;
      $('splat-toggle').onclick = () => { params.delete('mesh'); go(); };
    }
  }

  // ---- full detail in the background
  const full = data.lods.find((l) => l.lod === 0);
  const canUpgrade = forcedLod === null && full && full !== first;
  if (canUpgrade) {
    $('detail-toggle').hidden = false;
    $('detail-toggle').onclick = () => upgrade(ctx, full);
    if (full.bytes <= AUTO_FULL_DETAIL_MAX_BYTES && !params.has('light')) upgrade(ctx, full);
  }
  if (params.has('bench')) {
    if (canUpgrade && full.bytes <= AUTO_FULL_DETAIL_MAX_BYTES && !params.has('light')) await ctx.upgrading;
    await runBenchmark(ctx, Number(params.get('seconds') || 20));
  }
  if (params.has('selftest')) await runSelftest(ctx, startPose);
}

/** Estimated surfaces: a separate, clearly labelled layer, already in the model frame (no to_model). */
function setMeshHidden(app, hidden) {
  app.meshHidden = hidden;
  if (app.appearance) app.appearance.traverse((o) => { if (o.isMesh) o.material.visible = !hidden; });
}

async function loadSplat(ctx) {
  const { app, data } = ctx;
  const s = data.splat;
  const btn = $('splat-toggle');
  btn.hidden = false;
  btn.disabled = true;
  btn.textContent = `Loading photoreal view (${mb(s.bytes)})…`;
  const [bytes, spark] = await Promise.all([
    data.fetchSplat((got, total) => { btn.textContent = `Loading photoreal view… ${Math.round(100 * got / (total || s.bytes))}%`; }),
    import('../vendor/spark/spark.module.js')]);
  if (!app.spark) {
    app.spark = new spark.SparkRenderer({ renderer: app.renderer });
    app.scene.add(app.spark);
  }
  const type = s.format === 'spz' ? 'spz' : 'ply';
  const mesh = new spark.SplatMesh({ fileBytes: bytes, fileType: spark.SplatFileType ? spark.SplatFileType[type.toUpperCase()] : type });
  await mesh.initialized;
  mesh.matrixAutoUpdate = false;
  mesh.matrix.set(...s.to_model.matrix_row_major);             // splat frame -> model frame, applied once
  mesh.matrixWorldNeedsUpdate = true;
  app.scene.add(mesh);
  app.splat = mesh;
  const startSplat = !params.has('mesh');
  mesh.visible = startSplat;
  setMeshHidden(app, startSplat);
  state.splat = { splats: s.splats, bytes: s.bytes, shown: startSplat };
  btn.disabled = false;
  btn.textContent = startSplat ? 'Show mesh' : 'Show photoreal';
  btn.onclick = () => {
    const show = !state.splat.shown;
    mesh.visible = show;
    setMeshHidden(app, show);
    state.splat.shown = show;
    btn.textContent = show ? 'Show mesh' : 'Show photoreal';
  };
}

async function loadEstimated(ctx) {
  const buf = await ctx.data.fetchEstimated();
  const gltf = await new GLTFLoader().parseAsync(buf, '');
  const group = new THREE.Group();
  group.name = 'estimated_surfaces';
  gltf.scene.traverse((o) => {
    if (!o.isMesh) return;
    const old = o.material;
    // front faces point into the room: seen from outside (overview) the estimate is see-through
    o.material = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.FrontSide });
    if (old) old.dispose();
  });
  group.add(gltf.scene);
  ctx.app.scene.add(group);
  ctx.estimated = group;
  window.__nammaScene.estimatedShown = true;
  const btn = $('estimated-toggle');
  btn.hidden = false;
  btn.onclick = () => {
    group.visible = !group.visible;
    window.__nammaScene.estimatedShown = group.visible;
    btn.textContent = group.visible ? 'Hide estimated surfaces' : 'Show estimated surfaces';
  };
  const est = ctx.data.estimated;
  const filled = (est.planes || []).filter((p) => p.filled_cells > 0).map((p) => p.plane.replace('_', ' '));
  const about = $('about');
  const dt = document.createElement('dt'); dt.textContent = 'Estimated surfaces';
  const dd = document.createElement('dd');
  dd.textContent = `holes in ${filled.join(', ') || 'none'} closed with flat surfaces coloured from your photos – an estimate, not a scan`;
  about.append(dt, dd);
}

/** Floor guide: a faint grid at the detected floor height inside the walkable footprint. It sits BELOW y = 0
 * (2.5 % of the eye height, 4 cm for a standing eye), so reconstructed floor always covers it - scanned floors dip a
 * centimetre or two, and at 1 cm the guide showed through real floor - and it is visible only where the photos did
 * not reconstruct the floor. It is labelled as a guide (not geometry) in the panel and can be switched off. */
function addFloorGuide(app, g, eye) {
  const ppc = Math.max(1, Math.min(6, Math.floor(4096 / Math.max(g.width, g.height))));
  const c = document.createElement('canvas');
  c.width = g.width * ppc; c.height = g.height * ppc;
  const x = c.getContext('2d');
  const img = x.createImageData(c.width, c.height);
  const major = Math.max(2, Math.round(eye / 2 / g.cell));          // grid line about every half eye height
  for (let r = 0; r < g.height; r++) {
    for (let col = 0; col < g.width; col++) {
      if (!g.allowed[r * g.width + col]) continue;
      for (let dy = 0; dy < ppc; dy++) {
        for (let dx = 0; dx < ppc; dx++) {
          const line = (r % major === 0 && dy === 0) || (col % major === 0 && dx === 0);
          const i = ((r * ppc + dy) * c.width + (col * ppc + dx)) * 4;
          img.data[i] = line ? 185 : 128; img.data[i + 1] = line ? 200 : 140; img.data[i + 2] = line ? 228 : 160;
          img.data[i + 3] = line ? 200 : 105;
        }
      }
    }
  }
  x.putImageData(img, 0, 0);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = Math.min(4, app.renderer.capabilities.getMaxAnisotropy());
  const w = g.width * g.cell, h = g.height * g.cell;
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false, side: THREE.DoubleSide }));
  mesh.rotation.x = -Math.PI / 2;
  mesh.position.set(g.ox + w / 2, -0.025 * eye, g.oz + h / 2);
  mesh.name = 'floor-guide';
  mesh.renderOrder = -1;
  app.scene.add(mesh);
  app.floorGuide = mesh;
  // a suspect floor (probably a table top) would make the guide float in the air: start hidden, say why
  const suspect = (app.pkgWarnings || []).includes('FLOOR-SUSPECT');
  mesh.visible = !suspect;
  const btn = document.getElementById('guide-toggle');
  if (btn) {
    btn.hidden = false;
    btn.textContent = mesh.visible ? 'Hide floor guide' : 'Show floor guide (floor uncertain)';
    btn.onclick = () => { mesh.visible = !mesh.visible; btn.textContent = mesh.visible ? 'Hide floor guide' : 'Show floor guide'; };
  }
}

async function upgrade(ctx, lod) {
  if (ctx.upgrading) return ctx.upgrading;
  $('detail-toggle').disabled = true;
  $('hud-lod').textContent = `loading full detail…`;
  ctx.upgrading = (async () => {
    try {
      const t = performance.now();
      const buf = await ctx.data.fetchLod(lod, (got, total) => { $('hud-lod').textContent = `full detail ${Math.round(100 * got / (total || lod.bytes))}%`; });
      state.triangles = await ctx.app.setAppearance(buf, ctx.pkg.appearance.to_model.matrix_row_major);
      state.lodShown = lod.lod;
      state.timing.full_detail_ms = Math.round(performance.now() - T0);
      state.timing.full_detail_load_ms = Math.round(performance.now() - t);
      $('hud-lod').textContent = `LOD ${lod.lod} · ${(state.triangles / 1e6).toFixed(2)} M tris`;
      $('detail-toggle').textContent = 'Full detail ✓';
    } catch (e) {
      state.errors.push({ kind: 'full_detail', message: e.message });
      toast(`Full detail unavailable: ${e.message}`);
      $('detail-toggle').disabled = false;
    }
  })();
  return ctx.upgrading;
}

function renderAbout(pkg, quality) {
  const about = $('about');
  const add = (k, v) => { const dt = document.createElement('dt'); dt.textContent = k; const dd = document.createElement('dd'); dd.textContent = v; about.append(dt, dd); };
  const s = pkg.scale;
  add('Scale', s.status === 'unknown' ? 'unknown (photos only)' : `${s.metres_per_unit.toPrecision(4)} m per unit (${s.status.replace('_', ' ')}${s.evaluation_only ? ', evaluation only' : ''})`);
  const suspect = Boolean(quality && quality.facts && quality.facts.floor_suspect);
  add('Floor', !pkg.model_frame.floor_detected ? 'not detected (orientation from photos)'
    : suspect ? 'uncertain (may be a table or desk top)' : 'detected');
  if (quality) {
    const f = quality.facts;
    if (f.source === 'imported_mesh') add('Source', `${f.app} scan (${f.device}), imported`);
    else add('Photos placed', `${f.images_registered} of ${f.images_input}`);
    add('Surface', `${(f.mesh_faces / 1e6).toFixed(2)} M triangles`);
    const b = $('quality-badge');
    b.hidden = false;
    b.textContent = { good: 'Quality: good', usable_with_warnings: 'Quality: usable, with warnings', poor: 'Quality: poor', failed: 'Quality: failed' }[quality.verdict];
    b.className = `badge ${{ good: 'ok', usable_with_warnings: 'warn', poor: 'bad', failed: 'bad' }[quality.verdict]}`;
    const ul = $('warnings');
    for (const w of quality.warnings) {
      const li = document.createElement('li'); li.className = w.severity; li.textContent = w.message; ul.appendChild(li);
    }
    $('warn-count').textContent = quality.warnings.filter((w) => w.severity !== 'info').length;
    $('quality-details').hidden = false;
  }
  add('Navigation', { unavailable: 'not available', unverified: 'unverified (automatic)', accepted: 'verified' }[pkg.navigation.status]);
  add('Floor guide', 'faint grid = floor area the photos did not reconstruct (a guide, not a scan)');
  const nl = $('notices');
  for (const n of pkg.notices) { const li = document.createElement('li'); li.className = 'info'; li.textContent = n; nl.appendChild(li); }
}

function wireControls(app, walker, startPose) {
  const canvas = app.canvas;
  const stage = $('stage');
  const typing = (e) => ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target && e.target.tagName);
  window.addEventListener('keydown', (e) => {
    if (typing(e) || !$('help').hidden || !$('poi-form').hidden) return;
    if (['Space', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.code)) e.preventDefault();
    if (e.code === 'KeyF') toggleMode(walker);
    else if (e.code === 'KeyR') { startPose(); toast('Back at the starting view'); }
    else if (e.code === 'BracketRight') $('cam-next').click();
    else if (e.code === 'BracketLeft') $('cam-prev').click();
    else walker.keys.add(e.code);
  });
  window.addEventListener('keyup', (e) => walker.keys.delete(e.code));
  window.addEventListener('blur', () => walker.keys.clear());
  // mouse look: pointer lock when available, drag-to-look otherwise
  let drag = null, downAt = null;
  canvas.addEventListener('pointerdown', (e) => {
    downAt = [e.clientX, e.clientY, performance.now()];
    if (document.pointerLockElement !== canvas) { drag = [e.clientX, e.clientY]; canvas.setPointerCapture(e.pointerId); }
  });
  canvas.addEventListener('pointermove', (e) => {
    if (document.pointerLockElement === canvas) { walker.look(e.movementX, e.movementY); return; }
    if (!drag) return;
    walker.look(e.clientX - drag[0], e.clientY - drag[1]);
    drag = [e.clientX, e.clientY];
  });
  canvas.addEventListener('pointerup', (e) => {
    drag = null;
    const click = downAt && Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) < 5 && performance.now() - downAt[2] < 400;
    if (!click) return;
    if (app.onPick) { app.onPick(e); return; }
    if (state.ctx && pickMarker(state.ctx, e)) return;
    if (e.pointerType === 'mouse' && canvas.requestPointerLock && document.pointerLockElement !== canvas) {
      const r = canvas.requestPointerLock();
      if (r && r.catch) r.catch(() => {});
    }
  });
  document.addEventListener('pointerlockchange', () => {
    const locked = document.pointerLockElement === canvas;
    stage.classList.toggle('locked', locked);
    $('crosshair').hidden = !locked;
  });
  window.addEventListener('resize', () => app.resize());
  $('help-btn').onclick = () => { $('help').hidden = false; $('help-close').focus(); };
  $('help-close').onclick = () => { $('help').hidden = true; canvas.focus(); };
  $('reset-view').onclick = () => { startPose(); canvas.focus(); };
  $('mode-toggle').onclick = () => { toggleMode(walker); canvas.focus(); };
  $('error-retry').onclick = () => location.reload();
  for (const b of document.querySelectorAll('#touchpad button')) {
    b.addEventListener('pointerdown', (e) => { e.preventDefault(); walker.keys.add(b.dataset.key); });
    for (const ev of ['pointerup', 'pointerleave', 'pointercancel']) b.addEventListener(ev, () => walker.keys.delete(b.dataset.key));
  }
  setTimeout(() => { $('hint').style.opacity = '0.75'; }, 8000);
}

function toggleMode(walker) {
  walker.setMode(walker.mode === 'walk' ? 'fly' : 'walk');
  $('hud-mode').textContent = walker.mode;
  $('mode-toggle').textContent = walker.mode === 'walk' ? 'Fly mode' : 'Walk mode';
  toast(walker.mode === 'fly' ? 'Fly mode: free movement (E / Q for up / down)' : 'Walk mode');
}

// ------------------------------------------------------------------ places (POIs)
const asSearchable = (p) => ({ poi_id: p.poi_id, display_name: p.name, aliases: p.aliases || [], verification: { status: 'verified' } });

function renderPois(ctx) {
  const { app, eye } = ctx;
  if (app.minimap) app.minimap.setPois(ctx.pois);
  app.markers.clear();
  for (const p of ctx.pois) {
    const g = new THREE.Group();
    g.name = `poi:${p.poi_id}`;
    g.userData = { poiId: p.poi_id };
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.05 * eye, 16, 10), new THREE.MeshBasicMaterial({ color: 0x2563eb, depthTest: false }));
    dot.renderOrder = 9;
    const lbl = labelSprite(p.name, eye);
    lbl.position.y = 0.12 * eye;
    g.add(dot, lbl);
    g.position.set(...p.position);
    g.traverse((o) => { o.userData.poiId = p.poi_id; });
    app.markers.add(g);
  }
}

function pickMarker(ctx, e) {
  if (!ctx.pois.length) return false;
  const hit = ctx.app.pick(e.clientX, e.clientY, [ctx.app.markers]);
  if (!hit) return false;
  selectPoi(ctx, hit.object.userData.poiId);
  toast(`This is: ${ctx.pois.find((p) => p.poi_id === hit.object.userData.poiId).name}`);
  return true;
}

function selectPoi(ctx, id) {
  const p = ctx.pois.find((x) => x.poi_id === id);
  if (!p) return;
  ctx.selected = p;
  $('poi-card').hidden = false;
  $('poi-title').textContent = p.name;
  $('poi-meta').textContent = [p.category, (p.aliases || []).join(', ')].filter(Boolean).join(' · ') || 'Place label';
  $('route-status').hidden = true;
  $('poi-route').hidden = !studio || ctx.pkg.navigation.status === 'unavailable';
  ctx.app.routeGroup.clear();
  if (ctx.app.minimap) { ctx.app.minimap.setRoute(null); ctx.app.minimap.setSelected(p.poi_id); }
}

function flyTo(ctx, pos, target, ms = 900) {
  const { app, walker } = ctx;
  const p0 = app.camera.position.clone();
  const y0 = walker.yaw, x0 = walker.pitch;
  walker.setMode('fly');
  $('hud-mode').textContent = 'fly';
  $('mode-toggle').textContent = 'Walk mode';
  const tmp = { yaw: 0, pitch: 0 };
  const d = [target[0] - pos[0], target[1] - pos[1], target[2] - pos[2]];
  const n = Math.hypot(...d) || 1;
  tmp.yaw = Math.atan2(-d[0], -d[2]);
  tmp.pitch = Math.asin(d[1] / n);
  let dy = tmp.yaw - y0;
  dy = Math.atan2(Math.sin(dy), Math.cos(dy));
  const t0 = performance.now();
  return new Promise((resolve) => {
    app.tween = (now) => {
      const t = Math.min(1, (now - t0) / ms);
      const s = t * t * (3 - 2 * t);
      app.camera.position.set(p0.x + (pos[0] - p0.x) * s, p0.y + (pos[1] - p0.y) * s, p0.z + (pos[2] - p0.z) * s);
      walker.yaw = y0 + dy * s;
      walker.pitch = x0 + (tmp.pitch - x0) * s;
      walker.apply();
      if (t >= 1) { app.tween = null; resolve(); }
    };
  });
}

/** Where to look at a label from: a walkable floor spot at eye height, 0.5-2 eye heights away, with a clear line of
 * sight to it (nearest to ~0.9 eye heights first). A fixed diagonal offset put the camera in the air or behind walls -
 * and a photoreal splat only looks right near where people stand (where the photos were taken).
 * -> [position, target, onFloor] */
function poiView(ctx, p) {
  if (p.view) return [p.view.position, p.view.target, false];
  const e = ctx.eye, g = ctx.walker.grid, P = p.position;
  const fallback = [[P[0] + 1.2 * e, e, P[2] + 1.2 * e], P, false];
  if (!g) return fallback;
  const cands = [];
  for (let r = 0; r < g.height; r++) {
    for (let c = 0; c < g.width; c++) {
      if (g.allowed[r * g.width + c] !== 1) continue;
      const x = g.ox + (c + 0.5) * g.cell, z = g.oz + (r + 0.5) * g.cell;
      const d = Math.hypot(x - P[0], z - P[2]);
      if (d >= 0.5 * e && d <= 2 * e) cands.push([Math.abs(d - 0.9 * e), x, z]);
    }
  }
  cands.sort((a, b) => a[0] - b[0]);
  const ray = new THREE.Raycaster();
  const target = new THREE.Vector3(...P);
  for (const [, x, z] of cands.slice(0, 300)) {
    const from = new THREE.Vector3(x, e, z);
    const dir = target.clone().sub(from);
    const len = dir.length();
    ray.set(from, dir.normalize());
    ray.far = Math.max(0, len - 0.08 * e);
    if (!ctx.app.appearance || !ray.intersectObject(ctx.app.appearance, true).length) return [[x, e, z], P, true];
  }
  return fallback;
}

/** Route start: the camera's floor spot, or the nearest walkable spot with room around it (the planner never moves a
 * blocked start, by design; after a jump or while flying the camera is often not on clear floor). */
function routeStart(ctx) {
  const p = ctx.app.camera.position, g = ctx.walker.grid, room = 0.16 * ctx.eye;
  if (!g || canOccupyGrid(g, p.x, p.z, room)) return [[p.x, p.z], false];
  let best = null, bestD = Infinity;
  for (let r = 0; r < g.height; r++) {
    for (let c = 0; c < g.width; c++) {
      if (g.allowed[r * g.width + c] !== 1) continue;
      const x = g.ox + (c + 0.5) * g.cell, z = g.oz + (r + 0.5) * g.cell;
      const d = Math.hypot(x - p.x, z - p.z);
      if (d < bestD && canOccupyGrid(g, x, z, room)) { best = [x, z]; bestD = d; }
    }
  }
  return best ? [best, true] : [[p.x, p.z], false];
}

function wirePlaces(ctx) {
  const run = () => {
    const q = $('search').value;
    const res = search(ctx.pois.map(asSearchable), q);
    const list = $('results');
    list.innerHTML = '';
    const msg = { empty_query: ctx.pois.length ? 'Type a name to search.' : 'No places are labelled yet.',
      not_found: `No place matches "${q}".`, ambiguous: `${res.hits.length} matches – choose one:`, unique: '1 match' }[res.status];
    $('search-msg').textContent = msg;
    for (const h of res.hits) {
      const li = document.createElement('li');
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = h.display_name;
      b.onclick = () => { selectPoi(ctx, h.poi_id); goToSelected(ctx); };
      li.appendChild(b);
      list.appendChild(li);
    }
    if (res.status === 'unique') { selectPoi(ctx, res.hits[0].poi_id); goToSelected(ctx); }
    state.lastSearch = res;
    return res;
  };
  $('search-form').addEventListener('submit', (e) => { e.preventDefault(); run(); });
  $('poi-jump').onclick = () => goToSelected(ctx);
  $('poi-route').onclick = () => requestRoute(ctx);
  ctx.search = run;
  if (!ctx.pois.length) $('search-msg').textContent = studio ? 'No places labelled yet – use "Add a place label".' : 'No places are labelled in this model.';
}

async function goToSelected(ctx) {
  if (!ctx.selected) return;
  const [pos, target, onFloor] = poiView(ctx, ctx.selected);
  await flyTo(ctx, pos, target);
  if (onFloor) {                                         // standing on the floor: carry on walking from here
    ctx.walker.setMode('walk');
    $('hud-mode').textContent = 'walk';
    $('mode-toggle').textContent = 'Fly mode';
  }
  state.lastJump = { poi_id: ctx.selected.poi_id, position: pos, on_floor: onFloor };
}

async function requestRoute(ctx) {
  const box = $('route-status');
  box.hidden = false;
  box.dataset.state = 'pending';
  box.textContent = 'Computing a walking path…';
  const [start, moved] = routeStart(ctx);
  try {
    const r = await api('/route', { method: 'POST', body: JSON.stringify({ start, poi_id: ctx.selected.poi_id }) });
    r.start_moved = moved;
    state.lastRoute = r;
    box.dataset.state = r.status;
    box.innerHTML = '';
    const strong = document.createElement('strong');
    strong.textContent = r.title;
    const det = document.createElement('div');
    det.className = 'small';
    det.textContent = r.message + (moved ? ' (Starting from the nearest clear floor.)' : '');
    box.append(strong, det);
    ctx.app.routeGroup.clear();
    if (r.polyline_model && r.polyline_model.length > 1) drawRoute(ctx, r.polyline_model, r.status);
  } catch (e) {
    box.dataset.state = 'error';
    box.textContent = `Route request failed: ${e.message}`;
  }
}

function drawRoute(ctx, poly, status) {
  const y = 0.04 * ctx.eye;
  const pts = poly.map(([x, z]) => new THREE.Vector3(x, y, z));
  const color = status === 'validated' ? 0x16a34a : 0xf59e0b;
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineDashedMaterial({ color, dashSize: 0.12 * ctx.eye, gapSize: status === 'validated' ? 0.0001 : 0.08 * ctx.eye, depthTest: false,
      transparent: true }));                              // late pass: over the photoreal splat
  line.computeLineDistances();
  line.renderOrder = 8;
  ctx.app.routeGroup.add(line);
  if (ctx.app.minimap) ctx.app.minimap.setRoute(poly);
}

// ------------------------------------------------------------------ mini-map
function wireMinimap(ctx) {
  const { app, walker, eye } = ctx;
  if (!app.appearance) return;
  try {
    app.minimap = createMinimap({ app, walker, eye, canvas: $('minimap-canvas'), onGo: (x, z) => {
      const g = walker.grid;
      if (walker.mode === 'walk' && g && !isAllowed(g, x, z)) { toast('Not a walking spot – click on open floor (or switch to fly mode).'); return; }
      const dir = new THREE.Vector3();
      app.camera.getWorldDirection(dir);
      const y = walker.mode === 'walk' ? eye : app.camera.position.y;
      flyTo(ctx, [x, y, z], [x + dir.x, y + dir.y, z + dir.z], 600);
    } });
  } catch (e) {
    state.errors.push({ kind: 'minimap', message: e.message });
    return;
  }
  app.minimap.setPois(ctx.pois);
  const box = $('minimap'), btn = $('map-toggle');
  let shown = true;
  try { shown = localStorage.getItem('namma.map') !== 'off'; } catch { /* no storage */ }
  const show = (on) => {
    shown = on;
    box.hidden = !on;
    btn.textContent = on ? 'Hide map' : 'Show map';
    try { localStorage.setItem('namma.map', on ? 'on' : 'off'); } catch { /* no storage */ }
  };
  btn.hidden = false;
  btn.onclick = () => show(!shown);
  $('minimap-close').onclick = () => show(false);
  window.addEventListener('keydown', (e) => {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target && e.target.tagName)) return;
    if (e.code === 'KeyN') show(!shown);
  });
  show(shown);
  state.minimap = true;
}

// ------------------------------------------------------------------ photo viewpoints
function wireViewpoints(ctx, startPose) {
  const cams = ctx.data.cameras;
  let i = -1;
  const show = (k) => {
    if (!cams.length) return;
    i = (k + cams.length) % cams.length;
    const c = cams[i];
    const t = [c.position[0] + c.forward[0], c.position[1] + c.forward[1], c.position[2] + c.forward[2]];
    flyTo(ctx, c.position, t, 600);
    $('cam-label').textContent = `${i + 1} / ${cams.length} · ${c.name}`;
  };
  $('cam-next').onclick = () => show(i + 1);
  $('cam-prev').onclick = () => show(i - 1);
  $('cam-label').textContent = cams.length ? `${cams.length} photo positions` : 'none';
  ctx.showCamera = show;
  ctx.startPose = startPose;
}

// ------------------------------------------------------------------ studio tools
// ---- length measuring: click points in a chain; each segment shows its length in the view. A live line follows the
// pointer; Shift makes the next segment exactly level or upright; clicking the first point again closes a shape (area);
// Backspace removes the last point; Esc starts a new chain. Points are picked on the mesh, which stays under the
// photoreal splat (lined up to a few centimetres).
const UNITS = {
  m: { len: (m) => `${m.toFixed(2)} m`, area: (m2) => `${m2.toFixed(2)} m²` },
  cm: { len: (m) => `${(m * 100).toFixed(1)} cm`, area: (m2) => `${m2.toFixed(2)} m²` },
  ft: {
    len: (m) => { const inch = m / 0.0254, ft = Math.floor(inch / 12); return `${ft} ft ${(inch - 12 * ft).toFixed(1)} in`; },
    area: (m2) => `${(m2 / 0.09290304).toFixed(1)} ft²`,
  },
};

function polygonArea(pts) {                     // planar polygon in 3D: half the length of its Newell normal
  const n = new THREE.Vector3();
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    n.x += (a.y - b.y) * (a.z + b.z); n.y += (a.z - b.z) * (a.x + b.x); n.z += (a.x - b.x) * (a.y + b.y);
  }
  return n.length() / 2;
}

function wireMeasure(ctx) {
  const { app, pkg, eye } = ctx;
  const group = new THREE.Group();
  group.name = 'measurements';
  app.scene.add(group);
  // transparent: drawn in the late pass, after the photoreal splat (opaque lines were painted over by it)
  const dotMat = new THREE.MeshBasicMaterial({ color: 0xfacc15, depthTest: false, transparent: true });
  const lineMat = new THREE.LineBasicMaterial({ color: 0xfacc15, depthTest: false, transparent: true });
  const previewMat = new THREE.LineDashedMaterial({ color: 0xfacc15, dashSize: 0.03 * eye, gapSize: 0.02 * eye, depthTest: false,
    transparent: true });
  const m = { on: false, chains: [], current: null, shift: false };
  state.measure = m;
  const mpu = pkg.scale && pkg.scale.metres_per_unit;
  let unit = 'm';
  try { const u = localStorage.getItem('namma.measureUnit'); if (UNITS[u]) unit = u; } catch { /* no storage */ }
  $('measure-unit').value = unit;
  const fmt = (u) => (mpu ? UNITS[unit].len(u * mpu) : formatLength(u, pkg.scale));
  const fmtArea = (u2) => (mpu ? UNITS[unit].area(u2 * mpu * mpu) : `${u2.toFixed(2)} model units²`);
  const btn = $('measure-btn');
  const hint = (t) => { $('measure-hint').textContent = t; };
  const segs = (c) => {
    const s = c.points.slice(1).map((b, i) => [c.points[i], b]);
    if (c.closed) s.push([c.points[c.points.length - 1], c.points[0]]);
    return s;
  };
  const total = (c) => segs(c).reduce((t, [a, b]) => t + a.distanceTo(b), 0);

  // ---- live preview from the last point to the pointer
  const preview = {
    line: new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]), previewMat),
    label: labelSprite(' ', eye * 0.6), text: '',
  };
  preview.line.renderOrder = 9;
  const hidePreview = () => { preview.line.visible = false; preview.label.visible = false; };
  hidePreview();

  // ---- scene objects, rebuilt from the chains on every change (a handful of points: cheap)
  const redraw = () => {
    group.clear();
    for (const c of m.chains) {
      for (const p of c.points) {
        const dot = new THREE.Mesh(new THREE.SphereGeometry(0.012 * eye, 12, 8), dotMat);
        dot.position.copy(p);
        dot.renderOrder = 9;
        group.add(dot);
      }
      for (const [a, b] of segs(c)) {
        const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]), lineMat);
        line.renderOrder = 9;
        group.add(line);
        const label = labelSprite(fmt(a.distanceTo(b)), eye * 0.6);
        label.position.copy(a).add(b).multiplyScalar(0.5);
        group.add(label);
      }
      if (c.closed) {
        const centre = c.points.reduce((s, p) => s.add(p), new THREE.Vector3()).multiplyScalar(1 / c.points.length);
        const label = labelSprite(`area ${fmtArea(polygonArea(c.points))}`, eye * 0.7);
        label.position.copy(centre);
        group.add(label);
      }
    }
    group.add(preview.line, preview.label);
  };

  // ---- the list in the side panel
  const list = () => {
    const ol = $('measure-list');
    ol.innerHTML = '';
    m.chains.forEach((c, k) => {
      const head = document.createElement('li');
      const t = document.createElement('strong');
      t.textContent = `#${k + 1}` + (segs(c).length > 1 ? ` · total ${fmt(total(c))}` : '')
        + (c.closed ? ` · area ${fmtArea(polygonArea(c.points))}` : '');
      const del = document.createElement('button');
      del.type = 'button'; del.className = 'secondary'; del.textContent = '×';
      del.setAttribute('aria-label', `Delete measurement ${k + 1}`);
      del.onclick = () => { m.chains.splice(k, 1); if (m.current === c) m.current = null; hidePreview(); redraw(); list(); };
      head.append(t, del);
      ol.append(head);
      for (const [a, b] of segs(c)) {
        const li = document.createElement('li');
        const flat = Math.hypot(b.x - a.x, b.z - a.z), up = Math.abs(b.y - a.y);
        li.textContent = `${fmt(a.distanceTo(b))}  (across ${fmt(flat)}, height ${fmt(up)})`;
        ol.append(li);
      }
    });
    $('measure-panel').hidden = !m.on && !m.chains.length;
  };

  // ---- picking
  const screenXY = (e) => {
    if (document.pointerLockElement === app.canvas) {           // looking around: measure at the crosshair
      const r = app.canvas.getBoundingClientRect();
      return [r.left + r.width / 2, r.top + r.height / 2];
    }
    return [e.clientX, e.clientY];
  };
  const constrain = (a, p) => {                                 // Shift: exactly level, or exactly upright
    const flat = Math.hypot(p.x - a.x, p.z - a.z), up = Math.abs(p.y - a.y);
    return up > flat ? new THREE.Vector3(a.x, p.y, a.z) : new THREE.Vector3(p.x, a.y, p.z);
  };
  const hitAt = (x, y) => {
    const hit = app.pick(x, y, app.appearance ? [app.appearance] : []);
    return hit ? hit.point.clone() : null;
  };
  const nearFirst = (c, x, y) => {                              // within 14 px of the chain's first point on screen
    if (!c || c.points.length < 3) return false;
    const r = app.canvas.getBoundingClientRect();
    const s = c.points[0].clone().project(app.camera);
    return Math.hypot((s.x + 1) / 2 * r.width + r.left - x, (1 - s.y) / 2 * r.height + r.top - y) < 14;
  };
  const pick = (e) => {
    const [x, y] = screenXY(e);
    if (nearFirst(m.current, x, y)) {                           // close the shape
      m.current.closed = true;
      m.current = null;
      hidePreview();
      hint('Shape closed: its area is shown. Click to start a new measurement.');
      redraw(); list();
      return;
    }
    let p = hitAt(x, y);
    if (!p) { toast('No surface there – click on the room.'); return; }
    if (!m.current) { m.current = { points: [], closed: false }; m.chains.push(m.current); }
    const c = m.current;
    if (c.points.length && (e.shiftKey || m.shift)) p = constrain(c.points[c.points.length - 1], p);
    c.points.push(p);
    hint(c.points.length === 1 ? 'Click the next point (hold Shift: level / upright).'
      : 'Click to continue, the first point to close the shape, Esc for a new measurement.');
    redraw(); list();
  };

  let pending = null;
  const updatePreview = () => {
    const e = pending;
    pending = null;
    const c = m.current;
    if (!m.on || !c || !c.points.length || !e) { hidePreview(); return; }
    const [x, y] = screenXY(e);
    let p = hitAt(x, y);
    if (!p) { hidePreview(); return; }
    const a = c.points[c.points.length - 1];
    if (m.shift) p = constrain(a, p);
    if (nearFirst(c, x, y)) p = c.points[0].clone();
    preview.line.geometry.setFromPoints([a, p]);
    preview.line.computeLineDistances();
    const text = fmt(a.distanceTo(p));
    if (text !== preview.text) {
      group.remove(preview.label);
      preview.label = labelSprite(text, eye * 0.6);
      preview.text = text;
      group.add(preview.label);
    }
    preview.label.position.copy(a).add(p).multiplyScalar(0.5);
    preview.line.visible = preview.label.visible = true;
  };
  app.canvas.addEventListener('mousemove', (e) => {
    if (!m.on) return;
    if (!pending) requestAnimationFrame(updatePreview);
    pending = e;
  });

  // ---- actions
  const undo = () => {
    const c = m.current || m.chains[m.chains.length - 1];
    if (!c) return;
    if (c.closed) c.closed = false;
    else c.points.pop();
    if (!c.points.length) { m.chains.splice(m.chains.indexOf(c), 1); m.current = null; } else m.current = c;
    hidePreview(); redraw(); list();
  };
  const setOn = (on) => {
    m.on = on;
    if (on && document.pointerLockElement) document.exitPointerLock();
    app.onPick = on ? pick : (app.onPick === pick ? null : app.onPick);
    btn.textContent = on ? 'Stop measuring' : 'Measure';
    btn.classList.toggle('active', on);
    if (!on) { m.current = null; hidePreview(); }
    hint(on ? 'Click two or more points on the room to measure between them.' : '');
    list();
  };
  btn.onclick = () => setOn(!m.on);
  $('measure-clear').onclick = () => {
    m.chains = [];
    m.current = null;
    hidePreview(); redraw();
    if (m.on) hint('Click two or more points on the room to measure between them.');
    list();
  };
  $('measure-undo').onclick = undo;
  $('measure-unit').onchange = (e) => {
    unit = e.target.value;
    try { localStorage.setItem('namma.measureUnit', unit); } catch { /* no storage */ }
    preview.text = '';
    redraw(); list();
  };
  $('measure-copy').onclick = async () => {
    const lines = [...$('measure-list').children].map((li) => {
      const head = li.querySelector('strong');
      return head ? head.textContent : `  ${li.textContent}`;
    });
    try { await navigator.clipboard.writeText(lines.join('\n')); toast('Measurements copied.'); } catch { toast('Could not copy (clipboard not allowed here).'); }
  };
  window.addEventListener('keydown', (e) => {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target && e.target.tagName)) return;
    if (e.key === 'Shift') m.shift = true;
    if (e.code === 'KeyM') setOn(!m.on);
    else if (e.code === 'Escape' && m.on && m.current) { m.current = null; hidePreview(); hint('New measurement: click its first point.'); }
    else if (e.code === 'Backspace' && m.on) { e.preventDefault(); undo(); }
  });
  window.addEventListener('keyup', (e) => { if (e.key === 'Shift') m.shift = false; });
  redraw();
  if (!mpu) hint('The scale of this model is unknown: lengths are in model units.');
}

function wireStudioTools(ctx) {
  const { app } = ctx;
  const meshTargets = () => (app.appearance ? [app.appearance] : []);
  $('tool-poi').onclick = () => {
    if (document.pointerLockElement) document.exitPointerLock();
    $('tool-msg').textContent = 'Click on the object or spot you want to label.';
    app.onPick = (e) => {
      const hit = app.pick(e.clientX, e.clientY, meshTargets());
      if (!hit) { $('tool-msg').textContent = 'No surface there – click on the model.'; return; }
      app.onPick = null;
      openPoiForm(ctx, hit.point);
    };
  };
  $('poi-cancel').onclick = () => { $('poi-form').hidden = true; $('tool-msg').textContent = ''; };
  $('poi-form-el').onsubmit = async (e) => {
    e.preventDefault();
    const pt = ctx.pendingPoint;
    const cam = app.camera.position;
    const body = { name: $('poi-name').value.trim(), category: $('poi-cat').value.trim(),
      aliases: $('poi-alias').value.split(',').map((s) => s.trim()).filter(Boolean),
      position: [pt.x, pt.y, pt.z], view: { position: [cam.x, cam.y, cam.z], target: [pt.x, pt.y, pt.z] } };
    try {
      const r = await api('/pois', { method: 'POST', body: JSON.stringify(body) });
      ctx.pois = r.pois;
      renderPois(ctx);
      $('poi-form').hidden = true;
      $('tool-msg').textContent = `Saved "${body.name}".`;
      toast(`Label saved: ${body.name}`);
    } catch (err) {
      $('tool-msg').textContent = `Could not save: ${err.message}`;
    }
  };
  // measuring / scale
  $('tool-measure').onclick = () => {
    if (document.pointerLockElement) document.exitPointerLock();
    app.measureGroup.clear();
    ctx.measure = [];
    $('measure-box').hidden = false;
    $('measure-value').textContent = '–';
    $('tool-msg').textContent = 'Click the first point of a distance you know (e.g. a door width).';
    app.onPick = (e) => {
      const hit = app.pick(e.clientX, e.clientY, meshTargets());
      if (!hit) return;
      ctx.measure.push(hit.point.clone());
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.025 * ctx.eye, 12, 8), new THREE.MeshBasicMaterial({ color: 0xef4444, depthTest: false }));
      dot.position.copy(hit.point);
      dot.renderOrder = 9;
      app.measureGroup.add(dot);
      if (ctx.measure.length === 2) {
        app.onPick = null;
        const [a, b] = ctx.measure;
        app.measureGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]), new THREE.LineBasicMaterial({ color: 0xef4444, depthTest: false })));
        ctx.measureUnits = a.distanceTo(b);
        $('measure-value').textContent = formatLength(ctx.measureUnits, ctx.pkg.scale);
        $('tool-msg').textContent = 'Distance measured.';
      } else {
        $('tool-msg').textContent = 'Now click the second point.';
      }
    };
  };
  $('measure-apply').onclick = async () => {
    const metres = Number($('measure-real').value);
    if (!(metres > 0) || !ctx.measure || ctx.measure.length !== 2) { $('tool-msg').textContent = 'Measure two points and enter a length first.'; return; }
    const [a, b] = ctx.measure;
    $('tool-msg').textContent = 'Saving the scale and rebuilding the package…';
    try {
      await api('/calibration', { method: 'POST', body: JSON.stringify({ p1: a.toArray(), p2: b.toArray(), metres }) });
      $('tool-msg').textContent = 'Scale saved. Reloading…';
      setTimeout(() => location.reload(), 600);
    } catch (err) {
      $('tool-msg').textContent = `Could not set the scale: ${err.message}`;
    }
  };
}

function openPoiForm(ctx, point) {
  ctx.pendingPoint = point;
  $('poi-name').value = '';
  $('poi-cat').value = '';
  $('poi-alias').value = '';
  $('poi-form').hidden = false;
  $('poi-name').focus();
}

// ------------------------------------------------------------------ benchmark + self-test
const pct = (arr, p) => { const s = [...arr].sort((a, b) => a - b); return s.length ? s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))] : null; };

async function runBenchmark(ctx, seconds) {
  const { app, walker, eye } = ctx;
  cancelAnimationFrame(app.raf);
  let hiddenAt = null;
  const onVis = () => { if (document.visibilityState !== 'visible' && hiddenAt === null) hiddenAt = performance.now(); };
  document.addEventListener('visibilitychange', onVis);
  const p0 = app.camera.position.clone();
  const yaw0 = walker.yaw;
  const intervals = [];
  await new Promise((resolve) => {
    const t0 = performance.now();
    let last = t0;
    const step = (now) => {
      intervals.push(now - last);
      last = now;
      const t = (now - t0) / 1000;
      walker.yaw = yaw0 + t * 0.6;           // deterministic look-around while circling the start point
      walker.pitch = -0.12 + 0.1 * Math.sin(t);
      app.camera.position.set(p0.x + 0.4 * eye * Math.cos(t * 0.3), p0.y, p0.z + 0.4 * eye * Math.sin(t * 0.3));
      walker.apply();
      app.render();
      if (t < seconds) requestAnimationFrame(step); else resolve();
    };
    requestAnimationFrame(step);
  });
  document.removeEventListener('visibilitychange', onVis);
  const frames = intervals.slice(1);
  const visible = hiddenAt === null && document.visibilityState === 'visible';
  const gl = app.renderer.getContext();
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  const res = {
    valid: visible && frames.length >= seconds * 10,
    invalid_reason: !visible ? 'page not visible during run' : frames.length < seconds * 10 ? `only ${frames.length} frames delivered` : null,
    seconds, frames: frames.length,
    frame_ms: { median: pct(frames, 50), p95: pct(frames, 95), max: Math.max(...frames), mean: frames.reduce((a, b) => a + b, 0) / frames.length },
    over_33ms_share: frames.filter((f) => f > 33.4).length / frames.length,
    lod: state.lodShown, triangles: state.triangles, draw_calls_last_frame: app.renderer.info.render.calls,
    canvas_px: [app.renderer.domElement.width, app.renderer.domElement.height], device_pixel_ratio: window.devicePixelRatio,
    gpu: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
    js_heap_mb: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 2 ** 20) : null,
    gpu_memory_textures: app.renderer.info.memory.textures, gpu_memory_geometries: app.renderer.info.memory.geometries,
    user_agent: navigator.userAgent, timing: state.timing, package_id: ctx.pkg.package_id, page_url: location.href,
    at: new Date().toISOString(),
  };
  window.__nammaBench = res;
  $('bench-box').hidden = false;
  $('bench-summary').textContent = res.valid ? `VALID · median ${res.frame_ms.median.toFixed(1)} ms · p95 ${res.frame_ms.p95.toFixed(1)} ms · LOD ${res.lod}`
    : `INVALID: ${res.invalid_reason}`;
  fetch('/__bench', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(res) }).catch(() => {});
  let last = performance.now();
  const tick = (now) => { const dt = Math.min(0.1, (now - last) / 1000); last = now; if (app.tween) app.tween(now); else walker.update(dt); app.render(); app.raf = requestAnimationFrame(tick); };
  app.raf = requestAnimationFrame(tick);
  return res;
}

async function runSelftest(ctx, startPose) {
  const { app, walker, eye } = ctx;
  const results = [];
  const check = (id, name, pass, detail) => results.push({ id, name, pass: !!pass, detail: String(detail) });
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  check('ST-01', 'package verified and model shown', state.phase === 'ready' && state.triangles > 0, `${state.triangles} triangles, LOD ${state.lodShown}`);
  startPose();
  const p0 = app.camera.position.clone();
  check('ST-02', 'starts at the start view at eye height', Math.abs(p0.y - eye) < 1e-6, `y=${p0.y.toFixed(3)} eye=${eye.toFixed(3)}`);
  const stepKeys = (code, n) => { walker.keys.add(code); for (let k = 0; k < n; k++) walker.update(1 / 60); walker.keys.delete(code); for (let k = 0; k < 20; k++) walker.update(1 / 60); };
  stepKeys('KeyW', 40);                       // deterministic controller steps (rendering may be throttled)
  const moved = app.camera.position.distanceTo(p0);
  check('ST-03', 'W moves the camera (or is stopped by an obstacle)', moved > 0.05 * eye || walker.blockedMoves > 0, `moved ${moved.toFixed(3)} units, blocked moves ${walker.blockedMoves}`);
  const y0 = walker.yaw; walker.look(120, 0);
  check('ST-04', 'mouse look turns the view', Math.abs(walker.yaw - y0) > 0.1, `yaw change ${(walker.yaw - y0).toFixed(3)} rad`);
  walker.setMode('fly'); const fy = app.camera.position.y; stepKeys('KeyE', 30);
  check('ST-05', 'fly mode moves up', app.camera.position.y > fy + 0.02 * eye, `dy ${(app.camera.position.y - fy).toFixed(3)}`);
  startPose();
  check('ST-06', 'reset returns to start in walk mode', walker.mode === 'walk' && app.camera.position.distanceTo(p0) < 1e-6, `mode ${walker.mode}`);
  const g = walker.grid;
  let blockedTest = g ? 'no free spot next to an obstacle found' : 'no roam grid';
  let blockedOk = false;
  if (g) {
    // a spot where the body fits, with a not-allowed cell a few cells further along +x: walk into it
    const reach = Math.ceil(walker.radius / g.cell) + 4;
    outer: for (let r = 1; r < g.height - 1; r++) for (let c = 1; c < g.width - reach; c++) {
      const x = g.ox + (c + 0.5) * g.cell, z = g.oz + (r + 0.5) * g.cell;
      let wall = -1;
      for (let k = 1; k <= reach; k++) if (!g.allowed[r * g.width + c + k]) { wall = c + k; break; }
      if (wall < 0 || !walker.ok(x, z)) continue;
      walker.setPose([x, eye, z], [x + 1, eye, z]);
      stepKeys('KeyW', 180);
      const cx = app.camera.position.x;
      const wallX = g.ox + wall * g.cell;
      blockedOk = cx < wallX && isAllowed(g, cx, app.camera.position.z);
      blockedTest = `stopped at x=${cx.toFixed(3)}, obstacle cell starts at x=${wallX.toFixed(3)}`;
      break outer;
    }
  }
  check('ST-07', 'walking stops at reconstructed obstacles / edge', blockedOk, blockedTest);
  startPose();
  if (ctx.pois.length) {
    $('search').value = ctx.pois[0].name;
    const res = ctx.search();
    for (let k = 0; k < 120 && app.tween; k++) app.tween(performance.now() + k * 20);
    await wait(50);
    check('ST-08', 'search finds a labelled place and jumps to it', res.status !== 'not_found' && state.lastJump, `${res.status}; jumped to ${state.lastJump && state.lastJump.poi_id}`);
  } else {
    check('ST-08', 'search (no labelled places in this package)', true, 'not measurable: no POIs');
  }
  check('ST-09', 'pointer lock available', typeof app.canvas.requestPointerLock === 'function', 'requestPointerLock present');
  const visible = document.visibilityState === 'visible' && document.hasFocus();
  check('ST-10', 'frames are being rendered', app.frameTimes.length > 10 || !visible,
    app.frameTimes.length > 10 ? `${app.frameTimes.length} frames recorded` : `not measurable: page hidden or unfocused (${app.frameTimes.length} frames)`);
  startPose();
  const passed = results.filter((r) => r.pass).length;
  const out = { passed, total: results.length, allPassed: passed === results.length, results };
  window.__nammaSelftest = out;
  $('bench-box').hidden = false;
  $('bench-summary').textContent = `self-test ${passed}/${results.length}: ` + results.map((r) => `${r.pass ? 'PASS' : 'FAIL'} ${r.id}`).join(', ');
  return out;
}

main().catch((e) => fatal('unexpected', 'Something went wrong while starting the viewer.', e.stack || e.message));
