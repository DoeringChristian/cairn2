import { useEffect, useMemo, useRef, useState, type ComponentType, type CSSProperties, type ReactNode } from 'react';
import { ChevronLeft, ChevronRight, Maximize2, Pause, Play, type LucideIcon } from 'lucide-react';
import { blobPath, envelope, type BlobRef, type Envelope } from '../../api';
import { ActionIcon } from '../../components/Button';
import { MediaLightbox } from '../../components/MediaLightbox';
import { Select } from '../../components/Select';
import { preloadBlob, useImageBlob } from '../../components/useBlobUrl';
import { WorkspaceDialog } from '../Dialog';
import { compare, groups, label, sorted, typeOf, valueAt, type Card, type Observation } from '../model';
import { runColor } from '../presentation';
import { SharedZoom } from '../zoom';
import { display, Inspector, recordId } from './records';
import {
  Choice,
  FieldPicker,
  Group,
  GroupBy,
  More,
  NumberField,
  PointOrder,
  RecordFilters,
  Segments,
} from './settings';
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
  /** The zoom every tile of the card shares. */
  zoom: SharedZoom;
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
  const [zoom] = useState(() => new SharedZoom());

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
    // At the end, the newest stays shown as it arrives.
    setFollow(index >= axisValues.length - 1);
    setCursor(JSON.stringify(axisValues[Math.max(0, Math.min(axisValues.length - 1, index))]));
  };

  useEffect(() => {
    setPage(0);
    setChosen('');
    setCursor(undefined);
    setFollow(true);
    setPlaying(false);
    setLinked(undefined);
    zoom.reset();
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
          return found.map((row, index) => ({ key: `${group.key}:${index}`, name: group.name, row }));
        });
  const totalPages = Math.max(1, Math.ceil(tiles.length / card.pageSize));
  const currentPage = Math.min(page, totalPages - 1);
  const visible = tiles.slice(currentPage * card.pageSize, (currentPage + 1) * card.pageSize);

  useEffect(() => {
    if (card.mode === 'gallery') return;
    for (const step of [1, -1, 2, -2]) {
      const near = axisValues[cursorIndex + step];
      if (near === undefined) continue;
      for (const group of shownGroups)
        for (const row of pick(card, group.rows, near)) {
          const blob = envelope(valueAt(row, card.value))?.blobs?.[0];
          if (blob) preloadBlob(blob);
        }
    }
  }, [card, axisValues, cursorIndex, shownGroups]);

  const play = () => {
    if (!playing && cursorIndex === axisValues.length - 1) setIndex(0);
    setPlaying(!playing);
  };
  const emitRange = (range: Range) => {
    if (!card.linkAxes) return;
    const axes = Object.fromEntries(
      Object.entries(range).filter(([key]) => /^[xy]axis\.(range\[[01]\]|autorange)$/.test(key)),
    );
    if (Object.keys(axes).length)
      setLinked((previous) => (JSON.stringify(previous) === JSON.stringify(axes) ? previous : axes));
  };
  const pages = totalPages > 1 && (
    <span className="workspace-pages">
      <button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)} aria-label="Previous gallery page">
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
  );
  const columns = card.mode === 'sequence' ? 1 : Math.min(card.columns, visible.length);

  return (
    <>
      {!media.length ? (
        <div className="workspace-empty">
          No {format.noun} in <code>{card.value}</code>. Choose a logged field in settings.
        </div>
      ) : !axisValues.length && card.mode !== 'gallery' ? (
        <div className="workspace-empty">
          No values for <code>{card.axis}</code>. Choose another field to step through in settings.
        </div>
      ) : (
        <div
          className={`workspace-media-grid workspace-media-${card.mode}`}
          style={{ '--tile-columns': Math.max(1, columns) } as CSSProperties}
        >
          {visible.map(({ key, name, row }) => (
            <div key={key} className="workspace-media-tile">
              {row ? (
                <format.Tile
                  card={card}
                  value={envelope(valueAt(row, card.value))!}
                  title={`${label(row.run)} · ${card.caption ? display(valueAt(row, card.caption)) : card.value}`}
                  onExpand={() => setExpanded(row)}
                  linked={card.linkAxes ? linked : undefined}
                  onRange={emitRange}
                  zoom={zoom}
                />
              ) : (
                <div className="workspace-empty workspace-tile-missing">No observation</div>
              )}
              <div className="workspace-tile-heading">
                {row && (
                  <i className="workspace-run-swatch" style={{ background: runColor(row.run.id) }} aria-hidden="true" />
                )}
                <span title={name}>{name}</span>
              </div>
              <div className="workspace-tile-caption">
                <span>
                  {card.axis}: {display(row ? valueAt(row, card.axis) : current)}
                </span>
                {row && card.caption && card.caption !== card.axis && (
                  <span>
                    {card.caption}: {display(valueAt(row, card.caption))}
                  </span>
                )}
                {row && (
                  <button className="text-button" title="Show the record" onClick={() => setInspected(row)}>
                    #{row.ordinal}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {!!media.length &&
        (card.mode !== 'gallery' ? (
          <div className="workspace-navigator">
            {card.mode === 'sequence' && series.length > 1 && (
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
            {axisValues.length > 1 && (
              <div className="workspace-slider">
                <ActionIcon label={playing ? 'Pause sequence' : 'Play sequence'} onClick={play}>
                  {playing ? <Pause size={15} /> : <Play size={15} />}
                </ActionIcon>
                <ActionIcon
                  label="Previous axis value"
                  disabled={cursorIndex === 0}
                  onClick={() => setIndex(cursorIndex - 1)}
                >
                  <ChevronLeft size={16} />
                </ActionIcon>
                <input
                  type="range"
                  aria-label={`Browse ${card.axis}`}
                  min={0}
                  max={axisValues.length - 1}
                  value={cursorIndex}
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
              </div>
            )}
            <output>
              {card.axis} {display(current)} ({cursorIndex + 1}/{axisValues.length})
            </output>
            {pages}
          </div>
        ) : (
          pages && <div className="workspace-navigator">{pages}</div>
        ))}

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
  const stepped = card.mode !== 'gallery';
  return (
    <>
      <Group title="Show">
        <FieldPicker
          label={`${format.label} field`}
          value={card.value}
          fields={media}
          onChange={(value) => patch({ value })}
        />
        <Segments
          label="View"
          value={card.mode}
          options={[
            ['compare', 'Side by side', 'Every run at the same step'],
            ['sequence', 'One run', 'One run, stepped through'],
            ['gallery', 'All records', 'Everything, in order'],
          ]}
          onChange={(mode) => patch({ mode })}
        />
      </Group>
      {stepped && (
        <Group title="Steps">
          <FieldPicker label="Step through" value={card.axis} fields={axes} onChange={(axis) => patch({ axis })} />
          <Choice
            label="Where a run has nothing at a step"
            value={card.matching}
            options={[
              ['exact', 'Leave it empty'],
              ['previous', 'Show its last one before'],
              ['nearest', 'Show its nearest one'],
            ]}
            onChange={(matching) => patch({ matching })}
          />
          <GroupBy label="One tile per" {...props} />
        </Group>
      )}
      <Group title="Look">
        {card.mode !== 'sequence' && (
          <NumberField
            label="Columns"
            value={card.columns}
            min={1}
            max={6}
            onChange={(columns) => patch({ columns })}
          />
        )}
        <FieldPicker
          label="Caption"
          value={card.caption}
          fields={axes}
          none="None"
          onChange={(caption) => patch({ caption })}
        />
        {format.Appearance && <format.Appearance {...props} />}
      </Group>
      <More title="Records" count={card.filters.length}>
        <RecordFilters {...props} />
        <PointOrder label="Order by" {...props} fields={axes} />
        <NumberField
          label="Tiles per page"
          value={card.pageSize}
          min={1}
          max={24}
          onChange={(pageSize) => patch({ pageSize })}
        />
      </More>
    </>
  );
}

/** A card kind for one media format. */
export function mediaKind(format: MediaFormat): CardKind {
  return {
    label: format.label,
    icon: format.icon,
    defaults: { title: format.title, value: format.field },
    usesRecords: true,
    suggest: (fields) => {
      const media = fields.filter((field) => [...field.kinds].some(format.accepts)).map((field) => field.name);
      return media.length && !media.includes(format.field) ? { value: media[0], title: media[0] } : {};
    },
    isValid: (card) => !!card.value && (card.mode === 'gallery' || !!card.axis),
    Content: (props) => <MediaContent format={format} {...props} />,
    Settings: (props) => <MediaSettings format={format} {...props} />,
  };
}

/** Keeps the shown media on screen until the next has loaded, so sweeping a slider never blanks a tile. */
export function Held({
  id,
  placeholder,
  children,
}: {
  id?: string;
  placeholder: ReactNode;
  children: (id: string, ready: () => void) => ReactNode;
}) {
  const [shown, setShown] = useState<string>();
  const latest = useRef(id);
  latest.current = id;
  const layers = [...new Set([shown, id])].filter((layer): layer is string => !!layer);
  return (
    <div className="workspace-held">
      {!shown && placeholder}
      {layers.map((layer) => (
        <div key={layer} className={`workspace-held-layer ${layer === shown ? '' : 'pending'}`}>
          {children(layer, () => {
            if (latest.current === layer) setShown(layer);
          })}
        </div>
      ))}
    </div>
  );
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

/** Opens a tile's media large, in its corner. */
export const Enlarge = ({ label, onClick }: { label: string; onClick: () => void }) => (
  <div className="workspace-enlarge">
    <ActionIcon label={label} onClick={onClick}>
      <Maximize2 size={14} />
    </ActionIcon>
  </div>
);

export const DownloadBlob = ({ blob }: { blob: BlobRef }) => (
  <a href={blobPath(blob)} download={blob.name || blob.digest}>
    Download original
  </a>
);

/** The lightbox title of a record: its run and axis value. */
export const recordName = (card: Card, row: Observation) =>
  `${label(row.run)} · ${card.axis} ${display(valueAt(row, card.axis))}`;
