import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react';
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
import { AlignCenter, AlignLeft, AlignRight, ArrowDown, ArrowDownAZ, ArrowDownZA, ArrowLeft, ArrowRight, ArrowUp, Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Code2, Copy, MoveHorizontal, Table, Trash2, type LucideIcon } from 'lucide-react';
import { yCollab, yUndoManagerKeymap } from 'y-codemirror.next';
import type { WebsocketProvider } from 'y-websocket';
import * as Y from 'yjs';
import { math, tableEnds } from './markdown';
import { listEditing } from './lists';
import { preview, type Actions, type Element, type Mounts } from './preview';
import { references, type Cite } from './reference';
import { tableEditing, type MenuEntry } from './table';
import { inline } from './tex';
import { copyText } from '../components/useCopyText';

export type Heading = { title: string; level: number; from: number };
const code = HighlightStyle.define([
  { tag: [tags.keyword, tags.bool, tags.null, tags.self], color: '#a1d9c0' },
  { tag: [tags.string, tags.special(tags.string), tags.propertyName], color: '#e1c894' },
  { tag: tags.comment, color: '#91a397', fontStyle: 'italic' },
  { tag: [tags.number, tags.typeName, tags.className], color: '#b8cde4' },
  { tag: [tags.definition(tags.variableName), tags.function(tags.variableName), tags.function(tags.propertyName)], color: '#e6bd9e' },
  { tag: [tags.meta, tags.annotation], color: '#c5b6dc' },
]);
const syntax = HighlightStyle.define([{ tag: [tags.processingInstruction, tags.contentSeparator, tags.labelName], color: 'var(--muted)' }], { scope: markdownLanguage });
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

function Controls({ actions }: { actions: Actions }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1400);
    return () => clearTimeout(timer);
  }, [copied]);
  const keep = (event: MouseEvent) => event.preventDefault();
  return (
    <>
      <button type="button" className="report-block-edit" aria-label="Edit source" title="Edit source" onMouseDown={keep} onClick={actions.edit}>
        <Code2 aria-hidden="true" />
      </button>
      <button type="button" className="image-copy md-copy" aria-label="Copy reference" onMouseDown={keep} onClick={() => { actions.copy(); setCopied(true); }}>
        {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
      </button>
    </>
  );
}
const icons: Record<string, LucideIcon> = {
  'arrow-up': ArrowUp, 'arrow-down': ArrowDown, 'arrow-left': ArrowLeft, 'arrow-right': ArrowRight, 'chevron-up': ChevronUp, 'chevron-down': ChevronDown, 'chevron-left': ChevronLeft, 'chevron-right': ChevronRight,
  'align-left': AlignLeft, 'align-center': AlignCenter, 'align-right': AlignRight, 'arrow-down-az': ArrowDownAZ, 'arrow-down-za': ArrowDownZA, trash: Trash2, copy: Copy, table: Table, code: Code2, width: MoveHorizontal,
};
function figureMenu(element: Element, actions: Actions): MenuEntry[] {
  const sizes: MenuEntry[] = element.kind === 'figure' && element.place === 'line'
    ? [...[['Full width', 100], ['Three quarters', 75], ['Half width', 50], ['A third', 33]].map(([label, width]): MenuEntry => ({ label: String(label), icon: 'width', run: () => actions.size({ width: Number(width) }) })), 'separator']
    : [];
  return [
    ...sizes,
    { label: 'Copy reference', icon: 'copy', run: actions.copy },
    { label: 'Edit source', icon: 'code', run: actions.edit },
    'separator',
    { label: 'Remove', icon: 'trash', danger: true, run: actions.remove },
  ];
}
/** The menu of a figure, or of a table's row or column. */
function Menu({ entries, x, y, onClose }: { entries: MenuEntry[]; x: number; y: number; onClose: () => void }) {
  const host = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState({ left: x, top: y });
  useEffect(() => {
    const box = host.current!.getBoundingClientRect();
    setPlace({ left: Math.max(8, Math.min(x, innerWidth - box.width - 8)), top: Math.max(8, Math.min(y, innerHeight - box.height - 8)) });
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !host.current?.contains(event.target as Node)) onClose();
    };
    addEventListener('mousedown', close, true);
    addEventListener('keydown', close, true);
    addEventListener('wheel', close, true);
    return () => {
      removeEventListener('mousedown', close, true);
      removeEventListener('keydown', close, true);
      removeEventListener('wheel', close, true);
    };
  }, [entries, x, y]);
  useEffect(() => {
    // The table keeps the focus, so the menu's keys are taken on their way to it.
    const move = (event: KeyboardEvent) => {
      const items = [...host.current!.querySelectorAll<HTMLElement>('button:not(:disabled)')];
      const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
      const at = items.findIndex((item) => item.dataset.on !== undefined);
      if (step) {
        items.forEach((item) => delete item.dataset.on);
        items[(at + step + items.length) % items.length].dataset.on = '';
      } else if (event.key === 'Enter' && at >= 0) items[at].click();
      else return;
      event.preventDefault();
      event.stopPropagation();
    };
    addEventListener('keydown', move, true);
    return () => removeEventListener('keydown', move, true);
  }, []);
  return createPortal(
    <div ref={host} className="md-menu" role="menu" style={place} onMouseDown={(event) => event.preventDefault()}>
      {entries.map((entry, i) => {
        if (entry === 'separator') return <hr key={i} />;
        const Icon = icons[entry.icon];
        return (
          <button key={i} type="button" role="menuitem" className={entry.danger ? 'danger' : ''} disabled={entry.disabled} onClick={() => { onClose(); entry.run(); }}>
            <Icon size={14} aria-hidden="true" /> {entry.label}
          </button>
        );
      })}
    </div>,
    document.body,
  );
}

