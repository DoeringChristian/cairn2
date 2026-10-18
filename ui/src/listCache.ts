import type { ArtifactSummary } from './api';

export type ListPage = {items: ArtifactSummary[]; total: number};
const key = 'cairn:list-pages:v1';
const scopeKey = 'cairn:list-repository';
const maxPages = 40, maxBytes = 2 * 1024 * 1024;
const pages = new Map<string, ListPage>();
try {
  const saved = sessionStorage.getItem(key);
  if (saved && saved.length * 2 <= maxBytes) {
    const entries: unknown = JSON.parse(saved);
    if (Array.isArray(entries)) for (const entry of entries.slice(-maxPages)) {
      if (Array.isArray(entry) && typeof entry[0] === 'string' && Array.isArray(entry[1]?.items) && typeof entry[1]?.total === 'number') pages.set(entry[0], entry[1]);
    }
  }
} catch { /* Storage may be unavailable; the in-memory cache still works. */ }
function persist() {
  while (pages.size > maxPages) pages.delete(pages.keys().next().value!);
  let encoded = JSON.stringify([...pages]);
  while (encoded.length * 2 > maxBytes && pages.size) {
    pages.delete(pages.keys().next().value!);
    encoded = JSON.stringify([...pages]);
  }
  try {sessionStorage.setItem(key, encoded);} catch { /* Quota/private browsing. */ }
}
export function cachedList(path: string) {return pages.get(path);}
export function cacheList(path: string, page: ListPage) {
  pages.delete(path); pages.set(path, page); persist();
}
export function invalidateLists() {pages.clear();persist();}
export function invalidateList(path: string) {pages.delete(path);persist();}
export function scopeListCache(repository: string) {
  try {
    if (sessionStorage.getItem(scopeKey) !== repository) invalidateLists();
    sessionStorage.setItem(scopeKey, repository);
  } catch {invalidateLists();}
}
