// Test helpers: project paths and a file-system backed fetch for built sites.
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
export const SITE = process.env.NAMMA_SITE_DIR || path.join(ROOT, 'dist');
export const HAVE_SITE = existsSync(path.join(SITE, 'data', 'viewer_manifest.json'));
export const SITE_URL = 'http://site.test/data/viewer_manifest.json';

export function readJson(...parts) {
  return JSON.parse(readFileSync(path.join(...parts), 'utf-8'));
}

/** fetch() over SITE; `overlay` maps a data-relative path to {status} or {body} to simulate faults. */
export function siteFetch(overlay = {}) {
  return async (url) => {
    const rel = new URL(url).pathname.replace(/^\/data\//, '');
    const o = overlay[rel];
    if (o && o.status) return new Response('', { status: o.status });
    if (o && o.body !== undefined) return new Response(o.body, { status: 200 });
    const p = path.join(SITE, 'data', rel);
    if (!existsSync(p)) return new Response('', { status: 404 });
    return new Response(readFileSync(p), { status: 200 });
  };
}

export function siteRoutes() {
  const m = readJson(SITE, 'data', 'viewer_manifest.json');
  return m.routes_precomputed.map((r, i) => ({
    result: readJson(SITE, 'data', r),
    request: readJson(SITE, 'data', m.route_requests[i]),
    report: m.route_reports[i] ? readJson(SITE, 'data', m.route_reports[i]) : null,
  }));
}
