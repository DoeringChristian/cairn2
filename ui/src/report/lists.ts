// From Pumice (plugins/core-editor/src/lists.ts).
import { syntaxTree } from '@codemirror/language';
import { EditorSelection, EditorState, Prec, type Extension } from '@codemirror/state';
import { ViewPlugin, keymap, type EditorView, type ViewUpdate } from '@codemirror/view';

/** Editing lists: a new line within an item, and selections that take the first marker along. */

const MARKER = /^([ \t]*)([-*+]|\d+[.)])([ \t]+)/;

/**
 * The indentation that continues a list item on a new line: up to where the item's text
 * starts (behind `- ` or `1. `), so the line stays part of the item. Undefined if the line is
 * not in a list.
 */
export function continuationIndent(itemLine: string): string | undefined {
  const m = MARKER.exec(itemLine);
  if (m) return m[1]! + ' '.repeat(m[2]!.length + 1);
  return undefined;
}

/**
 * Where a selection that begins at `column` of `line` should begin instead: at the list
 * marker, if it began right behind it. (The marker is easily missed: with the checkbox drawn,
 * the start of the line is behind the dash.)
 *
 * A selection that takes a task's checkbox along always takes the dash too: the two are one
 * marker. One that goes on past the line (`severalLines`) also does when it begins at the
 * item's text. Within one line, a selection of just the text stays as it is.
 */
export function markerStart(line: string, column: number, severalLines = true): number | undefined {
  const m = MARKER.exec(line);
  if (!m) return undefined;
  const behindMarker = m[0].length;
  const box = /^\[[ xX]\][ \t]+/.exec(line.slice(behindMarker))?.[0].length ?? 0;
  // `- |[ ] task`: from in front of the box.
  if (box > 0 && column === behindMarker) return m[1]!.length;
  if (!severalLines) return undefined;
  // `- |item` or `- [ ] |task`: from the text, on over further lines.
  if (column === behindMarker + box) return m[1]!.length;
  return undefined;
}

function inList(state: EditorState, pos: number): boolean {
  for (let node = syntaxTree(state).resolveInner(pos, -1); ;) {
    if (node.name === 'FencedCode' || node.name === 'CodeBlock') return false;
    if (node.name === 'ListItem') return true;
    if (!node.parent) return false;
    node = node.parent;
  }
}

/** Shift+Enter in a list item: a new line in the same item, not a new item. */
const newlineInItem = (view: EditorView) => {
  const { state } = view;
  if (state.readOnly) return false;
  const sel = state.selection.main;
  if (!inList(state, sel.from)) return false;
  // The line the item starts on, or the continuation line the cursor is on.
  let indent: string | undefined;
  for (let n = state.doc.lineAt(sel.from).number; n >= 1; n--) {
    const text = state.doc.line(n).text;
    indent = continuationIndent(text);
    if (indent !== undefined || !text.trim()) break;
    // A line that is itself a continuation: as far in as it is, unless an item starts above.
    if (n === state.doc.lineAt(sel.from).number && /^[ \t]+\S/.test(text))
      indent = /^[ \t]+/.exec(text)![0];
    if (indent !== undefined) break;
  }
  if (indent === undefined) return false;
  view.dispatch({
    changes: { from: sel.from, to: sel.to, insert: `\n${indent}` },
    selection: { anchor: sel.from + 1 + indent.length },
    userEvent: 'input',
    scrollIntoView: true,
  });
  return true;
};

/**
 * What a new item under `itemLine` starts with: the same marker (the next number, an empty
 * checkbox for a task).
 */
export function nextItemPrefix(itemLine: string): string | undefined {
  const m = MARKER.exec(itemLine);
  if (!m) return undefined;
  const number = /^(\d+)([.)])$/.exec(m[2]!);
  const marker = number ? `${Number(number[1]) + 1}${number[2]}` : m[2]!;
  const task = /^\[[ xX]\][ \t]+/.test(itemLine.slice(m[0].length)) ? '[ ] ' : '';
  return `${m[1]}${marker} ${task}`;
}

