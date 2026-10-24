// Phone capture page (served by namma.studio.phone_capture over HTTPS), Polycam-style UI.
//
// Phone work is kept small: in VIDEO mode (default) it only records with the hardware encoder and uploads 2-s pieces;
// the computer picks frames. PHOTO FRAMES mode analyses the image on the phone (captureLogic.KeyframeSelector).
// Both modes track coverage from the motion sensors (captureLogic.CoverageMap) and show it as a percentage; the camera
// image itself is left clean (a tint painted over it was confusing).
import { CoverageMap, KeyframeSelector, StepDetector, exposure, grey, rotationMatrix, sharpness,
  thumb } from './captureLogic.js';

const $ = (id) => document.getElementById(id);
const showScreen = (id) => { for (const s of ['s-pair', 'cam', 's-status']) $(s).hidden = s !== id; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ANALYSE_W = 160; const ANALYSE_H = 120;
const CHUNK_MS = 2000; const VIDEO_BPS = 16_000_000; const MIN_VIDEO_S = 8;
const FOV_LONG = 65; const FOV_SHORT = 51;        // iPhone main camera (26 mm equiv.), 4:3: degrees
const STEPS_PER_POSITION = 2;                     // a new "position" for coverage every 2 steps
const S = {
  token: null, pid: null, mode: 'video', state: 'idle', // idle -> armed -> recording <-> paused -> done
  queue: [], sent: 0, sentBytes: 0, failed: 0, uploading: false, frames: [], sensors: [],
  stream: null, recorder: null, ext: 'mp4', seq: 0, recMs: 0, recFrom: null,
  orient: null, gyroRad: 0, rotSinceKey: 0, rotSinceWalk: 0, lastMotionT: null, alpha: null, beta: null, gamma: null,
  cov: new CoverageMap(), steps: new StepDetector(), issue: null, torchOn: false, wake: null,
};
try { S.token = sessionStorage.getItem('namma-token'); S.pid = sessionStorage.getItem('namma-pid'); } catch { /* private mode */ }

// opened from the Studio's QR code: https://<computer>:8443/#c=<code>. The code is in the fragment (never sent to the
// server); wipe it from the address bar at once. iOS only grants motion sensors after a tap, so one big button remains.
const qrCode = new URLSearchParams(location.hash.slice(1)).get('c');
if (qrCode) {
  history.replaceState(null, '', location.pathname);
  $('code').value = qrCode;
  S.fromQr = true;
  for (const el of document.querySelectorAll('#s-pair > :not(#pair-btn):not(#pair-msg):not(h1):not(#polycam-btn):not(#polycam-hint)')) el.hidden = true;
  // Polycam export is the primary route; scanning with this page is the secondary one
  $('polycam-btn').style.cssText = 'order:1;padding:28px;font-size:21px;margin-top:24px;background:#fff;color:#000;border-color:#fff';
  $('polycam-hint').style.order = 2;
  $('pair-btn').textContent = 'Or scan with this phone';
  $('pair-btn').style.cssText = 'order:3;padding:18px;font-size:18px;margin-top:22px;background:transparent;color:#fff;border-color:rgba(255,255,255,.75)';
  $('pair-msg').style.order = 4;
}

async function api(path, opts = {}) {
  const r = await fetch(`/api${path}`, { ...opts, headers: { ...(S.token ? { Authorization: `Bearer ${S.token}` } : {}),
    ...(opts.body && !(opts.body instanceof Blob) ? { 'Content-Type': 'application/json' } : {}), ...(opts.headers || {}) } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(body.message || `HTTP ${r.status}`), { code: body.error, status: r.status });
  return body;
}

// ------------------------------------------------------------------ pairing
$('pair-btn').onclick = async () => {
  $('pair-msg').textContent = '';
  try {
    // iOS: motion / orientation permission must be asked from a tap
    for (const E of [window.DeviceMotionEvent, window.DeviceOrientationEvent]) {
      if (E && E.requestPermission) await E.requestPermission().catch(() => 'denied');
    }
    const { token } = await api('/pair', { method: 'POST', body: JSON.stringify({ code: $('code').value.trim() }) });
    S.token = token;
    const { project_id: pid } = await api('/session', { method: 'POST', body: JSON.stringify({ title: $('title').value }) });
    S.pid = pid;
    try { sessionStorage.setItem('namma-token', token); sessionStorage.setItem('namma-pid', pid); } catch { /* optional */ }
    await openCamera();
    if (S.fromQr) { S.state = 'armed'; hint('Move device to start'); updateShutter(); }   // straight into scanning
  } catch (e) {
    $('pair-msg').textContent = e.name === 'NotAllowedError'
      ? 'Camera access was refused. Allow the camera for this page in the browser settings.' : e.message;
  }
};

// ------------------------------------------------------------------ Polycam import (secondary to scanning here)
// The user exports from Polycam (GLTF, optionally Images) to the Files app and picks the file(s); they are streamed to
// the computer, recognised there (model / raw keyframes) and imported as a new space (namma.studio.polycam_bridge).
$('polycam-btn').onclick = () => { $('pair-msg').textContent = ''; $('polycam-files').click(); };
$('polycam-files').onchange = async () => {
  const files = [...$('polycam-files').files];
  if (!files.length) return;
  try {
    if (!S.token) {
      const { token } = await api('/pair', { method: 'POST', body: JSON.stringify({ code: $('code').value.trim() }) });
      S.token = token;
      try { sessionStorage.setItem('namma-token', token); } catch { /* optional */ }
    }
    const { import_id: iid } = await api('/polycam', { method: 'POST', body: '{}' });
    showScreen('s-status');
    const total = files.reduce((a, f) => a + f.size, 0);
    let done = 0;
    for (const f of files) {
      $('st-title').textContent = 'Sending to the computer…';
      $('st-msg').textContent = `${f.name} (${(f.size / 2 ** 20).toFixed(1)} MB)`;
      await uploadWithProgress(`/api/polycam/${iid}/files/${encodeURIComponent(f.name)}`, f, (n) => {
        $('st-bar').style.width = `${Math.round((100 * (done + n)) / total)}%`;
      });
      done += f.size;
    }
    $('st-title').textContent = 'Importing…';
    $('st-msg').textContent = 'Finding the floor and preparing the walkthrough';
    const r = await api(`/polycam/${iid}/import`, { method: 'POST', body: JSON.stringify({ title: $('title').value }) });
    const ext = r.facts.extent_m_p1_p99.map((v) => v.toFixed(1)).join(' × ');
    $('st-title').textContent = 'Imported';
    $('st-bar').style.width = '100%';
    $('st-msg').textContent = `${ext} m, ${(r.facts.mesh_faces / 1000).toFixed(0)}k triangles`
      + (r.raw.length ? `, plus ${r.raw[0].keyframes} Polycam photos kept for later` : '')
      + '. Open the Studio on the computer to walk through it.';
  } catch (e) {
    showScreen('s-status');
    $('st-title').textContent = 'Import failed';
    $('st-msg').textContent = e.message;
  } finally {
    $('polycam-files').value = '';
  }
};

function uploadWithProgress(url, file, onProgress) {
  // XMLHttpRequest (not fetch) for upload progress on large exports
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('PUT', url);
    x.setRequestHeader('Authorization', `Bearer ${S.token}`);
    x.setRequestHeader('Content-Type', 'application/octet-stream');
    x.upload.onprogress = (e) => onProgress(e.loaded);
    x.onload = () => {
      let body = {}; try { body = JSON.parse(x.responseText); } catch { /* keep */ }
      if (x.status >= 200 && x.status < 300) resolve(body); else reject(new Error(body.message || `HTTP ${x.status}`));
    };
    x.onerror = () => reject(new Error('network error while sending the file'));
    x.send(file);
  });
}

