import { ZoomImage } from './ZoomImage';
import { useImageBlob } from './useBlobUrl';
import { blobPath, bytes, envelope, print, useLoaded, type BlobRef, type Json } from '../api';
import { DataTable } from './Table';
import { JsonBlock } from './Code';
import { Hint, ErrorNote, Loading } from './States';
import { LinkButton } from './Button';
import { Pill } from './Pill';
import { PlotlyFigure } from './PlotlyFigure';
export function BlobTable({blobs}: {blobs: BlobRef[]}) {
  return blobs.length ? <DataTable head={['File','Size','Content','Download']}>{blobs.map((b,i)=><tr key={`${b.digest}-${i}`}><td className="wrap-any">{b.name || b.digest.slice(0,16)}</td><td className="nowrap">{bytes(b.size)}</td><td><code title={b.digest}>{b.digest.slice(0,12)}…</code></td><td><LinkButton small href={b.href || blobPath(b)} download={b.name || b.digest}>Download</LinkButton></td></tr>)}</DataTable> : <Hint>No files attached.</Hint>;
}
export function ImagePreview({blob}: {blob: BlobRef}) {
  const {url,error}=useImageBlob(blob,blob.size<=20*1024**2);
  if(blob.size>20*1024**2) return <Hint>Image exceeds the 20 MiB preview limit. Download it to view.</Hint>;
  return <>{error ? <ErrorNote error={error}/> : !url ? <Loading what="Loading image"/> : <figure className="image-preview"><ZoomImage src={url} alt={blob.name || 'Artifact image'}/><figcaption>{blob.name} · Click to enlarge and zoom</figcaption></figure>}</>;
}
function TablePreview({blob,columns}: {blob: BlobRef;columns: string[]}) {
  const limit=256*1024;
  const {data,error}=useLoaded(async signal=>{
    const response=await fetch(blobPath(blob),{signal,headers:{Range:`bytes=0-${limit-1}`}});
    if(!response.ok)throw new Error(`Could not load the table (${response.status}).`);
    const lines=(await response.text()).split('\n');
    return lines.slice(0,blob.size>limit?-1:undefined).filter(Boolean).slice(0,100).map(line=>JSON.parse(line) as Record<string,Json>);
  },`table:${blob.digest}`);
  return error?<ErrorNote error={error}/>:!data?<Loading what="Loading table"/>:<DataTable head={columns}>{data.map((row,i)=><tr key={i}>{columns.map(column=><td key={column}>{row[column]===undefined?'':print(row[column])}</td>)}</tr>)}</DataTable>;
}
/** A value as what it is; what the pages do not know stays JSON. */
export function ValueView({value}: {value: Json | undefined}) {
  const typed=envelope(value),blob=typed?.blobs[0];
  if(!typed) return value===undefined ? <Hint>No value.</Hint> : <JsonBlock value={value}/>;
  const media=blob?.media_type||'';
  const meta=Object.fromEntries(Object.entries(typed.data).filter(([key])=>!['_type','data','media_type','rows','columns'].includes(key)));
  return <div className="value-view">
    {blob && (media.startsWith('image/') ? <ImagePreview blob={blob}/> : media.startsWith('video/') ? <video className="value-media" controls src={blobPath(blob)}/> : media.startsWith('audio/') ? <audio controls src={blobPath(blob)}/> :
      typed.format==='figure/plotly' ? <PlotlyFigure src={blobPath(blob)}/> : typed.format==='table' ? <><TablePreview blob={blob} columns={(typed.data.columns as string[])||[]}/>{Number(typed.data.rows)>100&&<Hint>Showing the first 100 of {String(typed.data.rows)} rows.</Hint>}</> : null)}
    {Object.keys(meta).length>0 && <JsonBlock value={meta}/>}
    {blob && <p><Pill tone="alias">{typed.format}</Pill> <LinkButton small href={blobPath(blob)} download>Download · {bytes(blob.size)}</LinkButton></p>}</div>;
}
