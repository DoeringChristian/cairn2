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

/** A table ends at the first line without a `|`, and at a figure: what is written right under
 * a table is not a row of it. From Pumice (packages/markdown/src/lezer.ts). */
export const tableEnds: MarkdownConfig = {
  parseBlock: [
    {
      name: 'TableEnds',
      endLeaf(_cx, line, leaf) {
        const inTable = leaf.parsers.some((parser) => Array.isArray((parser as { rows?: unknown }).rows));
        const text = line.text.slice(line.pos);
        return inTable && (!text.includes('|') || /^!\[(?:\\.|[^\]\\])*\]\(\s*cairn:/.test(text));
      },
    },
  ],
};

export const callout = /^\[!(\w+)\]\s*/;
export const calloutKinds = ['note', 'tip', 'warning', 'important'];

/** Something of a run within a line, with its width as a share of the line: `![…](cairn:…){width=50%}`. */
export type Embed = { from: number; to: number; caption: string; source: string; width?: number };
const pattern = /!\[((?:\\.|[^\]\\\n])*)\]\(\s*cairn:([^)\s]+)\s*\)(?:\{\s*width\s*=\s*(\d+(?:\.\d+)?)%\s*\})?/g;
export const embedsIn = (line: string, offset = 0): Embed[] =>
  [...line.matchAll(pattern)].map((match) => ({ from: offset + match.index, to: offset + match.index + match[0].length, caption: match[1].replace(/\\(.)/g, '$1'), source: match[2], ...(match[3] ? { width: Number(match[3]) } : {}) }));
export const embedText = ({ caption, source, width }: Pick<Embed, 'caption' | 'source' | 'width'>) =>
  `![${caption.replace(/[\\\]]/g, '\\$&')}](cairn:${source})${width && width < 100 ? `{width=${Math.round(width)}%}` : ''}`;

/** A fenced block with a line of its YAML set, or removed. */
export function setting(source: string, key: string, value: string | number | undefined) {
  const lines = source.split('\n');
  const at = lines.findIndex((line, i) => i > 0 && i < lines.length - 1 && new RegExp(`^\\s*${key}\\s*:`).test(line));
  const text = value === undefined || value === '' ? undefined : `${key}: ${typeof value === 'number' ? value : JSON.stringify(value)}`;
  if (at >= 0) lines.splice(at, 1, ...(text ? [text] : []));
  else if (text) lines.splice(lines.length - 1, 0, text);
  return lines.join('\n');
}
