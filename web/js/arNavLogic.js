// AR Walk: the maths behind web/js/arNav.js, free of three.js and the DOM so Node can test it.
//
// Frames
//   device   W3C DeviceOrientation device frame: x right, y up the screen, z out of the screen (the back camera
//            looks along -z).
//   earth    W3C earth frame: x east, y north, z up (rotationMatrix in captureLogic.js: device -> earth).
//   world    the AR scene: metres, +Y up (gravity), x east, z south (three.js convention, y up, -z ahead).
//   model    the scene package frame: +Y up, model units (metres_per_unit), the walking path lives in its XZ plane.
// The phone gives gravity exactly but its yaw is only relative (or compass-based, which is noisy indoors), so the
// model is placed in the world by one yaw angle (theta), fixed when the visitor faces along the path and taps
// "Start", and re-fixed with "Re-align" at any time.

import { rotationMatrix } from './captureLogic.js';
import { pointAlong, polylineLength, remainingFrom } from './routePlan.js';

const D2R = Math.PI / 180;

/** Device -> earth rotation, compensated for the screen orientation (degrees, screen.orientation.angle). */
export function screenRotation(alpha, beta, gamma, screenAngleDeg = 0) {
  const R = rotationMatrix(alpha || 0, beta || 0, gamma || 0);
  if (!screenAngleDeg) return R;
  // screen frame = device frame turned by -angle about the device z axis: R * Rz(-angle)
  const c = Math.cos(-screenAngleDeg * D2R), s = Math.sin(-screenAngleDeg * D2R);
  return [
    R[0] * c + R[1] * s, -R[0] * s + R[1] * c, R[2],
    R[3] * c + R[4] * s, -R[3] * s + R[4] * c, R[5],
    R[6] * c + R[7] * s, -R[6] * s + R[7] * c, R[8],
  ];
}

/** Earth (ENU, z up) -> world (y up, z south): rows of M are [1,0,0], [0,0,1], [0,-1,0]. Returns M * R. */
export function earthToWorld(R) {
  return [R[0], R[1], R[2], R[6], R[7], R[8], -R[3], -R[4], -R[5]];
}

/** Row-major 3x3 rotation -> unit quaternion [x, y, z, w]. */
export function matToQuat(m) {
  const [m00, m01, m02, m10, m11, m12, m20, m21, m22] = m;
  const tr = m00 + m11 + m22;
  let x, y, z, w;
  if (tr > 0) {
    const s = 0.5 / Math.sqrt(tr + 1);
    w = 0.25 / s; x = (m21 - m12) * s; y = (m02 - m20) * s; z = (m10 - m01) * s;
  } else if (m00 > m11 && m00 > m22) {
    const s = 2 * Math.sqrt(1 + m00 - m11 - m22);
    w = (m21 - m12) / s; x = 0.25 * s; y = (m01 + m10) / s; z = (m02 + m20) / s;
  } else if (m11 > m22) {
    const s = 2 * Math.sqrt(1 + m11 - m00 - m22);
    w = (m02 - m20) / s; x = (m01 + m10) / s; y = 0.25 * s; z = (m12 + m21) / s;
  } else {
    const s = 2 * Math.sqrt(1 + m22 - m00 - m11);
    w = (m10 - m01) / s; x = (m02 + m20) / s; y = (m12 + m21) / s; z = 0.25 * s;
  }
  const n = Math.hypot(x, y, z, w);
  return [x / n, y / n, z / n, w / n];
}

/** Angle between two orientations (radians, 0..pi). */
export function quatAngle(a, b) {
  const d = Math.abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]);
  return 2 * Math.acos(Math.min(1, d));
}

export function slerp(a, b, t) {
  let d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  const bb = d < 0 ? b.map((v) => -v) : b;
  d = Math.abs(d);
  if (d > 0.9995) {
    const r = a.map((v, i) => v + (bb[i] - v) * t);
    const n = Math.hypot(...r);
    return r.map((v) => v / n);
  }
  const th = Math.acos(d), s = Math.sin(th);
  const ka = Math.sin((1 - t) * th) / s, kb = Math.sin(t * th) / s;
  return a.map((v, i) => ka * v + kb * bb[i]);
}

/**
 * Orientation jitter filter: an adaptive ("one euro") low-pass on the rotation. A still phone gets a low cutoff
 * (minCutoff Hz: the arrows stop shaking), a turning phone a high one (cutoff rises by `beta` per rad/s), so real
 * motion is followed without lag.
 */
export class OrientationFilter {
  constructor({ minCutoff = 1.2, beta = 6, speedCutoff = 1.0 } = {}) {
    Object.assign(this, { minCutoff, beta, speedCutoff, q: null, t: 0, speed: 0 });
  }

