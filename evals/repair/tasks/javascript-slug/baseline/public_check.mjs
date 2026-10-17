// 公开例子不覆盖连续多组分隔符，独立验收覆盖边界。
import assert from 'node:assert/strict';
import { slugify } from './slug.mjs';
assert.equal(slugify('Hello World'), 'hello-world');
