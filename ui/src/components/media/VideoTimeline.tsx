import { useEffect, useRef, useState, type PointerEvent } from 'react';
/** Per-frame playback position while playing; timeupdate alone moves in coarse steps. */
export function useLiveTime(media:HTMLVideoElement|null,time:number){
  const [live,setLive]=useState(time);
  useEffect(()=>{
    if(!media)return;let frame=0;
    const tick=()=>{setLive(media.currentTime);frame=media.paused?0:requestAnimationFrame(tick);};
    const wake=()=>{if(!frame)frame=requestAnimationFrame(tick);};
    const events=['play','seeking','seeked','timeupdate','loadedmetadata'];
    events.forEach(name=>media.addEventListener(name,wake));wake();
    return()=>{cancelAnimationFrame(frame);events.forEach(name=>media.removeEventListener(name,wake));};
  },[media]);
  return media?live:time;
}
type Clip={start:number;end:number};
export function VideoTimeline({duration,time,media=null,selecting,clip,onSeek,onClip}: {duration:number;time:number;media?:HTMLVideoElement|null;selecting:boolean;clip:Clip|null;onSeek:(time:number)=>void;onClip:(clip:Clip)=>void}) {
  const shown=useLiveTime(media,time);
  const track=useRef<HTMLDivElement>(null),drag=useRef<{anchor:number;edge?:'start'|'end'}>();
  const at=(event:PointerEvent)=>{const box=track.current!.getBoundingClientRect();return Math.max(0,Math.min(duration,(event.clientX-box.left)/box.width*duration));};
  const update=(value:number)=>{const d=drag.current;if(!d)return;onClip(d.edge&&clip?{start:d.edge==='start'?Math.min(value,clip.end):clip.start,end:d.edge==='end'?Math.max(value,clip.start):clip.end}:{start:Math.min(d.anchor,value),end:Math.max(d.anchor,value)});};
  return <div className={`media-timeline ${selecting?'selecting':''}`} ref={track} aria-label="Video timeline" onPointerDown={event=>{if(!duration||!selecting)return;event.preventDefault();const edge=(event.target as HTMLElement).dataset.edge as 'start'|'end'|undefined;const value=at(event);drag.current={anchor:value,edge};event.currentTarget.setPointerCapture(event.pointerId);if(!edge){onClip({start:value,end:value});onSeek(value);}}} onPointerMove={event=>{if(drag.current)update(at(event));}} onPointerUp={event=>{update(at(event));drag.current=undefined;}} onPointerCancel={()=>{drag.current=undefined;}}>
    <div className="media-timeline-track"/>
    {clip&&<div className="media-timeline-selection" style={{left:`${clip.start/duration*100}%`,width:`${(clip.end-clip.start)/duration*100}%`}}/>}
    <input aria-label="Video position" type="range" min={0} max={duration||1} step="any" value={Math.min(shown,duration||shown)} disabled={!duration} onChange={e=>onSeek(Number(e.target.value))} style={{pointerEvents:selecting?'none':undefined}}/>
    {clip&&(['start','end'] as const).map(edge=><button key={edge} type="button" className="media-timeline-handle" data-edge={edge} role="slider" aria-label={`Clip ${edge}`} aria-valuemin={edge==='start'?0:clip.start} aria-valuemax={edge==='end'?duration:clip.end} aria-valuenow={clip[edge]} aria-valuetext={`${clip[edge].toFixed(2)} seconds`} style={{left:`${clip[edge]/duration*100}%`}} onKeyDown={e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const lower=edge==='start'?0:clip.start,upper=edge==='end'?duration:clip.end;const value=e.key==='Home'?lower:e.key==='End'?upper:Math.max(lower,Math.min(upper,clip[edge]+(e.key==='ArrowRight'?1:-1)*(e.shiftKey?1:0.1)));onClip({...clip,[edge]:value});}}/>)}
  </div>;
}
