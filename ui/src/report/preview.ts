import { ensureSyntaxTree, syntaxTree } from '@codemirror/language';
import { EditorSelection, StateEffect, StateField, type EditorState, type Range, type Text } from '@codemirror/state';
import { Decoration, EditorView, ViewPlugin, WidgetType, type DecorationSet, type ViewUpdate } from '@codemirror/view';
import { copyText } from '../components/useCopyText';
import { heightOf } from './heights';
import { beginEmbedDrag } from './drag';
import { blockShare, callout, calloutKinds, embedsIn, embedText, setting, tenths, type Embed } from './markdown';
import { mediaHandling, type Span } from './media';
import { copyButton, type Cite } from './reference';
import { revealChanged, revealedAt, revealedLines, revealTracking, setFocused } from './reveal';
import { tex } from './tex';

/** The text is always itself: marks hide where the cursor is not, and figures and formulas are
 * drawn. A drawn figure is one step for the cursor; its source shows when asked for. The
 * behaviour is Pumice's (plugins/core-editor/src/live-preview.ts). */
export type Element = { kind: 'figure' | 'view' | 'card'; source: string; caption: string; width?: number; place: 'line' | 'row' | 'text' };
export type Actions = {
  edit: () => void;
  copy: () => void;
  remove: () => void;
  caption: (text: string) => void;
  /** Widths as shares of the line, of this one and (in a row) of those beside it; and a block's height. */
  size: (size: { width?: number; others?: { el: HTMLElement; width: number }[]; height?: number }) => void;
};
export type Mounts = { add: (dom: HTMLElement, element: Element, actions: Actions) => void; update: (dom: HTMLElement, element: Element) => void; menu: (dom: HTMLElement, at: { x: number; y: number }) => void; remove: (dom: HTMLElement) => void; follow: (href: string) => void; cite: Cite };

const showSource = StateEffect.define<Span>();
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
const asked = (state: EditorState, from: number, to: number) => {
  const span = state.field(sourceField);
  return !!span && span.from <= to && span.to >= from;
};
/** What is being written is drawn once the cursor leaves it. */
const within = (state: EditorState, { from, to }: Span) => state.selection.ranges.some((range) => range.head > from && range.head < to) && revealedAt(state, from, to);

