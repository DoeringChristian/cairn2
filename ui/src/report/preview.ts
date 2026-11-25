import { ensureSyntaxTree, syntaxTree } from '@codemirror/language';
import { StateEffect, StateField, type EditorState, type Range, type Text } from '@codemirror/state';
import { Decoration, EditorView, ViewPlugin, WidgetType, type DecorationSet, type ViewUpdate } from '@codemirror/view';
import type { SyntaxNode } from '@lezer/common';
import { heightOf } from './heights';
import { callout, calloutKinds } from './markdown';
import { TableWidget } from './table';
import { tex } from './tex';

/** The text is always itself: marks hide where the cursor is not, and figures, formulas and
 * tables are drawn. A drawn thing is one step for the cursor; its source shows when asked for. */
export type Element = { kind: 'figure' | 'view' | 'card'; source: string; caption: string; inline: boolean };
export type Mounts = { add: (dom: HTMLElement, element: Element, edit: () => void) => void; remove: (dom: HTMLElement) => void; follow: (href: string) => void };
type Span = { from: number; to: number };

export const focusEffect = StateEffect.define<boolean>();
const showSource = StateEffect.define<Span>();
export const focusField = StateField.define<boolean>({
  create: () => false,
  update: (value, tr) => tr.effects.reduce((current, effect) => (effect.is(focusEffect) ? effect.value : current), value),
});
const sourceField = StateField.define<Span | null>({
  create: () => null,
  update(value, tr) {
    for (const effect of tr.effects) if (effect.is(showSource)) return effect.value;
    if (!value) return null;
    const { from, to } = { from: tr.changes.mapPos(value.from, -1), to: tr.changes.mapPos(value.to, 1) };
    const { main } = tr.state.selection;
    return main.from < from || main.to > to ? null : { from, to };
  },
});

const touched = (state: EditorState, from: number, to: number) =>
  state.field(focusField) && state.selection.ranges.some((range) => range.from <= to && range.to >= from);
const inside = (state: EditorState, { from, to }: Span) => state.field(focusField) && state.selection.ranges.some((range) => range.head > from && range.head < to);
const asked = (state: EditorState, from: number, to: number) => {
  const span = state.field(sourceField);
  return !!span && span.from <= to && span.to >= from;
};
function edit(view: EditorView, dom: HTMLElement, from: number, to: number) {
  const at = view.posAtDOM(dom);
  view.dispatch({ selection: { anchor: at + (to - from) }, effects: [showSource.of({ from: at, to: at + (to - from) }), focusEffect.of(true)], scrollIntoView: true });
  view.focus();
}

class ElementWidget extends WidgetType {
  constructor(
    readonly element: Element,
    readonly text: string,
    readonly mounts: Mounts,
  ) {
    super();
  }
  eq(other: ElementWidget) {
    return other.text === this.text && other.element.inline === this.element.inline;
  }
  toDOM(view: EditorView) {
    const dom = document.createElement(this.element.inline ? 'span' : 'div');
    dom.className = this.element.inline ? 'md-element md-element-inline' : 'md-element';
    this.mounts.add(dom, this.element, () => edit(view, dom, 0, this.text.length));
    return dom;
  }
  destroy(dom: HTMLElement) {
    this.mounts.remove(dom);
  }
  ignoreEvent() {
    return true;
  }
  get estimatedHeight() {
    return this.element.inline ? -1 : (heightOf(this.element.source) ?? 320);
  }
}
class MathWidget extends WidgetType {
  constructor(
    readonly source: string,
    readonly display: boolean,
    readonly from = -1,
  ) {
    super();
  }
  eq(other: MathWidget) {
    return other.source === this.source && other.display === this.display && other.from === this.from;
  }
  toDOM(view: EditorView) {
    const dom = tex(this.source, this.display);
    if (this.from < 0) dom.classList.add('md-math-preview');
    else
      dom.addEventListener('mousedown', (event) => {
        event.preventDefault();
        view.focus();
        view.dispatch({ selection: { anchor: view.posAtDOM(dom) + (this.display ? 2 : 1) }, effects: focusEffect.of(true) });
      });
    return dom;
  }
  ignoreEvent() {
    return true;
  }
}
class MarkerWidget extends WidgetType {
  constructor(
    readonly text: string,
    readonly checked?: boolean,
  ) {
    super();
  }
  eq(other: MarkerWidget) {
    return other.text === this.text && other.checked === this.checked;
  }
  toDOM(view: EditorView) {
    const dom = document.createElement('span');
    dom.className = 'md-marker';
    if (this.checked === undefined) dom.textContent = this.text;
    else {
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = this.checked;
      box.setAttribute('aria-label', this.checked ? 'Completed task' : 'Open task');
      box.onmousedown = (event) => {
        event.preventDefault();
        const line = view.state.doc.lineAt(view.posAtDOM(dom));
        const at = line.from + line.text.indexOf('[') + 1;
        if (!view.state.readOnly) view.dispatch({ changes: { from: at, to: at + 1, insert: this.checked ? ' ' : 'x' }, userEvent: 'input' });
      };
      dom.append(box);
    }
    return dom;
  }
  ignoreEvent() {
    return true;
  }
}
class LabelWidget extends WidgetType {
  constructor(readonly text: string) {
    super();
  }
  eq(other: LabelWidget) {
    return other.text === this.text;
  }
  toDOM() {
    const dom = document.createElement('span');
    dom.className = 'md-callout-label';
    dom.textContent = this.text;
    return dom;
  }
}
class RuleWidget extends WidgetType {
  eq() {
    return true;
  }
  toDOM() {
    return document.createElement('hr');
  }
}

