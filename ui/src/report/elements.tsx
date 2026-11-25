import { useEffect, useMemo, useRef, type CSSProperties } from 'react';
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
import type { Element } from './preview';

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
function Figure({ source, caption, project, inline, known }: { source: string; caption: string; project: string; inline: boolean; known?: Known }) {
  const [ref, path = ''] = source.split(/#(.*)/s);
  const pulse = useRefresh();
  const found = useLoaded(async (signal) => {
    const [artifact] = await resolve([ref], project, signal);
    return { artifact, value: path ? await valueAt(artifact.id, path, signal) : undefined };
  }, `figure:${project}:${source}`, pulse);
  if (path.endsWith('.html')) return <Captioned caption={caption}><ViewerFrame viewer={source} settings={{}} project={project} title={caption || path} /></Captioned>;
  if (found.error && !found.data) return inline ? <span className="md-missing" title={found.error.message}>{source}</span> : <ErrorNote error={found.error} />;
  if (!found.data && known && path && shown(known)) return <Captioned caption={caption} inline={inline}><Media blob={known} alt={caption || path} /></Captioned>;
  if (!found.data) return inline ? <span className="muted">…</span> : <Waiting what={source} />;
  const { artifact, value } = found.data;
  if (!path) return <Link to={artifactPath(artifact.project, artifact.id)}>{caption || nameOf(artifact)}</Link>;
  if (value === undefined) return inline ? <span className="md-missing">{source}</span> : <Hint>{nameOf(artifact)} has no {path}.</Hint>;
  const typed = envelope(value);
  const blob = typed?.blobs[0];
  const media = blob?.media_type || '';
  if (!typed && typeof value !== 'object') return <span className="md-value" title={source}>{typeof value === 'number' ? Number(value.toPrecision(6)).toString() : String(value)}</span>;
  const sized = blob && { ...blob, width: null, height: null, ...(known?.digest === blob.digest ? known : sides(value)) };
  if (inline) return sized && media.startsWith('image/') ? <Media blob={sized} alt={caption || path} /> : <Link to={artifactPath(artifact.project, artifact.id)}>{caption || source}</Link>;
  return (
    <Captioned caption={caption}>
      {sized && shown(sized) ? <Media blob={sized} alt={caption || path} /> : <ValueView value={value} />}
    </Captioned>
  );
}
const Captioned = ({ caption, inline, children }: { caption?: string; inline?: boolean; children: React.ReactNode }) =>
  inline ? <>{children}</> : (
    <figure>
      {children}
      {caption && <figcaption>{caption}</figcaption>}
    </figure>
  );
const plotly = 'application/vnd.plotly.v1+json';
const shown = (blob: BlobRef) => /^(image|video|audio)\//.test(blob.media_type || '') || blob.media_type === plotly;
const sides = (value: Json) => {
  const { width, height } = value as { width?: Json; height?: Json };
  return typeof width === 'number' && typeof height === 'number' ? { width, height } : {};
};
/** Media in the space its size asks for; a video whose size is not known gets 16:9. */
function Media({ blob, alt }: { blob: Known; alt: string }) {
  const media = blob.media_type || '';
  if (media === plotly) return <PlotlyFigure src={blobPath(blob)} />;
  const [width, height] = [blob.width, blob.height];
  if (media.startsWith('audio/')) return <div className="md-audio"><MediaPlayback src={blobPath(blob)} mime={media} label={alt} /></div>;
  if (media.startsWith('video/'))
    return <div className="md-sized" style={{ '--aspect': width && height ? `${width} / ${height}` : '16 / 9', width: 720 } as CSSProperties}><MediaPlayback src={blobPath(blob)} mime={media} label={alt} /></div>;
  return <div className={width && height ? 'md-sized' : 'md-unsized'} style={width && height ? ({ '--aspect': `${width} / ${height}`, width } as CSSProperties) : undefined}><Picture blob={blob} alt={alt} /></div>;
}
function Picture({ blob, alt }: { blob: BlobRef; alt: string }) {
  const { url, error } = useImageBlob(blob);
  if (error) return <ErrorNote error={error} />;
  return url ? <ZoomImage src={url} alt={alt} /> : <div className="md-blank" role="img" aria-label={`Loading ${alt}`} />;
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
    <Captioned caption={spec.caption || spec.card.title}>
      <div className="workspace-card md-card" style={{ height: spec.card.height }}>
        {!runs.data || (data.loading && !data.rows.length) ? <Loading what="Loading runs" /> : <CardContent card={spec.card} rows={data.rows} />}
      </div>
    </Captioned>
  );
}

export function ReportElement({ element, project, known }: { element: Element; project: string; known?: Known }) {
  if (element.kind === 'view') return <View source={element.source} project={project} />;
  if (element.kind === 'card') return <CardBlock source={element.source} project={project} />;
  const figure = <Figure source={element.source} caption={element.caption} project={project} inline={element.inline} known={known} />;
  return element.inline ? figure : <Steady name={element.source}>{figure}</Steady>;
}
