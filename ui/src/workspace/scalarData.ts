import { groups, sorted, valueAt, label, type Card, type Observation } from './model';
import { identityColor, runColor } from './presentation';

export type ScalarPoint = {x:number|null;y:number|null;rows:Observation[];low?:number;high?:number};
export type ScalarLine = {key:string;groupKey:string;name:string;color:string;dash:number[];points:ScalarPoint[];member?:boolean};
const finite=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value);

/** Raw paths keep record order, duplicate Xs and explicit gaps. Only aggregation aligns Xs. */
export function scalarData(rows:Observation[],card:Card) {
  let count=0,omitted=0;
  const lines:ScalarLine[]=[];
  for(const group of groups(rows,card.split)) {
    const points:ScalarPoint[]=[];
    for(const row of sorted(group.rows,card.pointOrder)) {
      const x=valueAt(row,card.x),y=valueAt(row,card.y);
      if(x===undefined||y===undefined){omitted++;continue;}
      if(!finite(x)||!finite(y)||(card.logX&&x<=0)||(card.logY&&y<=0)) {
        omitted++;points.push({x:finite(x)&&(!card.logX||x>0)?x:null,y:null,rows:[row]});
      } else {count++;points.push({x,y,rows:[row]});}
    }
    if(!points.some(p=>p.y!==null))continue;
    const color=card.split.includes('@run')?runColor(group.rows[0].artifact_id):identityColor(JSON.stringify([card.split,group.key]));
    const subgroup=JSON.stringify(card.split.filter(f=>f!=='@run').map(f=>valueAt(group.rows[0],f)));
    const dash=card.split.includes('@run')&&card.split.length>1?[[],[6,3],[2,3],[6,3,2,3]][Array.from(subgroup).reduce((sum,ch)=>sum+ch.charCodeAt(0),0)%4]:[];
    const base={key:group.key,groupKey:group.key,name:group.name,color,dash};
    const aggregation=card.aggregation;
    if(!aggregation||aggregation.center==='none'){lines.push({...base,points});continue;}

    const members=new Map<string,ScalarPoint[]>();
    for(const point of points){const id=point.rows[0].artifact_id;if(!members.has(id))members.set(id,[]);members.get(id)!.push(point);}
    const grid=[...new Set(points.flatMap(p=>p.x===null?[]:[p.x]))].sort((a,b)=>a-b);
    // Each run gets one vote: bounded previous-value sampling, just as in Cairn 1.
    // A recorded null interrupts the member until its next valid observation.
    const samples=[...members.values()].map(member=>{
      const ordered=member.filter(p=>p.x!==null).sort((a,b)=>a.x!-b.x!||a.rows[0].ordinal-b.rows[0].ordinal);
      let index=-1;
      return grid.map(x=>{while(index+1<ordered.length&&ordered[index+1].x!<=x)index++;
        return index<0||x>ordered.at(-1)!.x!||ordered[index].y===null?undefined:ordered[index];});
    });
    const center=grid.map((x,index):ScalarPoint=>{
      const present=samples.flatMap(sample=>sample[index]?[sample[index]!]:[]);
      const values=present.map(p=>p.y!).sort((a,b)=>a-b),n=values.length;
      if(!n)return {x,y:null,rows:[]};
      const mean=values.reduce((a,b)=>a+b,0)/n;
      const y=aggregation.center==='mean'?mean:aggregation.center==='min'?values[0]:aggregation.center==='max'?values[n-1]:n%2?values[n>>1]:(values[n/2-1]+values[n/2])/2;
      const point:ScalarPoint={x,y,rows:present.flatMap(p=>p.rows)};
      if(aggregation.band!=='none') {
        const sd=n>1?Math.sqrt(values.reduce((sum,v)=>sum+(v-mean)**2,0)/(n-1)):0;
        const spread=aggregation.band==='sem'?sd/Math.sqrt(n):sd;
        point.low=aggregation.band==='minmax'?values[0]:y-spread;
        point.high=aggregation.band==='minmax'?values[n-1]:y+spread;
      }
      return point;
    });
    lines.push({...base,name:`${group.name} (n=${members.size})`,points:center});
    if(aggregation.showMembers)for(const [id,member]of members)lines.push({...base,key:`${group.key}:${id}`,name:label(member[0].rows[0].run),points:member,member:true});
  }
  const score=(line:ScalarLine)=>{let score=card.lineOrder==='minimum'?Infinity:-Infinity;for(const p of line.points)if(p.y!==null)score=card.lineOrder==='minimum'?Math.min(score,p.y):Math.max(score,p.y);return score;};
  lines.sort((a,b)=>card.lineOrder==='label'?a.name.localeCompare(b.name):score(a)-score(b)||a.name.localeCompare(b.name));
  return {lines,count,omitted};
}
