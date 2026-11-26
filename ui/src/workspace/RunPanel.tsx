import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Eye, EyeOff, ListFilter, Plus, Trash2 } from 'lucide-react';
import { artifactPath, status as runStatus, type Artifact, type Json } from '../api';
import { Button } from '../components/Button';
import { Select } from '../components/Select';
import { display } from './cards/records';
import { Choice, fieldName, FilterRow, filterText } from './cards/settings';
import { WorkspaceDialog } from './Dialog';
import { label, type Conditions, type FieldInfo, type Layout } from './model';
import { runColor } from './presentation';
import { runValue, searching, sharedPrefix } from './runs';

const statuses = ['', 'live', 'finished', 'completed', 'failed', 'cancelled', 'incomplete'];
const plain = new Set(['number', 'string', 'boolean']);
const itemText = (item: Conditions['items'][number]): string =>
  'items' in item ? `(${item.items.map(itemText).join(item.mode === 'all' ? ' and ' : ' or ')})` : filterText(item);

/** Conditions, and groups of them, of which all or any hold. */
function ConditionEditor({
  group,
  fields,
  path,
  onChange,
}: {
  group: Conditions;
  fields: FieldInfo[];
  path: string;
  onChange: (group: Conditions) => void;
}) {
  const set = (index: number, item?: Conditions['items'][number]) =>
    onChange({ ...group, items: group.items.flatMap((old, i) => (i !== index ? [old] : item ? [item] : [])) });
  const add = (item: Conditions['items'][number]) => onChange({ ...group, items: [...group.items, item] });
  return (
    <>
      {group.items.length > 1 && (
        <Choice
          label={path ? `Combine group ${path.slice(0, -1)}` : 'Combine'}
          value={group.mode}
          options={[
            ['all', 'All of these hold'],
            ['any', 'Any of these holds'],
          ]}
          onChange={(mode) => onChange({ ...group, mode })}
        />
      )}
      {group.items.map((item, index) =>
        'items' in item ? (
          <div className="workspace-condition-group" key={index}>
            <ConditionEditor
              group={item}
              fields={fields}
              path={`${path}${index + 1}.`}
              onChange={(changed) => set(index, changed)}
            />
            <Button small onClick={() => set(index)}>
              <Trash2 size={14} /> Remove group {path}
              {index + 1}
            </Button>
          </div>
        ) : (
          <FilterRow
            key={index}
            number={`${path}${index + 1}`}
            filter={item}
            fields={fields}
            onChange={(value) => set(index, { ...item, ...value })}
            onRemove={() => set(index)}
          />
        ),
      )}
      <div className="workspace-condition-actions">
        <Button small onClick={() => add({ field: '', op: 'eq', value: '' })}>
          <Plus size={14} /> Add condition
        </Button>
        <Button small onClick={() => add({ mode: 'any', items: [{ field: '', op: 'eq', value: '' }] })}>
          <Plus size={14} /> Add group
        </Button>
      </div>
    </>
  );
}

