import { useEffect, useRef, useState } from 'react';
import type { Data, Layout } from 'plotly.js';
import { ErrorNote } from './States';

/** Interactive notebook MIME output, with Cairn defaults and no remote script loading. */
export function PlotlyFigure({spec}: {spec:unknown}) {
  const host=useRef<HTMLDivElement>(null),[error,setError]=useState<Error>();
  useEffect(()=>{
    const node=host.current!;let closed=false,resize:ResizeObserver|undefined,purge:(()=>void)|undefined;
    (async()=>{
      const Plotly=await import('plotly.js-dist-min');if(closed)return;
      const figure=spec as {data:Data[];layout?:Partial<Layout>};
      if(!figure||!Array.isArray(figure.data))throw new Error('Invalid Plotly figure');
      const css=getComputedStyle(node),color=(name:string)=>css.getPropertyValue(name).trim();
      const axis={gridcolor:color('--line'),zerolinecolor:color('--line'),linecolor:color('--line'),automargin:true};
      const layout:Partial<Layout>={paper_bgcolor:color('--card'),plot_bgcolor:color('--card'),colorway:[color('--accent'),'#b56231','#637fa1','#a58a49','#8a709b'],hovermode:'closest',scene:{bgcolor:color('--card'),xaxis:{gridcolor:color('--line'),backgroundcolor:color('--paper')},yaxis:{gridcolor:color('--line'),backgroundcolor:color('--paper')},zaxis:{gridcolor:color('--line'),backgroundcolor:color('--paper')}},legend:{orientation:'h',y:-.25},...figure.layout,autosize:true,font:{family:'Inter, ui-sans-serif, system-ui, sans-serif',size:12,color:color('--ink'),...figure.layout?.font},margin:{l:55,r:24,t:60,b:90,...figure.layout?.margin},xaxis:{...axis,...figure.layout?.xaxis},yaxis:{...axis,...figure.layout?.yaxis}};
      delete layout.width;
      await Plotly.newPlot(node,structuredClone(figure.data),structuredClone(layout),{responsive:true,displaylogo:false,displayModeBar:true,scrollZoom:false,toImageButtonOptions:{format:'svg',filename:'cairn-figure',width:1000,height:600,scale:1}});
      purge=()=>Plotly.purge(node);if(closed){purge();return;}
      resize=new ResizeObserver(()=>{void Plotly.Plots.resize(node);});resize.observe(node);
    })().catch(e=>{if(!closed)setError(e);});
    return()=>{closed=true;resize?.disconnect();purge?.();};
  },[spec]);
  return <div className="native-plotly-card">{error&&<ErrorNote error={error}/>}<div ref={host} className="native-plotly" role="img" aria-label="Interactive Plotly figure"/></div>;
}
