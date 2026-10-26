// Site-frame helpers (ARCH-002, ARCH-020). Site frame: right-handed, metres, +Y up.
// three.js uses the same convention, so site coordinates are world coordinates.

/** Apply a row-major 4x4 matrix (IF-03 to_site) to a point [x, y, z]. */
export function applyRowMajor(m, [x, y, z]) {
  return [
    m[0] * x + m[1] * y + m[2] * z + m[3],
    m[4] * x + m[5] * y + m[6] * z + m[7],
    m[8] * x + m[9] * y + m[10] * z + m[11],
  ];
}

/** Camera yaw / pitch (three.js 'YXZ' order) that looks from `pos` to `target`. */
export function lookAngles(pos, target) {
  const d = [target[0] - pos[0], target[1] - pos[1], target[2] - pos[2]];
  const n = Math.hypot(...d);
  if (n === 0) return { yaw: 0, pitch: 0 };
  return { yaw: Math.atan2(-d[0], -d[2]), pitch: Math.asin(d[1] / n) };
}

/** Unit forward vector for yaw / pitch (camera looks down -Z at yaw = pitch = 0). */
export function forwardFromAngles(yaw, pitch) {
  return [-Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), -Math.cos(yaw) * Math.cos(pitch)];
}

export function angleBetween(a, b) {
  const dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const n = Math.hypot(...a) * Math.hypot(...b);
  return Math.acos(Math.min(1, Math.max(-1, dot / n)));
}
