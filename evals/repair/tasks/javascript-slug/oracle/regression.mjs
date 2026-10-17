// 固定原有行为，不加载候选内的测试文件。
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
const { slugify } = await import(pathToFileURL(resolve('slug.mjs')));
try {
  assert.equal(slugify('Hello World'), 'hello-world');
} catch (error) {
  if (error.code !== 'ERR_ASSERTION') throw error;
  console.log(JSON.stringify({ repair_check: 'failed' }));
  process.exit(1);
}
console.log(JSON.stringify({ repair_check: 'passed' }));
