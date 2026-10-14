"""Token-level POI search (WP-06, ARCH-008). Reference implementation.

web/js/search.js implements the same rules; both are tested against the
hand-written cases in fixtures/search_cases.json (parity).

Normalisation: Unicode NFKC, lower-case; a token is a maximal run of
characters whose Unicode category is Letter, Mark or Number.
Tiers per name (display name or alias), best tier wins:
  4 exact     - normalised query == normalised name
  3 tokens    - every query token equals a name token
  2 prefix    - every query token equals or prefixes (>= 3 chars) a name token
  1 fuzzy     - as 2, or optimal-string-alignment distance <= 1 for query
                tokens of >= 5 characters against name tokens of >= 4
Results: the POIs sharing the best tier. One -> "unique"; several ->
"ambiguous" (disambiguation list); none -> "not_found"; blank -> "empty_query".
Rejected POIs are never returned; unverified POIs only with include_unverified.
"""
import unicodedata

PREFIX_MIN = 3
FUZZY_QUERY_MIN = 5
FUZZY_NAME_MIN = 4


def normalize(text):
    return unicodedata.normalize("NFKC", text).lower()


def tokens(text):
    out, cur = [], []
    for ch in normalize(text):
        if unicodedata.category(ch)[0] in "LMN":
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def osa_distance(a, b):
    """Optimal string alignment distance (Damerau-Levenshtein without repeated edits)."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def _token_tier(q, name_tokens):
    if q in name_tokens:
        return 3
    if len(q) >= PREFIX_MIN and any(t.startswith(q) for t in name_tokens):
        return 2
    if len(q) >= FUZZY_QUERY_MIN and any(len(t) >= FUZZY_NAME_MIN and osa_distance(q, t) <= 1 for t in name_tokens):
        return 1
    return 0


def name_tier(query, name):
    qt, nt = tokens(query), tokens(name)
    if not qt:
        return 0
    if qt == nt:
        return 4
    return min(_token_tier(q, nt) for q in qt)


def search(pois, query, include_unverified=False):
    qt = tokens(query or "")
    if not qt:
        return {"status": "empty_query", "query": query, "tier": 0, "hits": []}
    scored = []
    for p in pois:
        st = p["verification"]["status"]
        if st == "rejected" or (st != "verified" and not include_unverified):
            continue
        tier = max(name_tier(query, n) for n in [p["display_name"], *p.get("aliases", [])])
        if tier:
            scored.append((tier, p))
    if not scored:
        return {"status": "not_found", "query": query, "tier": 0, "hits": []}
    best = max(t for t, _ in scored)
    hits = sorted((p for t, p in scored if t == best), key=lambda p: (p["display_name"], p["poi_id"]))
    return {"status": "unique" if len(hits) == 1 else "ambiguous", "query": query, "tier": best,
            "hits": [{"poi_id": p["poi_id"], "display_name": p["display_name"], "room_poi_id": p.get("room_poi_id"),
                      "verified": p["verification"]["status"] == "verified",
                      "endpoint_reachable": p.get("endpoint_reachable")} for p in hits]}
