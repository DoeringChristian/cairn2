import { cachedList, cacheList, invalidateList } from '../listCache';
import { ArtifactTags, artifactTags } from '../components/ArtifactTags';
import { useRefresh, useRefreshMode } from '../refresh';
import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api, useResource, artifactPath, when, type Project, type ArtifactSummary } from '../api';
import { Button, Crumbs, DataTable, ErrorNote, Hero, Hint, Loading, MetricCard, MetricGrid, Pill, ProjectCard, ReportCard, ReportGrid, SearchInput, Section, Toolbar } from '../components';
export function ProjectsPage() {
  const [revision, refresh] = useState(0), [search, setSearch] = useState('');
  const pulse = useRefresh();
  const {data, error, loading} = useResource<Project[]>('/ui-api/projects', revision + pulse);
  useEffect(() => {document.title = 'Projects · Cairn';}, []);
  const filtered = data?.filter(p => p.name.toLowerCase().includes(search.toLowerCase()));
  return <main><Hero eyebrow="Repository" title="Projects" subtitle="A home for your experiments and the evidence behind them." status="Cairn explorer"/>
    {data && <MetricGrid><MetricCard label="Projects" value={data.length}/><MetricCard label="Artifacts" value={data.reduce((n,p) => n+p.artifacts, 0)} note="Across all namespaces"/><MetricCard label="Reports" value={data.reduce((n,p) => n+p.reports, 0)}/></MetricGrid>}
    <Section title="Your projects" actions={<Button small onClick={() => refresh(n=>n+1)}>Refresh</Button>}><Toolbar><SearchInput label="Search projects" value={search} onChange={setSearch}/></Toolbar>
    {loading && <Loading/>}{error && <ErrorNote error={error}/>}{data?.length === 0 && <Hint>No projects yet. Record an experiment or publish an artifact to get started.</Hint>}
    {!!filtered?.length && <div className="project-grid">{filtered.map(p=><ProjectCard key={p.name} project={p}/>)}</div>}
    {!!data?.length && !filtered?.length && <Hint>No projects match “{search}”.</Hint>}</Section></main>;
}
export function ProjectPage() {
  const {project = ''} = useParams();
  const paused = useRefreshMode();
  const [params, setParams] = useSearchParams();
  const tab = params.get('view') ?? 'reports';
  const [search, setSearch] = useState(''), [revision, refresh] = useState(0);
  const [querySearch, setQuerySearch] = useState('');
  useEffect(() => {const timer = window.setTimeout(() => setQuerySearch(search), 200); return () => clearTimeout(timer);}, [search]);
  const selectedTags=params.getAll('tag');
  const pageNumber = Number(params.get('page'));
  const page = Number.isSafeInteger(pageNumber) && pageNumber > 0 ? pageNumber : 0;
  const [tagDraft, setTagDraft] = useState('');
  const query = new URLSearchParams({project, category: tab, search: querySearch, offset: String(page * 50), limit: '50'});
  [...selectedTags].sort().forEach(tag => query.append('tag', tag));
  const path = `/ui-api/artifacts?${query}`;
  const [result, setResult] = useState<{path: string; items: ArtifactSummary[]; total: number}>();
  const [error, setError] = useState<Error>(), [loading, setLoading] = useState(true);
  const visible = result?.path === path ? result : cachedList(path);
  const items = visible?.items ?? [];
  const total = visible?.total ?? 0;
  function refreshList() {invalidateList(path);refresh(n => n + 1);}
  useEffect(() => {
    document.title = `${project} · Cairn`;
    const controller = new AbortController();
    let timer: number;
    async function load() {
      setError(undefined);
      try {
        const data = await api<{items: ArtifactSummary[]; total: number}>(path, {signal: controller.signal});
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
      setResult({...cached, path});setLoading(false);setError(undefined);
      if (!paused) timer = window.setTimeout(poll, 5000);
    } else {setLoading(true);void load();}
    return () => {controller.abort();window.clearTimeout(timer);};
  }, [path, revision, paused]);
  const availableTags=[...new Set(items.flatMap(artifactTags))].sort();
  function toggleTag(tag:string){const next=new URLSearchParams(params);next.delete('page');next.delete('tag');for(const value of selectedTags.includes(tag)?selectedTags.filter(t=>t!==tag):[...selectedTags,tag])next.append('tag',value);setParams(next);}
  function changePage(value: number) {const next=new URLSearchParams(params);next.set('page', String(value));setParams(next);}
  const filtered = items;
  return <main><Hero eyebrow={<Crumbs items={[{label:'Projects',to:'/'},{label:project}]}/>} title={project} subtitle="Reports, experiments, and the evidence that connects them."/>
    <Section title={tab==='reports'?'Your reports':tab==='runs'?'Experiment runs':tab==='artifacts'?'Artifact library':'Everything'} actions={<Button small onClick={refreshList}>Refresh</Button>}>
    <Toolbar><SearchInput label={tab==='reports'?'Search reports':'Search artifacts'} value={search} onChange={value=>{setSearch(value);if(page)changePage(0);}}/><form onSubmit={e=>{e.preventDefault();const tag=tagDraft.trim();if(tag&&!selectedTags.includes(tag))toggleTag(tag);setTagDraft('');}}><SearchInput label="Filter by tag" value={tagDraft} onChange={setTagDraft}/></form></Toolbar>
    {(!!availableTags.length||!!selectedTags.length)&&<div className="tag-filters" aria-label="Filter by tags"><span className="muted">Tags</span>{[...new Set([...availableTags,...selectedTags])].map(tag=><button type="button" className="tag-chip" key={tag} aria-pressed={selectedTags.includes(tag)} onClick={()=>toggleTag(tag)}>{tag}</button>)}{!!selectedTags.length&&<button type="button" className="tag-clear" onClick={()=>{const next=new URLSearchParams(params);next.delete('tag');next.delete('page');setParams(next);}}>Clear tags</button>}</div>}
    {loading && !items.length && <Loading/>}{error && <ErrorNote error={error}/>}{!loading && !error && !filtered.length && <Hint>No matching {tab === 'reports' ? 'reports' : 'artifacts'} in this view.</Hint>}
    {!!filtered.length && tab==='reports' && <ReportGrid>{filtered.map(a=>{
      const metadata=a;
      return <ReportCard key={a.id} to={artifactPath(project,a.id)} project={project} title={typeof metadata?.title==='string'?metadata.title:a.name} description={typeof metadata?.description==='string'?metadata.description:undefined} status={a.outcome || a.state} updated={`${a.number ? 'Latest · v'+a.number+' · ' : ''}${when(a.finished_at || a.created_at)}`}/>;
    })}</ReportGrid>}
    {!!filtered.length && tab!=='reports' && <div className="artifact-list"><DataTable head={['Name','Namespace','State / version','Tags','Created']}>{filtered.map(a=><tr key={a.id}><td data-label="Name"><Link to={artifactPath(project,a.id)}><strong>{a.name}</strong></Link></td><td data-label="Namespace"><Pill tone="alias">{a.namespace}</Pill></td><td data-label="State / version"><Pill tone={a.state === 'finished' ? 'default':'pending'}>{a.number ? `v${a.number} · ${a.outcome || a.state}` : a.state}</Pill></td><td data-label="Tags"><ArtifactTags artifact={a} compact onTagClick={toggleTag} onChange={refreshList}/></td><td data-label="Created" className="muted nowrap">{when(a.created_at)}</td></tr>)}</DataTable></div>}
    <div className="pagination"><Hint>{total ? `${page*50+1}–${Math.min((page+1)*50,total)} of ${total}` : '0 results'}</Hint><Button disabled={page===0||loading} onClick={()=>changePage(page-1)}>Previous</Button><Button disabled={(page+1)*50>=total||loading} onClick={()=>changePage(page+1)}>Next</Button></div>
    </Section></main>;
}
