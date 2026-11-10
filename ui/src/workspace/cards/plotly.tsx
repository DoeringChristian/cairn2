import { PanelsTopLeft } from 'lucide-react';
import { WorkspaceDialog } from '../Dialog';
import { label } from '../model';
import { PlotSurface, type FigureSpec } from '../PlotSurface';
import { mediaKind } from './media';
import { Toggle } from './settings';

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
        <PlotSurface spec={value.data as unknown as FigureSpec} range={linked} onRange={onRange} />
      </div>
      <button className="text-button workspace-expand-plot" onClick={onExpand}>
        Expand figure ↗
      </button>
    </>
  ),
  Expanded: ({ row, value, onClose }) => (
    <WorkspaceDialog title={label(row.run)} onClose={onClose}>
      <div className="workspace-expanded-figure">
        <PlotSurface spec={value.data as unknown as FigureSpec} />
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
