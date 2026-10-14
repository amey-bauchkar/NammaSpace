// Route requests in the static viewer (ARCH-025).
// The viewer builds an IF-06 request and resolves it against the PRECOMPUTED,
// packaged routes. It never plans a route itself. Unmatched requests are
// reported as unavailable, never approximated.

let counter = 0;

export function buildRouteRequest(navlayerId, startXz, poiId) {
  counter += 1;
  return {
    contract: 'IF-06', contract_version: '1.1.0', request_id: `client-${Date.now()}-${counter}`,
    navlayer_id: navlayerId, start_xz_m: [startXz[0], startXz[1]], goal: { poi_id: poiId }, max_goal_snap_m: 1.0,
  };
}

export function resolveRequest(request, routes) {
  const [sx, sz] = request.start_xz_m;
  const match = routes.find((r) => r.request && r.request.navlayer_id === request.navlayer_id
    && Math.hypot(r.request.start_xz_m[0] - sx, r.request.start_xz_m[1] - sz) <= 1e-6
    && r.request.goal && r.request.goal.poi_id === request.goal.poi_id);
  if (!match) {
    return {
      status: 'unavailable',
      reason: 'No precomputed route for this start and destination. Live route planning is not available in the static viewer (ARCH-025).',
    };
  }
  return { status: 'found', route: match };
}
