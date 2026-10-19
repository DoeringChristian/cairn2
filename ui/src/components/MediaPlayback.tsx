import { OutputComment } from "./OutputComment";
import { useRef, useState } from 'react';
import { heldVideos, VideoPreview } from './media/VideoPreview';
import { MediaLightbox, type Playback } from './MediaLightbox';
/** Prism2 playback transport and lightbox; audio uses its native accessible player. */
export function MediaPlayback({src,mime,label,source}: {src:string;mime:string;label:string;source?:{file:string;line:number;artifact_ref?:string}}) {
  const [expanded,setExpanded]=useState<Playback>(), video=useRef<HTMLVideoElement|null>(null);
  if(mime.startsWith('audio/'))return <OutputComment source={source||{file:"report",line:1}} label="audio"><audio controls preload="metadata" src={src} aria-label={label} className="native-audio"/></OutputComment>;
  function enlarge(){const node=video.current;setExpanded({time:node?.currentTime??0,playing:node?!node.paused:true});if(node){heldVideos.add(node);node.pause();}}
  function close(playback?:Playback){const node=video.current;setExpanded(undefined);if(!node||!playback)return;node.currentTime=playback.time;if(playback.playing){heldVideos.delete(node);void node.play().catch(()=>undefined);}}
  return <OutputComment source={source||{file:"report",line:1}} label={label} getMedia={()=>{video.current?.pause();return {time:video.current?.currentTime};}}><VideoPreview src={src} mime={mime} label={label} videoRef={video} onEnlarge={enlarge}/>{expanded&&<MediaLightbox items={[{url:src,mime,name:label,time:expanded.time,playing:expanded.playing,source}]} onClose={close}/>}</OutputComment>;
}
