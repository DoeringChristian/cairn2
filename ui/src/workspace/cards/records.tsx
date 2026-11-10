import { Link } from 'react-router-dom';
import { artifactPath, type Json } from '../../api';
import { Button } from '../../components/Button';
import { WorkspaceDialog } from '../Dialog';
import { label, type Observation } from '../model';

export const recordId = (row: Observation) => `${row.artifact_id}:${row.ordinal}`;

export const display = (value: Json | undefined) =>
  typeof value === 'number' ? Number(value.toPrecision(5)).toString() : String(value ?? '—');

/** A record's raw data, with a link to its run. */
export function Inspector({ row, onClose }: { row: Observation; onClose: () => void }) {
  const reference = JSON.stringify({ artifact_id: row.artifact_id, ordinal: row.ordinal });
  return (
    <WorkspaceDialog title={`Record ${row.ordinal}`} onClose={onClose}>
      <p>
        <Link to={artifactPath(row.run.project, row.artifact_id)}>{label(row.run)}</Link>
      </p>
      <pre className="workspace-record-json">{JSON.stringify(row.data, null, 2)}</pre>
      <Button onClick={() => void navigator.clipboard.writeText(reference)}>Copy record reference</Button>
    </WorkspaceDialog>
  );
}
