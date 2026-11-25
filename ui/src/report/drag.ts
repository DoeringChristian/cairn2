// From Pumice (plugins/core-editor/src/drag-embed.ts).
import type { Extension } from '@codemirror/state';
import { EditorView, ViewPlugin } from '@codemirror/view';

/**
 * Moving a picture or video within its note by dragging it: its text (`![[…]]`, with its
 * label) is taken from where it stood and written where it is dropped. Nothing is copied.
 */

export interface EmbedMove {
  /** One change of the note's text. */
  change: { from: number; to: number; insert: string };
  /** Where the embed starts afterwards. */
  at: number;
}

const blank = (line: string | undefined) => line === undefined || line.trim() === '';

function oneChange(a: string, b: string): { from: number; to: number; insert: string } | null {
  if (a === b) return null;
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) (endA--, endB--);
  return { from: start, to: endA, insert: b.slice(start, endB) };
}

/** Where a dragged embed goes: a paragraph of its own before or after a line, or into a line. */
export type EmbedDrop = 'before' | 'after' | 'inline';

/**
 * The text of a note with the embed at `range` moved to `pos`: as a paragraph of its own
 * before or after the line `pos` is in, or into that line at `pos` (beside another picture:
 * a row). Where it stood nothing is left behind: its line goes with it if it had it to
 * itself, and no two empty lines remain.
 */
export function moveEmbed(
  text: string,
  range: { from: number; to: number },
  pos: number,
  how: EmbedDrop,
): EmbedMove | null {
  const lines = text.split('\n');
  const lineAt = (offset: number) => {
    let start = 0;
    for (let i = 0; i < lines.length; i++) {
      if (offset <= start + lines[i]!.length) return { index: i, start };
      start += lines[i]!.length + 1;
    }
    return { index: lines.length - 1, start: text.length - lines[lines.length - 1]!.length };
  };
  const source = lineAt(range.from);
  const target = lineAt(pos);
  const line = lines[source.index]!;
  const from = range.from - source.start;
  const to = range.to - source.start;
  const rest = line.slice(0, from) + line.slice(to);
  const alone = rest.trim() === '';
  const moved = text.slice(range.from, range.to);
  if (target.index === source.index) {
    if (alone || how !== 'inline') return alone ? null : moveWithin();
    if (pos >= range.from && pos <= range.to) return null;
  }

  const out = [...lines];
  let ti = target.index;
  let col = pos - target.start;
  if (alone) {
    out.splice(source.index, 1);
    if (ti > source.index) ti--;
    // No two empty lines where it stood (and none at the very top).
    if (blank(out[source.index - 1]) && blank(out[source.index]) && source.index < out.length) {
      out.splice(source.index, 1);
      if (ti > source.index) ti--;
      else if (ti === source.index) ti = Math.max(0, source.index - 1);
    }
  } else {
    // One of the spaces around it goes with it.
    let a = from;
    let b = to;
    if (line[b] === ' ') b++;
    else if (line[a - 1] === ' ') a--;
    out[source.index] = (line.slice(0, a) + line.slice(b)).replace(/[ \t]+$/, '');
    if (target.index === source.index && col >= b) col -= b - a;
  }
  ti = Math.max(0, Math.min(ti, out.length - 1));

  if (how === 'inline' && out.length) {
    const l = out[ti]!;
    col = Math.max(0, Math.min(col, l.length));
    const before = l.slice(0, col);
    const behind = l.slice(col);
    out[ti] =
      before +
      (before && !/\s$/.test(before) ? ' ' : '') +
      moved +
      (behind && !/^\s/.test(behind) ? ' ' : '') +
      behind;
  } else {
    let k = out.length === 0 ? 0 : how === 'after' ? ti + 1 : ti;
    // Onto an empty line: in its place.
    if (out.length && blank(out[ti])) k = ti;
    const block = [moved];
    if (k > 0 && !blank(out[k - 1])) block.unshift('');
    if (k < out.length && !blank(out[k])) block.push('');
    out.splice(k, 0, ...block);
  }
  return finish(out.join('\n'));

  /** Out of a row into a paragraph of its own, before or after the row it was in. */
  function moveWithin(): EmbedMove | null {
    let a = from;
    let b = to;
    if (line[b] === ' ') b++;
    else if (line[a - 1] === ' ') a--;
    const left = (line.slice(0, a) + line.slice(b)).replace(/[ \t]+$/, '');
    const next = [...lines];
    const block = how === 'before' ? [moved, '', left] : [left, '', moved];
    next.splice(source.index, 1, ...block);
    return finish(next.join('\n'));
  }

  function finish(next: string): EmbedMove | null {
    const change = oneChange(text, next);
    if (!change) return null;
    // Where it is now: the occurrence nearest to where it was dropped.
    let at = next.indexOf(moved);
    for (let i = at; i >= 0; i = next.indexOf(moved, i + 1))
      if (Math.abs(i - pos) < Math.abs(at - pos)) at = i;
    return { change, at };
  }
}

/** The embed that is being dragged, and the editor it is in. */
let dragged: { view: EditorView; el: HTMLElement } | undefined;

