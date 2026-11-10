import { useEffect, useMemo, useState, type ComponentType, type CSSProperties } from 'react';
import { ChevronLeft, ChevronRight, Pause, Play, type LucideIcon } from 'lucide-react';
import { envelope, type BlobRef, type Envelope } from '../../api';
import { ActionIcon } from '../../components/Button';
import { MediaLightbox } from '../../components/MediaLightbox';
import { Select } from '../../components/Select';
import { useImageBlob } from '../../components/useBlobUrl';
import { WorkspaceDialog } from '../Dialog';
import { compare, groups, label, sorted, typeOf, valueAt, type Card, type Observation } from '../model';
import { runColor } from '../presentation';
import { display, Inspector, recordId } from './records';
import { Choice, FieldPicker, GroupBy, Note, NumberField, PointOrder, RecordFilters, SettingsTabs } from './settings';
import type { CardKind, ContentProps, SettingsProps } from './types';

/** A shared 2D range, for figures whose axes are linked across tiles. */
export type Range = Record<string, unknown>;

export type TileProps = {
  card: Card;
  value: Envelope;
  /** The run and caption of the record, for accessible names. */
  title: string;
  onExpand: () => void;
  linked?: Range;
  onRange: (range: Range) => void;
};

/** One kind of logged media, shown as gallery, comparison or sequence tiles. */
export type MediaFormat = {
  label: string;
  icon: LucideIcon;
  /** The card's default title and the record field it reads. */
  title: string;
  field: string;
  /** Plural for empty states, e.g. "images". */
  noun: string;
  /** Whether a field type (see `typeOf`) holds this media. */
  accepts: (type: string) => boolean;
  Tile: ComponentType<TileProps>;
  Expanded: ComponentType<{ card: Card; row: Observation; value: Envelope; onClose: () => void }>;
  /** Extra settings in the Appearance tab. */
  Appearance?: ComponentType<SettingsProps>;
};

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** The observations of each group at the cursor's axis value, by the card's matching rule. */
function pick(card: Card, points: Observation[], current: ReturnType<typeof valueAt>) {
  const axis = (row: Observation) => valueAt(row, card.axis);
  const exact = points.filter((row) => compare(axis(row), current) === 0);
  if (exact.length) return exact;
  if (card.matching === 'exact' || current === undefined) return [];
  const valid = points.filter((row) => axis(row) != null);
  if (card.matching === 'previous') {
    const before = valid.filter((row) => compare(axis(row), current) <= 0).sort((a, b) => compare(axis(b), axis(a)));
    return before.length ? before.filter((row) => compare(axis(row), axis(before[0])) === 0) : [];
  }
  if (!finite(current)) return [];
  const nearest = valid
    .filter((row) => finite(axis(row)))
    .sort((a, b) => Math.abs(Number(axis(a)) - current) - Math.abs(Number(axis(b)) - current));
  return nearest.length ? nearest.filter((row) => compare(axis(row), axis(nearest[0])) === 0) : [];
}

