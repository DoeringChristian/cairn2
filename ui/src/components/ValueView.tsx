import { ZoomImage } from './ZoomImage';
import { useImageBlob } from './useBlobUrl';
import { bytes, envelope, type BlobRef, type Json } from '../api';
import { DataTable } from './Table';
import { JsonBlock } from './Code';
import { Hint, ErrorNote, Loading } from './States';
import { LinkButton } from './Button';
import { Pill } from './Pill';
export function BlobTable({blobs}: {blobs: (BlobRef & {href?: string})[]}) {
  return blobs.length ? <DataTable head={['File','Size','Content','Download']}>{blobs.map((b,i)=><tr key={`${b.digest}-${i}`}><td className="wrap-any">{b.name || b.digest.slice(0,16)}</td><td className="nowrap">{bytes(b.size)}</td><td><code title={b.digest}>{b.digest.slice(0,12)}…</code></td><td><LinkButton small href={b.href || `/v1/blobs/${b.digest}`} download={b.name || b.digest}>Download</LinkButton></td></tr>)}</DataTable> : <Hint>No files attached.</Hint>;
}
export function ImagePreview({blob}: {blob: BlobRef}) {
  const {url,error}=useImageBlob(blob,blob.size<=20*1024**2);
  if(blob.size>20*1024**2) return <Hint>Image exceeds the 20 MiB preview limit. Download it to view.</Hint>;
  return <>{error ? <ErrorNote error={error}/> : !url ? <Loading what="Loading image"/> : <figure className="image-preview"><ZoomImage src={url} alt={blob.name || 'Artifact image'}/><figcaption>{blob.name} · Click to enlarge and zoom</figcaption></figure>}</>;
}
/** Typed values retain an inspectable fallback when the UI does not know their format. */
export function ValueView({value}: {value: Json | undefined}) {
  const typed=envelope(value);
  if(!typed) return value === null || value === undefined ? <Hint>No final value recorded.</Hint> : <JsonBlock value={value}/>;
  if(typed.format==='json/literal') return <JsonBlock value={typed.data}/>;
  const data=typed.data && typeof typed.data==='object' && !Array.isArray(typed.data) ? typed.data : {};
  const rows=Array.isArray(data.rows) ? data.rows : null;
  const columns=Array.isArray(data.columns) ? data.columns.filter((c): c is string=>typeof c==='string') : [];
  return <div className="value-view"><p><Pill tone="alias">{typed.format}</Pill></p>
    {typed.format==='table/rows' && rows ? <><DataTable head={columns}>{rows.slice(0,100).map((row,i)=><tr key={i}>{columns.map(c=><td key={c}>{row && typeof row==='object' && !Array.isArray(row) ? JSON.stringify(row[c]) : ''}</td>)}</tr>)}</DataTable>{rows.length>100&&<Hint>Showing the first 100 of {rows.length} rows.</Hint>}</> :
    ['image','figure/svg'].includes(typed.format) && typed.blobs?.[0] ? <ImagePreview blob={typed.blobs[0]}/> : <JsonBlock value={typed.data}/>}
    {!!typed.blobs?.length && <BlobTable blobs={typed.blobs}/>}</div>;
}