const image = /^!\[([^\]]*)\]\(\s*cairn:([^)\s]+)\s*\)$/;
const hide = Decoration.replace({});
/** The positions of a node's whole lines. */
const linesOf = (doc: Text, node: Span) => ({ from: doc.lineAt(node.from).from, to: doc.lineAt(Math.max(node.from, node.to)).to });

/** What is drawn in place of whole lines. */
function blocks(state: EditorState, mounts: Mounts): DecorationSet {
  const found: Range<Decoration>[] = [];
  const { doc } = state;
  const draw = (span: Span, widget: WidgetType) => found.push(Decoration.replace({ widget, block: true }).range(span.from, span.to));
  (ensureSyntaxTree(state, doc.length, 100) || syntaxTree(state)).topNode.cursor().iterate((node) => {
    if (node.name === 'Document' || node.name === 'Blockquote') return true;
    if (!['Paragraph', 'FencedCode', 'BlockMath', 'Table'].includes(node.name)) return false;
    const span = linesOf(doc, node);
    if (node.name !== 'BlockMath' && node.node.parent?.name !== 'Document') return false;
    const text = doc.sliceString(span.from, span.to);
    // What is being written is drawn once the cursor leaves it.
    if (asked(state, span.from, span.to) || (node.name !== 'BlockMath' && node.name !== 'Table' && inside(state, span))) return false;
    if (node.name === 'Paragraph') {
      const match = image.exec(text.trim());
      if (match) draw(span, new ElementWidget({ kind: 'figure', source: match[2], caption: match[1], inline: false }, text, mounts));
    } else if (node.name === 'FencedCode') {
      const match = /^```\s*(view|card)\s*\n([\s\S]*?)\n?```\s*$/.exec(text);
      if (match) draw(span, new ElementWidget({ kind: match[1] as 'view' | 'card', source: match[2], caption: '', inline: false }, text, mounts));
    } else if (node.name === 'BlockMath') {
      if (node.node.parent?.name !== 'Document') return false;
      const source = text.trim().replace(/^\$\$|\$\$$/g, '');
      if (!touched(state, span.from, span.to)) draw(span, new MathWidget(source, true, span.from));
      else found.push(Decoration.widget({ widget: new MathWidget(source, true), block: true, side: 1 }).range(span.to));
    } else draw(span, new TableWidget(text.trimEnd(), (view, dom) => edit(view, dom, span.from, span.to), mounts.follow));
    return false;
  });
  return Decoration.set(found, true);
}

const marks: Record<string, string> = { Emphasis: 'md-em', StrongEmphasis: 'md-strong', Strikethrough: 'md-strike', InlineCode: 'md-code' };

