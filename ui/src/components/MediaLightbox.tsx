import { ReportArtifactContext, mediaCommentKey } from "./ReportContext";
import { CommentComposer, useReportComments, type Comment, type CommentAnchor } from "./ReportComments";
import { VideoTimeline } from "./media/VideoTimeline";
import { MessageSquare, MessageSquarePlus, Scissors, SquareDashed, RotateCcw, Repeat, Copy, Check, CircleAlert, Play, Pause, StepBack, StepForward, Volume2, VolumeX, ZoomIn, ZoomOut, Maximize2, Minimize2, Shrink, X, Download, ChevronLeft, ChevronRight } from "lucide-react";
import { MediaIconButton } from "./media/MediaIconButton";
import { createPortal } from "react-dom";
import { Button, LinkButton } from "./Button";
import { useContext, useEffect, useRef, useState, type PointerEvent } from "react";
import {
  FIT_VIEW,
  fitRect,
  transformToView,
  viewToTransform,
  type PaneTransform,
  type Size,
  type ZoomView,
} from "./media/geometry";
import "./media/lightbox.css";
import { Select } from "./Select";

export type MediaItem = {
  url: string;
  mime: string;
  name?: string;
  fps?: number;
  time?: number;
  source?: {
    artifact_ref?: string;
    hash?: string;
    path?: string;
    file?: string;
    line?: number;
  };
};
export type MediaSelection = {
  item: MediaItem;
  region?: {
    x: number;
    y: number;
    width: number;
    height: number;
    imageWidth: number;
    imageHeight: number;
  };
  time?: number;
  clip?: { start: number; end: number };
};
const clamp = (value: number, low = 0, high = 1) =>
  Math.min(high, Math.max(low, value));
export function pixelRegion(
  a: { x: number; y: number },
  b: { x: number; y: number },
  size: Size,
) {
  const x = Math.round(clamp(Math.min(a.x, b.x)) * size.w),
    y = Math.round(clamp(Math.min(a.y, b.y)) * size.h);
  return {
    x,
    y,
    width: Math.round(clamp(Math.max(a.x, b.x)) * size.w) - x,
    height: Math.round(clamp(Math.max(a.y, b.y)) * size.h) - y,
    imageWidth: size.w,
    imageHeight: size.h,
  };
}
type Point = { x: number; y: number };

