import { FileText, FlaskConical, Boxes, Layers, LayoutDashboard, PanelLeftClose, PanelLeftOpen, FolderOpen, List, Pause, Play, Palette, LogOut } from 'lucide-react';
import { clearViews, scopeViews } from './viewCache';
import { invalidateLists, scopeListCache } from './listCache';
import { setRefreshPaused, useRefresh, useRefreshMode } from './refresh';
import { useEffect, useState, type FormEvent } from 'react';
import { Link, Route, Routes, useLocation, useNavigate, matchPath } from 'react-router-dom';
import { api, post, useResource, projectPath, type Project } from './api';
import { Button, ErrorNote, Field, Hero, Loading, Select } from './components';
import { ProjectsPage, ProjectPage } from './pages/Projects';
import { ArtifactPage } from './pages/Artifact';
import { PalettePage } from './pages/Palette';
import { WorkspacePage, RunWorkspaceRedirect } from './workspace/Workspace';
import './styles/navigation.css';

type Session = { authenticated: boolean; no_auth: boolean; repository_id: string };
let initialSession: Promise<Session> | undefined;
function bootstrapSession() {
  if (!initialSession) {
    const linkToken = new URLSearchParams(window.location.hash.slice(1)).get('token');
    if (linkToken) window.history.replaceState(null, '', window.location.pathname + window.location.search);
    initialSession = (linkToken ? post('/ui-api/login', {link_token: linkToken}) : Promise.resolve())
      .then(() => api<Session>('/ui-api/session'));
  }
  return initialSession;
}
function Login({ onLogin }: {onLogin: () => void}) {
  const [token, setToken] = useState('');
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(undefined);
    try { await post('/ui-api/login', {token}); invalidateLists();clearViews();setToken(''); onLogin(); }
    catch (error) { setError(error as Error); } finally { setBusy(false); }
  }
  return <main><Hero eyebrow="Your research, together" title="Welcome to Cairn." subtitle="Explore the experiments, artifacts, and reports in your repository." />
    <form className="login-form" onSubmit={submit}><Field label="Server token" type="password" autoComplete="current-password" autoFocus required value={token} onChange={e => setToken(e.target.value)} />
      <p className="hint">Use the same token as your Cairn server.</p>{error && <ErrorNote error={error} />}<Button primary type="submit" disabled={busy}>{busy ? 'Connecting…' : 'Open repository'}</Button></form></main>;
}
function Rail({ authenticated, noAuth, logout }: {authenticated: boolean; noAuth: boolean; logout: () => void}) {
  const [open, setOpen] = useState(false), [expanded,setExpanded]=useState(false);
  const location = useLocation(), navigate = useNavigate();
  const project = matchPath('/p/:project/*', location.pathname)?.params.project || '';
  const view = new URLSearchParams(location.search).get('view') || 'reports';
  const inList = location.pathname === projectPath(project);
  const inWorkspace = !!matchPath('/p/:project/workspace',location.pathname);
  const pulse = useRefresh(authenticated), paused = useRefreshMode();
  const projects = useResource<Project[]>(authenticated ? '/ui-api/projects' : null, pulse);
  useEffect(() => setOpen(false), [location.pathname, location.search]);
  return <aside className={`rail ${open ? 'open' : ''} ${expanded?'expanded':'collapsed'}`} aria-label="Main navigation"><Link className="brand" to="/"><span>C</span><span className="site-title">Cairn</span></Link><button className="rail-fold" aria-label={expanded?'Collapse navigation':'Expand navigation'} title={expanded?'Collapse navigation':'Expand navigation'} aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>{expanded?<PanelLeftClose size={18}/>:<PanelLeftOpen size={18}/>}</button>
    <button className="rail-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>Browse</button>
    {authenticated && <section className="report-list workspace-navigation">
      <button className="rail-project-icon" aria-label="Choose project" title={project||"Choose project"} onClick={()=>setExpanded(true)}><FolderOpen size={18}/></button><div className="rail-project-picker"><Select label="Project" value={project} choices={[{value:'',label:'All projects'},...(projects.data||[]).map(p=>({value:p.name,label:p.name,note:`${p.runs} runs · ${p.reports} reports`}))]}
        onChange={value=>navigate(value ? (inWorkspace ? `${projectPath(value)}/workspace` : `${projectPath(value)}?view=${view}`) : '/')}/></div>
      <nav aria-label="Project navigation"><Link aria-label="Workspace" title="Workspace" to={project ? `${projectPath(project)}/workspace` : "/"} aria-current={project && inWorkspace ? "page" : undefined}><LayoutDashboard size={18} aria-hidden="true"/><span className="rail-item-label">Workspace</span></Link>{[
        {value:'reports',label:'Reports',Icon:FileText},
        {value:'runs',label:'Runs',Icon:FlaskConical},
        {value:'artifacts',label:'Artifacts',Icon:Boxes},
        {value:'all',label:'All',Icon:Layers},
      ].map(({value,label,Icon})=><Link key={value} aria-label={label} title={label} to={project ? `${projectPath(project)}?view=${value}` : '/'}
        aria-current={project && inList && view===value ? 'page' : undefined}><Icon size={18} aria-hidden="true"/><span className="rail-item-label">{label}</span></Link>)}</nav>
    </section>}
    <div id="report-toc-slot"/><button className="rail-outline-icon" aria-label="Show table of contents" title="Table of contents" onClick={()=>setExpanded(true)}><List size={18}/></button>
    <div className="rail-note"><p>{noAuth ? "Development · authentication off" : "Evidence worth keeping."}</p><div className="rail-links"><button className="text-button" aria-label={paused?'Resume auto-refresh':'Pause auto-refresh'} title={paused?'Resume auto-refresh':'Pause auto-refresh'} onClick={()=>setRefreshPaused(!paused)}>{paused?<Play size={16}/>:<Pause size={16}/>}<span className="rail-item-label">{paused?'Resume auto-refresh':'Auto-refresh · 5s'}</span></button><Link aria-label="Components" title="Components" to="/palette"><Palette size={16}/><span className="rail-item-label">Components</span></Link>{authenticated && !noAuth && <button className="text-button" aria-label="Log out" title="Log out" onClick={logout}><LogOut size={16}/><span className="rail-item-label">Log out</span></button>}</div></div>
  </aside>;
}
export function App() {
  const [auth, setAuth] = useState<boolean | null>(null);
  const [noAuth, setNoAuth] = useState(false);
  const [error, setError] = useState<Error>();
  useEffect(() => {
    bootstrapSession().then(session => {scopeListCache(session.repository_id);scopeViews(session.repository_id);setAuth(true); setNoAuth(session.no_auth);}).catch(e => {setAuth(false); if (e.status !== 401) setError(e);});
    const expired = () => {invalidateLists();clearViews();setAuth(false);};
    window.addEventListener('cairn:unauthorized', expired);
    return () => window.removeEventListener('cairn:unauthorized', expired);
  }, []);
  async function logout() { try { await post('/ui-api/logout', {}); invalidateLists();clearViews();setAuth(false); } catch(e) { setError(e as Error); } }
  return <><a className="skip-link" href="#content">Skip to content</a><Rail authenticated={auth === true} noAuth={noAuth} logout={logout}/><div id="content" className="page-host">
    {error && <ErrorNote error={error}/>}{auth === null ? <main><Loading what="Connecting to Cairn"/></main> : auth === false ? <Login onLogin={() => {setError(undefined); setAuth(true);}}/> : <Routes>
      <Route path="/" element={<ProjectsPage/>}/><Route path="/login" element={<ProjectsPage/>}/><Route path="/p/:project" element={<ProjectPage/>}/>
      <Route path="/p/:project/a/:id" element={<ArtifactPage/>}/><Route path="/p/:project/v/:version" element={<ArtifactPage/>}/>
      <Route path="/p/:project/workspace" element={<WorkspacePage/>}/><Route path="/p/:project/workspace/:runId" element={<RunWorkspaceRedirect/>}/><Route path="/palette" element={<PalettePage/>}/><Route path="*" element={<main><Hero title="Page not found"/><Link to="/">Back to projects</Link></main>}/>
    </Routes>}</div></>;
}
