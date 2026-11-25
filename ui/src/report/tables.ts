// From Pumice (packages/markdown/src/table.ts).
/** GFM pipe tables: a model for table editors (parse with cell offsets, format, edit). */

export type Align = 'left' | 'center' | 'right' | null;

export interface TableCell {
  /** Cell markdown as written (trimmed, escapes kept: `\|` stays `\|`). */
  text: string;
  /** Offsets of `text` in the table source; an empty cell has from === to. */
  from: number;
  to: number;
}

export interface TableModel {
  header: TableCell[];
  align: Align[];
  rows: TableCell[][];
  /** Per row (header first), cells beyond the header's width; GFM ignores them, we keep them. */
  extra: string[][];
}

const DELIMITER_CELL = /^\s*:?-+:?\s*$/;

/** Splits a table line into cells at unescaped pipes, ignoring the optional outer pipes. */
function splitRow(line: string, lineFrom: number): TableCell[] {
  const cells: TableCell[] = [];
  let i = 0;
  // Skip indentation and the leading pipe.
  while (i < line.length && (line[i] === ' ' || line[i] === '\t')) i++;
  if (line[i] === '|') i++;
  let start = i;
  let end = line.length;
  // Trailing pipe (unescaped) ends the row.
  const trimmed = line.replace(/\s+$/, '');
  if (trimmed.endsWith('|') && !trimmed.endsWith('\\|')) end = trimmed.length - 1;
  const push = (from: number, to: number) => {
    let a = from;
    let b = to;
    while (a < b && /\s/.test(line[a]!)) a++;
    while (b > a && /\s/.test(line[b - 1]!)) b--;
    // An empty cell: one space after the pipe, so typed text starts where it reads well.
    if (a === b && b === to && to > from) a = b = from + 1;
    cells.push({ text: line.slice(a, b), from: lineFrom + a, to: lineFrom + b });
  };
  for (; i < end; i++) {
    if (line[i] === '\\') {
      i++;
      continue;
    }
    if (line[i] === '|') {
      push(start, i);
      start = i + 1;
    }
  }
  if (start <= end) push(start, end);
  return cells;
}

/** Parses a table's source (header, delimiter and body lines). Returns null if it isn't one. */
export function parseTable(source: string): TableModel | null {
  const lines: Array<{ text: string; from: number }> = [];
  let pos = 0;
  for (const text of source.split('\n')) {
    lines.push({ text, from: pos });
    pos += text.length + 1;
  }
  while (lines.length && !lines[lines.length - 1]!.text.trim()) lines.pop();
  if (lines.length < 2) return null;
  const header = splitRow(lines[0]!.text, lines[0]!.from);
  const delim = splitRow(lines[1]!.text, lines[1]!.from);
  if (!delim.length || !delim.every((c) => DELIMITER_CELL.test(c.text))) return null;
  const width = header.length;
  const align: Align[] = header.map((_, i) => {
    const d = delim[i]?.text.trim() ?? '';
    const l = d.startsWith(':');
    const r = d.endsWith(':');
    return l && r ? 'center' : r ? 'right' : l ? 'left' : null;
  });
  const extra: string[][] = [header.slice(width).map((c) => c.text)];
  const rows = lines.slice(2).map(({ text, from }) => {
    const cells = splitRow(text, from);
    extra.push(cells.slice(width).map((c) => c.text));
    const row = cells.slice(0, width);
    const lineEnd = from + text.replace(/\s+$/, '').length;
    // Missing cells sit at the end of the line.
    while (row.length < width) row.push({ text: '', from: lineEnd, to: lineEnd });
    return row;
  });
  return { header: header.slice(0, width), align, rows, extra };
}

const width = (s: string) => [...s].length;

/** Markdown for a table, with columns padded to equal width. */
export function formatTable(model: {
  header: string[];
  align: Align[];
  rows: string[][];
  extra?: string[][];
}): string {
  const cols = model.header.length;
  const cell = (row: string[], i: number) => row[i] ?? '';
  const widths = Array.from({ length: cols }, (_, i) =>
    Math.max(3, width(cell(model.header, i)), ...model.rows.map((r) => width(cell(r, i)))),
  );
  const pad = (text: string, i: number) => {
    const n = widths[i]! - width(text);
    const a = model.align[i];
    if (a === 'right') return ' '.repeat(n) + text;
    if (a === 'center') return ' '.repeat(Math.floor(n / 2)) + text + ' '.repeat(Math.ceil(n / 2));
    return text + ' '.repeat(n);
  };
  const line = (row: string[], extra: string[] = []) =>
    `| ${[...Array.from({ length: cols }, (_, i) => pad(cell(row, i), i)), ...extra].join(' | ')} |`;
  const delim = `| ${widths
    .map((w, i) => {
      const a = model.align[i];
      const dashes = '-'.repeat(Math.max(1, w - (a === 'center' ? 2 : a ? 1 : 0)));
      return a === 'center'
        ? `:${dashes}:`
        : a === 'right'
          ? `${dashes}:`
          : a === 'left'
            ? `:${dashes}`
            : dashes;
    })
    .join(' | ')} |`;
  return [
    line(model.header, model.extra?.[0]),
    delim,
    ...model.rows.map((r, i) => line(r, model.extra?.[i + 1])),
  ].join('\n');
}

