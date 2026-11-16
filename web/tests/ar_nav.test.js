// Round 3: AR Walk maths (web/js/arNavLogic.js) - sensor mapping, smoothing, dead reckoning, guidance. No browser.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  alignYaw, chevronPlacements, coverFov, earthToWorld, guidance, HAPTIC, matToQuat, modelToWorld, motionMagnitude,
  OrientationFilter, PathTracker, quatAngle, screenRotation, turnAt, worldHeading, wrap,
} from '../js/arNavLogic.js';

const near = (a, b, eps = 1e-6) => Math.abs(a - b) < eps;
const D = Math.PI / 180;

test('sensor mapping: an upright phone facing north looks along world -Z with +Y up', () => {
  // W3C: alpha 0 (north), beta 90 (upright), gamma 0
  const Rw = earthToWorld(screenRotation(0, 90, 0));
  // camera forward = -(third column) = world -Z (north); screen up = second column = world +Y
  assert.ok(near(-Rw[2], 0) && near(-Rw[5], 0) && near(-Rw[8], -1));
  assert.ok(near(Rw[1], 0) && near(Rw[4], 1) && near(Rw[7], 0));
  assert.ok(near(worldHeading(Rw), Math.PI) || near(worldHeading(Rw), -Math.PI));
  // turning the phone to face east (alpha = 270: alpha grows counter-clockwise) -> forward = world +X
  const Re = earthToWorld(screenRotation(270, 90, 0));
  assert.ok(near(worldHeading(Re), Math.PI / 2));
  // a rotation matrix stays orthonormal and the quaternion is a unit one
  const q = matToQuat(Rw);
  assert.ok(near(Math.hypot(...q), 1));
});

test('screen orientation: landscape is compensated so the camera still looks the same way', () => {
  const portrait = earthToWorld(screenRotation(0, 90, 0, 0));
  const turned = earthToWorld(screenRotation(0, 90, 0, 90));
  // the view direction (third column) does not change with a rotation about the screen normal
  assert.ok(near(portrait[2], turned[2]) && near(portrait[5], turned[5]) && near(portrait[8], turned[8]));
  // but the screen's up axis does: in landscape it is the phone's former x axis
  assert.ok(!near(portrait[4], turned[4]));
});

test('jitter filter: small shakes are damped strongly, a real turn is followed', () => {
  const f = new OrientationFilter();
  const base = matToQuat(earthToWorld(screenRotation(0, 90, 0)));
  f.update(base, 0);
  let t = 0, worst = 0;
  for (let i = 0; i < 120; i++) {          // 2 s of +-0.6 degree sensor noise at 60 Hz
    t += 16.7;
    const out = f.update(matToQuat(earthToWorld(screenRotation(i % 2 ? 0.6 : -0.6, 90, 0))), t);
    worst = Math.max(worst, quatAngle(out, base) / D);
  }
  assert.ok(worst < 0.35, `jitter left ${worst.toFixed(2)} deg of 0.6`);
  // a 90 degree turn over 0.5 s is followed to within 3 degrees 0.25 s after it ends
  const target = matToQuat(earthToWorld(screenRotation(90, 90, 0)));
  for (let i = 1; i <= 45; i++) {
    t += 16.7;
    f.update(matToQuat(earthToWorld(screenRotation(Math.min(90, i * 3), 90, 0))), t);
  }
  assert.ok(quatAngle(f.q, target) / D < 3, `lag ${(quatAngle(f.q, target) / D).toFixed(1)} deg`);
});

test('yaw alignment puts the path start direction where the phone faces', () => {
  const h = 1.1;                                     // phone heading in the world
  const th = alignYaw(h, 1, 0);                      // path leaves along model +X
  const [x, z] = modelToWorld(1, 0, th, 1);
  assert.ok(near(Math.atan2(x, z), h));
  assert.ok(near(Math.hypot(...modelToWorld(3, 4, 0.7, 2)), 10));     // rotation keeps lengths, mpu scales them
  assert.ok(near(wrap(3 * Math.PI), Math.PI) || near(wrap(3 * Math.PI), -Math.PI));
});

