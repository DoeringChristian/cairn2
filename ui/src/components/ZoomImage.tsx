import { useState, type CSSProperties } from 'react';
import { MediaLightbox, type MediaItem } from './MediaLightbox';

/** Shared image inspection for artifact previews. */
export function ZoomImage({src, alt, className, style, source}: {src:string;alt:string;className?:string;style?:CSSProperties;source?:MediaItem['source']}) {
  const [open,setOpen]=useState(false);
  return <div className="native-output-shell"><button type="button" className="preview-open" style={{width:style?.width}} aria-label={`Enlarge ${alt}`} onClick={()=>setOpen(true)}><img src={src} alt={alt} className={className} style={{width:style?.width&&'100%',height:style?.height}} loading="lazy" decoding="async"/></button>{open&&<MediaLightbox items={[{url:src,mime:'image/*',name:alt,source}]} onClose={()=>setOpen(false)}/>}</div>;
}
