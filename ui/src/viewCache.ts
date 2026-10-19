/** Bounded session cache for rendered views; validated against repository identity at login. */
const key='cairn:views:v1', scopeKey='cairn:views-repository';
const budget=2*1024*1024;
const cache=new Map<string,unknown>();
try {const raw=sessionStorage.getItem(key);if(raw&&raw.length*2<=budget){const entries=JSON.parse(raw);if(Array.isArray(entries))for(const entry of entries.slice(-20))if(Array.isArray(entry)&&typeof entry[0]==='string')cache.set(entry[0],entry[1]);}} catch {/* Memory cache remains available. */}
function persist(){
  // Persistence has a smaller budget than memory. Never evict the live document
  // merely because it cannot fit in sessionStorage.
  const entries: [string,unknown][]=[];
  let size=2;
  for(const entry of [...cache].reverse()){
    const length=JSON.stringify(entry).length*2+2;
    if(size+length>budget)continue;
    entries.unshift(entry);size+=length;
  }
  try{sessionStorage.setItem(key,JSON.stringify(entries));}catch{/* Storage disabled or full. */}
}
export function readView<T>(path:string):T|undefined{return cache.get(path) as T|undefined;}
export function writeView<T>(path:string,value:T){cache.delete(path);cache.set(path,value);while(cache.size>20)cache.delete(cache.keys().next().value!);persist();}
export function clearViews(){cache.clear();persist();}
export function scopeViews(repository:string){try{if(sessionStorage.getItem(scopeKey)!==repository)clearViews();sessionStorage.setItem(scopeKey,repository);}catch{clearViews();}}
