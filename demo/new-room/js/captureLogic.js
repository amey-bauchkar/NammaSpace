// Auto-capture decisions for the phone page (pure functions: unit-tested in web/tests/capture_logic.test.js).
//
// A phone browser has no camera pose (no WebXR on iOS), so a keyframe is chosen from what the page CAN measure:
//  - sharpness   : variance of the Laplacian of a small grey image, relative to the recent median (adapts to the room)
//  - exposure    : mean brightness and clipped share
//  - novelty     : how far the view moved since the last keyframe (image shift found by correlation, appearance change)
//  - motion      : gyroscope rate (blur) and rotation accumulated since the last keyframe (overlap)
// Photogrammetry needs every surface in 3+ overlapping views: a new keyframe is taken after a moderate change
// (about 8 degrees of turn or 12 % of the image), and the user is told to slow down when a jump would break overlap.

export const DEFAULTS = {
  minIntervalMs: 350, warmupMs: 800, maxFrames: 250,
  minMean: 40, maxMean: 225, maxDark: 0.45, maxBright: 0.35,
  maxGyroRad: 1.0,            // rad/s: faster turns blur 1/60 s exposures
  sharpRel: 0.6,              // keep only frames at least this sharp relative to the recent median
  rotStepDeg: 8, shiftStep: 0.12, changeStep: 0.35,
  rotMaxDeg: 30,              // more than this since the last keyframe: overlap is at risk
  thumbW: 40, thumbH: 30, maxShift: 8,
};

export function grey(rgba, w, h) {
  const g = new Float32Array(w * h);
  for (let i = 0, j = 0; i < g.length; i += 1, j += 4) g[i] = 0.299 * rgba[j] + 0.587 * rgba[j + 1] + 0.114 * rgba[j + 2];
  return g;
}

export function sharpness(g, w, h) {
  let s = 0; let s2 = 0; let n = 0;
  for (let y = 1; y < h - 1; y += 1) {
    for (let x = 1; x < w - 1; x += 1) {
      const i = y * w + x;
      const l = 4 * g[i] - g[i - 1] - g[i + 1] - g[i - w] - g[i + w];
      s += l; s2 += l * l; n += 1;
    }
  }
  if (!n) return 0;
  const m = s / n;
  return s2 / n - m * m;
}

export function exposure(g) {
  let s = 0; let dark = 0; let bright = 0;
  for (const v of g) { s += v; if (v <= 12) dark += 1; if (v >= 245) bright += 1; }
  const n = g.length || 1;
  return { mean: s / n, dark: dark / n, bright: bright / n };
}

/** Box-downsampled, zero-mean / unit-variance thumbnail (comparison is then brightness-invariant). */
export function thumb(g, w, h, tw = DEFAULTS.thumbW, th = DEFAULTS.thumbH) {
  const t = new Float32Array(tw * th);
  for (let ty = 0; ty < th; ty += 1) {
    const y0 = Math.floor((ty * h) / th); const y1 = Math.max(y0 + 1, Math.floor(((ty + 1) * h) / th));
    for (let tx = 0; tx < tw; tx += 1) {
      const x0 = Math.floor((tx * w) / tw); const x1 = Math.max(x0 + 1, Math.floor(((tx + 1) * w) / tw));
      let s = 0;
      for (let y = y0; y < y1; y += 1) for (let x = x0; x < x1; x += 1) s += g[y * w + x];
      t[ty * tw + tx] = s / ((y1 - y0) * (x1 - x0));
    }
  }
  let m = 0; for (const v of t) m += v; m /= t.length;
  let sd = 0; for (const v of t) sd += (v - m) ** 2; sd = Math.sqrt(sd / t.length) || 1;
  for (let i = 0; i < t.length; i += 1) t[i] = (t[i] - m) / sd;
  return t;
}

