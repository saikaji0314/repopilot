import assert from 'node:assert/strict';
import test from 'node:test';
import { slugify } from './slug.mjs';

test('slugify keeps simple titles readable', () => {
  assert.equal(slugify('Hello World'), 'hello-world');
});
