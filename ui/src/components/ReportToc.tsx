import { createPortal } from "react-dom";
import { useEffect, useState, type RefObject } from 'react';
import { List } from 'lucide-react';

type Heading = {id:string;title:string;level:number};
/** Read the rendered intermediate document so included and rich-output headings agree. */
export function ReportToc({container,documentKey}: {container:RefObject<HTMLElement>;documentKey:unknown}) {
  const [headings,setHeadings]=useState<Heading[]>([]),[active,setActive]=useState('');
  const [wide,setWide]=useState(()=>window.matchMedia('(min-width: 761px)').matches);
  const [open,setOpen]=useState(false);
  useEffect(()=>{const query=window.matchMedia('(min-width: 761px)');const change=()=>setWide(query.matches);query.addEventListener('change',change);return()=>query.removeEventListener('change',change);},[]);
  useEffect(()=>{
    const article=container.current;if(!article)return;
    const nodes=Array.from(article.querySelectorAll<HTMLElement>('h1,h2,h3,h4'));
    const used=new Set(Array.from(document.querySelectorAll('[id]')).map(e=>e.id));
    const entries=nodes.map((node,index)=>{
      if(!node.id){const stem=`report-heading-${(node.textContent||'section').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,'-').replace(/^-|-$/g,'')||index}`;let id=stem,number=2;while(used.has(id))id=`${stem}-${number++}`;node.id=id;used.add(id);}
      return {id:node.id,title:node.textContent?.trim()||'Section',level:Number(node.tagName.slice(1))};
    });setHeadings(entries);
    let frame=0;
    const update=()=>{frame=0;let current=nodes[0];for(const node of nodes){if(node.getBoundingClientRect().top<=120)current=node;else break;}setActive(current?.id||'');};
    const schedule=()=>{if(!frame)frame=requestAnimationFrame(update);};
    update();window.addEventListener('scroll',schedule,{passive:true});window.addEventListener('resize',schedule);
    // A direct heading link is available only after the asynchronous document loads.
    const hash=decodeURIComponent(window.location.hash.slice(1));
    if(hash&&entries.some(h=>h.id===hash))document.getElementById(hash)?.scrollIntoView();
    return()=>{cancelAnimationFrame(frame);window.removeEventListener('scroll',schedule);window.removeEventListener('resize',schedule);};
  },[container,documentKey]);
  if(!headings.length)return null;
  const top=Math.min(...headings.map(h=>h.level));
  const toc = <nav className="report-toc" aria-label="Table of contents"><button type="button" className="report-toc-toggle" aria-expanded={wide||open} aria-controls="report-toc-list" onClick={()=>setOpen(!open)}><List size={17} aria-hidden="true"/>On this page</button>{(wide||open)&&<ol id="report-toc-list">{headings.map(h=><li key={h.id} style={{paddingLeft:Math.min(h.level-top,3)*12}}><a href={`#${h.id}`} aria-current={active===h.id?'location':undefined} onClick={event=>{event.preventDefault();history.replaceState(null,'',`#${h.id}`);document.getElementById(h.id)?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});setActive(h.id);if(!wide)setOpen(false);}}>{h.title}</a></li>)}</ol>}</nav>;
  const slot=wide?document.getElementById("report-toc-slot"):null;
  return slot?createPortal(toc,slot):toc;
}