test('camera field of view for a cover-fitted stream', () => {
  // 1280x720 landscape stream on a 390x844 portrait screen: the screen shows the full stream height across its
  // width... cover crops the long side, so the vertical FOV is the stream's short-side FOV or less
  const v = coverFov(390, 844, 720, 1280, 66);
  assert.ok(v > 50 && v <= 66.0001, `${v}`);
  assert.ok(near(coverFov(1280, 720, 1280, 720, 66), 2 * Math.atan(Math.tan(33 * D) * 720 / 1280) / D, 1e-9));
});

// an L-shaped path: 6 m north (model -Z), then 4 m east (model +X), mpu 1
const L = [[0, 0], [0, -6], [4, -6]];

test('turn-by-turn: straight, turn soon, arrive', () => {
  assert.ok(turnAt(L, 1) > 80);                      // north then east is a right turn
  assert.ok(turnAt([[0, 0], [0, -6], [-4, -6]], 1) < -80);
  let g = guidance(L, 0, 1);
  assert.equal(g.kind, 'straight');
  assert.equal(g.text, 'Walk straight · 6.0 m');
  assert.equal(g.nextTurn, 'right');
  g = guidance(L, 4.5, 1);
  assert.equal(g.kind, 'right');
  assert.equal(g.text, 'Turn right');
  g = guidance(L, 7, 1);
  assert.equal(g.kind, 'arrive');
  assert.ok(near(g.remaining, 3));
  assert.equal(guidance(L, 9.5, 1).kind, 'arrived');
  assert.equal(guidance(L, 2, 1, 2.5).text, 'Off path — head back');
  assert.equal(guidance([[0, 0], [0, -6], [0.5, -12]], 0, 1).kind, 'arrive');      // a slight bend is not a turn
});

test('dead reckoning: steps along the path advance it; noisy headings are pulled back onto it', () => {
  const t = new PathTracker(L, { mpu: 1, stride: 0.7 });
  const north = Math.PI;                             // atan2(dx, dz) of (0, -1)
  for (let i = 0; i < 5; i++) t.step(north + (i % 2 ? 0.25 : -0.25));    // +-14 degrees of heading noise
  assert.ok(Math.abs(t.along - 3.5) < 0.25, `along ${t.along}`);
  assert.ok(t.offPath < 0.3, `off ${t.offPath}`);
  // keep walking north past the corner: progress stops near the corner instead of running on
  for (let i = 0; i < 6; i++) t.step(north);
  assert.ok(t.along < 7.2, `along ${t.along}`);
  for (let i = 0; i < 8; i++) t.step(Math.PI / 2);   // turn east
  assert.equal(t.state().kind, 'arrived');
  // walking away from the path is reported
  const w = new PathTracker(L, { mpu: 1 });
  for (let i = 0; i < 6; i++) w.step(Math.PI / 2);   // east from the start, away from the first leg
  assert.ok(w.state().offPath, 'off path');
  // manual / simulated steps follow the path exactly
  const m = new PathTracker(L, { mpu: 0.5, stride: 0.7 });
  m.step(null);
  assert.ok(near(m.along, 1.4));
  m.placeAt(99);
  assert.ok(near(m.along, 10));
});

test('chevrons point along the path, haptics and motion input', () => {
  const c = chevronPlacements(L, 0.9);
  assert.ok(c.length >= 9 && c.length <= 11);
  assert.ok(near(c[0].heading, Math.PI) || near(c[0].heading, -Math.PI));       // first leg: north
  assert.ok(near(c[c.length - 1].heading, Math.PI / 2));                          // last leg: east
  assert.deepEqual(HAPTIC.turn, [40]);
  assert.deepEqual(HAPTIC.arrive, [60, 50, 100]);
  assert.ok(near(motionMagnitude({ acceleration: { x: 3, y: 4, z: 0 } }), 5));
  assert.ok(near(motionMagnitude({ acceleration: { x: null }, accelerationIncludingGravity: { x: 0, y: 11.81, z: 0 } }), 2));
  assert.equal(motionMagnitude({}), null);
});
