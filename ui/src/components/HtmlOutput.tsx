import { useEffect, useRef, useState } from 'react';
/** Executable output runs with an opaque origin, independently of the report DOM. */
export function HtmlOutput({src}: {src:string}) {
  const frame=useRef<HTMLIFrameElement>(null),[height,setHeight]=useState(360);
  useEffect(()=>{const receive=(event:MessageEvent)=>{if(event.source!==frame.current?.contentWindow||event.data?.type!=='cairn-html-size')return;const value=Number(event.data.height);if(Number.isFinite(value))setHeight(Math.max(80,Math.min(20000,value)));};window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive);},[src]);
  return <iframe ref={frame} className="native-html-viewer" src={src} sandbox="allow-scripts" title="Interactive HTML output" style={{height}}/>;
}
