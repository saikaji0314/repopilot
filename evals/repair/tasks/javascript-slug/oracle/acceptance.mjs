// 开发者可见、候选工作区之外的验收；不宣称对恶意代码保密。
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
const { slugify } = await import(pathToFileURL(resolve('slug.mjs')));
try {
  for (const [input, expected] of [
    ['one two three', 'one-two-three'],
    ['--Hello!! World??', 'hello-world'],
    ['  A___B / C  ', 'a-b-c'],
    ['', ''], ['!!!', ''], ['plain123', 'plain123'],
  ]) assert.equal(slugify(input), expected);
} catch (error) {
  if (error.code !== 'ERR_ASSERTION') throw error;
  console.log(JSON.stringify({ repair_check: 'failed' }));
  process.exit(1);
}
console.log(JSON.stringify({ repair_check: 'passed' }));
