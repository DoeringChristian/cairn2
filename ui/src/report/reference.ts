import { StateField, type EditorState, type Extension, type Text } from '@codemirror/state';
import { keymap, showTooltip, type EditorView, type Tooltip } from '@codemirror/view';
import { copyText } from '../components/useCopyText';
import { icon } from './icons';

/** Where something is in a report and what it reads, for an agent: `name.md:12 "…" [cairn:reports/name]`. */
export type Cite = (doc: Text, from: number, to: number, text?: string) => string;
export const citing =
  (name: string): Cite =>
  (doc, from, to, text) => {
    const source = doc.sliceString(from, to);
    const start = from + source.length - source.trimStart().length;
    const end = Math.max(start, from + source.trimEnd().length);
    return `${name}.md:${doc.lineAt(start).number} "${text ?? doc.sliceString(start, end)}" [cairn:reports/${name}]`;
  };

/** A round button that copies, and shows a tick when it has. */
export function copyButton(label: string, text: () => string) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'image-copy';
  button.setAttribute('aria-label', label);
  button.append(icon('copy'));
  button.onmousedown = (event) => {
    event.preventDefault();
    event.stopPropagation();
  };
  button.onclick = (event) => {
    event.stopPropagation();
    void copyText(text()).then(() => {
      button.replaceChildren(icon('check'));
      button.dataset.copied = '';
      setTimeout(() => {
        button.replaceChildren(icon('copy'));
        delete button.dataset.copied;
      }, 1400);
    });
  };
  return button;
}

export function references(cite: Cite): Extension {
  const tip = (state: EditorState): Tooltip | null => {
    const { main } = state.selection;
    if (main.empty || !state.sliceDoc(main.from, main.to).trim()) return null;
    return {
      pos: main.to,
      create: (view) => {
        const dom = document.createElement('div');
        dom.className = 'md-reference';
        dom.append(copyButton('Copy selected text and source', () => cite(view.state.doc, view.state.selection.main.from, view.state.selection.main.to)));
        return { dom };
      },
    };
  };
  return [
    StateField.define<Tooltip | null>({
      create: tip,
      update: (value, tr) => (tr.docChanged || tr.selection ? tip(tr.state) : value),
      provide: (field) => showTooltip.from(field),
    }),
    keymap.of([
      {
        key: 'Mod-Alt-c',
        run: (view: EditorView) => {
          const { main } = view.state.selection;
          const line = view.state.doc.lineAt(main.head);
          void copyText(main.empty ? cite(view.state.doc, line.from, line.to) : cite(view.state.doc, main.from, main.to));
          return true;
        },
      },
    ]),
  ];
}
