import { useEffect, useRef, useState } from 'react';
import { parse } from 'yaml';
import { api, resolve as find, type Json } from '../api';
import { heightOf, keepHeight } from '../report/heights';
import { ErrorNote, Loading } from './States';

export type View = { viewer: string; caption?: string; height?: number; settings: Record<string, Json> };
/** A view block: YAML whose viewer names the HTML file, caption the figure's caption,
 * and every other key is the viewer's own setting; `width: 50%` and `height: 300px` also size it. */
export function readView(text: string): View {
  const value = parse(text);
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('A view is a YAML mapping with a viewer, e.g. viewer: viewers/mesh@latest#index.html');
  const { viewer, caption, ...settings } = value as Record<string, Json>;
  if (typeof viewer !== 'string' || !viewer.includes('#')) throw new Error('A view needs viewer: REF#file.html');
  return {
    viewer: viewer.replace(/^cairn:/, ''),
    caption: typeof caption === 'string' ? caption : undefined,
    height: Number(/^(\d+)px$/.exec(String(settings.height))?.[1]) || undefined,
    settings,
  };
}
/** An artifact's HTML file run as a viewer with an opaque origin. Its settings arrive as
 * JSON in the URL fragment, each cairn:REF#file string replaced by a link to that file. */
export function ViewerFrame({
  viewer,
  settings,
  project,
  title,
}: {
  viewer: string;
  settings: Record<string, Json>;
  project: string;
  title: string;
}) {
  const frame = useRef<HTMLIFrameElement>(null),
    [src, setSrc] = useState<string>(),
    [error, setError] = useState<Error>(),
    [height, setHeight] = useState(() => heightOf(`viewer:${viewer}`) ?? 480);
  const { width: _width, height: _height, ...own } = settings;
  const size = (value: Json | undefined) => (/^[\d.]+(%|px)$/.test(String(value)) ? null : (value ?? null));
  const identity = JSON.stringify([viewer, own, size(_width), size(_height), project]);
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.type !== 'cairn-html-size') return;
      const value = Number(event.data.height);
      if (!Number.isFinite(value)) return;
      const next = Math.max(80, Math.min(4000, value));
      keepHeight(`viewer:${viewer}`, next);
      setHeight(next);
    };
    window.addEventListener('message', receive);
    return () => window.removeEventListener('message', receive);
  }, [viewer]);
  useEffect(() => {
    const controller = new AbortController(),
      { signal } = controller;
    setSrc(undefined);
    setError(undefined);
    const link = async (ref: string) => {
      const at = ref.indexOf('#'),
        name = at < 0 ? '' : ref.slice(at + 1),
        [artifact] = await find([at < 0 ? ref : ref.slice(0, at)], project, signal);
      if (!artifact) throw new Error(`Nothing matches ${ref}`);
      const { url } = await api<{ url: string }>(`/ui-api/viewer-tokens/${encodeURIComponent(artifact.id)}`, {
        signal,
      });
      return location.origin + url + name.split('/').map(encodeURIComponent).join('/');
    };
    const resolve = async (value: Json): Promise<Json> =>
      typeof value === 'string'
        ? value.startsWith('cairn:')
          ? link(value.slice(6))
          : value
        : Array.isArray(value)
          ? Promise.all(value.map(resolve))
          : value && typeof value === 'object'
            ? Object.fromEntries(
                await Promise.all(Object.entries(value).map(async ([key, item]) => [key, await resolve(item)])),
              )
            : value;
    Promise.all([link(viewer), resolve(settings)])
      .then(([url, resolved]) => {
        if (!signal.aborted) setSrc(`${url}#${encodeURIComponent(JSON.stringify(resolved))}`);
      })
      .catch((e) => {
        if (!signal.aborted) setError(e as Error);
      });
    return () => controller.abort();
  }, [identity]);
  if (error) return <ErrorNote error={error} />;
  if (!src) return <div style={{ height }}><Loading what={`Loading ${title}`} /></div>;
  return (
    <iframe
      ref={frame}
      className="native-html-viewer"
      src={src}
      sandbox="allow-scripts"
      title={title}
      style={{ height }}
    />
  );
}
