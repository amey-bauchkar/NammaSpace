// Token-level POI search. Port of src/namma/poi/search.py (the reference);
// parity is tested against fixtures/search_cases.json.

const PREFIX_MIN = 3;
const FUZZY_QUERY_MIN = 5;
const FUZZY_NAME_MIN = 4;
const TOKEN_CHAR = /[\p{L}\p{M}\p{N}]/u;

export function normalize(text) {
  return text.normalize('NFKC').toLowerCase();
}

export function tokens(text) {
  const out = [];
  let cur = '';
  for (const ch of normalize(text)) {
    if (TOKEN_CHAR.test(ch)) cur += ch;
    else if (cur) { out.push(cur); cur = ''; }
  }
  if (cur) out.push(cur);
  return out;
}

export function osaDistance(a, b) {
  const A = Array.from(a), B = Array.from(b);
  const d = Array.from({ length: A.length + 1 }, () => new Array(B.length + 1).fill(0));
  for (let i = 0; i <= A.length; i++) d[i][0] = i;
  for (let j = 0; j <= B.length; j++) d[0][j] = j;
  for (let i = 1; i <= A.length; i++) {
    for (let j = 1; j <= B.length; j++) {
      const cost = A[i - 1] === B[j - 1] ? 0 : 1;
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
      if (i > 1 && j > 1 && A[i - 1] === B[j - 2] && A[i - 2] === B[j - 1]) {
        d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
      }
    }
  }
  return d[A.length][B.length];
}

const len = (s) => Array.from(s).length;

function tokenTier(q, nameTokens) {
  if (nameTokens.includes(q)) return 3;
  if (len(q) >= PREFIX_MIN && nameTokens.some((t) => t.startsWith(q))) return 2;
  if (len(q) >= FUZZY_QUERY_MIN && nameTokens.some((t) => len(t) >= FUZZY_NAME_MIN && osaDistance(q, t) <= 1)) return 1;
  return 0;
}

export function nameTier(query, name) {
  const qt = tokens(query), nt = tokens(name);
  if (!qt.length) return 0;
  if (qt.length === nt.length && qt.every((t, i) => t === nt[i])) return 4;
  return Math.min(...qt.map((q) => tokenTier(q, nt)));
}

export function search(pois, query, includeUnverified = false) {
  if (!tokens(query || '').length) return { status: 'empty_query', query, tier: 0, hits: [] };
  const scored = [];
  for (const p of pois) {
    const st = p.verification.status;
    if (st === 'rejected' || (st !== 'verified' && !includeUnverified)) continue;
    const tier = Math.max(...[p.display_name, ...(p.aliases || [])].map((n) => nameTier(query, n)));
    if (tier) scored.push([tier, p]);
  }
  if (!scored.length) return { status: 'not_found', query, tier: 0, hits: [] };
  const best = Math.max(...scored.map(([t]) => t));
  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  const hits = scored.filter(([t]) => t === best).map(([, p]) => p)
    .sort((a, b) => cmp(a.display_name, b.display_name) || cmp(a.poi_id, b.poi_id));
  return {
    status: hits.length === 1 ? 'unique' : 'ambiguous', query, tier: best,
    hits: hits.map((p) => ({
      poi_id: p.poi_id, display_name: p.display_name, room_poi_id: p.room_poi_id ?? null,
      verified: p.verification.status === 'verified', endpoint_reachable: p.endpoint_reachable ?? null,
    })),
  };
}
