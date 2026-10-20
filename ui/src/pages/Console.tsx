import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useRefresh } from '../refresh';
import { envelope, post, type RecordPage } from '../api';
import { ErrorNote, Hint, Loading, Section } from '../components';

const shown = 1024 * 1024;
/** What a terminal would show: colours dropped, a line rewritten by carriage returns (a progress bar) kept once. */
const screen = (text: string) => text.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '').split('\n').map(line => line.replace(/\r+$/, '').split('\r').at(-1)).join('\n');

/** A run's stdout and stderr, stored as console/NNNNNN.log captures; live runs append new chunks. */
export function ConsolePanel({reference, project, live}: {reference: string; project: string; live: boolean}) {
  const pulse = useRefresh(live), last = useRef('console/'), block = useRef<HTMLPreElement>(null), stick = useRef(true);
  const [text, setText] = useState(''), [error, setError] = useState<Error>(), [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController(), decoder = new TextDecoder();
    (async () => {
      let cursor: string | null = null, name = last.current, added = '';
      do {
        const page: RecordPage = await post<RecordPage>('/v1/records/query', {ref: reference, project, kind: 'capture', where: {and: [{path: '/name', op: 'gt', value: name}, {path: '/name', op: 'lt', value: 'console0'}]}, limit: 1000, cursor}, controller.signal);
        for (const item of page.items) {
          const blob = envelope(item.data.value)?.blobs?.[0];
          if (blob) added += decoder.decode(await (await fetch(`/v1/blobs/${blob.digest}`, {signal: controller.signal})).arrayBuffer(), {stream: true});
          name = String(item.data.name);
        }
        cursor = page.next_cursor;
      } while (cursor);
      last.current = name; setError(undefined);
      if (added) setText(previous => previous + added);
    })().catch(e => {if (!controller.signal.aborted) setError(e as Error);}).finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [reference, project, pulse]);
  useLayoutEffect(() => {if (block.current && stick.current) block.current.scrollTop = block.current.scrollHeight;}, [text]);
  return <Section title="Console">
    {error && <ErrorNote error={error}/>}{loading && !text && <Loading what="Loading console output"/>}
    {text.length > shown && <Hint>Showing the last {shown / 1024 / 1024} MiB; <code>reader.console()</code> returns all of it.</Hint>}
    {text ? <div className="report-prose table-card"><pre ref={block} className="console-output" onScroll={event => {const el = event.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;}}>{screen(text.slice(-shown))}</pre></div> : !loading && <Hint>{live ? 'No output yet.' : 'This run stored no console output.'}</Hint>}
  </Section>;
}
