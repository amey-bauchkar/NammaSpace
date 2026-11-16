// AR Walk: the walking path drawn over the phone's camera picture, with turn-by-turn guidance.
//
//   startArWalk({ poly, mpu, scaleAssumed, destName, starts, onExit })
//
// Camera: getUserMedia (back camera) in a full-screen <video>; a transparent three.js canvas on top.
// Orientation: deviceorientation (iOS: DeviceOrientationEvent.requestPermission from the tap), smoothed by an
// adaptive low-pass (OrientationFilter) so the arrows stay steady. Position: step counting (StepDetector from the
// phone-capture page) with dead reckoning matched to the path (PathTracker). The yaw between the model and the real
// room is set when the visitor faces along the path and taps Start (and again with Re-align).
// No network use beyond the page's own files; nothing is recorded or sent.

import * as THREE from 'three';
import { StepDetector } from './captureLogic.js';
import { pointAlong } from './routePlan.js';
import {
  alignYaw, chevronPlacements, coverFov, earthToWorld, HAPTIC, matToQuat, modelToWorld, motionMagnitude,
  OrientationFilter, PathTracker, screenRotation, worldHeading, wrap,
} from './arNavLogic.js';

const $ = (id) => document.getElementById(id);
const PHONE_HEIGHT_M = 1.35;              // camera height when the phone is held in front of the chest
const CHEVRON_SPACING_M = 0.9;
const ICON = { straight: '↑', left: '↰', right: '↱', 'slight-left': '↖', 'slight-right': '↗', arrive: '◎', arrived: '✓' };

const vibrate = (p) => { try { if (navigator.vibrate) navigator.vibrate(p); } catch { /* not supported */ } };

/** Why AR cannot start here (null when it can). */
export function arUnavailableReason() {
  if (!window.isSecureContext) return 'AR Walk needs a secure page (https:// or localhost): the camera and motion sensors are blocked on plain http.';
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return 'This browser does not give web pages access to the camera.';
  if (typeof window.DeviceOrientationEvent === 'undefined') return 'This device has no orientation sensor available to the browser.';
  return null;
}

async function askSensorPermission() {
  // iOS 13+: must be called directly from the tap, before any other await
  const asks = [];
  if (typeof DeviceOrientationEvent !== 'undefined' && typeof DeviceOrientationEvent.requestPermission === 'function') asks.push(DeviceOrientationEvent.requestPermission());
  if (typeof DeviceMotionEvent !== 'undefined' && typeof DeviceMotionEvent.requestPermission === 'function') asks.push(DeviceMotionEvent.requestPermission());
  const res = await Promise.all(asks.map((p) => p.catch(() => 'denied')));
  if (res.some((r) => r !== 'granted')) throw new Error('Motion and orientation access was not allowed. Allow it in the browser settings and try again.');
}

function showModal(title, message, onClose) {
  $('ar-modal-title').textContent = title;
  $('ar-modal-text').textContent = message;
  $('ar-modal').hidden = false;
  $('ar-modal-ok').onclick = () => { $('ar-modal').hidden = true; if (onClose) onClose(); };
}

function chevronGeometry() {
  const s = 0.32;
  const sh = new THREE.Shape();
  sh.moveTo(0, s); sh.lineTo(s * 0.85, -s * 0.55); sh.lineTo(0, -s * 0.12); sh.lineTo(-s * 0.85, -s * 0.55); sh.closePath();
  const g = new THREE.ShapeGeometry(sh);
  g.rotateX(-Math.PI / 2);                // flat on the floor, tip towards -Z
  return g;
}

