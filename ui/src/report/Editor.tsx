import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap, type CompletionSource } from '@codemirror/autocomplete';
import { defaultKeymap, indentLess, indentMore } from '@codemirror/commands';
import { json } from '@codemirror/lang-json';
import { markdown, markdownLanguage } from '@codemirror/lang-markdown';
import { python } from '@codemirror/lang-python';
import { yaml } from '@codemirror/lang-yaml';
import { HighlightStyle, syntaxHighlighting, syntaxTree } from '@codemirror/language';
import { search, searchKeymap } from '@codemirror/search';
import { EditorState, Prec, type ChangeSpec } from '@codemirror/state';
import { drawSelection, EditorView, keymap, type Command } from '@codemirror/view';
import { tags } from '@lezer/highlight';
import { Code2 } from 'lucide-react';
import { yCollab, yUndoManagerKeymap } from 'y-codemirror.next';
import type { WebsocketProvider } from 'y-websocket';
import * as Y from 'yjs';
import { math } from './markdown';
import { focusEffect, preview, type Element, type Mounts } from './preview';

export type Heading = { title: string; level: number; from: number };
const code = HighlightStyle.define([
  { tag: tags.keyword, color: 'var(--accent)' },
  { tag: [tags.string, tags.special(tags.string)], color: 'var(--warn)' },
  { tag: tags.comment, color: 'var(--muted)', fontStyle: 'italic' },
  { tag: [tags.number, tags.bool, tags.null], color: '#637fa1' },
  { tag: [tags.processingInstruction, tags.url, tags.contentSeparator, tags.labelName], color: 'var(--muted)' },
]);
const languages: Record<string, ReturnType<typeof python>> = { python: python(), py: python(), json: json(), yaml: yaml(), yml: yaml(), view: yaml(), card: yaml() };

/** Puts marks around the selection, or takes them away. */
const around =
  (mark: string): Command =>
  ({ state, dispatch }) => {
    const { from, to } = state.selection.main;
    const n = mark.length;
    const has = state.sliceDoc(from - n, from) === mark && state.sliceDoc(to, to + n) === mark;
    const changes: ChangeSpec[] = has
      ? [{ from: from - n, to: from }, { from: to, to: to + n }]
      : [{ from, insert: mark }, { from: to, insert: mark }];
    const shift = has ? -n : n;
    dispatch(state.update({ changes, selection: { anchor: from + shift, head: to + shift }, userEvent: 'input' }));
    return true;
  };
const inList = (view: EditorView) => /^\s*(?:[-*+]|\d+[.)])\s/.test(view.state.doc.lineAt(view.state.selection.main.head).text);
const keys = keymap.of([
  { key: 'Mod-b', run: around('**') },
  { key: 'Mod-i', run: around('*') },
  { key: 'Mod-e', run: around('`') },
  { key: 'Tab', run: (view) => inList(view) && indentMore(view) },
  { key: 'Shift-Tab', run: (view) => inList(view) && indentLess(view) },
]);
function headings(state: EditorState): Heading[] {
  const found: Heading[] = [];
  syntaxTree(state).topNode.cursor().iterate((node) => {
    const match = /^(?:ATX|Setext)Heading(\d)$/.exec(node.name);
    if (match) found.push({ level: Number(match[1]), from: node.from, title: state.sliceDoc(node.from, node.to).split('\n')[0].replace(/^#+\s*|\s*#+$/g, '').replace(/[*_`~]/g, '') });
    return node.name === 'Document';
  });
  return found;
}

/** A note edited in place: formatted text, and drawn elements filled in by the page. */
export function Editor({
  text,
  provider,
  render,
  complete,
  follow,
  onHeadings,
  onView,
}: {
  text: Y.Text;
  provider: WebsocketProvider;
  render: (element: Element) => ReactNode;
  complete: CompletionSource;
  follow: (href: string) => void;
  onHeadings: (found: Heading[]) => void;
  onView: (view: EditorView) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef({ complete, follow, onHeadings });
  latest.current = { complete, follow, onHeadings };
  const [mounted, setMounted] = useState(new Map<HTMLElement, { element: Element; edit: () => void; key: number }>());
  useEffect(() => {
    let count = 0;
    let told = '';
    const resize = new ResizeObserver(() => view.requestMeasure());
    const mounts: Mounts = {
      add: (dom, element, edit) => {
        resize.observe(dom);
        setMounted((current) => new Map(current).set(dom, { element, edit, key: count++ }));
      },
      remove: (dom) => {
        resize.unobserve(dom);
        setMounted((current) => {
          const next = new Map(current);
          next.delete(dom);
          return next;
        });
      },
      follow: (href) => latest.current.follow(href),
    };
    const tell = (state: EditorState) => {
      const found = headings(state);
      if (JSON.stringify(found) !== told) {
        told = JSON.stringify(found);
        latest.current.onHeadings(found);
      }
    };
    const undo = new Y.UndoManager(text);
    const view = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: text.toString(),
        extensions: [
          markdown({ base: markdownLanguage, extensions: [math], codeLanguages: (info) => languages[info.split(/\s/)[0].toLowerCase()]?.language ?? null }),
          syntaxHighlighting(code),
          EditorView.lineWrapping,
          drawSelection(),
          preview(mounts),
          search({ top: true }),
          closeBrackets(),
          autocompletion({ override: [(context) => latest.current.complete(context)], icons: false }),
          Prec.high(keys),
          keymap.of([...yUndoManagerKeymap, ...closeBracketsKeymap, ...completionKeymap, ...searchKeymap, ...defaultKeymap]),
          EditorView.focusChangeEffect.of((_state, focusing) => focusEffect.of(focusing)),
          EditorView.contentAttributes.of({ 'aria-label': 'Report text', spellcheck: 'true' }),
          EditorView.updateListener.of((update) => {
            if (update.docChanged || syntaxTree(update.state) !== syntaxTree(update.startState)) tell(update.state);
          }),
          yCollab(text, provider.awareness, { undoManager: undo }),
        ],
      }),
    });
    tell(view.state);
    onView(view);
    return () => {
      view.destroy();
      undo.destroy();
      resize.disconnect();
    };
  }, [text, provider]);
  return (
    <>
      <div ref={host} className="report-editor" />
      {[...mounted].map(([dom, { element, edit, key }]) =>
        createPortal(
          <>
            {render(element)}
            <button type="button" className="md-element-source" aria-label="Edit source" title="Edit source" onMouseDown={(event) => event.preventDefault()} onClick={edit}>
              <Code2 size={15} aria-hidden="true" />
            </button>
          </>,
          dom,
          String(key),
        ),
      )}
    </>
  );
}
