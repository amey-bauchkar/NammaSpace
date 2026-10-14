// three.js viewer shell (WP-07, ARCH-009 provisional engine).
// World coordinates = site frame (right-handed, metres, +Y up).
import * as THREE from 'three';
import { canOccupy, collisionModel } from './collision.js';
import { forwardFromAngles, lookAngles } from './frames.js';

const EYE_HEIGHT = 1.6;
const BODY_RADIUS = 0.2;

export class FirstPersonController {
  constructor(camera, model) {
    this.camera = camera;
    this.model = model;
    this.yaw = 0;
    this.pitch = 0;
    this.speed = 1.5; // m/s
    this.keys = new Set();
    this.collision = true;
    camera.rotation.order = 'YXZ';
  }

  setPose(position, target) {
    this.camera.position.set(...position);
    const { yaw, pitch } = lookAngles(position, target);
    this.yaw = yaw;
    this.pitch = pitch;
    this.apply();
  }

  look(dx, dy) {
    this.yaw -= dx * 0.003;
    this.pitch = Math.max(-1.45, Math.min(1.45, this.pitch - dy * 0.003));
    this.apply();
  }

  apply() {
    this.camera.rotation.set(this.pitch, this.yaw, 0, 'YXZ');
    this.camera.updateMatrixWorld(true);
  }

  forward() {
    return forwardFromAngles(this.yaw, this.pitch);
  }

  update(dt) {
    const f = [-Math.sin(this.yaw), -Math.cos(this.yaw)];
    const r = [Math.cos(this.yaw), -Math.sin(this.yaw)];
    let mx = 0, mz = 0;
    if (this.keys.has('KeyW')) { mx += f[0]; mz += f[1]; }
    if (this.keys.has('KeyS')) { mx -= f[0]; mz -= f[1]; }
    if (this.keys.has('KeyD')) { mx += r[0]; mz += r[1]; }
    if (this.keys.has('KeyA')) { mx -= r[0]; mz -= r[1]; }
    const n = Math.hypot(mx, mz);
    if (!n) return false;
    const step = (this.speed * dt) / n;
    const p = this.camera.position;
    const nx = p.x + mx * step, nz = p.z + mz * step;
    const ok = (x, z) => !this.collision || !this.model || canOccupy(this.model, x, z, BODY_RADIUS);
    if (ok(nx, nz)) { p.x = nx; p.z = nz; }
    else if (ok(nx, p.z)) p.x = nx;      // slide along walls
    else if (ok(p.x, nz)) p.z = nz;
    else return false;
    this.camera.updateMatrixWorld(true);
    return true;
  }
}

function labelSprite(text, color = '#111') {
  const c = document.createElement('canvas');
  c.width = 256; c.height = 64;
  const g = c.getContext('2d');
  g.font = 'bold 40px system-ui, sans-serif';
  g.fillStyle = color;
  g.fillText(text, 8, 46);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false }));
  s.scale.set(0.8, 0.2, 1);
  return s;
}

