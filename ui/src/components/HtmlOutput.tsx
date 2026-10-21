import { useContext, useEffect, useRef, useState } from 'react';
import { useResource } from '../api';
import { ReportArtifactContext } from './ReportContext';
import { ErrorNote, Loading } from './States';
/** Executable output runs with an opaque origin, independently of the report DOM. */
export function HtmlOutput({src}: {src:string}) {
  const frame=useRef<HTMLIFrameElement>(null),[height,setHeight]=useState(360);
  useEffect(()=>{const receive=(event:MessageEvent)=>{if(event.source!==frame.current?.contentWindow||event.data?.type!=='cairn-html-size')return;const value=Number(event.data.height);if(Number.isFinite(value))setHeight(Math.max(80,Math.min(20000,value)));};window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive);},[src]);
  return <iframe ref={frame} className="native-html-viewer" src={src} sandbox="allow-scripts" title="Interactive HTML output" style={{height}}/>;
}
/** An HTML file of the report runs as a viewer with an opaque origin; a signed link serves the report's files to it. */
export function ViewerFrame({path,title,height}: {path:string;title:string;height?:string}) {
  const artifact=useContext(ReportArtifactContext);
  const {data,error}=useResource<{url:string}>(artifact?`/ui-api/viewer-tokens/${encodeURIComponent(artifact)}`:null);
  if(error)return <ErrorNote error={error}/>;
  if(!data)return <Loading what="Loading viewer"/>;
  return <iframe className="native-html-viewer" src={data.url+path.split('/').map(encodeURIComponent).join('/')} sandbox="allow-scripts" title={title} style={{height:height||'480px'}}/>;
}
