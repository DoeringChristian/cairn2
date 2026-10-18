import { Expand } from "lucide-react";
export function MediaOpenButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      className="prism-media-open"
      aria-label="Enlarge video"
      title="Enlarge video: wheel to zoom, drag to pan, inspect frames and copy references"
      data-selection-ignore
      onClick={onClick}
    >
      <Expand size={15} aria-hidden="true" />
    </button>
  );
}