/** What is drawn within lines that are in view. */
function inlines(view: EditorView, mounts: Mounts): DecorationSet {
  const { state } = view;
  const { doc } = state;
  const found: Range<Decoration>[] = [];
  const line = (at: number, name: string, style = '') => found.push(Decoration.line({ class: name, attributes: style ? { style } : {} }).range(doc.lineAt(at).from));
  const drop = (from: number, to: number) => from < to && found.push(hide.range(from, to));
  const drawn = view.state.field(blockField(mounts));
  const done = new Set<number>();
  for (const { from, to } of view.visibleRanges)
    syntaxTree(state).iterate({
      from,
      to,
      enter: (node) => {
        let covered = false;
        drawn.between(node.from, node.to, (a, b, value) => {
          if (a < b && value.spec.block && a <= node.from && b >= node.to) covered = true;
        });
        if (covered && node.name !== 'Document') return false;
        const first = doc.lineAt(node.from);
        const heading = /^(?:ATX|Setext)Heading(\d)$/.exec(node.name);
        if (heading) line(node.from, `md-heading md-h${heading[1]}`);
        else if (node.name === 'HeaderMark') {
          if (!touched(state, first.from, first.to)) drop(node.from, Math.min(first.to, node.to + (doc.sliceString(node.to, node.to + 1) === ' ' ? 1 : 0)));
        } else if (marks[node.name]) {
          found.push(Decoration.mark({ class: marks[node.name] }).range(node.from, node.to));
          if (!touched(state, node.from, node.to))
            for (let child = node.node.firstChild; child; child = child.nextSibling) if (/Mark$/.test(child.name)) drop(child.from, child.to);
        } else if (node.name === 'InlineMath') {
          if (!touched(state, node.from, node.to)) found.push(Decoration.replace({ widget: new MathWidget(doc.sliceString(node.from + 1, node.to - 1), false, node.from) }).range(node.from, node.to));
          else found.push(Decoration.mark({ class: 'md-source' }).range(node.from, node.to));
        } else if (node.name === 'Image') {
          const text = doc.sliceString(node.from, node.to);
          const match = image.exec(text);
          if (match && !touched(state, node.from + 1, node.to - 1) && !asked(state, node.from, node.to))
            found.push(Decoration.replace({ widget: new ElementWidget({ kind: 'figure', source: match[2], caption: match[1], inline: true }, text, mounts) }).range(node.from, node.to));
          return false;
        } else if (node.name === 'Link') {
          const url = node.node.getChild('URL');
          if (!url) return true;
          const href = doc.sliceString(url.from, url.to);
          const open = touched(state, node.from, node.to);
          const [start, end] = node.node.getChildren('LinkMark');
          found.push(Decoration.mark({ class: 'md-link', attributes: open ? {} : { 'data-href': href, title: href } }).range(node.from, node.to));
          if (!open && start && end) {
            drop(start.from, start.to);
            drop(end.from, node.to);
          }
        } else if (node.name === 'URL' && node.node.parent?.name !== 'Link' && node.node.parent?.name !== 'Image') {
          const href = doc.sliceString(node.from, node.to);
          found.push(Decoration.mark({ class: 'md-link', attributes: touched(state, node.from, node.to) ? {} : { 'data-href': href } }).range(node.from, node.to));
        } else if (node.name === 'ListItem') listItem(state, node.node, found, done);
        else if (node.name === 'Blockquote') quote(state, node.node, found);
        else if (node.name === 'QuoteMark') drop(node.from, Math.min(first.to, node.to + (doc.sliceString(node.to, node.to + 1) === ' ' ? 1 : 0)));
        else if (node.name === 'HorizontalRule') {
          if (!touched(state, first.from, first.to)) found.push(Decoration.replace({ widget: new RuleWidget() }).range(node.from, node.to));
        } else if (node.name === 'FencedCode' || node.name === 'CodeBlock') {
          const last = doc.lineAt(node.to).number;
          for (let n = first.number; n <= last; n++) {
            const edge = `${n === first.number ? ' md-fence-first' : ''}${n === last ? ' md-fence-last' : ''}`;
            line(doc.line(n).from, `md-fence${edge}`);
          }
          if (node.name === 'FencedCode' && !touched(state, node.from, node.to)) {
            const info = node.node.getChild('CodeInfo');
            const [open, close] = node.node.getChildren('CodeMark');
            if (open) drop(open.from, info ? info.from : open.to);
            if (close) drop(close.from, close.to);
          }
        }
        return true;
      },
    });
  return Decoration.set(found, true);
}

