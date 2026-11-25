// From Pumice (plugins/core-tables/src/widget.ts), with the menu and the look of Cairn.
import { syntaxTree } from '@codemirror/language';
import {
  Annotation,
  Prec,
  StateEffect,
  StateField,
  type EditorState,
  type Extension,
  type Range,
} from '@codemirror/state';
import { Decoration, EditorView, WidgetType, keymap, type DecorationSet } from '@codemirror/view';
import { yUndoManagerKeymap } from 'y-codemirror.next';
import { icon } from './icons';
import {
  deleteColumn,
  deleteRow,
  escapeCell,
  formatTable,
  insertColumn,
  insertRow,
  moveColumn,
  moveRow,
  parseTable,
  setAlign,
  sortRows,
  tableData,
  unescapeCell,
  type Align,
  type TableData,
  type TableModel,
} from './tables';

export type MenuEntry = 'separator' | { label: string; icon: string; run: () => void; disabled?: boolean; danger?: boolean };
export interface TableDeps {
  /** Inline Markdown of one cell, drawn. */
  renderInline(markdown: string): Node;
  showMenu(entries: MenuEntry[], at: { x: number; y: number }): void;
  openLink(href: string): void;
  /** Copies where a part of the table is, and what it reads. */
  copyReference(view: EditorView, from: number, to: number, text?: string): void;
}

/** Edits made by the table widget itself (the widget doesn't re-render the cell being typed in). */
const fromWidget = Annotation.define<boolean>();

// ---- locating tables -----------------------------------------------------------------------

interface Located {
  from: number;
  to: number;
  model: TableModel;
}

/** Top-level `Table` nodes (tables in quotes or lists keep the plain source view). */
function tablesIn(state: EditorState): Array<{ from: number; to: number }> {
  const out: Array<{ from: number; to: number }> = [];
  const cursor = syntaxTree(state).topNode.cursor();
  const visit = () => {
    if (!cursor.firstChild()) return;
    do {
      if (cursor.name === 'Body' || cursor.name === 'Document') {
        visit();
        continue;
      }
      if (cursor.name === 'Table') {
        let to = cursor.to;
        if (state.doc.sliceString(to - 1, to) === '\n') to--;
        out.push({ from: state.doc.lineAt(cursor.from).from, to: state.doc.lineAt(to).to });
      }
    } while (cursor.nextSibling());
    cursor.parent();
  };
  visit();
  return out;
}

function locate(view: EditorView, root: HTMLElement): Located | null {
  let pos: number;
  try {
    pos = view.posAtDOM(root);
  } catch {
    return null;
  }
  const t = tablesIn(view.state).find((r) => r.from <= pos && pos <= r.to);
  if (!t) return null;
  const model = parseTable(view.state.sliceDoc(t.from, t.to));
  return model ? { ...t, model } : null;
}

// ---- focus requests (survive widget re-creation) ---------------------------------------------

interface FocusRequest {
  /** Document position of the table start (after the transaction). */
  table: number;
  row: number;
  col: number;
  caret: 'start' | 'end' | 'all';
}

const pendingFocus = new WeakMap<EditorView, FocusRequest>();

function requestFocus(view: EditorView, req: FocusRequest): void {
  pendingFocus.set(view, req);
  // The widget may already be in place (updateDOM patched it); try right away, too.
  requestAnimationFrame(() => applyPendingFocus(view));
}

function applyPendingFocus(view: EditorView): void {
  const req = pendingFocus.get(view);
  if (!req) return;
  for (const root of view.contentDOM.querySelectorAll<HTMLElement>('.pm-table-widget')) {
    const t = locate(view, root);
    if (t?.from !== req.table) continue;
    pendingFocus.delete(view);
    const cell = cellAt(root, req.row, req.col);
    if (cell) focusCell(cell, req.caret);
    return;
  }
}

function cellAt(root: HTMLElement, row: number, col: number): HTMLElement | null {
  return root.querySelector<HTMLElement>(`.pm-table-cell[data-row="${row}"][data-col="${col}"]`);
}

