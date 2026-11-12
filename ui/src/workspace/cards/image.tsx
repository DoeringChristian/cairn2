import { useState } from 'react';
import { Image } from 'lucide-react';
import { useImageBlob } from '../../components/useBlobUrl';
import { BlobLightbox, DownloadBlob, Held, mediaKind, recordName, type TileProps } from './media';
import { Choice } from './settings';

function ImageTile({ card, value, title, onExpand }: TileProps) {
  const blob = value.blobs[0];
  const { url, error } = useImageBlob(blob);
  const [failed, setFailed] = useState<string>();
  if (error || (url && failed === url))
    return (
      <div className="workspace-empty">
        {error?.message || 'This image could not be decoded.'} <DownloadBlob blob={blob} />
      </div>
    );
  return (
    <Held id={url} placeholder={<div className="workspace-image-open" aria-label="Loading image" />}>
      {(src, ready) => (
        <button className="workspace-image-open" onClick={onExpand} aria-label="Enlarge image">
          <img
            src={src}
            alt={title}
            style={{ objectFit: card.fit }}
            onLoad={(e) => e.currentTarget.decode().then(ready, ready)}
            onError={() => setFailed(src)}
          />
        </button>
      )}
    </Held>
  );
}

export const image = mediaKind({
  label: 'Image',
  icon: Image,
  title: 'Image gallery',
  field: 'image',
  noun: 'images',
  accepts: (type) => type === 'image' || type === 'figure/svg',
  Tile: (props) => (props.value.blobs[0] ? <ImageTile {...props} /> : <p>Image has no blob.</p>),
  Expanded: ({ card, row, value, onClose }) => (
    <BlobLightbox blob={value.blobs[0]} name={recordName(card, row)} onClose={onClose} />
  ),
  Appearance: ({ card, patch }) => (
    <Choice
      label="Image fit"
      value={card.fit}
      options={[
        ['contain', 'Fit whole image'],
        ['cover', 'Fill tile (crop)'],
      ]}
      onChange={(fit) => patch({ fit })}
    />
  ),
});