function listItem(state: EditorState, item: SyntaxNode, found: Range<Decoration>[], done: Set<number>) {
  const { doc } = state;
  let depth = 0;
  for (let node: SyntaxNode | null = item; node; node = node.parent) if (node.name === 'ListItem') depth++;
  const mark = item.getChild('ListMark');
  if (!mark) return;
  const last = doc.lineAt(item.to).number;
  for (let n = doc.lineAt(item.from).number; n <= last; n++) {
    const line = doc.line(n);
    // Items inside come later and indent their own lines further.
    if (done.has(n) || !line.length) continue;
    const inner = /^\s*(?:[-*+]|\d+[.)])\s/.test(line.text) && n !== doc.lineAt(item.from).number;
    if (inner) continue;
    done.add(n);
    const own = n === doc.lineAt(item.from).number;
    found.push(Decoration.line({ class: own ? 'md-item' : 'md-item md-item-more', attributes: { style: `--depth:${depth}` } }).range(line.from));
    const lead = line.text.length - line.text.trimStart().length;
    if (!own && lead) found.push(hide.range(line.from, line.from + lead));
  }
  const line = doc.lineAt(mark.from);
  const task = item.getChild('Task')?.getChild('TaskMarker') || item.getChild('Paragraph')?.getChild('Task')?.getChild('TaskMarker');
  const text = doc.sliceString(mark.from, mark.to);
  const end = task ? task.to : mark.to;
  const after = Math.min(line.to, end + (doc.sliceString(end, end + 1) === ' ' ? 1 : 0));
  const checked = task ? /x/i.test(doc.sliceString(task.from, task.to)) : undefined;
  // Without the space after it, a mark is being typed.
  if (after > end || task)
    found.push(Decoration.replace({ widget: new MarkerWidget(/^[-*+]$/.test(text) ? '•' : text, checked) }).range(line.from + quoted(line.text), after));
}

function quote(state: EditorState, node: SyntaxNode, found: Range<Decoration>[]) {
  const { doc } = state;
  if (node.parent?.name === 'Blockquote') return;
  const first = doc.lineAt(node.from);
  const last = doc.lineAt(node.to).number;
  const body = /^\s*>\s?/.exec(first.text)?.[0].length || 0;
  const match = callout.exec(first.text.slice(body));
  const kind = match && (calloutKinds.includes(match[1].toLowerCase()) ? match[1].toLowerCase() : 'note');
  for (let n = first.number; n <= last; n++) {
    const edge = `${n === first.number ? ' md-first' : ''}${n === last ? ' md-last' : ''}`;
    found.push(Decoration.line({ class: kind ? `md-callout md-callout-${kind}${edge}` : `md-quote${edge}` }).range(doc.line(n).from));
  }
  if (!match) return;
  const from = first.from + body;
  found.push(Decoration.mark({ class: 'md-callout-title' }).range(from, first.to));
  if (!touched(state, first.from, first.to)) {
    const rest = first.text.slice(body + match[0].length).trim();
    found.push(Decoration.replace({ widget: rest ? undefined : new LabelWidget(match[1][0].toUpperCase() + match[1].slice(1).toLowerCase()) }).range(from, from + match[0].length));
  }
}

const fields = new WeakMap<Mounts, StateField<DecorationSet>>();
function blockField(mounts: Mounts) {
  let field = fields.get(mounts);
  if (!field) {
    field = StateField.define<DecorationSet>({
      create: (state) => blocks(state, mounts),
      update: (value, tr) =>
        tr.docChanged || tr.selection || tr.effects.length || syntaxTree(tr.state) !== syntaxTree(tr.startState) ? blocks(tr.state, mounts) : value,
      provide: (own) => [
        EditorView.decorations.from(own),
        EditorView.atomicRanges.of((view) => view.state.field(own)),
      ],
    });
    fields.set(mounts, field);
  }
  return field;
}

export function preview(mounts: Mounts) {
  const within = ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;
      constructor(view: EditorView) {
        this.decorations = inlines(view, mounts);
      }
      update(update: ViewUpdate) {
        if (update.docChanged || update.selectionSet || update.viewportChanged || update.transactions.some((tr) => tr.effects.length) || syntaxTree(update.state) !== syntaxTree(update.startState))
          this.decorations = inlines(update.view, mounts);
      }
    },
    { decorations: (plugin) => plugin.decorations, provide: (plugin) => EditorView.atomicRanges.of((view) => view.plugin(plugin)?.decorations.update({ filter: (_from, _to, value) => value.spec.widget instanceof ElementWidget || value.spec.widget instanceof MarkerWidget }) || Decoration.none) },
  );
  const follow = EditorView.domEventHandlers({
    mousedown(event, view) {
      const link = (event.target as globalThis.Element).closest<HTMLElement>('.md-link[data-href]');
      if (!link || event.button || event.shiftKey || event.altKey || !view.contentDOM.contains(link)) return false;
      event.preventDefault();
      mounts.follow(link.dataset.href!);
      return true;
    },
  });
  return [focusField, sourceField, blockField(mounts), within, follow];
}

/** How much of a line its quote marks take. */
const quoted = (text: string) => /^(?:\s*>\s?)*/.exec(text)![0].length;
