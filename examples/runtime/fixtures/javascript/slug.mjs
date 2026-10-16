export function slugify(title) {
  return title.trim().toLowerCase().replace(/\s+/g, '-').replace(/_/g, '-');
}
