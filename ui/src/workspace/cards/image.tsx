import { useState } from 'react';
import { Image } from 'lucide-react';
import { useImageBlob } from '../../components/useBlobUrl';
import { useZoomPane } from '../zoom';
import { BlobLightbox, DownloadBlob, Enlarge, Held, mediaKind, recordName, type TileProps } from './media';
import { Choice } from './settings';

function ImageTile({ card, value, title, onExpand, zoom }: TileProps) {
  const blob = value.blobs[0];
  const pane = useZoomPane(zoom);
  const { url, error } = useImageBlob(blob);
  const [failed, setFailed] = useState<string>();
  const broken = error || (url && failed === url);
  return (
    <div className="workspace-zoom">
      <div ref={pane} className="workspace-pane">
        {broken ? (
          <div className="workspace-empty">
            {error?.message || 'This image could not be decoded.'} <DownloadBlob blob={blob} />
          </div>
        ) : (
          <Held id={url} placeholder={<div aria-label="Loading image" />}>
            {(src, ready) => (
              <img
                src={src}
                alt={title}
                draggable={false}
                style={{ objectFit: card.fit }}
                onLoad={(e) => e.currentTarget.decode().then(ready, ready)}
                onError={() => setFailed(src)}
              />
            )}
          </Held>
        )}
      </div>
      <Enlarge label="Enlarge image" onClick={onExpand} />
    </div>
  );
}

export const image = mediaKind({
  label: 'Image',
  icon: Image,
  title: 'Image gallery',
  field: 'image',
  noun: 'images',
  accepts: (type) => type === 'image' || type === 'figure/svg' || type.startsWith('file:image/'),
  Tile: (props) => (props.value.blobs[0] ? <ImageTile {...props} /> : <p>Image has no blob.</p>),
  Expanded: ({ card, row, value, onClose }) => (
    <BlobLightbox blob={value.blobs[0]} name={recordName(card, row)} onClose={onClose} />
  ),
  Appearance: ({ card, patch }) => (
    <Choice
      label="Fit"
      value={card.fit}
      options={[
        ['contain', 'Whole image'],
        ['cover', 'Fill the tile (crops)'],
      ]}
      onChange={(fit) => patch({ fit })}
    />
  ),
});
