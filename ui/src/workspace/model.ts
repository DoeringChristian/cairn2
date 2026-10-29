import { envelope, type Artifact, type ArtifactSummary, type Json, type RecordEntry } from '../api';

export type Kind = 'scalar' | 'image' | 'plotly';
export type Filter = { field:string; op:'eq'|'ne'|'lt'|'le'|'gt'|'ge'|'contains'|'exists'; value:string };
export type Order = { field:string; descending:boolean };
export type Aggregation = {center:'none'|'mean'|'median'|'min'|'max';band:'none'|'minmax'|'std'|'sem';showMembers:boolean};
export type Card = {
  aggregation?:Aggregation;
  id:string; title:string; kind:Kind; section:string; width:number; height:number;
  filters:Filter[]; filterMode:'all'|'any'; x:string; y:string; value:string;
  split:string[]; pointOrder:Order; lineOrder:'label'|'minimum'|'maximum';
  marks:'lines'|'markers'|'lines+markers'; logX:boolean; logY:boolean;
  mode:'gallery'|'compare'|'sequence'; axis:string; matching:'exact'|'previous'|'nearest';
  columns:number; pageSize:number; caption:string; fit:'contain'|'cover'; linkAxes:boolean;
};
export type Section = {id:string; title:string; collapsed:boolean};
export type Layout = {version:1; id:string; name:string; search:string; versions:'latest'|'all'; status:string; selected:string[]|null; sections:Section[]; cards:Card[]};
export type Observation = RecordEntry & {run:Artifact};
export type FieldInfo = {name:string; kinds:Set<string>};
export const palette=['#176b5b','#b56231','#637fa1','#a58a49','#8a709b'];
export const uid=()=>globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random().toString(36).slice(2)}`;
export const label=(run:ArtifactSummary)=>`${run.name} · ${run.number?'v'+run.number:run.state}`;
/** Bare field names are literal record keys; nested fields use JSON pointers. */
export function valueAt(row:Observation,field:string):Json|undefined {
  if(field==='@run')return row.artifact_id;
  if(field==='@name')return row.run.name;
  if(field==='@version')return row.run.number;
  if(field==='@ordinal')return row.ordinal;
  if(field==='@created')return row.created_at;
  const root=field.startsWith('@config/')?row.run.metadata.config:row.data;
  const path=field.startsWith('@config/')?field.slice(7):field;
  if(!path.startsWith('/'))return row.data[path];
  return path.slice(1).split('/').reduce<Json|undefined>((v,key)=>v&&typeof v==='object'&&!Array.isArray(v)?v[key.replace(/~1/g,'/').replace(/~0/g,'~')]:undefined,root);
}
export function compare(a:Json|undefined,b:Json|undefined) {
  if(a===b||(a==null&&b==null))return 0;
  if(a===undefined||a===null)return 1;
  if(b===undefined||b===null)return -1;
  if(typeof a!==typeof b)return typeof a< typeof b?-1:1;
  if(typeof a==='number'&&typeof b==='number')return a-b;
  if(typeof a==='object'&&typeof b==='object')return JSON.stringify(a).localeCompare(JSON.stringify(b));
  return String(a).localeCompare(String(b),undefined,{numeric:true});
}
export function sorted(rows:Observation[],order:Order) {
  return [...rows].sort((a,b)=>{
    const av=valueAt(a,order.field),bv=valueAt(b,order.field);
    const direction=av==null||bv==null?1:order.descending?-1:1;
    return compare(av,bv)*direction||a.artifact_id.localeCompare(b.artifact_id)||a.ordinal-b.ordinal;
  });
}
function same(a:Json,b:Json):boolean {
  if(a===b)return true;
  if(a===null||b===null||typeof a!==typeof b||typeof a!=='object'||typeof b!=='object')return false;
  if(Array.isArray(a)||Array.isArray(b))return Array.isArray(a)&&Array.isArray(b)&&a.length===b.length&&a.every((v,i)=>same(v,b[i]));
  return Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(k=>k in b&&same(a[k],b[k]));
}
export function matches(row:Observation,filter:Filter) {
  const value=valueAt(row,filter.field);
  if(filter.op==='exists')return value!==undefined&&value!==null;
  if(value===undefined)return false;
  let expected:Json;try{expected=JSON.parse(filter.value);}catch{expected=filter.value;}
  if(filter.op==='eq')return same(value,expected);
  if(filter.op==='ne')return !same(value,expected);
  if(value===null||expected===null)return false;
  if(filter.op==='contains')return Array.isArray(value)?value.some(v=>same(v,expected)):typeof value==='string'&&value.includes(String(expected));
  if(typeof value!==typeof expected)return false;
  const c=compare(value,expected);
  return filter.op==='lt'?c<0:filter.op==='le'?c<=0:filter.op==='gt'?c>0:c>=0;
}
export const filtered=(rows:Observation[],card:Card)=>rows.filter(row=>!card.filters.length||(card.filterMode==='all'?card.filters.every(f=>matches(row,f)):card.filters.some(f=>matches(row,f))));
export function groups(rows:Observation[],fields:string[]) {
  const result=new Map<string,{name:string;rows:Observation[]}>();
  for(const row of rows){const key=JSON.stringify(fields.map(f=>valueAt(row,f)??null));let group=result.get(key);if(!group){group={name:fields.map(f=>f==='@run'?label(row.run):String(valueAt(row,f)??'Missing')).join(' · ')||'All records',rows:[]};result.set(key,group);}group.rows.push(row);}
  return [...result.entries()].map(([key,group])=>({key,...group}));
}
export function discover(rows:Observation[]):FieldInfo[] {
  const fields=new Map<string,Set<string>>();
  const add=(name:string,value:Json)=>{
    const kind=envelope(value)?.format||typeof value;
    if(!fields.has(name))fields.set(name,new Set());fields.get(name)!.add(kind);
  };
  const visit=(value:Json,path:string)=>{add(path,value);if(value&&typeof value==='object'&&!Array.isArray(value)&&!envelope(value))for(const [key,v]of Object.entries(value))visit(v,`${path}/${key.replace(/~/g,'~0').replace(/\//g,'~1')}`);};
  for(const row of rows){for(const [key,value]of Object.entries(row.data)){add(key,value);if(value&&typeof value==='object'&&!Array.isArray(value)&&!envelope(value))for(const [k,v]of Object.entries(value))visit(v,`/${key.replace(/~/g,'~0').replace(/\//g,'~1')}/${k.replace(/~/g,'~0').replace(/\//g,'~1')}`);}if(row.run.metadata.config)visit(row.run.metadata.config,'@config');}
  for(const [name,kind]of [['@run','string'],['@name','string'],['@version','number'],['@ordinal','number'],['@created','string']])fields.set(name,new Set([kind]));
  return [...fields].map(([name,kinds])=>({name,kinds})).sort((a,b)=>a.name.localeCompare(b.name));
}
export function newCard(kind:Kind,section:string):Card {
  return {id:uid(),title:kind==='scalar'?'Loss over training':kind==='image'?'Image gallery':'Interactive figures',kind,section,width:6,height:kind==='scalar'?360:470,filters:[],filterMode:'all',x:'step',y:'loss',value:kind==='image'?'image':'figure',split:['@run'],pointOrder:{field:'step',descending:false},lineOrder:'label',marks:'lines',logX:false,logY:false,mode:kind==='scalar'?'gallery':'compare',axis:'step',matching:'exact',columns:2,pageSize:6,caption:'',fit:'contain',linkAxes:false};
}
export function defaultLayout(example=false):Layout {
  const sections=[{id:'curves',title:'Learning curves',collapsed:false},{id:'images',title:'Visual progress',collapsed:false},{id:'figures',title:'Interactive figures',collapsed:false}];
  const scalar=newCard('scalar','curves');
  const grouped={...newCard('scalar','curves'),title:'Loss across runs',split:[],aggregation:{center:'mean' as const,band:'minmax' as const,showMembers:false}};
  const second={...newCard('scalar','curves'),title:'Quality by camera angle',x:'camera_angle',y:'quality',marks:'markers' as const,pointOrder:{field:'camera_angle',descending:false}};
  const image={...newCard('image','images'),title:'Compare reconstructions',split:['@run','camera'],caption:'camera',width:6,columns:3,height:520};
  const gallery={...newCard('image','images'),title:'Browse by quality',mode:'gallery' as const,pointOrder:{field:'quality',descending:true},caption:'quality',width:6};
  const sequence={...newCard('image','images'),title:'Follow a camera through time',mode:'sequence' as const,split:['@run','camera'],axis:'elapsed',caption:'elapsed',width:6};
  const plotly={...newCard('plotly','figures'),title:'Compare fitted curves',columns:3,width:6,height:560};
  const figures={...newCard('plotly','figures'),title:'Figure gallery',mode:'gallery' as const,width:6,columns:3,height:450};
  return uniformSections({version:1,id:uid(),name:example?'Training cards demo':'Overview',search:example?'workspace-demo/':'',versions:'latest',status:'',selected:null,sections,cards:example?[scalar,second,grouped,image,gallery,sequence,plotly,figures]:[scalar,image,plotly]});
}

export const cardWidths=[3,4,6] as const;
export const snapWidth=(width:number)=>cardWidths.reduce<number>((best,value)=>Math.abs(value-width)<Math.abs(best-width)?value:best,6);
/** Keep the v1 layout shape. The first card determines a section's shared size. */
export function uniformSections(layout:Layout):Layout {
  const sizes=new Map<string,Pick<Card,'width'|'height'>>();
  return {...layout,cards:layout.cards.map(card=>{
    let size=sizes.get(card.section);
    if(!size){size={width:snapWidth(card.width),height:card.height};sizes.set(card.section,size);}
    return card.width===size.width&&card.height===size.height?card:{...card,...size};
  })};
}