  /** q: [x,y,z,w], tMs: timestamp. Returns the filtered quaternion. */
  update(q, tMs) {
    if (!this.q) { this.q = q.slice(); this.t = tMs; return this.q; }
    const dt = Math.max(1e-3, (tMs - this.t) / 1000);
    this.t = tMs;
    const raw = quatAngle(this.q, q) / dt;
    const aS = 1 / (1 + 1 / (2 * Math.PI * this.speedCutoff * dt));
    this.speed += aS * (raw - this.speed);
    const fc = this.minCutoff + this.beta * this.speed;
    const a = 1 / (1 + 1 / (2 * Math.PI * fc * dt));
    this.q = slerp(this.q, q, a);
    return this.q;
  }
}

/**
 * Walking heading in the world (radians, atan2(x, z) convention: 0 = +Z, pi = -Z) from a world rotation: the
 * camera's view direction flattened onto the floor; when the phone is held flat, the top edge of the phone instead.
 * null when neither is usable.
 */
export function worldHeading(Rw) {
  let x = -Rw[2], z = -Rw[8];                        // camera forward = -(third column)
  if (Math.hypot(x, z) < 0.3) { x = Rw[1]; z = Rw[7]; }  // phone flat: its top edge (+y column)
  if (Math.hypot(x, z) < 1e-6) return null;
  return Math.atan2(x, z);
}

export const headingOfVec = (dx, dz) => Math.atan2(dx, dz);
export const wrap = (a) => Math.atan2(Math.sin(a), Math.cos(a));

/** Yaw that turns the model's path direction (dx, dz) onto the phone's current world heading. */
export function alignYaw(worldHeadingRad, dx, dz) {
  return wrap(worldHeadingRad - headingOfVec(dx, dz));
}

/** Model point (x, z) -> world metres (x, z) for yaw theta and metres per model unit. */
export function modelToWorld(x, z, theta, mpu) {
  const c = Math.cos(theta), s = Math.sin(theta);
  return [(x * c + z * s) * mpu, (-x * s + z * c) * mpu];
}

/** Vertical field of view (degrees) of a camera stream shown with object-fit: cover. longFovDeg is the camera's
 * field of view across the long side of its picture (about 65-70 degrees on phone main cameras). */
export function coverFov(screenW, screenH, vidW, vidH, longFovDeg = 66) {
  const f = (Math.max(vidW, vidH) / 2) / Math.tan((longFovDeg * D2R) / 2);
  const s = Math.max(screenW / vidW, screenH / vidH);
  return (2 * Math.atan((screenH / (2 * s)) / f)) / D2R;
}

/** Signed turn at vertex k of a polyline in degrees: > 0 right, < 0 left (seen from above, +Y up, -Z ahead). */
export function turnAt(poly, k) {
  if (k <= 0 || k >= poly.length - 1) return 0;
  const ax = poly[k][0] - poly[k - 1][0], az = poly[k][1] - poly[k - 1][1];
  const bx = poly[k + 1][0] - poly[k][0], bz = poly[k + 1][1] - poly[k][1];
  return Math.atan2(ax * bz - az * bx, ax * bx + az * bz) / D2R;
}

export const TURN_MIN_DEG = 25;          // smaller bends are announced as "straight on"
export const TURN_SOON_M = 2.0;          // "turn now" inside this distance from the bend
export const ARRIVE_M = 1.0;
export const OFF_PATH_M = 1.5;
const AGREE_RAD = 60 * D2R;               // a step within this angle of the path direction is taken as following it

/** Distance along the polyline (model units) of each vertex. */
export function vertexDistances(poly) {
  const out = [0];
  for (let k = 1; k < poly.length; k++) out.push(out[k - 1] + Math.hypot(poly[k][0] - poly[k - 1][0], poly[k][1] - poly[k - 1][1]));
  return out;
}

/**
 * Turn-by-turn instruction at distance `along` (model units) on the path.
 * -> { kind: 'straight' | 'left' | 'right' | 'slight-left' | 'slight-right' | 'arrive' | 'arrived',
 *      text, metres (to the next manoeuvre), remaining (m), turnIndex (vertex of the next turn or -1) }
 */
