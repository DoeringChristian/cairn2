import { commentColumnX, selectedTextRect, useAnchoredPosition } from './commentLayout';
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
const draftRequestId=()=>globalThis.crypto?.randomUUID?.()||`ui-comment-${Date.now()}-${Math.random().toString(36).slice(2)}`;

/** Drafts use the existing unsent comments/replies; only explicit send queues them. */
function useDraft(artifact:string,anchor:CommentAnchor,initial:Comment|undefined,replyTo:string|undefined,onSaved:(comment:Comment)=>void,onDeleted:(id:string)=>void) {
  const [body,setBody]=useState(initial?.body||''),[status,setStatus]=useState(''),[error,setError]=useState<Error>(),[busy,setBusy]=useState(false);
  const record=useRef(initial),latest=useRef(body),saved=useRef(initial?.body||''),pending=useRef<Promise<boolean>>(),timer=useRef<ReturnType<typeof setTimeout>>();
  const requestId=useRef(draftRequestId()),creation=useRef<{anchor?:CommentAnchor;body:string;for_agent:boolean;request_id:string}>(),removed=useRef(false),sending=useRef(false),parent=useRef(replyTo),notify=useRef(onSaved),forget=useRef(onDeleted);
  latest.current=body;notify.current=onSaved;forget.current=onDeleted;
  const base=`/ui-api/reports/${artifact}/comments`;
  async function flush():Promise<boolean>{
    clearTimeout(timer.current);
    if(pending.current){if(!await pending.current)return false;return flush();}
    if(removed.current||record.current?.sent_at)return true;
    const text=latest.current.trim();
    if(text===saved.current)return true;
    pending.current=(async()=>{
      setStatus('Saving…');setError(undefined);
      try {
        if(!text){if(record.current){await api(`${base}/${record.current.id}`,{method:'DELETE'});forget.current(record.current.id);}record.current=undefined;requestId.current=draftRequestId();creation.current=undefined;}
        else {
          if(!record.current)creation.current ||= {...(parent.current?{}:{anchor}),body:text,for_agent:true,request_id:requestId.current};
          const result=record.current
            ?await api<Comment>(`${base}/${record.current.id}`,{method:'PUT',body:JSON.stringify({anchor,body:text,for_agent:true})})
            :await post<Comment>(parent.current?`${base}/${parent.current}/replies`:base,creation.current);
          record.current=result;saved.current=result.body;notify.current(result);
        }
        if(!text)saved.current='';changed(record.current?.id);setStatus('Saved');return true;
      }catch(e){setError(e as Error);setStatus('Not saved');return false;}
      finally{pending.current=undefined;}
    })();
    const ok=await pending.current;
    return ok&&latest.current.trim()!==saved.current?flush():ok;
  }
  useEffect(()=>{if(body.trim()!==saved.current&&!sending.current)timer.current=setTimeout(()=>void flush(),650);return()=>clearTimeout(timer.current);},[body]);
  const flushCurrent=useRef(flush);flushCurrent.current=flush;
  useEffect(()=>()=>{void flushCurrent.current();},[]);
  async function send(){
    if(sending.current||!latest.current.trim())return;
    sending.current=true;setBusy(true);setError(undefined);
    try {
      if(!await flush()||!record.current)return;
      if(!record.current.for_agent)record.current=await api<Comment>(`${base}/${record.current.id}`,{method:'PUT',body:JSON.stringify({anchor,body:record.current.body,for_agent:true})});
      const result=await post<Comment>(`${base}/${record.current.id}/sending`,{});
      notify.current(result);parent.current ||= result.id;record.current=undefined;
      latest.current='';saved.current='';setBody('');requestId.current=draftRequestId();creation.current=undefined;setStatus('');changed(result.id);
    }catch(e){setError(e as Error);setStatus('Not sent');}
    finally{sending.current=false;setBusy(false);}
  }
  async function remove(threadId?:string){
    clearTimeout(timer.current);removed.current=true;if(pending.current)await pending.current;
    try{const id=threadId||record.current?.id;if(id)await api(`${base}/${id}`,{method:'DELETE'});changed();return true;}
    catch(e){removed.current=false;setError(e as Error);return false;}
  }
  return {body,setBody,status,error,setError,flush,send,remove,busy};
}