/** Build the AR scene for a path. Returns { group, update(tSeconds, alongMetres), dispose }. */
function buildScene(scene, poly, mpu) {
  const group = new THREE.Group();
  scene.add(group);
  const places = chevronPlacements(poly, CHEVRON_SPACING_M / mpu);
  const geo = chevronGeometry();
  const core = new THREE.InstancedMesh(geo, new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, depthWrite: false }), Math.max(1, places.length));
  const glow = new THREE.InstancedMesh(geo, new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.35, depthWrite: false,
    blending: THREE.AdditiveBlending }), Math.max(1, places.length));
  core.count = glow.count = places.length;
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0), one = new THREE.Vector3(1, 1, 1), big = new THREE.Vector3(1.45, 1, 1.45);
  places.forEach((p, i) => {
    q.setFromAxisAngle(up, p.heading + Math.PI);  // tip (-Z) along the heading
    m.compose(new THREE.Vector3(p.x * mpu, 0.01, p.z * mpu), q, one); core.setMatrixAt(i, m);
    m.compose(new THREE.Vector3(p.x * mpu, 0.005, p.z * mpu), q, big); glow.setMatrixAt(i, m);
  });
  core.instanceMatrix.needsUpdate = glow.instanceMatrix.needsUpdate = true;
  core.renderOrder = 2; glow.renderOrder = 1;
  group.add(glow, core);
  // destination beacon: a pole, a spinning diamond that bobs, and a pulsing ring on the floor
  const end = poly[poly.length - 1], ex = end[0] * mpu, ez = end[1] * mpu;
  const yellow = new THREE.Color(0xf7d794), coral = new THREE.Color(0xeda6a3), white = new THREE.Color(0xfcfbfb);
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, 1.6, 10), new THREE.MeshBasicMaterial({ color: yellow, transparent: true, opacity: 0.85 }));
  pole.position.set(ex, 0.8, ez);
  const gem = new THREE.Mesh(new THREE.OctahedronGeometry(0.16), new THREE.MeshBasicMaterial({ color: coral }));
  const ring = new THREE.Mesh(new THREE.RingGeometry(0.28, 0.36, 40), new THREE.MeshBasicMaterial({ color: yellow, transparent: true, side: THREE.DoubleSide, depthWrite: false }));
  ring.rotation.x = -Math.PI / 2;
  ring.position.set(ex, 0.01, ez);
  group.add(pole, gem, ring);
  const col = new THREE.Color();
  return {
    group,
    update(t, alongM) {
      // a wave of light runs from the visitor towards the destination; arrows behind the visitor fade out
      for (let i = 0; i < places.length; i++) {
        const dm = places[i].d * mpu;
        const ahead = dm - alongM;
        const fade = ahead < -0.5 ? 0.12 : ahead > 25 ? 0.25 : 1;
        const wave = 0.5 + 0.5 * Math.sin(t * 5 - dm * 1.6);
        col.copy(yellow).lerp(white, wave * 0.6).multiplyScalar(fade);
        core.setColorAt(i, col);
        glow.setColorAt(i, col.copy(coral).multiplyScalar(fade * (0.4 + 0.6 * wave)));
      }
      if (core.instanceColor) core.instanceColor.needsUpdate = true;
      if (glow.instanceColor) glow.instanceColor.needsUpdate = true;
      gem.position.set(ex, 1.75 + 0.08 * Math.sin(t * 2.2), ez);
      gem.rotation.y = t * 1.5;
      const p = (t * 0.8) % 1;
      ring.scale.setScalar(1 + p * 1.6);
      ring.material.opacity = 0.9 * (1 - p);
    },
    dispose() {
      scene.remove(group);
      group.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
    },
  };
}

/**
 * Start AR Walk. poly: path in model units [[x, z], ...] (start first); mpu: metres per model unit;
 * starts: [{ label, along }] choices for "I am at" (along = model units on the path); onExit(): back to the 3D view.
 */
