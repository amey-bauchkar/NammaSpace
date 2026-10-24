// Namma Space Studio front end (talks to namma.studio.server on 127.0.0.1).
const $ = (sel, root = document) => root.querySelector(sel);
const app = $('#app');
const H = { 'X-Namma-Studio': '1' };
let pollTimer = null;

async function api(path, opts = {}) {
  const r = await fetch(`/api${path}`, { ...opts, headers: { ...H, ...(opts.body ? { 'Content-Type': 'application/json' } : {}), ...(opts.headers || {}) } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error(body.message || `HTTP ${r.status}`);
    e.code = body.error;
    throw e;
  }
  return body;
}

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined) n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
}

function render(tplId) {
  app.innerHTML = '';
  app.append($(`#${tplId}`).content.cloneNode(true));
}

const f = (name) => $(`[data-f="${name}"]`, app);
const fmtTime = (s) => (s < 90 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`);
const fmtMB = (b) => `${(b / 2 ** 20).toFixed(b < 10 * 2 ** 20 ? 1 : 0)} MB`;
const STATUS = { succeeded: ['Finished', 'ok'], running: ['Running', 'info'], queued: ['Starting', 'info'], failed: ['Failed', 'bad'],
  cancelled: ['Cancelled', 'warn'], interrupted: ['Interrupted', 'warn'] };

function badge(status) {
  const [label, cls] = STATUS[status] || [status || '–', ''];
  return el('span', { class: `badge ${cls}` }, label);
}

function messageList(items) {
  const ul = el('ul', { class: 'warn-list' });
  for (const m of items) {
    const li = el('li', { class: m.severity }, m.message);
    if (m.images && m.images.length) li.append(el('div', { class: 'small muted' }, `Examples: ${m.images.slice(0, 6).join(', ')}${m.images.length > 6 ? ' …' : ''}`));
    if (m.examples && m.examples.length) li.append(el('div', { class: 'small muted' }, m.examples.slice(0, 3).join(' · ')));
    ul.append(li);
  }
  return ul;
}

async function health() {
  try {
    const h = await api('/health');
    const b = $('#health');
    const ok = h.tools.colmap && h.tools.openmvs;
    b.className = `badge ${ok ? 'ok' : 'bad'}`;
    b.textContent = ok ? `Ready · ${h.ram_available_gb} GB RAM free` : 'Reconstruction tools missing';
    const hw = h.hardware || {};
    const gpu = hw.gpu ? `GPU ${hw.gpu}: ${hw.gpu_steps && hw.gpu_steps.length ? `used for ${hw.gpu_steps.join(', ')}` : 'not used by the installed tools'}.`
      : `No GPU in use (${hw.gpu_reason || 'none found'}).`;
    b.title = ok ? `COLMAP and OpenMVS found. Disk free ${h.disk_free_gb} GB. ${gpu}${hw.projects_onedrive_synced ? ' Projects folder is synchronised by OneDrive: use a local folder.' : ''}` : h.tools.error;
    return h;
  } catch {
    $('#health').textContent = 'Server not reachable';
    $('#health').className = 'badge bad';
    return null;
  }
}

// ------------------------------------------------------------------ home
async function home() {
  $('#crumb').textContent = '';
  render('tpl-home');
  $('#new-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const p = await api('/projects', { method: 'POST', body: JSON.stringify({ title: $('#new-title').value, source: $('#new-source').value }) });
    location.hash = `#/project/${p.project_id}`;
  });
  showPhone();
  wirePolycamDrop();
  const list = $('#project-list');
  const { projects } = await api('/projects');
  if (!projects.length) list.append(el('li', { class: 'empty' }, 'No spaces yet – create one above.'));
  for (const p of projects) {
    const last = p.runs[p.runs.length - 1];
    list.append(el('li', {},
      el('div', {}, el('strong', {}, p.title), el('div', { class: 'small muted' }, `${p.uploads} files · created ${p.created_at.slice(0, 16).replace('T', ' ')}`)),
      el('div', { class: 'row' }, last ? badge(last.status) : el('span', { class: 'badge' }, 'not processed'),
        el('a', { class: 'btn secondary', href: `#/project/${p.project_id}` }, 'Open'),
        el('button', { type: 'button', class: 'danger', 'aria-label': `Delete ${p.title}`, onclick: async () => {
          if (!confirm(`Delete "${p.title}"? It moves to the bin; you can restore it from there.`)) return;
          try { await api(`/projects/${p.project_id}`, { method: 'DELETE' }); home(); } catch (e) { alert(e.message); }
        } }, 'Delete'))));
  }
  showBin();
}