export function guidance(poly, along, mpu, offPathM = 0) {
  const vd = vertexDistances(poly), total = vd[vd.length - 1];
  const remaining = Math.max(0, (total - along) * mpu);
  const fmt = (m) => `${m.toFixed(m < 10 ? 1 : 0)} m`;
  if (remaining <= ARRIVE_M) return { kind: 'arrived', text: 'You have arrived', metres: 0, remaining, turnIndex: -1, offPath: false };
  let k = vd.findIndex((d) => d > along + 1e-9);
  let next = -1;
  for (; k > 0 && k < poly.length - 1; k++) if (Math.abs(turnAt(poly, k)) >= TURN_MIN_DEG) { next = k; break; }
  const off = offPathM > OFF_PATH_M;
  if (next < 0) {
    return { kind: 'arrive', text: off ? 'Off path — head back' : `Destination ahead · ${fmt(remaining)}`, metres: remaining, remaining, turnIndex: -1, offPath: off };
  }
  const m = Math.max(0, (vd[next] - along) * mpu), a = turnAt(poly, next);
  const side = a > 0 ? 'right' : 'left', slight = Math.abs(a) < 60;
  const kind = slight ? `slight-${side}` : side;
  const turnText = `${slight ? 'Bear' : 'Turn'} ${side}`;
  const text = off ? 'Off path — head back' : m <= TURN_SOON_M ? turnText : `Walk straight · ${fmt(m)}`;
  return { kind: m <= TURN_SOON_M ? kind : 'straight', text, metres: m, remaining, turnIndex: next, offPath: off, nextTurn: kind };
}

/**
 * Dead reckoning along the path. Each detected step moves the visitor one stride (metres) in the direction the phone
 * faces; the estimate is then pulled towards the path (map matching: indoors people follow the path far more often
 * than the step headings say), unless the visitor is clearly walking away from it. Progress never runs backwards by
 * more than a stride, so heading noise cannot make the instructions jump around.
 */
export class PathTracker {
  constructor(poly, { mpu, stride = 0.7, snap = 0.6 } = {}) {
    Object.assign(this, { poly, mpu, stride, snap, total: polylineLength(poly), along: 0, steps: 0 });
    this.pos = poly[0].slice();
    this.offPath = 0;
  }

  /** Visitor placed at distance `along` (model units) on the path, e.g. after "I am here". */
  placeAt(along) {
    this.along = Math.max(0, Math.min(this.total, along));
    this.pos = pointAlong(this.poly, this.along).point.slice();
    this.offPath = 0;
  }

  /** One step in model heading h (radians, atan2(dx, dz) convention), or along the path when h is null. */
  step(h = null) {
    const len = this.stride / this.mpu;
    this.steps += 1;
    if (h === null) { this.placeAt(this.along + len); return this.state(); }
    const nx = this.pos[0] + Math.sin(h) * len, nz = this.pos[1] + Math.cos(h) * len;
    const { remaining, offPath } = remainingFrom(this.poly, nx, nz);
    const along = this.total - remaining;
    const onPath = pointAlong(this.poly, along).point;
    // pull onto the path only while the step goes roughly the path's way; walking away is followed (and reported)
    const [dx, dz] = this.direction();
    const agrees = Math.abs(wrap(h - headingOfVec(dx, dz))) < AGREE_RAD;
    const k = !agrees || offPath * this.mpu > OFF_PATH_M ? 0.1 : this.snap;
    this.pos = [nx + (onPath[0] - nx) * k, nz + (onPath[1] - nz) * k];
    const r = remainingFrom(this.poly, this.pos[0], this.pos[1]);
    this.along = Math.max(this.along - len, this.total - r.remaining);
    this.offPath = r.offPath * this.mpu;
    return this.state();
  }

  /** Path direction (model dx, dz) at the current progress. */
  direction() {
    const { heading } = pointAlong(this.poly, Math.min(this.total - 1e-6, this.along));
    return heading;
  }

  state() {
    return { pos: this.pos.slice(), along: this.along, offPath: this.offPath, steps: this.steps,
      ...guidance(this.poly, this.along, this.mpu, this.offPath) };
  }
}

/** Floor chevrons every `spacing` model units: [{ x, z, heading, d }] (heading in the atan2(dx, dz) convention). */
export function chevronPlacements(poly, spacing) {
  const total = polylineLength(poly), out = [];
  if (!(spacing > 0)) return out;
  for (let d = spacing * 0.5; d < total - spacing * 0.25; d += spacing) {
    const { point, heading } = pointAlong(poly, d);
    out.push({ x: point[0], z: point[1], heading: headingOfVec(heading[0], heading[1]), d });
  }
  return out;
}

/** Haptic patterns (navigator.vibrate). */
export const HAPTIC = { turn: [40], arrive: [60, 50, 100], offPath: [20, 40, 20] };

/** |acceleration| for the step detector from a DeviceMotionEvent-like object: gravity-free acceleration when the
 * browser gives it, otherwise |accelerationIncludingGravity| - g. null when neither is present. */
export function motionMagnitude(e) {
  const a = e && e.acceleration;
  if (a && a.x != null) return Math.hypot(a.x, a.y, a.z);
  const g = e && e.accelerationIncludingGravity;
  if (g && g.x != null) return Math.abs(Math.hypot(g.x, g.y, g.z) - 9.81);
  return null;
}
