/** Stable identity colors: never indexed by the currently visible or filtered runs. */
export function identityColor(key:string):string {
  let hash=2166136261;
  for(const char of key){hash^=char.codePointAt(0)!;hash=Math.imul(hash,16777619);}
  // Avalanche the bits so similar identifiers also spread across the color wheel.
  hash^=hash>>>16;hash=Math.imul(hash,0x85ebca6b);hash^=hash>>>13;
  hash=Math.imul(hash,0xc2b2ae35);hash^=hash>>>16;
  const value=hash>>>0;
  return `hsl(${value%360}, ${42+(value>>>9)%16}%, ${34+(value>>>17)%9}%)`;
}
export const runColor=(id:string)=>identityColor(`run:${id}`);
