import { commentColumnX } from './commentLayout';
import { useCommentHighlights } from './CommentHighlights';
import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { Check, CheckCheck, RotateCcw, Send, Trash2 } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { api, post } from '../api';
import { useRefresh } from '../refresh';
import { ErrorNote } from './States';
import { MediaIconButton } from './media/MediaIconButton';
export type CommentAnchor={quote?:string;path?:string;line_start?:number;line_end?:number;media_key?:string;media_id?:string;media?:{time?:number;clip?:{start:number;end:number};region?:{x:number;y:number;width:number;height:number;imageWidth:number;imageHeight:number}}};
export type Comment={id:string;artifact_id:string;anchor:CommentAnchor;body:string;created_at:string;number:number;author?:string|null;revision:number;resolved_at?:string|null;resolution?:{note?:string|null;by?:string|null}|null;reply_to?:string|null;for_agent?:boolean;sent_at?:string|null};
const changed=(id?:string)=>window.dispatchEvent(new CustomEvent('cairn:comments',{detail:id}));

/** Serialize autosaves so slow creates/updates cannot overwrite newer typing. */
function useDraft(artifact:string,anchor:CommentAnchor,initial?:Comment) {
  const [body,setBody]=useState(initial?.body||''),[agent,setAgent]=useState(initial?initial.for_agent===true:true),[status,setStatus]=useState(''),[error,setError]=useState<Error>();
  const id=useRef(initial?.id),latest=useRef(body),saved=useRef(initial?.body||''),latestAgent=useRef(agent),savedAgent=useRef(agent),pending=useRef<Promise<boolean>>(),timer=useRef<ReturnType<typeof setTimeout>>();
  const creation=useRef<{anchor:CommentAnchor;body:string;for_agent:boolean;request_id:string}>(),removed=useRef(false);
  latest.current=body;latestAgent.current=agent;
  const dirty=()=>latest.current.trim()!==saved.current||(!!id.current&&latestAgent.current!==savedAgent.current);
  async function flush():Promise<boolean>{
    clearTimeout(timer.current);
    if(pending.current){await pending.current;if(dirty()&&!removed.current)return flush();return !!id.current;}
    if(removed.current)return true;
    if(!latest.current.trim())return id.current?remove():true;
    if(!dirty())return true;
    pending.current=(async()=>{setStatus('Saving…');setError(undefined);
      try{while(!removed.current&&latest.current.trim()&&dirty()){
        const text=latest.current.trim(),forAgent=latestAgent.current;
        if(!id.current){creation.current ||= {anchor,body:text,for_agent:forAgent,request_id:`ui-comment-${Date.now()}-${Math.random().toString(36).slice(2)}`};const result=await post<Comment>(`/ui-api/reports/${artifact}/comments`,creation.current);id.current=result.id;saved.current=creation.current.body;savedAgent.current=creation.current.for_agent;}
        else{await api(`/ui-api/reports/${artifact}/comments/${id.current}`,{method:'PUT',body:JSON.stringify({anchor,body:text,for_agent:forAgent})});saved.current=text;savedAgent.current=forAgent;}
        changed(id.current);
      }setStatus('Saved');return true;}catch(e){setError(e as Error);setStatus('Not saved');return false;}finally{pending.current=undefined;}
    })();return pending.current;
  }
  useEffect(()=>{if(body.trim()&&dirty())timer.current=setTimeout(()=>{void flush();},650);return()=>clearTimeout(timer.current);},[body,agent]);
  useEffect(()=>{if(status!=='Saved')return;const timeout=setTimeout(()=>setStatus(''),1400);return()=>clearTimeout(timeout);},[status]);
  async function send(){if(!id.current||!latestAgent.current||removed.current)return true;try{await post(`/ui-api/reports/${artifact}/comments/${id.current}/sending`,{});changed(id.current);return true;}catch(e){setError(e as Error);return false;}}
  async function remove(){clearTimeout(timer.current);removed.current=true;if(pending.current)await pending.current;try{if(id.current)await api(`/ui-api/reports/${artifact}/comments/${id.current}`,{method:'DELETE'});changed();return true;}catch(e){removed.current=false;setError(e as Error);return false;}}
  return {body,setBody,agent,setAgent,status,error,setError,flush,send,remove};
}

