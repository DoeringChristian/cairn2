import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, post, projectPath, reportPath, useLoaded, when } from '../api';
import { Button, Crumbs, ErrorNote, Field, Hero, Hint, Loading, ReportCard, ReportGrid, Section, Select } from '../components';
import { useRefresh } from '../refresh';

export type Report = { name: string; text: string; epoch: string };
export const address = (project: string, name: string) => `/ui-api/report?${new URLSearchParams({ project, name })}`;

export function ReportsPage() {
  const { project = '' } = useParams();
  const navigate = useNavigate();
  const pulse = useRefresh();
  const { data, error, loading } = useLoaded((signal) => api<{ name: string; updated: number }[]>(`/ui-api/reports?${new URLSearchParams({ project })}`, { signal }), `reports:${project}`, pulse);
  const [name, setName] = useState('');
  const [start, setStart] = useState('');
  const [failure, setFailure] = useState<Error>();
  useEffect(() => {
    document.title = `${project} · Cairn`;
  }, [project]);
  async function create(event: FormEvent) {
    event.preventDefault();
    setFailure(undefined);
    try {
      const text = start ? (await api<Report>(address(project, start))).text : '';
      const report = await post<Report>(address(project, name.trim()), { text });
      navigate(reportPath(project, report.name));
    } catch (e) {
      setFailure(e as Error);
    }
  }
  const choices = [{ value: '', label: 'Blank' }, ...(data || []).map((report) => ({ value: report.name, label: `Copy of ${report.name}` }))];
  return (
    <main>
      <Hero eyebrow={<Crumbs items={[{ label: 'Projects', to: '/' }, { label: project, to: projectPath(project) }]} />} title={project} subtitle="Your plans, with the agent's results beside them." />
      <Section title="Your reports">
        <form className="report-create" onSubmit={create}>
          <Field label="New report" placeholder="Name" value={name} required onChange={(e) => setName(e.target.value)} />
          <div className="field"><span>Start from</span><Select label="Start from" value={start} onChange={setStart} choices={choices} /></div>
          <Button primary type="submit" disabled={!name.trim()}>Create</Button>
        </form>
        {failure && <ErrorNote error={failure} />}
        {loading && !data && <Loading />}
        {error && <ErrorNote error={error} />}
        {data?.length === 0 && <Hint>No reports yet. Write down what you want to find out.</Hint>}
        {!!data?.length && (
          <ReportGrid>
            {data.map((report) => <ReportCard key={report.name} to={reportPath(project, report.name)} project={project} title={report.name} updated={when(report.updated)} />)}
          </ReportGrid>
        )}
      </Section>
    </main>
  );
}