export class Viewer {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xeef1f5);
    this.camera = new THREE.PerspectiveCamera(70, 1, 0.05, 200);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8a80, 2.2));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(4, 10, 6);
    this.scene.add(sun);
    this.markers = new THREE.Group(); this.markers.name = 'poi-markers';
    this.routeGroup = new THREE.Group(); this.routeGroup.name = 'routes';
    this.dev = new THREE.Group(); this.dev.name = 'dev-aids'; this.dev.visible = false;
    this.geometryOverlay = new THREE.Group(); this.geometryOverlay.name = 'geometry-overlay'; this.geometryOverlay.visible = false;
    this.scene.add(this.markers, this.routeGroup, this.dev, this.geometryOverlay);
    this.controller = new FirstPersonController(this.camera, null);
    this.frames = 0;
    this.buildDevAids();
    this.resize();
  }

  buildDevAids() {
    const axes = new THREE.AxesHelper(1.5); axes.name = 'site-axes';
    const grid = new THREE.GridHelper(24, 24, 0x8899aa, 0xc8d0d8); grid.position.y = 0.002;
    const lx = labelSprite('+X', '#c0392b'); lx.position.set(1.8, 0.1, 0);
    const ly = labelSprite('+Y up', '#27ae60'); ly.position.set(0, 1.8, 0);
    const lz = labelSprite('+Z', '#2c6fd6'); lz.position.set(0, 0.1, 1.8);
    this.dev.add(axes, grid, lx, ly, lz);
  }

  setGeometry(geometry) {
    this.model = collisionModel(geometry);
    this.controller.model = this.model;
    const floor = geometry.floors[0];
    for (const o of geometry.obstacles) {
      const pts = o.footprint.map(([x, z]) => new THREE.Vector3(x, floor.elevation_m + 0.01, z));
      const blocking = this.model.blocking.some((b) => b.id === o.obstacle_id);
      const line = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts),
        new THREE.LineBasicMaterial({ color: blocking ? 0xd35400 : 0x7f8c8d }));
      line.name = `footprint:${o.obstacle_id}`;
      this.geometryOverlay.add(line);
    }
  }

  setAppearance(root) {
    if (this.appearance) this.scene.remove(this.appearance);
    this.appearance = root;
    this.scene.add(root);
  }

  setPois(pois) {
    this.markers.clear();
    for (const p of pois) {
      if (p.verification.status !== 'verified') continue;
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.12, 20, 12),
        new THREE.MeshStandardMaterial({ color: 0x2c6fd6, emissive: 0x0b2a66 }));
      m.position.set(...p.marker_site_m);
      m.name = `poi:${p.poi_id}`;
      m.userData = { poiId: p.poi_id };
      this.markers.add(m);
      if (p.route_endpoint_xz_m) {
        const e = new THREE.Mesh(new THREE.RingGeometry(0.08, 0.14, 24), new THREE.MeshBasicMaterial({ color: 0x16a085, side: THREE.DoubleSide }));
        e.rotation.x = -Math.PI / 2;
        e.position.set(p.route_endpoint_xz_m[0], 0.015, p.route_endpoint_xz_m[1]);
        e.name = `endpoint:${p.poi_id}`;
        this.dev.add(e);
      }
    }
  }

  clearRoute() {
    this.routeGroup.clear();
  }

  /** Draw a route. style: 'valid' (only after the gate says valid) or 'rejected' (dev mode only). */
  drawRoute(polyline, style) {
    this.clearRoute();
    const pts = polyline.map(([x, z]) => new THREE.Vector3(x, 0.06, z));
    let obj;
    if (style === 'valid') {
      const path = new THREE.CurvePath();
      for (let i = 1; i < pts.length; i++) path.add(new THREE.LineCurve3(pts[i - 1], pts[i]));
      obj = new THREE.Mesh(new THREE.TubeGeometry(path, Math.max(8, pts.length * 8), 0.05, 8, false),
        new THREE.MeshStandardMaterial({ color: 0x1e9e4a, emissive: 0x0c4d22 }));
    } else if (style === 'rejected') {
      obj = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
        new THREE.LineDashedMaterial({ color: 0xc0392b, dashSize: 0.15, gapSize: 0.1 }));
      obj.computeLineDistances();
    } else {
      throw new Error(`unknown route style ${style}`);
    }
    obj.name = `route:${style}`;
    obj.userData = { style, polyline };
    this.routeGroup.add(obj);
    return obj;
  }

  jumpTo(view) {
    this.controller.setPose(view.position_site_m, view.target_site_m);
  }

  resize() {
    const w = this.canvas.clientWidth || 800, h = this.canvas.clientHeight || 600;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  render() {
    this.renderer.render(this.scene, this.camera);
    this.frames += 1;
  }

  start() {
    let last = performance.now();
    const tick = (now) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      this.controller.update(dt);
      this.render();
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }
}

export { EYE_HEIGHT };
