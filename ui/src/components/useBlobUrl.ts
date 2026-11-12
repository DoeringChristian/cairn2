import { useEffect, useState } from 'react';
import type { BlobRef } from '../api';

type Entry={promise:Promise<string>;url?:string;size:number;users:number};
const entries=new Map<string,Entry>(),budget=256*1024**2;let total=0;
const mimeOf=(blob:BlobRef)=>blob.media_type||(blob.name?.toLowerCase().endsWith('.svg')?'image/svg+xml':'image/png');

/** Object URLs shared by every view of a blob; unused ones are revoked beyond the budget. */
function entry(blob:BlobRef,mime:string) {
  const key=`${blob.digest} ${mime}`;let found=entries.get(key);
  if(found)entries.delete(key);
  else{
    const created:Entry={size:blob.size,users:0,promise:Promise.resolve('')};
    created.promise=fetch(`/v1/blobs/${blob.digest}`).then(async response=>{
      if(!response.ok)throw new Error(`Could not load image (${response.status}).`);
      created.url=URL.createObjectURL(new Blob([await response.blob()],{type:mime}));total+=created.size;trim();return created.url;
    });
    created.promise.catch(()=>{if(entries.get(key)===created)entries.delete(key);});
    found=created;
  }
  entries.set(key,found);return found;
}
function trim() {
  for(const [key,item] of entries){if(total<=budget)return;if(item.users||!item.url)continue;URL.revokeObjectURL(item.url);entries.delete(key);total-=item.size;}
}
/** Fetch a blob before it is shown, e.g. the neighbouring steps of a slider. */
export const preloadBlob=(blob:BlobRef)=>void entry(blob,mimeOf(blob)).promise.catch(()=>undefined);

/** Blob storage is untyped HTTP; restore the recorded image MIME for the browser. */
export function useImageBlob(blob:BlobRef,enabled=true) {
  const mime=mimeOf(blob);
  const identity=JSON.stringify([blob.digest,mime]);
  const cached=()=>enabled?entries.get(`${blob.digest} ${mime}`)?.url:undefined;
  const [state,setState]=useState<{identity:string;url?:string;error?:Error}>(()=>({identity,url:cached()}));
  useEffect(()=>{
    if(!enabled){setState({identity});return;}
    const item=entry(blob,mime);let open=true;item.users++;
    if(item.url)setState({identity,url:item.url});
    else item.promise.then(url=>{if(open)setState({identity,url});},error=>{if(open)setState({identity,error});});
    return()=>{open=false;item.users--;trim();};
  },[blob.digest,mime,identity,enabled]);
  return {mime,...(state.identity===identity?state:{identity,url:cached()})};
}
