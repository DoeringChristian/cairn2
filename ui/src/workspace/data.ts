import { useEffect, useState } from 'react';
import { api, post, type Artifact, type ArtifactSummary, type RecordPage } from '../api';
import { useRefreshMode } from '../refresh';
import type { Observation } from './model';

export function useRuns(project:string,revision:number,enabled=true) {
  const [state,setState]=useState<{items:ArtifactSummary[];loading:boolean;error?:Error}>({items:[],loading:true});
  const paused=useRefreshMode();
  useEffect(()=>{
    if(!enabled){setState({items:[],loading:false});return;}
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    setState({items:[],loading:true});
    async function load(){
      try{const items:ArtifactSummary[]=[];let total=Infinity;
        while(items.length<total){const result=await api<{items:ArtifactSummary[];total:number}>(`/ui-api/artifacts?${new URLSearchParams({project,category:'runs',offset:String(items.length),limit:'100'})}`,{signal:controller.signal});total=result.total;items.push(...result.items);if(!result.items.length)break;}
        if(!controller.signal.aborted)setState({items,loading:false});
      }catch(e){if(!controller.signal.aborted)setState(s=>({...s,loading:false,error:e as Error}));}
      if(!controller.signal.aborted&&!paused)timer=setTimeout(()=>void load(),10000);
    }
    void load();return()=>{controller.abort();clearTimeout(timer);};
  },[project,revision,paused,enabled]);
  return state;
}
/** Complete, cutoff-pinned record pages through the existing API. Never sort a partial page. */
export function useObservations(project:string,runs:ArtifactSummary[],revision:number) {
  const [state,setState]=useState<{scope?:string;rows:Observation[];loading:boolean;error?:Error}>({rows:[],loading:false});
  const key=runs.map(r=>r.id).sort().join(','),scope=`${project}:${key}`,live=runs.some(r=>r.state!=='finished'),paused=useRefreshMode();
  useEffect(()=>{
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    setState({scope,rows:[],loading:!!key});
    async function load(){
      const rows:Observation[]=[];let bytes=0,next=0,failed=false;
      try{
        const ids=key?key.split(','):[];
        // Bound concurrent fetches, including when many runs are selected.
        await Promise.all(Array.from({length:Math.min(3,ids.length)},async()=>{
          while(!failed&&!controller.signal.aborted&&next<ids.length){const id=ids[next++];const [run]=await post<Artifact[]>('/v1/resolutions',{refs:[id],project},controller.signal);let cursor:string|null=null;
            do{const page:RecordPage=await post<RecordPage>('/v1/records/query',{ref:id,project,kind:'record',limit:1000,...(cursor?{cursor}:{})},controller.signal);
              bytes+=new TextEncoder().encode(JSON.stringify(page)).byteLength;rows.push(...page.items.map(row=>({...row,run})));cursor=page.next_cursor;
              if(rows.length>50000||bytes>32*1024*1024)throw new Error('This selection exceeds the workspace preview limit (50,000 records / 32 MiB). Select fewer runs. No partial results are plotted.');
            }while(cursor&&!failed&&!controller.signal.aborted);
          }
        }));
        if(!controller.signal.aborted)setState({scope,rows,loading:false});
      }catch(e){failed=true;if(!controller.signal.aborted)setState({scope,rows:[],loading:false,error:e as Error});}
      if(!controller.signal.aborted&&live&&!paused)timer=setTimeout(()=>void load(),5000);
    }
    void load();return()=>{controller.abort();clearTimeout(timer);};
  },[project,key,scope,revision,live,paused]);
  return state.scope===scope?state:{rows:[],loading:!!key};
}

/** Resolve one exact run independently of the project list's filters and version policy. */
export function useRun(project:string,id:string|undefined,revision:number){
  const identity=`${project}:${id||''}`;
  const [state,setState]=useState<{identity:string;run?:Artifact;loading:boolean;error?:Error}>({identity,loading:!!id});
  const paused=useRefreshMode();
  useEffect(()=>{
    if(!id){setState({identity,loading:false});return;}
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    setState({identity,loading:true});
    async function load(){
      try{
        const [run]=await post<Artifact[]>('/v1/resolutions',{refs:[id],project},controller.signal);
        if(!run||run.id!==id||run.project!==project||run.namespace!=='runs')throw new Error('Run not found in this project.');
        if(controller.signal.aborted)return;
        setState({identity,run,loading:false});
        if(run.state!=='finished'&&!paused)timer=setTimeout(()=>void load(),5000);
      }catch(error){if(!controller.signal.aborted)setState({identity,loading:false,error:error as Error});}
    }
    void load();return()=>{controller.abort();clearTimeout(timer);};
  },[project,id,identity,revision,paused]);
  return state.identity===identity?state:{identity,loading:!!id};
}
