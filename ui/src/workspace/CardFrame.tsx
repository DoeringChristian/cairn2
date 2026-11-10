import { type CSSProperties, type KeyboardEvent, type PointerEvent } from 'react';
import { ArrowDown, ArrowUp, Copy, Grip, Maximize2, Settings2, Trash2 } from 'lucide-react';
import { ActionIcon, Button } from '../components/Button';
import { CardContent } from './CardContent';
import { cardKinds } from './cards';
import { cardWidths, snapWidth } from './layout';
import type { Card, Observation } from './model';

export type CardActions = {
  settings: () => void;
  expand: () => void;
  move: (delta: number) => void;
  duplicate: () => void;
  remove: () => void;
  /** Resize every card of the card's section. */
  size: (width: number, height: number) => void;
  drag: (id: string | undefined) => void;
  drop: () => void;
};

export type RecordState = { rows: Observation[]; loading: boolean; failed: boolean };

/** Width steps through `cardWidths`; height changes in pixels. */
const keySteps: Record<string, [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -20],
  ArrowDown: [0, 20],
};

const clampHeight = (height: number) => Math.max(280, Math.min(1000, height));

/** A card in the grid: its heading, menu, content and resize handle. */
export function CardFrame({
  card,
  records,
  dragging,
  actions,
}: {
  card: Card;
  records: RecordState;
  dragging: boolean;
  actions: CardActions;
}) {
  const waiting = cardKinds[card.kind].usesRecords && (records.loading || records.failed);

  function resizeByKeyboard(e: KeyboardEvent) {
    const step = keySteps[e.key];
    if (!step) return;
    e.preventDefault();
    const index = cardWidths.indexOf(card.width as (typeof cardWidths)[number]) + step[0];
    actions.size(cardWidths[Math.max(0, Math.min(cardWidths.length - 1, index))], clampHeight(card.height + step[1]));
  }

  return (
    <article
      className="workspace-card"
      style={{ '--card-span': card.width, height: card.height } as CSSProperties}
      aria-label={card.title}
      onDragOver={(e) => {
        if (dragging) e.preventDefault();
      }}
      onDrop={(e) => {
        e.preventDefault();
        e.stopPropagation();
        actions.drop();
      }}
    >
      <header className="workspace-card-heading">
        <span
          className="workspace-drag-handle"
          draggable
          title="Drag to reorder"
          onDragStart={(e) => {
            actions.drag(card.id);
            e.dataTransfer.setData('text/plain', card.id);
            e.dataTransfer.effectAllowed = 'move';
          }}
          onDragEnd={() => actions.drag(undefined)}
        >
          <Grip size={16} />
        </span>
        <h3 title={card.title}>{card.title}</h3>
        <ActionIcon label={`Settings for ${card.title}`} onClick={actions.settings}>
          <Settings2 size={16} />
        </ActionIcon>
        <ActionIcon label={`Expand ${card.title}`} onClick={actions.expand}>
          <Maximize2 size={15} />
        </ActionIcon>
        <details className="workspace-card-menu">
          <summary aria-label={`Actions for ${card.title}`}>•••</summary>
          <div>
            <Button small onClick={() => actions.move(-1)}>
              <ArrowUp size={14} /> Move earlier
            </Button>
            <Button small onClick={() => actions.move(1)}>
              <ArrowDown size={14} /> Move later
            </Button>
            <Button small onClick={actions.duplicate}>
              <Copy size={14} /> Duplicate
            </Button>
            <Button small onClick={actions.remove}>
              <Trash2 size={14} /> Remove
            </Button>
          </div>
        </details>
      </header>

      {!waiting ? (
        <CardContent card={card} rows={records.rows} />
      ) : records.loading ? (
        <div className="workspace-empty">Loading records…</div>
      ) : (
        <div className="workspace-empty">Data could not be loaded. Try a smaller run selection or refresh.</div>
      )}

      <button
        className="workspace-resize"
        aria-label={`Resize ${card.title}`}
        title="Resize all cards in this section; arrow keys adjust width and height"
        onPointerDown={(e) => resizeByPointer(e, card, actions.size)}
        onKeyDown={resizeByKeyboard}
      />
    </article>
  );
}

/** Previews the new size on every card of the section while dragging, and applies it on release. */
function resizeByPointer(
  event: PointerEvent<HTMLButtonElement>,
  card: Card,
  apply: (width: number, height: number) => void,
) {
  event.preventDefault();
  const handle = event.currentTarget;
  const grid = handle.closest<HTMLElement>('.workspace-card')!.parentElement!;
  const peers = Array.from(grid.querySelectorAll<HTMLElement>(':scope > .workspace-card'));
  const startX = event.clientX;
  const startY = event.clientY;
  const gridWidth = grid.getBoundingClientRect().width;
  let width = card.width;
  let height = card.height;

  const preview = (width: number, height: number) =>
    peers.forEach((peer) => {
      peer.style.setProperty('--card-span', String(width));
      peer.style.height = `${height}px`;
    });
  const update = (e: globalThis.PointerEvent) => {
    width = snapWidth(card.width + ((e.clientX - startX) / (gridWidth + 16)) * 12);
    height = clampHeight(Math.round((card.height + e.clientY - startY) / 20) * 20);
    preview(width, height);
  };
  const stop = () => {
    handle.removeEventListener('pointermove', update);
    handle.removeEventListener('pointerup', finish);
    handle.removeEventListener('pointercancel', cancel);
  };
  const finish = (e: globalThis.PointerEvent) => {
    stop();
    if (handle.hasPointerCapture(e.pointerId)) handle.releasePointerCapture(e.pointerId);
    apply(width, height);
  };
  const cancel = () => {
    stop();
    preview(card.width, card.height);
  };

  handle.setPointerCapture(event.pointerId);
  handle.addEventListener('pointermove', update);
  handle.addEventListener('pointerup', finish);
  handle.addEventListener('pointercancel', cancel);
}
