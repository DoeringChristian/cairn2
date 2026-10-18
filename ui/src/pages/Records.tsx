import { useRefresh } from '../refresh';
import { useEffect, useState, type FormEvent } from 'react';
import { post, type RecordPage, type RecordEntry } from '../api';
import { Button, DataTable, ErrorNote, Field, Hint, JsonBlock, Loading, MetricCard, MetricGrid, RecordCharts, Section, Select } from '../components';
export function RecordsPanel({reference, project, run, refresh, overview=false, live=false}: {reference: string; project: string; run: boolean; refresh: number; overview?: boolean; live?: boolean}) {
  const pulse=useRefresh(live);
  const [records,setRecords]=useState<RecordEntry[]>([]),[cursor,setCursor]=useState<string|null>(null),[cutoff,setCutoff]=useState(0);
  const [error,setError]=useState<Error>(),[loading,setLoading]=useState(true),[kind,setKind]=useState('record');
  const [pointer,setPointer]=useState(''),[op,setOp]=useState('lt'),[value,setValue]=useState(''),[filter,setFilter]=useState<object|null>(null);
  const [expanded,setExpanded]=useState<number>();
  useEffect(()=>{
    const controller=new AbortController(); setError(undefined);setLoading(true);
    post<RecordPage>('/v1/records/query',{ref:reference,project,kind,where:filter,limit:Math.max(200,Math.min(10000,records.length))},controller.signal).then(page=>{setRecords(page.items);setCursor(page.next_cursor);setCutoff(page.cutoff);}).catch(e=>{if(!controller.signal.aborted)setError(e);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[reference,project,kind,filter,refresh,pulse]);
  async function more(){setLoading(true);setError(undefined);try{const page=await post<RecordPage>('/v1/records/query',{ref:reference,project,kind,where:filter,limit:200,cursor});setRecords(previous=>[...previous,...page.items]);setCursor(page.next_cursor);}catch(e){setError(e as Error);}finally{setLoading(false);}}
  function apply(event:FormEvent){event.preventDefault();setError(undefined);try {setFilter(pointer ? {path:pointer,op,...(['exists','is_null'].includes(op)?{}:{value:JSON.parse(value)})}:null);}catch{setError(new Error('Enter a JSON value, such as 0.5, true, or "validation".'));}}
  const keys=[...new Set(records.flatMap(r=>Object.keys(r.data)))].slice(0,8);
  if(overview) return <Section title="Metric trends">
    {error&&<ErrorNote error={error}/>}{loading&&!records.length&&<Loading/>}
    <MetricGrid>{Object.entries(records.at(-1)?.data||{}).filter(([key,value])=>key!=='step'&&typeof value==='number').slice(0,3).map(([key,value])=><MetricCard key={key} label={key} value={Number(value).toPrecision(4)} note="Last loaded sample"/>)}</MetricGrid>
    <RecordCharts records={records}/><div className="pagination"><Hint>{cursor?`Showing ${records.length} records from the start of this run.`:`All ${records.length} metric records loaded.`}</Hint>{cursor&&<Button disabled={loading} onClick={more}>Load more metrics</Button>}</div>
  </Section>;
  return <><Section title="Record explorer"><form className="record-filter" onSubmit={apply}>
    <Select label="Record kind" value={kind} onChange={setKind} choices={['record','metadata','capture','dependency'].map(value=>({value,label:value[0].toUpperCase()+value.slice(1)}))}/>
    <Field label="Field (JSON pointer)" placeholder="/loss" value={pointer} onChange={e=>setPointer(e.target.value)}/>
    <Select label="Comparison" value={op} onChange={setOp} choices={['eq','ne','lt','le','gt','ge','exists','is_null'].map(value=>({value,label:value}))}/>
    <Field label="JSON value" placeholder="0.5" disabled={['exists','is_null'].includes(op)} value={value} onChange={e=>setValue(e.target.value)}/>
    <Button type="submit">Apply filter</Button>{filter && <Button onClick={()=>{setFilter(null);setPointer('');setValue('');}}>Clear</Button>}</form>
    {error&&<ErrorNote error={error}/>}{run && kind==='record' && records.length>0 && <><RecordCharts records={records}/><Hint>Charts show the {records.length} loaded records{filter?' matching the filter':''}.</Hint></>}
    {records.length>0&&<DataTable head={['#',...keys,'Details']}>{records.map(row=><tr key={row.ordinal}><td className="mono">{row.ordinal}</td>{keys.map(key=><td className="record-cell" key={key}>{typeof row.data[key]==='object'?<code>{Array.isArray(row.data[key])?'Array':'Object'}</code>:String(row.data[key]??'—')}</td>)}<td><Button small onClick={()=>setExpanded(expanded===row.ordinal?undefined:row.ordinal)}>Inspect {row.ordinal}</Button></td></tr>)}</DataTable>}
    {expanded!==undefined&&<Section title={`Record ${expanded}`} actions={<Button small onClick={()=>setExpanded(undefined)}>Close</Button>}><JsonBlock value={records.find(r=>r.ordinal===expanded)}/></Section>}
    {loading&&<Loading/>}{!loading&&!error&&!records.length&&<Hint>No records match this view.</Hint>}
    <div className="pagination"><Hint>{records.length} records loaded · acknowledged cutoff {cutoff}</Hint>{cursor&&<Button disabled={loading} onClick={more}>Load more records</Button>}</div>
  </Section></>;
}