function focusCell(cell: HTMLElement, caret: FocusRequest['caret']): void {
  cell.focus({ preventScroll: false });
  const sel = cell.ownerDocument.getSelection();
  if (!sel) return;
  const range = cell.ownerDocument.createRange();
  range.selectNodeContents(cell);
  if (caret !== 'all') range.collapse(caret === 'start');
  sel.removeAllRanges();
  sel.addRange(range);
}

/** Caret offset in a focused cell's text, or null without a collapsed selection in it. */
function caretOffset(cell: HTMLElement): { offset: number; collapsed: boolean } | null {
  const sel = cell.ownerDocument.getSelection();
  if (!sel || !sel.rangeCount || !cell.contains(sel.anchorNode)) return null;
  const range = sel.getRangeAt(0);
  const before = cell.ownerDocument.createRange();
  before.selectNodeContents(cell);
  before.setEnd(range.startContainer, range.startOffset);
  return { offset: before.toString().length, collapsed: range.collapsed };
}

/** Cell text as shown while editing: escapes and `<br>` undone. */
const editText = (raw: string) => unescapeCell(raw).replace(/<br\s*\/?>/gi, '\n');

// ---- the widget ----------------------------------------------------------------------------

class TableWidget extends WidgetType {
  constructor(
    readonly source: string,
    readonly from: number,
    readonly deps: TableDeps,
  ) {
    super();
  }

  override eq(other: TableWidget): boolean {
    return other.source === this.source && other.from === this.from;
  }

  toDOM(view: EditorView): HTMLElement {
    const root = document.createElement('div');
    root.className = 'pm-table-widget';
    this.build(root, view);
    attachEvents(root, view, this.deps);
    // A focus request may be waiting for this table (after a structural edit).
    requestAnimationFrame(() => applyPendingFocus(view));
    return root;
  }

  override updateDOM(dom: HTMLElement, view: EditorView): boolean {
    const model = parseTable(this.source);
    if (!model) return false;
    const grid = [model.header, ...model.rows];
    const rows = dom.querySelectorAll('tr');
    if (rows.length !== grid.length || rows[0]?.children.length !== model.header.length)
      return false;
    for (let r = 0; r < grid.length; r++) {
      for (let c = 0; c < grid[r]!.length; c++) {
        const cell = cellAt(dom, r, c);
        if (!cell) return false;
        const raw = grid[r]![c]!.text;
        if (cell.dataset.raw === raw) continue;
        cell.dataset.raw = raw;
        if (cell === document.activeElement) {
          // A remote or undo change to the cell being edited: show it, caret at the end.
          // (Spaces at the cell's edges aren't stored, so compare trimmed.)
          if (escapeCell(cell.innerText.replace(/\n$/, '')).trim() !== raw) {
            cell.textContent = editText(raw);
            focusCell(cell, 'end');
          }
        } else {
          cell.replaceChildren(this.deps.renderInline(raw));
        }
      }
    }
    this.applyAlign(dom, model.align);
    requestAnimationFrame(() => applyPendingFocus(view));
    return true;
  }

  private applyAlign(root: HTMLElement, align: Align[]): void {
    for (const td of root.querySelectorAll<HTMLElement>('th, td')) {
      const a = align[Number(td.dataset.col)];
      td.style.textAlign = a ?? '';
    }
  }

  private build(root: HTMLElement, view: EditorView): void {
    const model = parseTable(this.source);
    root.replaceChildren();
    if (!model) return;
    const scroll = document.createElement('div');
    scroll.className = 'pm-table-scroll';
    const table = document.createElement('table');
    table.className = 'pm-table';
    const grid = [model.header, ...model.rows];
    const thead = document.createElement('thead');
    const tbody = document.createElement('tbody');
    grid.forEach((row, r) => {
      const tr = document.createElement('tr');
      row.forEach((c, col) => {
        const td = document.createElement(r === 0 ? 'th' : 'td');
        td.dataset.col = String(col);
        const cell = document.createElement('div');
        cell.className = 'pm-table-cell';
        cell.contentEditable = view.state.readOnly ? 'false' : 'true';
        cell.spellcheck = true;
        cell.dataset.row = String(r);
        cell.dataset.col = String(col);
        cell.dataset.raw = c.text;
        cell.setAttribute('role', r === 0 ? 'columnheader' : 'cell');
        cell.replaceChildren(this.deps.renderInline(c.text));
        td.append(cell);
        if (r === 0) td.append(handle('col', col, 'Column options'));
        if (col === 0 && r > 0) td.append(handle('row', r, 'Row options'));
        tr.append(td);
      });
      (r === 0 ? thead : tbody).append(tr);
    });
    table.append(thead, tbody);
    scroll.append(table);
    const addCol = addButton('pm-table-add-col', 'Add column');
    const addRow = addButton('pm-table-add-row', 'Add row');
    root.append(scroll, addCol, addRow);
    this.applyAlign(root, model.align);
  }

