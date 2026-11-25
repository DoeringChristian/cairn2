import { createContext, useContext, useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent as Press, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { parse } from 'yaml';
import { artifactPath, blobPath, envelope, last, operate, reference as nameOf, resolve, useLoaded, type Artifact, type BlobRef, type Json } from '../api';
import { ErrorNote, Hint, Loading, ValueView } from '../components';
import { MediaPlayback } from '../components/MediaPlayback';
import { PlotlyFigure } from '../components/PlotlyFigure';
import { useImageBlob } from '../components/useBlobUrl';
import { readView, ViewerFrame } from '../components/ViewerFrame';
import { ZoomImage } from '../components/ZoomImage';
import { useRefresh } from '../refresh';
import { CardContent } from '../workspace/CardContent';
import { isKind, newCard } from '../workspace/cards';
import { useObservations } from '../workspace/data';
import { baseCard, type Card } from '../workspace/model';
import { heightOf, keepHeight } from './heights';
import type { Actions, Element } from './preview';

/** A file a report shows, known with its size before anything is drawn. */
export type Known = BlobRef & { width: number | null; height: number | null };

/** The value of an artifact at a path such as `plot.png` or `figures/loss/image`. */
async function valueAt(artifact: string, path: string, signal: AbortSignal): Promise<Json | undefined> {
  const exact = await last(artifact, [path], signal);
  if (path in exact) return exact[path];
  const [head, ...rest] = path.split('/');
  let value: unknown = (await last(artifact, [head], signal))[head];
  while (rest.length && value && typeof value === 'object') {
    const inside = value as Record<string, unknown>;
    const whole = rest.join('/');
    if (whole in inside) return inside[whole] as Json;
    value = inside[rest.shift()!];
  }
  return rest.length ? undefined : (value as Json | undefined);
}

/** Something of a run, from a reference such as `runs/a@v3#plot.png`. */
function Figure({ source, caption, project, inline, known, report }: { source: string; caption: string; project: string; inline: boolean; known?: Known; report: string }) {
  const origin = { file: `${report}.md`, artifact_ref: source };
  const [ref, path = ''] = source.split(/#(.*)/s);
  const pulse = useRefresh();
  const found = useLoaded(async (signal) => {
    const [artifact] = await resolve([ref], project, signal);
    return { artifact, value: path ? await valueAt(artifact.id, path, signal) : undefined };
  }, `figure:${project}:${source}`, pulse);
  if (path.endsWith('.html')) return <Captioned caption={caption}><ViewerFrame viewer={source} settings={{}} project={project} title={caption || path} /></Captioned>;
  if (found.error && !found.data) return inline ? <span className="md-missing" title={found.error.message}>{source}</span> : <ErrorNote error={found.error} />;
  if (!found.data && known && path && shown(known)) return <Captioned caption={caption}><Media blob={known} alt={caption || path} origin={origin} /></Captioned>;
  if (!found.data) return inline ? <span className="muted">…</span> : <Waiting what={source} />;
  const { artifact, value } = found.data;
  if (!path) return <Link to={artifactPath(artifact.project, artifact.id)}>{caption || nameOf(artifact)}</Link>;
  if (value === undefined) return inline ? <span className="md-missing">{source}</span> : <Hint>{nameOf(artifact)} has no {path}.</Hint>;
  const typed = envelope(value);
  const blob = typed?.blobs[0];
  if (!typed && typeof value !== 'object') return <span className="md-value" title={source}>{typeof value === 'number' ? Number(value.toPrecision(6)).toString() : String(value)}</span>;
  const sized = blob && { ...blob, width: null, height: null, ...(known?.digest === blob.digest ? known : sides(value)) };
  if (inline && !(sized && shown(sized))) return <Link to={artifactPath(artifact.project, artifact.id)}>{caption || source}</Link>;
  return (
    <Captioned caption={caption}>
      {sized && shown(sized) ? <Media blob={sized} alt={caption || path} origin={origin} /> : <ValueView value={value} />}
    </Captioned>
  );
}
type Origin = { file: string; artifact_ref: string };
type Handling = { element: Element; actions: Actions; dom: HTMLElement };
const Handled = createContext<Handling | null>(null);
/** A caption, written where it stands; one without text offers its place to the pointer. */
function Caption({ text }: { text: string }) {
  const { actions, dom } = useContext(Handled)!;
  const host = useRef<HTMLElement>(null);
  const [editing, setEditing] = useState(false);
  const given = useRef(text);
  given.current = text;
  useEffect(() => {
    if (!editing) host.current!.textContent = text || 'Add a caption';
  }, [text, editing]);
  function leave(keep: boolean, dir?: -1 | 0 | 1) {
    const next = host.current!.textContent!.replace(/\s+/g, ' ').trim();
    setEditing(false);
    if (keep && next !== given.current) actions.caption(next);
    else host.current!.textContent = given.current || 'Add a caption';
    if (dir !== undefined) dom.dispatchEvent(new CustomEvent('cairn-caption', { bubbles: true, detail: { el: dom, dir } }));
  }
  function key(event: KeyboardEvent) {
    event.stopPropagation();
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'a') {
      event.preventDefault();
      getSelection()?.selectAllChildren(host.current!);
    } else if (event.key === 'Enter' || event.key === 'Escape' || event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      event.preventDefault();
      const dir = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0;
      const keep = event.key !== 'Escape';
      leaving.current = true;
      leave(keep, dir);
      leaving.current = false;
    }
  }
  const leaving = useRef(false);
  return (
    <figcaption
      ref={host}
      className={`md-caption${text || editing ? '' : ' is-empty'}`}
      contentEditable="plaintext-only"
      suppressContentEditableWarning
      role="textbox"
      aria-label="Caption"
      tabIndex={-1}
      draggable={false}
      onMouseDown={(event) => event.stopPropagation()}
      onFocus={() => {
        setEditing(true);
        if (!given.current) host.current!.textContent = '';
      }}
      onBlur={() => !leaving.current && editing && leave(true)}
      onKeyDown={key}
    />
  );
}
const Captioned = ({ caption = '', children }: { caption?: string; children: ReactNode }) => (
  <figure>
    {children}
    <Caption text={caption} />
  </figure>
);

const snaps: [string, number][] = [['1/4', 25], ['1/3', 100 / 3], ['1/2', 50], ['2/3', 200 / 3], ['3/4', 75], ['full', 100]];
/** Drags the width of a figure (in a row, those beside it give way), or the height of a block. */
function Resize({ vertical = false, height }: { vertical?: boolean; height?: number }) {
  const { dom, actions } = useContext(Handled)!;
  const [label, setLabel] = useState('');
  function press(event: Press<HTMLSpanElement>) {
    if (event.button) return;
    event.preventDefault();
    event.stopPropagation();
    const handle = event.currentTarget;
    const start = { x: event.clientX, y: event.clientY };
    const line = dom.parentElement!.getBoundingClientRect().width;
    const media = dom.querySelector<HTMLElement>('.md-sized, .md-card')!;
    const box = media.getBoundingClientRect();
    const others = vertical ? [] : [...dom.parentElement!.querySelectorAll<HTMLElement>(':scope > .md-embed')].filter((el) => el !== dom);
    const shares = others.map((el) => el.getBoundingClientRect().width);
    const before = [dom, ...others].map((el) => [el, el.style.getPropertyValue('--share'), el.classList.contains('is-sized')] as const);
    const total = shares.reduce((sum, share) => sum + share, 0);
    let size: { width?: number; others?: { el: HTMLElement; width: number }[]; height?: number } | undefined;
    const set = (el: HTMLElement, share: number) => {
      el.style.setProperty('--share', String(share));
      el.classList.add('is-sized');
    };
    const move = (e: globalThis.PointerEvent) => {
      if (vertical) {
        const tall = Math.round(Math.max(160, Math.min(1200, (height ?? box.height) + e.clientY - start.y)) / 10) * 10;
        media.style.height = `${tall}px`;
        size = { height: tall };
        setLabel(`${tall} px`);
        return;
      }
      // A figure alone stands in the middle, so both its edges move.
      const grown = (e.clientX - start.x) * (others.length ? 1 : 2);
      const free = Math.max(8, Math.min(100 - 8 * others.length, ((box.width + grown) / line) * 100));
      const snap = e.altKey ? undefined : snaps.find(([, share]) => Math.abs(share - free) < 2);
      const width = Math.round(snap?.[1] ?? free);
      set(dom, width);
      const given = others.map((el, i) => ({ el, width: Math.max(8, Math.round(((100 - width) * shares[i]) / total)) }));
      given.forEach((one) => set(one.el, one.width));
      size = { width, others: given };
      setLabel(`${snap ? `${snap[0]} · ` : ''}${width}%`);
    };
    const end = (e: globalThis.PointerEvent) => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', end);
      handle.removeEventListener('pointercancel', end);
      dom.classList.remove('is-resizing');
      setLabel('');
      if (size && e.type === 'pointerup') return actions.size(size);
      for (const [el, share, sized] of before) {
        if (share) el.style.setProperty('--share', share);
        else el.style.removeProperty('--share');
        el.classList.toggle('is-sized', sized);
      }
      if (vertical) media.style.height = `${height}px`;
    };
    handle.setPointerCapture(event.pointerId);
    dom.classList.add('is-resizing');
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
  }
  return (
    <span className="md-resize" title="Drag to resize" draggable={false} onPointerDown={press} onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}>
      {label && <span className="md-resize-label">{label}</span>}
    </span>
  );
}

