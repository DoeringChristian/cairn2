import { useEffect, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { parse, stringify } from 'yaml';
import { api, artifactBlobs, chunkedFiles, post, type Artifact, type ArtifactSummary, type Json } from '../api';
import { ActionIcon, Button } from './Button';
import { Select } from './Select';

/** The subset of JSON Schema a viewer's <file>.schema.json may use; format cairn-file picks an artifact's file. */
export type Schema = {
  type?: string;
  title?: string;
  description?: string;
  enum?: Json[];
  default?: Json;
  format?: string;
  accept?: string[];
  items?: Schema;
  properties?: Record<string, Schema>;
};
type Settings = Record<string, Json>;
type Reference = { name: string; selector: string; file: string };

const read = (text: string): Settings | undefined => {
  try {
    const value = parse(text) ?? {};
    return typeof value === 'object' && !Array.isArray(value) ? value : undefined;
  } catch {
    return undefined;
  }
};
export function reference(value: Json | undefined): Reference {
  const text = typeof value === 'string' ? value.replace(/^cairn:/, '') : '',
    hash = text.indexOf('#'),
    spec = hash < 0 ? text : text.slice(0, hash),
    at = spec.lastIndexOf('@');
  return {
    name: at < 0 ? spec : spec.slice(0, at),
    selector: at < 0 || !spec.slice(at + 1) ? 'latest' : spec.slice(at + 1),
    file: hash < 0 ? '' : text.slice(hash + 1),
  };
}
const written = ({ name, selector, file }: Reference) =>
  name ? `cairn:${name}@${selector}${file ? `#${file}` : ''}` : '';

function useNames(project: string, namespace?: string) {
  const [names, setNames] = useState<string[]>([]);
  useEffect(() => {
    const c = new AbortController();
    api<{ items: ArtifactSummary[] }>(
      `/ui-api/artifacts?${new URLSearchParams({ project, category: 'all', search: namespace ? `${namespace}/` : '', limit: '100' })}`,
      { signal: c.signal },
    )
      .then((found) =>
        setNames(
          [
            ...new Set(
              found.items.filter((a) => !namespace || a.namespace === namespace).map((a) => `${a.namespace}/${a.name}`),
            ),
          ].sort(),
        ),
      )
      .catch(() => {});
    return () => c.abort();
  }, [project, namespace]);
  return names;
}
/** The resolved artifact of a reference, its versions and its files. */
function useArtifact(ref: Reference, project: string) {
  const [state, setState] = useState<{ artifact?: Artifact; versions: number[] }>({ versions: [] });
  useEffect(() => {
    if (!ref.name) {
      setState({ versions: [] });
      return;
    }
    const c = new AbortController();
    post<Artifact[]>('/v1/resolutions', { refs: [`${ref.name}@${ref.selector}`], project }, c.signal)
      .then(async ([artifact]) => {
        const history = artifact
          ? await api<Artifact[]>(`/ui-api/artifacts/${encodeURIComponent(artifact.id)}/history`, { signal: c.signal })
          : [];
        setState({ artifact, versions: history.flatMap((a) => (a.number ? [a.number] : [])).sort((a, b) => b - a) });
      })
      .catch(() => {
        if (!c.signal.aborted) setState({ versions: [] });
      });
    return () => c.abort();
  }, [ref.name, ref.selector, project]);
  const files = state.artifact
    ? [
        ...new Set([
          ...Object.keys(state.artifact.manifest?.captures || {}),
          ...artifactBlobs(state.artifact).flatMap((b) => (b.name ? [b.name] : [])),
          ...chunkedFiles(state.artifact).map((b) => b.name!),
        ]),
      ]
        .filter((name) => !/^(console|provenance)\//.test(name))
        .sort()
    : [];
  return { ...state, files };
}

/** An artifact, its version and one of its files, written as cairn:NAME@VERSION#FILE. */
function FilePicker({
  label,
  value,
  onChange,
  project,
  accept,
  namespace,
}: {
  label: string;
  value: Json | undefined;
  onChange: (value: string) => void;
  project: string;
  accept?: string[];
  namespace?: string;
}) {
  const ref = reference(value),
    names = useNames(project, namespace),
    { versions, files } = useArtifact(ref, project);
  const shown = files.filter(
    (name) => !accept?.length || accept.some((ending) => name.toLowerCase().endsWith(ending.toLowerCase())),
  );
  return (
    <div className="view-file-picker" role="group" aria-label={label}>
      <Select
        label={`${label} artifact`}
        value={ref.name}
        choices={[{ value: '', label: 'Choose an artifact…' }, ...names.map((name) => ({ value: name, label: name }))]}
        onChange={(name) => onChange(written({ name, selector: 'latest', file: '' }))}
      />
      <Select
        label={`${label} version`}
        value={ref.selector}
        disabled={!ref.name}
        choices={[{ value: 'latest', label: 'Latest' }, ...versions.map((n) => ({ value: `v${n}`, label: `v${n}` }))]}
        onChange={(selector) => onChange(written({ ...ref, selector }))}
      />
      <Select
        label={`${label} file`}
        value={ref.file}
        disabled={!ref.name}
        choices={[
          { value: '', label: shown.length ? 'Choose a file…' : 'No matching files' },
          ...shown.map((name) => ({ value: name, label: name })),
        ]}
        onChange={(file) => onChange(written({ ...ref, file }))}
      />
    </div>
  );
}

const empty = (schema: Schema): Json => (schema.type === 'object' ? {} : schema.type === 'array' ? [] : '');
/** One setting from the viewer's schema; nested objects and lists recurse. */
function SchemaField({
  label,
  schema,
  value,
  onChange,
  project,
}: {
  label: string;
  schema: Schema;
  value: Json | undefined;
  onChange: (value: Json | undefined) => void;
  project: string;
}) {
  const title = schema.title || label,
    hint = schema.default !== undefined ? `Default: ${JSON.stringify(schema.default)}` : undefined;
  if (schema.enum)
    return (
      <div className="workspace-field">
        <span>{title}</span>
        <Select
          label={title}
          value={value === undefined ? '' : JSON.stringify(value)}
          choices={[
            { value: '', label: hint || 'Not set' },
            ...schema.enum.map((option) => ({ value: JSON.stringify(option), label: String(option) })),
          ]}
          onChange={(choice) => onChange(choice ? JSON.parse(choice) : undefined)}
        />
      </div>
    );
  if (schema.format === 'cairn-file')
    return (
      <div className="workspace-field">
        <span>{title}</span>
        <FilePicker label={title} value={value} accept={schema.accept} project={project} onChange={onChange} />
      </div>
    );
  if (schema.type === 'boolean')
    return (
      <label>
        <input
          type="checkbox"
          checked={value === true || (value === undefined && schema.default === true)}
          onChange={(e) => onChange(e.target.checked)}
        />{' '}
        {title}
      </label>
    );
  if (schema.type === 'number' || schema.type === 'integer')
    return (
      <label className="workspace-field">
        <span>{title}</span>
        <input
          type="number"
          step={schema.type === 'integer' ? 1 : 'any'}
          value={typeof value === 'number' ? value : ''}
          placeholder={hint}
          onChange={(e) => onChange(e.target.value === '' ? undefined : Number(e.target.value))}
        />
      </label>
    );
  if (schema.type === 'array') {
    const items = Array.isArray(value) ? value : [],
      item = schema.items || { type: 'string' };
    return (
      <fieldset className="view-settings-group">
        <legend>{title}</legend>
        {items.map((entry, index) => (
          <div className="view-settings-item" key={index}>
            <SchemaField
              label={`${title} ${index + 1}`}
              schema={item}
              value={entry}
              project={project}
              onChange={(next) => onChange(items.map((old, i) => (i === index ? (next ?? empty(item)) : old)))}
            />
            <ActionIcon
              label={`Remove ${title} ${index + 1}`}
              onClick={() => onChange(items.filter((_, i) => i !== index))}
            >
              <Trash2 size={15} />
            </ActionIcon>
          </div>
        ))}
        <Button small onClick={() => onChange([...items, empty(item)])}>
          <Plus size={14} /> Add {item.title || title}
        </Button>
      </fieldset>
    );
  }
  if (schema.type === 'object' || schema.properties) {
    const current = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const fields = Object.entries(schema.properties || {}).map(([key, child]) => (
      <SchemaField
        key={key}
        label={key}
        schema={child}
        value={current[key]}
        project={project}
        onChange={(next) => {
          const rest = Object.fromEntries(Object.entries(current).filter(([k]) => k !== key));
          onChange(next === undefined || next === '' ? rest : { ...rest, [key]: next });
        }}
      />
    ));
    return label ? (
      <fieldset className="view-settings-group">
        <legend>{title}</legend>
        {fields}
      </fieldset>
    ) : (
      <>{fields}</>
    );
  }
  return (
    <label className="workspace-field">
      <span>{title}</span>
      <input
        type={schema.format === 'color' ? 'color' : 'text'}
        value={typeof value === 'string' ? value : schema.format === 'color' ? String(schema.default || '#000000') : ''}
        placeholder={hint}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

/** Settings of a viewer without a schema: names with text, numbers or artifact files. */
function Pairs({
  settings,
  onChange,
  project,
}: {
  settings: Settings;
  onChange: (settings: Settings) => void;
  project: string;
}) {
  const entries = Object.entries(settings),
    set = (index: number, key: string, value: Json) =>
      onChange(Object.fromEntries(entries.map((entry, i) => (i === index ? [key, value] : entry))));
  return (
    <fieldset className="view-settings-group">
      <legend>Settings</legend>
      <p className="workspace-setting-note">This viewer describes no settings; name them as it expects.</p>
      {entries.map(([key, value], index) => {
        const file = typeof value === 'string' && value.startsWith('cairn:');
        return (
          <div className="view-settings-item" key={index}>
            <label className="workspace-field">
              <span>Name</span>
              <input value={key} onChange={(e) => set(index, e.target.value, value)} />
            </label>
            {file ? (
              <FilePicker
                label={key || 'File'}
                value={value}
                project={project}
                onChange={(next) => set(index, key, next)}
              />
            ) : (
              <label className="workspace-field">
                <span>Value</span>
                <input
                  value={typeof value === 'string' || typeof value === 'number' ? String(value) : JSON.stringify(value)}
                  onChange={(e) =>
                    set(
                      index,
                      key,
                      e.target.value !== '' && Number.isFinite(Number(e.target.value))
                        ? Number(e.target.value)
                        : e.target.value,
                    )
                  }
                />
              </label>
            )}
            <Button small onClick={() => set(index, key, file ? '' : 'cairn:')}>
              {file ? 'Text' : 'File'}
            </Button>
            <ActionIcon
              label={`Remove ${key || 'setting'}`}
              onClick={() => onChange(Object.fromEntries(entries.filter((_, i) => i !== index)))}
            >
              <Trash2 size={15} />
            </ActionIcon>
          </div>
        );
      })}
      <Button small onClick={() => onChange({ ...settings, [`setting${entries.length + 1}`]: '' })}>
        <Plus size={14} /> Add setting
      </Button>
    </fieldset>
  );
}

/** The schema a viewer's HTML file ships beside it, as <file>.schema.json; null when it has none. */
function useSchema(viewer: Reference, project: string) {
  const { artifact } = useArtifact(viewer, project),
    [schema, setSchema] = useState<Schema | null>();
  const file = viewer.file.replace(/\.html?$/, '.schema.json'),
    present =
      !!artifact?.manifest?.captures[file] || (!!artifact && artifactBlobs(artifact).some((b) => b.name === file));
  useEffect(() => {
    setSchema(undefined);
    if (!artifact) return;
    if (!present) {
      setSchema(null);
      return;
    }
    const c = new AbortController();
    api<Schema>(
      `/ui-api/artifacts/${encodeURIComponent(artifact.id)}/files/${file.split('/').map(encodeURIComponent).join('/')}`,
      { signal: c.signal },
    )
      .then(setSchema)
      .catch(() => {
        if (!c.signal.aborted) setSchema(null);
      });
    return () => c.abort();
  }, [artifact?.id, file, present]);
  return schema;
}

/** A view's YAML edited as a form: the viewer, its caption and its settings; the YAML tab shows what is written. */
export function ViewEditor({
  value,
  onChange,
  project,
}: {
  value: string;
  onChange: (value: string) => void;
  project: string;
}) {
  const [tab, setTab] = useState('Form'),
    settings = read(value),
    viewer = reference(settings?.viewer),
    schema = useSchema(viewer, project);
  const { viewer: _viewer, caption, ...rest } = settings || {};
  const update = (next: Settings) =>
    onChange(stringify(Object.fromEntries(Object.entries(next).filter(([, v]) => v !== undefined && v !== ''))));
  return (
    <div className="view-editor">
      <div className="tabs" role="tablist" aria-label="View editor">
        {['Form', 'YAML'].map((name) => (
          <button role="tab" key={name} aria-selected={tab === name} onClick={() => setTab(name)}>
            {name}
          </button>
        ))}
      </div>
      {tab === 'YAML' ? (
        <label className="workspace-field">
          <span>View YAML</span>
          <textarea
            className="workspace-view-source"
            rows={12}
            spellCheck={false}
            value={value}
            onChange={(e) => onChange(e.target.value)}
          />
          {!settings && <small className="view-error">Not a YAML mapping yet; the form shows it once it parses.</small>}
        </label>
      ) : !settings ? (
        <p className="view-error">The YAML does not parse; fix it in the YAML tab.</p>
      ) : (
        <div className="workspace-settings-panel">
          <div className="workspace-field">
            <span>Viewer</span>
            <FilePicker
              label="Viewer"
              value={settings.viewer}
              namespace="viewers"
              accept={['.html']}
              project={project}
              onChange={(next) => update({ viewer: next, caption, ...rest })}
            />
          </div>
          <label className="workspace-field">
            <span>Caption</span>
            <input
              value={typeof caption === 'string' ? caption : ''}
              onChange={(e) => update({ viewer: settings.viewer, caption: e.target.value, ...rest })}
            />
          </label>
          {viewer.file &&
            (schema === undefined ? (
              <p className="workspace-setting-note">Loading the viewer’s settings…</p>
            ) : schema ? (
              <SchemaField
                label=""
                schema={{ ...schema, type: 'object' }}
                value={rest}
                project={project}
                onChange={(next) => update({ viewer: settings.viewer, caption, ...(next as Settings) })}
              />
            ) : (
              <Pairs
                settings={rest}
                project={project}
                onChange={(next) => update({ viewer: settings.viewer, caption, ...next })}
              />
            ))}
        </div>
      )}
    </div>
  );
}
