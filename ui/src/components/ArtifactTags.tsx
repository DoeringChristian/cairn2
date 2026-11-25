import { invalidateLists } from '../listCache';
import { useState } from 'react';
import { Plus, X } from 'lucide-react';
import { literal, operate, type Artifact, type Json } from '../api';
import { ErrorNote } from '../components';

const text=(key:string,value:Json)=>value===null?key:`${key}=${value}`;
/** Tags of an artifact, which have a value or none: paper, topic=mesh. */
export function ArtifactTags({artifact,onChange,compact=false,onTagClick}: {artifact:Artifact;onChange:()=>void;compact?:boolean;onTagClick?:(tag:string)=>void}) {
  const [draft,setDraft]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState<Error>();
  async function save(change:{set?:Record<string,Json>;remove?:string[]}){
    setBusy(true);setError(undefined);
    try{await operate('label',{artifact:artifact.id,...change});invalidateLists();setDraft('');onChange();}
    catch(e){setError(e as Error);}
    finally{setBusy(false);}
  }
  function add(){
    const given=draft.trim(),at=given.indexOf('='),key=at<0?given:given.slice(0,at);
    if(key.startsWith('@'))setError(new Error('Tags do not start with @.'));
    else void save({set:{[key]:at<0?null:literal(given.slice(at+1))}});
  }
  return <div className={compact?"artifact-tags compact":"artifact-tags"}><div className="tag-row" aria-label="Artifact tags">{Object.entries(artifact.tags).map(([key,value])=><span className="tag-chip" key={key}>{onTagClick?<button type="button" aria-label={`Filter by tag ${key}`} onClick={()=>onTagClick(key)}>{text(key,value)}</button>:text(key,value)}<button type="button" aria-label={`Remove tag ${key}`} disabled={busy} onClick={()=>void save({remove:[key]})}><X size={12}/></button></span>)}
    <form onSubmit={e=>{e.preventDefault();add();}}><input aria-label="New tag" placeholder="Add tag…" value={draft} disabled={busy} onChange={e=>setDraft(e.target.value)}/><button type="submit" aria-label="Add tag" disabled={busy||!draft.trim()}><Plus size={15}/></button></form>
  </div>{error&&<ErrorNote error={error}/>}</div>;
}
