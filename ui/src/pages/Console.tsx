import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useRefresh } from '../refresh';
import { records } from '../api';
import { Bytes } from '../protocol';
import { ErrorNote, Hint, Loading, Section } from '../components';

const shown = 1024 * 1024;
/** What a terminal would show: colours dropped, a line rewritten by carriage returns (a progress bar) kept once. */
const screen = (text: string) => text.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '').split('\n').map(line => line.replace(/\r+$/, '').split('\r').at(-1)).join('\n');

/** What a run printed, logged as _console; live runs append. */
export function ConsolePanel({artifact, live}: {artifact: string; live: boolean}) {
  const pulse = useRefresh(live), reached = useRef(0), decoder = useRef(new TextDecoder()), block = useRef<HTMLPreElement>(null), stick = useRef(true);
  const [text, setText] = useState(''), [error, setError] = useState<Error>(), [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      let more = true;
      while (more) {
        const page = await records({artifact, after: reached.current, having: '_console', limit: 1000}, controller.signal);
        if (controller.signal.aborted) return;
        const added = page.items.map(({data}) => (data._console as unknown) instanceof Bytes ? decoder.current.decode((data._console as unknown as Bytes).data, {stream: true}) : '').join('');
        if (page.items.length) reached.current = page.items.at(-1)!.ordinal;
        if (added) setText(previous => (previous + added).slice(-2 * shown));
        more = page.more;
      }
      setError(undefined);
    })().catch(e => {if (!controller.signal.aborted) setError(e as Error);}).finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [artifact, pulse]);
  useLayoutEffect(() => {if (block.current && stick.current) block.current.scrollTop = block.current.scrollHeight;}, [text]);
  return <Section title="Console">
    {error && <ErrorNote error={error}/>}{loading && !text && <Loading what="Loading console output"/>}
    {text.length > shown && <Hint>Showing the last {shown / 1024 / 1024} MiB; <code>cairn console</code> prints all of it.</Hint>}
    {text ? <div className="report-prose table-card"><pre ref={block} className="console-output" onScroll={event => {const el = event.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;}}>{screen(text.slice(-shown))}</pre></div> : !loading && <Hint>{live ? 'No output yet.' : 'This run logged no console output.'}</Hint>}
  </Section>;
}
