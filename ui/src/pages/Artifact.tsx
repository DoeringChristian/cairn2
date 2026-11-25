import { clearViews, readView, writeView } from '../viewCache';
import { invalidateLists } from '../listCache';
import { ArtifactTags } from '../components/ArtifactTags';
import { CopyId } from '../components/CopyId';
import { useEffect, useRef, useState } from 'react';
import { RefreshCw, Trash2 } from 'lucide-react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useRefreshMode } from '../refresh';
import { Graph } from '../components/Graph';
import { current as currentValues, filePath, getArtifact, isLost, operate, post, print, projectPath, artifactPath, reference as referenceOf, runWorkspacePath, status, useLoaded, when, type Artifact, type FoundFile, type Link as Dependency } from '../api';
import { ActionIcon, BlobTable, Button, Crumbs, DataTable, ErrorNote, Hero, Hint, JsonBlock, KeyValueTable, Loading, Pill, Section, Select, SearchInput, StatusPill, Tabs, Toolbar, ValueView } from '../components';
import { RecordsPanel } from './Records';
import { ConsolePanel } from './Console';
import { Workspace } from '../workspace/Workspace';

type Detail = { target: string; selected: Artifact };
/** Soft removal behind a confirmation dialog; artifacts that still depend on it keep it readable by ID. */
function RemoveArtifact({artifact, reference}: {artifact: Artifact; reference: string}) {
  const navigate = useNavigate(), dialog = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState<Error>();
  const view = artifact.namespace === 'runs' ? 'runs' : 'artifacts';
  async function remove() {
    setBusy(true); setError(undefined);
    try { await operate('delete', {artifact: artifact.id}); invalidateLists(); clearViews(); navigate(`${projectPath(artifact.project)}?view=${view}`); }
    catch (e) { setError(e as Error); setBusy(false); }
  }
  return <><ActionIcon danger label="Remove artifact" onClick={() => dialog.current?.showModal()}><Trash2 size={17} aria-hidden="true"/></ActionIcon>
    <dialog ref={dialog} className="confirm-dialog" aria-labelledby={`remove-${artifact.id}`} onClick={event => {if (event.target === dialog.current && !busy) dialog.current.close();}} onClose={() => setError(undefined)}>
      <h2 id={`remove-${artifact.id}`}>Remove <code>{reference}</code>?</h2>
      <p>It disappears from lists and names. <code>cairn restore {artifact.id.slice(0,12)}…</code> shows it again with its whole ID, until <code>cairn collect</code> frees it; artifacts that use it keep it readable.</p>
      {error && <ErrorNote error={error}/>}
      <div className="form-actions"><Button disabled={busy} onClick={() => dialog.current?.close()}>Cancel</Button><Button className="danger" disabled={busy} onClick={() => void remove()}>{busy ? 'Removing…' : 'Remove'}</Button></div>
    </dialog></>;
}

/** An unfinished artifact: whether its process is still heard from, and finishing one that is gone. */
function Unfinished({artifact, onStopped}: {artifact: Artifact; onStopped: () => void}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState<Error>();
  async function stop() {
    setBusy(true); setError(undefined);
    try { await post(`/ui-api/stop/${artifact.id}`, {}); invalidateLists(); dialog.current?.close(); onStopped(); }
    catch (e) { setError(e as Error); }
    setBusy(false);
  }
  return <><Hint>{isLost(artifact) ? `Lost contact: nothing was heard from its process since ${when(artifact.heard_at)}.` : 'This artifact is live. Its ID stays the same when it finishes.'}</Hint>
    {isLost(artifact) && <Button small onClick={() => dialog.current?.showModal()}>Mark as stopped</Button>}
    <dialog ref={dialog} className="confirm-dialog" aria-labelledby={`stop-${artifact.id}`} onClick={event => {if (event.target === dialog.current && !busy) dialog.current.close();}} onClose={() => setError(undefined)}>
      <h2 id={`stop-${artifact.id}`}>Mark <code>{artifact.name}</code> as stopped?</h2>
      <p>It finishes as incomplete, which cannot be undone. Do this only when its process is gone: records it has not delivered yet can no longer be added.</p>
      {error && <ErrorNote error={error}/>}
      <div className="form-actions"><Button disabled={busy} onClick={() => dialog.current?.close()}>Cancel</Button><Button className="danger" disabled={busy} onClick={() => void stop()}>{busy ? 'Stopping…' : 'Mark as stopped'}</Button></div>
    </dialog></>;
}

