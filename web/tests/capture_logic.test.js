// Auto-capture keyframe decisions (synthetic grey images; no camera).
import test from 'node:test';
import assert from 'node:assert/strict';
import { KeyframeSelector, bestShift, coverageCell, exposure, sharpness, thumb } from '../js/captureLogic.js';

const W = 160; const H = 120;
function scene(offsetX = 0, blur = false, level = 1) {
  // textured "room": stripes and blobs; offsetX pans the view
  const g = new Float32Array(W * H);
  for (let y = 0; y < H; y += 1) {
    for (let x = 0; x < W; x += 1) {
      const u = x + offsetX;
      // coarse structure + fine detail (edges, texture) as in a real room photo
      g[y * W + x] = level * (110 + 50 * Math.sin(u / 5) * Math.cos(y / 7) + 30 * Math.sin((u * y) / 900)
        + 25 * Math.sin(u * 1.7 + y * 0.9) * Math.cos(y * 1.3));
    }
  }
  if (!blur) return g;
  const b = new Float32Array(g.length);
  for (let y = 0; y < H; y += 1) for (let x = 3; x < W - 3; x += 1) {
    let s = 0; for (let k = -3; k <= 3; k += 1) s += g[y * W + x + k]; b[y * W + x] = s / 7;
  }
  return b;
}
const sample = (g, t, extra = {}) => ({ t, sharp: sharpness(g, W, H), ...exposure(g), thumb: thumb(g, W, H), gyroRad: 0.1, rotSinceKeyDeg: 0, ...extra });

test('sharpness drops with blur; exposure flags darkness', () => {
  assert.ok(sharpness(scene(0), W, H) > 3 * sharpness(scene(0, true), W, H));
  assert.ok(exposure(scene(0, false, 0.15)).mean < 40);
});

test('image shift is recovered from thumbnails', () => {
  const a = thumb(scene(0), W, H); const b = thumb(scene(16), W, H);   // 16 px of 160 = 4 thumb px
  const s = bestShift(a, b);
  assert.equal(s.dy, 0);
  assert.ok(Math.abs(Math.abs(s.dx) - 4) <= 1, JSON.stringify(s));
  assert.ok(bestShift(a, a).ncc > 0.999);
});

test('keeps a first frame, skips near-duplicates, keeps after enough change', () => {
  const k = new KeyframeSelector({ warmupMs: 0, minIntervalMs: 0 });
  assert.equal(k.update(sample(scene(0), 0)).keep, true);
  assert.equal(k.update(sample(scene(1), 100)).keep, false);             // tiny pan: same view
  const d = k.update(sample(scene(30), 200));                              // ~19 % pan: new view
  assert.equal(d.keep, true, JSON.stringify(d));
  assert.equal(k.update(sample(scene(30), 300, { rotSinceKeyDeg: 9 })).keep, true);   // gyro says we turned
});

test('refuses blurry, dark, bright and fast-moving frames with a prompt', () => {
  const k = new KeyframeSelector({ warmupMs: 0, minIntervalMs: 0 });
  for (let i = 0; i < 6; i += 1) k.update(sample(scene(i * 40), i * 100));
  const blurry = k.update(sample(scene(500, true), 900));
  assert.equal(blurry.keep, false); assert.equal(blurry.reason, 'blurry');
  assert.match(k.update(sample(scene(0, false, 0.15), 1000)).prompt, /dark/);
  assert.match(k.update(sample(scene(0, false, 2.2), 1100)).prompt, /bright/i);
  assert.equal(k.update(sample(scene(700), 1200, { gyroRad: 2 })).prompt, 'Slow down');
});

test('a big jump is still kept but the user is told to go back', () => {
  const k = new KeyframeSelector({ warmupMs: 0, minIntervalMs: 0 });
  k.update(sample(scene(0), 0));
  const d = k.update(sample(scene(80), 100, { rotSinceKeyDeg: 45 }));
  assert.equal(d.keep, true); assert.match(d.prompt, /go back/);
});

test('frame limit and coverage cells', () => {
  const k = new KeyframeSelector({ warmupMs: 0, minIntervalMs: 0, maxFrames: 1 });
  k.update(sample(scene(0), 0));
  assert.equal(k.update(sample(scene(80), 100, { rotSinceKeyDeg: 20 })).reason, 'full');
  assert.equal(coverageCell(0, 0), 24); assert.equal(coverageCell(359, -40), 23); assert.equal(coverageCell(90, 40), 48 + 6);
});
