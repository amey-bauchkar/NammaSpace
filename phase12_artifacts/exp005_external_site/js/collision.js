// Plan-view collision for first-person roaming, from IF-04 (site frame [x, z]).
// Used only to keep the camera out of walls while walking; it is NOT a route
// validator (routes are validated offline by namma.navigation.validator).

const BODY_BAND = [0.05, 1.9];

export function collisionModel(geometry, floorId = null) {
  const floor = floorId ? geometry.floors.find((f) => f.floor_id === floorId) : geometry.floors[0];
  const elev = floor.elevation_m;
  const blocking = geometry.obstacles
    .filter((o) => o.floor_id === floor.floor_id && o.max_y_m > elev + BODY_BAND[0] && o.min_y_m < elev + BODY_BAND[1])
    .map((o) => ({ id: o.obstacle_id, ring: o.footprint }));
  return { floorId: floor.floor_id, walkable: floor.walkable_regions, holes: floor.holes || [], blocking };
}

export function pointInRing(x, z, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, zi] = ring[i], [xj, zj] = ring[j];
    if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) inside = !inside;
  }
  return inside;
}

export function distToRing(x, z, ring) {
  let best = Infinity;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [ax, az] = ring[j], [bx, bz] = ring[i];
    const dx = bx - ax, dz = bz - az;
    const t = Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / (dx * dx + dz * dz || 1)));
    best = Math.min(best, Math.hypot(x - (ax + t * dx), z - (az + t * dz)));
  }
  return best;
}

/** True if a body of `radius` centred at (x, z) is on the floor and clear of blocking obstacles. */
export function canOccupy(model, x, z, radius) {
  const onFloor = model.walkable.some((r) => pointInRing(x, z, r)) && !model.holes.some((r) => pointInRing(x, z, r));
  if (!onFloor) return false;
  for (const o of model.blocking) {
    if (pointInRing(x, z, o.ring) || distToRing(x, z, o.ring) < radius) return false;
  }
  return true;
}