async function showBin() {
  const { items } = await api('/bin');
  const card = $('#bin-card');
  if (!card) return;
  card.hidden = !items.length;
  $('#bin-summary').textContent = `Bin (${items.length})`;
  const ul = $('#bin-list');
  ul.innerHTML = '';
  for (const it of items) {
    const when = `${it.deleted_at.slice(0, 4)}-${it.deleted_at.slice(4, 6)}-${it.deleted_at.slice(6, 8)} ${it.deleted_at.slice(8, 10)}:${it.deleted_at.slice(10, 12)}`;
    ul.append(el('li', {},
      el('div', {}, el('strong', {}, it.title), el('div', { class: 'small muted' }, `deleted ${when} · ${fmtMB(it.bytes)}`)),
      el('div', { class: 'row' },
        el('button', { type: 'button', class: 'secondary', onclick: async () => {
          try { await api(`/bin/${encodeURIComponent(it.name)}/restore`, { method: 'POST', body: '{}' }); home(); } catch (e) { alert(e.message); }
        } }, 'Restore'),
        el('button', { type: 'button', class: 'danger', onclick: async () => {
          if (!confirm(`Permanently delete "${it.title}" (${fmtMB(it.bytes)})? Photos, video and models of this space are erased. This cannot be undone.`)) return;
          try { await api(`/bin/${encodeURIComponent(it.name)}`, { method: 'DELETE' }); showBin(); } catch (e) { alert(e.message); }
        } }, 'Delete permanently'))));
  }
}

// ------------------------------------------------------------------ Polycam import (drag and drop)
function wirePolycamDrop() {
  const dz = $('#polycam-drop');
  if (!dz) return;
  const input = $('#polycam-input');
  $('#polycam-pick').onclick = (e) => { e.preventDefault(); input.click(); };
  dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
  dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('drag'); importPolycam([...e.dataTransfer.files]); });
  input.onchange = () => { importPolycam([...input.files]); input.value = ''; };
}

async function importPolycam(files) {
  const msg = $('#polycam-msg');
  const bar = $('#polycam-progress');
  if (!files.length) return;
  msg.className = 'small';
  try {
    const { import_id: iid } = await api('/polycam', { method: 'POST', body: '{}' });
    bar.hidden = false;
    const total = files.reduce((a, f) => a + f.size, 0);
    let done = 0;
    for (const f of files) {
      msg.textContent = `Uploading ${f.name}…`;
      await new Promise((resolve, reject) => {
        const x = new XMLHttpRequest();
        x.open('PUT', `/api/polycam/${iid}/files/${encodeURIComponent(f.name)}`);
        x.setRequestHeader('X-Namma-Studio', '1');
        x.setRequestHeader('Content-Type', 'application/octet-stream');
        x.upload.onprogress = (e) => { bar.firstElementChild.style.width = `${Math.round((100 * (done + e.loaded)) / total)}%`; };
        x.onload = () => {
          let b = {}; try { b = JSON.parse(x.responseText); } catch { /* keep */ }
          if (x.status >= 200 && x.status < 300) resolve(b); else reject(new Error(b.message || `HTTP ${x.status}`));
        };
        x.onerror = () => reject(new Error('network error'));
        x.send(f);
      });
      done += f.size;
    }
    msg.textContent = 'Importing: finding the floor, start view and walkable area…';
    const r = await api(`/polycam/${iid}/import`, { method: 'POST', body: '{}' });
    location.hash = `#/project/${r.project_id}/run/${r.run_id}`;
  } catch (e) {
    msg.className = 'small callout bad';
    msg.textContent = `Import failed: ${e.message}`;
  } finally {
    bar.hidden = true;
  }
}

