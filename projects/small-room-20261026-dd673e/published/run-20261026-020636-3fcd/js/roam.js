// Roaming permission grid (IF-13 roam, model frame). Used ONLY to keep the first-person camera
// inside the reconstructed space and out of observed furniture / walls while exploring.
// It is never used for routes (routes need verified navigation geometry).
//
// Grid: row r spans z in [oz + r*cell, oz + (r+1)*cell), column c spans x likewise.
// rows[] are run lengths alternating not-allowed / allowed, starting with not-allowed.

export function decodeRoamGrid(grid) {
  const { width, height, rows } = grid;
  if (!Array.isArray(rows) || rows.length !== height) throw new Error(`roam grid has ${rows && rows.length} rows, expected ${height}`);
  const allowed = new Uint8Array(width * height);
  rows.forEach((runs, r) => {
    let c = 0, val = 0;
    for (const n of runs) {
      if (n < 0 || c + n > width) throw new Error(`roam grid row ${r} overflows its width`);
      if (val) allowed.fill(1, r * width + c, r * width + c + n);
      c += n;
      val ^= 1;
    }
    if (c !== width) throw new Error(`roam grid row ${r} covers ${c} cells, expected ${width}`);
  });
  return { ox: grid.origin_xz[0], oz: grid.origin_xz[1], cell: grid.cell, width, height, allowed };
}

export function cellAt(g, x, z) {
  const c = Math.floor((x - g.ox) / g.cell), r = Math.floor((z - g.oz) / g.cell);
  return (r < 0 || c < 0 || r >= g.height || c >= g.width) ? null : [r, c];
}

export function isAllowed(g, x, z) {
  const rc = cellAt(g, x, z);
  return !!rc && g.allowed[rc[0] * g.width + rc[1]] === 1;
}

/** True if a disc of `radius` centred at (x, z) touches only allowed cells (sampled at cell spacing). */
export function canOccupyGrid(g, x, z, radius) {
  if (!isAllowed(g, x, z)) return false;
  const steps = Math.max(1, Math.ceil(radius / g.cell));
  for (let i = -steps; i <= steps; i++) {
    for (let j = -steps; j <= steps; j++) {
      const dx = (i / steps) * radius, dz = (j / steps) * radius;
      if (dx * dx + dz * dz > radius * radius + 1e-12) continue;
      if (!isAllowed(g, x + dx, z + dz)) return false;
    }
  }
  return true;
}

/** Nearest allowed cell centre to (x, z) within maxDist (used to recover a camera stuck outside). */
export function nearestAllowed(g, x, z, maxDist) {
  const rc = cellAt(g, x, z);
  const r0 = rc ? rc[0] : Math.floor((z - g.oz) / g.cell), c0 = rc ? rc[1] : Math.floor((x - g.ox) / g.cell);
  const maxCells = Math.ceil(maxDist / g.cell);
  let best = null, bestD = Infinity;
  for (let dr = -maxCells; dr <= maxCells; dr++) {
    for (let dc = -maxCells; dc <= maxCells; dc++) {
      const r = r0 + dr, c = c0 + dc;
      if (r < 0 || c < 0 || r >= g.height || c >= g.width || !g.allowed[r * g.width + c]) continue;
      const cx = g.ox + (c + 0.5) * g.cell, cz = g.oz + (r + 0.5) * g.cell;
      const d = Math.hypot(cx - x, cz - z);
      if (d < bestD && d <= maxDist) { bestD = d; best = [cx, cz]; }
    }
  }
  return best;
}
