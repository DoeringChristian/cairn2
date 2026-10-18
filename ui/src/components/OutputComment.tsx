import { useContext, useState, type ReactNode } from 'react';
import { MessageSquarePlus } from 'lucide-react';
import { CommentComposer, type CommentAnchor } from './ReportComments';
import { mediaCommentKey, ReportArtifactContext } from './ReportContext';
export function OutputComment({source,label,children,getMedia}: {source:{file?:string;path?:string;line?:number};label:string;children:ReactNode;getMedia?:()=>CommentAnchor['media']}) {
  const [media,setMedia]=useState<CommentAnchor['media']>({});
  const artifact=useContext(ReportArtifactContext),[position,setPosition]=useState<{x:number;y:number}>();
  return <div className="native-output-shell" data-comment-media={mediaCommentKey(source,label)}>{children}{artifact&&<button type="button" className="image-copy output-comment-button" aria-label={`Comment on ${label}`} onClick={e=>{setMedia(getMedia?.()||{});const box=e.currentTarget.getBoundingClientRect();setPosition({x:box.left,y:box.top});}}><MessageSquarePlus aria-hidden="true"/></button>}{position&&artifact&&<CommentComposer artifact={artifact} anchor={{path:source.file||source.path,line_start:source.line,media_key:mediaCommentKey(source,label),media}} position={position} onClose={()=>setPosition(undefined)}/>}</div>;
}
