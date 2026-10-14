// SHA-256 helpers and the canonical route-polyline string (ARCH-021).
// Must stay identical to src/namma/contracts/binding.py (tested for parity).

const HEX = Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, '0'));

export async function sha256Hex(data) {
  const bytes = typeof data === 'string' ? new TextEncoder().encode(data) : new Uint8Array(data);
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (b) => HEX[b]).join('');
}

export function quantize(v) {
  return Number(Number(v).toFixed(6)) + 0; // "+ 0" turns -0 into 0
}

export function canonicalPolyline(polyline) {
  return polyline.map(([x, z]) => `${quantize(x).toFixed(6)},${quantize(z).toFixed(6)}`).join(';');
}

export async function routeSha256(polyline) {
  return sha256Hex(canonicalPolyline(polyline));
}