  override ignoreEvent(): boolean {
    // The table handles its own mouse and keyboard events.
    return true;
  }
}

function handle(kind: 'row' | 'col', index: number, label: string): HTMLElement {
  const b = document.createElement('button');
  b.className = `pm-table-handle pm-table-${kind}-handle`;
  b.dataset.kind = kind;
  b.dataset.index = String(index);
  b.title = label;
  b.setAttribute('aria-label', label);
  b.tabIndex = -1;
  b.contentEditable = 'false';
  b.append(icon('grip', 12));
  return b;
}

function addButton(cls: string, label: string): HTMLElement {
  const b = document.createElement('button');
  b.className = `pm-table-add ${cls}`;
  b.title = label;
  b.setAttribute('aria-label', label);
  b.tabIndex = -1;
  b.append(icon('plus', 14));
  return b;
}

// ---- editing ---------------------------------------------------------------------------------

/** Replaces the table with `data` (formatted) and optionally focuses a cell afterwards. */
function replaceTable(
  view: EditorView,
  t: Located,
  data: TableData,
  focus?: Omit<FocusRequest, 'table'>,
) {
  if (focus) pendingFocus.set(view, { table: t.from, ...focus });
  view.dispatch({
    changes: { from: t.from, to: t.to, insert: formatTable(data) },
    annotations: fromWidget.of(true),
    userEvent: 'input.table',
  });
  if (focus) requestAnimationFrame(() => applyPendingFocus(view));
}

/** Writes one cell's text into the document (only that cell's range changes). */
function writeCell(view: EditorView, root: HTMLElement, cell: HTMLElement): void {
  const t = locate(view, root);
  if (!t) return;
  const r = Number(cell.dataset.row);
  const c = Number(cell.dataset.col);
  const raw = escapeCell(cell.innerText.replace(/\n$/, ''));
  cell.dataset.raw = raw;
  const target = (r === 0 ? t.model.header : t.model.rows[r - 1])?.[c];
  if (!target || target.text === raw) return;
  const line = view.state.doc.lineAt(t.from + target.from);
  const missing =
    target.from === target.to && t.from + target.from === line.to && !/\|\s*$/.test(line.text);
  if (missing) {
    // The row has fewer cells than the header: rewrite the table instead.
    const data = tableData(t.model);
    (r === 0 ? data.header : data.rows[r - 1]!)[c] = raw;
    view.dispatch({
      changes: { from: t.from, to: t.to, insert: formatTable(data) },
      annotations: fromWidget.of(true),
      userEvent: 'input.type',
    });
    return;
  }
  view.dispatch({
    changes: { from: t.from + target.from, to: t.from + target.to, insert: raw },
    annotations: fromWidget.of(true),
    userEvent: 'input.type',
  });
}

function exitTable(view: EditorView, root: HTMLElement, where: 'before' | 'after'): void {
  const t = locate(view, root);
  if (!t) return;
  const doc = view.state.doc;
  if (where === 'after') {
    const last = doc.lineAt(t.to);
    if (last.number === doc.lines) {
      view.dispatch({
        changes: { from: doc.length, insert: '\n' },
        selection: { anchor: doc.length + 1 },
      });
    } else view.dispatch({ selection: { anchor: doc.line(last.number + 1).from } });
  } else {
    const first = doc.lineAt(t.from);
    if (first.number === 1) {
      view.dispatch({ changes: { from: 0, insert: '\n' }, selection: { anchor: 0 } });
    } else view.dispatch({ selection: { anchor: doc.line(first.number - 1).to } });
  }
  view.focus();
}

