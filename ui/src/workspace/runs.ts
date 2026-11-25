import type { Artifact } from '../api';
import type { Layout } from './model';

/** The runs a layout's search, status and version filters match. */
export function matchingRuns(runs: Artifact[], layout: Layout) {
  const search = layout.search.toLowerCase();
  const eligible = runs.filter(
    (run) =>
      (!layout.status || layout.status === (run.finished ? 'finished' : 'live') || run.values._outcome === layout.status) &&
      `${run.name} ${run.id} ${JSON.stringify(run.tags)} ${run.aliases.join(' ')}`.toLowerCase().includes(search),
  );
  if (layout.versions === 'all') return eligible;
  const latest = new Map<string, Artifact>();
  for (const run of eligible) {
    const name = `${run.namespace}/${run.name}`;
    const previous = latest.get(name);
    const newer =
      !previous ||
      run.created_at > previous.created_at ||
      (run.created_at === previous.created_at && (run.version || Infinity) > (previous.version || Infinity));
    if (newer) latest.set(name, run);
  }
  return [...latest.values()];
}

/** The folder all run names share, e.g. "sweep/", which the run list leaves out. */
export function sharedPrefix(runs: Artifact[]) {
  if (!runs.length) return '';
  const parts = runs[0].name.split('/').slice(0, -1);
  while (parts.length && !runs.every((run) => run.name.startsWith(parts.join('/') + '/'))) parts.pop();
  return parts.length ? parts.join('/') + '/' : '';
}
