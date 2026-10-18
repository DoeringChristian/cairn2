import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import './styles/report.css';
import './styles/report-extra.css';
import './styles/app.css';
import './styles/explorer.css';
import { App } from './App';
createRoot(document.getElementById('root')!).render(<StrictMode><BrowserRouter><App /></BrowserRouter></StrictMode>);
