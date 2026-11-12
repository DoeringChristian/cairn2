import { Link } from 'react-router-dom';
import type { ReactNode } from 'react';
import { StatusPill } from './Pill';
/** Prism2's report card, using the shared Cairn Report theme tokens. */
export function ReportCard({to,project,title,description,status,updated}: {to:string;project:string;title:string;description?:string;status:string;updated:string}) {
  return <Link className="report-card" to={to}><div className="report-card-top"><span className="document-icon" aria-hidden="true"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8M8 17h6"/></svg></span><StatusPill status={status}/></div><p className="eyebrow">{project}</p><h2>{title}</h2><p>{description || 'Executable source with pinned evidence and versioned builds.'}</p><div className="report-card-footer"><span>{updated}</span><span>Open report ↗</span></div></Link>;
}
export function ReportGrid({children}: {children:ReactNode}) {return <div className="report-grid">{children}</div>;}
