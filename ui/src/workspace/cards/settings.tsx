import { useState, type ReactNode } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { ActionIcon, Button } from '../../components/Button';
import { Select } from '../../components/Select';
import type { Card, FieldInfo, Filter } from '../model';
import type { SettingsProps } from './types';

const own: Record<string, string> = {
  '@run': 'Run',
  '@name': 'Run name',
  '@version': 'Run version',
  '@ordinal': 'Record number',
  '@created': 'Time logged',
  '@status': 'Status',
};
/** A field as people read it: `@config/lr` is the setting lr. */
export const fieldName = (field: string) =>
  own[field] ??
  field
    .replace(/^@config\//, 'config · ')
    .replace(/^@tags\//, 'tag · ')
    .replace(/^\//, '');

/** Settings that belong together. */
export const Group = ({ title, children }: { title: string; children: ReactNode }) => (
  <section className="workspace-settings-group">
    <h3>{title}</h3>
    {children}
  </section>
);
/** Settings few cards need; open when they say something. */
export function More({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  const [open, setOpen] = useState(count > 0);
  return (
    <details className="workspace-settings-group" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>
        {title}
        {count > 0 && <span className="workspace-setting-note">{count === 1 ? '1 filter' : `${count} filters`}</span>}
      </summary>
      {children}
    </details>
  );
}

export function Segments<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: [T, string, string?][];
  onChange: (value: T) => void;
}) {
  const note = options.find((option) => option[0] === value)?.[2];
  return (
    <div className="workspace-field">
      <span>{label}</span>
      <div className="workspace-segments" role="group" aria-label={label}>
        {options.map(([option, name]) => (
          <button type="button" key={option} aria-pressed={option === value} onClick={() => onChange(option)}>
            {name}
          </button>
        ))}
      </div>
      {note && <Note>{note}</Note>}
    </div>
  );
}

export function FieldPicker({
  label,
  value,
  fields,
  none = 'Choose a field…',
  onChange,
}: {
  label: string;
  value: string;
  fields: FieldInfo[];
  /** What no field reads as. */
  none?: string;
  onChange: (value: string) => void;
}) {
  const choices: { value: string; label: string; note?: string }[] = [
    { value: '', label: none },
    ...fields.map((field) => ({ value: field.name, label: fieldName(field.name) })),
  ];
  if (value && !fields.some((field) => field.name === value))
    choices.push({ value, label: fieldName(value), note: 'Not in these runs' });
  return (
    <div className="workspace-field">
      <span>{label}</span>
      <Select label={label} value={value} choices={choices} onChange={onChange} />
    </div>
  );
}

export function Choice<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: [T, string][];
  onChange: (value: T) => void;
}) {
  return (
    <div className="workspace-field">
      <span>{label}</span>
      <Select
        label={label}
        value={value}
        choices={options.map(([value, label]) => ({ value, label }))}
        onChange={(value) => onChange(value as T)}
      />
    </div>
  );
}

export function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /> {label}
    </label>
  );
}

export function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="workspace-field">
      <span>
        {label}: {value}
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

export function NumberField({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="workspace-field">
      <span>{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Math.max(min, Math.min(max, Number(e.target.value) || min)))}
      />
    </label>
  );
}

export const Note = ({ children }: { children: ReactNode }) => <p className="workspace-setting-note">{children}</p>;

const operators: [Filter['op'], string][] = [
  ['eq', 'equals'],
  ['ne', 'does not equal'],
  ['lt', 'less than'],
  ['le', 'at most'],
  ['gt', 'greater than'],
  ['ge', 'at least'],
  ['contains', 'contains'],
  ['exists', 'exists'],
];

const opText = Object.fromEntries(operators);
/** A filter as one line of text. */
export const filterText = (filter: Filter) =>
  `${fieldName(filter.field) || '…'} ${opText[filter.op]}${filter.op === 'exists' ? '' : ` ${filter.value}`}`;