/** Current files, found by part of their path. */
function FilesPanel({artifact, revision}: {artifact: string; revision: number}) {
  const [search, setSearch] = useState(''), [limit, setLimit] = useState(200);
  const {data, error} = useLoaded(signal => operate<{items: FoundFile[]; more: boolean}>('files', {artifact, text: search, limit}, signal), `files:${artifact}:${search}:${limit}`, revision);
  return <Section title="Files"><Toolbar><SearchInput label="Search files" value={search} onChange={setSearch}/></Toolbar>{error && <ErrorNote error={error}/>}
    {data ? <FileTable artifact={artifact} files={data.items}/> : !error && <Loading/>}
    {data?.more && <Button onClick={() => setLimit(limit * 5)}>Show more</Button>}</Section>;
}
function Made({artifact}: {artifact: string}) {
  const {data, error} = useLoaded(async signal => (await operate<{items: FoundFile[]}>('files', {artifact, pattern: '_about/**'}, signal)).items, `made:${artifact}`);
  return error ? <ErrorNote error={error}/> : data?.length ? <FileTable artifact={artifact} files={data}/> : null;
}
const FileTable = ({artifact, files}: {artifact: string; files: FoundFile[]}) => <BlobTable blobs={files.map(file => ({digest: file.blob.digest, size: file.blob.size, media_type: file.blob.media_type, name: file.path, href: filePath(artifact, file.path)}))}/>;
function Uses({artifact, revision}: {artifact: string; revision: number}) {
  const {data, error} = useLoaded(async signal => (await operate<{items: Dependency[]}>('links', {artifact}, signal)).items, `links:${artifact}`, revision);
  if (error) return <ErrorNote error={error}/>;
  return data?.length ? <DataTable head={['Artifact', 'As']}>{data.map(({path, artifact: used}) => <tr key={path + used.id}><td><Link to={artifactPath(used.project, used.id)}>{referenceOf(used)}</Link></td><td><Pill tone="alias">{path.replace(/^_use\//, '')}</Pill></td></tr>)}</DataTable> : <Hint>Uses nothing.</Hint>;
}

export function ArtifactPage() {
  const { project = '', id, version } = useParams();
  // /v remains a route alias for bookmarked links, but identities are always artifacts.
  const target = id || version || '';
  const [params, setParams] = useSearchParams(), navigate = useNavigate();
  const tab = params.get('tab') || 'overview';
  const [detail, setDetail] = useState<Detail>(), [error, setError] = useState<Error>();
  const [refresh, setRefresh] = useState(0);
  const cacheKey=`artifact:${project}:${target}`;
  const cached=readView<Artifact>(cacheKey);
  const current = detail?.target === target ? detail : cached ? {target,selected:cached} : undefined;
  const paused = useRefreshMode();

  useEffect(() => {
    const controller = new AbortController();let timer: number;
    let finished = readView<Artifact>(cacheKey)?.finished;
    async function load() {
      try {
        const selected = await getArtifact(target, project, controller.signal);
        if (selected.project !== project) throw new Error('Artifact not found in this project');
        if (!controller.signal.aborted) {
          finished = selected.finished;
          const cached = readView<Artifact>(cacheKey);
          const stable = cached && JSON.stringify(cached) === JSON.stringify(selected) ? cached : selected;
          if (stable !== cached) writeView(cacheKey,stable);
          setError(undefined);
          setDetail(previous => previous?.target === target && (previous.selected === stable || JSON.stringify(previous.selected) === JSON.stringify(stable)) ? previous : {target,selected:stable});
          document.title = `${selected.name} · Cairn`;
        }
      } catch (e) {if (!controller.signal.aborted) setError(e as Error);}
      finally {if (!controller.signal.aborted && !paused && !finished) timer=window.setTimeout(poll,5000);}
    }
    function poll() {if(document.visibilityState==='visible')void load();else timer=window.setTimeout(poll,5000);}
    void load();
    return () => {controller.abort();window.clearTimeout(timer);};
  }, [target, project, refresh, paused, cacheKey]);

  // Version history is ancillary: it must never hold up the value or figures.
  const selectedId = current?.selected.id;
  const named = current && {project: current.selected.project, namespaces: [current.selected.namespace], name: current.selected.name};
  const {data: history = []} = useLoaded(named ? async signal => (await operate<{items: Artifact[]}>('find', {...named, state: 'finished', values: ['_outcome'], limit: 1000}, signal)).items : null, `history:${JSON.stringify(named)}`, refresh);
  const values = useLoaded(selectedId ? signal => currentValues(selectedId, signal) : null, `values:${selectedId}`, refresh + (current?.selected.records || 0));

  if (!current) return <main>{error ? <><ErrorNote error={error}/><Button onClick={() => setRefresh(n => n + 1)}>Retry</Button></> : <Loading what="Loading artifact"/>}</main>;
  const { selected } = current;
  const run = selected.namespace === 'runs', live = !selected.finished, ref = referenceOf(selected);
  const label = (a: Artifact) => a.version ? `v${a.version} · ${status(a)}${a.aliases.length ? ` · @${a.aliases.join(' @')}` : ''}` : `${status(a)} · ${a.id.slice(0, 12)}`;
  const choices = history.map(a => ({ value: a.id, label: label(a) }));
  if (!choices.some(c => c.value === selected.id)) choices.unshift({ value: selected.id, label: label(selected) });
  const {_config: config, _about: about, _error: problem, ...rest} = values.data || {};
  const shown = Object.entries(rest).filter(([key, value]) => !key.startsWith('_') && typeof value !== 'number'), numbers = Object.entries(rest).filter(([key, value]) => !key.startsWith('_') && typeof value === 'number');
  const tabs = [{ value: 'overview', label: 'Overview' }, ...(run ? [{ value: 'workspace', label: 'Workspace' }] : []), { value: 'records', label: 'Records' }, ...(run ? [{ value: 'console', label: 'Console' }] : []), { value: 'files', label: 'Files' }, { value: 'lineage', label: 'Lineage' }, { value: 'versions', label: 'History', count: history.length }, { value: 'metadata', label: 'Details' }];
  return <main>
    <Hero eyebrow={<Crumbs items={[{ label: 'Projects', to: '/' }, { label: selected.project, to: projectPath(selected.project) }, { label: selected.namespace }]}/>} title={selected.name} status={<StatusPill status={status(selected)}/>}/>
    {error && <ErrorNote error={error}/>}
    <div className="detail-bar"><code className="wrap-any">{ref}</code><Select label="Artifact version" value={selected.id} choices={choices} onChange={target => navigate(run && tab === 'workspace' ? runWorkspacePath(selected.project,target) : artifactPath(selected.project, target))}/><ActionIcon label="Refresh" onClick={() => setRefresh(n => n + 1)}><RefreshCw size={17} aria-hidden="true"/></ActionIcon><RemoveArtifact key={selected.id} artifact={selected} reference={ref}/></div>
    <div className="detail-facts"><span>{live ? 'Started' : 'Finished'} {when(selected.finished_at || selected.created_at)}</span><span>{selected.records} records</span>{selected.aliases.map(alias => <Pill key={alias} tone="alias">@{alias}</Pill>)}<CopyId value={selected.id} short/></div>
    <ArtifactTags key={`tags:${selected.id}`} artifact={selected} onChange={()=>setRefresh(n=>n+1)}/>
    {live && <Unfinished artifact={selected} onStopped={() => setRefresh(n => n + 1)}/>}
    <Tabs label="Artifact detail" tabs={tabs} value={tab} onChange={tab => { const next = new URLSearchParams(params); next.set('tab', tab); setParams(next); }}/>
    {tab === 'overview' && <>
      {problem !== undefined && <Section title="Error"><JsonBlock value={problem}/></Section>}
      {run && <RecordsPanel key={`records:${selected.id}`} artifact={selected.id} overview live={live} refresh={refresh}/>}
      {values.error && <ErrorNote error={values.error}/>}
      {numbers.length > 0 && <Section title="Last values"><KeyValueTable rows={numbers.map(([key, value]) => [key, print(value)])}/></Section>}
      {config !== undefined && <Section title="Configuration"><JsonBlock value={config}/></Section>}
      {shown.map(([key, value]) => <Section key={key} title={key}><ValueView value={value}/></Section>)}
    </>}
    {tab === 'workspace' && run && <Workspace key={selected.id} project={selected.project} runId={selected.id}/>}
    {tab === 'records' && <RecordsPanel key={`records:${selected.id}`} artifact={selected.id} live={live} refresh={refresh}/>}
    {tab === 'console' && <ConsolePanel key={`console:${selected.id}`} artifact={selected.id} live={live}/>}
    {tab === 'files' && <FilesPanel key={selected.id} artifact={selected.id} revision={refresh + selected.records}/>}
    {tab === 'metadata' && <><Section title="Identity"><KeyValueTable rows={[["Artifact ID", <CopyId value={selected.id}/>], ["State", status(selected)], ["Version", selected.version ? `v${selected.version}` : 'Not finished'], ["Project", selected.project], ["Namespace", selected.namespace], ["Started", when(selected.created_at)], ...(selected.finished_at ? [["Finished", when(selected.finished_at)] as [string, string]] : [["Last heard", when(selected.heard_at)] as [string, string]])]}/></Section>{about !== undefined && <Section title="Made by"><JsonBlock value={about}/><Made artifact={selected.id}/></Section>}<Section title="Tags"><JsonBlock value={selected.tags}/></Section></>}
    {tab === 'lineage' && <Section title="Lineage"><Graph root={selected.id} project={selected.project}/><Section title="Uses"><Uses artifact={selected.id} revision={refresh + selected.records}/></Section></Section>}
    {tab === 'versions' && <Section title="Finished artifacts with this name">{history.length ? <DataTable head={['Version', 'Artifact ID', 'Outcome', 'Aliases', 'Records', 'Finished']}>{history.map(a => <tr key={a.id} className={a.id === selected.id ? 'selected' : ''}><td><Link to={artifactPath(a.project, a.id)}>v{a.version}</Link></td><td><CopyId value={a.id} short/></td><td><StatusPill status={status(a)}/></td><td>{a.aliases.map(alias => <Pill key={alias} tone="alias">@{alias}</Pill>)}</td><td>{a.records}</td><td>{when(a.finished_at)}</td></tr>)}</DataTable> : <Hint>No finished artifacts with this name yet.</Hint>}</Section>}
  </main>;
}