function moveFocus(
  view: EditorView,
  root: HTMLElement,
  from: HTMLElement,
  dr: number,
  dc: number,
  caret: FocusRequest['caret'],
): void {
  const t = locate(view, root);
  if (!t) return;
  const rows = t.model.rows.length + 1;
  const cols = t.model.header.length;
  let r = Number(from.dataset.row) + dr;
  let c = Number(from.dataset.col) + dc;
  if (c >= cols) ((c = 0), r++);
  if (c < 0) ((c = cols - 1), r--);
  if (r < 0) return exitTable(view, root, 'before');
  if (r >= rows) {
    if (dc !== 0 || dr > 0) {
      // Past the last cell (Tab or Enter): a new row.
      return replaceTable(view, t, insertRow(tableData(t.model), rows - 1), {
        row: rows,
        col: dc !== 0 ? 0 : Math.min(Number(from.dataset.col), cols - 1),
        caret: 'start',
      });
    }
    return exitTable(view, root, 'after');
  }
  const target = cellAt(root, r, c);
  if (target) focusCell(target, caret);
}

function runUndo(view: EditorView, redo: boolean): void {
  const key = redo ? 'Mod-y' : 'Mod-z';
  yUndoManagerKeymap.find((k) => k.key === key)?.run?.(view);
}

function cellKeydown(view: EditorView, root: HTMLElement, cell: HTMLElement, e: KeyboardEvent) {
  const mod = e.metaKey || e.ctrlKey;
  const caret = caretOffset(cell);
  const len = cell.innerText.replace(/\n$/, '').length;
  const lineOf = () => {
    const text = cell.innerText.replace(/\n$/, '');
    const off = caret?.offset ?? 0;
    return { first: !text.slice(0, off).includes('\n'), last: !text.slice(off).includes('\n') };
  };
  const handled = () => {
    e.preventDefault();
    e.stopPropagation();
  };
  if (e.key === 'Tab') {
    handled();
    return moveFocus(view, root, cell, 0, e.shiftKey ? -1 : 1, 'all');
  }
  if (e.key === 'Enter') {
    handled();
    if (e.shiftKey) return void cell.ownerDocument.execCommand('insertLineBreak');
    if (mod) return exitTable(view, root, 'after');
    return moveFocus(view, root, cell, 1, 0, 'end');
  }
  if (e.key === 'Escape') {
    handled();
    return exitTable(view, root, 'after');
  }
  if (e.key === 'ArrowUp' && !e.shiftKey && lineOf().first) {
    handled();
    return moveFocus(view, root, cell, -1, 0, 'end');
  }
  if (e.key === 'ArrowDown' && !e.shiftKey && lineOf().last) {
    handled();
    const t = locate(view, root);
    if (t && Number(cell.dataset.row) === t.model.rows.length)
      return exitTable(view, root, 'after');
    return moveFocus(view, root, cell, 1, 0, 'end');
  }
  if (e.key === 'ArrowLeft' && !e.shiftKey && caret?.collapsed && caret.offset === 0) {
    handled();
    return moveFocus(view, root, cell, 0, -1, 'end');
  }
  if (e.key === 'ArrowRight' && !e.shiftKey && caret?.collapsed && caret.offset === len) {
    handled();
    return moveFocus(view, root, cell, 0, 1, 'start');
  }
  // All of the cell, never of the report.
  if (mod && e.key.toLowerCase() === 'a') {
    handled();
    return focusCell(cell, 'all');
  }
  if (mod && (e.key === 'z' || e.key === 'Z' || e.key === 'y')) {
    handled();
    const redo = e.key === 'y' || e.shiftKey;
    const t = locate(view, root);
    if (t) {
      pendingFocus.set(view, {
        table: t.from,
        row: Number(cell.dataset.row),
        col: Number(cell.dataset.col),
        caret: 'end',
      });
    }
    runUndo(view, redo);
    requestAnimationFrame(() => applyPendingFocus(view));
    return;
  }
  // Everything else is typing; keep CodeMirror's keymaps out of it.
  e.stopPropagation();
}

