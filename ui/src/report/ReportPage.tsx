import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { CheckCheck, RefreshCw } from 'lucide-react';
import { snippetCompletion, type CompletionSource } from '@codemirror/autocomplete';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, artifactBlobs, envelope, post, projectPath, reportPath, useResource, when, type Artifact, type ArtifactSummary } from '../api';
import { useRefresh } from '../refresh';
import { ActionIcon, Button, Crumbs, ErrorNote, Field, Hero, Hint, Loading, ReportCard, ReportGrid, Section, ValueView } from '../components';
import { nodesToReact, type Node, type Render } from '../components/Markdown';
import { ReportArtifactContext } from '../components/ReportContext';
import { ReportComments, ReportCommentsProvider, SlotDiscussion } from '../components/ReportComments';
import { ReportToc } from '../components/ReportToc';
import { SelectionCopy } from '../components/SelectionCopy';
import { ZoomImage } from '../components/ZoomImage';
import { MediaPlayback } from '../components/MediaPlayback';
import { useImageBlob } from '../components/useBlobUrl';
import { OutputComment } from '../components/OutputComment';
import { readView, ViewerFrame } from '../components/ViewerFrame';
import { blocks, parse } from './markdown';
import { Editor } from './Editor';

type Summary = {id:string;project:string;name:string;created_at:string;updated_at:string};
type Part = {source:string;revision:number;author?:string|null;updated_at:string};
type Report = Summary & {parts:Record<string,Part>};
const media:Record<string,string>={png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',gif:'image/gif',webp:'image/webp',svg:'image/svg+xml',mp4:'video/mp4',webm:'video/webm',wav:'audio/wav',mp3:'audio/mpeg'};

export function ReportsPage() {
  const {project = ''} = useParams(), navigate = useNavigate(), pulse = useRefresh();
  const {data, error, loading} = useResource<Summary[]>(`/v1/reports?project=${encodeURIComponent(project)}`, pulse);
  const [name, setName] = useState(''), [failure, setFailure] = useState<Error>();
  useEffect(() => {document.title = `${project} · Cairn`;}, [project]);
  async function create(event: FormEvent) {
    event.preventDefault(); setFailure(undefined);
    try {const report = await post<Summary>('/ui-api/reports', {project, name: name.trim(), body: `# ${name.trim()}\n`});navigate(reportPath(project, report.name));}
    catch (e) {setFailure(e as Error);}
  }
  return <main><Hero eyebrow={<Crumbs items={[{label:'Projects',to:'/'},{label:project}]}/>} title={project} subtitle="Your plans, with the agent's results beside them."/>
    <Section title="Your reports"><form className="report-create" onSubmit={create}><Field label="New report" placeholder="Name" value={name} required onChange={e => setName(e.target.value)}/><Button primary type="submit" disabled={!name.trim()}>Create</Button></form>{failure && <ErrorNote error={failure}/>}
      {loading && !data && <Loading/>}{error && <ErrorNote error={error}/>}{data?.length === 0 && <Hint>No reports yet. Write down what you want to find out, and leave the agent room to answer.</Hint>}
      {!!data?.length && <ReportGrid>{data.map(report => <ReportCard key={report.id} to={reportPath(project, report.name)} project={project} title={report.name} updated={when(report.updated_at)}/>)}</ReportGrid>}
    </Section></main>;
}

/** An artifact's value, or with #file one of its files, from a cairn:REF reference; an HTML file runs as a viewer. */
function ArtifactMedia({reference,alt,project,part}: {reference:string;alt:string;project:string;part:string}) {
  const at=reference.indexOf('#'),ref=at<0?reference:reference.slice(0,at),file=at<0?undefined:reference.slice(at+1),[artifact,setArtifact]=useState<Artifact>(),[error,setError]=useState<Error>();
  useEffect(()=>{const c=new AbortController();setError(undefined);post<Artifact[]>('/v1/resolutions',{refs:[ref],project},c.signal).then(found=>setArtifact(found[0])).catch(e=>{if(!c.signal.aborted)setError(e);});return()=>c.abort();},[ref,project]);
  if(file?.endsWith('.html'))return <OutputComment source={{file:part,line:1,media_id:reference}} label={alt||'Viewer'}><figure><ViewerFrame viewer={reference} settings={{}} project={project} title={alt||'Viewer'}/>{alt&&<figcaption>{alt}</figcaption>}</figure></OutputComment>;
  if(error)return <ErrorNote error={error}/>;if(!artifact)return <Loading what={`Loading ${ref}`}/>;
  const typed=envelope(artifact.manifest?.value),blob=file?envelope(artifact.manifest?.captures[file])?.blobs?.[0]||artifactBlobs(artifact).find(b=>b.name===file):['image','figure/svg'].includes(typed?.format||'')?typed?.blobs?.[0]:undefined;
  if(file&&!blob)return <Hint>{ref} has no file {file}.</Hint>;
  if(!blob)return <ValueView value={artifact.manifest?.value}/>;
  const extension=(file||blob.name||'').split('.').pop()?.toLowerCase()||'',source={file:part,line:1,media_id:reference};
  return <BlobMedia blob={{...blob,media_type:blob.media_type||media[extension]}} alt={alt} source={source}/>;
}
/** A view block: a viewer and its settings, commentable like other figures. */
function ViewFigure({text,project,part,line}: {text:string;project:string;part:string;line:number}) {
  let view;try{view=readView(text);}catch(e){return <ErrorNote error={e as Error}/>;}
  const label=view.caption||view.viewer;
  return <OutputComment source={{file:part,line,media_id:`view:${line}`}} label={label}><figure><ViewerFrame viewer={view.viewer} settings={view.settings} project={project} title={label}/>{view.caption&&<figcaption>{view.caption}</figcaption>}</figure></OutputComment>;
}
function BlobMedia({blob,alt,source}: {blob:Parameters<typeof useImageBlob>[0];alt:string;source:{file:string;line:number;media_id:string}}) {
  const {url,mime,error}=useImageBlob(blob);
  if(error)return <ErrorNote error={error}/>;if(!url)return <Loading what={`Loading ${alt||blob.name}`}/>;
  return /^(video|audio)\//.test(mime)?<MediaPlayback src={url} mime={mime} label={alt||blob.name||'Video'} source={source}/>:<figure><ZoomImage src={url} alt={alt||blob.name||'Figure'} source={source}/>{alt&&<figcaption>{alt}</figcaption>}</figure>;
}

type Folds={open:Set<string>;toggle:(slot:string,open:boolean)=>void};
function renderer(report:Report,part:string,folds:Folds):Render {
  return {
    fence:(info,content,key,source)=>info==='view'?<ViewFigure key={key} text={content} project={report.project} part={part} line={source?.line_start||1}/>:undefined,
    image:(src,alt,_attrs,key)=>src.startsWith('cairn:')?<ArtifactMedia key={key} reference={src.slice(6)} alt={alt} project={report.project} part={part}/>:<span key={key}>[Only cairn: references can be shown: {alt}]</span>,
    container:(attrs,content,key)=>{
      if(!(attrs.class||'').split(' ').includes('agent'))return undefined;
      const id=attrs.id,slot=id?report.parts[id]:undefined;
      if(!id)return <section key={key} className="agent-slot" aria-label="Agent: unnamed"><div className="agent-request">{content}</div><Hint>Name this block, e.g. <code>::: {'{.agent #baseline}'}</code>, so the agent can answer here.</Hint></section>;
      return <section key={key} className="agent-slot" aria-label={`Agent: ${id}`}>
        <div className="agent-answer">{slot?.source.trim()?nodesToReact(parse(slot.source,id),renderer(report,id,folds)):<Hint>Waiting for the agent.</Hint>}</div>
        <SlotDiscussion artifact={report.id} slot={id} open={folds.open.has(id)} onToggle={open=>folds.toggle(id,open)} meta={slot&&<small>{slot.author?`${slot.author} · `:''}{when(slot.updated_at)}</small>}>{content}</SlotDiscussion>
      </section>;
    },
  };
}
const agent=(node:Node)=>node.type==='container_div_open'&&(node.attributes?.class||'').split(' ').includes('agent');

/** Completes cairn: references, their captures, agent slots, blocks and views. */
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
    const slot=context.matchBefore(/\{\.agent #[\w-]*/);
    if(slot)return {from:slot.from+9,options:Object.keys(report.parts).filter(part=>part!=='body').map(label=>({label,type:'variable'}))};
    const fence=context.matchBefore(/^:::.*/);
    if(fence)return {from:fence.from,options:[
      snippetCompletion('::: {.agent #${name}}\n${What should the agent answer here?}\n:::',{label:'::: agent',detail:'Space for the agent'}),
      ...['note','tip','warning','important'].map(kind=>snippetCompletion(`::: {.callout-${kind}}\n\${}\n:::`,{label:`::: ${kind}`,detail:'Callout'})),
    ]};
    const code=context.matchBefore(/^```\w*/);
    if(code)return {from:code.from,options:[snippetCompletion('```view\nviewer: cairn:${viewers/mesh@latest#index.html}\n${}\n```',{label:'```view',detail:'A viewer with settings'})]};
    const image=context.matchBefore(/!\[[^\]]*/);
    return image?{from:image.from,options:[snippetCompletion('![${caption}](cairn:${})',{label:'![…](cairn:…)',detail:'Artifact figure or value'})]}:null;
  };
}

/** Saves the body after a pause in typing; a save against a newer revision is refused, never merged. */
function useBody(report:Report|undefined) {
  const [draft,setDraft]=useState<string>(),[status,setStatus]=useState(''),[error,setError]=useState<Error>();
  const revision=useRef(0),saved=useRef<string>(),body=report?.parts.body;
  useEffect(()=>{if(!body||body.revision<revision.current||(draft!==undefined&&draft!==saved.current))return;revision.current=body.revision;saved.current=body.source;setDraft(undefined);},[body]);
  useEffect(()=>{
    if(!report||draft===undefined||draft===saved.current)return;
    const timer=setTimeout(async()=>{
      setStatus('Saving…');
      try{const result=await api<{revision:number}>(`/ui-api/reports/${report.id}/body`,{method:'PUT',body:JSON.stringify({source:draft,expected_revision:revision.current})});revision.current=result.revision;saved.current=draft;setStatus('Saved');setError(undefined);}
      catch(e){setError(e as Error);setStatus('Not saved');}
    },800);
    return()=>clearTimeout(timer);
  },[draft]);
  return {source:draft??body?.source??'',setDraft,status,error};
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
  const placed = new Set(parsed.blocks.flatMap(block => block.nodes).filter(agent).map(node => node.attributes?.id));
  const other = Object.entries(report?.parts || {}).filter(([part,slot]) => part !== 'body' && !placed.has(part) && slot.source.trim());
  const folds:Folds = {open, toggle:(slot,show)=>setOpen(current=>{const next=new Set(current);if(show)next.add(slot);else next.delete(slot);return next;})};
  const resolved = params.get('resolved') === '1';
  const missing = listing.data && !id;
  return <main className="report-page">
    <Hero eyebrow={<Crumbs items={[{label:'Projects',to:'/'},{label:project,to:projectPath(project)},{label:'Reports'}]}/>} title={name}/>
    {(listing.error || error) && <ErrorNote error={(listing.error || error)!}/>}{missing && <Hint>No report named “{name}” in {project}.</Hint>}
    {(loading || listing.loading) && !report && !missing && <Loading what="Loading report"/>}
    {report && <Section title="Report" actions={<div className="report-actions">
      <span className="comment-save-status" role="status">{body.status}</span>
      <ActionIcon label={resolved?'Hide resolved comments':'Show resolved comments'} aria-pressed={resolved} onClick={()=>{const next=new URLSearchParams(params);if(resolved)next.delete('resolved');else next.set('resolved','1');setParams(next,{replace:true});}}><CheckCheck size={17} aria-hidden="true"/></ActionIcon>
      <ActionIcon label="Refresh" onClick={() => setRevision(n => n + 1)}><RefreshCw size={17} aria-hidden="true"/></ActionIcon>
    </div>}>
      {body.error && <ErrorNote error={body.error}/>}
      <ReportArtifactContext.Provider value={report.id}><ReportCommentsProvider key={report.id} artifact={report.id}><div className="report-reader-layout">
        <ReportToc container={container} documentKey={rendered}/>
        <article ref={container} className="native-document report-prose" aria-label={name}>
          <Editor value={body.source} onChange={body.setDraft} onRender={() => setRendered(n => n + 1)} label="Report text" complete={completions(report)} render={block => nodesToReact(block.nodes, renderer(report, 'body', folds))}/>
          {nodesToReact(parsed.rest, renderer(report, 'body', folds))}
          {!!other.length&&<section className="agent-notes" aria-label="Other agent notes"><h2>Other agent notes</h2>{other.map(([part,slot])=><section key={part} className="agent-slot"><div className="agent-answer"><header><span>Agent · {part}</span><small>{when(slot.updated_at)}</small></header>{nodesToReact(parse(slot.source,part),renderer(report,part,folds))}</div></section>)}</section>}
          <SelectionCopy container={container} version={`reports/${name}`}/>
        </article>
        <ReportComments container={container} artifact={report.id} project={project}/>
      </div></ReportCommentsProvider></ReportArtifactContext.Provider>
    </Section>}
  </main>;
}
