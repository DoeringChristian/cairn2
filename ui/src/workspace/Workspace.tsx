import { useEffect, useMemo, useState, type CSSProperties, type PointerEvent } from 'react';
import { Link, Navigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowDown, ArrowLeft, ArrowUp, ChevronDown, ChevronRight, Copy, Eye, EyeOff, Grip, Maximize2, Plus, RefreshCw, Save, Settings2, SlidersHorizontal, Trash2, Undo2 } from 'lucide-react';
import { api, artifactPath, projectPath, type ArtifactSummary } from '../api';
import { Select } from '../components/Select';
import { Button, ActionIcon } from '../components/Button';
import { CardContent } from './CardContent';
import { CardEditor } from './CardEditor';
import { WorkspaceDialog } from './Dialog';
import { cardWidths, defaultLayout, discover, label, newCard, snapWidth, uniformSections, uid, type Card, type Layout } from './model';
import { useObservations, useRun, useRuns } from './data';
import { readSaved, storageKey, type SavedWorkspace } from './storage';
import { runColor } from './presentation';
import './workspace.css';

export function WorkspacePage(){const {project=''}=useParams();return <Workspace key={project} project={project}/>;}
// Keep links from the initial run-workspace UI pointing at the run's Workspace tab.
export function RunWorkspaceRedirect(){const {project='',runId=''}=useParams();const [params]=useSearchParams();const query=new URLSearchParams(params);query.set('tab','workspace');return <Navigate replace to={`${artifactPath(project,runId)}?${query}`}/>;}
export function Workspace({project,runId}:{project:string;runId?:string}) {
  const Surface=runId?'div':'main';
  const [params,setParams]=useSearchParams();
  const [layout,setLayout]=useState(()=>uniformSections(defaultLayout(params.get('example')==='cards')));
  const [saved,setSaved]=useState<Layout[]>([]),[key,setKey]=useState(''),[storageError,setStorageError]=useState(''),[loaded,setLoaded]=useState(false);
  const [history,setHistory]=useState<Layout[]>([]),[revision,setRevision]=useState(0),[showRuns,setShowRuns]=useState(false);
  const [editor,setEditor]=useState<{card:Card;creating:boolean;changed?:boolean}>(),[expanded,setExpanded]=useState<Card>();
  const [naming,setNaming]=useState<{kind:'save'|'section'|'rename';value:string;id?:string}>();
  const [dragged,setDragged]=useState<string>();
  const runs=useRuns(project,revision,!runId),focused=useRun(project,runId,revision);
  useEffect(()=>{let closed=false;void api<{repository_id:string}>('/ui-api/session').then(session=>{
    if(closed)return;const nextKey=storageKey(session.repository_id,project);
    try{const stored=readSaved(nextKey);setKey(nextKey);if(stored){setSaved(stored.layouts);if(!params.has('example'))setLayout(uniformSections(stored.layouts.find(l=>l.id===stored.active)||stored.layouts[0]||defaultLayout()));}}
    catch(error){setStorageError(String(error));}finally{setLoaded(true);}
  }).catch(error=>{if(!closed){setStorageError(String(error));setLoaded(true);}});return()=>{closed=true;};},[project]);
  const candidates=useMemo(()=>{
    const eligible=runs.items.filter(r=>(!layout.status||r.state===layout.status||r.outcome===layout.status)&&`${r.name} ${r.id} ${JSON.stringify(r.annotations)}`.toLowerCase().includes(layout.search.toLowerCase()));
    if(layout.versions==='all')return eligible;
    const latest=new Map<string,ArtifactSummary>();for(const run of eligible){const name=`${run.namespace}/${run.name}`;const previous=latest.get(name);if(!previous||run.created_at>previous.created_at||(run.created_at===previous.created_at&&(run.number||Infinity)>(previous.number||Infinity)))latest.set(name,run);}return [...latest.values()];
  },[runs.items,layout.search,layout.status,layout.versions]);
  const runPrefix=useMemo(()=>{
    if(!candidates.length)return '';
    const parts=candidates[0].name.split('/').slice(0,-1);
    while(parts.length&&!candidates.every(run=>run.name.startsWith(parts.join('/')+'/')))parts.pop();
    return parts.length?parts.join('/')+'/':'';
  },[candidates]);
  const selected=useMemo(()=>runId?(focused.run?[focused.run]:[]):candidates.filter(r=>layout.selected===null||layout.selected.includes(r.id)),[runId,focused.run,candidates,layout.selected]);
  const data=useObservations(project,loaded?selected:[],revision),fields=useMemo(()=>discover(data.rows),[data.rows]);
  const visibleRows=data.rows;
  const hiddenCount=candidates.length-selected.length;
  const allVisible=candidates.length>0&&hiddenCount===0;
  const scopeLoading=runId?focused.loading:runs.loading;
  const scopeError=runId?focused.error:runs.error;
  const dirty=JSON.stringify(saved.find(l=>l.id===layout.id))!==JSON.stringify(layout);
  function change(next:Layout,remember=true){if(remember)setHistory(h=>[...h.slice(-29),layout]);setLayout(uniformSections(next));}
  function patch(value:Partial<Layout>){change({...layout,...value});}
  function persist(next:Layout){if(!key)return;try{const layouts=[...saved.filter(l=>l.id!==next.id),next];const store:SavedWorkspace={version:1,active:next.id,layouts};localStorage.setItem(key,JSON.stringify(store));setSaved(layouts);setLayout(next);setStorageError('');if(params.has('example')){const query=new URLSearchParams(params);query.delete('example');setParams(query,{replace:true});}}catch(error){setStorageError(`Could not save this layout: ${String(error)}`);}}
  function move(id:string,delta:number){const cards=[...layout.cards],index=cards.findIndex(c=>c.id===id),section=cards[index].section;let target=index+delta;while(target>=0&&target<cards.length&&cards[target].section!==section)target+=delta;if(target<0||target>=cards.length)return;[cards[index],cards[target]]=[cards[target],cards[index]];patch({cards});}
  const sectionSizes=Object.fromEntries(layout.sections.map(section=>{const card=layout.cards.find(c=>c.section===section.id);return [section.id,card?{width:card.width,height:card.height}:undefined];}));
  const createCard=(section:string)=>({...newCard('scalar',section),...sectionSizes[section]});
  function sizeSection(section:string,width:number,height:number){patch({cards:layout.cards.map(c=>c.section===section?{...c,width:snapWidth(width),height}:c)});}
  function drop(section:string,before?:string){if(!dragged||dragged===before)return;const card=layout.cards.find(c=>c.id===dragged)!;const cards=layout.cards.filter(c=>c.id!==dragged);const index=before?cards.findIndex(c=>c.id===before):cards.length;cards.splice(index,0,{...card,section,...sectionSizes[section]});patch({cards});setDragged(undefined);}
  function resize(event:PointerEvent<HTMLButtonElement>,card:Card){
    event.preventDefault();const handle=event.currentTarget,element=handle.closest<HTMLElement>('.workspace-card')!,grid=element.parentElement!;
    const startX=event.clientX,startY=event.clientY,gridWidth=grid.getBoundingClientRect().width;
    const peers=Array.from(grid.querySelectorAll<HTMLElement>(':scope > .workspace-card'));
    const preview=(width:number,height:number)=>peers.forEach(peer=>{peer.style.setProperty('--card-span',String(width));peer.style.height=`${height}px`;});
    let width=card.width,height=card.height;handle.setPointerCapture(event.pointerId);
    const update=(e:globalThis.PointerEvent)=>{width=snapWidth(card.width+(e.clientX-startX)/(gridWidth+16)*12);height=Math.max(280,Math.min(1000,Math.round((card.height+e.clientY-startY)/20)*20));preview(width,height);};
    const finish=(e:globalThis.PointerEvent)=>{handle.removeEventListener('pointermove',update);handle.removeEventListener('pointerup',finish);handle.removeEventListener('pointercancel',cancel);if(handle.hasPointerCapture(e.pointerId))handle.releasePointerCapture(e.pointerId);sizeSection(card.section,width,height);};
    const cancel=()=>{handle.removeEventListener('pointermove',update);handle.removeEventListener('pointerup',finish);handle.removeEventListener('pointercancel',cancel);preview(card.width,card.height);};
    handle.addEventListener('pointermove',update);handle.addEventListener('pointerup',finish);handle.addEventListener('pointercancel',cancel);
  }
  return <Surface className={`workspace-page ${runId?'workspace-embedded':''}`}>
    {runId&&<div className="workspace-run-breadcrumb"><Link to={`${projectPath(project)}/workspace`}><ArrowLeft size={14}/> Project workspace</Link></div>}
    {!runId&&<header className="workspace-heading"><div><span className="eyebrow">{project} / Workspace</span><h1>Watch the work unfold.</h1><p>Explore records as they arrive. Arrange the views that matter.</p></div><Button primary onClick={()=>setEditor({card:createCard(layout.sections[0].id),creating:true})}><Plus size={16}/> Add card</Button></header>}
    <div className="workspace-toolbar"><div className="workspace-view-select"><span>Layout</span><Select label="Saved layout" value={layout.id} disabled={!loaded} choices={[...(!saved.some(l=>l.id===layout.id)?[{value:layout.id,label:layout.name}]:[]),...saved.map(l=>({value:l.id,label:l.name})),{value:'__new',label:'+ New workspace'}]} onChange={value=>{if(value==='__new'){change({...defaultLayout(),name:'Untitled workspace',sections:[{id:uid(),title:'Overview',collapsed:false}],cards:[]});return;}const next=saved.find(l=>l.id===value);if(next){change(next);if(key)try{localStorage.setItem(key,JSON.stringify({version:1,active:next.id,layouts:saved}));}catch(error){setStorageError(String(error));}}}}/></div><span className="workspace-save-status">{dirty?'Unsaved changes':'Saved in this browser'}</span><Button small disabled={!key||!dirty} onClick={()=>persist(layout)}><Save size={14}/> Save</Button><Button small disabled={!key} onClick={()=>setNaming({kind:'save',value:`${layout.name} copy`})}>Save as…</Button><span className="workspace-toolbar-spacer"/>{runId&&<Button small primary onClick={()=>setEditor({card:createCard(layout.sections[0].id),creating:true})}><Plus size={16}/> Add card</Button>}{!runId&&<Button small className="workspace-run-toggle" aria-expanded={showRuns} onClick={()=>setShowRuns(!showRuns)}><SlidersHorizontal size={14}/> {selected.length} runs</Button>}<ActionIcon label="Undo layout change" disabled={!history.length} onClick={()=>{persist(history[history.length-1]);setHistory(h=>h.slice(0,-1));}}><Undo2 size={16}/></ActionIcon><ActionIcon label="Refresh workspace" onClick={()=>setRevision(n=>n+1)}><RefreshCw size={16}/></ActionIcon></div>
    {storageError&&<p className="workspace-error" role="alert">{storageError}</p>}
    <div className={`workspace-body ${runId?'single-run':''}`}>{!runId&&<aside className={`workspace-run-panel ${showRuns?'open':''}`} aria-label="Run selection"><header className="workspace-runs-heading"><h2>Runs</h2><span>{selected.length} visible{hiddenCount?` · ${hiddenCount} hidden`:""}</span><button className="workspace-run-visibility" aria-label={allVisible?'Hide all runs':'Show all runs'} title={allVisible?'Hide all runs':'Show all runs'} aria-pressed={allVisible} disabled={!candidates.length} onClick={()=>patch({selected:allVisible?[]:null})}>{allVisible?<Eye size={14}/>:<EyeOff size={14}/>}</button></header><div className="workspace-run-filters"><label className="workspace-field"><span>Find runs · name, ID, tags</span><input value={layout.search} onChange={e=>patch({search:e.target.value})} placeholder="All runs"/></label><div className="workspace-field"><span>Versions</span><Select label="Versions" value={layout.versions} onChange={versions=>patch({versions:versions as Layout['versions']})} choices={[{value:'latest',label:'Latest run per name'},{value:'all',label:'All versions'}]}/></div><div className="workspace-field"><span>Status</span><Select label="Status" value={layout.status} onChange={status=>patch({status})} choices={['','live','closing','finished','completed','failed','cancelled','incomplete'].map(value=>({value,label:value||'Any status'}))}/></div></div><div className="workspace-run-list">{candidates.map(run=>{const hidden=!selected.some(item=>item.id===run.id);return <div key={run.id} className={`workspace-run-row ${hidden?'is-hidden':''}`}>

      <i className="workspace-run-swatch" style={{background:runColor(run.id)}} aria-hidden="true"/>
      <Link className="workspace-run-name" aria-label={`Open run ${label(run)}`} title={label(run)} to={artifactPath(project,run.id)}>{run.name.slice(runPrefix.length)}</Link><span className="workspace-run-version">{run.number?'v'+run.number:run.state}</span>
      <span className={`workspace-run-status ${run.outcome||run.state}`} role="img" aria-label={run.outcome||run.state} title={run.outcome||run.state}/>
      <button className="workspace-run-visibility" aria-label={`${hidden?'Show':'Hide'} ${label(run)}`} aria-pressed={!hidden} onClick={()=>patch({selected:hidden?[...selected.map(item=>item.id),run.id]:selected.filter(item=>item.id!==run.id).map(item=>item.id)})}>{hidden?<EyeOff size={14}/>:<Eye size={14}/>}</button>
    </div>;})}</div><p className="workspace-setting-note">{layout.selected===null?'All matching runs are shown automatically.':'Visibility is pinned to the runs shown.'} Latest is the most recently created run for each name among matching runs.</p></aside>}<div className="workspace-canvas">
    <div className="workspace-data-status" role="status">{scopeLoading||data.loading?'Loading complete record streams…':`${selected.length} ${selected.length===1?'run':'runs'} · ${data.rows.length.toLocaleString()} records${!runId&&hiddenCount?` · ${hiddenCount} hidden`:""}`}{!data.loading&&selected.some(r=>r.state!=='finished')?' · Live':''}<span>{runId?'Project panels · this run’s data':'Changes to cards affect this view only.'}</span></div>
    {(scopeError||data.error)&&<p className="workspace-error" role="alert">{String(scopeError||data.error)}</p>}
    {!scopeLoading&&!selected.length&&!scopeError&&<p className="workspace-empty">No runs visible. Use the eye controls to display runs.</p>}
    {layout.sections.map((section,index)=><section className="workspace-section" key={section.id} onDragOver={e=>{if(dragged)e.preventDefault();}} onDrop={e=>{e.preventDefault();drop(section.id);}}>
      <header className="workspace-section-heading"><button className="workspace-section-toggle" aria-expanded={!section.collapsed} onClick={()=>patch({sections:layout.sections.map(s=>s.id===section.id?{...s,collapsed:!s.collapsed}:s)})}>{section.collapsed?<ChevronRight size={18}/>:<ChevronDown size={18}/>}<h2>{section.title}</h2><span>{layout.cards.filter(c=>c.section===section.id).length}</span></button><div className="workspace-section-actions"><><ActionIcon label={`Rename ${section.title}`} onClick={()=>setNaming({kind:'rename',value:section.title,id:section.id})}><Settings2 size={15}/></ActionIcon><ActionIcon label={`Move ${section.title} up`} disabled={index===0} onClick={()=>{const sections=[...layout.sections];[sections[index-1],sections[index]]=[sections[index],sections[index-1]];patch({sections});}}><ArrowUp size={15}/></ActionIcon><ActionIcon label={`Remove ${section.title}`} disabled={layout.sections.length===1} onClick={()=>patch({sections:layout.sections.filter(s=>s.id!==section.id),cards:layout.cards.map(c=>c.section===section.id?{...c,section:layout.sections.find(s=>s.id!==section.id)!.id,...sectionSizes[layout.sections.find(s=>s.id!==section.id)!.id]}:c)})}><Trash2 size={15}/></ActionIcon></><ActionIcon label={`Add card to ${section.title}`} onClick={()=>setEditor({card:createCard(section.id),creating:true})}><Plus size={16}/></ActionIcon></div></header>
      {!section.collapsed&&<div className="workspace-grid">{layout.cards.filter(c=>c.section===section.id).map(card=>{return <article key={card.id} className="workspace-card" style={{'--card-span':card.width,height:card.height} as CSSProperties} aria-label={card.title} onDragOver={e=>{if(dragged)e.preventDefault();}} onDrop={e=>{e.preventDefault();e.stopPropagation();drop(section.id,card.id);}}>
        <header className="workspace-card-heading"><span className="workspace-drag-handle" draggable onDragStart={e=>{setDragged(card.id);e.dataTransfer.setData('text/plain',card.id);e.dataTransfer.effectAllowed='move';}} onDragEnd={()=>setDragged(undefined)} title="Drag to reorder"><Grip size={16}/></span><h3 title={card.title}>{card.title}</h3><ActionIcon label={`Settings for ${card.title}`} onClick={()=>setEditor({card,creating:false})}><Settings2 size={16}/></ActionIcon><ActionIcon label={`Expand ${card.title}`} onClick={()=>setExpanded(card)}><Maximize2 size={15}/></ActionIcon><details className="workspace-card-menu"><summary aria-label={`Actions for ${card.title}`}>•••</summary><div><Button small onClick={()=>move(card.id,-1)}><ArrowUp size={14}/> Move earlier</Button><Button small onClick={()=>move(card.id,1)}><ArrowDown size={14}/> Move later</Button><Button small onClick={()=>patch({cards:[...layout.cards,{...card,id:uid(),title:`${card.title} copy`}]})}><Copy size={14}/> Duplicate</Button><Button small onClick={()=>patch({cards:layout.cards.filter(c=>c.id!==card.id)})}><Trash2 size={14}/> Remove</Button></div></details></header>
        {scopeLoading||data.loading?<div className="workspace-empty">Loading records…</div>:scopeError||data.error?<div className="workspace-empty">Data could not be loaded. Try a smaller run selection or refresh.</div>:<CardContent card={card} rows={visibleRows}/>}
        <button className="workspace-resize" aria-label={`Resize ${card.title}`} title="Resize all cards in this section; arrow keys adjust width and height" onPointerDown={e=>resize(e,card)} onKeyDown={e=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;e.preventDefault();const index=cardWidths.indexOf(card.width as typeof cardWidths[number]);sizeSection(card.section,cardWidths[Math.max(0,Math.min(2,index+(e.key==='ArrowRight'?1:e.key==='ArrowLeft'?-1:0)))],Math.max(280,Math.min(1000,card.height+(e.key==='ArrowDown'?20:e.key==='ArrowUp'?-20:0))));}}/>
      </article>;})}{!layout.cards.some(c=>c.section===section.id)&&<button className="workspace-empty-section" onClick={()=>setEditor({card:createCard(section.id),creating:true})}>+ Add a card to this section</button>}</div>}
    </section>)}
    <Button onClick={()=>setNaming({kind:'section',value:'New section'})}><Plus size={16}/> Add section</Button></div></div>
    {editor&&<CardEditor initial={editor.card} creating={editor.creating} fields={fields} rows={visibleRows} onClose={()=>setEditor(undefined)} onApply={card=>{const cards=editor.creating?[...layout.cards,card]:layout.cards.map(c=>c.id===card.id?card:c);const next=uniformSections({...layout,cards:cards.map(c=>c.section===card.section?{...c,width:card.width,height:card.height}:c)});change(next,!editor.changed);persist(next);if(editor.creating)setEditor(undefined);else setEditor({...editor,changed:true});}}/>}
    {expanded&&<WorkspaceDialog title={expanded.title} onClose={()=>setExpanded(undefined)} wide><div className="workspace-expanded-card"><CardContent card={expanded} rows={visibleRows}/></div></WorkspaceDialog>}
    {naming&&<WorkspaceDialog title={naming.kind==='save'?'Save layout as':naming.kind==='rename'?'Rename section':'Add section'} onClose={()=>setNaming(undefined)}><form onSubmit={e=>{e.preventDefault();if(!naming.value.trim())return;if(naming.kind==='save')persist({...layout,id:uid(),name:naming.value.trim()});else if(naming.kind==='section')patch({sections:[...layout.sections,{id:uid(),title:naming.value.trim(),collapsed:false}]});else patch({sections:layout.sections.map(s=>s.id===naming.id?{...s,title:naming.value.trim()}:s)});setNaming(undefined);}}><label className="workspace-field"><span>Name</span><input autoFocus value={naming.value} onChange={e=>setNaming({...naming,value:e.target.value})}/></label><footer className="workspace-dialog-footer"><Button onClick={()=>setNaming(undefined)}>Cancel</Button><Button primary type="submit" disabled={!naming.value.trim()}>{naming.kind==='save'?'Save layout':'Apply'}</Button></footer></form></WorkspaceDialog>}
  </Surface>;
}
