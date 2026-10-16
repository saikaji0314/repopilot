import assert from 'node:assert/strict';
import test from 'node:test';
import { formatDuration } from './duration.mjs';

test('formatDuration keeps short durations readable', () => {
  assert.equal(formatDuration(0), '0s');
  assert.equal(formatDuration(45), '45s');
  assert.equal(formatDuration(90), '1m30s');
});

test('formatDuration matches the full project specification', () => {
  // 少于一天：按 h/m/s 组合，省略为零的部分。
  assert.equal(formatDuration(3600), '1h');
  assert.equal(formatDuration(3661), '1h1m1s');
  // 满一天及以上：按规格必须使用 d 单位，并保留余下的 h/m/s。
  assert.equal(formatDuration(86400), '1d');
  assert.equal(formatDuration(90061), '1d1h1m1s');
  // 非整数秒按规格向下取整。
  assert.equal(formatDuration(59.9), '59s');
});
