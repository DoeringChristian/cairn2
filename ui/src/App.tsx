import { clearViews, scopeViews } from './viewCache';
import { invalidateLists, scopeListCache } from './listCache';
import { setRefreshPaused, useRefresh, useRefreshMode } from './refresh';
import { useEffect, useState, type FormEvent } from 'react';
import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { api, post, useResource, projectPath, type Project } from './api';
import { Button, ErrorNote, Field, Hero, Loading } from './components';
import { ProjectsPage, ProjectPage } from './pages/Projects';
import { ArtifactPage } from './pages/Artifact';
import { PalettePage } from './pages/Palette';

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
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const pulse = useRefresh(authenticated), paused = useRefreshMode();
  const projects = useResource<Project[]>(authenticated ? '/ui-api/projects' : null, pulse);
  useEffect(() => setOpen(false), [location.pathname]);
  return <aside className={`rail ${open ? 'open' : ''}`}><Link className="brand" to="/"><span>C</span><span className="site-title">Cairn</span></Link>
    <button className="rail-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>Browse</button>
    <section className="report-list"><p className="rail-label">Repository</p><nav><NavLink to="/" end>All projects</NavLink></nav></section>
    {authenticated && <section className="report-list"><p className="rail-label">Projects</p><nav>{projects.data?.map(p => <NavLink key={p.name} to={projectPath(p.name)}>{p.name}</NavLink>)}</nav></section>}
    <div id="report-toc-slot"/>
    <div className="rail-note"><p>{noAuth ? "Development · authentication off" : "Evidence worth keeping."}</p><div className="rail-links"><button className="text-button" onClick={()=>setRefreshPaused(!paused)}>{paused?'Resume auto-refresh':'Auto-refresh · 5s'}</button><Link to="/palette">Components</Link>{authenticated && !noAuth && <button className="text-button" onClick={logout}>Log out</button>}</div></div>
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
      <Route path="/palette" element={<PalettePage/>}/><Route path="*" element={<main><Hero title="Page not found"/><Link to="/">Back to projects</Link></main>}/>
    </Routes>}</div></>;
}
