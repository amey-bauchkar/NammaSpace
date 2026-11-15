// In-browser walking-path planner for published (static) scene packages.
//
// The grid comes from the package (IF-13 navigation.route_grid, written by namma.studio.package): the SAME
// conservative navigation layer the Studio's Python planner uses - a cell is free only if it is fully on
// reconstructed floor and at least the agent radius away from every reconstructed obstacle (configuration space).
// So a path through free cells keeps a body's clearance without any further checks here.
//
// Same search rules as src/namma/navigation/planner.py: A* on an 8-connected grid, octile heuristic, a diagonal step
// only when both orthogonal neighbours are free (no corner cutting), the start is never moved silently, the goal is
// snapped to the nearest free cell within a stated limit. Results are always "unverified" in the viewer: the
// geometry is automatic and the independent vector validator runs only in the Studio.
//
// Path quality: every free cell already has body clearance, so the shortest path scrapes along furniture and cuts
// corners at exactly that clearance. A step therefore costs its length times (1 + penalty), where the penalty is
// highest next to a blocked cell and falls to zero `comfort` away from one. Paths keep to the middle of corridors
// and doorways and still take the short way through open floor. The reported length is always the real length.

const SQRT2 = Math.SQRT2;

class MinHeap {
  constructor() { this.k = []; this.v = []; }
  get size() { return this.k.length; }
  push(key, val) {
    const { k, v } = this;
    let i = k.length;
    k.push(key); v.push(val);
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (k[p] <= key) break;
      k[i] = k[p]; v[i] = v[p]; i = p;
    }
    k[i] = key; v[i] = val;
  }
  pop() {
    const { k, v } = this;
    const top = v[0], lastK = k.pop(), lastV = v.pop(), n = k.length;
    if (n) {
      let i = 0;
      for (;;) {
        let c = 2 * i + 1;
        if (c >= n) break;
        if (c + 1 < n && k[c + 1] < k[c]) c += 1;
        if (k[c] >= lastK) break;
        k[i] = k[c]; v[i] = v[c]; i = c;
      }
      k[i] = lastK; v[i] = lastV;
    }
    return top;
  }
}

const cellOf = (g, x, z) => {
  const c = Math.floor((x - g.ox) / g.cell), r = Math.floor((z - g.oz) / g.cell);
  return (r < 0 || c < 0 || r >= g.height || c >= g.width) ? -1 : r * g.width + c;
};
const centre = (g, i) => [g.ox + ((i % g.width) + 0.5) * g.cell, g.oz + (Math.floor(i / g.width) + 0.5) * g.cell];

/** Nearest free cell (index) to a point within maxDist (point to cell centre), or -1. */
export function nearestFree(g, x, z, maxDist) {
  const c0 = Math.floor((x - g.ox) / g.cell), r0 = Math.floor((z - g.oz) / g.cell), n = Math.ceil(maxDist / g.cell) + 1;
  let best = -1, bestD = Infinity;
  for (let r = Math.max(0, r0 - n); r <= Math.min(g.height - 1, r0 + n); r++) {
    for (let c = Math.max(0, c0 - n); c <= Math.min(g.width - 1, c0 + n); c++) {
      const i = r * g.width + c;
      if (!g.allowed[i]) continue;
      const d = Math.hypot(g.ox + (c + 0.5) * g.cell - x, g.oz + (r + 0.5) * g.cell - z);
      if (d <= maxDist && d < bestD) { bestD = d; best = i; }
    }
  }
  return best;
}

/** Distance from every free cell to the nearest blocked cell (or the grid edge), in cells: two-pass chamfer
 * transform with steps 1 and sqrt 2. A free cell touching a blocked one has distance 1. Cached on the grid. */
export function clearanceCells(g) {
  if (g.clearanceCells) return g.clearanceCells;
  const { width: W, height: H, allowed } = g;
  const d = new Float32Array(W * H);
  const at = (r, c) => (r < 0 || c < 0 || r >= H || c >= W ? 0 : d[r * W + c]);
  for (let r = 0; r < H; r++) {
    for (let c = 0; c < W; c++) {
      const i = r * W + c;
      d[i] = allowed[i] ? Math.min(at(r, c - 1) + 1, at(r - 1, c) + 1, at(r - 1, c - 1) + SQRT2, at(r - 1, c + 1) + SQRT2) : 0;
    }
  }
  for (let r = H - 1; r >= 0; r--) {
    for (let c = W - 1; c >= 0; c--) {
      const i = r * W + c;
      if (allowed[i]) d[i] = Math.min(d[i], at(r, c + 1) + 1, at(r + 1, c) + 1, at(r + 1, c + 1) + SQRT2, at(r + 1, c - 1) + SQRT2);
    }
  }
  g.clearanceCells = d;
  return d;
}

/** Per-cell cost penalty (>= 0): `weight` beside a blocked cell, falling quadratically to 0 at `comfortCells`. */
export function proximityPenalty(g, comfortCells, weight) {
  const d = clearanceCells(g), pen = new Float32Array(d.length);
  if (!(comfortCells > 0) || !(weight > 0)) return pen;
  for (let i = 0; i < d.length; i++) {
    const k = 1 - (d[i] - 1) / comfortCells;
    if (d[i] > 0 && k > 0) pen[i] = weight * k * k;
  }
  return pen;
}

