import { useEffect, useState } from 'react';
import { artifactBlobs, envelope, post, type Artifact } from '../api';
import { ErrorNote, Hint, Loading, ValueView } from '../components';
import { nodesToReact, type Render } from '../components/Markdown';
import { MediaPlayback } from '../components/MediaPlayback';
import { OutputComment } from '../components/OutputComment';
import { useImageBlob } from '../components/useBlobUrl';
import { readView, ViewerFrame } from '../components/ViewerFrame';
import { ZoomImage } from '../components/ZoomImage';
import { PlotSurface, type FigureSpec } from '../workspace/PlotSurface';
import { parse } from './markdown';

const mediaTypes: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  svg: 'image/svg+xml',
  mp4: 'video/mp4',
  webm: 'video/webm',
  wav: 'audio/wav',
  mp3: 'audio/mpeg',
};

type Source = { file: string; line: number; media_id: string };

/** An artifact's value, or with #file one of its captures, from a cairn:REF reference; an HTML file runs as a viewer. */
export function ArtifactMedia({
  reference,
  alt,
  project,
  part,
}: {
  reference: string;
  alt: string;
  project: string;
  part: string;
}) {
  const at = reference.indexOf('#');
  const ref = at < 0 ? reference : reference.slice(0, at);
  const file = at < 0 ? undefined : reference.slice(at + 1);
  const [artifact, setArtifact] = useState<Artifact>();
  const [error, setError] = useState<Error>();
  useEffect(() => {
    const controller = new AbortController();
    setError(undefined);
    post<Artifact[]>('/v1/resolutions', { refs: [ref], project }, controller.signal)
      .then((found) => setArtifact(found[0]))
      .catch((e) => {
        if (!controller.signal.aborted) setError(e);
      });
    return () => controller.abort();
  }, [ref, project]);

  const source: Source = { file: part, line: 1, media_id: reference };
  if (file?.endsWith('.html'))
    return (
      <OutputComment source={source} label={alt || 'Viewer'}>
        <figure>
          <ViewerFrame viewer={reference} settings={{}} project={project} title={alt || 'Viewer'} />
          {alt && <figcaption>{alt}</figcaption>}
        </figure>
      </OutputComment>
    );
  if (error) return <ErrorNote error={error} />;
  if (!artifact) return <Loading what={`Loading ${ref}`} />;

  const value = file ? artifact.manifest?.captures[file] : artifact.manifest?.value;
  const typed = envelope(value);
  const blob = ['image', 'figure/svg', 'file'].includes(typed?.format || '')
    ? typed?.blobs?.[0]
    : file && value === undefined
      ? artifactBlobs(artifact).find((b) => b.name === file)
      : undefined;
  if (file && value === undefined && !blob)
    return (
      <Hint>
        {ref} has no file {file}.
      </Hint>
    );
  if (typed?.format === 'figure/plotly')
    return (
      <OutputComment source={source} label={alt || 'Figure'}>
        <figure className="report-plot">
          <PlotSurface spec={typed.data as unknown as FigureSpec} />
          {alt && <figcaption>{alt}</figcaption>}
        </figure>
      </OutputComment>
    );
  if (!blob) return <ValueView value={value} />;
  const extension = (file || blob.name || '').split('.').pop()?.toLowerCase() || '';
  return (
    <BlobMedia blob={{ ...blob, media_type: blob.media_type || mediaTypes[extension] }} alt={alt} source={source} />
  );
}

function BlobMedia({ blob, alt, source }: { blob: Parameters<typeof useImageBlob>[0]; alt: string; source: Source }) {
  const { url, mime, error } = useImageBlob(blob);
  if (error) return <ErrorNote error={error} />;
  if (!url) return <Loading what={`Loading ${alt || blob.name}`} />;
  if (/^(video|audio)\//.test(mime))
    return <MediaPlayback src={url} mime={mime} label={alt || blob.name || 'Video'} source={source} />;
  return (
    <figure>
      <ZoomImage src={url} alt={alt || blob.name || 'Figure'} source={source} />
      {alt && <figcaption>{alt}</figcaption>}
    </figure>
  );
}

/** A view block: a viewer and its settings, commentable like other figures. */
export function ViewFigure({ text, project, part, id }: { text: string; project: string; part: string; id: string }) {
  let view;
  try {
    view = readView(text);
  } catch (e) {
    return <ErrorNote error={e as Error} />;
  }
  const label = view.caption || view.viewer;
  return (
    <OutputComment source={{ file: part, line: 1, media_id: `view:${id}` }} label={label}>
      <figure>
        <ViewerFrame viewer={view.viewer} settings={view.settings} project={project} title={label} />
        {view.caption && <figcaption>{view.caption}</figcaption>}
      </figure>
    </OutputComment>
  );
}

/** An agent's Markdown answer, with its cairn: figures and view blocks. */
export function Answer({ source, part, project }: { source: string; part: string; project: string }) {
  const render: Render = {
    fence: (info, content, key, at) =>
      info === 'view' ? (
        <ViewFigure key={key} text={content} project={project} part={part} id={`${part}-${at?.line_start || 1}`} />
      ) : undefined,
    image: (src, alt, _attrs, key) =>
      src.startsWith('cairn:') ? (
        <ArtifactMedia key={key} reference={src.slice(6)} alt={alt} project={project} part={part} />
      ) : (
        <span key={key}>[Only cairn: references can be shown: {alt}]</span>
      ),
  };
  return <>{nodesToReact(parse(source, part), render)}</>;
}