function tableMenu(
  view: EditorView,
  root: HTMLElement,
  deps: TableDeps,
  target: { row?: number; col?: number },
): MenuEntry[] {
  const t = locate(view, root);
  if (!t) return [];
  const data = tableData(t.model);
  const rows = data.rows.length;
  const cols = data.header.length;
  const entries: MenuEntry[] = [];
  // Leaving the table pads its columns, which moves where it ends.
  const now = () => {
    const found = tablesIn(view.state).find((x) => x.from === t.from);
    const model = found && parseTable(view.state.sliceDoc(found.from, found.to));
    return found && model ? { ...found, model } : t;
  };
  const edit = (next: TableData, focus?: Omit<FocusRequest, 'table'>) => () =>
    replaceTable(view, now(), next, focus);
  const { row, col } = target;
  if (row !== undefined) {
    const body = row - 1;
    if (row > 0) {
      entries.push({
        label: 'Insert row above',
        icon: 'arrow-up',
        run: edit(insertRow(data, body), { row, col: 0, caret: 'start' }),
      });
    }
    entries.push({
      label: 'Insert row below',
      icon: 'arrow-down',
      run: edit(insertRow(data, body + 1), { row: row + 1, col: 0, caret: 'start' }),
    });
    if (row > 0) {
      entries.push(
        {
          label: 'Move row up',
          icon: 'chevron-up',
          disabled: row <= 1,
          run: edit(moveRow(data, body, body - 1)),
        },
        {
          label: 'Move row down',
          icon: 'chevron-down',
          disabled: row >= rows,
          run: edit(moveRow(data, body, body + 1)),
        },
        { label: 'Delete row', icon: 'trash', danger: true, run: edit(deleteRow(data, body)) },
      );
    }
  }
  if (col !== undefined) {
    if (entries.length) entries.push('separator');
    entries.push(
      {
        label: 'Insert column left',
        icon: 'arrow-left',
        run: edit(insertColumn(data, col), { row: 0, col, caret: 'start' }),
      },
      {
        label: 'Insert column right',
        icon: 'arrow-right',
        run: edit(insertColumn(data, col + 1), { row: 0, col: col + 1, caret: 'start' }),
      },
      {
        label: 'Move column left',
        icon: 'chevron-left',
        disabled: col === 0,
        run: edit(moveColumn(data, col, col - 1)),
      },
      {
        label: 'Move column right',
        icon: 'chevron-right',
        disabled: col >= cols - 1,
        run: edit(moveColumn(data, col, col + 1)),
      },
      'separator',
      { label: 'Align left', icon: 'align-left', run: edit(setAlign(data, col, 'left')) },
      { label: 'Align center', icon: 'align-center', run: edit(setAlign(data, col, 'center')) },
      { label: 'Align right', icon: 'align-right', run: edit(setAlign(data, col, 'right')) },
      'separator',
      { label: 'Sort ascending', icon: 'arrow-down-az', run: edit(sortRows(data, col)) },
      { label: 'Sort descending', icon: 'arrow-down-za', run: edit(sortRows(data, col, true)) },
      'separator',
      {
        label: 'Delete column',
        icon: 'trash',
        danger: true,
        disabled: cols <= 1,
        run: edit(deleteColumn(data, col)),
      },
    );
  }
  const selected = getSelection()?.toString().trim() ?? '';
  entries.push(
    'separator',
    {
      label: 'Copy reference',
      icon: 'copy',
      run: () => {
        const current = now();
        const at = row !== undefined && col !== undefined ? (row === 0 ? current.model.header : current.model.rows[row - 1])?.[col] : undefined;
        if (at) deps.copyReference(view, current.from + at.from, current.from + at.to, selected || undefined);
        else deps.copyReference(view, current.from, current.to);
      },
    },
    { label: 'Format table', icon: 'table', run: edit(data) },
    {
      label: 'Edit as markdown',
      icon: 'code',
      run: () => {
        view.dispatch({
          effects: showSource.of(t.from),
          selection: { anchor: t.from },
        });
        view.focus();
      },
    },
    {
      label: 'Delete table',
      icon: 'trash',
      danger: true,
      run: () => {
        view.dispatch({ changes: { from: t.from, to: Math.min(now().to + 1, view.state.doc.length) } });
        view.focus();
      },
    },
  );
  return entries;
}