// ------------------------------------------------------------------ phone capture
async function showPhone() {
  const box = $('#phone-body');
  if (!box) return;
  let ph;
  try { ph = await api('/phone'); } catch { box.textContent = 'Phone capture status unavailable.'; return; }
  box.innerHTML = '';
  if (!ph.enabled) {
    box.append(el('p', { style: 'margin:0' }, 'Point your phone at the room and frames are captured automatically. ', ph.hint));
    return;
  }
  box.className = 'small';
  box.append(
    el('div', { style: 'display:flex;gap:20px;align-items:center;flex-wrap:wrap' },
      el('img', { src: `/api/phone/qr.svg?c=${ph.code}`, alt: 'QR code: scan with the phone camera to start scanning',
        width: 180, height: 180, style: 'border-radius:8px;border:1px solid var(--line)' }),
      el('div', {},
        el('p', { style: 'margin:0 0 6px;font-size:15px' }, el('strong', {}, 'Scan this with the phone camera'),
          ' (same Wi-Fi or hotspot) and tap the link: the scan page opens ready to go.'),
        el('p', { class: 'muted', style: 'margin:0' }, 'Or open ', el('span', { class: 'mono' }, ph.urls[0] || '(no network address found)'),
          ' and type the code ', el('strong', { class: 'mono' }, ph.code), '.'))),
    el('p', { class: 'muted', style: 'margin:0' }, `Valid for ${Math.max(1, Math.round(ph.expires_in_s / 60))} min, single use. `,
      'The phone shows a one-time "not private" warning for this computer\'s own certificate: choose to continue. ',
      'Tap Done on the phone and the check and Preview model start here automatically.'),
    ph.urls.length > 1 ? el('p', { class: 'muted', style: 'margin:4px 0 0' },
      `If that does not open, this computer's other addresses: ${ph.urls.slice(1).join(', ')}`) : '',
    el('button', { type: 'button', class: 'secondary', style: 'margin-top:8px', onclick: async () => {
      await api('/phone/code', { method: 'POST', body: '{}' }); showPhone(); } }, 'New code'));
  clearTimeout(showPhone.timer);
  showPhone.timer = setTimeout(() => { if (location.hash === '' || location.hash === '#/') showPhone(); }, 15000);
}

// ------------------------------------------------------------------ project
function estimate(n, profile) {
  // From Phase 13 runs on the team laptop (Intel Core 5 120U, CPU only). Preview: 3.8 s per frame (ARKitScenes
  // video) and 4.7 s (TUM 640 px) up to 39 s per 24 MP photo (ETH3D delivery_area). Standard: 79-99 s per 24 MP
  // photo (ETH3D office / kicker); a 44-photo hall needed 11.2 GB to build its surface and exceeded the time limit
  // (D13-14). Larger photos and more images take longer. A rough guide, not a promise.
  // High was added for GPU laptops after these measurements: no timing of its own yet, shown as Standard's range.
  if (!n) return '';
  const [lo, hi] = profile === 'preview' ? [4, 40] : [80, 200];
  const text = `roughly ${Math.max(2, Math.round((n * lo) / 60))}–${Math.round((n * hi) / 60) + 2} min for ${n} images on the team laptop`;
  if (profile === 'high') return `CPU-only reference: ${text}; GPU depth maps make it faster (not yet measured)`;
  return profile === 'standard' && n > 40
    ? `${text}. With more than 40 photos Standard can need more memory than a 16 GB laptop has: run Preview first.` : text;
}

