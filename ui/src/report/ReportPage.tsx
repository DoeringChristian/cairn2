import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { CheckCheck, RefreshCw } from 'lucide-react';
import { snippetCompletion, type CompletionSource } from '@codemirror/autocomplete';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, artifactBlobs, post, projectPath, reportPath, useResource, when, type Artifact, type ArtifactSummary } from '../api';
import { useRefresh } from '../refresh';
import { ActionIcon, Button, Crumbs, ErrorNote, Field, Hero, Hint, Loading, ReportCard, ReportGrid, Section } from '../components';
import { nodesToReact, type Render } from '../components/Markdown';
import { ReportArtifactContext } from '../components/ReportContext';
import { ReportComments, ReportCommentsProvider, SlotDiscussion } from '../components/ReportComments';
import { ReportToc } from '../components/ReportToc';
import { Select } from '../components/Select';
import { SelectionCopy } from '../components/SelectionCopy';
import { Editor } from './Editor';
import { agentLine, blocks } from './markdown';
import { Answer, ArtifactMedia, ViewFigure } from './media';
import type { Report, Summary, Template } from './types';

export function ReportsPage() {
  const {project = ''} = useParams(), navigate = useNavigate(), pulse = useRefresh();
  const {data, error, loading} = useResource<Summary[]>(`/v1/reports?project=${encodeURIComponent(project)}`, pulse);
  const templates = useResource<Template[]>('/ui-api/report-templates', 0);
  const [name, setName] = useState(''), [start, setStart] = useState(''), [failure, setFailure] = useState<Error>();
  useEffect(() => {document.title = `${project} · Cairn`;}, [project]);
  async function create(event: FormEvent) {
    event.preventDefault(); setFailure(undefined);
    try {
      // A template is a document to start from; so is any existing report.
      const [kind, key] = start.split(':', 2);
      const body = kind === 'template' ? JSON.stringify(templates.data?.find(template => template.name === key)?.document)
        : kind === 'report' ? (await api<Report>(`/v1/reports/${key}`)).parts.body?.source : undefined;
      const report = await post<Summary>('/ui-api/reports', {project, name: name.trim(), body});
      navigate(reportPath(project, report.name));
    }
    catch (e) {setFailure(e as Error);}
  }
  const choices = [{value: '', label: 'Blank'}, ...(templates.data || []).map(template => ({value: `template:${template.name}`, label: template.title})),
    ...(data || []).map(report => ({value: `report:${report.id}`, label: `Copy of ${report.name}`}))];
  return <main><Hero eyebrow={<Crumbs items={[{label:'Projects',to:'/'},{label:project}]}/>} title={project} subtitle="Your plans, with the agent's results beside them."/>
    <Section title="Your reports"><form className="report-create" onSubmit={create}>
      <Field label="New report" placeholder="Name" value={name} required onChange={e => setName(e.target.value)}/>
      <div className="field"><span>Start from</span><Select label="Start from" value={start} onChange={setStart} choices={choices}/></div>
      <Button primary type="submit" disabled={!name.trim()}>Create</Button>
    </form>{failure && <ErrorNote error={failure}/>}
      {loading && !data && <Loading/>}{error && <ErrorNote error={error}/>}{data?.length === 0 && <Hint>No reports yet. Write down what you want to find out, and type @agent where the agent should answer.</Hint>}
      {!!data?.length && <ReportGrid>{data.map(report => <ReportCard key={report.id} to={reportPath(project, report.name)} project={project} title={report.name} updated={when(report.updated_at)}/>)}</ReportGrid>}
    </Section></main>;
}

type Slots={open:Set<string>;toggle:(slot:string,open:boolean)=>void;remove:(slot:string)=>void;focus?:string};
/** The body's blocks: cairn: figures, view blocks and agent blocks render through the page. */
function renderer(report:Report,slots:Slots):Render {
  return {
    fence:(info,content,key,source)=>info==='view'?<ViewFigure key={key} text={content} project={report.project} part="body" id={`body-${source?.line_start||1}`}/>:undefined,
    image:(src,alt,_attrs,key)=>src.startsWith('cairn:')?<ArtifactMedia key={key} reference={src.slice(6)} alt={alt} project={report.project} part="body"/>:<span key={key}>[Only cairn: references can be shown: {alt}]</span>,
    agent:({slot,prompt},key)=>{
      const answer=report.parts[slot];
      return <section key={key} className="agent-slot" aria-label={`Agent: ${slot}`}>
        <SlotDiscussion artifact={report.id} slot={slot} prompt={prompt} focus={slots.focus===slot} open={slots.open.has(slot)} onToggle={open=>slots.toggle(slot,open)} onRemove={()=>slots.remove(slot)} meta={answer&&<small>{answer.author?`${answer.author} · `:''}{when(answer.updated_at)}</small>}>
          {answer?.source.trim()?<div className="agent-answer"><Answer source={answer.source} part={slot} project={report.project}/></div>:undefined}
        </SlotDiscussion>
      </section>;
    },
  };
}

