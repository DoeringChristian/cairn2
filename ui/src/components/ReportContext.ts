import { createContext } from 'react';
export const ReportArtifactContext=createContext<string|undefined>(undefined);
export function mediaCommentKey(source:{file?:string;path?:string;line?:number}|undefined,name:string|undefined){return source?`${source.file||source.path}:${name||''}`:undefined;}

/** Existing anchors distinguish figures by their saved source line. */
export function matchesMediaComment(anchor:{media_key?:string;line_start?:number;media_id?:string},source:{file?:string;path?:string;line?:number;media_id?:string}|undefined,name:string|undefined) {
  if(anchor.media_id)return anchor.media_id===source?.media_id;
  const key=mediaCommentKey(source,name);
  return !!key && anchor.media_key===key && (anchor.line_start===undefined || source?.line===undefined || anchor.line_start===source.line);
}
