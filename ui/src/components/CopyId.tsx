import { Check, Copy } from 'lucide-react';
import { useCopyText } from './useCopyText';

/** Abbreviated IDs always copy their complete, permanent identity. */
export function CopyId({value,short=false}: {value:string;short?:boolean}) {
  const {copy,copied,error}=useCopyText(value);
  return <button type="button" className="copy-id" title={value} aria-label={`Copy ID ${value}`} onClick={copy}><code>{short?`${value.slice(0,12)}…`:value}</code>{copied?<Check aria-hidden="true"/>:<Copy aria-hidden="true"/>}<span className="sr-only" role="status">{copied?'Copied':error?'Unable to copy ID':''}</span></button>;
}
