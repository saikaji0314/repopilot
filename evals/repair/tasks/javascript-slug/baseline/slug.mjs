// 将ASCII标题规范化为短横线分隔的小写slug。
export function slugify(text) {
  return text.trim().toLowerCase().replace(/[^a-z0-9]+/, '-');
}