/**
 * A press on a row or column handle: dragging it moves the row or column to where it is let
 * go; a click without a drag opens the handle's menu.
 */
function dragHandle(
  view: EditorView,
  root: HTMLElement,
  deps: TableDeps,
  handle: HTMLElement,
  down: MouseEvent,
): void {
  const row = handle.dataset.kind === 'row';
  const from = Number(handle.dataset.index);
  const cellsOf = (index: number) =>
    [
      ...root.querySelectorAll<HTMLElement>(
        row ? `.pm-table-cell[data-row="${index}"]` : `.pm-table-cell[data-col="${index}"]`,
      ),
    ].map((c) => c.closest<HTMLElement>('td, th') ?? c);
  // The rows (below the header) or columns there are, with where they are on screen.
  const slots = () => {
    const seen = new Map<number, DOMRect>();
    for (const cell of root.querySelectorAll<HTMLElement>('.pm-table-cell')) {
      const index = Number(row ? cell.dataset.row : cell.dataset.col);
      if ((row && index === 0) || seen.has(index)) continue;
      seen.set(index, (cell.closest('td, th') ?? cell).getBoundingClientRect());
    }
    return [...seen.entries()].sort((a, b) => a[0] - b[0]);
  };
  let dragging = false;
  let target = from;
  const clear = () => {
    for (const el of root.querySelectorAll('.is-drop-before, .is-drop-after, .is-dragged'))
      el.classList.remove('is-drop-before', 'is-drop-after', 'is-dragged');
  };
  const move = (e: MouseEvent) => {
    if (!dragging && Math.hypot(e.clientX - down.clientX, e.clientY - down.clientY) < 5) return;
    dragging = true;
    root.classList.add('is-dragging', row ? 'is-dragging-row' : 'is-dragging-col');
    const at = row ? e.clientY : e.clientX;
    const all = slots();
    // The row or column under the pointer (the first or last one beyond the table's edges).
    target = all[all.length - 1]?.[0] ?? from;
    for (const [index, rect] of all) {
      if (at < (row ? rect.bottom : rect.right)) {
        target = index;
        break;
      }
    }
    clear();
    cellsOf(from).forEach((c) => c.classList.add('is-dragged'));
    if (target !== from)
      cellsOf(target).forEach((c) =>
        c.classList.add(target > from ? 'is-drop-after' : 'is-drop-before'),
      );
  };
  const up = () => {
    window.removeEventListener('mousemove', move, true);
    window.removeEventListener('mouseup', up, true);
    clear();
    root.classList.remove('is-dragging', 'is-dragging-row', 'is-dragging-col');
    if (!dragging) {
      const rect = handle.getBoundingClientRect();
      deps.showMenu(tableMenu(view, root, deps, row ? { row: from } : { col: from }), {
        x: rect.left,
        y: rect.bottom + 2,
      });
      return;
    }
    if (target === from) return;
    const t = locate(view, root);
    if (!t) return;
    const data = tableData(t.model);
    // Rows are counted with the header as row 0; the data has the body rows only.
    replaceTable(
      view,
      t,
      row ? moveRow(data, from - 1, target - 1) : moveColumn(data, from, target),
    );
  };
  window.addEventListener('mousemove', move, true);
  window.addEventListener('mouseup', up, true);
}

