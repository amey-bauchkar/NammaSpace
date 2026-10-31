// Mini-map: a top-down picture of the room (the scanned mesh seen from above, cut at chest height so the floor and
// furniture show and the ceiling does not) with the labels, the current route and measurements, and a
// you-are-here arrow. Clicking the map moves there (walking spots only, in walk mode). The plan is turned so the
// room's walls run straight across and down (dominant wall direction of the mesh), which also makes it fill the map.
import * as THREE from 'three';

const SIZE_PX = 512;          // longest side of the rendered plan
const CUT = 0.85;             // cut height, in eye heights (1.36 m at a 1.6 m eye): tables and beds show, ceilings not

/** World-space vertices of the mesh and the dominant wall direction (area-weighted, walls taken modulo 90 deg). */
function wallFrame(root) {
  const pts = [];
  let sx = 0, sy = 0, ymin = Infinity;
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), n = new THREE.Vector3();
  root.updateMatrixWorld(true);
  root.traverse((o) => {
    if (!o.isMesh) return;
    const pos = o.geometry.attributes.position, idx = o.geometry.index;
    const v = (i) => new THREE.Vector3().fromBufferAttribute(pos, i).applyMatrix4(o.matrixWorld);
    for (let i = 0; i < pos.count; i += 3) { const p = v(i); pts.push(p.x, p.z); ymin = Math.min(ymin, p.y); }
    const tris = idx ? idx.count / 3 : pos.count / 3;
    for (let t = 0; t < tris; t++) {
      const i0 = idx ? idx.getX(3 * t) : 3 * t, i1 = idx ? idx.getX(3 * t + 1) : 3 * t + 1, i2 = idx ? idx.getX(3 * t + 2) : 3 * t + 2;
      a.copy(v(i0)); b.copy(v(i1)); c.copy(v(i2));
      n.subVectors(b, a).cross(c.sub(a));
      const area = n.length() / 2;
      if (area <= 0 || Math.abs(n.y) / (2 * area) > 0.2) continue;   // walls only (nearly vertical faces)
      const ang = 4 * Math.atan2(n.z, n.x);
      sx += area * Math.cos(ang); sy += area * Math.sin(ang);
    }
  });
  return { pts, theta: Math.atan2(sy, sx) / 4, ymin };
}

