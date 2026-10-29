import { selectedTextRect, selectedTextRanges } from './commentLayout';
import { CommentButton } from './CommentButton';
import { createPortal } from "react-dom";
import { Check, Copy } from "lucide-react";
import { CommentComposer, type CommentAnchor } from "./ReportComments";
import { useEffect, useRef, useState, type RefObject } from 'react';
/** Cairn Report's selection marker, extended with the pinned version identity. */
export function SelectionCopy({container,version}: {container:RefObject<HTMLElement>;version:string}) {
  const [selection,setSelection]=useState<{text:string;x:number;y:number;anchor:CommentAnchor}>(),[copied,setCopied]=useState(false);
  const [composer,setComposer]=useState(false),[commented,setCommented]=useState(false);
  const editing=useRef(false),selectedRange=useRef<Range>();
  useEffect(()=>{if(!copied&&!commented)return;const timer=setTimeout(()=>{setCopied(false);setCommented(false);},1400);return()=>clearTimeout(timer);},[copied,commented]);
  useEffect(()=>{
    function inspect(){if(editing.current)return;const selected=window.getSelection();if(!selected?.rangeCount||!selected.toString().trim()){setSelection(undefined);return;}
      const article=container.current;if(!article)return;
      const range=selected.getRangeAt(0).cloneRange(),bounds=document.createRange();bounds.selectNodeContents(article);
      if(!range.intersectsNode(article)){setSelection(undefined);return;}
      if(range.compareBoundaryPoints(Range.START_TO_START,bounds)<0)range.setStart(bounds.startContainer,bounds.startOffset);
      if(range.compareBoundaryPoints(Range.END_TO_END,bounds)>0)range.setEnd(bounds.endContainer,bounds.endOffset);
      const fragments=selectedTextRanges(range),first=fragments[0],last=fragments.at(-1);
      if(!first||!last){setSelection(undefined);return;}
      const start=first.startContainer,end=last.endContainer;
      const element=start.nodeType===3?start.parentElement:start as Element;
      const source=element?.closest<HTMLElement>('[data-source-path]');if(!source)return;
      const fragment=range.cloneContents();fragment.querySelectorAll('[data-latex]').forEach(n=>n.replaceWith(` $${n.getAttribute('data-latex')}$ `));
      fragment.querySelectorAll('button,summary').forEach(n=>n.remove());fragment.querySelectorAll('p,li,div,tr,td,th,h1,h2,h3,pre').forEach(n=>n.append(' '));
      const formula=element?.closest<HTMLElement>('[data-latex]');const endElement=end.nodeType===3?end.parentElement:end as Element;
      const quote=formula&&formula===endElement?.closest('[data-latex]')?`$${formula.dataset.latex}$`:(fragment.textContent||selected.toString()).replace(/\s+/g,' ').trim();
      selectedRange.current=range.cloneRange();const rect=selectedTextRect(range);if(!rect){setSelection(undefined);return;}setCopied(false);setSelection({anchor:{quote,path:source.dataset.sourcePath,line_start:Number(source.dataset.lineStart),line_end:Number(endElement?.closest<HTMLElement>('[data-line-end]')?.dataset.lineEnd||source.dataset.lineStart)},text:`${source.dataset.sourcePath}:${source.dataset.lineStart} "${quote}" [cairn:${version}]`,x:Math.max(8,Math.min(innerWidth-104,rect.right-80)),y:rect.bottom+52<innerHeight?rect.bottom+8:Math.max(8,rect.top-52)});
    }
    document.addEventListener('selectionchange',inspect);window.addEventListener('scroll',inspect,{passive:true});window.addEventListener('resize',inspect);
    return()=>{document.removeEventListener('selectionchange',inspect);window.removeEventListener('scroll',inspect);window.removeEventListener('resize',inspect);};
  },[container,version]);
  async function copy(){if(!selection)return;try{await navigator.clipboard.writeText(selection.text);setCopied(true);}catch{
    const text=document.createElement('textarea');text.value=selection.text;text.style.cssText='position:fixed;opacity:0';document.body.append(text);text.select();const ok=document.execCommand('copy');text.remove();setCopied(ok);
  }}
  return selection?createPortal(<><div hidden={composer} className="selection-actions" style={{left:selection.x,top:selection.y}}>
    <button className="image-copy" type="button" aria-label="Copy selected text and source" onMouseDown={e=>e.preventDefault()} onClick={copy}>{copied?<Check aria-hidden="true"/>:<Copy aria-hidden="true"/>}</button>
    <CommentButton saved={commented} aria-label="Comment on selected text" onMouseDown={e=>e.preventDefault()} onClick={()=>{editing.current=true;setComposer(true);}}/>
    <span className="sr-only" role="status">{copied?'Copied':commented?'Comment saved':''}</span>
  </div>{composer&&<CommentComposer artifact={version} anchor={selection.anchor} position={selection} getAnchorRect={()=>selectedRange.current?selectedTextRect(selectedRange.current,true):undefined} onClose={saved=>{editing.current=false;setComposer(false);if(saved)setCommented(true);document.dispatchEvent(new Event('selectionchange'));}}/>}</>,document.body):null;
}
