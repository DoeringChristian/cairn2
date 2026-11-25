import { Link } from 'react-router-dom';
import { artifactPath, print, type Json } from '../../api';
import { Button } from '../../components/Button';
import { WorkspaceDialog } from '../Dialog';
import { label, type Observation } from '../model';

export const recordId = (row: Observation) => `${row.run.id}:${row.ordinal}`;

export const display = (value: Json | undefined) =>
  typeof value === 'number'
    ? Number(value.toPrecision(5)).toString()
    : value && typeof value === 'object'
      ? print(value)
      : String(value ?? '—');

/** A record's raw data, with a link to its run. */
export function Inspector({ row, onClose }: { row: Observation; onClose: () => void }) {
  const reference = JSON.stringify({ artifact: row.run.id, ordinal: row.ordinal });
  return (
    <WorkspaceDialog title={`Record ${row.ordinal}`} onClose={onClose}>
      <p>
        <Link to={artifactPath(row.run.project, row.run.id)}>{label(row.run)}</Link>
      </p>
      <pre className="workspace-record-json">{print(row.data, 2)}</pre>
      <Button onClick={() => void navigator.clipboard.writeText(reference)}>Copy record reference</Button>
    </WorkspaceDialog>
  );
}
