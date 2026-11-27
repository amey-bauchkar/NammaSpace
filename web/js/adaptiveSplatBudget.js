/** Opt-in renderer budget controller. Time samples include CPU and scheduling overhead. */
export function updateSplatBudget(state, frameMs) {
  if (!Number.isFinite(frameMs) || frameMs <= 0 || frameMs > 250) return state;
  const mean = state.mean == null ? frameMs : state.mean * 0.9 + frameMs * 0.1;
  const frames = (state.frames || 0) + 1;
  let budget = state.budget;
  if (frames % 120 === 0) {
    if (mean > 28) budget = Math.max(state.min, Math.floor(budget * 0.85));
    else if (mean < 19) budget = Math.min(state.max, Math.floor(budget * 1.05));
  }
  return { ...state, mean, frames, budget };
}