// ------------------------------------------------------------------ sensors (browser events only: cheap)
window.addEventListener('devicemotion', (e) => {
  const now = performance.now();
  const r = e.rotationRate;
  if (r) {
    const degPerS = Math.hypot(r.alpha || 0, r.beta || 0, r.gamma || 0);
    S.gyroRad = (degPerS * Math.PI) / 180;
    if (S.lastMotionT !== null) {
      const turned = degPerS * Math.min(0.2, (now - S.lastMotionT) / 1000);
      S.rotSinceKey += turned;
      if (S.state === 'recording') S.rotSinceWalk += turned;
    }
  }
  const a = e.acceleration;
  if (a && S.state === 'recording' && S.steps.update(Math.hypot(a.x || 0, a.y || 0, a.z || 0), now)) S.rotSinceWalk = 0;
  S.lastMotionT = now;
});
window.addEventListener('deviceorientation', (e) => {
  if (e.alpha === null) return;
  S.alpha = e.alpha; S.beta = e.beta; S.gamma = e.gamma;
});

// ------------------------------------------------------------------ camera
async function openCamera() {
  S.stream = S.demo ? demoStream() : await navigator.mediaDevices.getUserMedia({ audio: false,
    video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1440 }, frameRate: { ideal: 30 } } });
  const v = $('video');
  v.srcObject = S.stream;
  await v.play();
  showScreen('cam');
  S.orient = v.videoWidth > v.videoHeight ? 'landscape' : 'portrait';
  const track = S.stream.getVideoTracks()[0];
  $('torch').hidden = !(track.getCapabilities && track.getCapabilities().torch);
  setMode(S.mode);
  hint('Tap the red button, then move the phone to start');
  setInterval(tick, 100);
}

