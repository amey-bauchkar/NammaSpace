// Release loading and integrity checks for the viewer (IF-10).
// Every file listed in IF-10 files[] is checked for byte length and SHA-256
// before use; IF-03 assets are also checked against IF-03 sha256. Any failure
// raises a ReleaseLoadError with a user-readable message (no silent fallback).
import { sha256Hex } from './hash.js';

export class ReleaseLoadError extends Error {
  constructor(kind, message, path = null) {
    super(message);
    this.kind = kind;
    this.path = path;
  }
}

const dirname = (p) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/') + 1) : '');

async function fetchBytes(url, path, fetchFn) {
  let resp;
  try {
    resp = await fetchFn(url);
  } catch (e) {
    throw new ReleaseLoadError('network', `Could not fetch ${path}: ${e.message}`, path);
  }
  if (resp.status === 404) throw new ReleaseLoadError('missing_file', `Missing file: ${path} (HTTP 404)`, path);
  if (!resp.ok) throw new ReleaseLoadError('http_error', `Could not load ${path}: HTTP ${resp.status}`, path);
  return resp.arrayBuffer();
}

function parseJson(buf, path) {
  try {
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(buf));
  } catch (e) {
    throw new ReleaseLoadError('invalid_json', `Invalid JSON in ${path}: ${e.message}`, path);
  }
}

function expectContract(doc, id, path) {
  if (!doc || doc.contract !== id || !/^1\.\d+\.\d+$/.test(String(doc.contract_version))) {
    throw new ReleaseLoadError('contract_mismatch',
      `${path} is not a supported ${id} artifact (contract ${JSON.stringify(doc && doc.contract)}, version ${JSON.stringify(doc && doc.contract_version)})`, path);
  }
}

export function checkManifest(m, path = 'viewer_manifest.json') {
  expectContract(m, 'IF-10', path);
  for (const k of ['release_id', 'site_frame', 'scene', 'navlayers', 'pois', 'routes_precomputed']) {
    if (!(k in m)) throw new ReleaseLoadError('invalid_manifest', `${path} lacks required field "${k}"`, path);
  }
  const f = m.site_frame;
  if (f.units !== 'm' || f.handedness !== 'right' || f.up_axis !== '+Y') {
    throw new ReleaseLoadError('invalid_manifest', `${path}: site frame must be metres, right-handed, +Y up`, path);
  }
  const n = m.routes_precomputed.length;
  for (const k of ['route_requests', 'route_reports']) {
    if (k in m && m[k].length !== n) throw new ReleaseLoadError('invalid_manifest', `${path}: ${k} length differs from routes_precomputed`, path);
  }
}

export async function loadRelease(manifestUrl, fetchFn = (u) => fetch(u, { cache: 'no-store' })) {
  const base = new URL('.', manifestUrl);
  const label = new URL(manifestUrl).pathname.split('/').pop() || 'viewer manifest';
  const manifest = parseJson(await fetchBytes(manifestUrl, label, fetchFn), label);
  checkManifest(manifest, label);
  const index = new Map((manifest.files || []).map((f) => [f.path, f]));

  async function getVerified(rel, expectedSha = null) {
    const buf = await fetchBytes(new URL(rel, base).href, rel, fetchFn);
    const listed = index.get(rel);
    const sha = (listed || expectedSha) ? await sha256Hex(buf) : null;
    if (listed && listed.bytes !== buf.byteLength) {
      throw new ReleaseLoadError('size_mismatch', `${rel}: ${buf.byteLength} bytes, manifest says ${listed.bytes}`, rel);
    }
    if (listed && listed.sha256 !== sha) throw new ReleaseLoadError('hash_mismatch', `${rel}: SHA-256 does not match the manifest`, rel);
    if (expectedSha && expectedSha !== sha) throw new ReleaseLoadError('hash_mismatch', `${rel}: SHA-256 does not match IF-03`, rel);
    return buf;
  }
  const getJson = async (rel) => parseJson(await getVerified(rel), rel);

  const scene = await getJson(manifest.scene);
  expectContract(scene, 'IF-03', manifest.scene);
  const assets = [];
  for (const a of scene.assets) {
    const rel = dirname(manifest.scene) + a.path;
    assets.push({ asset: a, path: rel, bytes: await getVerified(rel, a.sha256) });
  }
  const geometry = manifest.geometry ? await getJson(manifest.geometry) : null;
  if (geometry) expectContract(geometry, 'IF-04', manifest.geometry);
  const navlayers = [];
  for (const rel of manifest.navlayers) {
    const nav = await getJson(rel);
    expectContract(nav, 'IF-05', rel);
    navlayers.push({ navlayer_id: nav.navlayer_id, geometry_id: nav.geometry_id, floor_id: nav.floor_id,
      resolution_m: nav.resolution_m, agent: nav.agent });
  }
  if (geometry && navlayers.length && navlayers[0].geometry_id !== geometry.geometry_id) {
    throw new ReleaseLoadError('invalid_manifest',
      `navigation layer ${navlayers[0].navlayer_id} was built from ${navlayers[0].geometry_id}, not the release geometry ${geometry.geometry_id}`);
  }
  const pois = await getJson(manifest.pois);
  if (!Array.isArray(pois)) throw new ReleaseLoadError('invalid_json', `${manifest.pois} must be an array of IF-09 records`, manifest.pois);
  pois.forEach((p, i) => expectContract(p, 'IF-09', `${manifest.pois}[${i}]`));

  const routes = [];
  for (let i = 0; i < manifest.routes_precomputed.length; i++) {
    const result = await getJson(manifest.routes_precomputed[i]);
    expectContract(result, 'IF-07', manifest.routes_precomputed[i]);
    const reqPath = manifest.route_requests ? manifest.route_requests[i] : null;
    const repPath = manifest.route_reports ? manifest.route_reports[i] : null;
    const request = reqPath ? await getJson(reqPath) : null;
    const report = repPath ? await getJson(repPath) : null;
    if (request) expectContract(request, 'IF-06', reqPath);
    if (report) expectContract(report, 'IF-08', repPath);
    routes.push({ request, result, report });
  }
  const gcPath = 'geometry/geometry_check.json';
  const geometryCheck = index.has(gcPath) ? await getJson(gcPath) : null;
  return { manifest, scene, assets, geometry, navlayers, pois, routes, geometryCheck, baseUrl: base.href };
}
