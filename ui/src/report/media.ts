// The handling of figures is Pumice's (plugins/core-editor/src/live-preview.ts): one is an
// element of the text with exactly two places for the cursor, before and after it.
import type { EditorState, Extension } from '@codemirror/state';
import { Prec } from '@codemirror/state';
import { EditorView, keymap, layer, RectangleMarker, ViewPlugin, type DecorationSet, type ViewUpdate } from '@codemirror/view';
import { embedDrag } from './drag';
import { tablesIn } from './table';

export type Span = { from: number; to: number };
/** Where the drawn figures are: within lines, and blocks of whole lines. */
export type Drawn = { atoms: (view: EditorView) => DecorationSet | undefined; blocks: (state: EditorState) => DecorationSet | undefined };

export function mediaHandling({ atoms, blocks }: Drawn): Extension {
  function blockAt(state: EditorState, from: number, to = from): Span | undefined {
    let found: Span | undefined;
    blocks(state)?.between(from, to, (f, t, deco) => {
      if (!found && t > f && deco.spec.media) found = { from: f, to: t };
    });
    return found;
  }
  function embedsOn(view: EditorView, line: Span): Span[] {
    const out: Span[] = [];
    atoms(view)?.between(line.from, line.to, (from, to) => void out.push({ from, to }));
    return out;
  }
  function mediaOnly(view: EditorView, line: Span & { text: string }): boolean {
    const embeds = embedsOn(view, line);
    if (!embeds.length) return false;
    let rest = line.text;
    for (const embed of [...embeds].reverse()) rest = rest.slice(0, embed.from - line.from) + rest.slice(embed.to - line.from);
    return !rest.trim();
  }
  function elementOf(view: EditorView, span: Span): HTMLElement | undefined {
    for (const el of view.contentDOM.querySelectorAll<HTMLElement>('.md-element'))
      try {
        if (view.posAtDOM(el) === span.from) return el;
      } catch {
        // Not (or no longer) part of the text.
      }
    return undefined;
  }
  function atCursor(view: EditorView): { behind?: Span; front?: Span } | undefined {
    const { state } = view;
    const sel = state.selection.main;
    if (!sel.empty) return undefined;
    const { head } = sel;
    const block = blockAt(state, head);
    if (block) return head === block.from ? { front: block } : { behind: block };
    const embeds = embedsOn(view, state.doc.lineAt(head));
    if (!embeds.length) return undefined;
    const blank = (from: number, to: number) => !state.doc.sliceString(from, to).trim();
    let behind = embeds.find((e) => e.to === head);
    let front = embeds.find((e) => e.from === head);
    if (behind && !front) front = embeds.find((e) => e.from > head && blank(head, e.from));
    if (front && !behind) behind = [...embeds].reverse().find((e) => e.to < head && blank(e.to, head));
    return behind || front ? { behind, front } : undefined;
  }

  const turns = new WeakMap<EditorView, { at: number; turn: boolean }>();
  /** The cursor beside a figure, as tall as it: the editor's own has no place there. */
  const caret = layer({
    above: true,
    class: 'md-media-caret-layer',
    update: (update) => update.docChanged || update.selectionSet || update.focusChanged || update.viewportChanged || update.geometryChanged,
    markers(view) {
      const at = view.hasFocus ? atCursor(view) : undefined;
      const box = (span?: Span) => {
        const el = span && elementOf(view, span);
        return (el?.querySelector('.md-sized, .md-card, .native-plotly-card, .native-html-viewer, .md-value') ?? el)?.getBoundingClientRect();
      };
      const [behind, front] = [box(at?.behind), box(at?.front)];
      view.dom.classList.toggle('md-at-media', !!(behind || front));
      if (!behind && !front) return [];
      const top = Math.min(behind?.top ?? Infinity, front?.top ?? Infinity);
      const bottom = Math.max(behind?.bottom ?? -Infinity, front?.bottom ?? -Infinity);
      const x = behind && front && front.left >= behind.right ? (behind.right + front.left) / 2 - 1 : behind ? behind.right + 1 : front!.left - 3;
      const { head } = view.state.selection.main;
      const last = turns.get(view);
      const turn = last && last.at !== head ? !last.turn : (last?.turn ?? false);
      turns.set(view, { at: head, turn });
      const base = view.scrollDOM.getBoundingClientRect();
      return [new RectangleMarker(`md-media-caret ${turn ? 'is-a' : 'is-b'}`, x - base.left + view.scrollDOM.scrollLeft, top - base.top + view.scrollDOM.scrollTop, null, Math.max(view.defaultLineHeight, bottom - top))];
    },
  });

  const moveTo = (view: EditorView, anchor: number) => {
    view.dispatch({ selection: { anchor }, userEvent: 'select' });
    const at = atCursor(view);
    const span = at?.behind ?? at?.front;
    const el = span && elementOf(view, span);
    if (el) el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    else view.dispatch({ effects: EditorView.scrollIntoView(anchor, { y: 'nearest' }) });
    return true;
  };
  const across = (dir: -1 | 1) => (view: EditorView) => {
    const { state } = view;
    const sel = state.selection.main;
    if (!sel.empty) return false;
    const { head } = sel;
    const block = blockAt(state, head);
    if (block) {
      if (dir > 0) return moveTo(view, head < block.to ? block.to : Math.min(state.doc.length, block.to + 1));
      return moveTo(view, head > block.from ? block.from : Math.max(0, block.from - 1));
    }
    const line = state.doc.lineAt(head);
    let at = head;
    if (dir > 0) while (at < line.to && /[ \t]/.test(state.doc.sliceString(at, at + 1))) at++;
    else while (at > line.from && /[ \t]/.test(state.doc.sliceString(at - 1, at))) at--;
    for (const e of embedsOn(view, line)) {
      if (dir > 0 && (head >= e.from || at === e.from) && head < e.to) return moveTo(view, e.to);
      if (dir < 0 && (head <= e.to || at === e.to) && head > e.from) return moveTo(view, e.from);
    }
    return false;
  };
  /** Up and down: onto a figure's line, through its caption, and on. */
  const past =
    (dir: -1 | 1, captions = true) =>
    (view: EditorView) => {
      const { state } = view;
      const sel = state.selection.main;
      if (!sel.empty) return false;
      if (captions && intoCaption(view, dir)) return true;
      const { head } = sel;
      const line = state.doc.lineAt(head);
      const block = blockAt(state, head);
      if (!block && !mediaOnly(view, line) && state.doc.lineAt(view.moveVertically(sel, dir > 0).head).number === line.number) return false;
      const n = (block ? state.doc.lineAt(dir > 0 ? block.to : block.from).number : line.number) + dir;
      if (n < 1 || n > state.doc.lines) return false;
      const target = state.doc.line(n);
      const atStart = block ? head === block.from : head === line.from;
      const onward = blockAt(state, target.from, target.to);
      if (onward) return moveTo(view, atStart ? onward.from : onward.to);
      if (mediaOnly(view, target)) return moveTo(view, atStart ? target.from : target.to);
      if (block || mediaOnly(view, line)) return moveTo(view, atStart ? target.from : dir > 0 ? target.from : target.to);
      return false;
    };
  function intoCaption(view: EditorView, dir: -1 | 1) {
    const { state } = view;
    const { head } = state.selection.main;
    let span: Span | undefined;
    if (dir > 0) {
      const at = atCursor(view);
      span = at?.behind ?? at?.front;
    } else {
      const line = state.doc.lineAt(head);
      const block = blockAt(state, head);
      if (!block && !mediaOnly(view, line) && state.doc.lineAt(view.moveVertically(state.selection.main, false).head).number === line.number) return false;
      const n = (block ? state.doc.lineAt(block.from).number : line.number) - 1;
      if (n < 1) return false;
      const target = state.doc.line(n);
      span = blockAt(state, target.from, target.to) ?? (mediaOnly(view, target) ? embedsOn(view, target).at(-1) : undefined);
    }
    const caption = span && elementOf(view, span)?.querySelector<HTMLElement>('.md-caption:not(.is-empty)');
    if (!caption) return false;
    caption.focus();
    getSelection()?.selectAllChildren(caption);
    getSelection()?.collapseToEnd();
    return true;
  }
  /** Out of a caption with the keys: beside its figure (up), or on under it (down). */
  const leaveCaption = ViewPlugin.fromClass(
    class {
      constructor(readonly view: EditorView) {
        view.dom.addEventListener('cairn-caption', this.leave);
      }
      destroy() {
        this.view.dom.removeEventListener('cairn-caption', this.leave);
      }
      leave = (event: Event) => {
        const { view } = this;
        const { el, dir } = (event as CustomEvent<{ el: HTMLElement; dir: -1 | 0 | 1 }>).detail;
        const pos = view.posAtDOM(el);
        const span = blockAt(view.state, pos) ?? embedsOn(view, view.state.doc.lineAt(pos)).find((e) => e.from <= pos && pos < e.to);
        view.focus();
        if (!span) return;
        moveTo(view, span.to);
        if (dir > 0) past(1, false)(view);
      };
    },
  );

  /** Text typed at a figure's block goes on a line of its own, before or after it. */
  const typed = EditorView.inputHandler.of((view, _from, _to, text) => {
    const sel = view.state.selection.main;
    if (!sel.empty || !text || text.includes('\n')) return false;
    const block = blockAt(view.state, sel.head);
    if (!block) return false;
    const before = sel.head === block.from;
    view.dispatch({
      changes: before ? { from: block.from, insert: `${text}\n` } : { from: block.to, insert: `\n${text}` },
      selection: { anchor: before ? block.from + text.length : block.to + 1 + text.length },
      userEvent: 'input.type',
    });
    return true;
  });
  /** Backspace behind a block, or Delete in front of it, removes it; beside it only empty lines go. */
  const remove = (forward: boolean) => (view: EditorView) => {
    const { state } = view;
    const sel = state.selection.main;
    if (!sel.empty) return false;
    const block = blockAt(state, sel.head);
    if (!block) return false;
    const { doc } = state;
    const userEvent = forward ? 'delete.forward' : 'delete.backward';
    if (forward ? sel.head === block.from : sel.head === block.to) {
      const to = Math.min(doc.length, block.to + 1);
      const from = to === block.to ? Math.max(0, block.from - 1) : block.from;
      view.dispatch({ changes: { from, to }, selection: { anchor: from }, userEvent });
      return true;
    }
    const beyond = forward ? block.to + 1 : block.from - 1;
    if (beyond < 0 || beyond > doc.length) return true;
    const other = doc.lineAt(beyond);
    if (other.length === 0) {
      view.dispatch({ changes: forward ? { from: block.to, to: other.to } : { from: other.from, to: block.from }, userEvent });
      return true;
    }
    return moveTo(view, beyond);
  };
  /** Beside a line of text, a line with a figure on it is not joined to it: the cursor goes there. */
  const joined = (forward: boolean) => (view: EditorView) => {
    const { state } = view;
    const sel = state.selection.main;
    if (!sel.empty) return false;
    const line = state.doc.lineAt(sel.head);
    if (sel.head !== (forward ? line.to : line.from)) return false;
    const n = line.number + (forward ? 1 : -1);
    if (n < 1 || n > state.doc.lines) return false;
    const other = state.doc.line(n);
    if (!line.length || !other.length) return false;
    // A table takes the cursor itself.
    if (tablesIn(state).some((table) => table.from <= other.from && table.to >= other.to)) return false;
    const block = blockAt(state, other.from, other.to);
    if (!block && !mediaOnly(view, line) && !mediaOnly(view, other)) return false;
    return moveTo(view, forward ? (block?.from ?? other.from) : (block?.to ?? other.to));
  };
  const selected = ViewPlugin.fromClass(
    class {
      constructor(view: EditorView) {
        this.mark(view);
      }
      update(update: ViewUpdate) {
        if (update.selectionSet || update.docChanged || update.viewportChanged) update.view.requestMeasure({ key: this, read: () => this.mark(update.view) });
      }
      mark(view: EditorView) {
        const ranges = view.state.selection.ranges.filter((range) => !range.empty);
        for (const el of view.contentDOM.querySelectorAll<HTMLElement>('.md-element')) {
          let on = false;
          try {
            const pos = view.posAtDOM(el);
            on = ranges.some((range) => range.from <= pos && range.to > pos);
          } catch {
            // Not (or no longer) part of the text.
          }
          el.classList.toggle('is-selected', on);
        }
      }
    },
  );
  return [
    caret,
    selected,
    leaveCaption,
    Prec.high(typed),
    Prec.high(
      keymap.of([
        { key: 'ArrowLeft', run: across(-1) },
        { key: 'ArrowRight', run: across(1) },
        { key: 'ArrowUp', run: past(-1) },
        { key: 'ArrowDown', run: past(1) },
        { key: 'Backspace', run: (view) => remove(false)(view) || joined(false)(view) },
        { key: 'Delete', run: (view) => remove(true)(view) || joined(true)(view) },
      ]),
    ),
    embedDrag((view, el) => {
      const from = view.posAtDOM(el);
      return embedsOn(view, view.state.doc.lineAt(from)).find((e) => e.from === from) ?? { from, to: from };
    }),
  ];
}
