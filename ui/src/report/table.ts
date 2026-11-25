import type { EditorState } from '@codemirror/state';
import { EditorView, WidgetType } from '@codemirror/view';
import { yUndoManagerKeymap } from 'y-codemirror.next';
import { cells, cellSource, cellText } from './markdown';
import { inline } from './tex';

const written = (cell: HTMLElement) => cell.innerText.replace(/\u00a0/g, ' ').replace(/\s*\n\s*/g, ' ').trim();
type Place = { row: number; column: number };

/** Lines of the table that starts at a position, without the line of dashes. */
function rowsAt(state: EditorState, from: number) {
  const first = state.doc.lineAt(from);
  const rows = [first];
  for (let n = first.number + 2; n <= state.doc.lines; n++) {
    const line = state.doc.line(n);
    if (!line.text.includes('|')) break;
    rows.push(line);
  }
  return rows;
}

/** A table drawn as a grid whose cells are edited in place. */
export class TableWidget extends WidgetType {
  constructor(
    readonly source: string,
    readonly onSource: (view: EditorView, dom: HTMLElement) => void,
    readonly onLink: (href: string) => void,
  ) {
    super();
  }
  eq(other: TableWidget) {
    return other.source === this.source;
  }
  ignoreEvent() {
    return true;
  }
  toDOM(view: EditorView) {
    const dom = document.createElement('div');
    dom.className = 'md-table';
    this.fill(dom, view);
    return dom;
  }
  updateDOM(dom: HTMLElement, view: EditorView) {
    const table = dom.querySelector('table')!;
    const focused = document.activeElement instanceof HTMLTableCellElement && dom.contains(document.activeElement) ? document.activeElement : null;
    const lines = this.lines();
    const same = table.rows.length === lines.length && lines.every((line, i) => table.rows[i].cells.length === line.length);
    if (!same) {
      const place = focused && this.place(focused);
      this.fill(dom, view);
      if (place) this.focus(dom, place);
      return true;
    }
    lines.forEach((line, i) =>
      line.forEach((text, j) => {
        const cell = table.rows[i].cells[j];
        if (cell.dataset.text === text) return;
        cell.dataset.text = text;
        if (cell === focused) {
          if (written(cell) !== text) cell.textContent = text;
        } else cell.replaceChildren(inline(text));
      }),
    );
    return true;
  }
  private lines() {
    const lines = this.source.split('\n');
    const rows = [lines[0], ...lines.slice(2)].map((line) => cells(line).map(({ from, to }) => cellText(line.slice(from, to))));
    return rows.map((row) => Array.from({ length: rows[0].length }, (_, i) => row[i] ?? ''));
  }
  private place(cell: HTMLTableCellElement): Place {
    return { row: (cell.parentElement as HTMLTableRowElement).rowIndex, column: cell.cellIndex };
  }
  private focus(dom: HTMLElement, { row, column }: Place) {
    const rows = dom.querySelector('table')!.rows;
    const cell = rows[Math.min(row, rows.length - 1)]?.cells[column];
    if (!cell) return;
    cell.focus();
    const selection = getSelection();
    selection?.selectAllChildren(cell);
    selection?.collapseToEnd();
  }
  private fill(dom: HTMLElement, view: EditorView) {
    const aligns = cells(this.source.split('\n')[1] || '').map(({ from, to }) => {
      const dashes = this.source.split('\n')[1].slice(from, to);
      return dashes.endsWith(':') ? (dashes.startsWith(':') ? 'center' : 'right') : '';
    });
    const table = document.createElement('table');
    this.lines().forEach((line, i) => {
      const row = table.insertRow();
      line.forEach((text, j) => {
        const cell = document.createElement(i ? 'td' : 'th');
        cell.contentEditable = view.state.readOnly ? 'false' : 'true';
        cell.dataset.text = text;
        cell.style.textAlign = aligns[j] || '';
        cell.append(inline(text));
        row.append(cell);
      });
    });
    const bar = document.createElement('div');
    bar.className = 'md-table-bar';
    const button = (label: string, title: string, run: (place: Place) => void) => {
      const made = document.createElement('button');
      made.type = 'button';
      made.textContent = label;
      made.title = title;
      made.setAttribute('aria-label', title);
      made.onmousedown = (event) => event.preventDefault();
      made.onclick = () => {
        const cell = document.activeElement instanceof HTMLTableCellElement && dom.contains(document.activeElement) ? document.activeElement : null;
        run(cell ? this.place(cell) : { row: table.rows.length - 1, column: table.rows[0].cells.length - 1 });
      };
      bar.append(made);
    };
    button('+ row', 'Add a row below', ({ row, column }) => this.addRow(dom, view, row, column));
    button('− row', 'Remove this row', (place) => this.removeRow(dom, view, place));
    button('+ column', 'Add a column after', ({ row, column }) => this.columns(dom, view, column, true, row));
    button('− column', 'Remove this column', ({ row, column }) => this.columns(dom, view, column, false, row));
    button('</>', 'Edit source', () => this.onSource(view, dom));
    table.addEventListener('focusin', (event) => {
      const cell = event.target as HTMLTableCellElement;
      // While it is edited, a cell shows its Markdown.
      if (cell.textContent === cell.dataset.text) return;
      cell.textContent = cell.dataset.text!;
      const end = () => document.activeElement === cell && (getSelection()?.selectAllChildren(cell), getSelection()?.collapseToEnd());
      end();
      requestAnimationFrame(end);
    });
    table.addEventListener('focusout', (event) => {
      const cell = event.target as HTMLTableCellElement;
      cell.replaceChildren(inline(cell.dataset.text!));
    });
    table.addEventListener('input', (event) => {
      const cell = event.target as HTMLTableCellElement;
      const { row, column } = this.place(cell);
      const line = rowsAt(view.state, view.posAtDOM(dom))[row];
      const at = cells(line.text)[column];
      if (!at) return;
      cell.dataset.text = written(cell);
      const was = line.text.slice(at.start, at.end);
      const now = ` ${cellSource(cell.dataset.text)} `;
      // Only what differs, so that others may write in the same cell.
      let front = 0;
      while (front < Math.min(was.length, now.length) && was[front] === now[front]) front++;
      let back = 0;
      while (back < Math.min(was.length, now.length) - front && was[was.length - 1 - back] === now[now.length - 1 - back]) back++;
      view.dispatch({ changes: { from: line.from + at.start + front, to: line.from + at.end - back, insert: now.slice(front, now.length - back) }, userEvent: 'input.type' });
    });
    table.addEventListener('keydown', (event) => {
      const cell = event.target as HTMLTableCellElement;
      const { row, column } = this.place(cell);
      const { rows } = table;
      const go = (place: Place) => {
        event.preventDefault();
        this.focus(dom, place);
      };
      event.stopPropagation();
      if (event.key === 'Tab') {
        const next = row * rows[0].cells.length + column + (event.shiftKey ? -1 : 1);
        if (next < 0) return;
        if (next >= rows.length * rows[0].cells.length) {
          event.preventDefault();
          this.addRow(dom, view, row, 0);
        } else go({ row: Math.floor(next / rows[0].cells.length), column: next % rows[0].cells.length });
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (row === rows.length - 1) this.addRow(dom, view, row, column);
        else go({ row: row + 1, column });
      } else if (event.key === 'ArrowUp' && row > 0) go({ row: row - 1, column });
      else if (event.key === 'ArrowDown' && row < rows.length - 1) go({ row: row + 1, column });
      else if (event.key === 'Escape' || event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const lines = rowsAt(view.state, view.posAtDOM(dom));
        const after = event.key !== 'ArrowUp';
        const anchor = after ? Math.min(view.state.doc.length, lines.at(-1)!.to + 1) : Math.max(0, lines[0].from - 1);
        view.focus();
        view.dispatch({ selection: { anchor }, scrollIntoView: true });
      } else if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault();
        yUndoManagerKeymap.find((item) => item.key === (event.shiftKey ? 'Mod-Shift-z' : 'Mod-z'))?.run?.(view);
      }
    });
    table.addEventListener('mousedown', (event) => {
      const link = (event.target as Element).closest<HTMLElement>('[data-href]');
      if (link) {
        event.preventDefault();
        this.onLink(link.dataset.href!);
      }
    });
    dom.replaceChildren(table, bar);
  }
  private addRow(dom: HTMLElement, view: EditorView, row: number, column: number) {
    const lines = rowsAt(view.state, view.posAtDOM(dom));
    const width = cells(lines[0].text).length;
    // The line of dashes stays below the first row.
    const after = row ? lines[row] : view.state.doc.line(lines[0].number + 1);
    view.dispatch({ changes: { from: after.to, insert: `\n|${'  |'.repeat(width)}` }, userEvent: 'input' });
    this.focus(dom, { row: row + 1, column });
  }
  private removeRow(dom: HTMLElement, view: EditorView, { row, column }: Place) {
    const lines = rowsAt(view.state, view.posAtDOM(dom));
    if (!row) return;
    view.dispatch({ changes: { from: lines[row].from - 1, to: lines[row].to }, userEvent: 'delete' });
    this.focus(dom, { row: row - 1, column });
  }
  private columns(dom: HTMLElement, view: EditorView, column: number, add: boolean, row: number) {
    const lines = rowsAt(view.state, view.posAtDOM(dom));
    const all = [lines[0], view.state.doc.line(lines[0].number + 1), ...lines.slice(1)];
    if (!add && cells(lines[0].text).length < 2) return;
    const changes = all.map((line, i) => {
      const parts = cells(line.text).map(({ from, to }) => line.text.slice(from, to));
      if (add) parts.splice(column + 1, 0, i === 1 ? '---' : '');
      else parts.splice(column, 1);
      return { from: line.from, to: line.to, insert: `| ${parts.join(' | ')} |` };
    });
    view.dispatch({ changes, userEvent: add ? 'input' : 'delete' });
    this.focus(dom, { row, column: add ? column + 1 : Math.max(0, column - 1) });
  }
}
