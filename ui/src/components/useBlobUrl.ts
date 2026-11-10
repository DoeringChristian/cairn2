import { useEffect, useState } from 'react';
import type { BlobRef } from '../api';

/** Blob storage is untyped HTTP; restore the recorded image MIME for the browser. */
export function useImageBlob(blob:BlobRef,enabled=true) {
  const mime=blob.media_type||(blob.name?.toLowerCase().endsWith('.svg')?'image/svg+xml':'image/png');
  const identity=JSON.stringify([blob.digest,mime]);
  const [state,setState]=useState<{identity:string;url?:string;error?:Error}>({identity});
  useEffect(()=>{
    setState({identity});if(!enabled)return;
    const controller=new AbortController();let url:string|undefined;
    void fetch(`/v1/blobs/${blob.digest}`,{signal:controller.signal}).then(async response=>{
      if(!response.ok)throw new Error(`Could not load image (${response.status}).`);
      const data=await response.blob();if(controller.signal.aborted)return;
      url=URL.createObjectURL(new Blob([data],{type:mime}));setState({identity,url});
    }).catch(error=>{if(!controller.signal.aborted)setState({identity,error});});
    return()=>{controller.abort();if(url)URL.revokeObjectURL(url);};
  },[blob.digest,mime,identity,enabled]);
  return {mime,...(state.identity===identity?state:{identity})};
}
