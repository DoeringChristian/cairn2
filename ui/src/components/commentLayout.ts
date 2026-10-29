import { useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from 'react';
/** Report-wide annotation column, for media controls. */
export function commentColumnX(article: HTMLElement) {
  return Math.min(innerWidth - 52, article.getBoundingClientRect().right + 8);
}

/** Intersect a selection with actual text, excluding the empty next-block
 * boundary browsers include after a triple click. Element endpoints are valid. */
export function selectedTextRanges(range: Range): Range[] {
  const root=range.commonAncestorContainer,nodes:Text[]=[];
  if(root.nodeType===Node.TEXT_NODE)nodes.push(root as Text);
  else {
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
    for(let node=walker.nextNode();node;node=walker.nextNode())if(range.intersectsNode(node))nodes.push(node as Text);
  }
  return nodes.flatMap(node=>{
    if(!node.textContent?.trim()||node.parentElement?.closest('button,summary,script,style'))return [];
    const fragment=document.createRange();fragment.selectNodeContents(node);
    if(range.compareBoundaryPoints(Range.START_TO_START,fragment)>0)fragment.setStart(range.startContainer,range.startOffset);
    if(range.compareBoundaryPoints(Range.END_TO_END,fragment)<0)fragment.setEnd(range.endContainer,range.endOffset);
    return fragment.collapsed||!fragment.toString().trim()?[]:[fragment];
  });
}

export function selectedTextRect(range: Range, includeOffscreen=false): DOMRect|undefined {
  return selectedTextRanges(range).flatMap(fragment=>Array.from(fragment.getClientRects()))
    .filter(rect=>rect.width>0&&rect.height>0&&(includeOffscreen||(rect.bottom>0&&rect.top<innerHeight))).at(-1);
}

/** A single open chat tracks live text/media geometry, including scroll and zoom. */
export function useAnchoredPosition(
  element: RefObject<HTMLElement>, getRect: (()=>DOMRect|undefined)|undefined,
  fallback: {x:number;y:number}, boundary?: HTMLElement|null,
) {
  const latest=useRef({getRect,fallback,boundary});latest.current={getRect,fallback,boundary};
  const [style,setStyle]=useState<CSSProperties>({left:8,top:8,maxHeight:420});
  useLayoutEffect(()=>{
    let frame=0,previous='';
    const place=()=>{
      const {getRect,fallback,boundary}=latest.current;
      const viewport=window.visualViewport;
      const bounds=boundary?.closest('dialog')?.getBoundingClientRect();
      const left=Math.max(viewport?.offsetLeft||0,bounds?.left||0)+8;
      const top=Math.max(viewport?.offsetTop||0,bounds?.top||0)+8;
      const right=Math.min((viewport?.offsetLeft||0)+(viewport?.width||innerWidth),bounds?.right??innerWidth)-8;
      const bottom=Math.min((viewport?.offsetTop||0)+(viewport?.height||innerHeight),bounds?.bottom??innerHeight)-8;
      const width=Math.min(320,right-left),maxHeight=Math.min(420,bottom-top);
      const height=Math.min(element.current?.getBoundingClientRect().height||200,maxHeight);
      const rect=getRect?.()||new DOMRect(fallback.x,fallback.y,0,0);
      const clamp=(v:number,min:number,max:number)=>Math.max(min,Math.min(max,v));
      const candidates=[
        {x:rect.right+12,y:rect.top}, {x:rect.left-width-12,y:rect.top},
        {x:rect.left,y:rect.bottom+12}, {x:rect.left,y:rect.top-height-12},
      ];
      const controls=Array.from(boundary?.querySelectorAll('.prism-lightbox-header,.prism-lightbox-tools,.prism-video-transport,.prism-lightbox-footer')||[]).map(n=>n.getBoundingClientRect());
      const overlap=(x:number,y:number,r:DOMRect)=>Math.max(0,Math.min(x+width,r.right)-Math.max(x,r.left))*Math.max(0,Math.min(y+height,r.bottom)-Math.max(y,r.top));
      const choices=candidates.map(({x,y},index)=>{
        const cx=clamp(x,left,right-width),cy=clamp(y,top,bottom-height);
        return {x:cx,y:cy,score:overlap(cx,cy,rect)*10+controls.reduce((sum,r)=>sum+overlap(cx,cy,r),0)*20+Math.abs(x-cx)+Math.abs(y-cy)+index};
      }).sort((a,b)=>a.score-b.score);
      const next={left:Math.round(choices[0].x),top:Math.round(choices[0].y),width,maxWidth:right-left,maxHeight};
      const key=JSON.stringify(next);if(key!==previous){previous=key;setStyle(next);}
      frame=requestAnimationFrame(place);
    };
    place();return()=>cancelAnimationFrame(frame);
  },[element]);
  return style;
}
