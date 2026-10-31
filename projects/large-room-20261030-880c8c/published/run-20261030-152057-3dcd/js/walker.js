// First-person controller for scene packages (model frame, +Y up, floor at y = 0).
// Walk mode: eye height fixed, movement kept inside the roaming grid with wall sliding.
// Fly mode:  free movement (Q / E or Space / C for up / down) inside the scene bounds - for overview
//            and for recovering a lost camera. All speeds are in model units derived from the eye
//            height, so controls feel the same whether or not the real-world scale is known.
import { canOccupyGrid, isAllowed, nearestAllowed } from './roam.js';

export const KEYS = {
  forward: ['KeyW', 'ArrowUp'], back: ['KeyS', 'ArrowDown'], left: ['KeyA', 'ArrowLeft'], right: ['KeyD', 'ArrowRight'],
  up: ['KeyE', 'Space'], down: ['KeyQ', 'KeyC'], run: ['ShiftLeft', 'ShiftRight'],
};

// Start view: keep the photo's heading but limit its pitch to -20..+5 degrees, so a photo taken looking up at the
// ceiling (or down at the floor) still opens on the room at eye height.
export function levelStartTarget(position, target, eye, minPitch = -0.35, maxPitch = 0.09) {
  const dx = target[0] - position[0], dz = target[2] - position[2];
  const h = Math.hypot(dx, dz);
  if (h < 1e-9) return [position[0], eye, position[2] - eye];      // looking straight up / down: face -Z
  const pitch = Math.max(minPitch, Math.min(maxPitch, Math.atan2(target[1] - position[1], h)));
  return [position[0] + dx, eye + h * Math.tan(pitch), position[2] + dz];
}

export class Walker {
  constructor(camera, { eyeHeight, grid = null, bounds = null }) {
    this.camera = camera;
    this.eye = eyeHeight;
    this.grid = grid;
    this.bounds = bounds;
    this.mode = 'walk';
    this.keys = new Set();
    this.yaw = 0;
    this.pitch = 0;
    this.vel = [0, 0, 0];
    this.speed = 0.95 * eyeHeight;      // ~1.3 m/s for a 1.4 m capture height
    this.runFactor = 2.2;
    this.radius = 0.13 * eyeHeight;     // ~0.18 m body radius
    this.lookSensitivity = 0.0024;
    this.collision = true;
    this.blockedMoves = 0;
    camera.rotation.order = 'YXZ';
  }

  has(action) {
    return KEYS[action].some((k) => this.keys.has(k));
  }

  setPose(pos, target) {
    this.camera.position.set(pos[0], pos[1], pos[2]);
    const d = [target[0] - pos[0], target[1] - pos[1], target[2] - pos[2]];
    const n = Math.hypot(...d) || 1;
    this.yaw = Math.atan2(-d[0], -d[2]);
    this.pitch = Math.asin(Math.max(-1, Math.min(1, d[1] / n)));
    this.vel = [0, 0, 0];
    this.apply();
  }

  look(dx, dy) {
    this.yaw -= dx * this.lookSensitivity;
    this.pitch = Math.max(-1.45, Math.min(1.45, this.pitch - dy * this.lookSensitivity));
    this.apply();
  }

  apply() {
    this.camera.rotation.set(this.pitch, this.yaw, 0, 'YXZ');
    this.camera.updateMatrixWorld(true);
  }

  setMode(mode) {
    this.mode = mode;
    if (mode === 'walk') {
      this.camera.position.y = this.eye;
      if (this.grid && !isAllowed(this.grid, this.camera.position.x, this.camera.position.z)) this.recover();
    }
    this.apply();
  }

  /** Move to the nearest allowed spot (walk mode) - used when the camera ends up outside. */
  recover() {
    if (!this.grid) return false;
    const p = this.camera.position;
    const near = nearestAllowed(this.grid, p.x, p.z, 50 * this.eye);
    if (!near) return false;
    p.set(near[0], this.eye, near[1]);
    this.apply();
    return true;
  }

  ok(x, z) {
    if (!this.collision || !this.grid || this.mode === 'fly') return true;
    return canOccupyGrid(this.grid, x, z, this.radius);
  }

  update(dt) {
    const f = [-Math.sin(this.yaw), -Math.cos(this.yaw)];
    const r = [Math.cos(this.yaw), -Math.sin(this.yaw)];
    let mx = 0, mz = 0, my = 0;
    if (this.has('forward')) { mx += f[0]; mz += f[1]; }
    if (this.has('back')) { mx -= f[0]; mz -= f[1]; }
    if (this.has('right')) { mx += r[0]; mz += r[1]; }
    if (this.has('left')) { mx -= r[0]; mz -= r[1]; }
    if (this.mode === 'fly') {
      if (this.has('up')) my += 1;
      if (this.has('down')) my -= 1;
      if (this.has('forward') || this.has('back')) my += Math.sin(this.pitch) * (this.has('forward') ? 1 : -1);
    }
    const n = Math.hypot(mx, mz, my);
    const target = n ? [mx / n, my / n, mz / n].map((v) => v * this.speed * (this.has('run') ? this.runFactor : 1)) : [0, 0, 0];
    const k = Math.min(1, dt * 12);              // smooth acceleration / braking
    this.vel = this.vel.map((v, i) => v + (target[i] - v) * k);
    if (Math.hypot(...this.vel) < 1e-4 * this.eye) { this.vel = [0, 0, 0]; return false; }
    const p = this.camera.position;
    const nx = p.x + this.vel[0] * dt, nz = p.z + this.vel[2] * dt;
    let moved = true;
    const ox = p.x, oz = p.z;
    const startInside = this.ok(p.x, p.z);
    if (!startInside || this.ok(nx, nz)) { p.x = nx; p.z = nz; }
    else if (this.ok(nx, p.z)) { p.x = nx; this.vel[2] = 0; }       // slide along walls
    else if (this.ok(p.x, nz)) { p.z = nz; this.vel[0] = 0; }
    else { this.vel = [0, this.vel[1], 0]; moved = false; }
    if (Math.hypot(p.x - ox, p.z - oz) < 1e-6 * this.eye && Math.hypot(nx - ox, nz - oz) > 1e-6 * this.eye) {
      moved = false;                          // wanted to move but could not (blocked, or a zero-length slide)
      this.blockedMoves += 1;
    }
    if (this.mode === 'fly') {
      p.y += this.vel[1] * dt;
      if (this.bounds) {
        const [lo, hi] = this.bounds;
        const pad = 2 * this.eye;
        p.x = Math.max(lo[0] - pad, Math.min(hi[0] + pad, p.x));
        p.y = Math.max(lo[1] - pad, Math.min(hi[1] + pad, p.y));
        p.z = Math.max(lo[2] - pad, Math.min(hi[2] + pad, p.z));
      }
    } else {
      p.y = this.eye;
    }
    this.camera.updateMatrixWorld(true);
    return moved;
  }
}
