import { useMemo, useState } from 'react';
import { ChartLine } from 'lucide-react';
import { type Aggregation, type Smoothing } from '../model';
import { ScalarPlot } from '../ScalarPlot';
import { scalarData } from '../scalarData';
import {
  Choice,
  FieldPicker,
  Group,
  GroupBy,
  More,
  Note,
  PointOrder,
  RecordFilters,
  Segments,
  Slider,
  Toggle,
} from './settings';
import type { CardKind, ContentProps, SettingsProps } from './types';

function ScalarContent({ card, rows }: ContentProps) {
  const [hidden, setHidden] = useState<string[]>([]);
  const { lines, count, omitted } = useMemo(() => scalarData(rows, card), [rows, card]);
  const primary = lines.filter((line) => !line.member);
  const toggle = (group: string) =>
    setHidden((hidden) => (hidden.includes(group) ? hidden.filter((n) => n !== group) : [...hidden, group]));
  return (
    <>
      {count ? (
        <ScalarPlot lines={lines} card={card} hidden={hidden} />
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
        {omitted ? ` · ${omitted} records without valid axes` : ''}
      </div>
    </>
  );
}

function Aggregate({ card, patch }: SettingsProps) {
  const aggregation: Aggregation = card.aggregation ?? { center: 'none', band: 'minmax', showMembers: false };
  const update = (value: Partial<Aggregation>) => patch({ aggregation: { ...aggregation, ...value } });
  return (
    <>
      <Choice
        label="Combine runs"
        value={aggregation.center}
        options={[
          ['none', 'No'],
          ['mean', 'Mean'],
          ['median', 'Median'],
          ['min', 'Minimum'],
          ['max', 'Maximum'],
        ]}
        onChange={(center) => {
          // Combined runs share a line, so Run no longer tells lines apart.
          const split = card.split.filter((field) => field !== '@run');
          const was = aggregation.center !== 'none';
          patch({
            aggregation: { ...aggregation, center },
            ...((center !== 'none') !== was ? { split: was ? ['@run', ...split] : split } : {}),
          });
        }}
      />
      {aggregation.center !== 'none' && (
        <>
          <Choice
            label="Band around it"
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
            label="Show every run behind it"
            checked={aggregation.showMembers}
            onChange={(showMembers) => update({ showMembers })}
          />
          <Note>
            {card.split.includes('@run')
              ? 'With one line per run there is nothing to combine: remove Run above.'
              : 'Each run counts with its last value at or before X, only where it has records.'}
          </Note>
        </>
      )}
    </>
  );
}

// What a smoothing's value means, its range, and where it starts.
const kinds: Record<Smoothing['kind'], { name: string; value: string; range: [number, number, number, number] }> = {
  ema: { name: 'Moving average', value: 'Weight of the past', range: [0.01, 0.99, 0.01, 0.6] },
  twema: { name: 'Moving average over X', value: 'Weight of the past per step', range: [0.01, 0.99, 0.01, 0.6] },
  gaussian: { name: 'Gaussian', value: 'Width in points', range: [0.5, 50, 0.5, 3] },
  window: { name: 'Running average', value: 'Points', range: [2, 200, 1, 10] },
};

function Smoothen({ card, patch }: SettingsProps) {
  const smoothing = card.smoothing;
  const kind = smoothing && kinds[smoothing.kind];
  return (
    <>
      <Choice
        label="Smooth"
        value={smoothing?.kind ?? 'none'}
        options={[['none', 'No'], ...Object.entries(kinds).map(([kind, { name }]): [string, string] => [kind, name])]}
        onChange={(chosen) =>
          patch({
            smoothing:
              chosen === 'none'
                ? undefined
                : {
                    kind: chosen as Smoothing['kind'],
                    value: kinds[chosen as Smoothing['kind']].range[3],
                    original: smoothing?.original ?? true,
                  },
          })
        }
      />
      {smoothing && kind && (
        <>
          <Slider
            label={kind.value}
            value={smoothing.value}
            min={kind.range[0]}
            max={kind.range[1]}
            step={kind.range[2]}
            onChange={(value) => patch({ smoothing: { ...smoothing, value } })}
          />
          <Toggle
            label="Show the original faded"
            checked={smoothing.original}
            onChange={(original) => patch({ smoothing: { ...smoothing, original } })}
          />
        </>
      )}
    </>
  );
}

function ScalarSettings(props: SettingsProps) {
  const { card, fields, patch } = props;
  const numeric = fields.filter((field) => field.kinds.has('number'));
  return (
    <>
      <Group title="Axes">
        <FieldPicker label="Y" value={card.y} fields={numeric} onChange={(y) => patch({ y })} />
        <Toggle label="Logarithmic Y" checked={card.logY} onChange={(logY) => patch({ logY })} />
        <FieldPicker label="X" value={card.x} fields={numeric} onChange={(x) => patch({ x })} />
        <Toggle label="Logarithmic X" checked={card.logX} onChange={(logX) => patch({ logX })} />
      </Group>
      <Group title="Lines">
        <GroupBy label="One line per" {...props} />
        <Aggregate {...props} />
        <Segments
          label="Draw"
          value={card.marks}
          options={[
            ['lines', 'Lines'],
            ['markers', 'Points'],
            ['lines+markers', 'Both'],
          ]}
          onChange={(marks) => patch({ marks })}
        />
        <Choice
          label="Legend order"
          value={card.lineOrder}
          options={[
            ['label', 'By name'],
            ['minimum', 'Lowest minimum first'],
            ['maximum', 'Lowest maximum first'],
          ]}
          onChange={(lineOrder) => patch({ lineOrder })}
        />
      </Group>
      <Group title="Smoothing">
        <Smoothen {...props} />
      </Group>
      <More title="Records" count={card.filters.length}>
        <RecordFilters {...props} />
        <PointOrder label="Connect points by" {...props} />
      </More>
    </>
  );
}

export const scalar: CardKind = {
  label: 'Scalar',
  icon: ChartLine,
  defaults: { title: 'Loss over training', height: 360, mode: 'gallery' },
  usesRecords: true,
  suggest: (fields) => {
    const numeric = fields.filter((f) => f.kinds.has('number') && !/^[@_/]|^step$/.test(f.name)).map((f) => f.name);
    return numeric.length && !numeric.includes('loss') ? { y: numeric[0], title: numeric[0] } : {};
  },
  isValid: (card) => !!card.x && !!card.y,
  Content: ScalarContent,
  Settings: ScalarSettings,
};
