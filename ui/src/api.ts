import { readView, writeView } from './viewCache';
import { useEffect, useState } from 'react';
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type BlobRef = { digest: string; size: number; name?: string; media_type?: string };
export type Envelope = { $cairn: number; format: string; version: number; data: Json; blobs: BlobRef[]; refs: {artifact_id: string; role: string}[] };
export type Manifest = { metadata: Record<string, Json>; value: Json; captures: Record<string, Json>; dependencies: {artifact_id: string; role: string}[]; blobs: BlobRef[]; outcome: string };
export type Artifact = {
  id: string; entity: 'artifact'; project: string; namespace: string; name: string;
  state: 'live' | 'closing' | 'finished'; number: number | null; cutoff: number;
  metadata: Record<string, Json>; annotations: Record<string, Json>; annotations_etag: string; manifest: Manifest | null;
  created_at: string; finished_at: string | null; deleted_at: string | null;
};
export type ArtifactSummary = Pick<Artifact, 'id' | 'project' | 'namespace' | 'name' | 'state' | 'number' | 'created_at' | 'finished_at' | 'annotations' | 'annotations_etag'> & {title?: string; description?: string; outcome?: string};
export type Descriptor = Artifact;
export type RecordEntry = { artifact_id: string; ordinal: number; sequence: number; writer_id: string; kind: string; data: Record<string, Json>; created_at: string };
export type RecordPage = { items: RecordEntry[]; next_cursor: string | null; cutoff: number; artifact_id: string };
export type Project = { name: string; artifacts: number; runs: number; reports: number; updated: string };
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); } }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...options, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...options.headers } });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    if (response.status === 401 && path !== '/ui-api/login') window.dispatchEvent(new Event('cairn:unauthorized'));
    throw new ApiError(body?.error?.message ?? `Request failed (${response.status})`, response.status);
  }
  return response.json();
}
export const post = <T,>(path: string, body: unknown, signal?: AbortSignal) => api<T>(path, {method: 'POST', body: JSON.stringify(body), signal});
export function useResource<T>(path: string | null, revision = 0, cache = false) {
  const [state, setState] = useState<{data?: T; error?: Error; loading: boolean}>(() => ({data:cache && path ? readView<T>(path) : undefined, loading:!(cache && path && readView<T>(path))}));
  useEffect(() => {
    const controller = new AbortController();
    setState(previous => ({...previous, loading: !!path && !previous.data}));
    if (path) api<T>(path, {signal: controller.signal}).then(data => {if(controller.signal.aborted)return;if(cache){const previous=readView<T>(path);if(previous!==undefined&&JSON.stringify(previous)===JSON.stringify(data))data=previous;else writeView(path,data);}setState(previous=>{const stable=previous.data===data||(cache&&previous.data!==undefined&&JSON.stringify(previous.data)===JSON.stringify(data))?previous.data:data;return previous.data===stable&&!previous.loading&&!previous.error?previous:{data:stable, loading:false};});}).catch(error => {
      if (!controller.signal.aborted) setState(previous=>({...previous,error, loading: false}));
    });
    return () => controller.abort();
  }, [path, revision, cache]);
  return state;
}
export const projectPath = (project: string) => `/p/${encodeURIComponent(project)}`;
export const artifactPath = (project: string, id: string) => `${projectPath(project)}/a/${encodeURIComponent(id)}`;

export const when = (value: string) => new Date(value).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'});
export const bytes = (n: number) => n < 1024 ? `${n} B` : n < 1024 ** 2 ? `${(n/1024).toFixed(1)} KiB` : `${(n/1024**2).toFixed(1)} MiB`;
export function envelope(value: Json | undefined): Envelope | null {
  return value && typeof value === 'object' && !Array.isArray(value) && value.$cairn === 1 && typeof value.format === 'string' ? value as unknown as Envelope : null;
}
