import { ArtifactTags, artifactTags } from '../components/ArtifactTags';
import { useRefresh } from '../refresh';
import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api, post, useResource, artifactPath, when, type Project, type Artifact } from '../api';
import { Button, Crumbs, DataTable, ErrorNote, Hero, Hint, Loading, MetricCard, MetricGrid, Pill, ProjectCard, ReportCard, ReportGrid, SearchInput, Section, Tabs, Toolbar } from '../components';
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
  const pulse = useRefresh();
  const [params, setParams] = useSearchParams();
  const tab = params.get('view') ?? 'reports';
  const [search, setSearch] = useState(''), [revision, refresh] = useState(0);
  const [latestReports,setLatestReports]=useState<Map<string,string>>(new Map());
  const [items, setItems] = useState<Artifact[]>([]), [error, setError] = useState<Error>(), [loading, setLoading] = useState(true);
  useEffect(() => {
    document.title = `${project} · Cairn`; const controller = new AbortController();
    setError(undefined); setLoading(previous => items.length === 0 ? true : previous);
    (async () => {
      let cursor: string | null = null; const all: Artifact[] = [];
      do {
        const query = new URLSearchParams({project, limit:'1000', ...(cursor ? {cursor} : {})});
        const page: {items: Artifact[]; next_cursor: string | null} = await api(`/v1/artifacts?${query}`, {signal: controller.signal});
        all.push(...page.items); cursor = page.next_cursor;
      } while (cursor);
      const reportNames=[...new Set(all.filter(a=>a.namespace==='reports'&&a.state==='finished').map(a=>a.name))];
      const latest=reportNames.length?await post<Artifact[]>('/v1/resolutions',{project,refs:reportNames.map(name=>`reports/${name}@latest`)},controller.signal):[];
      if(!controller.signal.aborted){setLatestReports(new Map(latest.map(a=>[a.name,a.id])));setItems(all.sort((a,b)=>b.created_at.localeCompare(a.created_at)));}

    })().catch(e => {if (!controller.signal.aborted) setError(e);}).finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [project, revision, pulse]);
  const selectedTags=params.getAll('tag');
  const availableTags=[...new Set(items.flatMap(artifactTags))].sort();
  function toggleTag(tag:string){const next=new URLSearchParams(params);next.delete('tag');for(const value of selectedTags.includes(tag)?selectedTags.filter(t=>t!==tag):[...selectedTags,tag])next.append('tag',value);setParams(next);}
  const category = (a: Artifact) => a.namespace === 'runs' ? 'runs' : a.namespace === 'reports' ? 'reports' : 'artifacts';
  const reportsByName = new Map<string, Artifact>();
  for (const item of items.filter(a => a.namespace === 'reports')) {
    const previous = reportsByName.get(item.name);
    // vN is assigned on finish: newest creation time need not be latest publication.
    const newer = !previous || (item.number ?? 0) > (previous.number ?? 0)
      || (item.number === previous.number && item.created_at > previous.created_at);
    if (item.id===latestReports.get(item.name) || ((!previous||previous.id!==latestReports.get(item.name))&&newer)) reportsByName.set(item.name, item);
  }
  const reportItems = [...reportsByName.values()].sort((a,b) => (b.finished_at || b.created_at).localeCompare(a.finished_at || a.created_at));
  const filtered = (tab === 'reports' ? reportItems : items).filter(a => (tab === 'all' || category(a) === tab) && selectedTags.every(tag=>artifactTags(a).includes(tag)) && `${a.namespace}/${a.name}`.toLowerCase().includes(search.toLowerCase()));
  return <main><Hero eyebrow={<Crumbs items={[{label:'Projects',to:'/'},{label:project}]}/>} title={project} subtitle="Reports, experiments, and the evidence that connects them."/>
    <Section title={tab==='reports'?'Your reports':tab==='runs'?'Experiment runs':'Artifact library'} actions={<Button small onClick={() => refresh(n=>n+1)}>Refresh</Button>}><Tabs label="Artifact categories" value={tab} onChange={view => {const next=new URLSearchParams(params);next.set('view',view);setParams(next);}} tabs={['reports','runs','artifacts','all'].map(value => ({value,label:value[0].toUpperCase()+value.slice(1),count:value==='reports'?reportItems.length:items.filter(a=>value==='all'||category(a)===value).length}))}/>
    <Toolbar><SearchInput label={tab==='reports'?'Search reports':'Search artifacts'} value={search} onChange={setSearch}/></Toolbar>
    {(!!availableTags.length||!!selectedTags.length)&&<div className="tag-filters" aria-label="Filter by tags"><span className="muted">Tags</span>{[...new Set([...availableTags,...selectedTags])].map(tag=><button type="button" className="tag-chip" key={tag} aria-pressed={selectedTags.includes(tag)} onClick={()=>toggleTag(tag)}>{tag}</button>)}{!!selectedTags.length&&<button type="button" className="tag-clear" onClick={()=>{const next=new URLSearchParams(params);next.delete('tag');setParams(next);}}>Clear tags</button>}</div>}
    {loading && <Loading/>}{error && <ErrorNote error={error}/>}{!loading && !error && !filtered.length && <Hint>{items.length ? (tab==='reports'?'No reports here yet. Publish a report to bring this project’s findings together.':'No matching artifacts in this view.') : 'No artifacts in this project yet.'}</Hint>}
    {!!filtered.length && tab==='reports' && <ReportGrid>{filtered.map(a=>{
      const metadata={...(a.manifest?.metadata || a.metadata),...a.annotations};
      return <ReportCard key={a.id} to={artifactPath(project,a.id)} project={project} title={typeof metadata?.title==='string'?metadata.title:a.name} description={typeof metadata?.description==='string'?metadata.description:undefined} status={a.manifest?.outcome || a.state} updated={`${a.number ? 'Latest · v'+a.number+' · ' : ''}${when(a.finished_at || a.created_at)}`}/>;
    })}</ReportGrid>}
    {!!filtered.length && tab!=='reports' && <DataTable head={['Name','Namespace','State / version','Tags','Created']}>{filtered.map(a=><tr key={a.id}><td><Link to={artifactPath(project,a.id)}><strong>{a.name}</strong></Link></td><td><Pill tone="alias">{a.namespace}</Pill></td><td><Pill tone={a.state === 'finished' ? 'default':'pending'}>{a.number ? `v${a.number} · ${a.manifest?.outcome || a.state}` : a.state}</Pill></td><td><ArtifactTags artifact={a} compact onTagClick={toggleTag} onChange={()=>refresh(n=>n+1)}/></td><td className="muted nowrap">{when(a.created_at)}</td></tr>)}</DataTable>}</Section></main>;
}
