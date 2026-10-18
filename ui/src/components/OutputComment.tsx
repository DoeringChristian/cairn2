import { useContext, useState, type ReactNode } from 'react';
import { CommentButton } from './CommentButton';
import { CommentComposer, useReportComments, type CommentAnchor, type Comment } from './ReportComments';
import { mediaCommentKey, ReportArtifactContext } from './ReportContext';
export function OutputComment({source,label,children,getMedia}: {source:{file?:string;path?:string;line?:number};label:string;children:ReactNode;getMedia?:()=>CommentAnchor['media']}) {
  const [media,setMedia]=useState<CommentAnchor['media']>({}),[initial,setInitial]=useState<Comment>();
  const {items,saved}=useReportComments(),existing=items.find(item=>item.anchor.media_key===mediaCommentKey(source,label));
  const artifact=useContext(ReportArtifactContext),[position,setPosition]=useState<{x:number;y:number}>();
  return <div className="native-output-shell" data-comment-media={mediaCommentKey(source,label)}>{children}{artifact&&<CommentButton existing={!!existing} saved={!!existing&&saved===existing.id} className="output-comment-button" aria-label={existing?"Open comment":`Comment on ${label}`} title={existing?.body} onClick={e=>{e.stopPropagation();setInitial(existing);setMedia(getMedia?.()||{});const box=e.currentTarget.getBoundingClientRect();setPosition({x:box.left,y:box.top});}}/>}{position&&artifact&&<CommentComposer artifact={artifact} initial={initial} anchor={initial?.anchor||{path:source.file||source.path,line_start:source.line,media_key:mediaCommentKey(source,label),media}} position={position} onClose={()=>setPosition(undefined)}/>}</div>;
}