/** Best integer shift of b against a (normalised cross-correlation over the overlap). */
export function bestShift(a, b, tw = DEFAULTS.thumbW, th = DEFAULTS.thumbH, maxShift = DEFAULTS.maxShift) {
  let best = { dx: 0, dy: 0, ncc: -1 };
  for (let dy = -maxShift; dy <= maxShift; dy += 1) {
    for (let dx = -maxShift; dx <= maxShift; dx += 1) {
      let s = 0; let sa = 0; let sb = 0; let n = 0;
      for (let y = Math.max(0, -dy); y < Math.min(th, th - dy); y += 1) {
        for (let x = Math.max(0, -dx); x < Math.min(tw, tw - dx); x += 1) {
          const va = a[y * tw + x]; const vb = b[(y + dy) * tw + (x + dx)];
          s += va * vb; sa += va * va; sb += vb * vb; n += 1;
        }
      }
      if (n < (tw * th) / 3) continue;
      const ncc = s / (Math.sqrt(sa * sb) || 1);
      if (ncc > best.ncc + 1e-9) best = { dx, dy, ncc };
    }
  }
  return best;
}

/** Coverage cell for a viewing direction: 24 compass sectors x 3 pitch bands (down / level / up). */
export function coverageCell(headingDeg, pitchDeg) {
  const sector = Math.floor((((headingDeg % 360) + 360) % 360) / 15);
  const band = pitchDeg < -25 ? 0 : pitchDeg > 25 ? 2 : 1;
  return band * 24 + sector;
}

export class KeyframeSelector {
  constructor(opts = {}) {
    this.o = { ...DEFAULTS, ...opts };
    this.sharpHist = [];
    this.last = null;           // { thumb, t }
    this.count = 0;
    this.startT = null;
  }

  /**
   * sample: { t (ms), sharp, mean, dark, bright, thumb, gyroRad, rotSinceKeyDeg }
   * -> { keep: bool, prompt: string|null, reason: string, change: number }
   */
  update(s) {
    const o = this.o;
    if (this.startT === null) this.startT = s.t;
    this.sharpHist.push(s.sharp);
    if (this.sharpHist.length > 40) this.sharpHist.shift();
    const med = [...this.sharpHist].sort((x, y) => x - y)[Math.floor(this.sharpHist.length / 2)];
    const no = (reason, prompt = null) => ({ keep: false, prompt, reason, change: 0 });
    if (this.count >= o.maxFrames) return no('full', 'Frame limit reached - tap Done');
    if (s.mean < o.minMean || s.dark > o.maxDark) return no('dark', 'Too dark - turn on the lights');
    if (s.mean > o.maxMean || s.bright > o.maxBright) return no('bright', 'Too bright - avoid pointing at windows or lamps');
    if (s.gyroRad > o.maxGyroRad) return no('fast', 'Slow down');
    if (s.t - this.startT < o.warmupMs) return no('warmup');
    if (this.last && s.t - this.last.t < o.minIntervalMs) return no('interval');
    if (this.sharpHist.length >= 5 && s.sharp < o.sharpRel * med) return no('blurry', 'Hold steady');
    if (!this.last) return this.keep(s, 'first', 1);
    const sh = bestShift(this.last.thumb, s.thumb, o.thumbW, o.thumbH, o.maxShift);
    const shiftFrac = Math.hypot(sh.dx / o.thumbW, sh.dy / o.thumbH);
    const change = Math.max((s.rotSinceKeyDeg || 0) / o.rotStepDeg, shiftFrac / o.shiftStep, (1 - sh.ncc) / o.changeStep);
    if (change < 1) return { keep: false, prompt: null, reason: 'similar', change };
    const tooFar = (s.rotSinceKeyDeg || 0) > o.rotMaxDeg;
    return this.keep(s, tooFar ? 'jump' : 'new-view', change, tooFar ? 'Too fast - go back a little and move slower' : null);
  }

  keep(s, reason, change, prompt = null) {
    this.last = { thumb: s.thumb, t: s.t };
    this.count += 1;
    return { keep: true, prompt, reason, change };
  }
}

// ------------------------------------------------------------------ coverage from the motion sensors (no tracking)
// A browser on iOS knows the phone's ORIENTATION (deviceorientation) but not its POSITION. Coverage is therefore kept
// per viewing direction on a sphere (5-degree cells): seen once, then covered when seen again after WALKING (steps
// from the accelerometer); the capture page shows the covered share as a percentage. Requiring a second look from
// another position rewards the walking photogrammetry needs; spinning on the spot (first own capture, 2026-10-21)
// cannot complete it.

export const CELL_DEG = 5;
export const AZ_CELLS = 360 / CELL_DEG;   // 72
export const EL_CELLS = 180 / CELL_DEG;   // 36

