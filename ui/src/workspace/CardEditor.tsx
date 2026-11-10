import { useState } from 'react';
import { Button } from '../components/Button';
import { CardContent } from './CardContent';
import { cardKinds, newCard, type Kind } from './cards';
import { WorkspaceDialog } from './Dialog';
import type { Card, FieldInfo, Observation } from './model';

const isValid = (card: Card) =>
  !!card.title.trim() && card.filters.every((filter) => !!filter.field) && cardKinds[card.kind].isValid(card);

export function CardEditor({
  initial,
  creating,
  fields,
  rows,
  onApply,
  onClose,
}: {
  initial: Card;
  creating: boolean;
  fields: FieldInfo[];
  rows: Observation[];
  onApply: (card: Card) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const { Settings } = cardKinds[draft.kind];
  const valid = isValid(draft);
  const patch = (value: Partial<Card>) => {
    const next = { ...draft, ...value };
    setDraft(next);
    if (!creating && isValid(next)) onApply(next);
  };
  const choose = (kind: Kind) =>
    setDraft({ ...newCard(kind, draft.section), id: draft.id, width: draft.width, height: draft.height });
  const note = creating
    ? 'Preview uses the visible runs. Add the card when ready.'
    : valid
      ? 'Changes apply and save automatically. Undo in the workspace restores the previous settings.'
      : 'Complete the required fields. The card keeps its last valid settings.';

  return (
    <WorkspaceDialog title={creating ? 'Create a card' : 'Card settings'} onClose={onClose} wide>
      <div className="workspace-editor">
        <div className="workspace-editor-controls">
          {creating && (
            <div className="workspace-type-picker" aria-label="Card type">
              {Object.entries(cardKinds).map(([kind, { label, icon: Icon }]) => (
                <button key={kind} aria-pressed={draft.kind === kind} onClick={() => choose(kind as Kind)}>
                  <Icon size={19} />
                  {label}
                </button>
              ))}
            </div>
          )}
          <label className="workspace-field">
            <span>Card title</span>
            <input value={draft.title} onChange={(e) => patch({ title: e.target.value })} />
          </label>
          <Settings key={draft.kind} card={draft} fields={fields} patch={patch} />
        </div>
        <div className="workspace-editor-preview">
          <span className="eyebrow">Live preview</span>
          <article className="workspace-card" style={{ height: Math.min(620, draft.height) }}>
            <header>
              <h3>{draft.title || 'Untitled card'}</h3>
            </header>
            <CardContent card={draft} rows={rows} />
          </article>
          <p className="workspace-setting-note">{note}</p>
        </div>
      </div>
      <footer className="workspace-dialog-footer">
        {creating ? (
          <>
            <Button onClick={onClose}>Cancel</Button>
            <Button primary disabled={!valid} onClick={() => onApply(draft)}>
              Add card
            </Button>
          </>
        ) : (
          <Button onClick={onClose}>Done</Button>
        )}
      </footer>
    </WorkspaceDialog>
  );
}
