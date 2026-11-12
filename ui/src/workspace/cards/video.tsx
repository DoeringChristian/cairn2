import { Film } from 'lucide-react';
import { VideoPreview } from '../../components/media/VideoPreview';
import { useImageBlob } from '../../components/useBlobUrl';
import { BlobLightbox, DownloadBlob, Held, mediaKind, recordName, type TileProps } from './media';

function VideoTile({ value, title, onExpand }: TileProps) {
  const blob = value.blobs[0];
  const { url, mime, error } = useImageBlob(blob);
  if (error)
    return (
      <div className="workspace-empty">
        {error.message} <DownloadBlob blob={blob} />
      </div>
    );
  return (
    <Held id={url} placeholder={<div className="workspace-video-loading" aria-label="Loading video" />}>
      {(src, ready) => <VideoPreview src={src} mime={mime} label={title} onEnlarge={onExpand} onLoadedData={ready} />}
    </Held>
  );
}

/** Records hold videos as `cairn.File` values with a `video/*` media type. */
export const video = mediaKind({
  label: 'Video',
  icon: Film,
  title: 'Videos',
  field: 'video',
  noun: 'videos',
  accepts: (type) => type.startsWith('file:video/'),
  Tile: (props) =>
    props.value.blobs[0] ? (
      <VideoTile {...props} />
    ) : (
      <p>This video is stored in chunks; open it from the run’s files.</p>
    ),
  Expanded: ({ card, row, value, onClose }) => (
    <BlobLightbox blob={value.blobs[0]} name={recordName(card, row)} onClose={onClose} />
  ),
});
