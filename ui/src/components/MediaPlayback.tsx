import { OutputComment } from "./OutputComment";
import { useRef, useState } from 'react';
import { VideoPreview } from './media/VideoPreview';
import { MediaLightbox } from './MediaLightbox';
/** Prism2 playback transport and lightbox; audio uses its native accessible player. */
export function MediaPlayback({src,mime,label,source}: {src:string;mime:string;label:string;source?:{file:string;line:number;artifact_ref?:string}}) {
  const [expanded,setExpanded]=useState(false), video=useRef<HTMLVideoElement|null>(null);
  if(mime.startsWith('audio/'))return <OutputComment source={source||{file:"report",line:1}} label="audio"><audio controls preload="metadata" src={src} aria-label={label} className="native-audio"/></OutputComment>;
  return <OutputComment source={source||{file:"report",line:1}} label={label} getMedia={()=>{video.current?.pause();return {time:video.current?.currentTime};}}><VideoPreview src={src} mime={mime} label={label} videoRef={video} onEnlarge={()=>setExpanded(true)}/>{expanded&&<MediaLightbox items={[{url:src,mime,name:label,time:video.current?.currentTime,source}]} onClose={()=>setExpanded(false)}/>}</OutputComment>;
}