async function project(pid) {
  render('tpl-project');
  let p = await api(`/projects/${pid}`);
  $('#crumb').textContent = p.title;
  f('title').textContent = p.title;
  const steps = f('steps');
  const refreshSteps = (state) => {
    steps.innerHTML = '';
    ['1 Capture', '2 Check', '3 Reconstruct', '4 Explore & label', '5 Publish'].forEach((s, i) => {
      steps.append(el('li', { class: i < state ? 'done' : i === state ? 'active' : '' }, s));
    });
  };
  const hasPackage = p.runs.some((r) => r.has_package);
  refreshSteps(hasPackage ? 3 : p.capture_status === 'ready' ? 2 : p.uploads ? 1 : 0);

  // uploads
  const input = $('#file-input');
  const dz = $('#dropzone');
  $('#pick').onclick = () => input.click();
  dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
  dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('drag'); upload(pid, [...e.dataTransfer.files]); });
  input.onchange = () => upload(pid, [...input.files]);
  await listFiles(pid);

  // check
  $('#check-btn').onclick = async () => {
    $('#check-btn').disabled = true;
    try { await api(`/projects/${pid}/check`, { method: 'POST', body: '{}' }); } catch (e) { $('#check-step').textContent = e.message; $('#check-btn').disabled = false; return; }
    pollCheck(pid);
  };
  const chk = await api(`/projects/${pid}/check`);
  if (chk.status === 'running' || chk.status === 'starting') pollCheck(pid); else showCheck(pid, chk);

  // runs
  const nImages = chk.report ? chk.report.stats.inputs_accepted : p.uploads;
  f('est-preview').textContent = estimate(nImages, 'preview');
  f('est-standard').textContent = estimate(nImages, 'standard');
  f('est-high').textContent = estimate(nImages, 'high');
  if (chk.report) showAdvice(pid);
  $('#run-btn').onclick = async () => {
    const profile = $('input[name=profile]:checked').value;
    $('#run-btn').disabled = true;
    try {
      const r = await api(`/projects/${pid}/runs`, { method: 'POST', body: JSON.stringify({ profile }) });
      location.hash = `#/project/${pid}/run/${r.run_id}`;
    } catch (e) {
      $('#run-msg').textContent = e.message;
      $('#run-btn').disabled = false;
    }
  };
  const rl = $('#run-list');
  for (const r of [...p.runs].reverse()) {
    rl.append(el('li', {}, el('button', { type: 'button', onclick: () => { location.hash = `#/project/${pid}/run/${r.run_id}`; } },
      el('span', {}, `${r.profile} · ${r.created_at.slice(0, 16).replace('T', ' ')}`), badge(r.status))));
  }
  $('#trash-btn').onclick = async () => {
    if (!confirm(`Move "${p.title}" to the bin (projects/_trash)?`)) return;
    await api(`/projects/${pid}`, { method: 'DELETE' });
    location.hash = '#/';
  };
}

async function listFiles(pid) {
  const { files } = await api(`/projects/${pid}/uploads`);
  const ul = $('#file-list');
  if (!ul) return;
  ul.innerHTML = '';
  f('files-summary').textContent = `Uploaded files (${files.length}, ${fmtMB(files.reduce((a, x) => a + x.bytes, 0))})`;
  for (const x of files) {
    ul.append(el('li', { class: 'row' }, el('span', { style: 'flex:1' }, `${x.name} · ${fmtMB(x.bytes)}`),
      el('button', { type: 'button', class: 'ghost', 'aria-label': `Remove ${x.name}`, onclick: async () => {
        await api(`/projects/${pid}/uploads/${encodeURIComponent(x.name)}`, { method: 'DELETE' });
        listFiles(pid);
        $('#check-result').innerHTML = '';
      } }, '✕')));
  }
  $('#files-box').open = files.length > 0 && files.length <= 20;
}

function putFile(pid, file, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('PUT', `/api/projects/${pid}/uploads/${encodeURIComponent(file.name)}`);
    x.setRequestHeader('X-Namma-Studio', '1');
    x.setRequestHeader('Content-Type', 'application/octet-stream');
    x.upload.onprogress = (e) => onProgress(e.loaded);
    x.onload = () => {
      if (x.status >= 200 && x.status < 300) resolve(JSON.parse(x.responseText));
      else { let m = `HTTP ${x.status}`; try { m = JSON.parse(x.responseText).message; } catch { /* keep */ } reject(new Error(m)); }
    };
    x.onerror = () => reject(new Error('network error'));
    x.send(file);
  });
}