/** Enter on a continuation line of an item starts the next item (not one more such line). */
const newItemAfterContinuation = (view: EditorView) => {
  const { state } = view;
  const sel = state.selection.main;
  if (state.readOnly || !sel.empty || !inList(state, sel.head)) return false;
  const line = state.doc.lineAt(sel.head);
  // On the item's own line the usual Enter does it.
  if (MARKER.test(line.text) || !/^[ \t]+\S/.test(line.text)) return false;
  const depth = /^[ \t]+/.exec(line.text)![0].length;
  let prefix: string | undefined;
  for (let n = line.number - 1; n >= 1; n--) {
    const text = state.doc.line(n).text;
    if (!text.trim()) break;
    const m = MARKER.exec(text);
    // The item this line continues: the nearest one that starts further out.
    if (m && m[1]!.length < depth) {
      prefix = nextItemPrefix(text);
      break;
    }
  }
  if (prefix === undefined) return false;
  view.dispatch({
    changes: { from: sel.head, insert: `\n${prefix}` },
    selection: { anchor: sel.head + 1 + prefix.length },
    userEvent: 'input',
    scrollIntoView: true,
  });
  return true;
};

/** A selection that begins behind a list marker begins at the marker (see `markerStart`). */
const selectMarker = EditorState.transactionFilter.of((tr) => {
  if (!tr.selection || !tr.isUserEvent('select')) return tr;
  const doc = tr.newDoc;
  let changed = false;
  const ranges = tr.selection.ranges.map((range) => {
    if (range.empty) return range;
    const line = doc.lineAt(range.from);
    const start = markerStart(line.text, range.from - line.from, range.to > line.to);
    if (start === undefined) return range;
    changed = true;
    const from = line.from + start;
    return range.anchor === range.from
      ? EditorSelection.range(from, range.head)
      : EditorSelection.range(range.anchor, from);
  });
  if (!changed) return tr;
  return [tr, { selection: EditorSelection.create(ranges, tr.selection.mainIndex) }];
});

const NUMBERED = /^([ \t]*)(\d{1,9})([.)])(?=[ \t]|$)/;

/**
 * The changes that put the numbers of the numbered lists around the given lines in order
 * (`lines` are the note's lines, `first`..`last` indices into them): each list counts on from
 * the number of its first item. A list whose items all carry the same number (`1.` for each,
 * as some write them) is left as it is. Each change is within its line.
 */
export function renumberLists(
  lines: readonly string[],
  first: number,
  last: number,
  /** Lines that were just set in or out: a list that now begins with one of them begins at 1. */
  restart: ReadonlySet<number> = new Set(),
): Array<{ line: number; from: number; to: number; insert: string }> {
  const out: Array<{ line: number; from: number; to: number; insert: string }> = [];
  const done = new Set<string>();
  const indentOf = (text: string) => /^[ \t]*/.exec(text)![0].length;
  const blank = (i: number) => !lines[i]!.trim();
  for (let at = Math.max(0, first); at <= Math.min(lines.length - 1, last); at++) {
    const item = NUMBERED.exec(lines[at]!);
    if (!item) continue;
    const indent = item[1]!.length;
    const mark = item[3]!;
    // Whether line `i` belongs to the list: one of its items, or something set in under one.
    const kind = (i: number): 'item' | 'inside' | 'outside' => {
      const m = NUMBERED.exec(lines[i]!);
      if (m && m[1]!.length === indent && m[3] === mark) return 'item';
      return indentOf(lines[i]!) > indent ? 'inside' : 'outside';
    };
    // Up and down from the item, over empty lines that the list goes on behind.
    const walk = (from: number, step: 1 | -1): number[] => {
      const items: number[] = [];
      for (let i = from + step; i >= 0 && i < lines.length; i += step) {
        if (blank(i)) {
          let next = i;
          while (next >= 0 && next < lines.length && blank(next)) next += step;
          if (next < 0 || next >= lines.length || kind(next) === 'outside') break;
          i = next - step;
          continue;
        }
        const k = kind(i);
        if (k === 'outside') break;
        if (k === 'item') items.push(i);
      }
      return items;
    };
    const items = [...walk(at, -1).reverse(), at, ...walk(at, 1)];
    const key = `${items[0]}:${indent}`;
    if (done.has(key)) continue;
    done.add(key);
    const numbers = items.map((i) => Number(NUMBERED.exec(lines[i]!)![2]));
    const base = restart.has(items[0]!) ? 1 : numbers[0]!;
    if (base === numbers[0] && (items.length < 2 || numbers.every((n) => n === base))) continue;
    items.forEach((line, i) => {
      const want = String(base + i);
      const have = NUMBERED.exec(lines[line]!)![2]!;
      if (have !== want) out.push({ line, from: indent, to: indent + have.length, insert: want });
    });
  }
  return out;
}

