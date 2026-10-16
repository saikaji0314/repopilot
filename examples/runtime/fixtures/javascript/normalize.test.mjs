import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeTag } from './normalize.mjs';

test('normalize tags consistently', () => {
  assert.equal(normalizeTag('  RepoPilot '), 'repopilot');
  assert.equal(normalizeTag('   '), '');
});
