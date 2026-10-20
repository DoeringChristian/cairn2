import { commentColumnX } from './commentLayout';
import { CommentButton } from './CommentButton';
import { useCommentHighlights } from './CommentHighlights';
import { createContext, useContext, useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { Check, CheckCheck, RotateCcw, Trash2 } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { api, post } from '../api';
import { useRefresh } from '../refresh';
import { ErrorNote } from './States';
import { MediaIconButton } from './media/MediaIconButton';
export type CommentAnchor={quote?:string;path?:string;line_start?:number;line_end?:number;media_key?:string;media?:{time?:number;clip?:{start:number;end:number};region?:{x:number;y:number;width:number;height:number;imageWidth:number;imageHeight:number}}};
export type Comment={id:string;artifact_id:string;anchor:CommentAnchor;body:string;created_at:string;number:number;author?:string|null;revision:number;resolved_at?:string|null;resolution?:{note?:string|null;by?:string|null}|null};
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
  return {body,setBody,status,error,setError,flush,remove};
}

export function CommentComposer({artifact,anchor,position,onClose,initial,portalTarget}: {artifact:string;anchor:CommentAnchor;position:{x:number;y:number};onClose:(saved:boolean)=>void;initial?:Comment;portalTarget?:HTMLElement|null}) {
  const field=useRef<HTMLTextAreaElement>(null),popover=useRef<HTMLDivElement>(null),draft=useDraft(artifact,anchor,initial);
  useEffect(()=>{field.current?.focus({preventScroll:true});},[]);
  const [viewport, setViewport] = useState(() => ({x: window.visualViewport?.offsetLeft ?? 0, y: window.visualViewport?.offsetTop ?? 0, width: window.visualViewport?.width ?? innerWidth, height: window.visualViewport?.height ?? innerHeight}));
  useEffect(() => {
    const update = () => setViewport({x: window.visualViewport?.offsetLeft ?? 0, y: window.visualViewport?.offsetTop ?? 0, width: window.visualViewport?.width ?? innerWidth, height: window.visualViewport?.height ?? innerHeight});
    window.visualViewport?.addEventListener('resize', update);window.visualViewport?.addEventListener('scroll', update);window.addEventListener('resize', update);
    return () => {window.visualViewport?.removeEventListener('resize', update);window.visualViewport?.removeEventListener('scroll', update);window.removeEventListener('resize', update);};
  }, []);
  const close=async()=>{const saved=await draft.flush();if(saved)onClose(!!draft.body.trim());};
  const resolve=async()=>{if(!initial||!await draft.flush())return;try{await api(`/ui-api/reports/${artifact}/comments/${initial.id}/resolution`,{method:'PUT',body:JSON.stringify({resolved:!initial.resolved_at})});changed(initial.id);onClose(true);}catch(e){draft.setError(e as Error);}};
  const meta=initial&&[`v${initial.number}`,initial.author].filter(Boolean).join(' · '),resolution=initial?.resolution;
  const closeCurrent=useRef(close);closeCurrent.current=close;
  useEffect(()=>{const outside=(event:PointerEvent)=>{if(!popover.current?.contains(event.target as Node))void closeCurrent.current();};document.addEventListener('pointerdown',outside,true);return()=>document.removeEventListener('pointerdown',outside,true);},[]);
  return createPortal(<div ref={popover} className="comment-popover" role="group" aria-label="Add report comment" style={{left:Math.max(viewport.x+8,Math.min(viewport.x+viewport.width-328,position.x)),top:Math.max(viewport.y+8,Math.min(viewport.y+viewport.height-230,position.y+40)),maxWidth:viewport.width-16,maxHeight:viewport.height-16}}><header><span className="comment-save-status" role="status">{draft.status==='Saved'?<Check size={14} aria-label="Saved"/>:draft.status||<span className="muted">{meta}</span>}</span>{initial&&<MediaIconButton label={initial.resolved_at?'Reopen comment':'Resolve comment'} icon={initial.resolved_at?RotateCcw:CheckCheck} onMouseDown={e=>e.preventDefault()} onClick={()=>void resolve()}/>}<MediaIconButton label="Delete comment" icon={Trash2} onMouseDown={e=>e.preventDefault()} onClick={async()=>{if(await draft.remove())onClose(false);}}/></header><textarea ref={field} aria-label="Comment" placeholder="Write a comment…" maxLength={65536} rows={3} value={draft.body} onChange={e=>draft.setBody(e.target.value)} onBlur={()=>void draft.flush()} onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();void close();}}}/>{initial?.resolved_at&&<small>Resolved{resolution?.by?` by ${resolution.by}`:''}{resolution?.note?`: ${resolution.note}`:''}</small>}{draft.error&&<ErrorNote error={draft.error}/>}</div>,portalTarget||document.body);
}
const CommentsContext=createContext<{items:Comment[];saved:string;error?:Error}>({items:[],saved:''});
export const useReportComments=()=>useContext(CommentsContext);
export function ReportCommentsProvider({artifact,children}:{artifact:string;children:ReactNode}) {
  const [items,setItems]=useState<Comment[]>([]),[error,setError]=useState<Error>(),[revision,setRevision]=useState(0);
  const [saved,setSaved]=useState(''),pulse=useRefresh(),[params]=useSearchParams(),showResolved=params.get('resolved')==='1';
  useEffect(()=>{if(!saved)return;const timer=setTimeout(()=>setSaved(''),1400);return()=>clearTimeout(timer);},[saved]);
  useEffect(()=>{const update=(event:Event)=>{setRevision(n=>n+1);const id=(event as CustomEvent<string>).detail;if(id)setSaved(id);};window.addEventListener('cairn:comments',update);return()=>window.removeEventListener('cairn:comments',update);},[]);
  useEffect(()=>{const c=new AbortController();api<Comment[]>(`/ui-api/reports/${artifact}/comments`,{signal:c.signal}).then(data=>{setItems(data);setError(undefined);}).catch(e=>{if(!c.signal.aborted)setError(e);});return()=>c.abort();},[artifact,pulse,revision]);
  const visible=showResolved?items:items.filter(comment=>!comment.resolved_at);
  return <CommentsContext.Provider value={{items:visible,saved,error}}>{children}</CommentsContext.Provider>;
}
type Marker={comment:Comment;x:number;y:number};
export function ReportComments({artifact,container}: {artifact:string;project:string;container:RefObject<HTMLElement>}) {
  const {items,saved,error}=useReportComments();
  const [editing,setEditing]=useState<{comment:Comment;x:number;y:number}>(),[markers,setMarkers]=useState<Marker[]>([]);
  const ranges=useCommentHighlights(container,items);
  useEffect(()=>{
    const article=container.current;if(!article)return;
    let frame=0;
    // One column and spacing pass for both text markers and media controls.
    const place=()=>{frame=0;const box=article.getBoundingClientRect(),next:Marker[]=[],x=commentColumnX(article);
      const candidates:{top:number;comment?:Comment;button?:HTMLButtonElement;shellTop?:number}[]=[];
      for(const button of article.querySelectorAll<HTMLButtonElement>('.output-comment-button')) {
        const shell=button.closest<HTMLElement>('.native-output-shell');if(!shell)continue;
        observer.observe(shell);const rect=shell.getBoundingClientRect(),top=rect.top+8;
        button.style.left=`${x-rect.left}px`;button.style.top='8px';
        if(top>=-24&&top<=innerHeight-24)candidates.push({top,button,shellTop:rect.top});
      }
      for(const comment of items){if(comment.anchor.media_key)continue;
        const top=ranges.get(comment.id)?.getBoundingClientRect().top??box.top;
        if(top>=-24&&top<=innerHeight-24)candidates.push({top,comment});
      }
      candidates.sort((a,b)=>a.top-b.top);
      let previous=-40;
      for(const candidate of candidates){const y=Math.max(8,candidate.top,previous+48);previous=y;
        if(candidate.button)candidate.button.style.top=`${y-candidate.shellTop!}px`;
        if(candidate.comment)next.push({comment:candidate.comment,x,y});
      }
      setMarkers(next);
    };
    const schedule=()=>{if(!frame)frame=requestAnimationFrame(place);};
    const reveal=(event:Event)=>{const id=(event as CustomEvent<string>).detail,comment=items.find(c=>c.id===id);if(comment){const box=article.getBoundingClientRect(),rect=ranges.get(id)?.getBoundingClientRect();setEditing({comment,x:commentColumnX(article),y:Math.max(8,rect?.top||box.top)});}};
    const observer=new ResizeObserver(schedule);observer.observe(article);place();const mutations=new MutationObserver(schedule);mutations.observe(article,{childList:true,subtree:true});window.addEventListener('scroll',schedule,{passive:true});window.addEventListener('resize',schedule);window.addEventListener('cairn:show-comment',reveal);
    return()=>{cancelAnimationFrame(frame);observer.disconnect();mutations.disconnect();window.removeEventListener('scroll',schedule);window.removeEventListener('resize',schedule);window.removeEventListener('cairn:show-comment',reveal);};
  },[container,items,ranges]);
  return <>{error&&<ErrorNote error={error}/>} {createPortal(<>{markers.map(marker=><CommentButton key={marker.comment.id} existing saved={saved===marker.comment.id} className={marker.comment.resolved_at?'report-comment-marker resolved':'report-comment-marker'} aria-label="Open comment" title={marker.comment.body} style={{left:marker.x,top:marker.y}} onClick={()=>setEditing(marker)}/>)}{editing&&<CommentComposer key={editing.comment.id} artifact={artifact} anchor={editing.comment.anchor} initial={editing.comment} position={editing} onClose={()=>setEditing(current=>current?.comment.id===editing.comment.id?undefined:current)}/>}</>,document.body)}</>;
}
