import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronLeft, ChevronRight, Play, Pause } from 'lucide-react';
import { envelope, artifactPath, type Json } from '../api';
import { Select } from '../components/Select';
import { ActionIcon, Button } from '../components/Button';
import { MediaLightbox } from '../components/MediaLightbox';
import { PlotSurface, type FigureSpec } from './PlotSurface';
import { compare, filtered, groups, label, sorted, valueAt, type Card, type Observation } from './model';
import { WorkspaceDialog } from './Dialog';
import { ScalarPlot } from './ScalarPlot';
import { scalarData } from './scalarData';
import { runColor } from './presentation';

const recordId=(row:Observation)=>`${row.artifact_id}:${row.ordinal}`;
const finite=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
const display=(v:Json|undefined)=>typeof v==='number'?Number(v.toPrecision(5)).toString():String(v??'—');
function Inspector({row,onClose}:{row:Observation;onClose:()=>void}) {
  return <WorkspaceDialog title={`Record ${row.ordinal}`} onClose={onClose}><p><Link to={artifactPath(row.run.project,row.artifact_id)}>{label(row.run)}</Link></p><pre className="workspace-record-json">{JSON.stringify(row.data,null,2)}</pre><Button onClick={()=>void navigator.clipboard.writeText(JSON.stringify({artifact_id:row.artifact_id,ordinal:row.ordinal}))}>Copy record reference</Button></WorkspaceDialog>;
}
export function CardContent({card,rows}:{card:Card;rows:Observation[]}) {
  const data=useMemo(()=>filtered(rows,card),[rows,card]);
  return card.kind==='scalar'?<ScalarContent card={card} rows={data}/>:<MediaContent card={card} rows={data}/>;
}
function ScalarContent({card,rows}:{card:Card;rows:Observation[]}) {
  const [inspected,setInspected]=useState<Observation>(),[contributors,setContributors]=useState<Observation[]>(),[hidden,setHidden]=useState<string[]>([]);
  const {lines,count,omitted}=useMemo(()=>scalarData(rows,card),[rows,card]);
  const primary=lines.filter(line=>!line.member);
  return <>{count?<ScalarPlot lines={lines} card={card} hidden={hidden} onInspect={records=>{if(records.length===1)setInspected(records[0]);else if(records.length)setContributors(records);}}/>:<div className="workspace-empty">No numeric observations for <code>{card.x}</code> and <code>{card.y}</code>. Choose fields in card settings.</div>}
    {!!count&&<div className="workspace-legend" aria-label="Chart series">{primary.map(line=><button key={line.key} aria-pressed={!hidden.includes(line.groupKey)} onClick={()=>setHidden(h=>h.includes(line.groupKey)?h.filter(n=>n!==line.groupKey):[...h,line.groupKey])}><span style={{background:line.color}}/>{line.name}</button>)}</div>}
    <div className="workspace-card-note">{count.toLocaleString()} points · {primary.length} lines{omitted?` · ${omitted} records without valid axes`:''} · Click a point to inspect</div>
    {contributors&&<WorkspaceDialog title="Contributing records" onClose={()=>setContributors(undefined)}><p>Member observations used for this group point.</p>{contributors.map(row=><p key={recordId(row)}><Button onClick={()=>{setContributors(undefined);setInspected(row);}}>{label(row.run)} · record {row.ordinal} · {card.x}: {display(valueAt(row,card.x))} · {card.y}: {display(valueAt(row,card.y))}</Button></p>)}</WorkspaceDialog>}
    {inspected&&<Inspector row={inspected} onClose={()=>setInspected(undefined)}/>}</>;
}
function MediaContent({card,rows}:{card:Card;rows:Observation[]}) {
  const [page,setPage]=useState(0),[cursor,setCursor]=useState<string>(),[follow,setFollow]=useState(true),[playing,setPlaying]=useState(false),[chosen,setChosen]=useState('');
  const [expanded,setExpanded]=useState<Observation>(),[inspected,setInspected]=useState<Observation>(),[linked,setLinked]=useState<Record<string,unknown>>();
  const media=useMemo(()=>sorted(rows.filter(row=>{const typed=envelope(valueAt(row,card.value));return card.kind==='image'?typed?.format==='image'||typed?.format==='figure/svg':typed?.format==='figure/plotly';}),card.pointOrder),[rows,card]);
  const series=useMemo(()=>groups(media,card.split),[media,card.split]);
  const axisValues=useMemo(()=>[...new Map(media.map(r=>valueAt(r,card.axis)).filter(v=>v!=null&&typeof v!=='object').map(v=>[JSON.stringify(v),v!])).values()].sort(compare),[media,card.axis]);
  const cursorIndex=follow?Math.max(0,axisValues.length-1):Math.max(0,axisValues.findIndex(v=>JSON.stringify(v)===cursor));
  const current=axisValues[cursorIndex];
  const setIndex=(index:number)=>{setFollow(false);setCursor(JSON.stringify(axisValues[Math.max(0,Math.min(axisValues.length-1,index))]));};
  useEffect(()=>{setPage(0);setChosen('');setCursor(undefined);setFollow(true);setPlaying(false);setLinked(undefined);},[card]);
  useEffect(()=>{if(!playing||axisValues.length<2)return;const timer=setInterval(()=>setIndex((cursorIndex+1)%axisValues.length),900);return()=>clearInterval(timer);},[playing,cursorIndex,axisValues]);
  function pick(points:Observation[]) {
    const exact=points.filter(r=>compare(valueAt(r,card.axis),current)===0);if(exact.length)return exact;
    if(card.matching==='exact'||current===undefined)return [];
    const valid=points.filter(r=>valueAt(r,card.axis)!=null);
    if(card.matching==='previous'){
      const before=valid.filter(r=>compare(valueAt(r,card.axis),current)<=0).sort((a,b)=>compare(valueAt(b,card.axis),valueAt(a,card.axis)));
      return before.length?before.filter(r=>compare(valueAt(r,card.axis),valueAt(before[0],card.axis))===0):[];
    }
    if(!finite(current))return [];
    const nearest=valid.filter(r=>finite(valueAt(r,card.axis))).sort((a,b)=>Math.abs(Number(valueAt(a,card.axis))-current)-Math.abs(Number(valueAt(b,card.axis))-current));
    return nearest.length?nearest.filter(r=>compare(valueAt(r,card.axis),valueAt(nearest[0],card.axis))===0):[];
  }
  const selectedGroup=series.find(g=>g.key===chosen)||series[0];
  const tiles:{key:string;name:string;row?:Observation}[]=card.mode==='gallery'?media.map(row=>({key:recordId(row),name:label(row.run),row})): (card.mode==='sequence'?(selectedGroup?[selectedGroup]:[]):series).flatMap(group=>{const found=pick(group.rows);return found.length?found.map(row=>({key:recordId(row),name:group.name,row})):[{key:group.key,name:group.name}];});
  const totalPages=Math.max(1,Math.ceil(tiles.length/card.pageSize)),currentPage=Math.min(page,totalPages-1),visible=tiles.slice(currentPage*card.pageSize,(currentPage+1)*card.pageSize);
  const emitRange=(range:Record<string,unknown>)=>{if(!card.linkAxes)return;const axes=Object.fromEntries(Object.entries(range).filter(([key])=>/^[xy]axis\.(range\[[01]\]|autorange)$/.test(key)));if(Object.keys(axes).length)setLinked(previous=>JSON.stringify(previous)===JSON.stringify(axes)?previous:axes);};
  return <>
    {card.mode!=='gallery'&&!!media.length&&<div className="workspace-navigator">
      <div className="workspace-navigator-top"><span><strong>{card.axis}</strong> <output>{display(current)}</output></span>{card.mode==='sequence'&&<Select label="Sequence" value={selectedGroup?.key||''} onChange={value=>{setChosen(value);setPage(0);}} choices={series.map(g=>({value:g.key,label:g.name}))}/>}<label><input type="checkbox" checked={follow} onChange={e=>{setFollow(e.target.checked);setCursor(JSON.stringify(current));}}/> Latest</label></div>
      <div className="workspace-slider"><ActionIcon label="Previous axis value" disabled={!axisValues.length||cursorIndex===0} onClick={()=>setIndex(cursorIndex-1)}><ChevronLeft size={16}/></ActionIcon><input type="range" aria-label={`Browse ${card.axis}`} min={0} max={Math.max(0,axisValues.length-1)} value={cursorIndex} disabled={axisValues.length<2} onChange={e=>{setIndex(Number(e.target.value));setPage(0);}}/><ActionIcon label="Next axis value" disabled={cursorIndex>=axisValues.length-1} onClick={()=>setIndex(cursorIndex+1)}><ChevronRight size={16}/></ActionIcon><ActionIcon label={playing?'Pause sequence':'Play sequence'} disabled={axisValues.length<2} onClick={()=>{if(!playing&&cursorIndex===axisValues.length-1)setIndex(0);setPlaying(!playing);}}>{playing?<Pause size={15}/>:<Play size={15}/>}</ActionIcon></div>
    </div>}
    {!media.length?<div className="workspace-empty">No {card.kind==='image'?'images':'Plotly figures'} in <code>{card.value}</code>. Choose a logged field in settings.</div>:!axisValues.length&&card.mode!=='gallery'?<div className="workspace-empty">No values for navigation field <code>{card.axis}</code>. Choose another field or gallery mode.</div>:<div className="workspace-media-grid" style={{'--tile-columns':card.mode==='sequence'?1:card.columns} as React.CSSProperties}>{visible.map(tile=><div key={tile.key} className="workspace-media-tile">
      <div className="workspace-tile-heading">{tile.row&&<i className="workspace-run-swatch" style={{background:runColor(tile.row.artifact_id)}} aria-hidden="true"/>}<span title={tile.name}>{tile.name}</span>{tile.row&&<button className="text-button" onClick={()=>setInspected(tile.row)}>#{tile.row.ordinal}</button>}</div>
      {tile.row?<><MediaTile card={card} row={tile.row} onExpand={()=>setExpanded(tile.row)} linked={card.linkAxes?linked:undefined} onRange={emitRange}/><div className="workspace-tile-caption"><span>{card.axis}: {display(valueAt(tile.row,card.axis))}</span>{card.caption&&<span>{card.caption}: {display(valueAt(tile.row,card.caption))}</span>}</div></>:<div className="workspace-empty">No observation at {display(current)}</div>}
    </div>)}</div>}
    <div className="workspace-card-note"><span>{media.length} records · {card.mode==='gallery'?'Ordered by '+card.pointOrder.field:card.matching==='exact'?'Exact axis matches':card.matching==='previous'?'Previous available observation':'Nearest available observation'}</span><span><button disabled={currentPage===0} onClick={()=>setPage(currentPage-1)} aria-label="Previous gallery page">‹</button> {currentPage+1} / {totalPages} <button disabled={currentPage>=totalPages-1} onClick={()=>setPage(currentPage+1)} aria-label="Next gallery page">›</button></span></div>
    {expanded&&card.kind==='image'&&<MediaLightbox items={[{url:`/v1/blobs/${envelope(valueAt(expanded,card.value))!.blobs[0].digest}`,mime:envelope(valueAt(expanded,card.value))!.blobs[0].media_type||'image/png',name:`${label(expanded.run)} · ${card.axis} ${display(valueAt(expanded,card.axis))}`}]} onClose={()=>setExpanded(undefined)}/>}
    {expanded&&card.kind==='plotly'&&<WorkspaceDialog title={label(expanded.run)} onClose={()=>setExpanded(undefined)}><div className="workspace-expanded-figure"><PlotSurface spec={envelope(valueAt(expanded,card.value))!.data as unknown as FigureSpec}/></div></WorkspaceDialog>}
    {inspected&&<Inspector row={inspected} onClose={()=>setInspected(undefined)}/>}
  </>;
}
function MediaTile({card,row,onExpand,linked,onRange}:{card:Card;row:Observation;onExpand:()=>void;linked?:Record<string,unknown>;onRange:(range:Record<string,unknown>)=>void}) {
  const value=envelope(valueAt(row,card.value))!;
  if(card.kind==='image')return value.blobs[0]?<button className="workspace-image-open" onClick={onExpand} aria-label="Enlarge image"><img loading="lazy" src={`/v1/blobs/${value.blobs[0].digest}`} alt={`${label(row.run)} · ${card.caption?display(valueAt(row,card.caption)):card.value}`} style={{objectFit:card.fit}}/></button>:<p>Image has no blob.</p>;
  return <><div className="workspace-figure-tile"><PlotSurface spec={value.data as unknown as FigureSpec} range={linked} onRange={onRange}/></div><button className="text-button workspace-expand-plot" onClick={onExpand}>Expand figure ↗</button></>;
}