/** The runs a layout matches, with filters and an eye control per run. */
export function RunPanel({
  project,
  layout,
  candidates,
  selected,
  open,
  configs,
  fields,
  patch,
  hide,
  onChoosing,
}: {
  project: string;
  layout: Layout;
  candidates: Artifact[];
  selected: Artifact[];
  open: boolean;
  configs: Record<string, Json>;
  /** Fields of the records of the shown runs. */
  fields: FieldInfo[];
  patch: (value: Partial<Layout>) => void;
  hide: (ids: string[]) => void;
  /** Whether conditions are being chosen, which needs the runs' config. */
  onChoosing: (choosing: boolean) => void;
}) {
  const [choosing, setChoosing] = useState(false);
  const choose = (open: boolean) => {
    setChoosing(open);
    onChoosing(open);
  };
  const conditions = layout.conditions ?? { mode: 'all', items: [] };
  const setConditions = (next: Conditions) => patch({ conditions: next.items.length ? next : undefined });
  const grouping = layout.groupRuns ?? [];
  const runFields = useMemo(() => {
    const names = new Set(['@name', '@version', '@status']);
    const leaves = (value: Json, path: string): void => {
      if (value && typeof value === 'object' && !Array.isArray(value))
        for (const [key, item] of Object.entries(value))
          leaves(item, `${path}/${key.replace(/~/g, '~0').replace(/\//g, '~1')}`);
      else if (path) names.add(`@config${path}`);
    };
    Object.values(configs).forEach((config) => leaves(config, ''));
    candidates.forEach((run) => Object.keys(run.tags).forEach((key) => names.add(`@tags/${key}`)));
    fields
      .filter((field) => !field.name.startsWith('@') && [...field.kinds].every((kind) => plain.has(kind)))
      .forEach((field) => names.add(field.name));
    return [...names].map((name) => ({ name, kinds: new Set<string>() }));
  }, [configs, candidates, fields]);
  const groups = useMemo(() => {
    if (!grouping.length) return [{ key: '', name: '', runs: candidates }];
    const found = new Map<string, { key: string; name: string; runs: Artifact[] }>();
    for (const run of candidates) {
      const values = grouping.map((field) => runValue(run, field, configs));
      const key = JSON.stringify(values);
      if (!found.has(key))
        found.set(key, {
          key,
          name: grouping.map((field, i) => `${fieldName(field)} ${display(values[i])}`).join(' · '),
          runs: [],
        });
      found.get(key)!.runs.push(run);
    }
    return [...found.values()].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  }, [candidates, grouping, configs]);
  const prefix = sharedPrefix(candidates);
  const hiddenCount = candidates.length - selected.length;
  const allVisible = candidates.length > 0 && hiddenCount === 0;
  const visibleIds = new Set(selected.map((run) => run.id));
  const hiddenIds = candidates.filter((run) => !visibleIds.has(run.id)).map((run) => run.id);
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
          onClick={() => hide(allVisible ? candidates.map((run) => run.id) : [])}
        >
          {allVisible ? <Eye size={14} /> : <EyeOff size={14} />}
        </button>
      </header>

      <div className="workspace-run-filters">
        <label className="workspace-field">
          <span>Find runs · name, ID, status, tags</span>
          <input
            value={layout.search}
            onChange={(e) => patch({ search: e.target.value })}
            placeholder="All runs"
            aria-invalid={!searching(layout.search)}
            title={searching(layout.search) ? 'Text or a pattern, such as sweep/[12]' : 'Not a pattern: found as plain text'}
          />
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

      <div className="workspace-run-conditions">
        {conditions.items.map((item, index) => (
          <button
            key={index}
            className="workspace-group-chip"
            title={itemText(item)}
            aria-label={`Remove condition ${itemText(item)}`}
            onClick={() => setConditions({ ...conditions, items: conditions.items.filter((_, i) => i !== index) })}
          >
            {index > 0 && <i>{conditions.mode === 'all' ? 'and' : 'or'}</i>} {itemText(item)} ×
          </button>
        ))}
        {grouping.length > 0 && (
          <button
            className="workspace-group-chip"
            onClick={() => patch({ groupRuns: undefined })}
            aria-label="Stop grouping runs"
          >
            <i>by</i> {grouping.map(fieldName).join(' · ')} ×
          </button>
        )}
        <Button small onClick={() => choose(true)}>
          <ListFilter size={14} /> Filter and group
        </Button>
      </div>

      <div className="workspace-run-list">
        {groups.map((group) => {
          const shown = group.runs.filter((run) => visibleIds.has(run.id));
          const ids = new Set(group.runs.map((run) => run.id));
          return (
            <div key={group.key} role={group.key ? 'group' : undefined} aria-label={group.name || undefined}>
              {group.key && (
                <div className="workspace-run-group">
                  <button
                    className="workspace-run-group-name"
                    title="Show only this group"
                    onClick={() => hide(candidates.filter((run) => !ids.has(run.id)).map((run) => run.id))}
                  >
                    {group.name}
                  </button>
                  <span>{group.runs.length}</span>
                  <button
                    className="workspace-run-visibility"
                    aria-label={`${shown.length ? 'Hide' : 'Show'} ${group.name}`}
                    aria-pressed={!!shown.length}
                    onClick={() =>
                      hide(
                        shown.length
                          ? [...hiddenIds, ...shown.map((run) => run.id)]
                          : hiddenIds.filter((id) => !ids.has(id)),
                      )
                    }
                  >
                    {shown.length ? <Eye size={14} /> : <EyeOff size={14} />}
                  </button>
                </div>
              )}
              {group.runs.map((run) => {
                const hidden = !visibleIds.has(run.id);
                const status = runStatus(run);
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
                    <span className="workspace-run-version">{run.version ? 'v' + run.version : 'live'}</span>
                    <span className={`workspace-run-status ${status}`} role="img" aria-label={status} title={status} />
                    <button
                      className="workspace-run-visibility"
                      aria-label={`${hidden ? 'Show' : 'Hide'} ${label(run)}`}
                      aria-pressed={!hidden}
                      onClick={() => hide(hidden ? hiddenIds.filter((id) => id !== run.id) : [...hiddenIds, run.id])}
                    >
                      {hidden ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>

      <p className="workspace-setting-note">
        Hidden runs are remembered in this browser; new runs are shown automatically. Latest is the most recently
        created run for each name among matching runs.
      </p>
      {choosing && (
        <WorkspaceDialog title="Filter and group runs" onClose={() => choose(false)}>
          <div className="workspace-run-choice">
            <span className="workspace-label">Only runs where</span>
            <ConditionEditor group={conditions} fields={runFields} path="" onChange={setConditions} />
            <div className="workspace-grouping">
              <span className="workspace-label">Group runs by</span>
              <div className="workspace-grouping-fields">
                {grouping.map((field) => (
                  <button
                    className="workspace-group-chip"
                    key={field}
                    aria-label={`Remove grouping ${field}`}
                    onClick={() =>
                      patch({ groupRuns: grouping.length > 1 ? grouping.filter((f) => f !== field) : undefined })
                    }
                  >
                    {fieldName(field)} ×
                  </button>
                ))}
                <Select
                  label="Add grouping field"
                  value=""
                  choices={[
                    { value: '', label: 'Add field…' },
                    ...runFields
                      .filter((field) => !grouping.includes(field.name))
                      .map((field) => ({ value: field.name, label: fieldName(field.name) })),
                  ]}
                  onChange={(field) => field && patch({ groupRuns: [...grouping, field] })}
                />
              </div>
            </div>
            <p className="workspace-setting-note">
              A logged field counts with its last value. Clicking a group's name shows only that group.
            </p>
          </div>
        </WorkspaceDialog>
      )}
    </aside>
  );
}