export function createMinimap({ app, walker, eye, canvas, onGo }) {
  const { pts, theta, ymin } = wallFrame(app.appearance);
  const cs = Math.cos(theta), sn = Math.sin(theta);
  const toUV = (x, z) => [x * cs + z * sn, -x * sn + z * cs];     // plan axes along the walls
  let u0 = Infinity, v0 = Infinity, u1 = -Infinity, v1 = -Infinity;
  for (let i = 0; i < pts.length; i += 2) {
    const [u, v] = toUV(pts[i], pts[i + 1]);
    u0 = Math.min(u0, u); u1 = Math.max(u1, u); v0 = Math.min(v0, v); v1 = Math.max(v1, v);
  }
  const pad = 0.1 * eye;
  u0 -= pad; v0 -= pad;
  const w = u1 - u0 + pad, d = v1 - v0 + pad;
  const ppm = SIZE_PX / Math.max(w, d);                        // plan pixels per model unit
  const W = Math.round(w * ppm), H = Math.round(d * ppm);

  // ---- the plan image (rendered once): orthographic camera at the cut height looking straight down; everything
  // above the camera is behind it and not drawn
  const base = document.createElement('canvas');
  base.width = W; base.height = H;
  {
    const cut = CUT * eye;
    const uc = u0 + w / 2, vc = v0 + d / 2;
    const cx = uc * cs - vc * sn, cz = uc * sn + vc * cs;       // plan centre in the world
    const cam = new THREE.OrthographicCamera(-w / 2, w / 2, d / 2, -d / 2, 0.001, cut - ymin + 1);
    cam.position.set(cx, cut, cz);
    cam.up.set(sn, 0, -cs);                                     // screen up = -v; screen right = +u
    cam.lookAt(cx, cut - 1, cz);
    cam.updateMatrixWorld();
    const rt = new THREE.WebGLRenderTarget(W, H, { colorSpace: THREE.SRGBColorSpace });
    const meshes = [];
    app.appearance.traverse((o) => { if (o.isMesh) { meshes.push([o, o.material.visible]); o.material.visible = true; } });
    const r = app.renderer;
    r.setRenderTarget(rt);
    r.setClearColor(0x0f1a2a, 1);
    r.clear();
    r.render(app.appearance, cam);
    r.setRenderTarget(null);
    for (const [o, v] of meshes) o.material.visible = v;
    const px = new Uint8Array(W * H * 4);
    r.readRenderTargetPixels(rt, 0, 0, W, H, px);
    rt.dispose();
    const img = new ImageData(W, H);
    for (let y = 0; y < H; y++) img.data.set(px.subarray((H - 1 - y) * W * 4, (H - y) * W * 4), y * W * 4);   // GL rows are bottom-up
    base.getContext('2d').putImageData(img, 0, 0);
  }

  const ctx2 = canvas.getContext('2d');
  const toPx = (x, z) => { const [u, v] = toUV(x, z); return [(u - u0) * ppm, (v - v0) * ppm]; };
  const layers = { pois: [], route: null, measures: [] };
  let selected = null;

  function draw() {
    const cw = canvas.width, ch = canvas.height;
    const k = Math.min(cw / W, ch / H), ox = (cw - W * k) / 2, oy = (ch - H * k) / 2;
    const P = (x, z) => { const [u, v] = toPx(x, z); return [ox + u * k, oy + v * k]; };
    ctx2.fillStyle = '#0f1a2a';
    ctx2.fillRect(0, 0, cw, ch);
    ctx2.drawImage(base, ox, oy, W * k, H * k);
    ctx2.lineCap = ctx2.lineJoin = 'round';
    if (layers.route && layers.route.length > 1) {
      ctx2.strokeStyle = '#f59e0b'; ctx2.lineWidth = 3; ctx2.setLineDash([7, 5]);
      ctx2.beginPath();
      layers.route.forEach(([x, z], i) => { const [u, v] = P(x, z); if (i) ctx2.lineTo(u, v); else ctx2.moveTo(u, v); });
      ctx2.stroke(); ctx2.setLineDash([]);
    }
    ctx2.strokeStyle = '#facc15'; ctx2.lineWidth = 2;
    for (const pts of layers.measures) {
      if (pts.length < 2) continue;
      ctx2.beginPath();
      pts.forEach((p, i) => { const [u, v] = P(p.x, p.z); if (i) ctx2.lineTo(u, v); else ctx2.moveTo(u, v); });
      ctx2.stroke();
    }
    ctx2.font = '600 11px system-ui, sans-serif';
    for (const p of layers.pois) {
      const [u, v] = P(p.position[0], p.position[2]);
      const sel = selected && selected === p.poi_id;
      ctx2.fillStyle = sel ? '#22c55e' : '#38bdf8';
      ctx2.beginPath(); ctx2.arc(u, v, sel ? 6 : 4.5, 0, 2 * Math.PI); ctx2.fill();
      ctx2.strokeStyle = 'rgba(0,0,0,.65)'; ctx2.lineWidth = 3; ctx2.strokeText(p.name, u + 7, v + 4);
      ctx2.fillStyle = '#fff'; ctx2.fillText(p.name, u + 7, v + 4);
    }
    // you are here: arrow along the view direction
    const c = app.camera.position, dir = new THREE.Vector3();
    app.camera.getWorldDirection(dir);
    const [du, dv] = toUV(dir.x, dir.z);
    const a = Math.atan2(dv, du);
    const [u, v] = P(c.x, c.z);
    ctx2.save();
    ctx2.translate(u, v); ctx2.rotate(a);
    ctx2.fillStyle = 'rgba(56,189,248,.18)';                    // view cone
    ctx2.beginPath(); ctx2.moveTo(0, 0); ctx2.arc(0, 0, 34, -0.6, 0.6); ctx2.closePath(); ctx2.fill();
    ctx2.fillStyle = '#ef4444'; ctx2.strokeStyle = '#fff'; ctx2.lineWidth = 2;
    ctx2.beginPath(); ctx2.moveTo(10, 0); ctx2.lineTo(-6, 6); ctx2.lineTo(-3, 0); ctx2.lineTo(-6, -6); ctx2.closePath();
    ctx2.stroke(); ctx2.fill();
    ctx2.restore();
    return { k, ox, oy };
  }

  let last = null;
  canvas.addEventListener('click', (e) => {
    const r = canvas.getBoundingClientRect();
    const { k, ox, oy } = last || draw();
    const cx = (e.clientX - r.left) * (canvas.width / r.width), cy = (e.clientY - r.top) * (canvas.height / r.height);
    const u = u0 + (cx - ox) / k / ppm, v = v0 + (cy - oy) / k / ppm;
    onGo(u * cs - v * sn, u * sn + v * cs);
  });

  return {
    update() { last = draw(); },
    setPois(pois) { layers.pois = pois || []; },
    setSelected(id) { selected = id; },
    setRoute(poly) { layers.route = poly; },
    setMeasures(chains) { layers.measures = chains; },
  };
}
