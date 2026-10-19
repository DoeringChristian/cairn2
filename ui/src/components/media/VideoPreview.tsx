import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MutableRefObject,
} from "react";
import { Pause, Play, Volume2, VolumeX } from "lucide-react";
import { MediaOpenButton } from "./MediaOpenButton";
import { useLiveTime } from "./VideoTimeline";
import "./video-preview.css";

type VideoState = {
  time: number;
  duration: number;
  playing: boolean;
  muted: boolean;
  volume: number;
  error: string;
};
const initial: VideoState = {
  time: 0,
  duration: 0,
  playing: false,
  muted: false,
  volume: 1,
  error: "",
};
/** Videos paused by the viewer, or handed to the lightbox, stay paused when scrolled into view. */
export const heldVideos = new WeakSet<HTMLVideoElement>();
const formatTime = (value: number) =>
  `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`;

/** Shared transport also attaches to sanitized report-output video elements. */
export function InlineVideoControls({
  element,
  onEnlarge,
  label = "Video",
}: {
  element: HTMLVideoElement | null;
  onEnlarge: () => void;
  label?: string;
}) {
  const [state, setState] = useState<VideoState>(initial);
  const time = useLiveTime(element, state.time);
  const enlarge = useRef(onEnlarge);
  enlarge.current = onEnlarge;
  useEffect(() => {
    if (!element) {
      setState(initial);
      return;
    }
    element.controls = false;
    element.playsInline = true;
    element.loop = true;
    element.muted = true;
    const originalRole = element.getAttribute("role"),
      originalTabIndex = element.getAttribute("tabindex");
    element.setAttribute("role", "button");
    element.tabIndex = 0;
    element.classList.add("prism-video-clickable");
    const open = () => enlarge.current();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    };
    element.addEventListener("click", open);
    element.addEventListener("keydown", key);
    const read = () =>
      setState({
        time: Number.isFinite(element.currentTime) ? element.currentTime : 0,
        duration: Number.isFinite(element.duration) ? element.duration : 0,
        playing: !element.paused && !element.ended,
        muted: element.muted,
        volume: element.volume,
        error: element.error
          ? "This video codec or file could not be decoded by the browser."
          : "",
      });
    const events = [
      "loadedmetadata",
      "durationchange",
      "timeupdate",
      "play",
      "pause",
      "ended",
      "volumechange",
      "error",
      "emptied",
    ];
    events.forEach((name) => element.addEventListener(name, read));
    read();
    const visibility = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) element.pause();
      else if (!heldVideos.has(element)) void element.play().catch(() => undefined);
    });
    visibility.observe(element);
    return () => {
      visibility.disconnect();
      events.forEach((name) => element.removeEventListener(name, read));
      element.removeEventListener("click", open);
      element.removeEventListener("keydown", key);
      element.classList.remove("prism-video-clickable");
      if (originalRole === null) element.removeAttribute("role");
      else element.setAttribute("role", originalRole);
      if (originalTabIndex === null) element.removeAttribute("tabindex");
      else element.setAttribute("tabindex", originalTabIndex);
    };
  }, [element]);
  async function toggle() {
    if (!element) return;
    if (!element.paused) {
      heldVideos.add(element);
      element.pause();
    } else
      try {
        heldVideos.delete(element);
        await element.play();
      } catch {
        setState((previous) => ({
          ...previous,
          error:
            "Playback could not start. Try again or download the original video.",
        }));
      }
  }
  return (
    <span
      className="prism-inline-transport"
      data-selection-ignore
      role="group"
      aria-label={label + " controls"}
    >
      <span className="prism-inline-main">
        <button
          type="button"
          className="prism-video-icon"
          aria-label={state.playing ? "Pause video" : "Play video"}
          title={state.playing ? "Pause video" : "Play video"}
          disabled={!element}
          onClick={() => void toggle()}
        >
          {state.playing ? (
            <Pause size={16} aria-hidden="true" />
          ) : (
            <Play size={16} aria-hidden="true" />
          )}
        </button>
        <input
          type="range"
          aria-label="Video position"
          min={0}
          max={state.duration || 1}
          value={Math.min(time, state.duration || time)}
          step={0.01}
          disabled={!state.duration}
          onChange={(event) => {
            if (element) {
              element.currentTime = Number(event.target.value);
              setState((previous) => ({
                ...previous,
                time: element.currentTime,
              }));
            }
          }}
        />
        <output className="prism-inline-time" aria-label="Playback time">
          {formatTime(time)} /{" "}
          {state.duration ? formatTime(state.duration) : "—"}
        </output>
      </span>
      <span className="prism-inline-actions">
        <button
          type="button"
          className="prism-video-icon"
          aria-label={state.muted ? "Unmute video" : "Mute video"}
          title={state.muted ? "Unmute" : "Mute"}
          disabled={!element}
          onClick={() => {
            if (element) element.muted = !element.muted;
          }}
        >
          {state.muted ? (
            <VolumeX size={15} aria-hidden="true" />
          ) : (
            <Volume2 size={15} aria-hidden="true" />
          )}
        </button>
        <input
          className="prism-inline-volume"
          type="range"
          aria-label="Video volume"
          min={0}
          max={1}
          step={0.01}
          value={state.muted ? 0 : state.volume}
          disabled={!element}
          onChange={(event) => {
            if (element) {
              element.volume = Number(event.target.value);
              element.muted = false;
            }
          }}
        />
        <MediaOpenButton onClick={onEnlarge} />
      </span>
      {state.error && (
        <span role="alert" className="prism-inline-video-error">
          {state.error}{" "}
          {element?.currentSrc && (
            <a href={element.currentSrc} download>
              Download video
            </a>
          )}
        </span>
      )}
    </span>
  );
}

export function VideoPreview({
  src,
  mime,
  label = "Video",
  videoRef,
  videoClassName,
  reportPath,
  onEnlarge,
}: {
  src: string;
  mime: string;
  label?: string;
  videoRef?: MutableRefObject<HTMLVideoElement | null>;
  videoClassName?: string;
  reportPath?: string;
  onEnlarge: () => void;
}) {
  const [element, setElement] = useState<HTMLVideoElement | null>(null);
  const attach = useCallback(
    (node: HTMLVideoElement | null) => {
      setElement(node);
      if (videoRef) videoRef.current = node;
    },
    [videoRef],
  );
  return (
    <span className="prism-video-preview">
      <video
        ref={attach}
        src={src}
        className={["prism-inline-video", videoClassName]
          .filter(Boolean)
          .join(" ")}
        aria-label={label}
        data-report-media={mime}
        data-report-path={reportPath}
        playsInline
        preload="metadata"
      >
        <source src={src} type={mime} />
      </video>
      <InlineVideoControls
        element={element}
        label={label}
        onEnlarge={onEnlarge}
      />
    </span>
  );
}
