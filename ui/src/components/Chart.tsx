import type { RecordEntry } from '../api';
import { Hint } from './States';
type Point = [number, number];
const tick = (n: number) => Number(n.toPrecision(4)).toString();
/** Shared scalar chart. Ordinal order is preserved; gaps and repeated steps are not invented. */
export function LineChart({ title, points, xLabel = 'step' }: {title: string; points: Point[]; xLabel?: string}) {
  if (!points.length) return null;
  const xs = points.map(p=>p[0]), ys = points.map(p=>p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const sx = (x: number) => 55 + (x-x0)/(x1-x0 || 1)*285;
  const sy = (y: number) => 140 - (y-y0)/(y1-y0 || 1)*120;
  const last = points[points.length-1];
  return <figure className="run-chart"><figcaption><span className="mono">{title}</span><strong>{tick(last[1])}</strong></figcaption>
    <svg viewBox="0 0 360 175" role="img" aria-label={`${title} over ${xLabel}; latest ${tick(last[1])}`}>
      <line className="run-axis" x1="55" y1="140" x2="340" y2="140"/><line className="run-axis" x1="55" y1="20" x2="55" y2="140"/>
      <text className="run-tick" x="49" y="25" textAnchor="end">{tick(y1)}</text><text className="run-tick" x="49" y="140" textAnchor="end">{tick(y0)}</text>
      <text className="run-tick" x="55" y="163">{tick(x0)}</text><text className="run-tick" x="340" y="163" textAnchor="end">{tick(x1)} {xLabel}</text>
      <path className="run-line" d={points.map(([x,y],i)=>`${i?'L':'M'}${sx(x)},${sy(y)}`).join(' ')}/><circle className="run-dot" cx={sx(last[0])} cy={sy(last[1])} r="3"/>
    </svg></figure>;
}
export function RecordCharts({records}: {records: RecordEntry[]}) {
  const found = new Map<string, Point[]>();
  const stepped = records.length > 0 && records.every(r=>typeof r.data.step==='number');
  for(const row of records) for(const [key,value] of Object.entries(row.data)) {
    if(key==='step' || typeof value!=='number') continue;
    if(!found.has(key)) found.set(key,[]);
    found.get(key)!.push([stepped ? row.data.step as number : row.ordinal,value]);
  }
  return found.size ? <div className="run-charts">{[...found].map(([title,points])=><LineChart key={title} title={title} points={points} xLabel={stepped?'step':'record'}/>)}</div> : <Hint>No scalar metrics in the loaded records.</Hint>;
}
