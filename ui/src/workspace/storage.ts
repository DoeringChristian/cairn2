import { newCard, type Layout } from './model';
export type SavedWorkspace={version:1;active:string;layouts:Layout[]};
export const storageKey=(repository:string,project:string)=>`cairn:workspace:v1:${encodeURIComponent(repository)}:${encodeURIComponent(project)}`;
/** Validate local data before it can affect a query or a renderer. Unknown versions are retained untouched. */
export function readSaved(key:string):SavedWorkspace|null {
  const raw=localStorage.getItem(key);if(!raw)return null;
  const value=JSON.parse(raw);
  if(value.version!==1||!Array.isArray(value.layouts))throw new Error('Saved layouts use an unsupported format. They have not been changed.');
  for(const layout of value.layouts){
    if(layout.version!==1||typeof layout.id!=='string'||typeof layout.name!=='string'||typeof layout.search!=='string'||!['latest','all'].includes(layout.versions)||typeof layout.status!=='string'||(layout.selected!==null&&(!Array.isArray(layout.selected)||layout.selected.some((v:unknown)=>typeof v!=='string')))||!Array.isArray(layout.sections)||!Array.isArray(layout.cards))throw new Error('A saved layout is invalid. Local data has not been changed.');
    if(!layout.sections.length||layout.sections.some((s:any)=>typeof s.id!=='string'||typeof s.title!=='string'||typeof s.collapsed!=='boolean'))throw new Error('Saved sections are invalid.');
    for(const card of layout.cards){
      if(!['scalar','image','plotly'].includes(card.kind)||typeof card.id!=='string'||!layout.sections.some((s:any)=>s.id===card.section))throw new Error('Saved cards are invalid.');
      if(card.aggregation!==undefined&&(!card.aggregation||!['none','mean','median','min','max'].includes(card.aggregation.center)||!['none','minmax','std','sem'].includes(card.aggregation.band)||typeof card.aggregation.showMembers!=='boolean'))throw new Error('Saved scalar aggregation is invalid.');
      const defaults=newCard(card.kind,card.section);
      for(const [field,def]of Object.entries(defaults)){if(typeof def==='string'&&typeof card[field]!=='string'||typeof def==='boolean'&&typeof card[field]!=='boolean')throw new Error('Saved card settings are invalid.');}
      if(!Array.isArray(card.split)||card.split.some((v:unknown)=>typeof v!=='string')||!Array.isArray(card.filters)||card.filters.some((f:any)=>!f||typeof f.field!=='string'||typeof f.value!=='string'||!['eq','ne','lt','le','gt','ge','contains','exists'].includes(f.op))||!card.pointOrder||typeof card.pointOrder.field!=='string'||typeof card.pointOrder.descending!=='boolean')throw new Error('Saved card fields are invalid.');
      for(const [field,min,max]of [['width',1,12],['height',280,1000],['columns',1,6],['pageSize',1,24]] as const)if(!Number.isInteger(card[field])||card[field]<min||card[field]>max)throw new Error('Saved card sizes are invalid.');
    }
  }
  return value;
}
