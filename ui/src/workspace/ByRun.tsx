import { Link } from 'react-router-dom';
import { artifactPath, type ArtifactSummary } from '../api';
import { CardFrame, type CardActions, type RecordState } from './CardFrame';
import { cardKinds } from './cards';
import { label, type Card, type Layout } from './model';
import { runColor } from './presentation';

const still = () => {};

/** Every record card once per visible run, each showing only that run's records. */
export function ByRun({
  project,
  layout,
  runs,
  records,
  actions,
}: {
  project: string;
  layout: Layout;
  runs: ArtifactSummary[];
  records: RecordState;
  actions: (card: Card, run: ArtifactSummary) => Pick<CardActions, 'settings' | 'expand' | 'duplicate' | 'remove'>;
}) {
  // Cards in reading order: section by section. Viewer cards show the same file for every run.
  const cards = layout.sections.flatMap((section) =>
    layout.cards.filter((card) => card.section === section.id && cardKinds[card.kind].usesRecords),
  );
  return (
    <>
      {runs.map((run) => (
        <section className="workspace-section workspace-run-section" key={run.id} aria-label={`Run ${label(run)}`}>
          <header className="workspace-section-heading">
            <div className="workspace-section-toggle">
              <i className="workspace-run-swatch" style={{ background: runColor(run.id) }} aria-hidden="true" />
              <h2>
                <Link to={artifactPath(project, run.id)}>{label(run)}</Link>
              </h2>
              <span>{run.outcome || run.state}</span>
            </div>
          </header>
          <div className="workspace-grid">
            {cards.map((card) => (
              <CardFrame
                key={card.id}
                card={card}
                records={{ ...records, rows: records.rows.filter((row) => row.artifact_id === run.id) }}
                dragging={false}
                actions={{ ...actions(card, run), move: still, size: still, drag: still, drop: still }}
              />
            ))}
          </div>
        </section>
      ))}
    </>
  );
}
