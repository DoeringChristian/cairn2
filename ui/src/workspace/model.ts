import { envelope, type Artifact, type Json, type RecordEntry, type Run } from '../api';
import type { Kind } from './cards';

export type Filter = {
  field: string;
  op: 'eq' | 'ne' | 'lt' | 'le' | 'gt' | 'ge' | 'contains' | 'exists';
  value: string;
};
/** Conditions on runs, nested in groups of which all or any hold. */
export type Conditions = { mode: 'all' | 'any'; items: (Filter | Conditions)[] };
export type Smoothing = { kind: 'ema' | 'twema' | 'gaussian' | 'window'; value: number; original: boolean };
export type Order = { field: string; descending: boolean };
export type Aggregation = {
  center: 'none' | 'mean' | 'median' | 'min' | 'max';
  band: 'none' | 'minmax' | 'std' | 'sem';
  showMembers: boolean;
};
export type Card = {
  aggregation?: Aggregation;
  smoothing?: Smoothing;
  id: string;
  title: string;
  kind: Kind;
  section: string;
  width: number;
  height: number;
  filters: Filter[];
  filterMode: 'all' | 'any';
  x: string;
  y: string;
  value: string;
  split: string[];
  pointOrder: Order;
  lineOrder: 'label' | 'minimum' | 'maximum';
  marks: 'lines' | 'markers' | 'lines+markers';
  logX: boolean;
  logY: boolean;
  mode: 'gallery' | 'compare' | 'sequence';
  axis: string;
  matching: 'exact' | 'previous' | 'nearest';
  columns: number;
  pageSize: number;
  caption: string;
  fit: 'contain' | 'cover';
  linkAxes: boolean;
};
export type Section = { id: string; title: string; collapsed: boolean };
export type Layout = {
  version: 1;
  id: string;
  name: string;
  search: string;
  versions: 'latest' | 'all';
  status: string;
  selected: string[] | null;
  /** Conditions on the runs' config, tags and values. */
  conditions?: Conditions;
  /** Fields the run list is grouped by. */
  groupRuns?: string[];
  sections: Section[];
  cards: Card[];
  /** Cards in their sections, or every card once per run. */
  view?: 'together' | 'runs';
};
export type Observation = RecordEntry & { created: number; run: Run };
export type FieldInfo = { name: string; kinds: Set<string> };
export const palette = ['#176b5b', '#b56231', '#637fa1', '#a58a49', '#8a709b'];
export const uid = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
export const label = (run: Artifact) => `${run.name} · ${run.version ? 'v' + run.version : 'live'}`;
/** Bare field names are literal record keys; nested fields use JSON pointers. */
export function valueAt(row: Observation, field: string): Json | undefined {
  if (field === '@run') return row.run.id;
  if (field === '@name') return row.run.name;
  if (field === '@version') return row.run.version || null;
  if (field === '@ordinal') return row.ordinal;
  if (field === '@created') return row.created;
  const root = field.startsWith('@config/') ? row.run.config : row.data;
  const path = field.startsWith('@config/') ? field.slice(7) : field;
  if (!path.startsWith('/')) return row.data[path];
  return at(root, path);
}
/** The value at a JSON pointer. */
export function at(root: Json | undefined, path: string) {
  return path
    .slice(1)
    .split('/')
    .reduce<Json | undefined>(
      (v, key) =>
        v && typeof v === 'object' && !Array.isArray(v) ? v[key.replace(/~1/g, '/').replace(/~0/g, '~')] : undefined,
      root,
    );
}
export function compare(a: Json | undefined, b: Json | undefined) {
  if (a === b || (a == null && b == null)) return 0;
  if (a === undefined || a === null) return 1;
  if (b === undefined || b === null) return -1;
  if (typeof a !== typeof b) return typeof a < typeof b ? -1 : 1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  if (typeof a === 'object' && typeof b === 'object') return JSON.stringify(a).localeCompare(JSON.stringify(b));
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}
export function sorted(rows: Observation[], order: Order) {
  const keyed = rows.map((row) => ({ row, value: valueAt(row, order.field) }));
  const before = (a: (typeof keyed)[number], b: (typeof keyed)[number]) => {
    const direction = a.value == null || b.value == null ? 1 : order.descending ? -1 : 1;
    const difference = compare(a.value, b.value) * direction;
    if (difference) return difference;
    if (a.row.run.id !== b.row.run.id) return a.row.run.id.localeCompare(b.row.run.id);
    return a.row.ordinal - b.row.ordinal;
  };
  // Runs mostly log in order.
  if (keyed.every((item, i) => !i || before(keyed[i - 1], item) <= 0)) return rows.slice();
  return keyed.sort(before).map((item) => item.row);
}
function same(a: Json, b: Json): boolean {
  if (a === b) return true;
  if (a === null || b === null || typeof a !== typeof b || typeof a !== 'object' || typeof b !== 'object') return false;
  if (Array.isArray(a) || Array.isArray(b))
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => same(v, b[i]));
  return Object.keys(a).length === Object.keys(b).length && Object.keys(a).every((k) => k in b && same(a[k], b[k]));
}
export const matches = (row: Observation, filter: Filter) => holds(valueAt(row, filter.field), filter);
/** Whether a value meets a filter. */
export function holds(value: Json | undefined, filter: Filter) {
  if (filter.op === 'exists') return value !== undefined && value !== null;
  if (value === undefined) return false;
  let expected: Json;
  try {
    expected = JSON.parse(filter.value);
  } catch {
    expected = filter.value;
  }
  if (filter.op === 'eq') return same(value, expected);
  if (filter.op === 'ne') return !same(value, expected);
  if (value === null || expected === null) return false;
  if (filter.op === 'contains')
    return Array.isArray(value)
      ? value.some((v) => same(v, expected))
      : typeof value === 'string' && value.includes(String(expected));
  if (typeof value !== typeof expected) return false;
  const c = compare(value, expected);
  return filter.op === 'lt' ? c < 0 : filter.op === 'le' ? c <= 0 : filter.op === 'gt' ? c > 0 : c >= 0;
}
export const filtered = (rows: Observation[], card: Card) =>
  rows.filter(
    (row) =>
      !card.filters.length ||
      (card.filterMode === 'all'
        ? card.filters.every((f) => matches(row, f))
        : card.filters.some((f) => matches(row, f))),
  );