function setMode(mode) {
  if (S.state !== 'idle') return toast('Change the mode before you start');
  S.mode = mode === 'video' && typeof MediaRecorder === 'undefined' ? 'frames' : mode;
  $('mode-btn').textContent = S.mode === 'video' ? 'Video' : 'Photos';
}

// ------------------------------------------------------------------ shutter: idle -> armed -> recording <-> paused
$('shutter').onclick = async () => {
  if (S.state === 'idle') {
    S.state = 'armed';
    try { S.wake = await navigator.wakeLock?.request('screen'); } catch { /* optional */ }
    hint('Move device to start');
  } else if (S.state === 'recording') {
    S.state = 'paused';
    if (S.recorder && S.recorder.state === 'recording') S.recorder.pause();
    hint('Paused – tap to continue');
  } else if (S.state === 'paused') {
    S.state = 'recording';
    if (S.recorder && S.recorder.state === 'paused') S.recorder.resume();
    S.recFrom = performance.now();
    hint(null);
  }
  updateShutter();
};

function beginRecording() {
  S.state = 'recording';
  S.recFrom = performance.now();
  hint(null);
  updateShutter();
  if (S.mode === 'video') {
    const types = [['video/mp4;codecs=avc1', 'mp4'], ['video/mp4', 'mp4'], ['video/webm;codecs=vp9', 'webm'], ['video/webm', 'webm']];
    const [mimeType, ext] = types.find(([t]) => MediaRecorder.isTypeSupported(t)) || [null, 'mp4'];
    S.ext = ext;
    S.recorder = new MediaRecorder(S.stream, { ...(mimeType ? { mimeType } : {}), videoBitsPerSecond: VIDEO_BPS });
    S.recorder.ondataavailable = (e) => {
      if (!e.data || !e.data.size) return;
      S.queue.push({ path: `/session/${S.pid}/video/${S.seq}?type=${S.ext}`, blob: e.data, type: e.data.type || `video/${S.ext}`, tries: 0 });
      S.seq += 1;
      pump();
    };
    S.recorder.start(CHUNK_MS);
  } else {
    S.selector = new KeyframeSelector();
    S.small = Object.assign(document.createElement('canvas'), { width: ANALYSE_W, height: ANALYSE_H });
    S.sctx = S.small.getContext('2d', { willReadFrequently: true });
  }
}

function updateShutter() {
  const b = $('shutter');
  $('rec-pill').classList.toggle('on', S.state === 'recording');
  b.className = `shutter ${S.state === 'recording' ? 'rec' : S.state === 'armed' ? 'armed' : ''}`;
  b.setAttribute('aria-label', { idle: 'Start recording', armed: 'Waiting for movement', recording: 'Pause', paused: 'Continue' }[S.state] || '');
  $('done-btn').disabled = !(S.state === 'recording' || S.state === 'paused') || recordedS() < MIN_VIDEO_S
    || (S.mode === 'frames' && S.frames.length < 10);
}

const recordedS = () => (S.recMs + (S.state === 'recording' && S.recFrom ? performance.now() - S.recFrom : 0)) / 1000;