const plotly = 'application/vnd.plotly.v1+json';
const shown = (blob: BlobRef) => /^(image|video|audio)\//.test(blob.media_type || '') || blob.media_type === plotly;
const sides = (value: Json) => {
  const { width, height } = value as { width?: Json; height?: Json };
  return typeof width === 'number' && typeof height === 'number' ? { width, height } : {};
};
/** Media in the space its size asks for; a video whose size is not known gets 16:9. */
function Media({ blob, alt, origin }: { blob: Known; alt: string; origin: Origin }) {
  const media = blob.media_type || '';
  if (media === plotly) return <PlotlyFigure src={blobPath(blob)} />;
  if (media.startsWith('audio/')) return <div className="md-audio"><MediaPlayback src={blobPath(blob)} mime={media} label={alt} source={origin} /></div>;
  const video = media.startsWith('video/');
  const known = blob.width && blob.height;
  const style = { '--aspect': known ? `${blob.width} / ${blob.height}` : '16 / 9', '--natural': `${video ? 720 : blob.width}px` } as CSSProperties;
  return (
    <div className={known || video ? 'md-sized' : 'md-sized md-unsized'} style={known || video ? style : undefined}>
      {video ? <MediaPlayback src={blobPath(blob)} mime={media} label={alt} source={origin} /> : <Picture blob={blob} alt={alt} origin={origin} />}
      <Resize />
    </div>
  );
}
function Picture({ blob, alt, origin }: { blob: BlobRef; alt: string; origin: Origin }) {
  const { url, error } = useImageBlob(blob);
  if (error) return <ErrorNote error={error} />;
  return url ? <ZoomImage src={url} alt={alt} source={origin} /> : <div className="md-blank" role="img" aria-label={`Loading ${alt}`} />;
}
const Waiting = ({ what }: { what: string }) => <div className="md-wait"><Loading what={`Loading ${what}`} /></div>;
/** Keeps the height something had while it is loaded again. */
function Steady({ name, children }: { name: string; children: React.ReactNode }) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = host.current!;
    const watch = new ResizeObserver(() => {
      if (!node.querySelector('.md-wait')) keepHeight(name, node.offsetHeight);
    });
    watch.observe(node);
    return () => watch.disconnect();
  }, [name]);
  return <div ref={host} className="md-steady" style={{ '--height': `${heightOf(name) ?? 320}px` } as CSSProperties}>{children}</div>;
}

