// From Pumice (packages/api/src/editor-reveal.ts).
import {
  StateEffect,
  StateField,
  type EditorState,
  type Extension,
} from '@codemirror/state';
import { EditorView, ViewPlugin, type ViewUpdate } from '@codemirror/view';

/**
 * Which lines live preview shows as raw markdown: the lines the selection touches while the
 * editor has focus.
 *
 * While a mouse button is held the revealed lines stay frozen. Revealing syntax changes the
 * layout, and CodeMirror re-reads the pointer position against the new layout on mouse move and
 * mouse up; with the layout frozen, a click or drag lands exactly where the user pointed, and
 * the clicked line opens up once the button is released.
 */
interface RevealState {
  focused: boolean;
  /** Ranges frozen at mousedown, or null when the pointer isn't held. */
  held: ReadonlyArray<{ from: number; to: number }> | null;
}

/** What shows as source right now: the selection, or what it was when the pointer went down. */
function revealRanges(
  state: EditorState,
  reveal: RevealState,
): ReadonlyArray<{ from: number; to: number }> {
  if (reveal.held) return reveal.held;
  if (reveal.focused) return state.selection.ranges;
  return [];
}

export const setFocused = StateEffect.define<boolean>();
const setHeld = StateEffect.define<boolean>();

const revealField = StateField.define<RevealState>({
  create: () => ({ focused: false, held: null }),
  update(value, tr) {
    let { focused, held } = value;
    if (held && tr.docChanged) {
      held = held.map((r) => ({
        from: tr.changes.mapPos(r.from, -1),
        to: tr.changes.mapPos(r.to, 1),
      }));
    }
    for (const e of tr.effects) {
      if (e.is(setFocused)) focused = e.value;
      else if (e.is(setHeld)) {
        held = e.value
          ? focused
            ? tr.startState.selection.ranges.map((r) => ({ from: r.from, to: r.to }))
            : []
          : null;
      }
    }
    return focused === value.focused && held === value.held ? value : { focused, held };
  },
});

const pointer = EditorView.domEventHandlers({
  mousedown(event, view) {
    if (event.button !== 0 || event.defaultPrevented) return false;
    // A press whose release was never seen (it ended in a frame, or on what was taken out of
    // the page while it was dragged) must not keep the lines as they were for good.
    if (view.state.field(revealField).held) view.dispatch({ effects: setHeld.of(false) });
    view.dispatch({ effects: setHeld.of(true) });
    const win = view.dom.ownerDocument.defaultView ?? window;
    const release = () => {
      win.removeEventListener('mouseup', release, true);
      win.removeEventListener('dragend', release, true);
      win.removeEventListener('blur', release);
      // After CodeMirror's own mouseup handler has made the final selection.
      setTimeout(() => {
        if (view.state.field(revealField, false)?.held)
          view.dispatch({ effects: setHeld.of(false) });
      });
    };
    win.addEventListener('mouseup', release, true);
    win.addEventListener('dragend', release, true);
    win.addEventListener('blur', release);
    return false;
  },
  // No button is down any more, or something is typed: the press is over, seen or not.
  mousemove(event, view) {
    if (!event.buttons && view.state.field(revealField, false)?.held)
      view.dispatch({ effects: setHeld.of(false) });
    return false;
  },
  keydown(_event, view) {
    if (view.state.field(revealField, false)?.held) view.dispatch({ effects: setHeld.of(false) });
    return false;
  },
});

/**
 * Keeps what is known about the focus true. The editor only tells of a change of focus, and
 * the field starts out unfocused: one made while the editor has the focus (another note is
 * opened in it, the way notes are shown is switched) would never hear of it, and nothing
 * would show as source until the editor is left and entered again.
 */
const focusSync = ViewPlugin.fromClass(
  class {
    private pending = false;
    constructor(readonly view: EditorView) {
      this.check();
    }
    update(_update: ViewUpdate) {
      this.check();
    }
    private check() {
      if (this.pending) return;
      this.pending = true;
      queueMicrotask(() => {
        this.pending = false;
        const view = this.view;
        const known = view.state.field(revealField, false);
        if (known && known.focused !== view.hasFocus)
          view.dispatch({ effects: setFocused.of(view.hasFocus) });
      });
    }
  },
);

/** Tracks focus and pointer state for {@link revealedLines}; the markdown editor installs it. */
export const revealTracking: Extension = [
  revealField,
  pointer,
  focusSync,
  EditorView.focusChangeEffect.of((_state, focusing) => setFocused.of(focusing)),
];

/** Line numbers (1-based) whose markdown syntax live preview shows. */
export function revealedLines(state: EditorState): Set<number> {
  const lines = new Set<number>();
  const reveal = state.field(revealField, false);
  if (!reveal) return lines;
  const ranges = revealRanges(state, reveal);
  for (const r of ranges) {
    const first = state.doc.lineAt(Math.min(r.from, state.doc.length)).number;
    const last = state.doc.lineAt(Math.min(r.to, state.doc.length)).number;
    for (let n = first; n <= last; n++) lines.add(n);
  }
  return lines;
}

/**
 * Whether the cursor (or a selection) is in or right at `from`..`to`: what is drawn in place of
 * that piece of source gives way to the source then. Finer than {@link revealedLines}: the rest
 * of the line stays drawn.
 */
export function revealedAt(state: EditorState, from: number, to: number): boolean {
  const reveal = state.field(revealField, false);
  if (!reveal) return false;
  const ranges = revealRanges(state, reveal);
  return ranges.some((r) => r.from <= to && r.to >= from);
}

/** Whether a transaction may have changed {@link revealedLines}. */
export function revealChanged(startState: EditorState, state: EditorState): boolean {
  const a = startState.field(revealField, false);
  const b = state.field(revealField, false);
  if (a !== b) return true;
  return !b?.held && startState.selection !== state.selection;
}
