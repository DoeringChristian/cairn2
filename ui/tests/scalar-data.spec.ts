import { test, expect } from '@playwright/test';
import { scalarData } from '../src/workspace/scalarData';
import { newCard, type Card, type Observation } from '../src/workspace/model';
const row=(id:string,ordinal:number,x:number,y:number|null)=>({artifact_id:id,ordinal,data:{step:x,loss:y},run:{id,name:id,number:1,metadata:{}}} as unknown as Observation);
const card=():Card=>({...newCard('scalar','test'),split:[],aggregation:{center:'mean',band:'minmax',showMembers:false}});

test('uneven member sampling is bounded, duplicate Xs get one vote per run, and nulls interrupt contributions',()=>{
  const rows=[row('a',1,0,0),row('a',2,2,4),row('a',3,2,6),row('a',4,3,null),row('a',5,4,8),row('b',1,1,10),row('b',2,3,14),row('c',1,5,20)];
  const result=scalarData(rows,card()).lines[0].points;
  expect(result.map(p=>[p.x,p.y,p.low,p.high,p.rows.length])).toEqual([[0,0,0,0,1],[1,5,0,10,2],[2,8,6,10,2],[3,14,14,14,1],[4,8,8,8,1],[5,20,20,20,1]]);
  expect(result[2].rows.map(r=>r.ordinal)).toEqual([3,1]);
});

test('sample standard deviation, standard error, median, min and max have explicit numerical meanings',()=>{
  const rows=[row('a',1,0,1),row('b',1,0,3),row('c',1,0,8)];
  const settings=card();settings.aggregation={center:'median',band:'std',showMembers:false};
  const p=scalarData(rows,settings).lines[0].points[0];
  expect(p.y).toBe(3);expect(p.low).toBeCloseTo(3-Math.sqrt(13));expect(p.high).toBeCloseTo(3+Math.sqrt(13));
  settings.aggregation.band='sem';expect(scalarData(rows,settings).lines[0].points[0].high).toBeCloseTo(3+Math.sqrt(13/3));
  settings.aggregation.center='min';expect(scalarData(rows,settings).lines[0].points[0].y).toBe(1);
  settings.aggregation.center='max';expect(scalarData(rows,settings).lines[0].points[0].y).toBe(8);
});

test('raw curves preserve reverse order, repeated Xs and null gaps; aggregation is opt-in',()=>{
  const settings=card();delete settings.aggregation;settings.pointOrder={field:'@ordinal',descending:true};
  const result=scalarData([row('a',1,0,1),row('a',2,0,2),row('a',3,1,null),row('a',4,2,4)],settings);
  expect(result.lines[0].points.map(p=>[p.x,p.y])).toEqual([[2,4],[1,null],[0,2],[0,1]]);
  expect(result.count).toBe(3);expect(result.omitted).toBe(1);
});

test('log axes exclude nonpositive observations and member lines share their group identity',()=>{
  const settings=card();settings.logX=true;settings.logY=true;settings.aggregation!.showMembers=true;
  const result=scalarData([row('a',1,0,3),row('a',2,1,-1),row('a',3,2,4),row('b',1,2,8)],settings);
  expect(result.count).toBe(2);expect(result.lines.filter(l=>l.member)).toHaveLength(2);
  expect(new Set(result.lines.map(l=>l.groupKey)).size).toBe(1);
  expect(result.lines.find(l=>!l.member)!.points.at(-1)!.y).toBe(6);
});