export function FilterRow({
  number,
  filter,
  fields,
  onChange,
  onRemove,
}: {
  number: number | string;
  filter: Filter;
  fields: FieldInfo[];
  onChange: (value: Partial<Filter>) => void;
  onRemove: () => void;
}) {
  return (
    <div className="workspace-filter">
      <FieldPicker
        label={`Filter field ${number}`}
        value={filter.field}
        fields={fields}
        onChange={(field) => onChange({ field })}
      />
      <Choice label={`Operator ${number}`} value={filter.op} options={operators} onChange={(op) => onChange({ op })} />
      {filter.op !== 'exists' && (
        <label className="workspace-field">
          <span>Value</span>
          <input
            value={filter.value}
            placeholder="Text, number, or JSON"
            onChange={(e) => onChange({ value: e.target.value })}
          />
        </label>
      )}
      <ActionIcon label={`Remove filter ${number}`} onClick={onRemove}>
        <Trash2 size={15} />
      </ActionIcon>
    </div>
  );
}

export function RecordFilters({ card, fields, patch }: SettingsProps) {
  const update = (index: number, value: Partial<Filter>) =>
    patch({ filters: card.filters.map((filter, i) => (i === index ? { ...filter, ...value } : filter)) });
  return (
    <>
      <span className="workspace-label">Only records where</span>
      {card.filters.length > 1 && (
        <Choice
          label="Combine"
          value={card.filterMode}
          options={[
            ['all', 'All of these hold'],
            ['any', 'Any of these holds'],
          ]}
          onChange={(filterMode) => patch({ filterMode })}
        />
      )}
      {card.filters.map((filter, index) => (
        <FilterRow
          key={index}
          number={index + 1}
          filter={filter}
          fields={fields}
          onChange={(value) => update(index, value)}
          onRemove={() => patch({ filters: card.filters.filter((_, i) => i !== index) })}
        />
      ))}
      <Button small onClick={() => patch({ filters: [...card.filters, { field: '', op: 'eq', value: '' }] })}>
        <Plus size={14} /> Add filter
      </Button>
    </>
  );
}

const plain = new Set(['number', 'string', 'boolean']);
export function GroupBy({ label, card, fields, patch }: SettingsProps & { label: string }) {
  return (
    <div className="workspace-grouping">
      <span className="workspace-label">{label}</span>
      <div className="workspace-grouping-fields">
        {card.split.map((field) => (
          <button
            className="workspace-group-chip"
            key={field}
            aria-label={`Remove grouping ${field}`}
            onClick={() => patch({ split: card.split.filter((f) => f !== field) })}
          >
            {fieldName(field)} ×
          </button>
        ))}
        {!card.split.length && <span className="workspace-setting-note">Everything together</span>}
        <Select
          label="Add grouping field"
          value=""
          choices={[
            { value: '', label: 'Add field…' },
            ...fields
              .filter((field) => !card.split.includes(field.name) && [...field.kinds].every((kind) => plain.has(kind)))
              .map((field) => ({ value: field.name, label: fieldName(field.name) })),
          ]}
          onChange={(field) => {
            if (field) patch({ split: [...card.split, field] });
          }}
        />
      </div>
    </div>
  );
}

export function PointOrder({ label, card, fields, patch }: SettingsProps & { label: string }) {
  const order: Card['pointOrder'] = card.pointOrder;
  return (
    <>
      <FieldPicker
        label={label}
        value={order.field}
        fields={fields}
        onChange={(field) => patch({ pointOrder: { ...order, field } })}
      />
      <Segments
        label="Direction"
        value={order.descending ? 'descending' : 'ascending'}
        options={[
          ['ascending', 'Ascending'],
          ['descending', 'Descending'],
        ]}
        onChange={(direction) => patch({ pointOrder: { ...order, descending: direction === 'descending' } })}
      />
    </>
  );
}