export async function startArWalk({ poly, mpu, scaleAssumed = false, destName = 'the destination', starts = [], onExit = () => {} }) {
  const exitTo3d = () => onExit();
  const why = arUnavailableReason();
  if (why) { showModal('AR Walk is not available', why, exitTo3d); return null; }
  if (!poly || poly.length < 2) { showModal('No path yet', 'Choose a place and show the walking path first; AR Walk follows that path.', exitTo3d); return null; }
  let stream;
  try {
    await askSensorPermission();
    stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } } });
  } catch (e) {
    const msg = e && e.name === 'NotAllowedError' ? 'Camera access was not allowed. Allow the camera for this site and try again.'
      : e && e.name === 'NotFoundError' ? 'No camera was found on this device.' : (e && e.message) || String(e);
    showModal('AR Walk could not start', msg, exitTo3d);
    return null;
  }

  const layer = $('ar-layer'), video = $('ar-video');
  layer.hidden = false;
  document.body.classList.add('ar-on');
  video.srcObject = stream;
  try { await video.play(); } catch { /* autoplay with playsinline + muted normally works */ }

  // ---- renderer (transparent, over the video)
  const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.setClearColor(0x000000, 0);
  $('ar-canvas-host').appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(60, 1, 0.05, 200);
  const fovOverride = Number(new URLSearchParams(location.search).get('arfov')) || 0;
  const resize = () => {
    const w = window.innerWidth, h = window.innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.fov = fovOverride || (video.videoWidth ? coverFov(w, h, video.videoWidth, video.videoHeight) : 60);
    camera.updateProjectionMatrix();
  };
  resize();
  window.addEventListener('resize', resize);
  video.addEventListener('loadedmetadata', resize);

  const world = buildScene(scene, poly, mpu);
  const pivot = new THREE.Group();                // yaw of the model in the room
  scene.add(pivot);
  pivot.add(world.group);

  // ---- sensors
  const filter = new OrientationFilter();
  const steps = new StepDetector();
  let qWorld = null, Rw = null, sensorSeen = false;
  const screenAngle = () => (screen.orientation && typeof screen.orientation.angle === 'number' ? screen.orientation.angle : (window.orientation || 0));
  const onOrient = (e) => {
    if (e.alpha == null && e.beta == null) return;
    sensorSeen = true;
    Rw = earthToWorld(screenRotation(e.alpha, e.beta, e.gamma, screenAngle()));
    qWorld = filter.update(matToQuat(Rw), performance.now());
  };
  const tracker = new PathTracker(poly, { mpu });
  let theta = null;                               // model -> world yaw (set by Start / Re-align)
  let lastTurnBuzz = -1, arrived = false, offBuzz = false;
  const heading = () => (Rw ? worldHeading(Rw) : null);
  const modelHeading = () => { const h = heading(); return h === null || theta === null ? null : wrap(h - theta); };
  const onStep = () => {
    if (theta === null || arrived) return;
    const st = tracker.step(sim ? null : modelHeading());
    if (sim) align();                              // simulated phone keeps facing along the path
    refresh(st);
  };
  const onMotion = (e) => { const a = motionMagnitude(e); if (a !== null && steps.update(a, performance.now())) onStep(); };
  window.addEventListener('deviceorientation', onOrient);
  window.addEventListener('devicemotion', onMotion);

  // ---- HUD
  const setup = $('ar-setup'), hud = $('ar-hud');
  const startSel = $('ar-start');
  startSel.innerHTML = '';
  for (const [i, s] of [{ label: 'The start of the path', along: 0 }, ...starts].entries()) {
    const o = document.createElement('option');
    o.value = String(i); o.textContent = s.label; o.dataset.along = String(s.along);
    startSel.appendChild(o);
  }
  $('ar-dest').textContent = destName;
  $('ar-scale-note').hidden = !scaleAssumed;
  setup.hidden = false; hud.hidden = true;

  function refresh(st) {
    $('ar-icon').textContent = ICON[st.kind] || '↑';
    $('ar-text').textContent = st.text;
    $('ar-remaining').textContent = st.kind === 'arrived' ? destName : `${st.remaining.toFixed(st.remaining < 10 ? 1 : 0)} m to ${destName}`;
    hud.dataset.state = st.offPath ? 'off' : st.kind === 'arrived' ? 'arrived' : st.kind === 'straight' || st.kind === 'arrive' ? 'go' : 'turn';
    if (st.turnIndex >= 0 && st.metres <= 2 && st.turnIndex !== lastTurnBuzz) { lastTurnBuzz = st.turnIndex; vibrate(HAPTIC.turn); }
    if (st.offPath && !offBuzz) { offBuzz = true; vibrate(HAPTIC.offPath); } else if (!st.offPath) offBuzz = false;
    if (st.kind === 'arrived' && !arrived) { arrived = true; vibrate(HAPTIC.arrive); }
    window.__nammaAr = { ...st, theta, sensor: sensorSeen, simulated: sim };
  }

  const align = () => {
    const h = heading();
    if (h === null) return false;
    const [dx, dz] = tracker.direction();
    theta = alignYaw(h, dx, dz);
    return true;
  };
  $('ar-go').onclick = () => {
    const opt = startSel.options[startSel.selectedIndex];
    tracker.placeAt(Number(opt ? opt.dataset.along : 0) || 0);
    if (!align()) {
      showModal('Waiting for the motion sensor', 'No orientation reading yet: hold the phone still for a moment and tap Start again.');
      return;
    }
    arrived = false; lastTurnBuzz = -1;
    setup.hidden = true; hud.hidden = false;
    refresh(tracker.state());
  };
  $('ar-realign').onclick = () => { if (align()) refresh(tracker.state()); };
  $('ar-step').onclick = () => onStep();
  $('ar-exit').onclick = () => stop();
  $('ar-setup-exit').onclick = () => stop();
  // no sensor event within 2 s: say so (desktop browsers expose the API but never fire it)
  // Desktop / no sensor: simulation - the view looks along the path and "Step" walks it (for testing and demos)
  let sim = false;
  const simulate = () => {
    sim = true;
    Rw = earthToWorld(screenRotation(0, 80, 0, 0));
    qWorld = matToQuat(Rw);
  };
  const sensorTimer = setTimeout(() => {
    if (sensorSeen) return;
    simulate();
    showModal('Simulation mode', 'This device sends no orientation readings to the browser (desktop computers usually have none). '
      + 'AR Walk continues as a simulation: press Start, then "Step" to walk the path one stride at a time.');
  }, 2000);

  // ---- render loop
  const v = new THREE.Vector3(), q = new THREE.Quaternion();
  let raf = 0, stopped = false;
  const tick = (now) => {
    if (stopped) return;
    raf = requestAnimationFrame(tick);
    if (qWorld) camera.quaternion.set(qWorld[0], qWorld[1], qWorld[2], qWorld[3]);
    if (theta !== null) {
      pivot.quaternion.copy(q.setFromAxisAngle(v.set(0, 1, 0), theta));
      const [wx, wz] = modelToWorld(tracker.pos[0], tracker.pos[1], theta, mpu);
      camera.position.set(wx, PHONE_HEIGHT_M, wz);
    }
    world.update(now / 1000, tracker.along * mpu);
    renderer.render(scene, camera);
  };
  raf = requestAnimationFrame(tick);

  function stop() {
    if (stopped) return;
    stopped = true;
    cancelAnimationFrame(raf);
    clearTimeout(sensorTimer);
    window.removeEventListener('deviceorientation', onOrient);
    window.removeEventListener('devicemotion', onMotion);
    window.removeEventListener('resize', resize);
    stream.getTracks().forEach((t) => t.stop());
    video.srcObject = null;
    world.dispose();
    renderer.dispose();
    renderer.domElement.remove();
    layer.hidden = true;
    setup.hidden = true; hud.hidden = true;
    document.body.classList.remove('ar-on');
    exitTo3d();
  }
  return { stop, tracker };
}

/** "I am at" choices: labelled places close to the path (within 1.5 m), at their nearest point on it. */
export function startChoices(poly, pois, mpu) {
  const out = [];
  for (const p of pois || []) {
    let best = Infinity, along = 0, acc = 0;
    for (let k = 1; k < poly.length; k++) {
      const ax = poly[k - 1][0], az = poly[k - 1][1], dx = poly[k][0] - ax, dz = poly[k][1] - az, L = Math.hypot(dx, dz);
      const t = L > 0 ? Math.max(0, Math.min(1, ((p.position[0] - ax) * dx + (p.position[2] - az) * dz) / (L * L))) : 0;
      const d = Math.hypot(ax + dx * t - p.position[0], az + dz * t - p.position[2]);
      if (d < best) { best = d; along = acc + L * t; }
      acc += L;
    }
    if (best * mpu <= 1.5 && along > 0) out.push({ label: p.name, along, point: pointAlong(poly, along).point });
  }
  return out.sort((a, b) => a.along - b.along);
}
