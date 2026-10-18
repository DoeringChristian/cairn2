import { CommentButton } from './CommentButton';
import { createPortal } from "react-dom";
import { Check, Copy } from "lucide-react";
import { CommentComposer, type CommentAnchor } from "./ReportComments";
import { useEffect, useRef, useState, type RefObject } from 'react';
/** Cairn Report's selection marker, extended with the pinned version identity. */
export function SelectionCopy({container,version}: {container:RefObject<HTMLElement>;version:string}) {
  const [selection,setSelection]=useState<{text:string;x:number;y:number;anchor:CommentAnchor}>(),[copied,setCopied]=useState(false);
  const [composer,setComposer]=useState(false),[commented,setCommented]=useState(false);
  const editing=useRef(false),savedRange=useRef<Range>();
  useEffect(()=>{if(!copied&&!commented)return;const timer=setTimeout(()=>{setCopied(false);setCommented(false);},1400);return()=>clearTimeout(timer);},[copied,commented]);
  useEffect(()=>{
    function inspect(){if(editing.current)return;const selected=window.getSelection();if(!selected?.rangeCount||!selected.toString().trim()){setSelection(undefined);return;}
      const range=selected.getRangeAt(0),start=range.startContainer,end=range.endContainer;
      if(!container.current?.contains(start)||!container.current?.contains(end)){setSelection(undefined);return;}
      const element=start.nodeType===3?start.parentElement:start as Element;
      const source=element?.closest<HTMLElement>('[data-source-path]');if(!source)return;
      const fragment=range.cloneContents();fragment.querySelectorAll('[data-latex]').forEach(n=>n.replaceWith(` $${n.getAttribute('data-latex')}$ `));
      fragment.querySelectorAll('button,summary').forEach(n=>n.remove());fragment.querySelectorAll('p,li,div,tr,td,th,h1,h2,h3,pre').forEach(n=>n.append(' '));
      const formula=element?.closest<HTMLElement>('[data-latex]');const endElement=end.nodeType===3?end.parentElement:end as Element;
      const quote=formula&&formula===endElement?.closest('[data-latex]')?`$${formula.dataset.latex}$`:(fragment.textContent||selected.toString()).replace(/\s+/g,' ').trim();
      savedRange.current=range.cloneRange();
      const rect=range.getBoundingClientRect();setCopied(false);setSelection({anchor:{quote,path:source.dataset.sourcePath,line_start:Number(source.dataset.lineStart),line_end:Number(endElement?.closest<HTMLElement>('[data-line-end]')?.dataset.lineEnd||source.dataset.lineStart)},text:`${source.dataset.sourcePath}:${source.dataset.lineStart} "${quote}" [cairn:${version}]`,x:Math.max(8,Math.min(innerWidth-102,rect.right+6)),y:Math.max(8,Math.min(innerHeight-52,rect.bottom+6))});
    }
    document.addEventListener('selectionchange',inspect);window.addEventListener('scroll',inspect,{passive:true});
    return()=>{document.removeEventListener('selectionchange',inspect);window.removeEventListener('scroll',inspect);};
  },[container,version]);
  async function copy(){if(!selection)return;try{await navigator.clipboard.writeText(selection.text);setCopied(true);}catch{
    const text=document.createElement('textarea');text.value=selection.text;text.style.cssText='position:fixed;opacity:0';document.body.append(text);text.select();const ok=document.execCommand('copy');text.remove();setCopied(ok);
  }}
  return selection?createPortal(<><div className="selection-actions" style={{left:selection.x,top:selection.y}}>
    <button className="image-copy" type="button" aria-label="Copy selected text and source" onMouseDown={e=>e.preventDefault()} onClick={copy}>{copied?<Check aria-hidden="true"/>:<Copy aria-hidden="true"/>}</button>
    <CommentButton saved={commented} aria-label="Comment on selected text" onMouseDown={e=>e.preventDefault()} onClick={()=>{editing.current=true;setComposer(true);}}/>
    <span className="sr-only" role="status">{copied?'Copied':commented?'Comment saved':''}</span>
  </div>{composer&&<CommentComposer artifact={version} anchor={selection.anchor} position={selection} onClose={saved=>{setComposer(false);if(saved)setCommented(true);requestAnimationFrame(()=>{if(savedRange.current){const native=window.getSelection();native?.removeAllRanges();native?.addRange(savedRange.current);}editing.current=false;});}}/>}</>,document.body):null;
}