async function upload(pid, files) {
  if (!files.length) return;
  const total = files.reduce((a, x) => a + x.size, 0);
  const done = new Array(files.length).fill(0);
  const bar = $('#upload-progress');
  bar.hidden = false;
  const msg = $('#upload-msg');
  const errors = [];
  let next = 0;
  const update = () => {
    const sum = done.reduce((a, b) => a + b, 0);
    bar.firstElementChild.style.width = `${Math.round((100 * sum) / total)}%`;
    msg.textContent = `Uploading ${files.length} files… ${fmtMB(sum)} of ${fmtMB(total)}`;
  };
  const worker = async () => {
    while (next < files.length) {
      const i = next++;
      try { await putFile(pid, files[i], (b) => { done[i] = b; update(); }); done[i] = files[i].size; update(); } catch (e) { errors.push(`${files[i].name}: ${e.message}`); }
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  bar.hidden = true;
  msg.textContent = errors.length ? `${files.length - errors.length} uploaded; problems: ${errors.join('; ')}` : `${files.length} files uploaded. Next: check the capture.`;
  $('#check-result').innerHTML = '';
  $('#run-btn').disabled = true;
  listFiles(pid);
}

function pollCheck(pid) {
  clearInterval(pollTimer);
  const bar = $('#check-progress');
  bar.hidden = false;
  pollTimer = setInterval(async () => {
    if (!$('#check-progress')) { clearInterval(pollTimer); return; }
    const s = await api(`/projects/${pid}/check`).catch(() => null);
    if (!s) return;
    bar.firstElementChild.style.width = `${Math.round(100 * (s.fraction || 0))}%`;
    $('#check-step').textContent = s.label || '';
    if (s.status === 'done' || s.status === 'error') {
      clearInterval(pollTimer);
      bar.hidden = true;
      $('#check-btn').disabled = false;
      showCheck(pid, s);
    }
  }, 700);
}

async function showAdvice(pid) {
  // profile advice for this capture on this machine (namma.studio.advisor); advice only, the user still chooses
  const box = f('advice');
  if (!box) return;
  try {
    const r = await api(`/projects/${pid}/recommendation`);
    box.hidden = false;
    box.innerHTML = '';
    box.append(el('strong', {}, `Suggested: ${r.profile[0].toUpperCase()}${r.profile.slice(1)}. `), r.reasons.join(' '));
    for (const w of r.warnings) box.append(el('div', { class: 'small muted' }, `⚠ ${w}`));
    const radio = $(`input[name=profile][value=${r.profile}]`);
    if (radio) radio.checked = true;
  } catch { box.hidden = true; }
}

function showCheck(pid, s) {
  const box = $('#check-result');
  if (!box) return;
  box.innerHTML = '';
  $('#check-step').textContent = '';
  if (s.status === 'error') { box.append(el('div', { class: 'callout bad' }, `The check could not run: ${s.error}`)); return; }
  if (!s.report) return;
  const r = s.report;
  const st = r.stats;
  const ready = r.status === 'ready';
  box.append(el('div', { class: `callout ${ready ? (r.warnings.length ? 'warn' : 'ok') : 'bad'}` },
    ready ? `Ready to reconstruct: ${st.inputs_accepted} ${st.mode === 'video' ? 'frames from the video' : 'photos'} accepted${r.warnings.length ? `, ${r.warnings.length} things to be aware of` : ''}.`
      : 'This capture cannot be processed yet – fix the problems below.'));
  box.append(messageList([...r.errors, ...r.warnings]));
  if (st.overlap) {
    box.append(el('p', { class: 'small muted' }, `Overlap check: ${st.overlap.verified_pairs} overlapping pairs found; largest connected group ${st.overlap.largest_group} of ${st.inputs_accepted}. `
      + 'This is a quick prediction; the reconstruction itself is the real test.'));
  }
  if (s.inputs && s.inputs.length) {
    const grid = el('div', { class: 'thumbs' });
    for (const x of s.inputs.slice(0, 240)) {
      grid.append(el('figure', { class: x.flags.length ? 'flagged' : '' }, el('img', { src: x.thumb, alt: x.name, loading: 'lazy' }),
        el('figcaption', {}, x.flags.length ? x.flags.join(', ') : x.name)));
    }
    box.append(grid);
  }
  $('#run-btn').disabled = !ready;
}

// ------------------------------------------------------------------ run
async function runPage(pid, rid) {
  render('tpl-run');
  const p = await api(`/projects/${pid}`);
  $('#crumb').textContent = p.title;
  f('back').href = `#/project/${pid}`;
  f('title').textContent = `Reconstruction of ${p.title}`;
  f('cancel').onclick = async () => { await api(`/projects/${pid}/runs/${rid}/cancel`, { method: 'POST', body: '{}' }); f('detail').textContent = 'Cancelling…'; };
  // after Resume the status stays "cancelled" / "failed" until the new worker process starts (a few seconds):
  // keep polling until the attempt counter rises, otherwise the page would stop updating (pre-capture QA, QA-04)
  let resume = null;
  f('resume').onclick = async () => {
    try {
      const st0 = await api(`/projects/${pid}/runs/${rid}`);
      await api(`/projects/${pid}/runs/${rid}/resume`, { method: 'POST', body: '{}' });
      resume = { attempts: st0.attempts || 0, until: Date.now() + 60000 };
      f('resume').hidden = true;
      f('error').innerHTML = '';
      f('state').textContent = 'Resuming…';
      clearInterval(pollTimer);
      pollTimer = setInterval(tick, 1500);
      tick();
    } catch (e) { f('detail').textContent = e.message; }
  };
  let shownResult = false;
  const tick = async () => {
    if (!f('stages')) { clearInterval(pollTimer); return; }
    let st;
    try { st = await api(`/projects/${pid}/runs/${rid}`); } catch (e) { f('detail').textContent = e.message; return; }
    f('sub').textContent = `Profile ${st.profile || '–'} · run ${rid}`;
    const [label] = STATUS[st.status] || [st.status];
    f('state').textContent = st.status === 'running' && st.current ? st.current.label : label;
    f('bar').style.width = `${Math.round(100 * (st.progress || 0))}%`;
    f('detail').textContent = st.current && st.current.detail ? `${st.current.detail}${st.current.fraction !== null && st.current.fraction !== undefined ? ` – ${Math.round(100 * st.current.fraction)}%` : ''}` : '';
    if (st.started_at) {
      const end = st.ended_at ? Date.parse(st.ended_at) : Date.now();
      f('elapsed').textContent = fmtTime((end - Date.parse(st.started_at)) / 1000);
    }
    const ul = f('stages');
    ul.innerHTML = '';
    for (const s of st.stages || []) {
      ul.append(el('li', {}, el('span', { class: `dot ${s.status}` }), el('span', {}, s.label), el('span', { class: 'small muted mono' }, s.seconds !== undefined ? fmtTime(s.seconds) : s.status === 'running' ? '…' : '')));
    }
    if (resume && ((st.attempts || 0) > resume.attempts || Date.now() > resume.until)) resume = null;
    const errBox = f('error');
    errBox.innerHTML = '';
    if (st.error && !resume) errBox.append(el('div', { class: 'callout bad' }, el('strong', {}, st.error.message), el('div', { class: 'small' }, st.error.hint || '')));
    const active = ['running', 'queued'].includes(st.status) || resume !== null;
    if (resume && !['running', 'queued'].includes(st.status)) f('state').textContent = 'Resuming…';
    f('cancel').hidden = !active;
    f('resume').hidden = active || !['failed', 'cancelled', 'interrupted'].includes(st.status);
    // the technical log is fetched only while its section is open (it was a second request every 1.5 s)
    if (f('log').closest('details').open) {
      const log = await api(`/projects/${pid}/runs/${rid}/log`).catch(() => null);
      if (log) f('log').textContent = `${log.file || ''}\n${log.lines.join('\n')}`;
    }
    if (st.status === 'succeeded' && !shownResult) { shownResult = true; showResult(pid, rid); }
    if (!active) clearInterval(pollTimer);
  };
  f('log').closest('details').ontoggle = (e) => { if (e.target.open) tick(); };
  clearInterval(pollTimer);
  await tick();
  pollTimer = setInterval(tick, 1500);
}

async function showResult(pid, rid) {
  const rep = await api(`/projects/${pid}/runs/${rid}/report`);
  const box = f('result');
  box.hidden = false;
  const q = rep.quality;
  const v = { good: ['Good', 'ok'], usable_with_warnings: ['Usable, with warnings', 'warn'], poor: ['Poor', 'bad'], failed: ['Failed', 'bad'] }[q.verdict];
  f('verdict').append(el('span', { class: `badge ${v[1]}` }, `Quality: ${v[0]}`));
  const facts = f('facts');
  const add = (k, val) => facts.append(el('dt', {}, k), el('dd', {}, val));
  const fa = q.facts;
  const imported = fa.source === 'imported_mesh';
  if (imported) {
    add('Source', `${fa.app} scan (${fa.device}), imported unchanged`);
    add('Size', `${fa.extent_m_p1_p99.map((v) => v.toFixed(2)).join(' × ')} m (width × height × depth)`);
  } else {
    add('Photos placed', `${fa.images_registered} of ${fa.images_input} (${Math.round(100 * q.registration_rate)}%)`);
  }
  add('3D surface', `${(fa.mesh_faces / 1e6).toFixed(2)} million triangles`);
  if (!imported) add('Dense points', fa.dense_points ? fa.dense_points.toLocaleString() : 'none (coarse fallback)');
  add('Floor reconstructed', fa.floor_detected ? `${Math.round(100 * fa.floor_coverage)}% of the floor area` : 'no floor plane found');
  add('Real-world scale', fa.scale_status === 'unknown' ? 'unknown (set it in the viewer with a known length)'
    : fa.scale_status === 'device_tracking' ? 'metres from the phone\'s tracking (check one length in the viewer)' : fa.scale_status);
  const total = (rep.status.stages || []).reduce((a, s) => a + (s.seconds || 0), 0);
  add('Processing time', fmtTime(total));
  if (rep.commands) add('Peak memory', `${(Math.max(...rep.commands.map((c) => c.peak_rss_mb)) / 1024).toFixed(1)} GB`);
  add('Navigation', rep.navigation ? (rep.navigation.status === 'unverified' ? 'automatic floor map (unverified)' : 'not available') : '–');
  f('warnings').replaceWith(messageList(q.warnings.filter((w) => w.severity !== 'info').concat(q.warnings.filter((w) => w.severity === 'info'))));
  const pkg = `/files/${pid}/runs/${rid}/90_package/scene_package.json`;
  f('open').href = `viewer.html?package=${encodeURIComponent(pkg)}&project=${encodeURIComponent(pid)}&run=${encodeURIComponent(rid)}`;
  f('report').href = `/api/projects/${pid}/runs/${rid}/report`;
  f('publish').onclick = async () => {
    const out = f('publish-result');
    out.innerHTML = '';
    out.append(el('p', { class: 'small muted' }, 'Building a static website and a ZIP…'));
    try {
      const r = await api(`/projects/${pid}/runs/${rid}/publish`, { method: 'POST', body: '{}' });
      out.innerHTML = '';
      out.append(el('div', { class: 'callout ok' }, `Published: ${r.files} files, ${fmtMB(r.bytes)}. `,
        el('a', { href: r.site, target: '_blank', rel: 'noopener' }, 'Open the static site'), ' · ',
        el('a', { href: r.zip, download: '' }, `Download ZIP (${fmtMB(r.zip_bytes)})`),
        el('div', { class: 'small' }, 'Upload the folder (or ZIP contents) to any static web host, e.g. GitHub Pages or Netlify, to share a link. Respect the licence of the source photos.')));
    } catch (e) {
      out.innerHTML = '';
      out.append(el('div', { class: 'callout bad' }, `Publishing failed: ${e.message}`));
    }
  };
}

// ------------------------------------------------------------------ router
async function route() {
  clearInterval(pollTimer);
  const h = location.hash.replace(/^#\/?/, '');
  const parts = h.split('/').filter(Boolean).map(decodeURIComponent);
  try {
    if (parts[0] === 'project' && parts[2] === 'run') await runPage(parts[1], parts[3]);
    else if (parts[0] === 'project') await project(parts[1]);
    else await home();
  } catch (e) {
    app.innerHTML = '';
    app.append(el('div', { class: 'callout bad' }, `Could not load this page: ${e.message}`), el('p', {}, el('a', { href: '#/' }, 'Back to all spaces')));
  }
}

window.addEventListener('hashchange', route);
health();
setInterval(health, 15000);
route();
