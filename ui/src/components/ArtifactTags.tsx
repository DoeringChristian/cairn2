import { invalidateLists } from '../listCache';
import { useEffect, useState } from 'react';
import { Plus, X } from 'lucide-react';
import { api, type Artifact } from '../api';
import { ErrorNote } from '../components';

export const artifactTags=(artifact:Pick<Artifact, "id" | "annotations" | "annotations_etag">):string[]=>Array.isArray(artifact.annotations?.tags)?artifact.annotations.tags.filter((tag):tag is string=>typeof tag==='string'):[];
/** With scopes, "All versions" edits the tags shared by every version of the name. */
export function ArtifactTags({artifact,onChange,compact=false,scopes=false,onTagClick}: {artifact:Pick<Artifact, "id" | "annotations" | "annotations_etag">;onChange:()=>void;compact?:boolean;scopes?:boolean;onTagClick?:(tag:string)=>void}) {
  const [draft,setDraft]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState<Error>();
  const [all,setAll]=useState(false),[shared,setShared]=useState<string[]>();
  useEffect(()=>{if(!all)return;const c=new AbortController();api<{name:Artifact['annotations']}>(`/v1/artifacts/${artifact.id}/annotations`,{signal:c.signal}).then(layers=>setShared(artifactTags({...artifact,annotations:layers.name}))).catch(e=>{if(!c.signal.aborted)setError(e);});return()=>c.abort();},[all,artifact.id,artifact.annotations_etag]);
  const tags=all?shared??[]:artifactTags(artifact);
  async function save(next:string[]){
    setBusy(true);setError(undefined);
    try{await api(`/ui-api/artifacts/${artifact.id}/tags`,{method:'PATCH',body:JSON.stringify({tags:next,expected_etag:artifact.annotations_etag,scope:all?'name':'artifact'})});invalidateLists();setDraft('');onChange();}
    catch(e){setError(e as Error);onChange();}
    finally{setBusy(false);}
  }
  return <div className={compact?"artifact-tags compact":"artifact-tags"}><div className="tag-row" aria-label="Artifact tags">{tags.map(tag=><span className="tag-chip" key={tag}>{onTagClick?<button type="button" aria-label={`Filter by tag ${tag}`} onClick={()=>onTagClick(tag)}>{tag}</button>:tag}<button type="button" aria-label={`Remove tag ${tag}`} disabled={busy} onClick={()=>void save(tags.filter(t=>t!==tag))}><X size={12}/></button></span>)}
    <form onSubmit={e=>{e.preventDefault();const tag=draft.trim();if(tag&&!tags.includes(tag))void save([...tags,tag]);}}><input aria-label="New tag" placeholder="Add tag…" value={draft} disabled={busy} onChange={e=>setDraft(e.target.value)}/><button type="submit" aria-label="Add tag" disabled={busy||!draft.trim()||tags.includes(draft.trim())}><Plus size={15}/></button></form>{scopes&&<button type="button" className="tag-chip" aria-pressed={all} onClick={()=>setAll(value=>!value)}>All versions</button>}
  </div>{error&&<ErrorNote error={error}/>}</div>;
}
