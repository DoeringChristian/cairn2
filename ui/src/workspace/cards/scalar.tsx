import { useMemo, useState } from 'react';
import { ChartLine } from 'lucide-react';
import { Button } from '../../components/Button';
import { WorkspaceDialog } from '../Dialog';
import { label, valueAt, type Aggregation, type Observation } from '../model';
import { ScalarPlot } from '../ScalarPlot';
import { scalarData } from '../scalarData';
import { display, Inspector, recordId } from './records';
import { Choice, FieldPicker, GroupBy, Note, PointOrder, RecordFilters, SettingsTabs, Toggle } from './settings';
import type { CardKind, ContentProps, SettingsProps } from './types';

function ScalarContent({ card, rows }: ContentProps) {
  const [inspected, setInspected] = useState<Observation>();
  const [contributors, setContributors] = useState<Observation[]>();
  const [hidden, setHidden] = useState<string[]>([]);
  const { lines, count, omitted } = useMemo(() => scalarData(rows, card), [rows, card]);
  const primary = lines.filter((line) => !line.member);
  const toggle = (group: string) =>
    setHidden((hidden) => (hidden.includes(group) ? hidden.filter((n) => n !== group) : [...hidden, group]));
  const inspect = (records: Observation[]) => {
    if (records.length === 1) setInspected(records[0]);
    else if (records.length) setContributors(records);
  };
  return (
    <>
      {count ? (
        <ScalarPlot lines={lines} card={card} hidden={hidden} onInspect={inspect} />
      ) : (
        <div className="workspace-empty">
          No numeric observations for <code>{card.x}</code> and <code>{card.y}</code>. Choose fields in card settings.
        </div>
      )}
      {!!count && (
        <div className="workspace-legend" aria-label="Chart series">
          {primary.map((line) => (
            <button key={line.key} aria-pressed={!hidden.includes(line.groupKey)} onClick={() => toggle(line.groupKey)}>
              <span style={{ background: line.color }} />
              {line.name}
            </button>
          ))}
        </div>
      )}
      <div className="workspace-card-note">
        {count.toLocaleString()} points · {primary.length} lines
        {omitted ? ` · ${omitted} records without valid axes` : ''} · Click a point to inspect
      </div>
      {contributors && (
        <WorkspaceDialog title="Contributing records" onClose={() => setContributors(undefined)}>
          <p>Member observations used for this group point.</p>
          {contributors.map((row) => (
            <p key={recordId(row)}>
              <Button
                onClick={() => {
                  setContributors(undefined);
                  setInspected(row);
                }}
              >
                {label(row.run)} · record {row.ordinal} · {card.x}: {display(valueAt(row, card.x))} · {card.y}:{' '}
                {display(valueAt(row, card.y))}
              </Button>
            </p>
          ))}
        </WorkspaceDialog>
      )}
      {inspected && <Inspector row={inspected} onClose={() => setInspected(undefined)} />}
    </>
  );
}

function Aggregate({ card, patch }: SettingsProps) {
  const aggregation: Aggregation = card.aggregation ?? { center: 'none', band: 'minmax', showMembers: false };
  const update = (value: Partial<Aggregation>) => patch({ aggregation: { ...aggregation, ...value } });
  return (
    <>
      <Choice
        label="Group center"
        value={aggregation.center}
        options={[
          ['none', 'None · keep every observation'],
          ['mean', 'Mean'],
          ['median', 'Median'],
          ['min', 'Minimum'],
          ['max', 'Maximum'],
        ]}
        onChange={(center) => update({ center })}
      />
      {aggregation.center !== 'none' && (
        <>
          <Choice
            label="Group band"
            value={aggregation.band}
            options={[
              ['none', 'None'],
              ['minmax', 'Min – max'],
              ['std', '± standard deviation'],
              ['sem', '± standard error'],
            ]}
            onChange={(band) => update({ band })}
          />
          <Toggle
            label="Show member runs"
            checked={aggregation.showMembers}
            onChange={(showMembers) => update({ showMembers })}
          />
          <Note>
            {card.split.includes('@run') ? 'Remove @run from grouping to combine runs. ' : ''}
            Each run contributes its latest observation at or before X, within its recorded range. At duplicate X
            values, the last record wins. Bands summarize those member values; no extrapolation.
          </Note>
        </>
      )}
    </>
  );
}

function ScalarSettings(props: SettingsProps) {
  const { card, fields, patch } = props;
  const numeric = fields.filter((field) => field.kinds.has('number'));
  return (
    <SettingsTabs
      tabs={{
        Data: (
          <>
            <Note>Filters apply to this card’s visible runs.</Note>
            <RecordFilters {...props} />
          </>
        ),
        Mapping: (
          <>
            <FieldPicker label="X field" value={card.x} fields={numeric} onChange={(x) => patch({ x })} />
            <FieldPicker label="Y field" value={card.y} fields={numeric} onChange={(y) => patch({ y })} />
            <GroupBy {...props} />
            <Aggregate {...props} />
            <PointOrder label="Point order" {...props} />
            <Choice
              label="Line order"
              value={card.lineOrder}
              options={[
                ['label', 'Label'],
                ['minimum', 'Minimum Y'],
                ['maximum', 'Maximum Y'],
              ]}
              onChange={(lineOrder) => patch({ lineOrder })}
            />
          </>
        ),
        Appearance: (
          <>
            <Choice
              label="Draw"
              value={card.marks}
              options={[
                ['lines', 'Lines'],
                ['markers', 'Points'],
                ['lines+markers', 'Lines and points'],
              ]}
              onChange={(marks) => patch({ marks })}
            />
            <Toggle label="Logarithmic X" checked={card.logX} onChange={(logX) => patch({ logX })} />
            <Toggle label="Logarithmic Y" checked={card.logY} onChange={(logY) => patch({ logY })} />
          </>
        ),
      }}
    />
  );
}

export const scalar: CardKind = {
  label: 'Scalar',
  icon: ChartLine,
  defaults: { title: 'Loss over training', height: 360, mode: 'gallery' },
  usesRecords: true,
  isValid: (card) => !!card.x && !!card.y,
  Content: ScalarContent,
  Settings: ScalarSettings,
};
