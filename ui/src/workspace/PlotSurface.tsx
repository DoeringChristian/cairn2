import { useEffect, useRef, useState } from 'react';
import type { Data, Layout } from 'plotly.js';
export type FigureSpec={data:Data[];layout?:Partial<Layout>};
/** One responsive Plotly surface used by scalar cards and logged figures. */
export function PlotSurface({spec,onPoint,onRange,range}: {spec:FigureSpec;onPoint?:(id:string)=>void;onRange?:(range:Record<string,unknown>)=>void;range?:Record<string,unknown>}) {
  const host=useRef<HTMLDivElement>(null),handlers=useRef({onPoint,onRange});handlers.current={onPoint,onRange};
  const [visible,setVisible]=useState(false),[error,setError]=useState('');
  const active=useRef(false),plot=useRef<typeof import('plotly.js-dist-min')>();
  useEffect(()=>{const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){setVisible(true);observer.disconnect();}},{rootMargin:'100px'});if(host.current)observer.observe(host.current);return()=>observer.disconnect();},[]);
  useEffect(()=>{
    if(!visible)return;let closed=false;const node=host.current!;
    const resize=new ResizeObserver(()=>{if(active.current)void plot.current?.Plots.resize(node);});resize.observe(node);
    return()=>{closed=true;resize.disconnect();if(closed&&active.current){plot.current?.purge(node);active.current=false;}};
  },[visible]);
  useEffect(()=>{
    if(!visible)return;let closed=false;const node=host.current!;setError('');
    void import('plotly.js-dist-min').then(async Plotly=>{
      if(closed)return;plot.current=Plotly;
      const css=getComputedStyle(node),color=(name:string)=>css.getPropertyValue(name).trim();
      const layout:Partial<Layout>={paper_bgcolor:color('--card'),plot_bgcolor:color('--card'),colorway:['#176b5b','#b56231','#637fa1','#a58a49','#8a709b'],uirevision:'workspace',...spec.layout,autosize:true,font:{family:'Inter,ui-sans-serif,system-ui,sans-serif',size:11,color:color('--ink'),...spec.layout?.font},margin:{l:52,r:18,b:52,...spec.layout?.margin,t:Math.max(40,spec.layout?.margin?.t||0)}};
      delete layout.width;delete layout.height;
      await Plotly.react(node,structuredClone(spec.data),structuredClone(layout),{responsive:true,displaylogo:false,displayModeBar:true,scrollZoom:false,toImageButtonOptions:{format:'svg',filename:'cairn-card',width:1000,height:600}});
      if(closed)return;active.current=true;
      const emitter=node as unknown as {removeAllListeners:(name:string)=>void;on:(name:string,callback:(data:any)=>void)=>void};
      emitter.removeAllListeners('plotly_click');emitter.removeAllListeners('plotly_relayout');
      emitter.on('plotly_click',data=>{const id=data.points?.[0]?.customdata;if(typeof id==='string')handlers.current.onPoint?.(id);});
      emitter.on('plotly_relayout',data=>handlers.current.onRange?.(data));
    }).catch(e=>{if(!closed)setError(String(e));});
    return()=>{closed=true;};
  },[spec,visible]);
  useEffect(()=>{if(range&&active.current&&host.current)void plot.current?.relayout(host.current,range as Partial<Layout>);},[range]);
  return <div className="workspace-plot-wrap">{error&&<p role="alert">{error}</p>}<div className="workspace-plot" ref={host} aria-label="Interactive chart"/></div>;
}
