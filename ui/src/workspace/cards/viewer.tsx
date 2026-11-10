import { AppWindow } from 'lucide-react';
import { useParams } from 'react-router-dom';
import { ViewEditor } from '../../components/ViewEditor';
import { readView, ViewerFrame } from '../../components/ViewerFrame';
import { Note } from './settings';
import type { CardKind, ContentProps, SettingsProps } from './types';

function ViewerContent({ card }: ContentProps) {
  const { project = '' } = useParams();
  let view;
  try {
    view = readView(card.value);
  } catch (e) {
    return <div className="workspace-empty">{(e as Error).message}</div>;
  }
  return (
    <ViewerFrame viewer={view.viewer} settings={view.settings} project={project} title={view.caption || card.title} />
  );
}

function ViewerSettings({ card, patch }: SettingsProps) {
  const { project = '' } = useParams();
  return (
    <div className="workspace-settings-panel">
      <ViewEditor value={card.value} project={project} onChange={(value) => patch({ value })} />
      <Note>The YAML tab holds the same text as a report’s view block.</Note>
    </div>
  );
}

/** An artifact's HTML viewer, configured by the same YAML as a report's view block. */
export const viewer: CardKind = {
  label: 'Viewer',
  icon: AppWindow,
  defaults: { title: 'Viewer' },
  usesRecords: false,
  isValid: (card) => {
    try {
      readView(card.value);
      return true;
    } catch {
      return false;
    }
  },
  Content: ViewerContent,
  Settings: ViewerSettings,
};
