import type { ComponentProps } from 'react';
import { PanelsTopLeft } from 'lucide-react';
import { WorkspaceDialog } from '../Dialog';
import { label } from '../model';
import { PlotSurface, type FigureSpec } from '../PlotSurface';
import { blobPath, useLoaded, type BlobRef } from '../../api';
import { mediaKind } from './media';
import { Toggle } from './settings';

function Figure({ blob, ...props }: { blob: BlobRef } & Omit<ComponentProps<typeof PlotSurface>, 'spec'>) {
  const { data, error } = useLoaded(async (signal) => {
    const response = await fetch(blobPath(blob), { signal });
    if (!response.ok) throw new Error(`Could not load the figure (${response.status}).`);
    return (await response.json()) as FigureSpec;
  }, `figure:${blob.digest}`);
  if (data) return <PlotSurface spec={data} {...props} />;
  return <div className="workspace-empty">{error?.message || 'Loading figure…'}</div>;
}

export const plotly = mediaKind({
  label: 'Plotly',
  icon: PanelsTopLeft,
  title: 'Interactive figures',
  field: 'figure',
  noun: 'Plotly figures',
  accepts: (type) => type === 'figure/plotly',
  Tile: ({ value, onExpand, linked, onRange }) => (
    <>
      <div className="workspace-figure-tile">
        <Figure blob={value.blobs[0]} range={linked} onRange={onRange} />
      </div>
      <button className="text-button workspace-expand-plot" onClick={onExpand}>
        Expand figure ↗
      </button>
    </>
  ),
  Expanded: ({ row, value, onClose }) => (
    <WorkspaceDialog title={label(row.run)} onClose={onClose}>
      <div className="workspace-expanded-figure">
        <Figure blob={value.blobs[0]} />
      </div>
    </WorkspaceDialog>
  ),
  Appearance: ({ card, patch }) => (
    <Toggle
      label="Link 2D axes across figures with the same units"
      checked={card.linkAxes}
      onChange={(linkAxes) => patch({ linkAxes })}
    />
  ),
});
