import test from 'node:test';
import assert from 'node:assert/strict';
import { updateSplatBudget } from '../js/adaptiveSplatBudget.js';

test('adaptive budget responds gradually within limits and ignores invalid timing', () => {
  let state = {min:250000, max:1500000, budget:1000000};
  assert.equal(updateSplatBudget(state, NaN), state);
  for(let i=0;i<120;i++) state = updateSplatBudget(state, 35);
  assert.equal(state.budget, 850000);
  for(let i=0;i<2400;i++) state = updateSplatBudget(state, 15);
  assert.ok(state.budget <= state.max && state.budget > 850000);
});
