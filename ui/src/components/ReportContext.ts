import { createContext } from 'react';
export const ReportArtifactContext=createContext<string|undefined>(undefined);
export function mediaCommentKey(source:{file?:string;path?:string;line?:number}|undefined,name:string|undefined){return source?`${source.file||source.path}:${name||''}`:undefined;}
