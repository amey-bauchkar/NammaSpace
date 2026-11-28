import test from 'node:test';
import assert from 'node:assert/strict';
import { applySplatBudget } from '../js/adaptiveSplatBudget.js';

test('shared frame-budget update reduces load without changing navigation or hidden splats', () => {
  const app = { splatBudget: { min: 250000, max: 1000000, budget: 1000000 },
    spark: { lodSplatCount: 1000000 }, splat: { visible: true }, camera: { position: [1,2,3] } };
  const originalCamera = JSON.stringify(app.camera);
  for (let frame = 0; frame < 120; frame++) applySplatBudget(app, 40);
  assert.equal(app.spark.lodSplatCount, 850000);
  assert.equal(JSON.stringify(app.camera), originalCamera);
  app.splat.visible = false;
  const frozen = JSON.stringify(app.splatBudget);
  for (let frame = 0; frame < 120; frame++) applySplatBudget(app, 40);
  assert.equal(JSON.stringify(app.splatBudget), frozen);
  applySplatBudget({}, 40);
});
