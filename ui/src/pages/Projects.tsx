import { cachedList, cacheList, invalidateList } from '../listCache';
import { ArtifactTags } from '../components/ArtifactTags';
import { useRefresh, useRefreshMode } from '../refresh';
import { useEffect, useState, type FormEvent } from 'react';
import { ReportsPage } from '../report/ReportsPage';
import { RefreshCw } from 'lucide-react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { artifactPath, artifactsOf, condition, operate, post, projectPath, status, useProjects, when, type Artifact } from '../api';
import { ActionIcon, Button, Crumbs, DataTable, ErrorNote, Field, Hero, Hint, Loading, MetricCard, MetricGrid, Pill, ProjectCard, SearchInput, Section, Select, Toolbar } from '../components';
export function ProjectsPage() {
  const [revision, refresh] = useState(0), [search, setSearch] = useState('');
  const pulse = useRefresh(), navigate = useNavigate();
  const [name, setName] = useState(''), [failure, setFailure] = useState<Error>();
  async function create(event: FormEvent) {
    event.preventDefault();
    setFailure(undefined);
    try {
      await post('/ui-api/projects', {name: name.trim()});
      navigate(projectPath(name.trim()));
    } catch (e) {setFailure(e as Error);}
  }
  const {data, error, loading} = useProjects(true, revision + pulse);
  useEffect(() => {document.title = 'Projects · Cairn';}, []);
  const filtered = data?.filter(p => p.name.toLowerCase().includes(search.toLowerCase()));
  return <main><Hero eyebrow="Repository" title="Projects" subtitle="A home for your experiments and the evidence behind them." status="Cairn explorer"/>
    {data && <MetricGrid><MetricCard label="Projects" value={data.length}/><MetricCard label="Artifacts" value={data.reduce((n,p) => n+artifactsOf(p), 0)} note="Across all namespaces"/><MetricCard label="Reports" value={data.reduce((n,p) => n+p.reports, 0)}/></MetricGrid>}
    <Section title="Your projects" actions={<ActionIcon label="Refresh" onClick={() => refresh(n=>n+1)}><RefreshCw size={17} aria-hidden="true"/></ActionIcon>}><form className="report-create" onSubmit={create}><Field label="New project" placeholder="Name" value={name} required onChange={e=>setName(e.target.value)}/><Button primary type="submit" disabled={!name.trim()}>Create</Button></form>{failure && <ErrorNote error={failure}/>}<Toolbar><SearchInput label="Search projects" value={search} onChange={setSearch}/></Toolbar>
    {loading && <Loading/>}{error && <ErrorNote error={error}/>}{data?.length === 0 && <Hint>No projects yet. Create one, or log a run.</Hint>}
    {!!filtered?.length && <div className="project-grid">{filtered.map(p=><ProjectCard key={p.name} project={p}/>)}</div>}
    {!!data?.length && !filtered?.length && <Hint>No projects match “{search}”.</Hint>}</Section></main>;
}
export function ProjectPage() {
  const [params] = useSearchParams();
  return (params.get('view') ?? 'reports') === 'reports' ? <ReportsPage/> : <ArtifactList/>;
}
function ArtifactList() {
  const {project = ''} = useParams();
  const paused = useRefreshMode();
  const [params, setParams] = useSearchParams();
  const view = params.get('view');
  const tab = view === 'artifacts' || view === 'all' ? view : 'runs';
  const [search, setSearch] = useState(''), [revision, refresh] = useState(0);
  const [querySearch, setQuerySearch] = useState('');
  useEffect(() => {const timer = window.setTimeout(() => setQuerySearch(search), 200); return () => clearTimeout(timer);}, [search]);
  const conditions=params.getAll('where');
  const pageNumber = Number(params.get('page'));
  const page = Number.isSafeInteger(pageNumber) && pageNumber > 0 ? pageNumber : 0;
  const [draft, setDraft] = useState('');
  const namespace = params.get('namespace') || '';
  const projects = useProjects(tab === 'artifacts', 0);
  const namespaces = Object.keys(projects.data?.find(p => p.name === project)?.artifacts || {}).filter(name => name !== 'runs').sort();
  const wanted = tab === 'runs' ? ['runs'] : tab === 'all' ? [] : namespace ? [namespace] : namespaces;
  const ready = tab !== 'artifacts' || !!projects.data || !!projects.error;
  const path = JSON.stringify([project, tab, wanted, querySearch, conditions, page]);
  const [result, setResult] = useState<{path: string; items: Artifact[]; total: number}>();
  const [error, setError] = useState<Error>(), [loading, setLoading] = useState(true);
  const visible = result?.path === path ? result : cachedList(path);
  const items = visible?.items ?? [];
  const total = visible?.total ?? 0;
  function refreshList() {invalidateList(path);refresh(n => n + 1);}
  useEffect(() => {
    document.title = `${project} · Cairn`;
    if (!ready) return;
    const controller = new AbortController();
    let timer: number;
    async function load() {
      setError(undefined);
      try {
        const request = {project, namespaces: wanted, text: querySearch, where: conditions.map(condition), values: ['_outcome'], offset: page * 50, limit: 50};
        // A project with runs alone has no artifacts; no namespace would mean every one.
        const data = tab === 'artifacts' && !wanted.length ? {items: [], total: 0} : await operate<{items: Artifact[]; total: number}>('find', request, controller.signal);
        if (!controller.signal.aborted) {cacheList(path, data);setResult({...data, path});}
      } catch (e) {if (!controller.signal.aborted) setError(e as Error);}
      finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          // Schedule after completion: slow responses are never cancelled by polling.
          if (!paused) timer = window.setTimeout(poll, 5000);
        }
      }
    }
    function poll() {
      if (document.visibilityState === 'visible') void load();
      else timer = window.setTimeout(poll, 5000);
    }
    const cached = cachedList(path);
    if (cached) {
      setResult({...cached, path});setLoading(false);
    } else setLoading(true);
    // A cached page only bridges the request, unless auto-refresh is paused.
    if (!cached || !paused) void load();
    return () => {controller.abort();window.clearTimeout(timer);};
  }, [path, ready, revision, paused]);
  const available=[...new Set(items.flatMap(a=>Object.keys(a.tags)))].sort().map(tag=>`tag:${tag}`);
  function toggle(text:string){const next=new URLSearchParams(params);next.delete('page');next.delete('where');for(const value of conditions.includes(text)?conditions.filter(t=>t!==text):[...conditions,text])next.append('where',value);setParams(next);}
  function choose(value:string){const next=new URLSearchParams(params);next.delete('page');if(value)next.set('namespace',value);else next.delete('namespace');setParams(next);}
  function add(){try{condition(draft);}catch(e){setError(e as Error);return;}if(!conditions.includes(draft.trim()))toggle(draft.trim());setDraft('');}
  function changePage(value: number) {const next=new URLSearchParams(params);next.set('page', String(value));setParams(next);}
  const filtered = items;
  return <main><Hero eyebrow={<Crumbs items={[{label:'Projects',to:'/'},{label:project}]}/>} title={project} subtitle="Reports, experiments, and the evidence that connects them."/>
    <Section title={tab==='runs'?'Experiment runs':tab==='artifacts'?'Artifact library':'Everything'} actions={<ActionIcon label="Refresh" onClick={refreshList}><RefreshCw size={17} aria-hidden="true"/></ActionIcon>}>
    <Toolbar><SearchInput label="Search artifacts" value={search} onChange={value=>{setSearch(value);if(page)changePage(0);}}/><form onSubmit={e=>{e.preventDefault();add();}}><SearchInput label="Filter, e.g. loss &lt; 0.1 or tag:paper" value={draft} onChange={setDraft}/></form>{tab==='artifacts'&&namespaces.length>1&&<Select label="Namespace" value={namespace} choices={[{value:'',label:'All namespaces'},...namespaces.map(value=>({value,label:value}))]} onChange={choose}/>}</Toolbar>
    {(!!available.length||!!conditions.length)&&<div className="tag-filters" aria-label="Filters"><span className="muted">Filters</span>{[...new Set([...available,...conditions])].map(text=><button type="button" className="tag-chip" key={text} aria-pressed={conditions.includes(text)} onClick={()=>toggle(text)}>{text.replace(/^tag:/,'')}</button>)}{!!conditions.length&&<button type="button" className="tag-clear" onClick={()=>{const next=new URLSearchParams(params);next.delete('where');next.delete('page');setParams(next);}}>Clear filters</button>}</div>}
    {loading && !items.length && <Loading/>}{error && <ErrorNote error={error}/>}{!loading && !error && !filtered.length && <Hint>No matching artifacts in this view.</Hint>}
    {!!filtered.length && <div className="artifact-list"><DataTable head={['Name','Namespace','State / version','Tags','Created']}>{filtered.map(a=><tr key={a.id}><td data-label="Name"><Link to={artifactPath(project,a.id)}><strong>{a.name}</strong></Link></td><td data-label="Namespace"><Pill tone="alias">{a.namespace}</Pill></td><td data-label="State / version"><Pill tone={a.finished ? 'default':'pending'}>{a.version ? `v${a.version} · ${status(a)}` : status(a)}</Pill>{a.aliases.map(alias=><Pill key={alias} tone="alias">@{alias}</Pill>)}</td><td data-label="Tags"><ArtifactTags artifact={a} compact onTagClick={tag=>toggle(`tag:${tag}`)} onChange={refreshList}/></td><td data-label="Created" className="muted nowrap">{when(a.created_at)}</td></tr>)}</DataTable></div>}
    <div className="pagination"><Hint>{total ? `${page*50+1}–${Math.min((page+1)*50,total)} of ${total}` : '0 results'}</Hint><Button disabled={page===0||loading} onClick={()=>changePage(page-1)}>Previous</Button><Button disabled={(page+1)*50>=total||loading} onClick={()=>changePage(page+1)}>Next</Button></div>
    </Section></main>;
}
