import { Activity, Boxes, FileText, ArrowUpRight } from 'lucide-react';
import { Link } from 'react-router-dom';
import { projectPath,when,type Project } from '../api';
/** Prism2's project card, with reports as the primary destination. */
export function ProjectCard({project:p}: {project:Project}) {
  return <article className="project-card"><header className="project-card-heading"><span className="project-initial" aria-hidden="true">{p.name[0]?.toUpperCase()}</span></header>
    <h2><Link className="project-title" to={projectPath(p.name)}>{p.name}<ArrowUpRight size={20} aria-hidden="true"/></Link></h2>
    <nav className="project-stat-links" aria-label={`${p.name} contents`}>{[{view:'reports',label:'Reports',count:p.reports,Icon:FileText},{view:'runs',label:'Runs',count:p.runs,Icon:Activity},{view:'artifacts',label:'Artifacts',count:p.artifacts-p.runs-p.reports,Icon:Boxes}].map(item=><Link key={item.view} to={`${projectPath(p.name)}?view=${item.view}`} aria-label={`${item.count} ${item.label.toLowerCase()} in ${p.name}`}><item.Icon size={20} strokeWidth={1.7} aria-hidden="true"/><strong>{item.count}</strong><span>{item.label}</span></Link>)}</nav>
    <footer className="project-card-footer"><div><time dateTime={p.updated}>Latest activity {when(p.updated)}</time></div><Link className="project-workspace" to={projectPath(p.name)}>Reports ↗</Link></footer></article>;
}
