// IF-13 scene package loading with integrity checks and byte-level progress.
// Every file listed in files[] is checked for byte length and SHA-256 before use. Any failure
// raises a PackageLoadError with a user-readable message (nothing unverified is displayed).
import { sha256Hex } from './hash.js';

export class PackageLoadError extends Error {
  constructor(kind, message, path = null) {
    super(message);
    this.kind = kind;
    this.path = path;
  }
}

// device_tracking (IF-13 1.2.0): metric units from the capture device's tracking (imported phone-scan meshes)
const SCALE_STATES = ['unknown', 'user_distance', 'markers', 'reference_alignment', 'device_tracking'];

/** Structural checks the viewer relies on (the full schema is validated when the package is built). */
export function checkScenePackage(p, label = 'scene_package.json') {
  if (!p || p.contract !== 'IF-13' || !/^1\.\d+\.\d+$/.test(String(p.contract_version))) {
    throw new PackageLoadError('contract_mismatch', `${label} is not a supported IF-13 scene package `
      + `(contract ${JSON.stringify(p && p.contract)}, version ${JSON.stringify(p && p.contract_version)})`);
  }
  for (const k of ['model_frame', 'scale', 'appearance', 'eye_height_units', 'start_view', 'files', 'roam', 'cameras', 'pois']) {
    if (!(k in p)) throw new PackageLoadError('invalid_package', `${label} lacks required field "${k}"`);
  }
  const f = p.model_frame;
  if (f.units !== 'model' || f.handedness !== 'right' || f.up_axis !== '+Y') {
    throw new PackageLoadError('invalid_package', `${label}: model frame must be right-handed, +Y up, model units`);
  }
  if (!SCALE_STATES.includes(p.scale.status)) throw new PackageLoadError('invalid_package', `${label}: unknown scale status ${p.scale.status}`);
  if (p.scale.status === 'unknown' && p.scale.metres_per_unit !== null) {
    throw new PackageLoadError('invalid_package', `${label}: scale is unknown but metres_per_unit is set`);
  }
  if (!(p.eye_height_units > 0)) throw new PackageLoadError('invalid_package', `${label}: eye height must be positive`);
  const m = p.appearance.to_model.matrix_row_major;
  if (!Array.isArray(m) || m.length !== 16 || !m.every(Number.isFinite)) {
    throw new PackageLoadError('invalid_package', `${label}: to_model must be 16 finite numbers`);
  }
  if (!p.appearance.lods.length) throw new PackageLoadError('invalid_package', `${label}: no appearance levels`);
  const listed = new Set(p.files.map((x) => x.path));
  const est = p.appearance.estimated_surfaces;
  if (est && est.frame !== 'model') throw new PackageLoadError('invalid_package', `${label}: estimated surfaces must be in the model frame`);
  for (const rel of [...p.appearance.lods.map((l) => l.path), ...(est ? [est.path] : []), p.roam, p.cameras, p.pois]) {
    if (!listed.has(rel)) throw new PackageLoadError('invalid_package', `${label}: ${rel} is not listed in files[]`);
    if (rel.includes('..') || rel.startsWith('/') || rel.includes('\\')) throw new PackageLoadError('invalid_package', `${label}: unsafe path ${rel}`);
  }
}

/** Fetch with progress callbacks (bytes received, total) where the body is streamable. */
export async function fetchWithProgress(url, onProgress, fetchFn = (u) => fetch(u, { cache: 'no-store' })) {
  let resp;
  try {
    resp = await fetchFn(url);
  } catch (e) {
    throw new PackageLoadError('network', `Could not fetch ${url}: ${e.message}`);
  }
  if (resp.status === 404) throw new PackageLoadError('missing_file', `Missing file: ${url} (HTTP 404)`);
  if (!resp.ok) throw new PackageLoadError('http_error', `Could not load ${url}: HTTP ${resp.status}`);
  if (!resp.body || !resp.body.getReader || !onProgress) return resp.arrayBuffer();
  const total = Number(resp.headers.get('content-length')) || 0;
  const reader = resp.body.getReader();
  const chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    got += value.byteLength;
    onProgress(got, total);
  }
  const out = new Uint8Array(got);
  let o = 0;
  for (const c of chunks) { out.set(c, o); o += c.byteLength; }
  return out.buffer;
}

export async function loadScenePackage(url, { fetchFn, onProgress } = {}) {
  const base = new URL('.', url);
  const label = new URL(url).pathname.split('/').pop() || 'scene_package.json';
  const raw = await fetchWithProgress(url, null, fetchFn);
  let pkg;
  try {
    pkg = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw));
  } catch (e) {
    throw new PackageLoadError('invalid_json', `Invalid JSON in ${label}: ${e.message}`);
  }
  checkScenePackage(pkg, label);
  const index = new Map(pkg.files.map((f) => [f.path, f]));
  async function getVerified(rel, progress) {
    const listed = index.get(rel);
    const buf = await fetchWithProgress(new URL(rel, base).href, progress, fetchFn);
    if (buf.byteLength !== listed.bytes) {
      throw new PackageLoadError('size_mismatch', `${rel}: ${buf.byteLength} bytes, package says ${listed.bytes}`, rel);
    }
    if ((await sha256Hex(buf)) !== listed.sha256) throw new PackageLoadError('hash_mismatch', `${rel}: SHA-256 does not match the package`, rel);
    return buf;
  }
  const getJson = async (rel) => JSON.parse(new TextDecoder().decode(await getVerified(rel)));
  const [cameras, roam, pois] = await Promise.all([getJson(pkg.cameras), getJson(pkg.roam), getJson(pkg.pois)]);
  if (!Array.isArray(pois)) throw new PackageLoadError('invalid_package', `${pkg.pois} must be an array`);
  const quality = pkg.quality && pkg.quality.report && index.has(pkg.quality.report) ? await getJson(pkg.quality.report) : null;
  // lightest level first (highest lod number) for a fast first view
  const lods = [...pkg.appearance.lods].sort((a, b) => b.lod - a.lod);
  return { pkg, base: base.href, cameras: cameras.cameras || [], roam, pois, quality, lods,
    estimated: pkg.appearance.estimated_surfaces || null,
    fetchLod: (lod, progress) => getVerified(lod.path, progress || onProgress),
    fetchEstimated: () => getVerified(pkg.appearance.estimated_surfaces.path) };
}

/** Row-major 4x4 applied to a point. */
export function applyRowMajor(m, [x, y, z]) {
  return [m[0] * x + m[1] * y + m[2] * z + m[3], m[4] * x + m[5] * y + m[6] * z + m[7], m[8] * x + m[9] * y + m[10] * z + m[11]];
}

export function formatLength(units, scale) {
  if (scale && scale.metres_per_unit) return `${(units * scale.metres_per_unit).toFixed(2)} m`;
  return `${units.toFixed(2)} model units (scale unknown)`;
}
