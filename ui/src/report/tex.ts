import katex from 'katex';

/** A formula as MathML, which needs no fonts; a wrong one shows in red what it says. */
export function tex(source: string, display: boolean) {
  const node = document.createElement(display ? 'div' : 'span');
  node.className = display ? 'md-math md-math-block' : 'md-math';
  try {
    katex.render(source, node, { displayMode: display, throwOnError: true, output: 'mathml' });
  } catch (error) {
    node.classList.add('md-math-wrong');
    node.textContent = source;
    node.title = (error as Error).message;
  }
  return node;
}

/** A line of Markdown as drawn in a table's cell. */
export function inline(text: string) {
  const out = document.createDocumentFragment();
  const pattern = /`([^`]+)`|\*\*(.+?)\*\*|\*([^*]+)\*|~~(.+?)~~|\$([^$\s](?:[^$]*[^$\s])?)\$|\[([^\]]+)\]\(([^)\s]+)\)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    out.append(text.slice(last, match.index));
    last = match.index + match[0].length;
    const [, code, strong, em, strike, formula, label, href] = match;
    if (formula !== undefined) out.append(tex(formula, false));
    else {
      const node = document.createElement(code ? 'code' : strong ? 'strong' : em ? 'em' : strike ? 's' : 'a');
      if (code) node.textContent = code;
      else node.append(inline(strong ?? em ?? strike ?? label));
      if (href && /^(https?:|cairn:)/.test(href)) node.setAttribute('data-href', href);
      out.append(node);
    }
  }
  out.append(text.slice(last));
  return out;
}