export function CommentComposer({artifact,anchor,position,getAnchorRect,onClose,initial,portalTarget,autoFocus=true,dismissOnSelectionWithin}: {
  artifact:string;anchor:CommentAnchor;position:{x:number;y:number};getAnchorRect?:()=>DOMRect|undefined;
  onClose:(saved:boolean)=>void;initial?:Comment;portalTarget?:HTMLElement|null;autoFocus?:boolean;dismissOnSelectionWithin?:HTMLElement|null;
}) {
  const context=useReportComments(),[root,setRoot]=useState(initial),[local,setLocal]=useState<Comment[]>([]);
  const currentRoot=context.items.find(c=>c.id===root?.id)||root;
  const [initialDraft]=useState(()=>initial&&!initial.sent_at?initial:context.replies.find(c=>c.reply_to===initial?.id&&c.for_agent&&!c.sent_at));
  const remember=(comment:Comment)=>{setLocal(rows=>[...rows.filter(c=>c.id!==comment.id),comment]);if(!comment.reply_to)setRoot(comment);};
  const draft=useDraft(artifact,anchor,initialDraft,initial?.sent_at?initial.id:undefined,remember,id=>{setRoot(current=>current?.id===id?undefined:current);setLocal(rows=>rows.filter(c=>c.id!==id));});
  const messages=Array.from(new Map([...context.replies.filter(c=>c.reply_to===root?.id),...(currentRoot?[currentRoot]:[]),...local].map(c=>[c.id,c])).values())
    .filter(c=>c.sent_at||(c.reply_to&&!c.for_agent)||(c.id===currentRoot?.id&&currentRoot.resolved_at)).sort((a,b)=>(a.created_at||'').localeCompare(b.created_at||''));
  const field=useRef<HTMLTextAreaElement>(null),popover=useRef<HTMLDivElement>(null),history=useRef<HTMLDivElement>(null);
  const placement=useAnchoredPosition(popover,getAnchorRect,position,portalTarget);
  useEffect(()=>{if(autoFocus)field.current?.focus({preventScroll:true});},[autoFocus]);
  useLayoutEffect(()=>{history.current?.scrollTo({top:history.current.scrollHeight});},[messages.length]);
  const close=async()=>{if(draft.busy)return;if(await draft.flush())onClose(!!root||!!draft.body.trim());};
  const resolve=async()=>{if(!currentRoot||!await draft.flush())return;try{await api(`/ui-api/reports/${artifact}/comments/${currentRoot.id}/resolution`,{method:'PUT',body:JSON.stringify({resolved:!currentRoot.resolved_at})});changed(currentRoot.id);onClose(true);}catch(e){draft.setError(e as Error);}};
  const closeCurrent=useRef(close);closeCurrent.current=close;
  // A rapid multi-click can finish before the outside-click listener mounts.
  // Observe the resulting selection as well, without stealing native text focus.
  useEffect(()=>{
    if(!dismissOnSelectionWithin)return;
    const selecting=()=>{const selection=window.getSelection();if(selection?.toString().trim()&&dismissOnSelectionWithin.contains(selection.anchorNode))void closeCurrent.current();};
    document.addEventListener('selectionchange',selecting);selecting();
    return()=>document.removeEventListener('selectionchange',selecting);
  },[dismissOnSelectionWithin]);
  const [superseded,setSuperseded]=useState(false);
  useEffect(()=>{
    window.dispatchEvent(new Event('cairn:open-composer'));
    const retire=()=>{setSuperseded(true);void closeCurrent.current();};
    window.addEventListener('cairn:open-composer',retire);
    return()=>window.removeEventListener('cairn:open-composer',retire);
  },[]);
  useEffect(()=>{if(superseded&&!draft.busy&&!draft.error)void closeCurrent.current();},[superseded,draft.busy,draft.error]);
  useEffect(()=>{const outside=(event:MouseEvent)=>{if(!popover.current?.contains(event.target as Node))void closeCurrent.current();};const timer=setTimeout(()=>document.addEventListener('click',outside),0);return()=>{clearTimeout(timer);document.removeEventListener('click',outside);};},[]);
  const editor=<div ref={popover} className="comment-popover" role="group" aria-label="Add report comment" style={{...placement,visibility:superseded&&!draft.error?'hidden':undefined}}>
    <header><strong className="comment-title">Discussion</strong><span className="comment-save-status" role="status">{draft.status==='Saved'?<><Check size={14} aria-label="Saved"/> Draft saved</>:draft.status}</span>
      {currentRoot&&<MediaIconButton label={currentRoot.resolved_at?'Reopen comment':'Resolve comment'} icon={currentRoot.resolved_at?RotateCcw:CheckCheck} disabled={draft.busy} onMouseDown={e=>e.preventDefault()} onClick={()=>void resolve()}/>}
      <MediaIconButton label="Delete comment" icon={Trash2} disabled={draft.busy} onMouseDown={e=>e.preventDefault()} onClick={async()=>{if(await draft.remove(root?.id))onClose(false);}}/>
    </header>
    {currentRoot&&<small className="comment-context">{[`v${currentRoot.number}`,currentRoot.author].filter(Boolean).join(' · ')}</small>}
    {!!messages.length&&<div className="comment-messages" ref={history} role="log" aria-label="Messages">{messages.map(message=><div key={message.id} className="comment-reply"><span className="muted">{message.author||(message.for_agent?'You':'Reply')}</span><p>{message.body}</p>{message.sent_at&&<small>Sent to agent</small>}</div>)}</div>}
    {currentRoot?.resolved_at?<small>Resolved{currentRoot.resolution?.note?`: ${currentRoot.resolution.note}`:''}</small>:<div className="comment-reply-box">
      <textarea ref={field} aria-label="Comment" placeholder={messages.length?'Reply…':'Write a message…'} maxLength={65536} rows={3} value={draft.body} disabled={draft.busy} onChange={e=>draft.setBody(e.target.value)} onBlur={()=>void draft.flush()} onKeyDown={e=>{
        if(e.nativeEvent.isComposing||e.keyCode===229)return;
        if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();void draft.send().then(()=>field.current?.focus({preventScroll:true}));}
        if(e.key==='Escape'){e.preventDefault();e.stopPropagation();void close();}
      }}/>
      <footer><small>Enter to send · Shift+Enter for a new line</small><MediaIconButton label="Send message" icon={Send} disabled={!draft.body.trim()||draft.busy} onMouseDown={e=>e.preventDefault()} onClick={()=>void draft.send().then(()=>field.current?.focus({preventScroll:true}))}/></footer>
    </div>}
    {draft.error&&<ErrorNote error={draft.error}/>}
  </div>;
  return createPortal(editor,portalTarget||document.body);
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
  return <>{error&&<ErrorNote error={error}/>} {!!unplaced.length&&<details className="unplaced-comments"><summary>{unplaced.length} unplaced {unplaced.length===1?'comment':'comments'}</summary><p className="muted">These passages could not be matched in this version.</p>{unplaced.map(comment=><button type="button" key={comment.id} className="text-button" onClick={event=>{const box=event.currentTarget.getBoundingClientRect();setEditing({comment,x:box.left,y:box.top});}}>{comment.body}</button>)}</details>} {editing&&<CommentComposer autoFocus={false} dismissOnSelectionWithin={container.current} key={editing.comment.id} artifact={artifact} anchor={editing.comment.anchor} initial={editing.comment} position={editing} getAnchorRect={()=>{const range=ranges.get(editing.comment.id);return range?selectedTextRect(range,true):undefined;}} onClose={()=>setEditing(current=>current?.comment.id===editing.comment.id?undefined:current)}/>}</>;
}