export function MediaLightbox({
  items,
  initialIndex = 0,
  onClose,
  onSelection,
}: {
  items: MediaItem[];
  initialIndex?: number;
  onClose: () => void;
  onSelection?: (selection: MediaSelection) => void;
}) {
  const artifact=useContext(ReportArtifactContext);
  const [comment,setComment]=useState<{x:number;y:number;anchor:CommentAnchor;initial?:Comment}>();
  const dialog = useRef<HTMLDialogElement>(null),
    surface = useRef<HTMLDivElement>(null),
    stage = useRef<HTMLDivElement>(null),
    video = useRef<HTMLVideoElement>(null);
  const [index, setIndex] = useState(
    clamp(initialIndex, 0, Math.max(0, items.length - 1)),
  );
  const item = items[index],
    isVideo = item?.mime.startsWith("video/");
  const {items:comments,saved}=useReportComments(),existingComment=comments.find(comment=>comment.anchor.media_key===mediaCommentKey(item?.source,item?.name));
  const [pane, setPane] = useState<Size>({ w: 1, h: 1 }),
    [natural, setNatural] = useState<Size | null>(null);
  const [view, setView] = useState<ZoomView>(FIT_VIEW),
    [selecting, setSelecting] = useState(false);
  const liveView = useRef<ZoomView>(FIT_VIEW),
    animation = useRef<number | null>(null);
  const [region, setRegion] = useState<{ a: Point; b: Point } | null>(null);
  const [time, setTime] = useState(0),
    [duration, setDuration] = useState(0),
    [playing, setPlaying] = useState(false);
  const [clipStart, setClipStart] = useState<number | null>(null),
    [clipEnd, setClipEnd] = useState<number | null>(null);
  const [selectingTime, setSelectingTime] = useState(false);
  const [loop, setLoop] = useState(false),
    [copied, setCopied] = useState(""),
    [failure, setFailure] = useState("");
  const [fps, setFps] = useState(clamp(item?.fps || 30, 1, 240)),
    [rate, setRate] = useState(1);
  const [volume, setVolume] = useState(1),
    [muted, setMuted] = useState(false),
    [fullscreen, setFullscreen] = useState(false),
    [notice, setNotice] = useState("");
  const pointers = useRef(new Map<number, Point>()),
    drag = useRef<{ start: Point; view: ZoomView } | null>(null);
  const pinch = useRef<{
    distance: number;
    center: Point;
    transform: PaneTransform;
  } | null>(null);
  const fit = fitRect(pane, natural),
    transform = viewToTransform(view, pane, natural);
  const crisp =
    natural && (fit.w / natural.w) * view.zoom > 1.5 ? "pixelated" : "auto";
  // Gesture updates are local to this dialog and coalesced to one React commit per
  // animation frame. The stable video element never remounts for zoom/pan/playback.
  function changeView(next: ZoomView | ((previous: ZoomView) => ZoomView)) {
    const requested =
      typeof next === "function" ? next(liveView.current) : next;
    // Store the visible, bounded center so a drag at fit cannot create a hidden
    // offscreen center that later jumps when zooming or resizing.
    liveView.current = transformToView(
      viewToTransform(requested, pane, natural),
      pane,
      natural,
    );
    if (animation.current === null)
      animation.current = requestAnimationFrame(() => {
        animation.current = null;
        setView(liveView.current);
      });
  }
  useEffect(() => {
    const prior = document.activeElement as HTMLElement | null,
      node = dialog.current;
    if (node && !node.open) {
      if (node.showModal) node.showModal();
      else node.setAttribute("open", "");
    }
    const priorOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const fullscreenChanged = () =>
      setFullscreen(document.fullscreenElement === surface.current);
    document.addEventListener("fullscreenchange", fullscreenChanged);
    return () => {
      if (animation.current !== null) cancelAnimationFrame(animation.current);
      animation.current = null;
      document.removeEventListener("fullscreenchange", fullscreenChanged);
      document.body.style.overflow = priorOverflow;
      node?.close?.();
      prior?.focus({ preventScroll: true });
    };
  }, []);
  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const measure = () => {
      const box = element.getBoundingClientRect();
      setPane({ w: Math.max(1, box.width), h: Math.max(1, box.height) });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    setNatural(null);
    setPlaying(false);
    changeView(FIT_VIEW);
    setSelecting(false);
    setRegion(null);
    setTime(item?.time || 0);
    setDuration(0);
    setClipStart(null);
    setClipEnd(null);
    setSelectingTime(false);
    setFailure("");
    setCopied("");
    setNotice("");
    setFps(clamp(item?.fps || 30, 1, 240));
    pointers.current.clear();
    drag.current = null;
    pinch.current = null;
  }, [item?.url, index]);
  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      if ((event.target as HTMLElement).closest("button,input,select,a"))
        return;
      event.preventDefault();
      const bounds = element.getBoundingClientRect(),
        x = event.clientX - bounds.left,
        y = event.clientY - bounds.top;
      const delta =
        event.deltaY *
        (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? pane.h : 1);
      changeView((previous) => {
        const old = viewToTransform(previous, pane, natural),
          scale = clamp(old.scale * Math.exp(-delta * 0.002), 1, 32);
        return transformToView(
          {
            scale,
            x: x - ((x - old.x) * scale) / old.scale,
            y: y - ((y - old.y) * scale) / old.scale,
          },
          pane,
          natural,
        );
      });
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, [pane, natural]);
  useEffect(() => {
    const node = video.current;
    if (
      !node ||
      !playing ||
      !loop ||
      clipStart === null ||
      clipEnd === null ||
      clipEnd <= clipStart
    )
      return;
    let closed = false,
      frame = 0;
    const videoFrames = typeof node.requestVideoFrameCallback === "function";
    const tick = () => {
      if (closed) return;
      if (node.currentTime >= clipEnd) node.currentTime = clipStart;
      frame = videoFrames
        ? node.requestVideoFrameCallback(tick)
        : requestAnimationFrame(tick);
    };
    frame = videoFrames
      ? node.requestVideoFrameCallback(tick)
      : requestAnimationFrame(tick);
    return () => {
      closed = true;
      if (videoFrames) node.cancelVideoFrameCallback(frame);
      else cancelAnimationFrame(frame);
    };
  }, [playing, loop, clipStart, clipEnd, item?.url, index]);
  if (!item) return null;
  const selection: MediaSelection = {
    item,
    ...(region && natural
      ? { region: pixelRegion(region.a, region.b, natural) }
      : {}),
    ...(isVideo
      ? {
          time,
          ...(clipStart !== null && clipEnd !== null && clipEnd >= clipStart
            ? { clip: { start: clipStart, end: clipEnd } }
            : {}),
        }
      : {}),
  };
  const seek = (value: number) => {
    if (video.current && duration) {
      video.current.currentTime = clamp(value, 0, duration);
      setTime(video.current.currentTime);
    }
  };
  const step = (direction: number) => {
    video.current?.pause();
    seek((video.current?.currentTime ?? time) + direction / fps);
  };
  const play = () => {
    const node = video.current;
    if (node) {
      if (node.paused)
        void node
          .play()
          .catch(() =>
            setNotice(
              "Playback could not start. Try Play again or download the original.",
            ),
          );
      else node.pause();
    }
  };
  function point(event: PointerEvent<HTMLDivElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    return {
      x: clamp((event.clientX - box.left) / box.width),
      y: clamp((event.clientY - box.top) / box.height),
    };
  }
  function pointerUp(event: PointerEvent<HTMLDivElement>) {
    pointers.current.delete(event.pointerId);
    pinch.current = null;
    const remaining = [...pointers.current.values()][0];
    drag.current = remaining
      ? { start: remaining, view: liveView.current }
      : null;
  }
  function currentSelection(): MediaSelection {
    return isVideo
      ? { ...selection, time: video.current?.currentTime ?? time }
      : selection;
  }
  useEffect(() => {
    if (!copied) return;
    const timeout = window.setTimeout(() => setCopied(""), 1800);
    return () => window.clearTimeout(timeout);
  }, [copied]);
  async function copy() {
    setCopied("");
    const { item: selected, ...position } = currentSelection();
    try {
      await navigator.clipboard.writeText(
        JSON.stringify(
          { ...selected.source, name: selected.name, ...position },
          null,
          2,
        ),
      );
      setCopied("Copied");
    } catch {
      setCopied("Copy unavailable");
    }
  }
  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement === surface.current)
        await document.exitFullscreen();
      else await surface.current?.requestFullscreen();
    } catch {
      setNotice("Fullscreen is unavailable in this browser context.");
    }
  }
  const referenceControls = <>
            <MediaIconButton onClick={copy}
              label={copied || (selection.clip ? "Copy clip reference" : selection.region ? "Copy region reference" : isVideo ? "Copy time reference" : "Copy source reference")}
              icon={copied === "Copied" ? Check : copied ? CircleAlert : Copy}/>
            <span className="sr-only" role="status">{copied}</span>
            {artifact&&<MediaIconButton label={existingComment?"Open comment":"Comment on media"} icon={existingComment?(saved===existingComment.id?Check:MessageSquare):MessageSquarePlus} onClick={event=>{video.current?.pause();const box=event.currentTarget.getBoundingClientRect();const {item:media,...position}=currentSelection();setComment({x:box.left,y:box.top,initial:existingComment,anchor:existingComment?.anchor||{path:media.source?.file||media.source?.path,line_start:media.source?.line,media_key:mediaCommentKey(media.source,media.name),media:position}});}}/>}
            {onSelection && (
              <Button small onClick={() => onSelection(currentSelection())}>
                Use selection
              </Button>
            )}
            {(
              <MediaIconButton label="Select spatial region" icon={SquareDashed} aria-pressed={selecting} onClick={()=>setSelecting(!selecting)}/>
            )}
            {(region || clipStart !== null) && <MediaIconButton label="Clear reference selection" icon={RotateCcw} onClick={()=>{setRegion(null);setClipStart(null);setClipEnd(null);}}/>}
  </>;
  return createPortal(
    <dialog
      ref={dialog}
      className="prism-lightbox"
      aria-label={item.name || "Media viewer"}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onKeyDown={(event) => {
        if (
          event.defaultPrevented ||
          (event.target as HTMLElement).matches("input,textarea,select")
        )
          return;
        if (event.key === "Escape") {
          event.preventDefault();
          if (document.fullscreenElement === surface.current)
            void document.exitFullscreen();
          else onClose();
        }
        if (event.key === "0") changeView(FIT_VIEW);
        if (event.key === "+" || event.key === "=")
          changeView((previous) => ({
            ...previous,
            zoom: Math.min(32, previous.zoom * 1.25),
          }));
        if (event.key === "-")
          changeView((previous) => ({
            ...previous,
            zoom: Math.max(1, previous.zoom / 1.25),
          }));
        if (isVideo && event.key === ".") {
          event.preventDefault();
          step(1);
        }
        if (isVideo && event.key === ",") {
          event.preventDefault();
          step(-1);
        }
        if (
          isVideo &&
          event.key === " " &&
          !(event.target as HTMLElement).closest("button")
        ) {
          event.preventDefault();
          play();
        }
        if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
          event.preventDefault();
          const direction = event.key === "ArrowRight" ? 1 : -1;
          if (isVideo)
            seek((video.current?.currentTime ?? time) + direction * 5);
          else setIndex((index + direction + items.length) % items.length);
        }
      }}
    >
      <div ref={surface} className="prism-lightbox-surface">
        {comment&&artifact&&<CommentComposer artifact={artifact} initial={comment.initial} anchor={comment.anchor} position={comment} portalTarget={surface.current} onClose={()=>setComment(undefined)}/>}
        <header className="prism-lightbox-header">
          <div>
            <strong>{item.name || "Media"}</strong>
            <span>
              {index + 1} / {items.length}
            </span>
          </div>
          <div>
            {items.length > 1 && (
              <>
                <MediaIconButton
                  onClick={() =>
                    setIndex((index - 1 + items.length) % items.length)
                  }
                  label="Previous media"
                 icon={ChevronLeft}/>
                <MediaIconButton
                  onClick={() => setIndex((index + 1) % items.length)}
                  label="Next media"
                 icon={ChevronRight}/>
              </>
            )}
            <LinkButton small title="Download media" href={item.url} download={item.name || "media"}>
              <Download size={18} aria-hidden="true"/><span className="sr-only">Download media</span>
            </LinkButton>
            <MediaIconButton autoFocus onClick={onClose} label="Close media viewer" icon={X}/>
          </div>
        </header>
        <div
          ref={stage}
          tabIndex={0}
          role="region"
          aria-label={
            isVideo ? "Zoomable video viewport" : "Zoomable image viewport"
          }
          className={
            "prism-lightbox-stage" + (selecting ? " select-region" : "")
          }
          onDoubleClick={(event) => {
            if (!(event.target as HTMLElement).closest("button,input,select,a"))
              changeView(FIT_VIEW);
          }}
          onPointerDown={(event) => {
            if (
              selecting ||
              (event.target as HTMLElement).closest("button,input,select,a")
            )
              return;
            if (event.pointerType === "mouse" && event.button !== 0) return;
            event.preventDefault();
            event.currentTarget.focus({ preventScroll: true });
            event.currentTarget.setPointerCapture(event.pointerId);
            const current = { x: event.clientX, y: event.clientY };
            pointers.current.set(event.pointerId, current);
            const positions = [...pointers.current.values()];
            if (positions.length === 2) {
              const [a, b] = positions,
                box = event.currentTarget.getBoundingClientRect();
              pinch.current = {
                distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
                center: {
                  x: (a.x + b.x) / 2 - box.left,
                  y: (a.y + b.y) / 2 - box.top,
                },
                transform: viewToTransform(liveView.current, pane, natural),
              };
              drag.current = null;
            } else if (positions.length === 1)
              drag.current = { start: current, view: liveView.current };
          }}
          onPointerMove={(event) => {
            if (!pointers.current.has(event.pointerId)) return;
            pointers.current.set(event.pointerId, {
              x: event.clientX,
              y: event.clientY,
            });
            const gesture = pinch.current;
            if (gesture && pointers.current.size === 2) {
              const [a, b] = [...pointers.current.values()],
                box = event.currentTarget.getBoundingClientRect();
              const scale = clamp(
                (gesture.transform.scale * Math.hypot(a.x - b.x, a.y - b.y)) /
                  gesture.distance,
                1,
                32,
              );
              const center = {
                x: (a.x + b.x) / 2 - box.left,
                y: (a.y + b.y) / 2 - box.top,
              };
              changeView(
                transformToView(
                  {
                    scale,
                    x:
                      center.x -
                      ((gesture.center.x - gesture.transform.x) * scale) /
                        gesture.transform.scale,
                    y:
                      center.y -
                      ((gesture.center.y - gesture.transform.y) * scale) /
                        gesture.transform.scale,
                  },
                  pane,
                  natural,
                ),
              );
            } else if (drag.current) {
              const start = drag.current,
                old = viewToTransform(start.view, pane, natural);
              changeView(
                transformToView(
                  {
                    ...old,
                    x: old.x + event.clientX - start.start.x,
                    y: old.y + event.clientY - start.start.y,
                  },
                  pane,
                  natural,
                ),
              );
            }
          }}
          onPointerUp={pointerUp}
          onPointerCancel={pointerUp}
          onLostPointerCapture={pointerUp}
        >
          {failure ? (
            <div role="alert" className="prism-lightbox-error">
              {failure}{" "}
              <LinkButton small href={item.url} download={item.name}>
                Download original
              </LinkButton>
            </div>
          ) : (
            <div
              className="prism-lightbox-transform"
              style={{
                width: pane.w,
                height: pane.h,
                transform:
                  "translate(" +
                  transform.x +
                  "px," +
                  transform.y +
                  "px) scale(" +
                  transform.scale +
                  ")",
              }}
            >
              {isVideo ? (
                <video
                  key={index + ":" + item.url}
                  ref={video}
                  preload="metadata"
                  loop={loop && clipEnd === null}
                  playsInline
                  muted={muted}
                  style={{
                    position: "absolute",
                    left: fit.x,
                    top: fit.y,
                    width: fit.w,
                    height: fit.h,
                    imageRendering: crisp,
                  }}
                  onPlay={() => setPlaying(true)}
                  onPause={() => setPlaying(false)}
                  onEnded={() => setPlaying(false)}
                  onDurationChange={(event) => {
                    if (Number.isFinite(event.currentTarget.duration))
                      setDuration(event.currentTarget.duration);
                  }}
                  onLoadedMetadata={(event) => {
                    const node = event.currentTarget;
                    setNatural({ w: node.videoWidth, h: node.videoHeight });
                    setDuration(
                      Number.isFinite(node.duration) ? node.duration : 0,
                    );
                    node.volume = volume;
                    node.playbackRate = rate;
                    if (item.time && Number.isFinite(item.time)) {
                      node.currentTime = clamp(
                        item.time,
                        0,
                        Number.isFinite(node.duration)
                          ? node.duration
                          : item.time,
                      );
                      setTime(node.currentTime);
                    }
                  }}
                  onTimeUpdate={(event) => {
                    const node = event.currentTarget;
                    setTime(node.currentTime);
                    if (
                      loop &&
                      clipStart !== null &&
                      clipEnd !== null &&
                      clipEnd > clipStart &&
                      node.currentTime >= clipEnd
                    )
                      node.currentTime = clipStart;
                  }}
                  onError={() =>
                    setFailure(
                      "This video codec or file could not be decoded by the browser.",
                    )
                  }
                >
                  <source src={item.url} type={item.mime} />
                </video>
              ) : (
                <img
                  src={item.url}
                  alt={item.name || "Media"}
                  draggable={false}
                  style={{
                    position: "absolute",
                    left: fit.x,
                    top: fit.y,
                    width: fit.w,
                    height: fit.h,
                    imageRendering: crisp,
                  }}
                  onLoad={(event) =>
                    setNatural({
                      w: event.currentTarget.naturalWidth,
                      h: event.currentTarget.naturalHeight,
                    })
                  }
                  onError={() =>
                    setFailure(
                      "This image format could not be decoded by the browser.",
                    )
                  }
                />
              )}
              {(selecting || region) && (
                <div
                  className="prism-region-layer"
                  aria-label="Spatial reference region"
                  style={{
                    left: fit.x,
                    top: fit.y,
                    width: fit.w,
                    height: fit.h,
                    pointerEvents: selecting ? "auto" : "none",
                  }}
                  onPointerDown={(event) => {
                    event.stopPropagation();
                    video.current?.pause();
                    const p = point(event);
                    setRegion({ a: p, b: p });
                    event.currentTarget.setPointerCapture(event.pointerId);
                  }}
                  onPointerMove={(event) => {
                    if (event.buttons) {
                      const p = point(event);
                      setRegion((previous) =>
                        previous ? { ...previous, b: p } : null,
                      );
                    }
                  }}
                  onPointerUp={(event) => {
                    const p = point(event);
                    setRegion((previous) =>
                      previous ? { ...previous, b: p } : null,
                    );
                  }}
                >
                  {region && (
                    <div
                      className="prism-region-box"
                      style={{
                        left: Math.min(region.a.x, region.b.x) * 100 + "%",
                        top: Math.min(region.a.y, region.b.y) * 100 + "%",
                        width: Math.abs(region.b.x - region.a.x) * 100 + "%",
                        height: Math.abs(region.b.y - region.a.y) * 100 + "%",
                        borderWidth: 2 / transform.scale,
                      }}
                    />
                  )}
                </div>
              )}
            </div>
          )}
          <div className="prism-zoom-toolbar" aria-label="Media zoom controls">
            <MediaIconButton
              label="Zoom out"
              onClick={() =>
                changeView((previous) => ({
                  ...previous,
                  zoom: Math.max(1, previous.zoom / 1.25),
                }))
              }
             icon={ZoomOut}/>
            <output aria-label="Zoom level">
              {Math.round(view.zoom * 100)}%
            </output>
            <MediaIconButton
              label="Zoom in"
              onClick={() =>
                changeView((previous) => ({
                  ...previous,
                  zoom: Math.min(32, previous.zoom * 1.25),
                }))
              }
             icon={ZoomIn}/>
            <MediaIconButton onClick={() => changeView(FIT_VIEW)} label="Fit / reset" icon={Shrink}/>
            <MediaIconButton onClick={() => void toggleFullscreen()} label={fullscreen ? "Exit fullscreen" : "Fullscreen viewer"} icon={fullscreen ? Minimize2 : Maximize2}/>
          </div>
          <span className="prism-gesture-hint">
            {selecting ? "Drag on the content to select a spatial region" : "Scroll or pinch to zoom · drag to pan · double-click to fit"}
          </span>
        </div>
        {isVideo && (
          <div className="prism-video-transport">
            <div className="prism-video-main">
              <MediaIconButton onClick={play} label={playing ? "Pause video" : "Play video"} icon={playing ? Pause : Play}/>
              <VideoTimeline duration={duration} time={time} selecting={selectingTime} clip={selection.clip || null} onSeek={seek} onClip={clip=>{video.current?.pause();setClipStart(clip.start);setClipEnd(clip.end);}}/>
              <span>
                {time.toFixed(3)} / {duration.toFixed(3)} s
              </span>
            </div>
            {selectingTime && <div className="media-selection-summary">Drag across the timeline to select a range. {selection.clip && `${selection.clip.start.toFixed(2)}–${selection.clip.end.toFixed(2)} s`}{selection.region && ` · ${selection.region.width} × ${selection.region.height} px region`}</div>}
            <div className="prism-video-options">
              <MediaIconButton
                onClick={() => step(-1)}
                label="Previous video frame"
               icon={StepBack}/>
              <MediaIconButton onClick={() => step(1)} label="Next video frame" icon={StepForward}/>
              <label>
                FPS{" "}
                <input
                  aria-label="Frame step rate"
                  type="number"
                  min={1}
                  max={240}
                  value={fps}
                  onChange={(event) =>
                    setFps(clamp(Number(event.target.value) || 30, 1, 240))
                  }
                />
              </label>
              <label>
                Speed{" "}
                <Select
                  label="Playback speed"
                  value={String(rate)}
                  onChange={(value) => {
                    const next = Number(value);
                    setRate(next);
                    if (video.current) video.current.playbackRate = next;
                  }}
                  choices={[0.25, 0.5, 1, 1.5, 2].map((value) => ({
                    value: String(value),
                    label: value + "×",
                  }))}
                />
              </label>
              <MediaIconButton
                onClick={() => {
                  setMuted(!muted);
                  if (video.current) video.current.muted = !muted;
                }}
                label={muted ? "Unmute video" : "Mute video"}
                icon={muted ? VolumeX : Volume2}
              />
              <input
                aria-label="Video volume"
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={volume}
                onChange={(event) => {
                  const next = Number(event.target.value);
                  setVolume(next);
                  if (video.current) video.current.volume = next;
                }}
              />
              <MediaIconButton label="Loop playback" icon={Repeat} aria-pressed={loop} onClick={()=>setLoop(!loop)}/>
              <MediaIconButton label="Select time range" icon={Scissors} aria-pressed={selectingTime} onClick={()=>setSelectingTime(!selectingTime)}/>
              {referenceControls}
            </div>
          </div>
        )}
        {notice && (
          <div role="status" className="prism-media-notice">
            {notice}
          </div>
        )}
        {!isVideo && <footer className="prism-lightbox-footer">
          <div>
            {referenceControls}
          </div>
          <small>
            {natural ? natural.w + " × " + natural.h : "Loading dimensions"}
            {isVideo && !item.fps ? " · frame step uses chosen FPS" : ""}
          </small>
        </footer>}
      </div>
    </dialog>, document.body
  );
}
