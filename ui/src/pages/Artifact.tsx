import { clearViews, readView, writeView } from '../viewCache';
import { invalidateLists } from '../listCache';
import { ArtifactTags } from '../components/ArtifactTags';
import { CopyId } from '../components/CopyId';
import { useEffect, useRef, useState } from 'react';
import { CheckCheck, RefreshCw, Trash2 } from 'lucide-react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useRefreshMode } from '../refresh';
import { Graph } from '../components/Graph';
import { api, post, envelope, projectPath, artifactPath, runWorkspacePath, when, type Artifact, type BlobRef, type Json } from '../api';
import { ActionIcon, BlobTable, Button, Crumbs, DataTable, DocumentReader, ErrorNote, Hero, Hint, JsonBlock, KeyValueTable, LinkButton, Loading, Pill, Section, Select, StatusPill, Tabs, ValueView } from '../components';
import { RecordsPanel } from './Records';
import { ConsolePanel } from './Console';
import { Workspace } from '../workspace/Workspace';

type Detail = { target: string; selected: Artifact; history: Artifact[] };
/** Soft removal behind a confirmation dialog; artifacts that still depend on it keep it readable by ID. */
function RemoveArtifact({artifact, reference}: {artifact: Artifact; reference: string}) {
  const navigate = useNavigate(), dialog = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState<Error>();
  const view = artifact.namespace === 'runs' ? 'runs' : artifact.namespace === 'reports' ? 'reports' : 'artifacts';
  async function remove() {
    setBusy(true); setError(undefined);
    try { await api(`/ui-api/artifacts/${artifact.id}`, {method: 'DELETE'}); invalidateLists(); clearViews(); navigate(`${projectPath(artifact.project)}?view=${view}`); }
    catch (e) { setError(e as Error); setBusy(false); }
  }
  return <><ActionIcon danger label="Remove artifact" onClick={() => dialog.current?.showModal()}><Trash2 size={17} aria-hidden="true"/></ActionIcon>
    <dialog ref={dialog} className="confirm-dialog" aria-labelledby={`remove-${artifact.id}`} onClick={event => {if (event.target === dialog.current && !busy) dialog.current.close();}} onClose={() => setError(undefined)}>
      <h2 id={`remove-${artifact.id}`}>Remove <code>{reference}</code>?</h2>
      <p>It disappears from lists and names. Artifacts that use it keep it readable by ID; <code>cairn gc</code> frees its storage.</p>
      {error && <ErrorNote error={error}/>}
      <div className="form-actions"><Button disabled={busy} onClick={() => dialog.current?.close()}>Cancel</Button><Button className="danger" disabled={busy} onClick={() => void remove()}>{busy ? 'Removing…' : 'Remove'}</Button></div>
    </dialog></>;
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
  const current = detail?.target === target ? detail : cached ? {target,selected:cached,history:[]} : undefined;
  const paused = useRefreshMode();

  useEffect(() => {
    const controller = new AbortController();let timer: number;
    let finished = readView<Artifact>(cacheKey)?.state === 'finished';
    async function load() {
      try {
        const [selected] = await post<Artifact[]>('/v1/resolutions', {refs:[target],project}, controller.signal);
        if (!selected || selected.project !== project) throw new Error('Artifact not found in this project');
        if (!controller.signal.aborted) {
          finished = selected.state === 'finished';
          const cached = readView<Artifact>(cacheKey);
          const stable = cached && JSON.stringify(cached) === JSON.stringify(selected) ? cached : selected;
          if (stable !== cached) writeView(cacheKey,stable);
          setError(undefined);
          setDetail(previous => previous?.target === target && (previous.selected === stable || JSON.stringify(previous.selected) === JSON.stringify(stable)) ? previous : {target,selected:stable,history:previous?.selected.id===stable.id?previous.history:[]});
          document.title = `${selected.name} · Cairn`;
        }
      } catch (e) {if (!controller.signal.aborted) setError(e as Error);}
      finally {if (!controller.signal.aborted && !paused && !finished) timer=window.setTimeout(poll,5000);}
    }
    function poll() {if(document.visibilityState==='visible')void load();else timer=window.setTimeout(poll,5000);}
    void load();
    return () => {controller.abort();window.clearTimeout(timer);};
  }, [target, project, refresh, paused, cacheKey]);

  // Version history is ancillary: it must never hold up report text or figures.
  const selectedId = current?.selected.id;
  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    api<Artifact[]>(`/ui-api/artifacts/${selectedId}/history`, {signal:controller.signal})
      .then(history => {if(!controller.signal.aborted)setDetail(previous=>{const selected=previous?.selected??readView<Artifact>(cacheKey);return selected?.id===selectedId?(previous?.target===target&&JSON.stringify(previous.history)===JSON.stringify(history)?previous:{target,selected,history}):previous;});})
      .catch(e=>{if(!controller.signal.aborted)setError(e as Error);});
    return ()=>controller.abort();
  }, [selectedId, refresh, cacheKey, target]);

  if (!current) return <main>{error ? <><ErrorNote error={error}/><Button onClick={() => setRefresh(n => n + 1)}>Retry</Button></> : <Loading what="Loading artifact"/>}</main>;
  const { selected, history } = current;
  const manifest = selected.manifest, metadata = {...(manifest?.metadata || selected.metadata),...selected.annotations};
  const value = manifest?.value, typed = envelope(value);
  const documentVersion = typed?.data && typeof typed.data==='object' && !Array.isArray(typed.data) ? Number(typed.data.document_version || 1) : 1;
  const report = typed?.format === 'report/bundle', run = selected.namespace === 'runs';
  const live = selected.state !== 'finished';
  const ref = `${selected.namespace}/${selected.name}${selected.number ? `@v${selected.number}` : ''}`;
  const choices = history.slice().reverse().map(a => ({ value: a.id, label: `v${a.number} · ${a.manifest?.outcome || a.state}` }));
  if (!choices.some(c => c.value === selected.id)) choices.unshift({ value: selected.id, label: selected.number ? `v${selected.number} · ${selected.manifest?.outcome || selected.state}` : `${selected.state} · ${selected.id.slice(0, 12)}` });
  const captures = Object.entries(manifest?.captures || {}).filter(([name]) => !name.startsWith('console/'));
  const showConsole = run && (live || captures.length < Object.keys(manifest?.captures || {}).length);
  const tabs = [{ value: 'overview', label: report ? 'Report' : 'Overview' }, ...(run ? [{ value: 'workspace', label: 'Workspace' }] : []), { value: 'records', label: 'Records' }, ...(showConsole ? [{ value: 'console', label: 'Console' }] : []), { value: 'files', label: 'Files' }, { value: 'lineage', label: 'Lineage' }, { value: 'versions', label: 'History', count: history.length }, { value: 'metadata', label: 'Metadata' }];
  const namedBlobs: BlobRef[] = [];
  const visit = (v: unknown) => {
    if (!v || typeof v !== 'object') return;
    const t = envelope(v as Json);
    if (t) { namedBlobs.push(...(t.blobs || [])); if (t.format === 'json/literal') return; }
    for (const [key, child] of Object.entries(v)) if (key !== 'blobs' && key !== 'refs') visit(child);
  };
  visit(value); visit(Object.fromEntries(captures));
  const unique = new Map<string, BlobRef>();
  for (const blob of [...namedBlobs, ...(manifest?.blobs || [])]) if (!unique.has(blob.digest)) unique.set(blob.digest, blob);

  return <main className={report ? 'report-page' : undefined}>
    <Hero eyebrow={<Crumbs items={[{ label: 'Projects', to: '/' }, { label: selected.project, to: projectPath(selected.project) }, { label: selected.namespace }]}/>} title={typeof metadata.title === 'string' ? metadata.title : selected.name} subtitle={typeof metadata.description === 'string' ? metadata.description : undefined} status={<StatusPill status={manifest?.outcome || selected.state}/>}/>
    {error && <ErrorNote error={error}/>}
    <div className="detail-bar"><code className="wrap-any">{ref}</code><Select label="Artifact version" value={selected.id} choices={choices} onChange={target => navigate(run && tab === 'workspace' ? runWorkspacePath(selected.project,target) : artifactPath(selected.project, target))}/><ActionIcon label="Refresh" onClick={() => setRefresh(n => n + 1)}><RefreshCw size={17} aria-hidden="true"/></ActionIcon><RemoveArtifact key={selected.id} artifact={selected} reference={ref}/></div>
    <div className="detail-facts"><span>{live ? 'Started' : 'Finished'} {when(selected.finished_at || selected.created_at)}</span><span>{selected.cutoff} entries</span><CopyId value={selected.id} short/></div>
    <ArtifactTags key={`tags:${selected.id}`} artifact={selected} scopes onChange={()=>setRefresh(n=>n+1)}/>
    {live && <Hint>This artifact is {selected.state}. Its ID stays the same when it finishes.</Hint>}
    <Tabs label="Artifact detail" tabs={tabs} value={tab} onChange={tab => { const next = new URLSearchParams(params); next.set('tab', tab); setParams(next); }}/>
    {tab === 'overview' && <>
      {report ? <Section title="Published report" actions={<><ActionIcon label={params.get('resolved')==='1'?'Hide resolved comments':'Show resolved comments'} aria-pressed={params.get('resolved')==='1'} onClick={()=>{const next=new URLSearchParams(params);if(next.get('resolved')==='1')next.delete('resolved');else next.set('resolved','1');setParams(next,{replace:true});}}><CheckCheck size={17} aria-hidden="true"/></ActionIcon><LinkButton href={`/ui-api/reports/${selected.id}/index.html`}>HTML export</LinkButton></>}><DocumentReader documentVersion={documentVersion} key={selected.id} project={selected.project} version={selected.id} blobs={typed?.blobs || []}/></Section> : <>
        {run && <RecordsPanel key={`records:${selected.id}`} reference={selected.id} project={selected.project} run overview live={live} refresh={refresh}/>}
        <Section title={run ? 'Final result' : 'Value'}><ValueView value={value}/></Section>
      </>}
      {metadata.config !== undefined && <Section title="Configuration"><JsonBlock value={metadata.config}/></Section>}
      {captures.length > 0 && <Section title="Captures">{captures.map(([name, value]) => <Section key={name} title={name}><ValueView value={value}/></Section>)}</Section>}
    </>}
    {tab === 'workspace' && run && <Workspace key={selected.id} project={selected.project} runId={selected.id}/>}
    {tab === 'records' && <RecordsPanel key={`records:${selected.id}`} reference={selected.id} project={selected.project} run={false} live={live} refresh={refresh}/>}
    {tab === 'console' && <ConsolePanel key={`console:${selected.id}`} reference={selected.id} project={selected.project} live={live}/>}
    {tab === 'files' && <Section title="Files"><BlobTable blobs={[...unique.values()]}/>{live && <Hint>Captured values are available in the capture records while this artifact is live. Finished artifacts collect them here.</Hint>}</Section>}
    {tab === 'metadata' && <><Section title="Metadata"><JsonBlock value={metadata}/></Section><Section title="Identity"><KeyValueTable rows={[["Artifact ID", <CopyId value={selected.id}/>], ["State", selected.state], ["Version label", selected.number ? `v${selected.number}` : 'Not finished'], ["Project", selected.project], ["Namespace", selected.namespace]]}/></Section><Section title="Annotations"><JsonBlock value={selected.annotations}/></Section><Section title="Raw descriptor"><JsonBlock value={selected}/></Section></>}
    {tab === 'lineage' && <Section title="Lineage"><Graph root={selected.id} project={selected.project}/><Section title="Pinned inputs">{manifest?.dependencies.length ? <DataTable head={['Artifact', 'Role']}>{manifest.dependencies.map(d => <tr key={d.artifact_id + d.role}><td><CopyId value={d.artifact_id}/><Link to={artifactPath(selected.project,d.artifact_id)} aria-label="Open input artifact"> ↗</Link></td><td><Pill tone="alias">{d.role}</Pill></td></tr>)}</DataTable> : <Hint>{live ? 'The graph includes the dependencies recorded so far.' : 'No input dependencies recorded.'}</Hint>}</Section></Section>}
    {tab === 'versions' && <Section title="Finished artifacts with this name">{history.length ? <DataTable head={['Version', 'Artifact ID', 'Outcome', 'Entries', 'Finished']}>{history.slice().reverse().map(a => <tr key={a.id} className={a.id === selected.id ? 'selected' : ''}><td><Link to={artifactPath(a.project, a.id)}>v{a.number}</Link></td><td><CopyId value={a.id} short/></td><td><StatusPill status={a.manifest?.outcome || a.state}/></td><td>{a.cutoff}</td><td>{when(a.finished_at || a.created_at)}</td></tr>)}</DataTable> : <Hint>No finished artifacts with this name yet.</Hint>}</Section>}
  </main>;
}
