import { useEffect, useMemo, useRef, useState } from 'react';
import { api, post, type Artifact, type ArtifactSummary } from '../api';
import { useRefreshMode } from '../refresh';
import type { Observation } from './model';

export function useRuns(project: string, revision: number, enabled = true) {
  const [state, setState] = useState<{ items: ArtifactSummary[]; loading: boolean; error?: Error }>({
    items: [],
    loading: true,
  });
  const paused = useRefreshMode();
  useEffect(() => {
    if (!enabled) {
      setState({ items: [], loading: false });
      return;
    }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setState((s) => ({ ...s, loading: true, error: undefined }));
    async function load() {
      try {
        const items: ArtifactSummary[] = [];
        let total = Infinity;
        while (items.length < total) {
          const result = await api<{ items: ArtifactSummary[]; total: number }>(
            `/ui-api/artifacts?${new URLSearchParams({ project, category: 'runs', offset: String(items.length), limit: '100' })}`,
            { signal: controller.signal },
          );
          total = result.total;
          items.push(...result.items);
          if (!result.items.length) break;
        }
        if (!controller.signal.aborted) setState({ items, loading: false });
      } catch (e) {
        if (!controller.signal.aborted) setState((s) => ({ ...s, loading: false, error: e as Error }));
      }
      if (!controller.signal.aborted && !paused) timer = setTimeout(() => void load(), 5000);
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [project, revision, paused, enabled]);
  return state;
}
type Loaded = { run: Artifact; rows: Observation[]; cutoff: number; complete: boolean };
type Row = [ordinal: number, sequence: number, writer: string, created: string, data: Observation['data']];
const streams = 4;
const interval = 1000;

/** Reads the records after each run's ordinal, calling back whenever a run is complete. */
async function read(
  project: string,
  after: Record<string, number>,
  signal: AbortSignal,
  found: (run: Artifact, rows: Row[], cutoff: number) => void,
  progress: (count: number) => void,
) {
  const response = await fetch('/ui-api/observations', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project, after }),
    signal,
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null);
    if (response.status === 401) window.dispatchEvent(new Event('cairn:unauthorized'));
    throw new Error(body?.error?.message ?? `Request failed (${response.status})`);
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let run: Artifact | undefined;
  let rows: Row[] = [];
  let done = false;
  let rest: string[] = [];
  const line = (text: string) => {
    const value = JSON.parse(text);
    if (Array.isArray(value)) {
      rows = rows.length ? rows.concat(value) : value;
      progress(value.length);
    } else if (value.artifact) [run, rows] = [value.artifact, []];
    else if (value.done) done = true;
    else found(run!, rows, value.cutoff);
  };
  for (;;) {
    const part = await reader.read();
    if (part.done) break;
    const lines = part.value.split('\n');
    if (lines.length > 1) {
      line(rest.join('') + lines[0]);
      lines.slice(1, -1).forEach(line);
      rest = [];
    }
    rest.push(lines[lines.length - 1]);
  }
  if (!done) throw new Error('The record stream ended early. Refresh to load it again.');
}

/** Complete records of the runs: a run appears once all its records have arrived, stays
 * loaded while its selection changes, and then only its new records are fetched. */