/** A* over free cells; returns the cell-index path start..goal, or null. With `pen` (proximityPenalty) a step costs
 * its length times (1 + mean penalty of its two cells); the octile heuristic stays admissible (penalty >= 0). */
export function astar(g, start, goal, pen = null) {
  const { width: W, height: H, allowed } = g;
  if (!allowed[start] || !allowed[goal]) return null;
  const gr = Math.floor(goal / W), gc = goal % W;
  const h = (i) => {
    const dy = Math.abs(Math.floor(i / W) - gr), dx = Math.abs((i % W) - gc);
    return dx + dy + (SQRT2 - 2) * Math.min(dx, dy);
  };
  const cost = new Float32Array(W * H).fill(Infinity);
  const came = new Int32Array(W * H).fill(-1);
  const closed = new Uint8Array(W * H);
  const open = new MinHeap();
  cost[start] = 0;
  open.push(h(start), start);
  while (open.size) {
    const cur = open.pop();
    if (cur === goal) {
      const path = [cur];
      for (let i = cur; came[i] >= 0; i = came[i]) path.push(came[i]);
      return path.reverse();
    }
    if (closed[cur]) continue;
    closed[cur] = 1;
    const r = Math.floor(cur / W), c = cur % W;
    for (let dr = -1; dr <= 1; dr++) {
      for (let dc = -1; dc <= 1; dc++) {
        if (!dr && !dc) continue;
        const nr = r + dr, nc = c + dc;
        if (nr < 0 || nc < 0 || nr >= H || nc >= W) continue;
        const nb = nr * W + nc;
        if (!allowed[nb] || closed[nb]) continue;
        if (dr && dc && !(allowed[nr * W + c] && allowed[r * W + nc])) continue;      // no corner cutting
        const ng = cost[cur] + (dr && dc ? SQRT2 : 1) * (pen ? 1 + 0.5 * (pen[cur] + pen[nb]) : 1);
        if (ng < cost[nb]) { cost[nb] = ng; came[nb] = cur; open.push(ng + h(nb), nb); }
      }
    }
  }
  return null;
}

/** True if the straight segment between two cell centres crosses only free cells (every cell the segment touches,
 * sampled at a quarter cell, plus the orthogonal neighbours at each cell change so it cannot slip between corners). */
export function lineFree(g, a, b) {
  const [ax, az] = centre(g, a), [bx, bz] = centre(g, b);
  const n = Math.max(1, Math.ceil(Math.hypot(bx - ax, bz - az) / (g.cell * 0.25)));
  let prev = a;
  for (let k = 1; k <= n; k++) {
    const i = cellOf(g, ax + ((bx - ax) * k) / n, az + ((bz - az) * k) / n);
    if (i < 0 || !g.allowed[i]) return false;
    if (i !== prev) {
      const pr = Math.floor(prev / g.width), pc = prev % g.width, r = Math.floor(i / g.width), c = i % g.width;
      if (pr !== r && pc !== c && !(g.allowed[pr * g.width + c] && g.allowed[r * g.width + pc])) return false;
      prev = i;
    }
  }
  return true;
}

/** Penalised cost of the straight segment between two cell centres (length x (1 + penalty), sampled at half a cell). */
function segmentCost(g, a, b, pen) {
  const [ax, az] = centre(g, a), [bx, bz] = centre(g, b), len = Math.hypot(bx - ax, bz - az) / g.cell;
  const n = Math.max(1, Math.ceil(len * 2));
  let s = 0;
  for (let k = 0; k < n; k++) {
    const i = cellOf(g, ax + ((bx - ax) * (k + 0.5)) / n, az + ((bz - az) * (k + 0.5)) / n);
    s += 1 + (i < 0 ? 0 : pen[i]);
  }
  return (s / n) * len;
}

const LOOKAHEAD = 160;      // cells a single straight segment may replace (bounds the work on long paths)
const SLACK = 1.02;         // a shortcut may cost this much more than the cells it replaces (removes grid zig-zag)

/** Fewest waypoints with free straight segments between them (greedy string pulling over the A* cells). With `pen`
 * a shortcut is taken only if it is no costlier than the cells it replaces, so smoothing cannot pull a centred
 * path back against the furniture. */
export function smooth(g, path, pen = null) {
  if (path.length <= 2) return path.slice();
  let prefix = null;
  if (pen) {
    prefix = new Float64Array(path.length);
    for (let k = 1; k < path.length; k++) {
      const a = path[k - 1], b = path[k], diag = (a % g.width) !== (b % g.width) && Math.floor(a / g.width) !== Math.floor(b / g.width);
      prefix[k] = prefix[k - 1] + (diag ? SQRT2 : 1) * (1 + 0.5 * (pen[a] + pen[b]));
    }
  }
  const out = [path[0]];
  let i = 0;
  while (i < path.length - 1) {
    let j = pen ? Math.min(path.length - 1, i + LOOKAHEAD) : path.length - 1;
    while (j > i + 1 && !(lineFree(g, path[i], path[j])
      && (!pen || segmentCost(g, path[i], path[j], pen) <= SLACK * (prefix[j] - prefix[i])))) j -= 1;
    out.push(path[j]);
    i = j;
  }
  return out;
}