/** A viewer from an artifact, with its settings, as YAML. */
function View({ source, project }: { source: string; project: string }) {
  let view;
  try {
    view = readView(source);
  } catch (error) {
    return <ErrorNote error={error as Error} />;
  }
  return <Captioned caption={view.caption}><ViewerFrame viewer={view.viewer} settings={view.settings} project={project} title={view.caption || view.viewer} /></Captioned>;
}

type Spec = { runs: string[]; card: Card; caption: string };
/** A workspace card over runs, as YAML: `kind`, `runs` by reference or name with *, and the card's settings. */
function readCard(source: string): Spec {
  const value = parse(source);
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('A card is YAML, e.g. runs: runs/sweep/*');
  const { runs, caption, kind = 'scalar', ...rest } = value as Record<string, unknown>;
  if (!isKind(kind) || kind === 'viewer') throw new Error('A card’s kind is scalar, image, video or plotly');
  const list = typeof runs === 'string' ? [runs] : runs;
  if (!Array.isArray(list) || !list.length || list.some((run) => typeof run !== 'string')) throw new Error('A card needs runs, e.g. runs: [runs/a@v1, runs/sweep/*]');
  const card = newCard(kind, '');
  for (const [key, given] of Object.entries(rest)) {
    const known = (baseCard(kind, '') as unknown as Record<string, unknown>)[key];
    if (known === undefined || ['id', 'section', 'kind'].includes(key)) throw new Error(`A card has no setting ${key}`);
    if (typeof given !== typeof known || Array.isArray(given) !== Array.isArray(known)) throw new Error(`${key} is not what a card expects`);
  }
  return { runs: list as string[], caption: typeof caption === 'string' ? caption : '', card: { ...card, ...(rest as Partial<Card>), id: 'report' } };
}
function CardBlock({ source, project }: { source: string; project: string }) {
  const spec = useMemo(() => {
    try {
      return readCard(source);
    } catch (error) {
      return error as Error;
    }
  }, [source]);
  return spec instanceof Error ? <ErrorNote error={spec} /> : <Runs spec={spec} project={project} />;
}
function Runs({ spec, project }: { spec: Spec; project: string }) {
  const pulse = useRefresh();
  const key = JSON.stringify(spec.runs);
  const runs = useLoaded(async (signal) => {
    const exact = spec.runs.filter((ref) => !/[*?]/.test(ref)).map((ref) => ref.replace(/^cairn:/, ''));
    const found = exact.length ? await resolve(exact, project, signal) : [];
    for (const glob of spec.runs.filter((ref) => /[*?]/.test(ref))) {
      const [namespace, ...name] = glob.replace(/^cairn:/, '').split('/');
      found.push(...(await operate<{ items: Artifact[] }>('find', { project, namespaces: [namespace], name: name.join('/'), latest: true, limit: 1000 }, signal)).items);
    }
    return [...new Map(found.map((run) => [run.id, run])).values()];
  }, `runs:${project}:${key}`, pulse);
  const list = useMemo(() => runs.data || [], [runs.data?.map((run) => run.id).join()]);
  const data = useObservations(project, list, 0);
  if (runs.error || data.error) return <ErrorNote error={(runs.error || data.error)!} />;
  return (
    <Captioned caption={spec.caption}>
      <div className="workspace-card md-card" style={{ height: spec.card.height }}>
        {!runs.data || (data.loading && !data.rows.length) ? <Loading what="Loading runs" /> : <CardContent card={spec.card} rows={data.rows} />}
        <Resize vertical height={spec.card.height} />
      </div>
    </Captioned>
  );
}

export function ReportElement({ element, actions, dom, project, report, known }: Handling & { project: string; report: string; known?: Known }) {
  const inline = element.place === 'text';
  const figure = <Figure source={element.source} caption={element.caption} project={project} inline={inline} known={known} report={report} />;
  return (
    <Handled.Provider value={{ element, actions, dom }}>
      {element.kind === 'view' ? <View source={element.source} project={project} /> : element.kind === 'card' ? <CardBlock source={element.source} project={project} /> : inline ? figure : <Steady name={element.source}>{figure}</Steady>}
    </Handled.Provider>
  );
}
