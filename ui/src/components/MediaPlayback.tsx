import { useRef, useState } from 'react';
import { heldVideos, VideoPreview } from './media/VideoPreview';
import { MediaLightbox, type Playback } from './MediaLightbox';
/** Prism2 playback transport and lightbox; audio uses its native accessible player. */
export function MediaPlayback({src,mime,label,source}: {src:string;mime:string;label:string;source?:{file:string;line?:number;artifact_ref?:string;media_id?:string}}) {
  const [expanded,setExpanded]=useState<Playback>(), video=useRef<HTMLVideoElement|null>(null);
  if(mime.startsWith('audio/'))return <div className="native-output-shell"><audio controls preload="metadata" src={src} aria-label={label} className="native-audio"/></div>;
  function enlarge(){const node=video.current;setExpanded({time:node?.currentTime??0,playing:node?!node.paused:true});if(node){heldVideos.add(node);node.pause();}}
  function close(playback?:Playback){const node=video.current;setExpanded(undefined);if(!node||!playback)return;node.currentTime=playback.time;if(playback.playing){heldVideos.delete(node);void node.play().catch(()=>undefined);}}
  return <div className="native-output-shell"><VideoPreview src={src} mime={mime} label={label} videoRef={video} onEnlarge={enlarge}/>{expanded&&<MediaLightbox items={[{url:src,mime,name:label,time:expanded.time,playing:expanded.playing,source}]} onClose={close}/>}</div>;
}
