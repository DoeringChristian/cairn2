import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { snippetCompletion, type CompletionSource } from '@codemirror/autocomplete';
import type { EditorView } from '@codemirror/view';
import { List, Trash2 } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { WebsocketProvider } from 'y-websocket';
import * as Y from 'yjs';
import { api, artifactPath, operate, projectPath, resolve, type Artifact, type FoundFile } from '../api';
import { ActionIcon, Button, Crumbs, ErrorNote, Hero, Loading, Section } from '../components';
import { Editor, type Heading } from './Editor';
import { ReportElement, type Known } from './elements';
import { calloutKinds } from './markdown';
import { citing } from './reference';
import { address, type Report } from './ReportsPage';


/** Completes references to artifacts and their files, figures, callouts and blocks. */
const completions = (project: string): CompletionSource => async (context) => {
  const file = context.matchBefore(/cairn:[^\s()#]+#[^\s()]*/);
  if (file) {
    const [ref, path] = file.text.slice(6).split(/#(.*)/s);
    const [artifact] = await resolve([ref], project).catch(() => []);
    if (!artifact) return null;
    const [{ items }, { keys }] = await Promise.all([operate<{ items: FoundFile[] }>('files', { artifact: artifact.id, limit: 1000 }), operate<{ keys: string[] }>('keys', { artifact: artifact.id })]);
    const labels = [...new Set([...keys.filter((key) => !key.startsWith('_')), ...items.map((item) => item.path.replace(/\/data$/, ''))])];
    return { from: file.to - path.length, options: labels.map((label) => ({ label })), validFor: /^[^\s()]*$/ };
  }
  const ref = context.matchBefore(/cairn:[^\s()#]*/);
  if (ref) {
    const text = ref.text.slice(6).split('@')[0].split('/').slice(1).join('/');
    const { items } = await operate<{ items: Artifact[] }>('find', { project, text, latest: true, limit: 50 });
    return { from: ref.from + 6, options: items.map((item) => ({ label: `${item.namespace}/${item.name}@v${item.version}` })) };
  }
  const fence = context.matchBefore(/^```\w*/);
  if (fence)
    return {
      from: fence.from,
      options: [
        snippetCompletion('```card\nkind: scalar\nruns: ${runs/*}\nx: step\ny: ${loss}\n```\n', { label: '```card', detail: 'A live plot, images or figures of runs' }),
        snippetCompletion('```view\nviewer: cairn:${viewers/name}#index.html\n```\n', { label: '```view', detail: 'A viewer from an artifact' }),
      ],
    };
  const quote = context.matchBefore(/^>\s*\[?!?\w*/);
  if (quote) return { from: quote.from, options: calloutKinds.map((kind) => ({ label: `> [!${kind}]`, apply: `> [!${kind}] `, detail: 'Callout' })) };
  const image = context.matchBefore(/!\[[^\]\n]*$/);
  if (image) {
    const closed = context.state.sliceDoc(context.pos, context.pos + 1) === ']' ? 1 : 0;
    return { from: image.from, to: context.pos + closed, options: [snippetCompletion('![${caption}](cairn:${})', { label: '![…](cairn:…)', detail: 'Something of a run' })] };
  }
  return null;
};

/** The shared text of a report, in step with the server. */
function useShared(project: string, name: string) {
  const [state, setState] = useState<{ text?: Y.Text; provider?: WebsocketProvider; files: Record<string, Known>; status: string; error?: Error }>({ files: {}, status: 'Opening…' });
  useEffect(() => {
    const controller = new AbortController();
    const doc = new Y.Doc();
    let provider: WebsocketProvider | undefined;
    setState({ files: {}, status: 'Opening…' });
    // The space figures need, before any text is drawn; a slow server does not hold the text up.
    const files = Promise.race([
      api<Record<string, Known>>(`/ui-api/report/files?${new URLSearchParams({ project, name })}`, { signal: controller.signal }).catch(() => ({})),
      new Promise<Record<string, Known>>((done) => setTimeout(() => done({}), 1500)),
    ]);
    api<Report>(address(project, name), { signal: controller.signal })
      .then(({ epoch }) => {
        if (controller.signal.aborted) return;
        const made = new WebsocketProvider(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ui-api/live`, 'report', doc, { params: { project, name, epoch }, disableBc: true });
        provider = made;
        const color = `hsl(${Math.floor(Math.random() * 360)} 45% 45%)`;
        made.awareness.setLocalStateField('user', { name: 'Someone', color, colorLight: color.replace(')', ' / .2)') });
        const tell = (status: string) => setState((previous) => ({ ...previous, status }));
        made.on('sync', (synced: boolean) => synced && void files.then((known) => controller.signal.aborted || setState({ text: doc.getText('text'), provider: made, files: known, status: 'Saved as you write' })));
        made.on('status', ({ status }: { status: string }) => status === 'disconnected' && tell('Offline · your changes are kept until it is back'));
        made.on('connection-close', (event: CloseEvent | null) => {
          if (event && [4401, 4404, 4409].includes(event.code)) {
            made.disconnect();
            setState((previous) => ({ ...previous, error: new Error(event.code === 4404 ? 'This report was deleted.' : 'This report was replaced on the server. Copy what you still need and reload the page.') }));
          }
        });
      })
      .catch((error) => {
        if (!controller.signal.aborted) setState({ files: {}, status: '', error });
      });
    return () => {
      controller.abort();
      provider?.destroy();
      doc.destroy();
    };
  }, [project, name]);
  return state;
}

function Outline({ headings, onOpen }: { headings: Heading[]; onOpen: (from: number) => void }) {
  const [wide, setWide] = useState(() => matchMedia('(min-width: 761px)').matches);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const query = matchMedia('(min-width: 761px)');
    const change = () => setWide(query.matches);
    query.addEventListener('change', change);
    return () => query.removeEventListener('change', change);
  }, []);
  if (!headings.length) return null;
  const top = Math.min(...headings.map((heading) => heading.level));
  const list = (
    <nav className="report-toc" aria-label="Table of contents">
      <button type="button" className="report-toc-toggle" aria-expanded={wide || open} onClick={() => setOpen(!open)}><List size={17} aria-hidden="true" />On this page</button>
      {(wide || open) && (
        <ol>
          {headings.map((heading) => (
            <li key={heading.from} style={{ paddingLeft: Math.min(heading.level - top, 3) * 12 }}>
              <a href="#" onClick={(event) => { event.preventDefault(); onOpen(heading.from); setOpen(false); }}>{heading.title || 'Untitled'}</a>
            </li>
          ))}
        </ol>
      )}
    </nav>
  );
  const slot = wide ? document.getElementById('report-toc-slot') : null;
  return slot ? createPortal(list, slot) : list;
}

export function ReportPage() {
  const { project = '', '*': name = '' } = useParams();
  const navigate = useNavigate();
  const { text, provider, files, status, error } = useShared(project, name);
  const [headings, setHeadings] = useState<Heading[]>([]);
  const [failure, setFailure] = useState<Error>();
  const view = useRef<EditorView>();
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    document.title = `${name} · Cairn`;
    setHeadings([]);
  }, [name]);
  const follow = useCallback(async (href: string) => {
    if (!href.startsWith('cairn:')) {
      // Another page of Cairn opens here, as other reports, workspaces and runs.
      const url = new URL(href, location.href);
      if (url.origin === location.origin) navigate(url.pathname + url.search + url.hash);
      else if (/^https?:$/.test(url.protocol)) window.open(href, '_blank', 'noopener');
      return;
    }
    try {
      const [artifact] = await resolve([href.slice(6).split('#')[0]], project);
      navigate(artifactPath(artifact.project, artifact.id));
    } catch (e) {
      setFailure(e as Error);
    }
  }, [project, navigate]);
  async function remove() {
    try {
      await api(address(project, name), { method: 'DELETE' });
      navigate(projectPath(project));
    } catch (e) {
      dialog.current?.close();
      setFailure(e as Error);
    }
  }
  function show(from: number) {
    const current = view.current;
    if (!current) return;
    window.scrollTo({ top: current.lineBlockAt(Math.min(from, current.state.doc.length)).top + current.documentTop + window.scrollY - 24 });
  }
  return (
    <main className="report-page">
      <Hero eyebrow={<Crumbs items={[{ label: 'Projects', to: '/' }, { label: project, to: projectPath(project) }, { label: 'Reports' }]} />} title={name} />
      <dialog ref={dialog} className="confirm-dialog" aria-label="Delete report">
        <h2>Delete <code>{name}</code>?</h2>
        <p>Its Markdown file is deleted. Runs and artifacts it shows stay as they are.</p>
        <div className="form-actions"><Button onClick={() => dialog.current?.close()}>Cancel</Button><Button className="danger" onClick={() => void remove()}>Delete</Button></div>
      </dialog>
      {error && <ErrorNote error={error} />}
      {failure && <ErrorNote error={failure} />}
      {!text && !error && <Loading what="Loading report" />}
      {text && provider && (
        <Section title="Report" actions={<div className="report-actions">
          <span className="comment-save-status" role="status">{status}</span>
          <ActionIcon danger label="Delete report" onClick={() => dialog.current?.showModal()}><Trash2 size={17} aria-hidden="true" /></ActionIcon>
        </div>}><div className="report-reader-layout">
          <Outline headings={headings} onOpen={show} />
          <article className="native-document report-prose" aria-label={name}>
            <Editor text={text} provider={provider} complete={completions(project)} follow={(href) => void follow(href)} cite={citing(name)} onHeadings={setHeadings} onView={(made) => { view.current = made; }} render={(element, actions, dom) => <ReportElement element={element} actions={actions} dom={dom} project={project} report={name} known={files[element.source]} />} />
          </article>
        </div></Section>
      )}
    </main>
  );
}