function MediaContent({ format, card, rows }: ContentProps & { format: MediaFormat }) {
  const [page, setPage] = useState(0);
  const [cursor, setCursor] = useState<string>();
  const [follow, setFollow] = useState(true);
  const [playing, setPlaying] = useState(false);
  const [chosen, setChosen] = useState('');
  const [expanded, setExpanded] = useState<Observation>();
  const [inspected, setInspected] = useState<Observation>();
  const [linked, setLinked] = useState<Range>();

  const media = useMemo(
    () =>
      sorted(
        rows.filter((row) => format.accepts(typeOf(valueAt(row, card.value)))),
        card.pointOrder,
      ),
    [format, rows, card],
  );
  const series = useMemo(() => groups(media, card.split), [media, card.split]);
  const axisValues = useMemo(() => {
    const values = media.map((row) => valueAt(row, card.axis)).filter((v) => v != null && typeof v !== 'object');
    return [...new Map(values.map((v) => [JSON.stringify(v), v!])).values()].sort(compare);
  }, [media, card.axis]);

  const cursorIndex = follow
    ? Math.max(0, axisValues.length - 1)
    : Math.max(
        0,
        axisValues.findIndex((v) => JSON.stringify(v) === cursor),
      );
  const current = axisValues[cursorIndex];
  const setIndex = (index: number) => {
    setFollow(false);
    setCursor(JSON.stringify(axisValues[Math.max(0, Math.min(axisValues.length - 1, index))]));
  };

  useEffect(() => {
    setPage(0);
    setChosen('');
    setCursor(undefined);
    setFollow(true);
    setPlaying(false);
    setLinked(undefined);
  }, [card]);
  useEffect(() => {
    if (!playing || axisValues.length < 2) return;
    const timer = setInterval(() => setIndex((cursorIndex + 1) % axisValues.length), 900);
    return () => clearInterval(timer);
  }, [playing, cursorIndex, axisValues]);

  const selectedGroup = series.find((group) => group.key === chosen) || series[0];
  const shownGroups = card.mode === 'sequence' ? (selectedGroup ? [selectedGroup] : []) : series;
  const tiles: { key: string; name: string; row?: Observation }[] =
    card.mode === 'gallery'
      ? media.map((row) => ({ key: recordId(row), name: label(row.run), row }))
      : shownGroups.flatMap((group) => {
          const found = pick(card, group.rows, current);
          if (!found.length) return [{ key: group.key, name: group.name }];
          return found.map((row) => ({ key: recordId(row), name: group.name, row }));
        });
  const totalPages = Math.max(1, Math.ceil(tiles.length / card.pageSize));
  const currentPage = Math.min(page, totalPages - 1);
  const visible = tiles.slice(currentPage * card.pageSize, (currentPage + 1) * card.pageSize);

  const emitRange = (range: Range) => {
    if (!card.linkAxes) return;
    const axes = Object.fromEntries(
      Object.entries(range).filter(([key]) => /^[xy]axis\.(range\[[01]\]|autorange)$/.test(key)),
    );
    if (Object.keys(axes).length)
      setLinked((previous) => (JSON.stringify(previous) === JSON.stringify(axes) ? previous : axes));
  };
  const matching =
    card.mode === 'gallery'
      ? `Ordered by ${card.pointOrder.field}`
      : {
          exact: 'Exact axis matches',
          previous: 'Previous available observation',
          nearest: 'Nearest available observation',
        }[card.matching];

  return (
    <>
      {card.mode !== 'gallery' && !!media.length && (
        <div className="workspace-navigator">
          <div className="workspace-navigator-top">
            <span>
              <strong>{card.axis}</strong> <output>{display(current)}</output>
            </span>
            {card.mode === 'sequence' && (
              <Select
                label="Sequence"
                value={selectedGroup?.key || ''}
                choices={series.map((group) => ({ value: group.key, label: group.name }))}
                onChange={(value) => {
                  setChosen(value);
                  setPage(0);
                }}
              />
            )}
            <label>
              <input
                type="checkbox"
                checked={follow}
                onChange={(e) => {
                  setFollow(e.target.checked);
                  setCursor(JSON.stringify(current));
                }}
              />{' '}
              Latest
            </label>
          </div>
          <div className="workspace-slider">
            <ActionIcon
              label="Previous axis value"
              disabled={!axisValues.length || cursorIndex === 0}
              onClick={() => setIndex(cursorIndex - 1)}
            >
              <ChevronLeft size={16} />
            </ActionIcon>
            <input
              type="range"
              aria-label={`Browse ${card.axis}`}
              min={0}
              max={Math.max(0, axisValues.length - 1)}
              value={cursorIndex}
              disabled={axisValues.length < 2}
              onChange={(e) => {
                setIndex(Number(e.target.value));
                setPage(0);
              }}
            />
            <ActionIcon
              label="Next axis value"
              disabled={cursorIndex >= axisValues.length - 1}
              onClick={() => setIndex(cursorIndex + 1)}
            >
              <ChevronRight size={16} />
            </ActionIcon>
            <ActionIcon
              label={playing ? 'Pause sequence' : 'Play sequence'}
              disabled={axisValues.length < 2}
              onClick={() => {
                if (!playing && cursorIndex === axisValues.length - 1) setIndex(0);
                setPlaying(!playing);
              }}
            >
              {playing ? <Pause size={15} /> : <Play size={15} />}
            </ActionIcon>
          </div>
        </div>
      )}

      {!media.length ? (
        <div className="workspace-empty">
          No {format.noun} in <code>{card.value}</code>. Choose a logged field in settings.
        </div>
      ) : !axisValues.length && card.mode !== 'gallery' ? (
        <div className="workspace-empty">
          No values for navigation field <code>{card.axis}</code>. Choose another field or gallery mode.
        </div>
      ) : (
        <div
          className="workspace-media-grid"
          style={{ '--tile-columns': card.mode === 'sequence' ? 1 : card.columns } as CSSProperties}
        >
          {visible.map(({ key, name, row }) => (
            <div key={key} className="workspace-media-tile">
              <div className="workspace-tile-heading">
                {row && (
                  <i
                    className="workspace-run-swatch"
                    style={{ background: runColor(row.artifact_id) }}
                    aria-hidden="true"
                  />
                )}
                <span title={name}>{name}</span>
                {row && (
                  <button className="text-button" onClick={() => setInspected(row)}>
                    #{row.ordinal}
                  </button>
                )}
              </div>
              {row ? (
                <>
                  <format.Tile
                    card={card}
                    value={envelope(valueAt(row, card.value))!}
                    title={`${label(row.run)} · ${card.caption ? display(valueAt(row, card.caption)) : card.value}`}
                    onExpand={() => setExpanded(row)}
                    linked={card.linkAxes ? linked : undefined}
                    onRange={emitRange}
                  />
                  <div className="workspace-tile-caption">
                    <span>
                      {card.axis}: {display(valueAt(row, card.axis))}
                    </span>
                    {card.caption && (
                      <span>
                        {card.caption}: {display(valueAt(row, card.caption))}
                      </span>
                    )}
                  </div>
                </>
              ) : (
                <div className="workspace-empty">No observation at {display(current)}</div>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="workspace-card-note">
        <span>
          {media.length} records · {matching}
        </span>
        <span>
          <button
            disabled={currentPage === 0}
            onClick={() => setPage(currentPage - 1)}
            aria-label="Previous gallery page"
          >
            ‹
          </button>{' '}
          {currentPage + 1} / {totalPages}{' '}
          <button
            disabled={currentPage >= totalPages - 1}
            onClick={() => setPage(currentPage + 1)}
            aria-label="Next gallery page"
          >
            ›
          </button>
        </span>
      </div>

      {expanded && (
        <format.Expanded
          card={card}
          row={expanded}
          value={envelope(valueAt(expanded, card.value))!}
          onClose={() => setExpanded(undefined)}
        />
      )}
      {inspected && <Inspector row={inspected} onClose={() => setInspected(undefined)} />}
    </>
  );
}

const plainTypes = new Set(['number', 'string', 'boolean', 'object', 'undefined']);

function MediaSettings({ format, ...props }: SettingsProps & { format: MediaFormat }) {
  const { card, fields, patch } = props;
  const media = fields.filter((field) => [...field.kinds].some(format.accepts));
  const axes = fields.filter((field) => [...field.kinds].every((kind) => plainTypes.has(kind)));
  return (
    <SettingsTabs
      tabs={{
        Data: (
          <>
            <Note>Filters apply to this card’s visible runs.</Note>
            <FieldPicker
              label={`${format.label} field`}
              value={card.value}
              fields={media}
              onChange={(value) => patch({ value })}
            />
            <RecordFilters {...props} />
          </>
        ),
        Mapping: (
          <>
            <Choice
              label="View"
              value={card.mode}
              options={[
                ['gallery', 'Gallery · ordered records'],
                ['compare', 'Compare · one group per tile'],
                ['sequence', 'Sequence · one group with a slider'],
              ]}
              onChange={(mode) => patch({ mode })}
            />
            {card.mode !== 'gallery' && (
              <>
                <FieldPicker label="Slider axis" value={card.axis} fields={axes} onChange={(axis) => patch({ axis })} />
                <Choice
                  label="Match observations"
                  value={card.matching}
                  options={[
                    ['exact', 'Exact value'],
                    ['previous', 'Previous available'],
                    ['nearest', 'Nearest (numeric axes)'],
                  ]}
                  onChange={(matching) => patch({ matching })}
                />
                <Note>
                  Every tile shows its actual axis value. Missing matches remain empty; observations are never
                  interpolated.
                </Note>
              </>
            )}
            <GroupBy {...props} />
            <PointOrder label="Gallery / duplicate order" {...props} />
            <FieldPicker
              label="Caption field (optional)"
              value={card.caption}
              fields={fields}
              onChange={(caption) => patch({ caption })}
            />
          </>
        ),
        Appearance: (
          <>
            <NumberField
              label="Columns"
              value={card.columns}
              min={1}
              max={6}
              onChange={(columns) => patch({ columns })}
            />
            <NumberField
              label="Tiles per page"
              value={card.pageSize}
              min={1}
              max={24}
              onChange={(pageSize) => patch({ pageSize })}
            />
            {format.Appearance && <format.Appearance {...props} />}
          </>
        ),
      }}
    />
  );
}

/** A card kind for one media format. */
export function mediaKind(format: MediaFormat): CardKind {
  return {
    label: format.label,
    icon: format.icon,
    defaults: { title: format.title, value: format.field },
    usesRecords: true,
    isValid: (card) => !!card.value && (card.mode === 'gallery' || !!card.axis),
    Content: (props) => <MediaContent format={format} {...props} />,
    Settings: (props) => <MediaSettings format={format} {...props} />,
  };
}

/** A blob in the shared media lightbox, once its bytes have loaded. */
export function BlobLightbox({ blob, name, onClose }: { blob: BlobRef; name: string; onClose: () => void }) {
  const { url, mime, error } = useImageBlob(blob);
  if (url) return <MediaLightbox items={[{ url, mime, name }]} onClose={onClose} />;
  return (
    <WorkspaceDialog title={name} onClose={onClose}>
      <p>{error?.message || 'Loading…'}</p>
      {error && <DownloadBlob blob={blob} />}
    </WorkspaceDialog>
  );
}

export const DownloadBlob = ({ blob }: { blob: BlobRef }) => (
  <a href={`/v1/blobs/${blob.digest}`} download={blob.name || blob.digest}>
    Download original
  </a>
);

/** The lightbox title of a record: its run and axis value. */
export const recordName = (card: Card, row: Observation) =>
  `${label(row.run)} · ${card.axis} ${display(valueAt(row, card.axis))}`;
