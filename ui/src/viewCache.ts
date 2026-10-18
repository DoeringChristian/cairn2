/** Bounded session cache for rendered views; validated against repository identity at login. */
const key='cairn:views:v1', scopeKey='cairn:views-repository';
const budget=2*1024*1024;
const cache=new Map<string,unknown>();
try {const raw=sessionStorage.getItem(key);if(raw&&raw.length*2<=budget){const entries=JSON.parse(raw);if(Array.isArray(entries))for(const entry of entries.slice(-20))if(Array.isArray(entry)&&typeof entry[0]==='string')cache.set(entry[0],entry[1]);}} catch {/* Memory cache remains available. */}
function persist(){while(cache.size>20)cache.delete(cache.keys().next().value!);let raw=JSON.stringify([...cache]);while(raw.length*2>budget&&cache.size){cache.delete(cache.keys().next().value!);raw=JSON.stringify([...cache]);}try{sessionStorage.setItem(key,raw);}catch{/* Storage disabled or full. */}}
export function readView<T>(path:string):T|undefined{return cache.get(path) as T|undefined;}
export function writeView<T>(path:string,value:T){cache.delete(path);cache.set(path,value);persist();}
export function clearViews(){cache.clear();persist();}
export function scopeViews(repository:string){try{if(sessionStorage.getItem(scopeKey)!==repository)clearViews();sessionStorage.setItem(scopeKey,repository);}catch{clearViews();}}