export function CommentComposer({artifact,anchor,position,onClose,initial,portalTarget,docked=false}: {artifact:string;anchor:CommentAnchor;position:{x:number;y:number};onClose:(saved:boolean)=>void;initial?:Comment;portalTarget?:HTMLElement|null;docked?:boolean}) {
  const field=useRef<HTMLTextAreaElement>(null),popover=useRef<HTMLDivElement>(null),draft=useDraft(artifact,anchor,initial);
  useEffect(()=>{field.current?.focus({preventScroll:true});},[]);
  const [viewport, setViewport] = useState(() => ({x: window.visualViewport?.offsetLeft ?? 0, y: window.visualViewport?.offsetTop ?? 0, width: window.visualViewport?.width ?? innerWidth, height: window.visualViewport?.height ?? innerHeight}));
  useEffect(() => {
    const update = () => setViewport({x: window.visualViewport?.offsetLeft ?? 0, y: window.visualViewport?.offsetTop ?? 0, width: window.visualViewport?.width ?? innerWidth, height: window.visualViewport?.height ?? innerHeight});
    window.visualViewport?.addEventListener('resize', update);window.visualViewport?.addEventListener('scroll', update);window.addEventListener('resize', update);
    return () => {window.visualViewport?.removeEventListener('resize', update);window.visualViewport?.removeEventListener('scroll', update);window.removeEventListener('resize', update);};
  }, []);
  const close=async()=>{if(await draft.flush()&&await draft.send())onClose(!!draft.body.trim());};
  const resolve=async()=>{if(!initial||!await draft.flush())return;try{await api(`/ui-api/reports/${artifact}/comments/${initial.id}/resolution`,{method:'PUT',body:JSON.stringify({resolved:!initial.resolved_at})});changed(initial.id);onClose(true);}catch(e){draft.setError(e as Error);}};
  const meta=initial&&[`v${initial.number}`,initial.author].filter(Boolean).join(' · '),resolution=initial?.resolution;
  const replies=useReportComments().replies.filter(reply=>initial&&reply.reply_to===initial.id);
  const [height,setHeight]=useState(230);
  useLayoutEffect(()=>{const node=popover.current;if(!node)return;const observer=new ResizeObserver(()=>setHeight(node.getBoundingClientRect().height));observer.observe(node);return()=>observer.disconnect();},[]);
  useLayoutEffect(()=>{
    if(!docked)return;
    const frame=requestAnimationFrame(()=>popover.current?.scrollIntoView({block:'nearest'}));
    return()=>cancelAnimationFrame(frame);
  },[docked,viewport.height,viewport.width,height]);
  const below=position.y+40;
  const top=Math.max(viewport.y+8,Math.min(viewport.y+viewport.height-Math.min(height,viewport.height-16)-8,below+height<=viewport.y+viewport.height-8?below:position.y-height-8));
  const closeCurrent=useRef(close);closeCurrent.current=close;
  const [superseded,setSuperseded]=useState(false);
  useEffect(()=>{
    // Opening another annotation retires this editor while its save finishes.
    window.dispatchEvent(new Event('cairn:open-composer'));
    const retire=()=>{setSuperseded(true);void closeCurrent.current();};
    window.addEventListener('cairn:open-composer',retire);
    return()=>window.removeEventListener('cairn:open-composer',retire);
  },[]);
  useEffect(()=>{const outside=(event:MouseEvent)=>{if(!popover.current?.contains(event.target as Node))void closeCurrent.current();};const timer=setTimeout(()=>document.addEventListener('click',outside),0);return()=>{clearTimeout(timer);document.removeEventListener('click',outside);};},[]);
  const editor=<div ref={popover} className="comment-popover" role="group" aria-label="Add report comment" style={{position:docked?'relative':undefined,width:docked?'auto':undefined,flexShrink:docked?0:undefined,visibility:superseded&&!draft.error?'hidden':undefined,left:docked?undefined:Math.max(viewport.x+8,Math.min(viewport.x+viewport.width-328,position.x)),top:docked?undefined:top,maxWidth:viewport.width-16,maxHeight:docked?Math.min(240,viewport.height*.4):viewport.y+viewport.height-8-top}}><header><span className="comment-save-status" role="status">{draft.status==='Saved'?<Check size={14} aria-label="Saved"/>:draft.status||<span className="muted">{meta}</span>}</span>{initial&&<MediaIconButton label={initial.resolved_at?'Reopen comment':'Resolve comment'} icon={initial.resolved_at?RotateCcw:CheckCheck} onMouseDown={e=>e.preventDefault()} onClick={()=>void resolve()}/>}<MediaIconButton label="Delete comment" icon={Trash2} onMouseDown={e=>e.preventDefault()} onClick={async()=>{if(await draft.remove())onClose(false);}}/></header><textarea ref={field} aria-label="Comment" placeholder="Write a comment…" maxLength={65536} rows={3} value={draft.body} onChange={e=>draft.setBody(e.target.value)} onBlur={()=>void draft.flush()} onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();void close();}}}/><AgentToggle comment={initial} checked={draft.agent} onChange={draft.setAgent}/>{replies.map(reply=><div key={reply.id} className="comment-reply"><span className="muted">{reply.author||'Reply'}</span><p>{reply.body}</p><AgentToggle comment={reply}/></div>)}{initial&&!initial.resolved_at&&<ReplyBox artifact={artifact} comment={initial} onError={draft.setError}/>}{initial?.resolved_at&&<small>Resolved{resolution?.by?` by ${resolution.by}`:''}{resolution?.note?`: ${resolution.note}`:''}</small>}{draft.error&&<ErrorNote error={draft.error}/>}</div>;
  return docked?editor:createPortal(editor,portalTarget||document.body);
}
/** Whether a comment goes to the agent when its author closes it. */
function AgentToggle({comment,checked,onChange}:{comment?:Comment;checked?:boolean;onChange?:(value:boolean)=>void}) {
  if(!onChange)return comment?.sent_at?<small>Sent to agent</small>:null;
  return <label className="comment-agent"><input type="checkbox" checked={checked} onChange={e=>onChange(e.target.checked)}/>Send to agent</label>;
}
function ReplyBox({artifact,comment,onError}:{artifact:string;comment:Comment;onError:(error:Error)=>void}) {
  const [body,setBody]=useState(''),[agent,setAgent]=useState(true),[busy,setBusy]=useState(false);
  const reply=async()=>{if(!body.trim()||busy)return;setBusy(true);
    try{const created=await post<Comment>(`/ui-api/reports/${artifact}/comments/${comment.id}/replies`,{body:body.trim(),for_agent:agent,request_id:`ui-reply-${Date.now()}-${Math.random().toString(36).slice(2)}`});
      if(agent)await post(`/ui-api/reports/${artifact}/comments/${created.id}/sending`,{});setBody('');changed(comment.id);}catch(e){onError(e as Error);}finally{setBusy(false);}};
  return <div className="comment-reply-box"><textarea aria-label="Reply" placeholder="Reply…" maxLength={65536} rows={2} value={body} onChange={e=>setBody(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){e.preventDefault();void reply();}}}/><footer><label className="comment-agent"><input type="checkbox" checked={agent} onChange={e=>setAgent(e.target.checked)}/>Send to agent</label><MediaIconButton label="Send reply" icon={Send} disabled={!body.trim()||busy} onMouseDown={e=>e.preventDefault()} onClick={()=>void reply()}/></footer></div>;
}
const CommentsContext=createContext<{items:Comment[];replies:Comment[];saved:string;error?:Error}>({items:[],replies:[],saved:''});
export const useReportComments=()=>useContext(CommentsContext);
export function ReportCommentsProvider({artifact,children}:{artifact:string;children:ReactNode}) {
  const [items,setItems]=useState<Comment[]>([]),[error,setError]=useState<Error>(),[revision,setRevision]=useState(0);
  const [saved,setSaved]=useState(''),pulse=useRefresh(),[params]=useSearchParams(),showResolved=params.get('resolved')==='1';
  useEffect(()=>{if(!saved)return;const timer=setTimeout(()=>setSaved(''),1400);return()=>clearTimeout(timer);},[saved]);
  useEffect(()=>{const update=(event:Event)=>{setRevision(n=>n+1);const id=(event as CustomEvent<string>).detail;if(id)setSaved(id);};window.addEventListener('cairn:comments',update);return()=>window.removeEventListener('cairn:comments',update);},[]);
  useEffect(()=>{const c=new AbortController();api<Comment[]>(`/ui-api/reports/${artifact}/comments`,{signal:c.signal}).then(data=>{setItems(data);setError(undefined);}).catch(e=>{if(!c.signal.aborted)setError(e);});return()=>c.abort();},[artifact,pulse,revision]);
  const visible=showResolved?items:items.filter(comment=>!comment.resolved_at);
  return <CommentsContext.Provider value={{items:visible.filter(c=>!c.reply_to),replies:visible.filter(c=>c.reply_to),saved,error}}>{children}</CommentsContext.Provider>;
}
export function ReportComments({artifact,container}: {artifact:string;project:string;container:RefObject<HTMLElement>}) {
  const {items,error}=useReportComments();
  const [editing,setEditing]=useState<{comment:Comment;x:number;y:number}>();
  const ranges=useCommentHighlights(container,items);
  useEffect(()=>{
    const article=container.current;if(!article)return;
    let frame=0;
    // Text comments open from highlights; only figures need gutter controls.
    const place=()=>{frame=0;const x=commentColumnX(article);
      const candidates:{top:number;button:HTMLButtonElement;shellTop:number}[]=[];
      for(const button of article.querySelectorAll<HTMLButtonElement>('.output-comment-button')) {
        const shell=button.closest<HTMLElement>('.native-output-shell');if(!shell)continue;
        observer.observe(shell);const rect=shell.getBoundingClientRect(),top=rect.top+8;
        button.style.left=`${x-rect.left}px`;button.style.top='8px';
        if(top>=-24&&top<=innerHeight-24)candidates.push({top,button,shellTop:rect.top});
      }
      candidates.sort((a,b)=>a.top-b.top);
      let previous=-40;
      for(const candidate of candidates){const y=Math.max(8,candidate.top,previous+48);previous=y;candidate.button.style.top=`${y-candidate.shellTop}px`;}
    };
    const schedule=()=>{if(!frame)frame=requestAnimationFrame(place);};
    const reveal=(event:Event)=>{const id=(event as CustomEvent<string>).detail,comment=items.find(c=>c.id===id);if(comment){const box=article.getBoundingClientRect(),rect=ranges.get(id)?.getBoundingClientRect();setEditing({comment,x:commentColumnX(article),y:Math.max(8,rect?.top||box.top)});}};
    const observer=new ResizeObserver(schedule);observer.observe(article);place();const mutations=new MutationObserver(schedule);mutations.observe(article,{childList:true,subtree:true});window.addEventListener('scroll',schedule,{passive:true});window.addEventListener('resize',schedule);window.addEventListener('cairn:show-comment',reveal);
    return()=>{cancelAnimationFrame(frame);observer.disconnect();mutations.disconnect();window.removeEventListener('scroll',schedule);window.removeEventListener('resize',schedule);window.removeEventListener('cairn:show-comment',reveal);};
  },[container,items,ranges]);
  const unplaced=items.filter(comment=>!comment.anchor.media_key&&!ranges.has(comment.id));
  return <>{error&&<ErrorNote error={error}/>} {!!unplaced.length&&<details className="unplaced-comments"><summary>{unplaced.length} unplaced {unplaced.length===1?'comment':'comments'}</summary><p className="muted">These passages could not be matched in this version.</p>{unplaced.map(comment=><button type="button" key={comment.id} className="text-button" onClick={event=>{const box=event.currentTarget.getBoundingClientRect();setEditing({comment,x:box.left,y:box.top});}}>{comment.body}</button>)}</details>} {editing&&<CommentComposer key={editing.comment.id} artifact={artifact} anchor={editing.comment.anchor} initial={editing.comment} position={editing} onClose={()=>setEditing(current=>current?.comment.id===editing.comment.id?undefined:current)}/>}</>;
}