/**
 * A numbered list stays in order while it is written: an item put in, taken out, moved or
 * set in gives the items behind it their new numbers, in the same step (one undo takes both
 * back).
 */
const renumber = EditorState.transactionFilter.of((tr) => {
  if (!tr.docChanged) return tr;
  if (!tr.isUserEvent('input') && !tr.isUserEvent('delete') && !tr.isUserEvent('move')) return tr;
  const doc = tr.newDoc;
  let first = Infinity;
  let last = -1;
  const moved = new Set<number>();
  const indenting = tr.isUserEvent('input.indent') || tr.isUserEvent('delete.dedent');
  tr.changes.iterChanges((fromA, toA, fromB, toB, inserted) => {
    const a = doc.lineAt(fromB);
    const b = doc.lineAt(toB);
    // A number that is being written is the writer's: `2` on its way to `27` stays.
    const m = a.number === b.number ? NUMBERED.exec(a.text) : null;
    const digits = /^\d*$/;
    if (
      m &&
      fromB >= a.from + m[1]!.length &&
      toB <= a.from + m[1]!.length + m[2]!.length &&
      digits.test(inserted.toString()) &&
      digits.test(tr.startState.sliceDoc(fromA, toA))
    )
      return;
    first = Math.min(first, a.number - 1);
    last = Math.max(last, b.number + 1);
    if (indenting) for (let n = a.number; n <= b.number; n++) moved.add(n - 1);
  });
  if (last < 0) return tr;
  if (doc.lines > 20000) return tr;
  // Not in code, where a number is just a number (as it was read before this change).
  const before = tr.changes.invertedDesc;
  const tree = syntaxTree(tr.startState);
  const inCode = (pos: number): boolean => {
    for (let n = tree.resolveInner(Math.min(before.mapPos(pos), tr.startState.doc.length), 1); ;) {
      if (/Code|Frontmatter|Math|Comment/.test(n.name)) return true;
      if (!n.parent) return false;
      n = n.parent;
    }
  };
  const lines: string[] = [];
  for (const line of doc.iterLines()) lines.push(line);
  const changes = renumberLists(lines, first - 1, last - 1, moved)
    .map((c) => {
      const line = doc.line(c.line + 1);
      return { from: line.from + c.from, to: line.from + c.to, insert: c.insert };
    })
    .filter((c) => !inCode(c.from));
  if (!changes.length) return tr;
  return [tr, { changes, sequential: true }];
});

/**
 * How wide a space is in the note's font, for the steps of nested lists (a step is as wide as
 * a marker and the space behind it; see `.cm-pm-indent`).
 */
const spaceWidth = ViewPlugin.fromClass(
  class {
    private font = '';
    constructor(view: EditorView) {
      this.measure(view);
    }
    update(update: ViewUpdate) {
      if (update.geometryChanged) this.measure(update.view);
    }
    private measure(view: EditorView) {
      view.requestMeasure({
        key: this,
        read: () => {
          const font = getComputedStyle(view.contentDOM).font;
          if (font === this.font) return undefined;
          this.font = font;
          const canvas = document.createElement('canvas').getContext('2d');
          if (!canvas) return undefined;
          canvas.font = font;
          return canvas.measureText(' ').width;
        },
        write: (width) => {
          if (width) view.dom.style.setProperty('--pm-space', `${width.toFixed(2)}px`);
        },
      });
    }
  },
);

export function listEditing(): Extension {
  return [
    Prec.high(
      keymap.of([
        { key: 'Shift-Enter', run: newlineInItem },
        { key: 'Enter', run: newItemAfterContinuation },
      ]),
    ),
    selectMarker,
    renumber,
    spaceWidth,
  ];
}
