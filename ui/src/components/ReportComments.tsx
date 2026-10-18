import { useCommentHighlights } from './CommentHighlights';
import { useEffect, useRef, useState, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { Check, MessageSquare, Trash2 } from 'lucide-react';
import { api, post } from '../api';
import { useRefresh } from '../refresh';
import { ErrorNote } from './States';
import { MediaIconButton } from './media/MediaIconButton';
export type CommentAnchor={quote?:string;path?:string;line_start?:number;line_end?:number;media_key?:string;media?:{time?:number;clip?:{start:number;end:number};region?:{x:number;y:number;width:number;height:number;imageWidth:number;imageHeight:number}}};
type Comment={id:string;artifact_id:string;anchor:CommentAnchor;body:string;created_at:string;number:number};
const changed=(id?:string)=>window.dispatchEvent(new CustomEvent('cairn:comments',{detail:id}));

/** Serialize autosaves so slow creates/updates cannot overwrite newer typing. */
function useDraft(artifact:string,anchor:CommentAnchor,initial?:Comment) {
  const [body,setBody]=useState(initial?.body||''),[status,setStatus]=useState(''),[error,setError]=useState<Error>();
  const id=useRef(initial?.id),latest=useRef(body),saved=useRef(initial?.body||''),pending=useRef<Promise<boolean>>(),timer=useRef<ReturnType<typeof setTimeout>>();
  const creation=useRef<{anchor:CommentAnchor;body:string;request_id:string}>(),removed=useRef(false);
  latest.current=body;
  async function flush():Promise<boolean>{
    clearTimeout(timer.current);
    if(pending.current){await pending.current;if(latest.current.trim()!==saved.current&&!removed.current)return flush();return !!id.current;}
    if(removed.current)return true;
    if(!latest.current.trim())return id.current?remove():true;
    if(latest.current.trim()===saved.current)return true;
    pending.current=(async()=>{setStatus('Saving…');setError(undefined);
      try{while(!removed.current&&latest.current.trim()&&latest.current.trim()!==saved.current){
        const text=latest.current.trim();
        if(!id.current){creation.current ||= {anchor,body:text,request_id:`ui-comment-${Date.now()}-${Math.random().toString(36).slice(2)}`};const result=await post<Comment>(`/ui-api/reports/${artifact}/comments`,creation.current);id.current=result.id;saved.current=creation.current.body;}
        else{await api(`/ui-api/reports/${artifact}/comments/${id.current}`,{method:'PUT',body:JSON.stringify({anchor,body:text})});saved.current=text;}
        changed(id.current);
      }setStatus('Saved');return true;}catch(e){setError(e as Error);setStatus('Not saved');return false;}finally{pending.current=undefined;}
    })();return pending.current;
  }
  useEffect(()=>{if(body.trim()&&body.trim()!==saved.current)timer.current=setTimeout(()=>{void flush();},650);return()=>clearTimeout(timer.current);},[body]);
  useEffect(()=>{if(status!=='Saved')return;const timeout=setTimeout(()=>setStatus(''),1400);return()=>clearTimeout(timeout);},[status]);
  async function remove(){clearTimeout(timer.current);removed.current=true;if(pending.current)await pending.current;try{if(id.current)await api(`/ui-api/reports/${artifact}/comments/${id.current}`,{method:'DELETE'});changed();return true;}catch(e){removed.current=false;setError(e as Error);return false;}}
  return {body,setBody,status,error,flush,remove};
}

export function CommentComposer({artifact,anchor,position,onClose,initial,portalTarget}: {artifact:string;anchor:CommentAnchor;position:{x:number;y:number};onClose:(saved:boolean)=>void;initial?:Comment;portalTarget?:HTMLElement|null}) {
  const field=useRef<HTMLTextAreaElement>(null),popover=useRef<HTMLDivElement>(null),draft=useDraft(artifact,anchor,initial);
  useEffect(()=>{field.current?.focus({preventScroll:true});},[]);
  const close=async()=>{const saved=await draft.flush();if(saved)onClose(!!draft.body.trim());};
  const closeCurrent=useRef(close);closeCurrent.current=close;
  useEffect(()=>{const outside=(event:PointerEvent)=>{if(!popover.current?.contains(event.target as Node))void closeCurrent.current();};document.addEventListener('pointerdown',outside,true);return()=>document.removeEventListener('pointerdown',outside,true);},[]);
  return createPortal(<div ref={popover} className="comment-popover" role="group" aria-label="Add report comment" style={{left:Math.max(8,Math.min(innerWidth-328,position.x)),top:Math.max(8,Math.min(innerHeight-230,position.y+40))}}><header><span className="comment-save-status" role="status">{draft.status==='Saved'?<Check size={14} aria-label="Saved"/>:draft.status}</span><MediaIconButton label="Delete comment" icon={Trash2} onMouseDown={e=>e.preventDefault()} onClick={async()=>{if(await draft.remove())onClose(false);}}/></header><textarea ref={field} aria-label="Comment" placeholder="Write a comment…" maxLength={65536} rows={3} value={draft.body} onChange={e=>draft.setBody(e.target.value)} onBlur={()=>void draft.flush()} onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();void close();}}}/>{draft.error&&<ErrorNote error={draft.error}/>}</div>,portalTarget||document.body);
}
type Marker={comment:Comment;x:number;y:number};
export function ReportComments({artifact,container}: {artifact:string;project:string;container:RefObject<HTMLElement>}) {
  const [items,setItems]=useState<Comment[]>([]),[error,setError]=useState<Error>(),[revision,setRevision]=useState(0);
  const [editing,setEditing]=useState<{comment:Comment;x:number;y:number}>(),[markers,setMarkers]=useState<Marker[]>([]),[saved,setSaved]=useState('');
  const ranges=useCommentHighlights(container,items),pulse=useRefresh();
  useEffect(()=>{if(!saved)return;const timer=setTimeout(()=>setSaved(''),1400);return()=>clearTimeout(timer);},[saved]);
  useEffect(()=>{const update=(event:Event)=>{setRevision(n=>n+1);const id=(event as CustomEvent<string>).detail;if(id)setSaved(id);};window.addEventListener('cairn:comments',update);return()=>window.removeEventListener('cairn:comments',update);},[]);
  useEffect(()=>{const c=new AbortController();api<Comment[]>(`/ui-api/reports/${artifact}/comments`,{signal:c.signal}).then(data=>{setItems(data);setError(undefined);}).catch(e=>{if(!c.signal.aborted)setError(e);});return()=>c.abort();},[artifact,pulse,revision]);
  useEffect(()=>{
    const article=container.current;if(!article)return;
    let frame=0;
    const place=()=>{frame=0;const box=article.getBoundingClientRect(),next:Marker[]=[];
      for(const comment of items){const rect=ranges.get(comment.id)?.getBoundingClientRect();const top=rect?.top??box.top;
        if(top < -24 || top > innerHeight-24)continue;
        let y=Math.max(8,top);while(next.some(m=>Math.abs(m.y-y)<30))y+=30;
        next.push({comment,x:Math.min(innerWidth-36,box.right+8),y});
      }setMarkers(next);
    };
    const schedule=()=>{if(!frame)frame=requestAnimationFrame(place);};
    const reveal=(event:Event)=>{const id=(event as CustomEvent<string>).detail,comment=items.find(c=>c.id===id);if(comment){const box=article.getBoundingClientRect(),rect=ranges.get(id)?.getBoundingClientRect();setEditing({comment,x:Math.min(innerWidth-36,box.right+8),y:Math.max(8,rect?.top||box.top)});}};
    place();const observer=new ResizeObserver(schedule);observer.observe(article);window.addEventListener('scroll',schedule,{passive:true});window.addEventListener('resize',schedule);window.addEventListener('cairn:show-comment',reveal);
    return()=>{cancelAnimationFrame(frame);observer.disconnect();window.removeEventListener('scroll',schedule);window.removeEventListener('resize',schedule);window.removeEventListener('cairn:show-comment',reveal);};
  },[container,items,ranges]);
  return <>{error&&<ErrorNote error={error}/>} {createPortal(<>{markers.map(marker=><button key={marker.comment.id} type="button" className="image-copy report-comment-marker" aria-label="Open comment" title={marker.comment.body} style={{left:marker.x,top:marker.y}} onClick={()=>setEditing(marker)}>{saved===marker.comment.id?<Check aria-hidden="true"/>:<MessageSquare aria-hidden="true"/>}</button>)}{editing&&<CommentComposer key={editing.comment.id} artifact={artifact} anchor={editing.comment.anchor} initial={editing.comment} position={editing} onClose={()=>setEditing(current=>current?.comment.id===editing.comment.id?undefined:current)}/>}</>,document.body)}</>;
}