// ------------------------------------------------------------------ 10 Hz tick: guidance, coverage, frames
function tick() {
  if (S.state === 'armed' && S.gyroRad > 0.15) beginRecording();         // "Move device to start"
  if (S.state === 'recording') {
    const now = performance.now();
    S.recMs += now - S.recFrom; S.recFrom = now;
  }
  const t = recordedS();
  $('elapsed').textContent = `${String(Math.floor(t / 60)).padStart(2, '0')}:${(t % 60).toFixed(2).padStart(5, '0')}`;
  updateShutter();
  if (S.state !== 'recording') return;
  const v = $('video');
  const orient = v.videoWidth > v.videoHeight ? 'landscape' : 'portrait';
  // issues, most important first; the warning button shows the current one
  let issue = null;
  if (orient !== S.orient) issue = `Turn the phone back to ${S.orient}`;
  else if (S.gyroRad > 0.8) issue = 'Slow down';
  else if (S.rotSinceWalk > 150) issue = "Walk a few steps – don't just turn";
  setIssue(issue);
  const R = S.alpha === null ? null : rotationMatrix(S.alpha, S.beta, S.gamma);
  if (R && S.gyroRad < 0.8 && orient === S.orient) {
    const [h, w] = S.orient === 'portrait' ? [FOV_LONG, FOV_SHORT] : [FOV_SHORT, FOV_LONG];
    S.cov.mark(R, w, h, Math.floor(S.steps.steps / STEPS_PER_POSITION));
  }
  const st = S.cov.stats();
  const pct = `${Math.round(100 * st.coveredShare)}%`;
  if ($('cov').textContent !== pct) {          // only on change: re-setting it every tick keeps restarting the animation
    $('cov').textContent = pct;
    $('cov-bar').style.width = pct;
  }
  if (S.sensors.length < 30000) S.sensors.push([Math.round(t * 1000), +S.gyroRad.toFixed(3), S.alpha === null ? null : Math.round(S.alpha),
    S.beta === null ? null : Math.round(S.beta), S.gamma === null ? null : Math.round(S.gamma), S.steps.steps]);
  if (S.mode === 'frames') analyseFrame(v);
}

function setIssue(text) {
  S.issue = text;
  $('warn').hidden = !text;
  if (text && (text !== setIssue.last || performance.now() - setIssue.t > 4000)) { toast(text); setIssue.t = performance.now(); }
  setIssue.last = text;
}
setIssue.t = 0;

let toastTimer = null;
function toast(text) {
  const t = $('toast');
  t.textContent = text; t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 1800);
}
function hint(text) { $('hint').hidden = !text; if (text) $('hint-text').textContent = text; }

// ------------------------------------------------------------------ photo-frames mode
function analyseFrame(v) {
  S.sctx.drawImage(v, 0, 0, ANALYSE_W, ANALYSE_H);
  const g = grey(S.sctx.getImageData(0, 0, ANALYSE_W, ANALYSE_H).data, ANALYSE_W, ANALYSE_H);
  const sample = { t: performance.now(), sharp: sharpness(g, ANALYSE_W, ANALYSE_H), ...exposure(g),
    thumb: thumb(g, ANALYSE_W, ANALYSE_H), gyroRad: S.gyroRad, rotSinceKeyDeg: S.rotSinceKey };
  const d = S.selector.update(sample);
  if (d.prompt && !S.issue) toast(d.prompt);
  if (!d.keep) return;
  const c = Object.assign(document.createElement('canvas'), { width: v.videoWidth, height: v.videoHeight });
  c.getContext('2d').drawImage(v, 0, 0);
  const name = `frame_${String(S.frames.length + 1).padStart(5, '0')}.jpg`;
  S.frames.push({ name, t: Math.round(sample.t), alpha: S.alpha, beta: S.beta, gamma: S.gamma, steps: S.steps.steps, reason: d.reason });
  S.rotSinceKey = 0;
  c.toBlob((blob) => { S.queue.push({ path: `/session/${S.pid}/frames/${name}`, blob, type: 'image/jpeg', tries: 0 }); pump(); }, 'image/jpeg', 0.92);
  if (navigator.vibrate) navigator.vibrate(12);
}

// ------------------------------------------------------------------ upload queue (in order, retried)
async function pump() {
  if (S.demo) { S.sent += S.queue.length; S.sentBytes += S.queue.reduce((a, j) => a + j.blob.size, 0); S.queue = []; return; }
  if (S.uploading) return;
  S.uploading = true;
  while (S.queue.length) {
    const job = S.queue[0];
    try {
      await api(job.path, { method: 'PUT', body: job.blob, headers: { 'Content-Type': job.type } });
      S.queue.shift(); S.sent += 1; S.sentBytes += job.blob.size;
    } catch (e) {
      job.tries += 1;
      if (e.status && e.status < 500 && e.status !== 408) { S.queue.shift(); S.failed += 1; toast(`Not accepted: ${e.message}`); }
      else await sleep(Math.min(8000, 500 * 2 ** job.tries));
    }
    $('sent').textContent = `↑ ${(S.sentBytes / 2 ** 20).toFixed(0)} MB${S.queue.length ? ` · ${S.queue.length}` : ''}`;
  }
  S.uploading = false;
}

