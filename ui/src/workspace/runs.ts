import type { ArtifactSummary } from '../api';
import type { Layout } from './model';

/** The runs a layout's search, status and version filters match. */
export function matchingRuns(runs: ArtifactSummary[], layout: Layout) {
  const search = layout.search.toLowerCase();
  const eligible = runs.filter(
    (run) =>
      (!layout.status || run.state === layout.status || run.outcome === layout.status) &&
      `${run.name} ${run.id} ${JSON.stringify(run.annotations)}`.toLowerCase().includes(search),
  );
  if (layout.versions === 'all') return eligible;
  const latest = new Map<string, ArtifactSummary>();
  for (const run of eligible) {
    const name = `${run.namespace}/${run.name}`;
    const previous = latest.get(name);
    const newer =
      !previous ||
      run.created_at > previous.created_at ||
      (run.created_at === previous.created_at && (run.number || Infinity) > (previous.number || Infinity));
    if (newer) latest.set(name, run);
  }
  return [...latest.values()];
}

/** The folder all run names share, e.g. "sweep/", which the run list leaves out. */
export function sharedPrefix(runs: ArtifactSummary[]) {
  if (!runs.length) return '';
  const parts = runs[0].name.split('/').slice(0, -1);
  while (parts.length && !runs.every((run) => run.name.startsWith(parts.join('/') + '/'))) parts.pop();
  return parts.length ? parts.join('/') + '/' : '';
}