/** A note edited in place: formatted text, and drawn elements filled in by the page. */
export function Editor({
  text,
  provider,
  render,
  complete,
  follow,
  cite,
  onHeadings,
  onView,
}: {
  text: Y.Text;
  provider: WebsocketProvider;
  render: (element: Element, actions: Actions, dom: HTMLElement) => ReactNode;
  complete: CompletionSource;
  follow: (href: string) => void;
  cite: Cite;
  onHeadings: (found: Heading[]) => void;
  onView: (view: EditorView) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef({ complete, follow, cite, onHeadings });
  latest.current = { complete, follow, cite, onHeadings };
  const [menu, setMenu] = useState<{ entries: MenuEntry[]; x: number; y: number }>();
  const [mounted, setMounted] = useState(new Map<HTMLElement, { element: Element; actions: Actions; key: number }>());
  const latestMounted = useRef(mounted);
  latestMounted.current = mounted;
  useEffect(() => {
    let count = 0;
    let told = '';
    const resize = new ResizeObserver(() => view.requestMeasure());
    const mounts: Mounts = {
      update: (dom, element) => setMounted((current) => {
        const known = current.get(dom);
        return known ? new Map(current).set(dom, { ...known, element }) : current;
      }),
      menu: (dom, at) => {
        const known = latestMounted.current.get(dom);
        if (known) setMenu({ entries: figureMenu(known.element, known.actions), ...at });
      },
      add: (dom, element, actions) => {
        resize.observe(dom);
        setMounted((current) => new Map(current).set(dom, { element, actions, key: count++ }));
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
      cite: (...given) => latest.current.cite(...given),
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
          markdown({ base: markdownLanguage, extensions: [math, tableEnds], codeLanguages: (info) => languages[info.split(/\s/)[0].toLowerCase()]?.language ?? null }),
          syntaxHighlighting(syntax),
          syntaxHighlighting(code),
          EditorView.lineWrapping,
          drawSelection(),
          preview(mounts),
          references(mounts.cite),
          listEditing(),
          tableEditing({
            renderInline: inline,
            showMenu: (entries, at) => setMenu({ entries, ...at }),
            openLink: mounts.follow,
            copyReference: (editor, from, to, part) => void copyText(mounts.cite(editor.state.doc, from, to, part)),
          }),
          search({ top: true }),
          closeBrackets(),
          autocompletion({ override: [(context) => latest.current.complete(context)], icons: false }),
          Prec.high(keys),
          keymap.of([...yUndoManagerKeymap, ...closeBracketsKeymap, ...completionKeymap, ...searchKeymap, ...defaultKeymap]),
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
      {menu && <Menu {...menu} onClose={() => setMenu(undefined)} />}
      {[...mounted].map(([dom, { element, actions, key }]) =>
        createPortal(
          <>
            {render(element, actions, dom)}
            {element.place !== 'text' && <Controls actions={actions} />}
          </>,
          dom,
          String(key),
        ),
      )}
    </>
  );
}
