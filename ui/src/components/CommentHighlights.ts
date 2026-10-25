import { useEffect, useState, type RefObject } from 'react';
import type { CommentAnchor } from './ReportComments';
type AnchoredComment={id:string;anchor:CommentAnchor};
type Position={node:Text;offset:number};

/** Match saved quotations without changing React's DOM or moving report layout. */
function findQuote(container:HTMLElement,anchor:CommentAnchor):Range|undefined {
  if(anchor.media_key)return; // Figure annotations must not highlight chart titles or labels.
  if(!anchor.quote)return;
  const walker=document.createTreeWalker(container,NodeFilter.SHOW_TEXT);
  let text='',previousBlock:Element|null=null;
  const positions:Position[]=[];
  for(let next=walker.nextNode();next;next=walker.nextNode()){
    const node=next as Text,parent=node.parentElement;
    if(!parent||parent.closest('button,summary,script,style,.native-code'))continue;
    const source=parent.closest<HTMLElement>('[data-source-path]');
    if(anchor.path&&source?.dataset.sourcePath!==anchor.path)continue;
    const block=parent.closest('p,li,h1,h2,h3,h4,pre,td,th,blockquote');
    if(previousBlock&&block!==previousBlock&&text&&!text.endsWith(' ')){text+=' ';positions.push({node,offset:0});}
    previousBlock=block;
    for(let offset=0;offset<node.data.length;offset++){
      const char=node.data[offset],space=/\s/.test(char);
      if(space&&(!text||text.endsWith(' ')))continue;
      text+=space?' ':char;positions.push({node,offset});
    }
  }
  const quote=anchor.quote.replace(/\s+/g,' ').trim();
  let start=text.indexOf(quote),best=-1,distance=Infinity;
  while(start!==-1){const pos=positions[start],line=Number(pos.node.parentElement?.closest<HTMLElement>('[data-line-start]')?.dataset.lineStart||0),delta=Math.abs(line-(anchor.line_start||0));if(delta<distance){best=start;distance=delta;}start=text.indexOf(quote,start+1);}
  if(best<0||!quote)return;
  const a=positions[best],b=positions[best+quote.length-1],range=document.createRange();
  range.setStart(a.node,a.offset);range.setEnd(b.node,b.offset+1);return range;
}

export function useCommentHighlights(container:RefObject<HTMLElement>,comments:AnchoredComment[]) {
  const [ranges,setRanges]=useState<Map<string,Range>>(new Map());
  useEffect(()=>{
    const article=container.current;if(!article)return;
    let matches=new Map<string,Range>(),mediaNodes:HTMLElement[]=[],frame=0;
    const refresh=()=>{
      frame=0;matches=new Map();
      for(const comment of comments){const range=findQuote(article,comment.anchor);if(range)matches.set(comment.id,range);}
      mediaNodes.forEach(node=>node.classList.remove('commented-media'));
      mediaNodes=comments.filter(c=>c.anchor.media_key).flatMap(c=>Array.from(article.querySelectorAll<HTMLElement>('[data-comment-media]')).filter(n=>c.anchor.media_id ? n.dataset.commentId===c.anchor.media_id : n.dataset.commentMedia===c.anchor.media_key && (c.anchor.line_start===undefined || Number(n.dataset.commentLine)===c.anchor.line_start)));
      mediaNodes.forEach(node=>node.classList.add('commented-media'));
      setRanges(matches);
      if(typeof Highlight!=='undefined'&&CSS.highlights)CSS.highlights.set('cairn-comments',new Highlight(...matches.values()));
    };
    refresh();
    const observer=new MutationObserver(()=>{if(!frame)frame=requestAnimationFrame(refresh);});
    observer.observe(article,{childList:true,subtree:true});
    const showComment=(event:MouseEvent)=>{
      if(window.getSelection()?.toString().trim())return;
      for(const [id,range] of matches){if(comments.find(c=>c.id===id)?.anchor.media_key)continue;if(Array.from(range.getClientRects()).some(r=>event.clientX>=r.left&&event.clientX<=r.right&&event.clientY>=r.top&&event.clientY<=r.bottom)){
        window.dispatchEvent(new CustomEvent('cairn:show-comment',{detail:id}));break;
      }}
    };
    article.addEventListener('click',showComment);
    return()=>{observer.disconnect();cancelAnimationFrame(frame);mediaNodes.forEach(node=>node.classList.remove('commented-media'));CSS.highlights?.delete('cairn-comments');article.removeEventListener('click',showComment);};
  },[container,comments]);
  return ranges;
}