function attachEvents(root: HTMLElement, view: EditorView, deps: TableDeps): void {
  const cellOf = (el: EventTarget | null) =>
    el instanceof Element ? el.closest<HTMLElement>('.pm-table-cell') : null;

  root.addEventListener('focusin', (e) => {
    const cell = cellOf(e.target);
    if (!cell || cell.dataset.editing) return;
    cell.dataset.editing = 'true';
    const raw = cell.dataset.raw ?? '';
    cell.textContent = editText(raw);
    root.classList.add('is-editing');
  });
  root.addEventListener('focusout', (e) => {
    const cell = cellOf(e.target);
    if (cell) {
      delete cell.dataset.editing;
      cell.replaceChildren(deps.renderInline(cell.dataset.raw ?? ''));
    }
    const next = e.relatedTarget as Node | null;
    if (next && root.contains(next)) return;
    root.classList.remove('is-editing');
    // Leaving the table: pad its columns again.
    requestAnimationFrame(() => {
      if (!root.isConnected || root.contains(document.activeElement)) return;
      const t = locate(view, root);
      if (!t) return;
      const formatted = formatTable(tableData(t.model));
      if (view.state.sliceDoc(t.from, t.to) !== formatted) {
        view.dispatch({
          changes: { from: t.from, to: t.to, insert: formatted },
          annotations: fromWidget.of(true),
          userEvent: 'input.table',
        });
      }
    });
  });
  root.addEventListener('input', (e) => {
    const cell = cellOf(e.target);
    if (cell) writeCell(view, root, cell);
  });
  root.addEventListener('paste', (e) => {
    const cell = cellOf(e.target);
    if (!cell) return;
    e.preventDefault();
    const text = e.clipboardData?.getData('text/plain') ?? '';
    cell.ownerDocument.execCommand('insertText', false, text.replace(/\r?\n/g, ' '));
  });
  root.addEventListener('keydown', (e) => {
    const cell = cellOf(e.target);
    if (cell) cellKeydown(view, root, cell, e);
  });
  root.addEventListener('mousedown', (e) => {
    const target = e.target as HTMLElement;
    const link = target.closest<HTMLElement>('[data-href]');
    if (link && !target.closest('[data-editing]')) {
      e.preventDefault();
      deps.openLink(link.dataset.href!);
      return;
    }
    const h = target.closest<HTMLElement>('.pm-table-handle');
    if (h) {
      e.preventDefault();
      dragHandle(view, root, deps, h, e);
      return;
    }
    const add = target.closest<HTMLElement>('.pm-table-add');
    if (add) {
      e.preventDefault();
      const t = locate(view, root);
      if (!t) return;
      const data = tableData(t.model);
      if (add.classList.contains('pm-table-add-row')) {
        replaceTable(view, t, insertRow(data, data.rows.length), {
          row: data.rows.length + 1,
          col: 0,
          caret: 'start',
        });
      } else {
        replaceTable(view, t, insertColumn(data, data.header.length), {
          row: 0,
          col: data.header.length,
          caret: 'start',
        });
      }
    }
  });
  root.addEventListener('contextmenu', (e) => {
    const cell = cellOf(e.target);
    e.preventDefault();
    e.stopPropagation();
    const target = cell ? { row: Number(cell.dataset.row), col: Number(cell.dataset.col) } : {};
    deps.showMenu(tableMenu(view, root, deps, target), { x: e.clientX, y: e.clientY });
  });
}

// ---- state: which tables render as grids -------------------------------------------------------

/** Shows one table (by its start) as markdown until the cursor leaves it. */
const showSource = StateEffect.define<number>();

const sourceTables = StateField.define<readonly number[]>({
  create: () => [],
  update(value, tr) {
    let next = tr.docChanged ? value.map((p) => tr.changes.mapPos(p)) : value;
    for (const e of tr.effects) if (e.is(showSource)) next = [...next, e.value];
    if (next.length && (tr.selection || tr.docChanged)) {
      // Drop tables the cursor has left.
      const tables = tablesIn(tr.state);
      const head = tr.state.selection.main.head;
      next = next.filter((p) => {
        const t = tables.find((x) => x.from === p);
        return t && head >= t.from && head <= t.to;
      });
    }
    return next.length === value.length && next.every((p, i) => p === value[i]) ? value : next;
  },
});

