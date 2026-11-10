import { Link } from 'react-router-dom';
import { Eye, EyeOff } from 'lucide-react';
import { artifactPath, type ArtifactSummary } from '../api';
import { Select } from '../components/Select';
import { label, type Layout } from './model';
import { runColor } from './presentation';
import { sharedPrefix } from './runs';

const statuses = ['', 'live', 'closing', 'finished', 'completed', 'failed', 'cancelled', 'incomplete'];

/** The runs a layout matches, with filters and an eye control per run. */
export function RunPanel({
  project,
  layout,
  candidates,
  selected,
  open,
  patch,
}: {
  project: string;
  layout: Layout;
  candidates: ArtifactSummary[];
  selected: ArtifactSummary[];
  open: boolean;
  patch: (value: Partial<Layout>) => void;
}) {
  const prefix = sharedPrefix(candidates);
  const hiddenCount = candidates.length - selected.length;
  const allVisible = candidates.length > 0 && hiddenCount === 0;
  const visibleIds = selected.map((run) => run.id);
  return (
    <aside className={`workspace-run-panel ${open ? 'open' : ''}`} aria-label="Run selection">
      <header className="workspace-runs-heading">
        <h2>Runs</h2>
        <span>
          {selected.length} visible{hiddenCount ? ` · ${hiddenCount} hidden` : ''}
        </span>
        <button
          className="workspace-run-visibility"
          aria-label={allVisible ? 'Hide all runs' : 'Show all runs'}
          title={allVisible ? 'Hide all runs' : 'Show all runs'}
          aria-pressed={allVisible}
          disabled={!candidates.length}
          onClick={() => patch({ selected: allVisible ? [] : null })}
        >
          {allVisible ? <Eye size={14} /> : <EyeOff size={14} />}
        </button>
      </header>

      <div className="workspace-run-filters">
        <label className="workspace-field">
          <span>Find runs · name, ID, tags</span>
          <input value={layout.search} onChange={(e) => patch({ search: e.target.value })} placeholder="All runs" />
        </label>
        <div className="workspace-field">
          <span>Versions</span>
          <Select
            label="Versions"
            value={layout.versions}
            onChange={(versions) => patch({ versions: versions as Layout['versions'] })}
            choices={[
              { value: 'latest', label: 'Latest run per name' },
              { value: 'all', label: 'All versions' },
            ]}
          />
        </div>
        <div className="workspace-field">
          <span>Status</span>
          <Select
            label="Status"
            value={layout.status}
            onChange={(status) => patch({ status })}
            choices={statuses.map((value) => ({ value, label: value || 'Any status' }))}
          />
        </div>
      </div>

      <div className="workspace-run-list">
        {candidates.map((run) => {
          const hidden = !visibleIds.includes(run.id);
          const status = run.outcome || run.state;
          return (
            <div key={run.id} className={`workspace-run-row ${hidden ? 'is-hidden' : ''}`}>
              <i className="workspace-run-swatch" style={{ background: runColor(run.id) }} aria-hidden="true" />
              <Link
                className="workspace-run-name"
                aria-label={`Open run ${label(run)}`}
                title={label(run)}
                to={artifactPath(project, run.id)}
              >
                {run.name.slice(prefix.length)}
              </Link>
              <span className="workspace-run-version">{run.number ? 'v' + run.number : run.state}</span>
              <span className={`workspace-run-status ${status}`} role="img" aria-label={status} title={status} />
              <button
                className="workspace-run-visibility"
                aria-label={`${hidden ? 'Show' : 'Hide'} ${label(run)}`}
                aria-pressed={!hidden}
                onClick={() =>
                  patch({ selected: hidden ? [...visibleIds, run.id] : visibleIds.filter((id) => id !== run.id) })
                }
              >
                {hidden ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
          );
        })}
      </div>

      <p className="workspace-setting-note">
        {layout.selected === null
          ? 'All matching runs are shown automatically.'
          : 'Visibility is pinned to the runs shown.'}{' '}
        Latest is the most recently created run for each name among matching runs.
      </p>
    </aside>
  );
}
