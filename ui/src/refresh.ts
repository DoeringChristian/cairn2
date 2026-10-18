import { useEffect, useState } from 'react';
const eventName='cairn:refresh-mode';
let paused=false;
export function setRefreshPaused(value:boolean){paused=value;window.dispatchEvent(new Event(eventName));}
export function useRefreshMode(){const [value,setValue]=useState(paused);useEffect(()=>{const changed=()=>setValue(paused);window.addEventListener(eventName,changed);return()=>window.removeEventListener(eventName,changed);},[]);return value;}
/** Poll only visible pages. Exact immutable versions pass enabled=false. */
export function useRefresh(enabled=true){const [tick,setTick]=useState(0);const stopped=useRefreshMode();useEffect(()=>{if(!enabled||stopped)return;const refresh=()=>{if(document.visibilityState==='visible')setTick(n=>n+1);};const timer=window.setInterval(refresh,5000);document.addEventListener('visibilitychange',refresh);return()=>{clearInterval(timer);document.removeEventListener('visibilitychange',refresh);};},[enabled,stopped]);return tick;}
