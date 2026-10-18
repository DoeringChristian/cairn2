import { useState } from 'react';
import { Plus, X } from 'lucide-react';
import { api, type Artifact } from '../api';
import { ErrorNote } from '../components';

export const artifactTags=(artifact:Artifact):string[]=>Array.isArray(artifact.annotations?.tags)?artifact.annotations.tags.filter((tag):tag is string=>typeof tag==='string'):[];
export function ArtifactTags({artifact,onChange,compact=false,onTagClick}: {artifact:Artifact;onChange:()=>void;compact?:boolean;onTagClick?:(tag:string)=>void}) {
  const [draft,setDraft]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState<Error>();
  const tags=artifactTags(artifact);
  async function save(next:string[]){
    setBusy(true);setError(undefined);
    try{await api(`/ui-api/artifacts/${artifact.id}/tags`,{method:'PATCH',body:JSON.stringify({tags:next,expected_etag:artifact.annotations_etag})});setDraft('');onChange();}
    catch(e){setError(e as Error);onChange();}
    finally{setBusy(false);}
  }
  return <div className={compact?"artifact-tags compact":"artifact-tags"}><div className="tag-row" aria-label="Artifact tags">{tags.map(tag=><span className="tag-chip" key={tag}>{onTagClick?<button type="button" aria-label={`Filter by tag ${tag}`} onClick={()=>onTagClick(tag)}>{tag}</button>:tag}<button type="button" aria-label={`Remove tag ${tag}`} disabled={busy} onClick={()=>void save(tags.filter(t=>t!==tag))}><X size={12}/></button></span>)}
    <form onSubmit={e=>{e.preventDefault();const tag=draft.trim();if(tag&&!tags.includes(tag))void save([...tags,tag]);}}><input aria-label="New tag" placeholder="Add tag…" value={draft} disabled={busy} onChange={e=>setDraft(e.target.value)}/><button type="submit" aria-label="Add tag" disabled={busy||!draft.trim()||tags.includes(draft.trim())}><Plus size={15}/></button></form>
  </div>{error&&<ErrorNote error={error}/>}</div>;
}