export function groups(rows: Observation[], fields: string[]) {
  const result = new Map<string, { name: string; rows: Observation[] }>();
  // Fields of the run are the same for all its records.
  const perRun = fields.every((f) => ['@run', '@name', '@version'].includes(f) || f.startsWith('@config/'));
  const byRun = new Map<Run, { name: string; rows: Observation[] }>();
  for (const row of rows) {
    const known = perRun && byRun.get(row.run);
    if (known) {
      known.rows.push(row);
      continue;
    }
    const key = JSON.stringify(fields.map((f) => valueAt(row, f) ?? null));
    let group = result.get(key);
    if (!group) {
      group = {
        name:
          fields.map((f) => (f === '@run' ? label(row.run) : String(valueAt(row, f) ?? 'Missing'))).join(' · ') ||
          'All records',
        rows: [],
      };
      result.set(key, group);
    }
    group.rows.push(row);
    if (perRun) byRun.set(row.run, group);
  }
  return [...result.entries()].map(([key, group]) => ({ key, ...group }));
}
/** A value's type: a typed value's format, with a file's media type, or its JSON type. */
export function typeOf(value: Json | undefined): string {
  const typed = envelope(value);
  if (!typed) return typeof value;
  return typed.format === 'file' ? `file:${(typed.data as { media_type?: string }).media_type}` : typed.format;
}
const discovered = new WeakMap<Observation[], FieldInfo[]>();
/** The fields of several runs' records; a run whose records did not change is not read again. */
export function discoverAll(runs: Observation[][]): FieldInfo[] {
  const fields = new Map<string, Set<string>>();
  for (const rows of runs.length ? runs : [[]]) {
    if (!discovered.has(rows)) discovered.set(rows, discover(rows));
    for (const { name, kinds } of discovered.get(rows)!) {
      if (!fields.has(name)) fields.set(name, new Set());
      kinds.forEach((kind) => fields.get(name)!.add(kind));
    }
  }
  return [...fields].map(([name, kinds]) => ({ name, kinds })).sort((a, b) => a.name.localeCompare(b.name));
}
export function discover(rows: Observation[]): FieldInfo[] {
  const fields = new Map<string, Set<string>>();
  const add = (name: string, value: Json) => {
    const kind = typeOf(value);
    if (!fields.has(name)) fields.set(name, new Set());
    fields.get(name)!.add(kind);
  };
  const visit = (value: Json, path: string) => {
    add(path, value);
    if (value && typeof value === 'object' && !Array.isArray(value) && !envelope(value))
      for (const [key, v] of Object.entries(value)) visit(v, `${path}/${key.replace(/~/g, '~0').replace(/\//g, '~1')}`);
  };
  const configured = new Set<Run>();
  for (const row of rows) {
    for (const [key, value] of Object.entries(row.data)) {
      add(key, value);
      if (value && typeof value === 'object' && !Array.isArray(value) && !envelope(value))
        for (const [k, v] of Object.entries(value))
          visit(v, `/${key.replace(/~/g, '~0').replace(/\//g, '~1')}/${k.replace(/~/g, '~0').replace(/\//g, '~1')}`);
    }
    if (row.run.config && !configured.has(row.run)) visit(row.run.config, '@config');
    configured.add(row.run);
  }
  for (const [name, kind] of [
    ['@run', 'string'],
    ['@name', 'string'],
    ['@version', 'number'],
    ['@ordinal', 'number'],
    ['@created', 'number'],
  ])
    fields.set(name, new Set([kind]));
  return [...fields].map(([name, kinds]) => ({ name, kinds })).sort((a, b) => a.name.localeCompare(b.name));
}
/** Settings every card starts with; each card kind overrides some of them. */
export function baseCard(kind: Kind, section: string): Card {
  return {
    id: uid(),
    title: '',
    kind,
    section,
    width: 6,
    height: 470,
    filters: [],
    filterMode: 'all',
    x: 'step',
    y: 'loss',
    value: '',
    split: ['@run'],
    pointOrder: { field: 'step', descending: false },
    lineOrder: 'label',
    marks: 'lines',
    logX: false,
    logY: false,
    mode: 'compare',
    axis: 'step',
    matching: 'exact',
    columns: 2,
    pageSize: 6,
    caption: '',
    fit: 'contain',
    linkAxes: false,
  };
}
