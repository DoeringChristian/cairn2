import type { MarkdownConfig } from '@lezer/markdown';

/** $inline$ and $$display$$ formulas, as nodes of the Markdown tree. */
export const math: MarkdownConfig = {
  defineNodes: ['InlineMath', { name: 'BlockMath', block: true }, 'MathMark'],
  parseInline: [
    {
      name: 'InlineMath',
      before: 'Escape',
      parse(cx, next, pos) {
        if (next !== 36 || cx.char(pos + 1) === 36 || /\s/.test(cx.slice(pos + 1, pos + 2))) return -1;
        for (let at = pos + 1; at < cx.end; at++) {
          const char = cx.char(at);
          if (char === 10) return -1;
          if (char === 92) at++;
          else if (char === 36) {
            if (at === pos + 1 || /\s/.test(cx.slice(at - 1, at)) || /\d/.test(cx.slice(at + 1, at + 2))) return -1;
            return cx.addElement(
              cx.elt('InlineMath', pos, at + 1, [cx.elt('MathMark', pos, pos + 1), cx.elt('MathMark', at, at + 1)]),
            );
          }
        }
        return -1;
      },
    },
  ],
  parseBlock: [
    {
      name: 'BlockMath',
      before: 'FencedCode',
      parse(cx, line) {
        if (!line.text.slice(line.pos).startsWith('$$')) return false;
        const start = cx.lineStart + line.pos;
        const closes = (text: string) => text.trimEnd().endsWith('$$');
        let end = cx.lineStart + line.text.length;
        if (!(closes(line.text) && line.text.trim().length > 3))
          while (cx.nextLine()) {
            end = cx.lineStart + line.text.length;
            if (closes(line.text)) break;
          }
        cx.addElement(cx.elt('BlockMath', start, end));
        cx.nextLine();
        return true;
      },
      endLeaf: (_cx, line) => line.text.slice(line.pos).startsWith('$$'),
    },
  ],
};

export const callout = /^\[!(\w+)\]\s*/;
export const calloutKinds = ['note', 'tip', 'warning', 'important'];

/** Cells of a table's line: their trimmed text, and all between the bars. */
export function cells(line: string): { from: number; to: number; start: number; end: number }[] {
  const bars: number[] = [];
  for (let i = 0; i < line.length; i++)
    if (line[i] === '\\') i++;
    else if (line[i] === '|') bars.push(i);
  const first = line.search(/\S/);
  const edges = bars[0] === first ? bars : [-1, ...bars];
  if (bars.at(-1) !== line.trimEnd().length - 1 || bars.length < 2) edges.push(line.length);
  return edges.slice(1).map((to, i) => {
    const from = edges[i] + 1;
    const text = line.slice(from, to);
    const lead = text.length - text.trimStart().length;
    const whole = { start: from, end: to };
    return text.trim() ? { ...whole, from: from + lead, to: from + text.trimEnd().length } : { ...whole, from, to: from };
  });
}
export const cellText = (source: string) => source.replace(/\\\|/g, '|');
export const cellSource = (text: string) => text.replace(/\s*\n\s*/g, ' ').replace(/\\?\|/g, '\\|');
