import { useState } from 'react';
import { ChartLine, Image, PanelsTopLeft, Plus, Trash2 } from 'lucide-react';
import { Button, ActionIcon } from '../components/Button';
import { Select } from '../components/Select';
import { CardContent } from './CardContent';
import { WorkspaceDialog } from './Dialog';
import { newCard, type Card, type FieldInfo, type Filter, type Kind, type Observation } from './model';
export function FieldPicker({label,value,onChange,fields}:{label:string;value:string;onChange:(value:string)=>void;fields:FieldInfo[]}) {
  const choices=[{value:'',label:'Choose a field…'},...fields.map(field=>({value:field.name,label:field.name,note:[...field.kinds].join(', ')}))];
  if(value&&!fields.some(field=>field.name===value))choices.push({value,label:value,note:'Not present in the selected records'});
  return <div className="workspace-field"><span>{label}</span><Select label={label} value={value} choices={choices} onChange={onChange}/></div>;
}
function Choice<T extends string>({label,value,options,onChange}:{label:string;value:T;options:[T,string][];onChange:(value:T)=>void}) {
  return <div className="workspace-field"><span>{label}</span><Select label={label} value={value} choices={options.map(([value,label])=>({value,label}))} onChange={value=>onChange(value as T)}/></div>;
}
export function CardEditor({initial,creating,fields,rows,onApply,onClose}:{initial:Card;creating:boolean;fields:FieldInfo[];rows:Observation[];onApply:(card:Card)=>void;onClose:()=>void}) {
  const [draft,setDraft]=useState(initial),[tab,setTab]=useState('Data');
  const isValid=(card:Card)=>!!card.title.trim()&&(card.kind==='scalar'?!!card.x&&!!card.y:!!card.value&&(card.mode==='gallery'||!!card.axis))&&card.filters.every(f=>!!f.field);
  const patch=(value:Partial<Card>)=>{const next={...draft,...value};setDraft(next);if(!creating&&isValid(next))onApply(next);};
  const scalar=draft.kind==='scalar',numeric=fields.filter(f=>f.kinds.has('number')),media=fields.filter(f=>draft.kind==='image'?f.kinds.has('image')||f.kinds.has('figure/svg'):f.kinds.has('figure/plotly'));
  const updateFilter=(index:number,value:Partial<Filter>)=>patch({filters:draft.filters.map((f,i)=>i===index?{...f,...value}:f)});
  const valid=isValid(draft);
  const aggregation=draft.aggregation??{center:'none',band:'minmax',showMembers:false};
  return <WorkspaceDialog title={creating?'Create a card':'Card settings'} onClose={onClose} wide>
    <div className="workspace-editor">
      <div className="workspace-editor-controls">
        {creating&&<div className="workspace-type-picker" aria-label="Card type">{([['scalar','Scalar',ChartLine],['image','Image',Image],['plotly','Plotly',PanelsTopLeft]] as const).map(([kind,title,Icon])=><button key={kind} aria-pressed={draft.kind===kind} onClick={()=>setDraft({...newCard(kind as Kind,draft.section),id:draft.id,width:draft.width,height:draft.height})}><Icon size={19}/>{title}</button>)}</div>}
        <label className="workspace-field"><span>Card title</span><input value={draft.title} onChange={e=>patch({title:e.target.value})}/></label>
        <div className="tabs" role="tablist" aria-label="Card settings">{['Data','Mapping','Appearance'].map(value=><button role="tab" key={value} aria-selected={tab===value} onClick={()=>setTab(value)}>{value}</button>)}</div>
        {tab==='Data'&&<div className="workspace-settings-panel">
          <p className="workspace-setting-note">Filters apply to this card’s visible runs.</p>
          {!scalar&&<FieldPicker label={draft.kind==='image'?'Image field':'Plotly field'} value={draft.value} fields={media} onChange={value=>patch({value})}/>}
          <h3 className="workspace-settings-heading">Record filters</h3><p className="workspace-setting-note">Choose which records this card displays.</p>
          {draft.filters.length>1&&<Choice label="Combine filter conditions" value={draft.filterMode} options={[['all','All conditions (AND)'],['any','Any condition (OR)']]} onChange={filterMode=>patch({filterMode})}/>}
          {draft.filters.map((filter,index)=><div className="workspace-filter" key={index}>
            <FieldPicker label={`Filter field ${index+1}`} value={filter.field} fields={fields} onChange={field=>updateFilter(index,{field})}/>
            <Choice label={`Operator ${index+1}`} value={filter.op} options={['eq','ne','lt','le','gt','ge','contains','exists'].map((op,i)=>[op as Filter['op'],['equals','does not equal','less than','at most','greater than','at least','contains','exists'][i]])} onChange={op=>updateFilter(index,{op})}/>
            {filter.op!=='exists'&&<label className="workspace-field"><span>Value</span><input value={filter.value} placeholder="Text, number, or JSON" onChange={e=>updateFilter(index,{value:e.target.value})}/></label>}
            <ActionIcon label={`Remove filter ${index+1}`} onClick={()=>patch({filters:draft.filters.filter((_,i)=>i!==index)})}><Trash2 size={15}/></ActionIcon>
          </div>)}
          <Button small onClick={()=>patch({filters:[...draft.filters,{field:'',op:'eq',value:''}]})}><Plus size={14}/> Add filter</Button>
        </div>}
        {tab==='Mapping'&&<div className="workspace-settings-panel">
          {scalar?<><FieldPicker label="X field" value={draft.x} fields={numeric} onChange={x=>patch({x})}/><FieldPicker label="Y field" value={draft.y} fields={numeric} onChange={y=>patch({y})}/></>:<>
            <Choice label="View" value={draft.mode} options={[['gallery','Gallery · ordered records'],['compare','Compare · one group per tile'],['sequence','Sequence · one group with a slider']]} onChange={mode=>patch({mode})}/>
            {draft.mode!=='gallery'&&<><FieldPicker label="Slider axis" value={draft.axis} fields={fields.filter(f=>!['image','figure/plotly'].some(type=>f.kinds.has(type)))} onChange={axis=>patch({axis})}/><Choice label="Match observations" value={draft.matching} options={[['exact','Exact value'],['previous','Previous available'],['nearest','Nearest (numeric axes)']]} onChange={matching=>patch({matching})}/><p className="workspace-setting-note">Every tile shows its actual axis value. Missing matches remain empty; observations are never interpolated.</p></>}
          </>}
          <div className="workspace-grouping"><span className="workspace-label">Group by</span><div className="workspace-grouping-fields">{draft.split.map(field=><button className="workspace-group-chip" key={field} onClick={()=>patch({split:draft.split.filter(f=>f!==field)})} aria-label={`Remove grouping ${field}`}>{field==='@run'?'Run':field} ×</button>)}{!draft.split.length&&<span className="workspace-setting-note">All records</span>}<Select label="Add grouping field" value="" choices={[{value:'',label:'Add field…'},...fields.filter(field=>!draft.split.includes(field.name)).map(field=>({value:field.name,label:field.name==='@run'?'Run':field.name}))]} onChange={field=>{if(field)patch({split:[...draft.split,field]});}}/></div></div>
          {scalar&&<><Choice label="Group center" value={aggregation.center} options={[["none","None · keep every observation"],["mean","Mean"],["median","Median"],["min","Minimum"],["max","Maximum"]]} onChange={center=>patch({aggregation:{...aggregation,center}})}/>{aggregation.center!=='none'&&<><Choice label="Group band" value={aggregation.band} options={[["none","None"],["minmax","Min – max"],["std","± standard deviation"],["sem","± standard error"]]} onChange={band=>patch({aggregation:{...aggregation,band}})}/><label><input type="checkbox" checked={aggregation.showMembers} onChange={e=>patch({aggregation:{...aggregation,showMembers:e.target.checked}})}/> Show member runs</label><p className="workspace-setting-note">{draft.split.includes('@run')?'Remove @run from grouping to combine runs. ':''}Each run contributes its latest observation at or before X, within its recorded range. At duplicate X values, the last record wins. Bands summarize those member values; no extrapolation.</p></>}</>}
          <FieldPicker label={scalar?'Point order':'Gallery / duplicate order'} value={draft.pointOrder.field} fields={fields} onChange={field=>patch({pointOrder:{...draft.pointOrder,field}})}/>
          <Choice label="Direction" value={draft.pointOrder.descending?'descending':'ascending'} options={[['ascending','Ascending'],['descending','Descending']]} onChange={direction=>patch({pointOrder:{...draft.pointOrder,descending:direction==='descending'}})}/>
          {scalar?<Choice label="Line order" value={draft.lineOrder} options={[['label','Label'],['minimum','Minimum Y'],['maximum','Maximum Y']]} onChange={lineOrder=>patch({lineOrder})}/>:<FieldPicker label="Caption field (optional)" value={draft.caption} fields={fields} onChange={caption=>patch({caption})}/>}
        </div>}
        {tab==='Appearance'&&<div className="workspace-settings-panel">
          {scalar?<><Choice label="Draw" value={draft.marks} options={[['lines','Lines'],['markers','Points'],['lines+markers','Lines and points']]} onChange={marks=>patch({marks})}/><label><input type="checkbox" checked={draft.logX} onChange={e=>patch({logX:e.target.checked})}/> Logarithmic X</label><label><input type="checkbox" checked={draft.logY} onChange={e=>patch({logY:e.target.checked})}/> Logarithmic Y</label></>:<>
            <label className="workspace-field"><span>Columns</span><input type="number" min={1} max={6} value={draft.columns} onChange={e=>patch({columns:Math.max(1,Math.min(6,Number(e.target.value)||1))})}/></label>
            <label className="workspace-field"><span>Tiles per page</span><input type="number" min={1} max={24} value={draft.pageSize} onChange={e=>patch({pageSize:Math.max(1,Math.min(24,Number(e.target.value)||1))})}/></label>
            {draft.kind==='image'?<Choice label="Image fit" value={draft.fit} options={[['contain','Fit whole image'],['cover','Fill tile (crop)']]} onChange={fit=>patch({fit})}/>:<label><input type="checkbox" checked={draft.linkAxes} onChange={e=>patch({linkAxes:e.target.checked})}/> Link 2D axes across figures with the same units</label>}
          </>}
        </div>}
      </div>
      <div className="workspace-editor-preview"><span className="eyebrow">Live preview</span><article className="workspace-card" style={{height:Math.min(620,draft.height)}}><header><h3>{draft.title||'Untitled card'}</h3></header><CardContent card={draft} rows={rows}/></article><p className="workspace-setting-note">{creating?'Preview uses the visible runs. Add the card when ready.':valid?'Changes apply and save automatically. Undo in the workspace restores the previous settings.':'Complete the required fields. The card keeps its last valid settings.'}</p></div>
    </div>
    <footer className="workspace-dialog-footer">{creating?<><Button onClick={onClose}>Cancel</Button><Button primary disabled={!valid} onClick={()=>onApply(draft)}>Add card</Button></>:<Button onClick={onClose}>Done</Button>}</footer>
  </WorkspaceDialog>;
}