const shown = new WeakMap<HTMLElement, ElementWidget>();
class ElementWidget extends WidgetType {
  constructor(
    readonly element: Element,
    readonly text: string,
    readonly mounts: Mounts,
  ) {
    super();
  }
  eq(other: ElementWidget) {
    return other.text === this.text && other.element.place === this.element.place;
  }
  /** What is shown stays when only its caption, its size or what stands around it changed. */
  updateDOM(dom: HTMLElement) {
    const was = shown.get(dom);
    if (!was || was.element.kind !== this.element.kind || (this.element.kind === 'figure' && was.element.source !== this.element.source)) return false;
    shown.set(dom, this);
    this.look(dom);
    this.mounts.update(dom, this.element);
    return true;
  }
  private look(dom: HTMLElement) {
    const { kind, place, width } = this.element;
    dom.className = `md-element${kind === 'figure' ? ` md-embed md-in-${place}` : ' md-block'}${width ? ' is-sized' : ''}`;
    if (width) dom.style.setProperty('--share', String(width));
    else dom.style.removeProperty('--share');
  }
  toDOM(view: EditorView) {
    const figure = this.element.kind === 'figure';
    const dom: HTMLElement = document.createElement(figure ? 'span' : 'div');
    shown.set(dom, this);
    this.look(dom);
    const span = (el = dom) => {
      const from = view.posAtDOM(el);
      return { from, to: from + shown.get(el)!.text.length };
    };
    const write = (insert: string, userEvent: string) => {
      const { from, to } = span();
      if (view.state.sliceDoc(from, to) !== insert) view.dispatch({ changes: { from, to, insert }, userEvent });
    };
    const embed = (el = dom) => shown.get(el)!.element;
    const actions: Actions = {
      edit: () => {
        const { from, to } = span();
        view.dispatch({ selection: { anchor: figure ? from + 2 : to }, effects: [showSource.of({ from, to }), setFocused.of(true)], scrollIntoView: true });
        view.focus();
      },
      copy: () => void copyText(this.mounts.cite(view.state.doc, span().from, span().to)),
      remove: () => {
        let { from, to } = span();
        const line = view.state.doc.lineAt(from);
        if (!figure || line.length === to - from) {
          if (to < view.state.doc.length) to++;
          else if (from) from--;
        }
        view.dispatch({ changes: { from, to }, selection: { anchor: from }, userEvent: 'delete' });
        view.focus();
      },
      caption: (caption) => {
        if (figure) write(embedText({ ...embed(), caption }), 'input.caption');
        else write(setting(shown.get(dom)!.text, 'caption', caption), 'input.caption');
      },
      size: ({ width, others = [], height }) => {
        if (!figure) {
          const sized = setting(shown.get(dom)!.text, 'width', width && width < 100 ? `${tenths(width)}%` : undefined);
          return write(height === undefined ? sized : setting(sized, 'height', embed().kind === 'view' ? `${height}px` : height), 'input.resize');
        }
        const changes = [{ el: dom, width }, ...others].map((one) => ({ ...span(one.el), insert: embedText({ ...embed(one.el), width: one.width }) }));
        view.dispatch({ changes, userEvent: 'input.resize' });
      },
    };
    if (figure && !view.state.readOnly) {
      dom.draggable = true;
      dom.addEventListener('dragstart', (event) => beginEmbedDrag(view, dom, event));
    }
    dom.addEventListener('contextmenu', (event) => {
      event.preventDefault();
      event.stopPropagation();
      this.mounts.menu(dom, { x: event.clientX, y: event.clientY });
    });
    // A value within a line is like a formula: a click shows what it stands for.
    dom.addEventListener('mousedown', (event) => {
      if (event.button || !(event.target as HTMLElement).closest('.md-value, .md-missing')) return;
      event.preventDefault();
      actions.edit();
    });
    this.mounts.add(dom, this.element, actions);
    return dom;
  }
  destroy(dom: HTMLElement) {
    this.mounts.remove(dom);
  }
  ignoreEvent() {
    return true;
  }
  get estimatedHeight() {
    return this.element.place === 'text' ? -1 : (heightOf(this.element.source) ?? 320);
  }
}
class MathWidget extends WidgetType {
  constructor(
    readonly source: string,
    readonly display: boolean,
    readonly preview = false,
  ) {
    super();
  }
  eq(other: MathWidget) {
    return other.source === this.source && other.display === this.display && other.preview === this.preview;
  }
  toDOM(view: EditorView) {
    const dom = tex(this.source, this.display);
    if (this.preview) dom.classList.add('md-math-preview');
    else
      dom.onmousedown = (event) => {
        if (event.button) return;
        event.preventDefault();
        const anchor = view.posAtDOM(dom) + (this.display ? 2 : 1);
        view.focus();
        view.dispatch({ selection: { anchor }, effects: setFocused.of(true) });
      };
    return dom;
  }
  ignoreEvent() {
    return true;
  }
}
class BulletWidget extends WidgetType {
  eq() {
    return true;
  }
  toDOM() {
    const dom = document.createElement('span');
    dom.className = 'md-bullet';
    dom.textContent = '•';
    return dom;
  }
  ignoreEvent() {
    return false;
  }
}
class CheckboxWidget extends WidgetType {
  constructor(readonly checked: boolean) {
    super();
  }
  eq(other: CheckboxWidget) {
    return other.checked === this.checked;
  }
  toDOM(view: EditorView) {
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.className = 'md-checkbox';
    box.checked = this.checked;
    box.setAttribute('aria-label', this.checked ? 'Completed task' : 'Open task');
    box.onmousedown = (event) => {
      event.preventDefault();
      const at = view.posAtDOM(box) + 1;
      if (!view.state.readOnly) view.dispatch({ changes: { from: at, to: at + 1, insert: this.checked ? ' ' : 'x' }, userEvent: 'input.task' });
    };
    return box;
  }
  ignoreEvent() {
    return true;
  }
}
/** A callout's kind, in place of its mark. */
class TitleWidget extends WidgetType {
  constructor(readonly text: string) {
    super();
  }
  eq(other: TitleWidget) {
    return other.text === this.text;
  }
  toDOM() {
    const dom = document.createElement('span');
    dom.textContent = this.text;
    return dom;
  }
  ignoreEvent() {
    return false;
  }
}
/** In place of a code block's opening fence: its language, and a button that copies the code. */
class CodeHeaderWidget extends WidgetType {
  constructor(
    readonly language: string,
    readonly code: string,
  ) {
    super();
  }
  eq(other: CodeHeaderWidget) {
    return other.language === this.language && other.code === this.code;
  }
  toDOM() {
    const dom = document.createElement('span');
    dom.className = 'md-code-header';
    const label = document.createElement('span');
    label.className = 'syntax-language';
    label.textContent = this.language;
    const button = copyButton('Copy code', () => this.code);
    button.classList.add('code-copy');
    dom.append(label, button);
    return dom;
  }
  ignoreEvent(event: Event) {
    return !!(event.target as HTMLElement).closest?.('.code-copy');
  }
}
class RuleWidget extends WidgetType {
  eq() {
    return true;
  }
  toDOM() {
    return document.createElement('hr');
  }
  ignoreEvent() {
    return false;
  }
}