/** `dragstart` on an embed: it can be dropped elsewhere in its note. */
export function beginEmbedDrag(view: EditorView, el: HTMLElement, e: DragEvent): void {
  const target = e.target as HTMLElement | null;
  // What is dragged for its own sake: the size, the place in a video, the text of a caption.
  if (
    view.state.readOnly ||
    target?.closest?.(
      '.md-resize, .md-caption, .prism-inline-transport, input',
    )
  ) {
    if (target?.closest?.('.md-resize, .prism-inline-transport, input')) e.preventDefault();
    return;
  }
  dragged = { view, el };
  el.classList.add('is-dragging');
  e.dataTransfer?.setData('application/x-cairn-figure', '1');
  if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
  const end = () => {
    el.classList.remove('is-dragging');
    el.removeEventListener('dragend', end);
    // After a drop that is handled here.
    setTimeout(() => {
      if (dragged?.el === el) dragged = undefined;
    });
  };
  el.addEventListener('dragend', end);
}

/** Where dragged embeds can be dropped: `rangeOf` says where an embed is written. */
export function embedDrag(
  rangeOf: (view: EditorView, el: HTMLElement) => { from: number; to: number },
): Extension {
  return ViewPlugin.fromClass(
    class {
      private readonly mark = document.createElement('div');
      private readonly over = (e: DragEvent) => {
        const drop = this.place(e);
        if (!drop) return this.mark.remove();
        e.preventDefault();
        e.stopPropagation();
        if (e.dataTransfer) e.dataTransfer.dropEffect = 'move';
        this.show(drop);
      };
      private readonly leave = (e: DragEvent) => {
        if (!this.view.dom.contains(e.relatedTarget as Node | null)) this.mark.remove();
      };
      private readonly drop = (e: DragEvent) => {
        const drop = this.place(e);
        this.mark.remove();
        if (!drop || !dragged) return;
        e.preventDefault();
        e.stopPropagation();
        const { el } = dragged;
        dragged = undefined;
        el.classList.remove('is-dragging');
        const move = moveEmbed(
          this.view.state.doc.toString(),
          rangeOf(this.view, el),
          drop.pos,
          drop.how,
        );
        if (!move) return;
        this.view.dispatch({
          changes: move.change,
          selection: { anchor: move.at },
          userEvent: 'move.drop',
        });
        this.view.focus();
      };

      constructor(readonly view: EditorView) {
        this.mark.className = 'md-drop-mark';
        // Before the editor's own handling of drops (and inside what is drawn, where it has none).
        view.dom.addEventListener('dragover', this.over, true);
        view.dom.addEventListener('dragleave', this.leave, true);
        view.dom.addEventListener('drop', this.drop, true);
      }

      destroy() {
        this.mark.remove();
        this.view.dom.removeEventListener('dragover', this.over, true);
        this.view.dom.removeEventListener('dragleave', this.leave, true);
        this.view.dom.removeEventListener('drop', this.drop, true);
      }

      /**
       * Where a drop at the pointer would put the embed. On the left or right third of
       * another picture: beside it, in its line (a row). Above or below its middle, and on
       * text: a paragraph of its own before or after that line.
       */
      private place(e: DragEvent) {
        if (!dragged || dragged.view !== this.view || this.view.state.readOnly) return null;
        const { view } = this;
        const range = rangeOf(view, dragged.el);
        const other = (e.target as HTMLElement | null)?.closest?.<HTMLElement>('.md-embed');
        if (other === dragged.el) return null;
        if (other && view.contentDOM.contains(other)) {
          const box = other.getBoundingClientRect();
          const x = (e.clientX - box.left) / Math.max(1, box.width);
          const at = rangeOf(view, other);
          if (x < 0.3 || x > 0.7) {
            const after = x > 0.7;
            // (Beside what it already stands beside: nothing to do.)
            const pos = after ? at.to : at.from;
            const gap = view.state.doc.sliceString(
              Math.min(pos, range.from),
              Math.max(pos, range.to),
            );
            if (gap.replace(view.state.doc.sliceString(range.from, range.to), '').trim() === '')
              return null;
            return { pos, how: 'inline' as const, edge: after ? box.right : box.left, box };
          }
          const below = e.clientY > box.top + box.height / 2;
          return {
            pos: at.from,
            how: below ? ('after' as const) : ('before' as const),
            y: below ? box.bottom : box.top,
          };
        }
        const pos = view.posAtCoords({ x: e.clientX, y: e.clientY }, false);
        const block = view.lineBlockAt(pos);
        const top = view.documentTop + block.top;
        const below = e.clientY > top + block.height / 2;
        // Before or after the line it has to itself: where it is.
        const line = view.state.doc.lineAt(range.from);
        const rest =
          line.text.slice(0, range.from - line.from) + line.text.slice(range.to - line.from);
        if (
          rest.trim() === '' &&
          view.state.doc.lineAt(pos).number === line.number
        )
          return null;
        return {
          pos,
          how: below ? ('after' as const) : ('before' as const),
          y: below ? top + block.height : top,
        };
      }

      private show(drop: NonNullable<ReturnType<typeof this.place>>) {
        const style = this.mark.style;
        if (drop.how === 'inline') {
          style.left = `${drop.edge - 1}px`;
          style.top = `${drop.box.top}px`;
          style.width = '3px';
          style.height = `${drop.box.height}px`;
        } else {
          const content = this.view.contentDOM.getBoundingClientRect();
          style.left = `${content.left}px`;
          style.top = `${drop.y - 1}px`;
          style.width = `${content.width}px`;
          style.height = '3px';
        }
        if (!this.mark.isConnected) document.body.append(this.mark);
      }
    },
  );
}
