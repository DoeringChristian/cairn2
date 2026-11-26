import { status, type Artifact, type Json } from '../api';
import { at, holds, type Conditions, type Layout } from './model';

/** A field of a run: its name, version or status, a setting, a tag, or the last value it logged. */
export function runValue(run: Artifact, field: string, configs: Record<string, Json>): Json | undefined {
  if (field === '@name') return run.name;
  if (field === '@version') return run.version || null;
  if (field === '@status') return status(run);
  if (field.startsWith('@config/')) return at(configs[run.id], field.slice(7));
  if (field.startsWith('@tags/')) return run.tags[field.slice(6)];
  return run.values[logged(field)];
}
/** The server's path of a field of records. */
export const logged = (field: string) => field.replace(/^\//, '');
/** Fields the conditions and grouping of a layout read. */
export function fieldsOf(layout: Layout) {
  const found = new Set(layout.groupRuns);
  const visit = (group: Conditions) =>
    group.items.forEach((item) => ('items' in item ? visit(item) : item.field && found.add(item.field)));
  if (layout.conditions) visit(layout.conditions);
  return [...found];
}
/** A search is a pattern, or text where it is none. */
export function searching(search: string) {
  try {
    return new RegExp(search, 'i');
  } catch {
    return undefined;
  }
}
function meets(run: Artifact, group: Conditions, configs: Record<string, Json>): boolean {
  const met = (item: Conditions['items'][number]) =>
    'items' in item ? meets(run, item, configs) : !item.field || holds(runValue(run, item.field, configs), item);
  return group.mode === 'all' ? group.items.every(met) : !group.items.length || group.items.some(met);
}

/** The runs a layout's search, status, conditions and version filters match. */
export function matchingRuns(runs: Artifact[], layout: Layout, configs: Record<string, Json> = {}) {
  const pattern = searching(layout.search);
  const eligible = runs.filter((run) => {
    const text = `${run.name} ${run.id} ${status(run)} ${JSON.stringify(run.tags)} ${run.aliases.join(' ')}`;
    return (
      (!layout.status ||
        layout.status === (run.finished ? 'finished' : 'live') ||
        run.values._outcome === layout.status) &&
      (pattern ? pattern.test(text) : text.toLowerCase().includes(layout.search.toLowerCase())) &&
      (!layout.conditions || meets(run, layout.conditions, configs))
    );
  });
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