/** A node's whole lines; some blocks end after their line break. */
function linesOf(doc: Text, node: Span) {
  const to = node.to > node.from && doc.sliceString(node.to - 1, node.to) === '\n' ? node.to - 1 : node.to;
  return { from: doc.lineAt(node.from).from, to: doc.lineAt(to).to };
}

/** What is drawn in place of whole lines. */
function blocks(state: EditorState, mounts: Mounts): DecorationSet {
  const found: Range<Decoration>[] = [];
  const { doc } = state;
  const active = revealedLines(state);
  const touches = (span: Span) => {
    for (let n = doc.lineAt(span.from).number; n <= doc.lineAt(span.to).number; n++) if (active.has(n)) return true;
    return false;
  };
  const draw = (span: Span, widget: WidgetType) => found.push(Decoration.replace({ widget, block: true }).range(span.from, span.to));
  const cursor = (ensureSyntaxTree(state, doc.length, 100) || syntaxTree(state)).topNode.cursor();
  if (!cursor.firstChild()) return Decoration.none;
  do {
    if (!['FencedCode', 'BlockMath'].includes(cursor.name)) continue;
    const span = linesOf(doc, cursor);
    const text = doc.sliceString(span.from, span.to);
    if (cursor.name === 'BlockMath') {
      const source = text.trim().replace(/^\$\$|\$\$$/g, '');
      if (!touches(span)) draw(span, new MathWidget(source, true));
      else if (source.trim()) found.push(Decoration.widget({ widget: new MathWidget(source, true, true), block: true, side: 1 }).range(span.to));
      continue;
    }
    if (asked(state, span.from, span.to) || within(state, span)) continue;
    const block = /^```\s*(view|card)\s*\n([\s\S]*?)\n?```\s*$/.exec(text);
    if (block) found.push(Decoration.replace({ media: true, block: true, widget: new ElementWidget({ kind: block[1] as 'view' | 'card', source: block[2], caption: '', place: 'line', width: blockShare(block[2]) }, text, mounts) }).range(span.from, span.to));
  } while (cursor.nextSibling());
  return Decoration.set(found, true);
}

/** The figures of the whole text: one that is forgotten off screen would change the text's
 * height as it is scrolled to. */
