import { useEffect, useRef, useState } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import { RotateCcw } from 'lucide-react';
import { ActionIcon } from '../components/Button';
import type { Card, Observation } from './model';
import type { ScalarLine, ScalarPoint } from './scalarData';

const number=(value:number)=>Number(value.toPrecision(5)).toString();
// Match Cairn 1: less than 20 px of movement on an axis selects only the other.
const axisDragThreshold=20;
type Hover={line:ScalarLine;point:ScalarPoint;left:number;top:number};

/** uPlot owns axes, scales, zoom and canvas sizing. Paths retain arbitrary record order. */
export function ScalarPlot({lines,card,hidden,onInspect}:{lines:ScalarLine[];card:Card;hidden:string[];onInspect:(rows:Observation[])=>void}) {
  const host=useRef<HTMLDivElement>(null),plot=useRef<uPlot>();
  const [hover,setHover]=useState<Hover>();
  const inspect=useRef(onInspect);inspect.current=onInspect;
  const zoom=useRef<{identity:string;x?:[number,number];y?:[number,number]}>({identity:''});
  useEffect(()=>{
    const element=host.current;if(!element)return;
    const identity=JSON.stringify([card.x,card.y,card.logX,card.logY]);
    if(zoom.current.identity!==identity)zoom.current={identity};
    setHover(undefined);
    const visible=lines.filter(line=>!hidden.includes(line.groupKey));
    // Mode 2 accepts independent XY arrays. Types in uPlot 1.x only describe aligned mode 1.
    const rendered=visible.length?visible:[{name:'',color:'transparent',points:[]}];
    const data=[[],...rendered.map(line=>[
      line.points.map(p=>p.x),line.points.map(p=>p.y),
    ])] as unknown as uPlot.AlignedData;
    const domain=(axis:'x'|'y',log:boolean):uPlot.Range.MinMax=>{
      let low=Infinity,high=-Infinity;
      for(const line of visible)for(const p of line.points)for(const value of axis==='x'?[p.x]:[p.y,p.low,p.high]) {
        if(value!=null&&Number.isFinite(value)&&(!log||value>0)){low=Math.min(low,value);high=Math.max(high,value);}
      }
      if(low>high)return log?[1,10]:[0,1];
      // Pad in scale space so zero and end-point markers do not touch the frame.
      if(log){const lo=Math.log(low),hi=Math.log(high),pad=(hi-lo||1)*.05;return [Math.max(Number.MIN_VALUE,Math.exp(lo-pad)),Math.min(Number.MAX_VALUE,Math.exp(hi+pad))];}
      const pad=low===high?(Math.abs(low)*.05||1):high*.05-low*.05;
      return [Math.max(-Number.MAX_VALUE,low-pad),Math.min(Number.MAX_VALUE,high+pad)];
    };
    const draw=(u:uPlot)=>{
      const ctx=u.ctx,{left,top,width,height}=u.bbox;
      ctx.save();ctx.beginPath();ctx.rect(left,top,width,height);ctx.clip();
      const pos=(p:ScalarPoint)=>p.x===null||p.y===null?undefined:[u.valToPos(p.x,'x',true),u.valToPos(p.y,'y',true)];
      // Bands draw first, so member and centre lines remain legible.
      for(const line of visible) {
        let segment:ScalarPoint[]=[];
        const fill=()=>{if(segment.length<2){segment=[];return;}ctx.beginPath();
          segment.forEach((p,i)=>{const x=u.valToPos(p.x!,'x',true),y=u.valToPos(p.high!,'y',true);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});
          for(const p of segment.reverse())ctx.lineTo(u.valToPos(p.x!,'x',true),u.valToPos(card.logY?Math.max(p.low!,u.scales.y.min!):p.low!,'y',true));
          ctx.closePath();ctx.fillStyle=line.color;ctx.globalAlpha=.17;ctx.fill();segment=[];};
        for(const p of line.points){if(p.x!==null&&p.y!==null&&p.low!==undefined&&p.high!==undefined&&(!card.logY||p.high>0))segment.push(p);else fill();}fill();
      }
      for(const line of [...visible.filter(l=>l.member),...visible.filter(l=>!l.member)]) {
        ctx.globalAlpha=line.member?.25:1;ctx.strokeStyle=line.color;ctx.fillStyle=line.color;ctx.lineWidth=(line.member?1:1.7)*devicePixelRatio;ctx.setLineDash(line.dash.map(n=>n*devicePixelRatio));
        if(card.marks!=='markers') {ctx.beginPath();let start=true;for(const p of line.points){const xy=pos(p);if(!xy){start=true;continue;}if(start)ctx.moveTo(xy[0],xy[1]);else ctx.lineTo(xy[0],xy[1]);start=false;}ctx.stroke();}
        // Isolated points must remain visible even in line-only mode.
        const marker=card.marks!=='lines'||line.points.filter(p=>p.y!==null).length===1;
        if(marker){ctx.setLineDash([]);ctx.beginPath();for(const p of line.points){const xy=pos(p);if(!xy)continue;ctx.moveTo(xy[0]+2.5*devicePixelRatio,xy[1]);ctx.arc(xy[0],xy[1],2.5*devicePixelRatio,0,2*Math.PI);}ctx.fill();}
      }
      ctx.restore();
    };
    const axis=(label:string):uPlot.Axis=>({label,labelSize:18,labelFont:'11px Inter, sans-serif',font:'10px Inter, sans-serif',stroke:'#697169',grid:{stroke:'#d9d8ce',width:1},ticks:{stroke:'#d9d8ce',width:1},values:(_u,ticks)=>ticks.map(value=>value==null?'':number(value)),size:42});
    const u=new uPlot({mode:2,width:Math.max(100,element.clientWidth),height:Math.max(100,element.clientHeight),padding:[12,12,0,0],legend:{show:false},cursor:{points:{show:false},drag:{x:true,y:true,uni:axisDragThreshold,dist:5}},scales:{x:{time:false,distr:card.logX?3:1,range:()=>zoom.current.x??domain('x',card.logX)},y:{distr:card.logY?3:1,range:()=>zoom.current.y??domain('y',card.logY)}},axes:[axis(card.x),{...axis(card.y),size:54}],series:[{},...rendered.map(line=>({label:line.name,stroke:line.color,paths:()=>null,points:{show:false},facets:[{scale:'x',sorted:0 as const},{scale:'y',sorted:0 as const}]}))],hooks:{draw:[draw],setSelect:[u=>{if(u.select.width>5&&u.select.height>5){
      // uPlot expands a one-axis selection across the other axis. Leave that axis untouched.
      if(u.select.width<u.over.clientWidth-1)zoom.current.x=[u.posToVal(u.select.left,'x'),u.posToVal(u.select.left+u.select.width,'x')];
      if(u.select.height<u.over.clientHeight-1)zoom.current.y=[u.posToVal(u.select.top+u.select.height,'y'),u.posToVal(u.select.top,'y')];
    }}]}},data,element);
    plot.current=u;
    // Expose the actual renderer instance for inspection, as Plotly does on its root.
    Object.assign(element,{uplot:u});
    let frame=0,lastEvent:MouseEvent|undefined,current:Hover|undefined,down:{x:number;y:number}|undefined;
    const move=(event:MouseEvent)=>{lastEvent=event;if(frame)return;frame=requestAnimationFrame(()=>{frame=0;if(!lastEvent)return;const rect=u.over.getBoundingClientRect(),x=lastEvent.clientX-rect.left,y=lastEvent.clientY-rect.top;
      let distance=18**2;current=undefined;
      for(const line of visible)for(const point of line.points){if(point.x===null||point.y===null)continue;const px=u.valToPos(point.x,'x'),py=u.valToPos(point.y,'y'),d=(px-x)**2+(py-y)**2;if(d<distance){distance=d;current={line,point,left:Math.min(element.clientWidth-210,Math.max(0,x+u.over.offsetLeft+12)),top:Math.max(0,y+u.over.offsetTop-65)};}}
      setHover(current);
    });};
    const leave=()=>{lastEvent=undefined;current=undefined;setHover(undefined);};
    const press=(event:MouseEvent)=>{down={x:event.clientX,y:event.clientY};};
    const click=(event:MouseEvent)=>{if(current&&down&&Math.hypot(event.clientX-down.x,event.clientY-down.y)<5)inspect.current(current.point.rows);};
    const reset=()=>{zoom.current={identity};u.setData(data);};
    u.over.addEventListener('mousemove',move);u.over.addEventListener('mouseleave',leave);u.over.addEventListener('mousedown',press);u.over.addEventListener('click',click);u.over.addEventListener('dblclick',reset);
    const resize=new ResizeObserver(()=>{if(element.clientWidth&&element.clientHeight)u.setSize({width:element.clientWidth,height:element.clientHeight});});resize.observe(element);
    return()=>{cancelAnimationFrame(frame);resize.disconnect();u.over.removeEventListener('mousemove',move);u.over.removeEventListener('mouseleave',leave);u.over.removeEventListener('mousedown',press);u.over.removeEventListener('click',click);u.over.removeEventListener('dblclick',reset);u.destroy();plot.current=undefined;Object.assign(element,{uplot:undefined});};
  },[lines,card.x,card.y,card.logX,card.logY,card.marks,hidden]);
  return <div className="workspace-scalar-wrap"><div className="workspace-scalar-toolbar"><ActionIcon label="Reset plot zoom" onClick={()=>{zoom.current={identity:zoom.current.identity};plot.current?.setData(plot.current.data);}}><RotateCcw size={13}/></ActionIcon></div><div className="workspace-scalar-plot" ref={host} role="img" aria-label={`${card.y} by ${card.x}`}/>{hover&&<div className="workspace-scalar-tooltip" style={{left:hover.left,top:hover.top+24}}><strong style={{color:hover.line.color}}>{hover.line.name}</strong><span>{card.x}: {number(hover.point.x!)} · {card.y}: {number(hover.point.y!)}</span>{hover.point.low!==undefined&&<span>Band: {number(hover.point.low)} – {number(hover.point.high!)}</span>}<span>{hover.point.rows.length>1?`${hover.point.rows.length} contributing runs · Click to inspect`:'Click to inspect record'}</span></div>}</div>;
}
