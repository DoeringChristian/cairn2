import { useRefresh } from '../refresh';
import { useEffect, useState } from 'react';
import { print, records as read, useLoaded, operate, type RecordEntry } from '../api';
import { Button, DataTable, ErrorNote, Hint, JsonBlock, Loading, MetricCard, MetricGrid, RecordCharts, Section, Select } from '../components';
const cell=(value:unknown)=>value===undefined?'—':value!==null&&typeof value==='object'?<code>{print(value).slice(0,60)}</code>:String(value);
export function RecordsPanel({artifact, refresh, overview=false, live=false}: {artifact: string; refresh: number; overview?: boolean; live?: boolean}) {
  const pulse=useRefresh(live);
  const [records,setRecords]=useState<RecordEntry[]>([]),[more,setMore]=useState(false),[total,setTotal]=useState(0);
  const [error,setError]=useState<Error>(),[loading,setLoading]=useState(true),[having,setHaving]=useState('');
  const [expanded,setExpanded]=useState<number>();
  const known=useLoaded(async signal=>(await operate<{keys:string[]}>('keys',{artifact},signal)).keys,`keys:${artifact}`,refresh+pulse);
  async function load(after:number,limit:number,signal?:AbortSignal){
    setLoading(true);setError(undefined);
    try{const page=await read({artifact,after,limit,having},signal);if(signal?.aborted)return;setRecords(previous=>after?[...previous,...page.items]:page.items);setMore(page.more);setTotal(page.total);}
    catch(e){if(!signal?.aborted)setError(e as Error);}
    finally{if(!signal?.aborted)setLoading(false);}
  }
  useEffect(()=>{
    const controller=new AbortController();void load(0,overview?1000:Math.max(200,Math.min(10000,records.length)),controller.signal);
    return ()=>controller.abort();
  },[artifact,having,refresh,pulse]);
  const next=()=>void load(records.at(-1)?.ordinal||0,overview?1000:200);
  const metrics=records.map(row=>({...row,data:Object.fromEntries(Object.entries(row.data).filter(([key])=>!key.startsWith('_')))})).filter(row=>Object.keys(row.data).length);
  if(overview) return <Section title="Metric trends">
    {error&&<ErrorNote error={error}/>}{loading&&!records.length&&<Loading/>}
    <MetricGrid>{Object.entries(metrics.at(-1)?.data||{}).filter(([key,value])=>key!=='step'&&typeof value==='number').slice(0,3).map(([key,value])=><MetricCard key={key} label={key} value={Number(value).toPrecision(4)} note="Last loaded sample"/>)}</MetricGrid>
    <RecordCharts records={metrics}/>{more&&<div className="pagination"><Hint>Showing the first {records.length} of {total} records.</Hint><Button disabled={loading} onClick={next}>Load more</Button></div>}
  </Section>;
  const keys=[...new Set(records.flatMap(row=>Object.keys(row.data)))].filter(key=>key!=='_time').slice(0,8);
  return <Section title="Records"><Select label="Records with" value={having} onChange={value=>{setRecords([]);setHaving(value);}} choices={[{value:'',label:'Any key'},...(known.data||[]).map(value=>({value,label:value}))]}/>
    {error&&<ErrorNote error={error}/>}
    {records.length>0&&<DataTable head={['#',...keys,'Details']}>{records.map(row=><tr key={row.ordinal}><td className="mono">{row.ordinal}</td>{keys.map(key=><td className="record-cell" key={key}>{cell(row.data[key])}</td>)}<td><Button small onClick={()=>setExpanded(expanded===row.ordinal?undefined:row.ordinal)}>Inspect {row.ordinal}</Button></td></tr>)}</DataTable>}
    {expanded!==undefined&&<Section title={`Record ${expanded}`} actions={<Button small onClick={()=>setExpanded(undefined)}>Close</Button>}><JsonBlock value={records.find(row=>row.ordinal===expanded)?.data}/></Section>}
    {loading&&<Loading/>}{!loading&&!error&&!records.length&&<Hint>No records yet.</Hint>}
    <div className="pagination"><Hint>{records.length} records loaded · {total} in all</Hint>{more&&<Button disabled={loading} onClick={next}>Load more records</Button>}</div>
  </Section>;
}