function embeds(state: EditorState, mounts: Mounts): { found: Range<Decoration>[]; rows: Range<Decoration>[] } {
  const found: Range<Decoration>[] = [];
  const rows: Range<Decoration>[] = [];
  const { doc } = state;
  let at = 1;
  for (const text of doc.iterLines()) {
    const line = doc.line(at++);
    if (!text.includes('](cairn:') && !/\]\(\s*cairn:/.test(text)) continue;
    const inCode = (pos: number) => {
      for (let node = syntaxTree(state).resolveInner(pos, 1); ; node = node.parent!) {
        if (/Code|Math/.test(node.name)) return true;
        if (!node.parent) return false;
      }
    };
    const list = embedsIn(text, line.from).filter((one) => !inCode(one.from) && !asked(state, one.from, one.to) && !within(state, one));
    if (!list.length) continue;
    let rest = text;
    for (const one of [...list].reverse()) rest = rest.slice(0, one.from - line.from) + rest.slice(one.to - line.from);
    const place = rest.trim() ? 'text' : list.length > 1 ? 'row' : 'line';
    list.forEach((one, i) => {
      found.push(Decoration.replace({ atom: true, widget: figure(one, doc.sliceString(one.from, one.to), place, mounts) }).range(one.from, one.to));
      const next = list[i + 1];
      // The space between the figures of a row has no place on screen.
      if (place === 'row' && next && next.from > one.to) found.push(Decoration.replace({}).range(one.to, next.from));
    });
    if (place !== 'text') rows.push(Decoration.line({ class: `md-figures md-${place}` }).range(line.from));
  }
  return { found, rows };
}
const figure = ({ source, caption, width }: Embed, text: string, place: Element['place'], mounts: Mounts) =>
  new ElementWidget({ kind: 'figure', source, caption, place, ...(width ? { width } : {}) }, text, mounts);

const marks: Record<string, string> = { Emphasis: 'md-em', StrongEmphasis: 'md-strong', Strikethrough: 'md-strike', InlineCode: 'md-code' };
const hidden = new Set(['EmphasisMark', 'StrikethroughMark', 'CodeMark']);