// ------------------------------------------------------------------ buttons and sheets
$('tips-btn').onclick = () => { $('tips').hidden = false; };
$('mode-btn').onclick = () => { if (S.state === 'idle') $('mode-menu').hidden = false; else toast('Change the mode before you start'); };
for (const b of document.querySelectorAll('[data-mode]')) b.onclick = () => { setMode(b.dataset.mode); $('mode-menu').hidden = true; };
for (const b of document.querySelectorAll('[data-close]')) b.onclick = () => { b.closest('.sheet').hidden = true; };
$('warn').onclick = () => S.issue && toast(S.issue);
$('torch').onclick = async () => {
  S.torchOn = !S.torchOn;
  $('torch').classList.toggle('on', S.torchOn);
  try { await S.stream.getVideoTracks()[0].applyConstraints({ advanced: [{ torch: S.torchOn }] }); } catch { toast('Flashlight not available'); }
};
$('close-btn').onclick = () => {
  if (S.state !== 'idle' && !confirm('Stop and discard this scan? What was already sent stays on the computer.')) return;
  location.reload();
};

$('done-btn').onclick = async () => {
  $('done-btn').disabled = true;
  if (S.recorder && S.recorder.state !== 'inactive') {
    const stopped = new Promise((r) => { S.recorder.onstop = r; });
    S.recorder.stop();
    await stopped;
  }
  const seconds = Math.round(recordedS());
  S.state = 'done';
  S.wake?.release?.().catch(() => {});
  S.stream.getTracks().forEach((t) => t.stop());
  showScreen('s-status');
  const total = () => S.sent + S.queue.length;
  while (S.queue.length || S.uploading) {
    $('st-title').textContent = 'Sending…';
    $('st-msg').textContent = `${(S.sentBytes / 2 ** 20).toFixed(1)} MB sent`;
    $('st-bar').style.width = `${Math.round((100 * S.sent) / Math.max(1, total()))}%`;
    await sleep(400);
  }
  try {
    const st = S.cov.stats();
    await api(`/session/${S.pid}/done`, { method: 'POST', body: JSON.stringify({ frames: S.frames, sensors: S.sensors,
      device: { ua: navigator.userAgent, screen: [screen.width, screen.height], mode: S.mode, video_type: S.ext },
      stats: { mode: S.mode, seconds, pieces: S.sent, bytes: S.sentBytes, failed: S.failed, steps: S.steps.steps,
        cells_seen: st.seen, cells_covered: st.covered } }) });
  } catch (e) {
    $('st-title').textContent = 'Could not finish';
    $('st-msg').textContent = e.message;
    return;
  }
  try { sessionStorage.removeItem('namma-pid'); } catch { /* optional */ }
  $('st-title').textContent = 'Done';
  $('st-bar').style.width = '100%';
  $('st-msg').textContent = 'The computer is building the 3D model now. You can put the phone away and follow it in the Studio.';
};

// ------------------------------------------------------------------ demo mode (?demo=1): try the UI without a phone
// A synthetic camera feed (canvas) and no pairing / uploads; motion and orientation still come from the device (or
// from dispatched DeviceOrientationEvent / DeviceMotionEvent in tests). window.__nammaCapture exposes the state.
function demoStream() {
  const c = Object.assign(document.createElement('canvas'), { width: 480, height: 640 });
  const g = c.getContext('2d');
  const draw = (t) => {
    const k = (S.alpha || 0) * 6;
    const grad = g.createLinearGradient(0, 0, 0, 640);
    grad.addColorStop(0, '#d9d4cb'); grad.addColorStop(0.62, '#a49d91'); grad.addColorStop(0.63, '#6d675e'); grad.addColorStop(1, '#4a453e');
    g.fillStyle = grad; g.fillRect(0, 0, 480, 640);
    g.fillStyle = '#7a5c3e'; g.fillRect(((120 - k) % 600) - 60, 330, 150, 120);       // a table moving with the view
    g.fillStyle = '#3c5a78'; g.fillRect(((380 - k) % 600) - 60, 140, 90, 160);       // a window
    requestAnimationFrame(draw);
  };
  draw(0);
  return c.captureStream(30);
}
if (new URLSearchParams(location.search).has('demo')) {
  S.demo = true;
  window.__nammaCapture = S;
  openCamera();
}
