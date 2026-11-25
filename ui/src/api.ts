import { useEffect, useState } from 'react';
import { Bytes, Content, Ref, decode, decodeRecord, encode, type Json } from './protocol';
export type { Json };
export type BlobRef = { digest: string; size: number; name?: string; media_type?: string; href?: string };
/** Content in a value: a file, or a typed map such as an image with its content. */
export type Envelope = { format: string; data: Record<string, Json>; blobs: BlobRef[] };
/** Times are Unix seconds. */
export type Artifact = {
  id: string; project: string; namespace: string; name: string; finished: boolean; completed: boolean; version: number; records: number;
  created_at: number; finished_at: number; heard_at: number; tags: Record<string, Json>; aliases: string[]; values: Record<string, Json>;
};
export type Run = Artifact & { config?: Json };
export type Project = { name: string; artifacts: Record<string, number>; updated: number; reports: number };
export type RecordEntry = { ordinal: number; data: Record<string, Json> };
export type FoundFile = { artifact: string; path: string; blob: Content; ordinal: number; reference: string };
export type Link = { path: string; artifact: Artifact };
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); } }
async function refused(response: Response): Promise<never> {
  const body = await response.json().catch(() => null);
  if (response.status === 401 && !response.url.endsWith('/ui-api/login')) window.dispatchEvent(new Event('cairn:unauthorized'));
  throw new ApiError(body?.error?.message ?? `Request failed (${response.status})`, response.status);
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...options, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...options.headers } });
  if (!response.ok) return refused(response);
  return response.json();
}
export const post = <T,>(path: string, body: unknown, signal?: AbortSignal) => api<T>(path, {method: 'POST', body: JSON.stringify(body), signal});
const operations = {
  resolve: ['Resolve', 'Artifacts'], projects: ['Projects', 'ProjectList'], records: ['Records', 'RecordPage'], keys: ['Keys', 'KeyList'], last: ['Last', 'LastValues'],
  find: ['Find', 'Artifacts'], files: ['Files', 'FoundFiles'], links: ['Links', 'LinkList'], label: ['Label', 'Artifact'], delete: ['Delete', 'Artifact'],
} as const;
/** One of Cairn's operations. */
export async function operate<T>(name: keyof typeof operations, request: object, signal?: AbortSignal): Promise<T> {
  const [asked, answered] = operations[name];
  const response = await fetch(`/ui-api/do/${name}`, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/x-protobuf'}, body: encode(asked, request), signal});
  if (!response.ok) return refused(response);
  return decode<T>(answered, new Uint8Array(await response.arrayBuffer()));
}
export const resolve = async (refs: string[], project: string, signal?: AbortSignal) => (await operate<{items: Artifact[]}>('resolve', {project, refs}, signal)).items;
/** What an artifact is, with how it ended. */
export async function getArtifact(ref: string, project: string, signal?: AbortSignal) {
  const [found] = await resolve([ref], project, signal);
  return {...found, values: await last(found.id, ['_outcome'], signal)};
}
export async function last(artifact: string, keys: string[], signal?: AbortSignal): Promise<Record<string, Json>> {
  const {entries} = await operate<{entries: {key: string; value: Json}[]}>('last', {artifact, keys}, signal);
  return Object.fromEntries(entries.map(entry => [entry.key, entry.value]));
}
export async function current(artifact: string, signal?: AbortSignal) {
  const {keys} = await operate<{keys: string[]}>('keys', {artifact}, signal);
  return last(artifact, keys.filter(key => key !== '_console'), signal);
}
export async function records(request: {artifact: string; after?: number; limit?: number; having?: string}, signal?: AbortSignal) {
  const page = await operate<{ordinals: number[]; records: Uint8Array[]; total: number; more: boolean}>('records', request, signal);
  return {items: page.ordinals.map((ordinal, i): RecordEntry => ({ordinal, data: decodeRecord(page.records[i])})), total: page.total, more: page.more};
}
/** Conditions such as `loss < 0.1`, or a path that has any value, e.g. tag:paper. */
export function condition(text: string) {
  const match = /^(\S.*?)\s+(!=|<=|>=|=|<|>|contains)\s+(.+)$/s.exec(text.trim());
  const path = match ? match[1] : text.trim(), stat = /^(min|max)\((.+)\)$/.exec(path);
  if (!match && (!path || /[<>=\s]/.test(path))) throw new Error('Conditions read like “loss < 0.1”, with spaces, or name a field such as tag:paper.');
  return {path: stat ? stat[2] : path, stat: stat ? stat[1] : '', op: match ? match[2] : '', value: match ? literal(match[3].trim()) : null};
}
export function literal(text: string): Json {
  if (text.length > 1 && text[0] === text.at(-1) && `'"`.includes(text[0])) return text.slice(1, -1);
  if (text.toLowerCase() === 'nan') return NaN;
  if (/^-?inf$/i.test(text)) return text[0] === '-' ? -Infinity : Infinity;
  try { const value = JSON.parse(text); return typeof value === 'number' || typeof value === 'boolean' ? value : text; } catch { return text; }
}
export function useLoaded<T>(load: ((signal: AbortSignal) => Promise<T>) | null, key: string, revision = 0) {
  const [state, setState] = useState<{key: string; data?: T; error?: Error; loading: boolean}>({key, loading: !!load});
  useEffect(() => {
    if (!load) { setState({key, loading: false}); return; }
    const controller = new AbortController();
    setState(previous => previous.key === key ? {...previous, loading: !previous.data} : {key, loading: true});
    load(controller.signal).then(
      data => { if (!controller.signal.aborted) setState(previous => ({key, data: print(previous.data) === print(data) ? previous.data : data, loading: false})); },
      error => { if (!controller.signal.aborted) setState(previous => ({...previous, error, loading: false})); });
    return () => controller.abort();
  }, [key, revision]);
  return state.key === key ? state : {key, loading: !!load, data: undefined, error: undefined};
}
export const useProjects = (enabled: boolean, revision: number) =>
  useLoaded(enabled ? async signal => {
    const [{items}, reports] = await Promise.all([operate<{items: Project[]}>('projects', {}, signal), api<Record<string, number>>('/ui-api/report-counts', {signal})]);
    return items.map(item => ({...item, reports: reports[item.name] || 0}));
  } : null, `projects:${enabled}`, revision);
export const runsOf = (project: Project) => project.artifacts.runs || 0;
export const artifactsOf = (project: Project) => Object.values(project.artifacts).reduce((sum, count) => sum + count, 0);
export const projectPath = (project: string) => `/p/${encodeURIComponent(project)}`;
export const runWorkspacePath = (project:string,id:string) => `${artifactPath(project,id)}?tab=workspace`;
export const artifactPath = (project: string, id: string) => `${projectPath(project)}/a/${encodeURIComponent(id)}`;
export const reportPath = (project: string, name: string) => `${projectPath(project)}/r/${name.split('/').map(encodeURIComponent).join('/')}`;
export const filePath = (id: string, path: string) => `/ui-api/files/${encodeURIComponent(id)}/${path.split('/').map(encodeURIComponent).join('/')}`;
export const blobPath = (blob: BlobRef) => `/ui-api/content/${blob.digest}${blob.media_type ? `?type=${encodeURIComponent(blob.media_type)}` : ''}`;
export const reference = (artifact: Artifact) => `${artifact.namespace}/${artifact.name}${artifact.version ? `@v${artifact.version}` : ''}`;
/** A live artifact is heard from every 10 seconds; this computer's clock may differ from the server's. */
let clock = 0;
export const setClock = (now: number) => { clock = now - Date.now() / 1000; };
export const isLost = (artifact: Artifact) => !artifact.finished && Date.now() / 1000 + clock - artifact.heard_at > 60;
export const status = (artifact: Artifact) => artifact.finished ? typeof artifact.values._outcome === 'string' ? artifact.values._outcome : 'finished' : isLost(artifact) ? 'lost contact' : 'live';

export const when = (seconds: number) => new Date(seconds * 1000).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'});
export const bytes = (n: number) => n < 1024 ? `${n} B` : n < 1024 ** 2 ? `${(n/1024).toFixed(1)} KiB` : n < 1024 ** 3 ? `${(n/1024**2).toFixed(1)} MiB` : `${(n/1024**3).toFixed(1)} GiB`;
/** A value as text, also where JSON has no word for it. */
export const print = (value: unknown, space?: number) => JSON.stringify(value, (_key, item) =>
  typeof item === 'number' && !Number.isFinite(item) ? String(item)
    : item instanceof Content ? `file · ${item.media_type} · ${bytes(item.size)} · ${item.digest.slice(0, 12)}…`
    : item instanceof Ref ? `artifact ${item.artifact}` : item, space) ?? 'undefined';
export function envelope(given: Json | undefined): Envelope | null {
  const value: unknown = given;
  if (value instanceof Content) return {format: 'file', data: {media_type: value.media_type}, blobs: [value]};
  if (!value || typeof value !== 'object' || Array.isArray(value) || value instanceof Ref || value instanceof Bytes) return null;
  const typed = value as Record<string, unknown>, content = typed.data instanceof Content ? typed.data : undefined;
  if (typeof typed._type !== 'string') return null;
  const format = typed._type !== 'figure' ? typed._type : content?.media_type === 'image/svg+xml' ? 'figure/svg' : 'figure/plotly';
  return {format, data: typed as Record<string, Json>, blobs: content ? [content] : []};
}
