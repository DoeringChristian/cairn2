import { useEffect, useState } from 'react';

export async function copyText(value:string) {
  try {if(navigator.clipboard){await navigator.clipboard.writeText(value);return;}} catch { /* HTTP preview or denied clipboard: try the browser fallback. */ }
  const field=document.createElement('textarea'),focused=document.activeElement;
  field.value=value;field.style.cssText='position:fixed;opacity:0';document.body.append(field);field.select();
  try {if(!document.execCommand('copy'))throw new Error('Copy failed');}
  finally {field.remove();if(focused instanceof HTMLElement)focused.focus({preventScroll:true});}
}
export function useCopyText(value:string) {
  const [copied,setCopied]=useState(false),[error,setError]=useState(false);
  useEffect(()=>{if(!copied)return;const timer=setTimeout(()=>setCopied(false),1400);return()=>clearTimeout(timer);},[copied]);
  return {copied,error,copy:async()=>{try{await copyText(value);setError(false);setCopied(true);}catch{setError(true);}}};
}
