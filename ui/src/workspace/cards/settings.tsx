import { useState, type ReactNode } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { ActionIcon, Button } from '../../components/Button';
import { Select } from '../../components/Select';
import type { Card, FieldInfo, Filter } from '../model';
import type { SettingsProps } from './types';

export function FieldPicker({
  label,
  value,
  fields,
  onChange,
}: {
  label: string;
  value: string;
  fields: FieldInfo[];
  onChange: (value: string) => void;
}) {
  const choices = [
    { value: '', label: 'Choose a field…' },
    ...fields.map((field) => ({ value: field.name, label: field.name, note: [...field.kinds].join(', ') })),
  ];
  if (value && !fields.some((field) => field.name === value))
    choices.push({ value, label: value, note: 'Not present in the selected records' });
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

/** Data, Mapping and Appearance tabs; a card kind fills in each one. */
export function SettingsTabs({ tabs }: { tabs: Record<string, ReactNode> }) {
  const [tab, setTab] = useState(Object.keys(tabs)[0]);
  return (
    <>
      <div className="tabs" role="tablist" aria-label="Card settings">
        {Object.keys(tabs).map((name) => (
          <button role="tab" key={name} aria-selected={tab === name} onClick={() => setTab(name)}>
            {name}
          </button>
        ))}
      </div>
      <div className="workspace-settings-panel">{tabs[tab]}</div>
    </>
  );
}

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

export function RecordFilters({ card, fields, patch }: SettingsProps) {
  const update = (index: number, value: Partial<Filter>) =>
    patch({ filters: card.filters.map((filter, i) => (i === index ? { ...filter, ...value } : filter)) });
  return (
    <>
      <h3 className="workspace-settings-heading">Record filters</h3>
      <Note>Choose which records this card displays.</Note>
      {card.filters.length > 1 && (
        <Choice
          label="Combine filter conditions"
          value={card.filterMode}
          options={[
            ['all', 'All conditions (AND)'],
            ['any', 'Any condition (OR)'],
          ]}
          onChange={(filterMode) => patch({ filterMode })}
        />
      )}
      {card.filters.map((filter, index) => (
        <div className="workspace-filter" key={index}>
          <FieldPicker
            label={`Filter field ${index + 1}`}
            value={filter.field}
            fields={fields}
            onChange={(field) => update(index, { field })}
          />
          <Choice
            label={`Operator ${index + 1}`}
            value={filter.op}
            options={operators}
            onChange={(op) => update(index, { op })}
          />
          {filter.op !== 'exists' && (
            <label className="workspace-field">
              <span>Value</span>
              <input
                value={filter.value}
                placeholder="Text, number, or JSON"
                onChange={(e) => update(index, { value: e.target.value })}
              />
            </label>
          )}
          <ActionIcon
            label={`Remove filter ${index + 1}`}
            onClick={() => patch({ filters: card.filters.filter((_, i) => i !== index) })}
          >
            <Trash2 size={15} />
          </ActionIcon>
        </div>
      ))}
      <Button small onClick={() => patch({ filters: [...card.filters, { field: '', op: 'eq', value: '' }] })}>
        <Plus size={14} /> Add filter
      </Button>
    </>
  );
}

export function GroupBy({ card, fields, patch }: SettingsProps) {
  const name = (field: string) => (field === '@run' ? 'Run' : field);
  return (
    <div className="workspace-grouping">
      <span className="workspace-label">Group by</span>
      <div className="workspace-grouping-fields">
        {card.split.map((field) => (
          <button
            className="workspace-group-chip"
            key={field}
            aria-label={`Remove grouping ${field}`}
            onClick={() => patch({ split: card.split.filter((f) => f !== field) })}
          >
            {name(field)} ×
          </button>
        ))}
        {!card.split.length && <span className="workspace-setting-note">All records</span>}
        <Select
          label="Add grouping field"
          value=""
          choices={[
            { value: '', label: 'Add field…' },
            ...fields
              .filter((field) => !card.split.includes(field.name))
              .map((field) => ({ value: field.name, label: name(field.name) })),
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
      <Choice
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