/** Completes cairn: references, their captures, @agent, callouts and views. */
function completions(report:Report):CompletionSource {
  const {project}=report;
  return async context=>{
    const capture=context.matchBefore(/cairn:[^\s()#]+#[^\s()]*/);
    if(capture){
      const [ref,file]=capture.text.slice(6).split('#',2),[artifact]=await post<Artifact[]>('/v1/resolutions',{refs:[ref],project}).catch(()=>[]);
      return artifact?{from:capture.to-file.length,options:[...new Set([...Object.keys(artifact.manifest?.captures||{}),...artifactBlobs(artifact).flatMap(blob=>blob.name?[blob.name]:[])])].map(label=>({label,type:'variable'}))}:null;
    }
    const ref=context.matchBefore(/cairn:[^\s()#]*/);
    if(ref){
      const found=await api<{items:ArtifactSummary[]}>(`/ui-api/artifacts?${new URLSearchParams({project,category:'all',search:ref.text.slice(6).split('@')[0],limit:'30'})}`).catch(()=>({items:[]}));
      return {from:ref.from+6,options:[...new Set(found.items.map(item=>`${item.namespace}/${item.name}`))].map(label=>({label,apply:`${label}@latest`,type:'class'}))};
    }
    const mention=context.matchBefore(/^@\w*/);
    if(mention)return {from:mention.from,options:[{label:'@agent',apply:'@agent ',detail:'Ask the agent here; Enter places the block',type:'keyword'}]};
    const fence=context.matchBefore(/^:::.*/);
    if(fence)return {from:fence.from,options:['note','tip','warning','important'].map(kind=>snippetCompletion(`::: {.callout-${kind}}\n\${}\n:::`,{label:`::: ${kind}`,detail:'Callout'}))};
    const code=context.matchBefore(/^```\w*/);
    if(code)return {from:code.from,options:[snippetCompletion('```view\nviewer: cairn:${viewers/mesh@latest#index.html}\n${}\n```',{label:'```view',detail:'A viewer with settings'})]};
    const image=context.matchBefore(/!\[[^\]]*/);
    // The bracket typed with ![ closed itself; the figure replaces it too.
    const closing=context.state.sliceDoc(context.pos,context.pos+1)===']'?1:0;
    return image?{from:image.from,to:context.pos+closing,options:[snippetCompletion('![${caption}](cairn:${})',{label:'![…](cairn:…)',detail:'Artifact figure or value'})]}:null;
  };
}

/** The body as Markdown, saved after a pause in typing; a save against a newer revision is refused, never merged. */
function useBody(report:Report|undefined) {
  const revisionNow=report?.parts.body?.revision;
  const loaded=useResource<{markdown:string;revision:number}>(report?`/ui-api/reports/${report.id}/markdown`:null,revisionNow||0);
  const [draft,setDraft]=useState<string>(),[status,setStatus]=useState(''),[error,setError]=useState<Error>();
  const revision=useRef(0),saved=useRef<string>(),body=loaded.data;
  // The server's Markdown replaces the text only on load or when someone else saved; our own save echoes a normalized form of what is on screen.
  useEffect(()=>{if(!body||body.revision<=revision.current&&saved.current!==undefined||(draft!==undefined&&draft!==saved.current))return;revision.current=body.revision;saved.current=body.markdown;setDraft(undefined);},[body]);
  useEffect(()=>{
    if(!report||draft===undefined||draft===saved.current)return;
    const timer=setTimeout(async()=>{
      setStatus('Saving…');
      try{const result=await api<{revision:number}>(`/ui-api/reports/${report.id}/body`,{method:'PUT',body:JSON.stringify({source:draft,expected_revision:revision.current})});revision.current=result.revision;saved.current=draft;setStatus('Saved');setError(undefined);}
      catch(e){setError(e as Error);setStatus('Not saved');}
    },800);
    return()=>clearTimeout(timer);
  },[draft]);
  return {ready:!!body,source:draft??body?.markdown??'',setDraft,status,error};
}

export function ReportPage() {
  const {project = '', '*': name = ''} = useParams(), pulse = useRefresh();
  const [revision,setRevision]=useState(0),[rendered,setRendered]=useState(0),[open,setOpen]=useState(new Set<string>()),[params,setParams]=useSearchParams();
  const listing = useResource<Summary[]>(`/v1/reports?project=${encodeURIComponent(project)}`, revision);
  const id = listing.data?.find(report => report.name === name)?.id;
  const {data: report, error, loading} = useResource<Report>(id ? `/v1/reports/${id}` : null, pulse + revision);
  const body = useBody(report), container = useRef<HTMLElement>(null);
  useEffect(() => {document.title = `${name} · Cairn`;}, [name]);
  const parsed = useMemo(() => blocks(body.source, 'body'), [body.source]);
  const placed = new Set(parsed.blocks.flatMap(block => block.nodes).filter(node => node.type === 'agent').map(node => node.attributes?.slot));
  const other = Object.entries(report?.parts || {}).filter(([part,slot]) => part !== 'body' && !placed.has(part) && slot.source.trim());
  const [focus,setFocus]=useState<string>();
  /** A request typed after @agent is the block's first message, sent at once; without one, its message box takes the cursor. */
  async function placeAgent(slot:string,request:string) {
    if(!report)return;
    if(!request){setFocus(slot);return;}
    const base=`/ui-api/reports/${report.id}/comments`;
    const comment=await post<{id:string}>(base,{anchor:{path:slot},body:request,for_agent:true,request_id:crypto.randomUUID()});
    await post(`${base}/${comment.id}/sending`,{});
    window.dispatchEvent(new CustomEvent('cairn:comments',{detail:comment.id}));
  }
  const slots:Slots = {focus, open, toggle:(slot,show)=>setOpen(current=>{const next=new Set(current);if(show)next.add(slot);else next.delete(slot);return next;}),
    remove:slot=>body.setDraft(body.source.split('\n').filter(line=>agentLine.exec(line)?.[1]!==slot).join('\n'))};
  const resolved = params.get('resolved') === '1';
  const missing = listing.data && !id;
  return <main className="report-page">
    <Hero eyebrow={<Crumbs items={[{label:'Projects',to:'/'},{label:project,to:projectPath(project)},{label:'Reports'}]}/>} title={name}/>
    {(listing.error || error) && <ErrorNote error={(listing.error || error)!}/>}{missing && <Hint>No report named “{name}” in {project}.</Hint>}
    {(loading || listing.loading || (report && !body.ready)) && !missing && <Loading what="Loading report"/>}
    {report && body.ready && <Section title="Report" actions={<div className="report-actions">
      <span className="comment-save-status" role="status">{body.status}</span>
      <ActionIcon label={resolved?'Hide resolved comments':'Show resolved comments'} aria-pressed={resolved} onClick={()=>{const next=new URLSearchParams(params);if(resolved)next.delete('resolved');else next.set('resolved','1');setParams(next,{replace:true});}}><CheckCheck size={17} aria-hidden="true"/></ActionIcon>
      <ActionIcon label="Refresh" onClick={() => setRevision(n => n + 1)}><RefreshCw size={17} aria-hidden="true"/></ActionIcon>
    </div>}>
      {body.error && <ErrorNote error={body.error}/>}
      <ReportArtifactContext.Provider value={report.id}><ReportCommentsProvider key={report.id} artifact={report.id}><div className="report-reader-layout">
        <ReportToc container={container} documentKey={rendered}/>
        <article ref={container} className="native-document report-prose" aria-label={name}>
          <Editor value={body.source} onChange={body.setDraft} onRender={() => setRendered(n => n + 1)} onAgent={(slot,request)=>void placeAgent(slot,request)} label="Report text" complete={completions(report)} render={block => nodesToReact(block.nodes, renderer(report, slots))}/>
          {nodesToReact(parsed.rest, renderer(report, slots))}
          {!!other.length&&<section className="agent-notes" aria-label="Other agent notes"><h2>Other agent notes</h2>{other.map(([part,slot])=><section key={part} className="agent-slot"><div className="agent-answer"><header><span>Agent · {part}</span><small>{when(slot.updated_at)}</small></header><Answer source={slot.source} part={part} project={project}/></div></section>)}</section>}
          <SelectionCopy container={container} version={`reports/${name}`}/>
        </article>
        <ReportComments container={container} artifact={report.id} project={project}/>
      </div></ReportCommentsProvider></ReportArtifactContext.Provider>
    </Section>}
  </main>;
}
