// Valid-display gate for routes (ARCH-006, ARCH-021).
// Mirrors namma.contracts.binding.display_decision. A route is drawn as VALID
// only if a bound, passing IF-08 report that checked the accepted geometry
// exists. `display_as_valid` alone is never trusted.
import { routeSha256 } from './hash.js';

export async function displayDecision(route, report, acceptedGeometryId = null) {
  const reasons = [];
  if (!route || route.status !== 'route') {
    return { state: 'no_route', reasons: [`route status is ${JSON.stringify(route && route.status)}`] };
  }
  if (!report) return { state: 'unvalidated', reasons: ['no IF-08 report supplied'] };
  const link = route.validation || {};
  if (link.report_id !== report.report_id) reasons.push('IF-07 validation.report_id does not match the IF-08 report_id');
  if (report.request_id !== route.request_id) reasons.push('IF-08 request_id does not match IF-07 request_id');
  if (acceptedGeometryId !== null && report.geometry_id !== acceptedGeometryId) {
    reasons.push(`IF-08 checked geometry ${JSON.stringify(report.geometry_id)}, not the accepted geometry ${JSON.stringify(acceptedGeometryId)}`);
  }
  const evidence = report.evidence;
  if (!evidence) {
    return {
      state: reasons.length ? 'binding_mismatch' : 'unvalidated',
      reasons: [...reasons, 'IF-08 has no evidence.route_sha256 (1.0.0 report): cannot prove which route was checked'],
    };
  }
  if (evidence.route_sha256 !== (await routeSha256(route.polyline_xz_m || []))) {
    reasons.push('IF-08 evidence.route_sha256 does not match the IF-07 polyline');
  }
  if (reasons.length) return { state: 'binding_mismatch', reasons };
  if (report.verdict !== 'pass') return { state: 'rejected', reasons: ['independent validation verdict is fail'] };
  if (link.verdict !== 'pass' || route.display_as_valid !== true) {
    return { state: 'unvalidated', reasons: ['IF-07 does not record the passing verdict / display_as_valid'] };
  }
  return { state: 'valid', reasons: [] };
}