function buildTables(state: EditorState, deps: TableDeps): DecorationSet {
  const raw = state.field(sourceTables);
  const out: Range<Decoration>[] = [];
  for (const t of tablesIn(state)) {
    if (raw.includes(t.from)) continue;
    const source = state.sliceDoc(t.from, t.to);
    if (!parseTable(source)) continue;
    out.push(
      Decoration.replace({ widget: new TableWidget(source, t.from, deps), block: true }).range(
        t.from,
        t.to,
      ),
    );
  }
  return Decoration.set(out, true);
}

/** The editor extension: grid widgets for tables plus arrow-key entry from the lines around them. */
export function tableEditing(deps: TableDeps): Extension {
  const field = StateField.define<DecorationSet>({
    create: (state) => buildTables(state, deps),
    update(deco, tr) {
      if (
        tr.docChanged ||
        tr.startState.field(sourceTables) !== tr.state.field(sourceTables) ||
        syntaxTree(tr.startState) !== syntaxTree(tr.state)
      )
        return buildTables(tr.state, deps);
      return deco;
    },
    provide: (f) => [
      EditorView.decorations.from(f),
      EditorView.atomicRanges.of((v) => v.state.field(f)),
    ],
  });

  const enter = (view: EditorView, dir: 1 | -1): boolean => {
    const { state } = view;
    const sel = state.selection.main;
    if (!sel.empty) return false;
    const line = state.doc.lineAt(sel.head);
    // Only when the cursor would leave its line (wrapped lines move within the line first).
    const moved = view.moveVertically(sel, dir === 1);
    if (
      moved.head !== state.doc.length &&
      moved.head !== 0 &&
      state.doc.lineAt(moved.head).number === line.number
    )
      return false;
    const n = line.number + dir;
    if (n < 1 || n > state.doc.lines) return false;
    const target = state.doc.line(n);
    const t = tablesIn(state).find((x) =>
      dir === 1 ? x.from === target.from : x.to === target.to,
    );
    if (!t || state.field(sourceTables).includes(t.from)) return false;
    const model = parseTable(state.sliceDoc(t.from, t.to));
    if (!model) return false;
    requestFocus(view, {
      table: t.from,
      row: dir === 1 ? 0 : model.rows.length,
      col: 0,
      caret: 'end',
    });
    return true;
  };

  /** Text beside a table is not joined to it: an empty line goes, and the cursor moves into the table. */
  const beside = (view: EditorView, dir: 1 | -1): boolean => {
    const { state } = view;
    const sel = state.selection.main;
    if (!sel.empty) return false;
    const line = state.doc.lineAt(sel.head);
    if (sel.head !== (dir === 1 ? line.to : line.from)) return false;
    const n = line.number + dir;
    if (n < 1 || n > state.doc.lines) return false;
    const target = state.doc.line(n);
    const t = tablesIn(state).find((x) => (dir === 1 ? x.from === target.from : x.to === target.to));
    if (!t || state.field(sourceTables).includes(t.from)) return false;
    const model = parseTable(state.sliceDoc(t.from, t.to));
    if (!model) return false;
    const last = line.number === (dir === 1 ? 1 : state.doc.lines);
    if (!line.length && !(dir === -1 && last))
      view.dispatch({ changes: dir === 1 ? { from: line.from, to: target.from } : { from: t.to, to: line.to }, userEvent: dir === 1 ? 'delete.forward' : 'delete.backward' });
    requestFocus(view, {
      table: dir === 1 && !line.length ? line.from : t.from,
      row: dir === 1 ? 0 : model.rows.length,
      col: dir === 1 ? 0 : model.header.length - 1,
      caret: dir === 1 ? 'start' : 'end',
    });
    return true;
  };

  return [
    sourceTables,
    field,
    Prec.high(
      keymap.of([
        { key: 'Backspace', run: (v) => beside(v, -1) },
        { key: 'Delete', run: (v) => beside(v, 1) },
        { key: 'ArrowDown', run: (v) => enter(v, 1) },
        { key: 'ArrowUp', run: (v) => enter(v, -1) },
      ]),
    ),
  ];
}

export { tablesIn };