export function useObservations(project: string, runs: ArtifactSummary[], revision: number) {
  const [state, setState] = useState<{
    scope?: string;
    loading: boolean;
    loaded?: number;
    version: number;
    error?: Error;
  }>({
    loading: false,
    version: 0,
  });
  const cache = useRef(new Map<string, Loaded>());
  const origin = useRef('');
  const key = runs
      .map((r) => r.id)
      .sort()
      .join(','),
    scope = `${project}:${revision}:${key}`,
    paused = useRefreshMode();
  useEffect(() => {
    const ids = key ? key.split(',') : [];
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    if (origin.current !== `${project}:${revision}`) {
      origin.current = `${project}:${revision}`;
      cache.current = new Map();
    }
    const publish = (value: Partial<typeof state>) =>
      setState((previous) => ({ loading: false, ...value, scope, version: previous.version + 1 }));
    function add(run: Artifact, added: Row[], cutoff: number) {
      const previous = cache.current.get(run.id);
      const same =
        previous &&
        previous.run.state === run.state &&
        previous.run.number === run.number &&
        JSON.stringify(previous.run.metadata) === JSON.stringify(run.metadata);
      const shown = same ? previous.run : run;
      const kept = previous ? (same ? previous.rows : previous.rows.map((row) => ({ ...row, run: shown }))) : [];
      const rows = added.map(
        ([ordinal, sequence, writer_id, created_at, data]): Observation => ({
          artifact_id: run.id,
          ordinal,
          sequence,
          writer_id,
          kind: 'record',
          data,
          created_at,
          run: shown,
        }),
      );
      cache.current.set(run.id, {
        run: shown,
        rows: rows.length || !same ? kept.concat(rows) : kept,
        cutoff,
        complete: run.state === 'finished',
      });
      return rows.length > 0 || !same;
    }
    async function load() {
      const missing = ids.filter((id) => !cache.current.has(id));
      let loaded = ids.reduce((sum, id) => sum + (cache.current.get(id)?.rows.length || 0), 0);
      let shown = performance.now();
      publish({ loading: !!missing.length, loaded });
      const failed = new AbortController();
      const abort = () => failed.abort();
      controller.signal.addEventListener('abort', abort, { once: true });
      try {
        await Promise.all(
          Array.from({ length: Math.min(streams, missing.length) }, (_, stream) =>
            read(
              project,
              Object.fromEntries(missing.filter((_, i) => i % streams === stream).map((id) => [id, 0])),
              failed.signal,
              (run, rows, cutoff) => void add(run, rows, cutoff),
              (count) => {
                loaded += count;
                if (performance.now() - shown < 400 || controller.signal.aborted) return;
                shown = performance.now();
                publish({ loading: true, loaded });
              },
            ),
          ),
        );
        if (controller.signal.aborted) return;
        publish({});
        schedule();
      } catch (e) {
        failed.abort();
        if (!controller.signal.aborted) publish({ error: e as Error });
      } finally {
        controller.signal.removeEventListener('abort', abort);
      }
    }
    const live = () => ids.filter((id) => !cache.current.get(id)?.complete);
    function schedule(spent = 0) {
      // A slow computer updates less often instead of falling behind.
      if (!paused && live().length) timer = setTimeout(() => void update(), Math.max(interval, spent * 3));
    }
    async function update() {
      let changed = false;
      if (document.visibilityState === 'visible')
        try {
          const after = Object.fromEntries(live().map((id) => [id, cache.current.get(id)!.cutoff]));
          await read(
            project,
            after,
            controller.signal,
            (run, rows, cutoff) => {
              changed = add(run, rows, cutoff) || changed;
            },
            () => {},
          );
        } catch {
          // The next attempt continues from the same position.
        }
      if (controller.signal.aborted) return;
      if (!changed) return schedule();
      const start = performance.now();
      publish({});
      // After the new records are drawn.
      requestAnimationFrame(() =>
        setTimeout(() => {
          if (!controller.signal.aborted) schedule(performance.now() - start);
        }),
      );
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [project, key, scope, revision, paused]);
  const current = state.scope === scope;
  const loaded = useMemo(
    () =>
      current && state.error
        ? []
        : origin.current === `${project}:${revision}` && key
          ? key.split(',').flatMap((id) => cache.current.get(id) || [])
          : [],
    [project, revision, key, current, state.version, state.error],
  );
  const rows = useMemo(() => loaded.flatMap((item) => item.rows), [loaded]);
  return current
    ? { rows, runs: loaded, loading: state.loading, loaded: state.loaded, error: state.error }
    : { rows, runs: loaded, loading: !!key, loaded: undefined, error: undefined };
}

/** Resolve one exact run independently of the project list's filters and version policy. */
export function useRun(project: string, id: string | undefined, revision: number) {
  const identity = `${project}:${id || ''}`;
  const [state, setState] = useState<{ identity: string; run?: Artifact; loading: boolean; error?: Error }>({
    identity,
    loading: !!id,
  });
  const paused = useRefreshMode();
  useEffect(() => {
    if (!id) {
      setState({ identity, loading: false });
      return;
    }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setState({ identity, loading: true });
    async function load() {
      try {
        const [run] = await post<Artifact[]>('/v1/resolutions', { refs: [id], project }, controller.signal);
        if (!run || run.id !== id || run.project !== project || run.namespace !== 'runs')
          throw new Error('Run not found in this project.');
        if (controller.signal.aborted) return;
        setState({ identity, run, loading: false });
        if (run.state !== 'finished' && !paused) timer = setTimeout(() => void load(), 5000);
      } catch (error) {
        if (!controller.signal.aborted) setState({ identity, loading: false, error: error as Error });
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [project, id, identity, revision, paused]);
  return state.identity === identity ? state : { identity, loading: !!id };
}