/** Plain arrays (cell texts) of a parsed table, for editing operations. */
export interface TableData {
  header: string[];
  align: Align[];
  rows: string[][];
  extra: string[][];
}

export function tableData(model: TableModel): TableData {
  return {
    header: model.header.map((c) => c.text),
    align: [...model.align],
    rows: model.rows.map((r) => r.map((c) => c.text)),
    extra: model.extra.map((e) => [...e]),
  };
}

/** Cell text as shown to the user (`\|` → `|`). */
export function unescapeCell(text: string): string {
  return text.replace(/\\\|/g, '|');
}

/** Cell text as stored: pipes escaped, line breaks as `<br>`. */
export function escapeCell(text: string): string {
  return text
    .replace(/\r?\n/g, '<br>')
    .replace(/(^|[^\\])\|/g, '$1\\|')
    .replace(/(^|[^\\])\|/g, '$1\\|');
}

// ---- operations (all return new data) ----------------------------------------------------------

const clone = (d: TableData): TableData => ({
  header: [...d.header],
  align: [...d.align],
  rows: d.rows.map((r) => [...r]),
  extra: d.extra.map((e) => [...e]),
});

/** Inserts an empty body row at `index` (0 = first body row). */
export function insertRow(d: TableData, index: number): TableData {
  const t = clone(d);
  const at = Math.max(0, Math.min(index, t.rows.length));
  t.rows.splice(
    at,
    0,
    t.header.map(() => ''),
  );
  t.extra.splice(at + 1, 0, []);
  return t;
}

export function deleteRow(d: TableData, index: number): TableData {
  const t = clone(d);
  t.rows.splice(index, 1);
  t.extra.splice(index + 1, 1);
  return t;
}

export function moveRow(d: TableData, from: number, to: number): TableData {
  const t = clone(d);
  if (to < 0 || to >= t.rows.length) return t;
  const [row] = t.rows.splice(from, 1);
  const [extra] = t.extra.splice(from + 1, 1);
  t.rows.splice(to, 0, row!);
  t.extra.splice(to + 1, 0, extra ?? []);
  return t;
}

export function insertColumn(d: TableData, index: number, header = ''): TableData {
  const t = clone(d);
  const at = Math.max(0, Math.min(index, t.header.length));
  t.header.splice(at, 0, header);
  t.align.splice(at, 0, null);
  for (const r of t.rows) r.splice(at, 0, '');
  return t;
}

export function deleteColumn(d: TableData, index: number): TableData {
  const t = clone(d);
  if (t.header.length <= 1) return t;
  t.header.splice(index, 1);
  t.align.splice(index, 1);
  for (const r of t.rows) r.splice(index, 1);
  return t;
}

export function moveColumn(d: TableData, from: number, to: number): TableData {
  const t = clone(d);
  if (to < 0 || to >= t.header.length) return t;
  const move = <T>(a: T[]) => {
    const [x] = a.splice(from, 1);
    a.splice(to, 0, x!);
  };
  move(t.header);
  move(t.align);
  for (const r of t.rows) move(r);
  return t;
}

export function setAlign(d: TableData, index: number, align: Align): TableData {
  const t = clone(d);
  t.align[index] = align;
  return t;
}

/** Sorts body rows by a column: numbers numerically, otherwise text (natural order). */
export function sortRows(d: TableData, index: number, descending = false): TableData {
  const t = clone(d);
  const order = t.rows.map((r, i) => ({ r, e: t.extra[i + 1] ?? [] }));
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
  order.sort((a, b) => {
    const x = a.r[index] ?? '';
    const y = b.r[index] ?? '';
    const nx = Number(x.replace(/,/g, ''));
    const ny = Number(y.replace(/,/g, ''));
    const c =
      x !== '' && y !== '' && !Number.isNaN(nx) && !Number.isNaN(ny)
        ? nx - ny
        : collator.compare(x, y);
    return descending ? -c : c;
  });
  t.rows = order.map((o) => o.r);
  t.extra = [t.extra[0] ?? [], ...order.map((o) => o.e)];
  return t;
}