/** W3C DeviceOrientation (alpha, beta, gamma in degrees) -> 3x3 rotation, device frame -> earth frame (row-major). */
export function rotationMatrix(alpha, beta, gamma) {
  const r = Math.PI / 180;
  const [cA, sA, cB, sB, cG, sG] = [Math.cos(alpha * r), Math.sin(alpha * r), Math.cos(beta * r), Math.sin(beta * r),
    Math.cos(gamma * r), Math.sin(gamma * r)];
  // R = Rz(alpha) * Rx(beta) * Ry(gamma)
  return [
    cA * cG - sA * sB * sG, -sA * cB, cA * sG + sA * sB * cG,
    sA * cG + cA * sB * sG, cA * cB, sA * sG - cA * sB * cG,
    -cB * sG, sB, cB * cG,
  ];
}

/** Earth-frame direction of a screen point (u, v in -1..1, v up) seen by the BACK camera (looks along device -z). */
export function screenDirection(R, u, v, hfovDeg, vfovDeg) {
  const tx = Math.tan((hfovDeg * Math.PI) / 360) * u;
  const ty = Math.tan((vfovDeg * Math.PI) / 360) * v;
  const n = Math.hypot(tx, ty, 1);
  const d = [tx / n, ty / n, -1 / n];
  return [R[0] * d[0] + R[1] * d[1] + R[2] * d[2], R[3] * d[0] + R[4] * d[1] + R[5] * d[2], R[6] * d[0] + R[7] * d[1] + R[8] * d[2]];
}

export function cellOf(dir) {
  const az = ((Math.atan2(dir[0], dir[1]) * 180) / Math.PI + 360) % 360;
  const el = (Math.asin(Math.max(-1, Math.min(1, dir[2]))) * 180) / Math.PI;
  return Math.min(EL_CELLS - 1, Math.floor((el + 90) / CELL_DEG)) * AZ_CELLS + Math.min(AZ_CELLS - 1, Math.floor(az / CELL_DEG));
}

export class CoverageMap {
  constructor() { this.seen = new Map(); }       // cell -> Set of position ids (walking segments)

  /** Mark the central part of the current view as seen from position `pos`. */
  mark(R, hfov, vfov, pos, inner = 0.7) {
    // sample finer than a cell (0.6 x CELL_DEG apart) so no row / column of cells is skipped (a fixed 7 x 7 grid left
    // 5-degree stripes unmarked across a 65-degree view)
    const nx = Math.ceil((hfov * inner) / (CELL_DEG * 0.6)) + 1; const ny = Math.ceil((vfov * inner) / (CELL_DEG * 0.6)) + 1;
    for (let i = 0; i < nx; i += 1) {
      for (let j = 0; j < ny; j += 1) {
        const u = (((i + 0.5) / nx) * 2 - 1) * inner; const v = (((j + 0.5) / ny) * 2 - 1) * inner;
        const c = cellOf(screenDirection(R, u, v, hfov, vfov));
        let s = this.seen.get(c);
        if (!s) { s = new Set(); this.seen.set(c, s); }
        if (s.size < 3) s.add(pos);
      }
    }
  }

  level(cell) { const s = this.seen.get(cell); return s ? Math.min(2, s.size) : 0; }

  /** Share of seen cells that are fully covered, and how many were seen at all. */
  stats() {
    let full = 0;
    for (const s of this.seen.values()) if (s.size >= 2) full += 1;
    return { seen: this.seen.size, covered: full, coveredShare: this.seen.size ? full / this.seen.size : 0 };
  }
}

/** Pedometer-style step detection from devicemotion acceleration (m/s^2, gravity removed). */
export class StepDetector {
  constructor({ threshold = 1.3, minGapMs = 300, smooth = 0.3 } = {}) {
    Object.assign(this, { threshold, minGapMs, smooth, f: 0, prev: 0, rising: false, lastStep: -1e9, steps: 0 });
  }

  /** a: |acceleration| in m/s^2, t: ms. Returns true when a step is counted. */
  update(a, t) {
    this.f += this.smooth * (a - this.f);
    let step = false;
    if (this.f > this.prev) this.rising = true;
    else if (this.rising && this.prev > this.threshold && t - this.lastStep >= this.minGapMs) {
      this.steps += 1; this.lastStep = t; step = true; this.rising = false;
    } else if (this.f < this.prev) this.rising = false;
    this.prev = this.f;
    return step;
  }
}