/** Smallest distance (model units) from the polyline to a blocked cell, measured between cell centres. */
export function minClearance(g, pts) {
  const d = clearanceCells(g);
  let best = Infinity;
  for (let k = 1; k < pts.length; k++) {
    const n = Math.max(1, Math.ceil(Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]) / (g.cell * 0.5)));
    for (let s = 0; s <= n; s++) {
      const i = cellOf(g, pts[k - 1][0] + ((pts[k][0] - pts[k - 1][0]) * s) / n, pts[k - 1][1] + ((pts[k][1] - pts[k - 1][1]) * s) / n);
      best = Math.min(best, i < 0 ? 0 : d[i]);
    }
  }
  return best * g.cell;
}

export const polylineLength = (pts) => pts.slice(1).reduce((s, p, k) => s + Math.hypot(p[0] - pts[k][0], p[1] - pts[k][1]), 0);

/**
 * Plan a walking path on the package's route grid (model units).
 * status: 'route' | 'start_blocked' | 'goal_not_snappable' | 'unreachable'
 * opts.maxGoalSnap / opts.maxStartMove are distances in model units.
 * opts.comfort (model units, default 6 cells) is the distance from obstacles beyond which a path is not penalised;
 * opts.clearanceWeight (default 3) is how strongly cells nearer than that are avoided; 0 gives the plain shortest path.
 */
export function planRoute(g, startXZ, goalXZ, { maxGoalSnap, maxStartMove = 0, comfort, clearanceWeight = 3 } = {}) {
  let start = cellOf(g, startXZ[0], startXZ[1]);
  let startMoved = 0;
  if (start < 0 || !g.allowed[start]) {
    // never moved silently: only within maxStartMove, and the distance is reported
    const s = maxStartMove > 0 ? nearestFree(g, startXZ[0], startXZ[1], maxStartMove) : -1;
    if (s < 0) return { status: 'start_blocked', polyline: [], length: 0, startMoved: 0 };
    const [sx, sz] = centre(g, s);
    startMoved = Math.hypot(sx - startXZ[0], sz - startXZ[1]);
    start = s;
  }
  let goal = cellOf(g, goalXZ[0], goalXZ[1]);
  let goalSnap = 0;
  if (goal < 0 || !g.allowed[goal]) {
    goal = nearestFree(g, goalXZ[0], goalXZ[1], maxGoalSnap);
    if (goal < 0) return { status: 'goal_not_snappable', polyline: [], length: 0, startMoved };
    const [gx, gz] = centre(g, goal);
    goalSnap = Math.hypot(gx - goalXZ[0], gz - goalXZ[1]);
  }
  const pen = clearanceWeight > 0 ? proximityPenalty(g, comfort > 0 ? comfort / g.cell : 6, clearanceWeight) : null;
  const cells = astar(g, start, goal, pen);
  if (!cells) return { status: 'unreachable', polyline: [], length: 0, startMoved, goalSnap };
  const polyline = smooth(g, cells, pen).map((i) => centre(g, i));
  return { status: 'route', polyline, length: polylineLength(polyline), startMoved, goalSnap, cells: cells.length,
    minClearance: minClearance(g, polyline) };
}

/** Point at distance d along a polyline, and the remaining length from there. */
export function pointAlong(pts, d) {
  let left = Math.max(0, d);
  for (let k = 1; k < pts.length; k++) {
    const seg = Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]);
    if (left <= seg || k === pts.length - 1) {
      const t = seg > 0 ? Math.min(1, left / seg) : 1;
      return { point: [pts[k - 1][0] + (pts[k][0] - pts[k - 1][0]) * t, pts[k - 1][1] + (pts[k][1] - pts[k - 1][1]) * t],
        segment: k - 1, heading: [pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]] };
    }
    left -= seg;
  }
  return { point: pts[pts.length - 1], segment: Math.max(0, pts.length - 2), heading: [0, 0] };
}

/** Distance still to walk from the point on the polyline nearest to (x, z). */
export function remainingFrom(pts, x, z) {
  let best = Infinity, rem = 0, acc = polylineLength(pts);
  let walked = 0;
  for (let k = 1; k < pts.length; k++) {
    const ax = pts[k - 1][0], az = pts[k - 1][1], dx = pts[k][0] - ax, dz = pts[k][1] - az, seg = Math.hypot(dx, dz);
    const t = seg > 0 ? Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / (seg * seg))) : 0;
    const d = Math.hypot(ax + dx * t - x, az + dz * t - z);
    if (d < best) { best = d; rem = acc - walked - seg * t; }
    walked += seg;
  }
  return { remaining: Math.max(0, rem), offPath: best };
}