/** What is drawn within lines that are in view. */
function inlines(view: EditorView, mounts: Mounts): DecorationSet {
  const { state } = view;
  const { doc } = state;
  const active = revealedLines(state);
  const isActive = (pos: number) => active.has(doc.lineAt(pos).number);
  const rangeActive = (from: number, to: number) => {
    for (let n = doc.lineAt(from).number; n <= doc.lineAt(to).number; n++) if (active.has(n)) return true;
    return false;
  };
  const near = (from: number, to: number) => revealedAt(state, from, to);
  const found: Range<Decoration>[] = [];
  const hide = (from: number, to: number) => {
    if (from < to) found.push(Decoration.replace({}).range(from, to));
  };
  const lineClass = (from: number, to: number, name: (first: boolean, last: boolean) => string) => {
    const [first, last] = [doc.lineAt(from).number, doc.lineAt(to).number];
    for (let n = first; n <= last; n++) found.push(Decoration.line({ class: name(n === first, n === last) }).range(doc.line(n).from));
  };
  const edges = (name: string) => (first: boolean, last: boolean) => `${name}${first ? ' md-first' : ''}${last ? ' md-last' : ''}`;
  const drawn = state.field(blockField(mounts));
  for (const { from, to } of view.visibleRanges)
    syntaxTree(state).iterate({
      from,
      to,
      enter: (node) => {
        const { name } = node;
        let covered = false;
        if (name !== 'Document')
          drawn.between(node.from, node.to, (a, b, value) => {
            if (value.spec.block && a < b && a <= node.from && b >= node.to) covered = true;
          });
        if (covered) return false;
        const heading = /^(?:ATX|Setext)Heading(\d)$/.exec(name);
        if (heading) lineClass(node.from, node.from, () => `md-heading md-h${heading[1]}`);
        else if (name === 'HeaderMark') {
          if (!isActive(node.from) && node.node.parent?.name.startsWith('ATX')) hide(node.from, node.to + (doc.sliceString(node.to, node.to + 1) === ' ' ? 1 : 0));
        } else if (marks[name]) found.push(Decoration.mark({ class: marks[name] }).range(node.from, node.to));
        else if (hidden.has(name)) {
          const { parent } = node.node;
          if (parent && parent.name !== 'FencedCode' && !near(parent.from, parent.to)) hide(node.from, node.to);
        } else if (name === 'InlineMath') {
          if (near(node.from, node.to)) found.push(Decoration.mark({ class: 'md-source' }).range(node.from, node.to));
          else found.push(Decoration.replace({ widget: new MathWidget(doc.sliceString(node.from + 1, node.to - 1), false) }).range(node.from, node.to));
          return false;
        } else if (name === 'Image') return false;
        else if (name === 'Link') {
          const url = node.node.getChild('URL');
          const [open, close] = node.node.getChildren('LinkMark');
          if (!url || !open || !close) return true;
          const href = doc.sliceString(url.from, url.to);
          const shown = near(node.from, node.to);
          if (open.to < close.from) found.push(Decoration.mark({ class: 'md-link', attributes: { title: href, ...(shown ? {} : { 'data-href': href }) } }).range(open.to, close.from));
          if (!shown) {
            hide(node.from, open.to);
            hide(close.from, node.to);
          }
        } else if (name === 'URL' && !['Link', 'Image'].includes(node.node.parent?.name || '')) {
          const href = doc.sliceString(node.from, node.to);
          found.push(Decoration.mark({ class: 'md-link', attributes: near(node.from, node.to) ? {} : { 'data-href': href } }).range(node.from, node.to));
        } else if (name === 'Blockquote') {
          if (node.node.parent?.name === 'Blockquote') return true;
          const first = doc.lineAt(node.from);
          const lead = /^\s*>[ \t]?/.exec(first.text)?.[0].length || 0;
          const match = callout.exec(first.text.slice(lead));
          const span = linesOf(doc, node);
          if (!match) {
            lineClass(span.from, span.to, edges('md-quote'));
            return true;
          }
          const kind = calloutKinds.includes(match[1].toLowerCase()) ? match[1].toLowerCase() : 'note';
          lineClass(span.from, span.to, (one, end) => `${edges(`md-callout md-callout-${kind}`)(one, end)}${one ? ' md-callout-title' : ''}`);
          const at = first.from + lead;
          if (!near(at, at + match[0].length)) {
            const rest = first.text.slice(lead + match[0].length).trim();
            found.push(Decoration.replace(rest ? {} : { widget: new TitleWidget(match[1][0].toUpperCase() + match[1].slice(1).toLowerCase()) }).range(at, at + match[0].length));
          }
        } else if (name === 'QuoteMark') {
          const after = node.to + (doc.sliceString(node.to, node.to + 1) === ' ' ? 1 : 0);
          if (!near(node.from, after)) hide(node.from, after);
        } else if (name === 'FencedCode' || name === 'CodeBlock') {
          const span = linesOf(doc, node);
          const [first, last] = [doc.lineAt(span.from), doc.lineAt(span.to)];
          lineClass(span.from, span.to, edges('md-fence'));
          const fence = /^\s*(`{3,}|~{3,})\s*([^\s`~]*)/.exec(first.text);
          if (name === 'FencedCode' && fence && !rangeActive(span.from, span.to)) {
            const closed = last.number > first.number && /^\s*(`{3,}|~{3,})\s*$/.test(last.text);
            const code = last.number > first.number ? doc.sliceString(first.to + 1, closed ? Math.max(first.to + 1, last.from - 1) : last.to) : '';
            found.push(Decoration.replace({ widget: new CodeHeaderWidget(fence[2], code) }).range(first.from, first.to));
            if (closed) {
              hide(last.from, last.to);
              found.push(Decoration.line({ class: 'md-fence-close' }).range(last.from));
            }
          }
        } else if (name === 'TaskMarker') {
          const dash = node.node.parent?.prevSibling;
          const checked = /x/i.test(doc.sliceString(node.from, node.to));
          if (!near(dash?.name === 'ListMark' ? dash.from : node.from, node.to)) found.push(Decoration.replace({ widget: new CheckboxWidget(checked) }).range(node.from, node.to));
          if (checked) lineClass(node.from, node.from, () => 'md-done');
        } else if (name === 'HorizontalRule') {
          if (!isActive(node.from)) found.push(Decoration.replace({ widget: new RuleWidget() }).range(node.from, node.to));
        } else if (name === 'ListMark' && node.node.parent?.parent?.name === 'BulletList') {
          const task = node.node.nextSibling;
          if (task?.name === 'Task') {
            if (!near(node.from, task.getChild('TaskMarker')?.to ?? task.from)) hide(node.from, task.from);
          } else if (!near(node.from, node.to)) found.push(Decoration.replace({ widget: new BulletWidget() }).range(node.from, node.to));
        }
        return true;
      },
    });
  indentation(view, found);
  const figures = embeds(state, mounts);
  found.push(...figures.found, ...figures.rows);
  return Decoration.set(
    // While text is typed, marks may pair across lines for a moment; a replacement may not.
    found.filter((range) => !(range.value.point && range.to > range.from) || doc.lineAt(range.from).to >= range.to),
    true,
  );
}

/** List items hang by what stands in front of their text, and nested ones start under it. */
function indentation(view: EditorView, found: Range<Decoration>[]) {
  const { state } = view;
  for (const { from, to } of view.visibleRanges)
    for (let pos = from; pos <= to; ) {
      const line = state.doc.lineAt(pos);
      pos = line.to + 1;
      const indent = /^[ \t]*/.exec(line.text)![0];
      if (indent.length === line.length) continue;
      const marker = /^(?:[-*+]|\d+[.)])[ \t]+/.exec(line.text.slice(indent.length))?.[0];
      if (!indent && !marker) continue;
      let listed = false;
      for (let node = syntaxTree(state).resolveInner(line.from + indent.length, 1); ; ) {
        if (node.name === 'FencedCode' || node.name === 'CodeBlock') {
          listed = false;
          break;
        }
        if (node.name === 'BulletList' || node.name === 'OrderedList') listed = true;
        if (!node.parent) break;
        node = node.parent;
      }
      if (!listed) continue;
      let steps = 0;
      let spare = 0;
      for (let i = 0; i < indent.length; ) {
        const length = indent[i] === '\t' ? 1 : indent[i + 1] === ' ' ? 2 : 1;
        const step = indent[i] === '\t' || length === 2;
        if (step) steps++;
        else spare++;
        found.push(Decoration.mark({ class: step ? 'md-indent' : 'md-indent-half' }).range(line.from + i, line.from + i + length));
        i += length;
      }
      const numbered = marker && /^\d/.test(marker) ? marker.length : 0;
      if (numbered) {
        const at = line.from + indent.length;
        found.push(Decoration.mark({ class: 'md-number', attributes: { style: `--chars:${numbered}` } }).range(at, at + numbered));
      }
      found.push(Decoration.line({ class: 'md-item', attributes: { style: `--steps:${steps + (marker && !numbered ? 1 : 0)};--chars:${spare + numbered}` } }).range(line.from));
    }
}

const fields = new WeakMap<Mounts, StateField<DecorationSet>>();
function blockField(mounts: Mounts) {
  let field = fields.get(mounts);
  if (!field) {
    field = StateField.define<DecorationSet>({
      create: (state) => blocks(state, mounts),
      update: (value, tr) =>
        tr.docChanged || revealChanged(tr.startState, tr.state) || tr.startState.field(sourceField) !== tr.state.field(sourceField) || syntaxTree(tr.state) !== syntaxTree(tr.startState)
          ? blocks(tr.state, mounts)
          : value,
      provide: (own) => [EditorView.decorations.from(own), EditorView.atomicRanges.of((view) => view.state.field(own).update({ filter: (_from, _to, value) => !!value.spec.media }))],
    });
    fields.set(mounts, field);
  }
  return field;
}

export function preview(mounts: Mounts) {
  const inside = ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;
      atoms: DecorationSet;
      constructor(view: EditorView) {
        this.decorations = inlines(view, mounts);
        this.atoms = this.decorations.update({ filter: (_from, _to, value) => !!value.spec.atom });
      }
      update(update: ViewUpdate) {
        if (
          update.docChanged ||
          update.viewportChanged ||
          revealChanged(update.startState, update.state) ||
          update.startState.field(sourceField) !== update.state.field(sourceField) ||
          update.startState.field(blockField(mounts)) !== update.state.field(blockField(mounts)) ||
          syntaxTree(update.state) !== syntaxTree(update.startState)
        )
        {
          this.decorations = inlines(update.view, mounts);
          this.atoms = this.decorations.update({ filter: (_from, _to, value) => !!value.spec.atom });
        }
      }
    },
    { decorations: (plugin) => plugin.decorations, provide: (plugin) => EditorView.atomicRanges.of((view) => view.plugin(plugin)?.atoms ?? Decoration.none) },
  );
  const follow = EditorView.domEventHandlers({
    click(event, view) {
      const link = (event.target as globalThis.Element).closest<HTMLElement>('.md-link[data-href]');
      if (!link || event.button || event.shiftKey || event.altKey || !view.state.selection.main.empty) return false;
      mounts.follow(link.dataset.href!);
      return true;
    },
    mousedown(event, view) {
      const link = (event.target as globalThis.Element).closest<HTMLElement>('.md-link[data-href]');
      // A link is followed, not written in, by a plain click on it.
      if (!link || event.button || event.shiftKey || event.altKey || event.detail > 1 || !view.contentDOM.contains(link)) return false;
      event.preventDefault();
      return true;
    },
  });
  // Marks that appeared with the first click moved the text: the second means what the first pointed at.
  let first: { at: number; x: number; y: number } | undefined;
  const clicks = [
    EditorView.domEventHandlers({
      mousedown(event, view) {
        if (event.detail === 1) first = { at: view.posAtCoords({ x: event.clientX, y: event.clientY }, false), x: event.clientX, y: event.clientY };
        return false;
      },
    }),
    EditorView.mouseSelectionStyle.of((view, event) => {
      if (!first || event.button || event.detail < 2 || event.shiftKey || event.altKey || Math.hypot(event.clientX - first.x, event.clientY - first.y) > 6) return null;
      const { at, x, y } = first;
      const part = (pos: number) => {
        if (event.detail > 2) return view.state.doc.lineAt(pos);
        return view.state.wordAt(pos) ?? { from: pos, to: pos };
      };
      return {
        get(current) {
          const one = part(at);
          const moved = Math.hypot(current.clientX - x, current.clientY - y) > 6;
          const other = moved ? part(view.posAtCoords({ x: current.clientX, y: current.clientY }, false)) : one;
          return EditorSelection.single(Math.min(one.from, other.from), Math.max(one.to, other.to));
        },
        update: () => false,
      };
    }),
  ];
  const atCode = EditorView.editorAttributes.of(({ state }) => {
    for (let node = syntaxTree(state).resolveInner(state.doc.lineAt(state.selection.main.head).to, -1); node.parent; node = node.parent)
      if (node.name === 'FencedCode' || node.name === 'CodeBlock') return { class: 'md-at-code' };
    return null;
  });
  const handling = mediaHandling({ atoms: (view) => view.plugin(inside)?.atoms, blocks: (state) => state.field(blockField(mounts), false) });
  return [clicks, follow, atCode, revealTracking, sourceField, inside, blockField(mounts), handling];
}
