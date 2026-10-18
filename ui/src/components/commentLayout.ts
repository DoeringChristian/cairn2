/** Report-wide annotation column, shared by text, media, and selection controls. */
export function commentColumnX(article: HTMLElement) {
  return Math.min(innerWidth - 52, article.getBoundingClientRect().right + 8);
}

/** Element boxes in a browser Range can include an entire following block.
 * Use selected text fragments instead, so a full-line selection stays local. */
export function selectedTextRect(range: Range): DOMRect | undefined {
  const root = range.commonAncestorContainer;
  const nodes: Text[] = [];
  if (root.nodeType === Node.TEXT_NODE) nodes.push(root as Text);
  else {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (range.intersectsNode(node)) nodes.push(node as Text);
    }
  }
  const rects: DOMRect[] = [];
  for (const node of nodes) {
    if (!node.textContent?.trim() || node.parentElement?.closest('button,summary,script,style')) continue;
    const fragment = document.createRange();
    fragment.selectNodeContents(node);
    if (node === range.startContainer) fragment.setStart(node, range.startOffset);
    if (node === range.endContainer) fragment.setEnd(node, range.endOffset);
    if (fragment.collapsed) continue;
    rects.push(...Array.from(fragment.getClientRects()).filter(rect => rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < innerHeight));
  }
  return rects.at(-1);
}
