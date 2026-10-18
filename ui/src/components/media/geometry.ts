// Adapted from Cairn Track view-geometry.ts; Apache-2.0, see LICENSE and ORIGIN.md.
/**
 * The zoom/pan view of a media pane (image, video), in a form that does not
 * depend on the pane's size.
 *
 * The zoom library (react-zoom-pan-pinch, inside ZoomSplitPane) works in
 * PIXELS: `translate(x, y) scale(s)` with origin top-left, applied to a box
 * that fills the pane, with the media `object-contain`-fitted inside it.
 * The same `{x, y}` means a different visible region as soon as the pane has
 * another size (the card or the window resizes, the card opens in its
 * settings view, two panes of different widths, gallery cells whose media
 * have different aspect ratios): the view jumped, drifted towards the
 * top-left corner, or showed the empty letterbox. So the pixel transform
 * never leaves ZoomSplitPane; cards keep and share a {@link ZoomView}:
 *
 * - `zoom`: magnification relative to the fitted view (1 = fitted).
 * - `cx`, `cy`: the point of the media at the pane's centre, as a fraction
 *   of the media's width and height (0..1; 0.5, 0.5 is its middle).
 *
 * Each pane maps the view through its own size and its own media's fit rect
 * ({@link viewToTransform}) at render and on every resize, and turns its
 * user's gestures back into a view ({@link transformToView}). Every pane of a
 * card then centres the same point of its media at the same zoom, and a
 * resize keeps that point where it was.
 *
 * Pure: tested in `view-geometry.test.ts`.
 */
const MAX_SCALE = 32, MIN_SCALE = 1;
export type PaneTransform = { x: number; y: number; scale: number };

export interface ZoomView {
  /** Magnification relative to the fitted view. */
  zoom: number;
  /** The media point at the pane's centre, as a fraction of its width. */
  cx: number;
  /** … and of its height. */
  cy: number;
}

export interface Size {
  w: number;
  h: number;
}

/** A rectangle in pane pixels (before the zoom transform). */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** The fitted view: the whole media, centred. */
export const FIT_VIEW: ZoomView = Object.freeze({ zoom: 1, cx: 0.5, cy: 0.5 });

/** The view is the fitted one (nothing to reset). */
export const isFitView = (v: ZoomView): boolean => Math.abs(v.zoom - 1) < 1e-6;

const known = (s: Size | null | undefined): s is Size => !!s && s.w > 0 && s.h > 0;

/**
 * Where `object-contain` puts media of natural size `content` in a `pane`:
 * scaled to fit, centred. While the content size is unknown the whole pane
 * stands in for it.
 */
export function fitRect(pane: Size, content: Size | null | undefined): Rect {
  if (!known(content)) return { x: 0, y: 0, w: pane.w, h: pane.h };
  const f = Math.min(pane.w / content.w, pane.h / content.h);
  const w = content.w * f;
  const h = content.h * f;
  return { x: (pane.w - w) / 2, y: (pane.h - h) / 2, w, h };
}

/**
 * The zoom library's bounds (`limitToBounds` + `centerZoomedOut`, scale ≥ 1):
 * the zoomed pane box covers the pane, so `x ∈ [w − w·s, 0]`.
 */
export function clampTransform(t: PaneTransform, pane: Size): PaneTransform {
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, t.scale));
  const clamp = (p: number, size: number) => Math.min(0, Math.max(size - size * scale, p));
  return { scale, x: clamp(t.x, pane.w), y: clamp(t.y, pane.h) };
}

/**
 * The pixel transform that shows `view` in a pane: the media point
 * (`cx`, `cy`) at the pane's centre, magnified `zoom` times the fit, then
 * kept inside the pan bounds (a pane that cannot centre that point shows the
 * nearest view it can; the view itself is not changed by that).
 */
export function viewToTransform(view: ZoomView, pane: Size, content: Size | null | undefined): PaneTransform {
  if (!(pane.w > 0) || !(pane.h > 0)) return { scale: 1, x: 0, y: 0 };
  const fit = fitRect(pane, content);
  const s = Math.min(MAX_SCALE, Math.max(MIN_SCALE, view.zoom));
  return clampTransform(
    {
      scale: s,
      x: pane.w / 2 - s * (fit.x + view.cx * fit.w),
      y: pane.h / 2 - s * (fit.y + view.cy * fit.h),
    },
    pane,
  );
}

/** The view a pixel transform shows in a pane (inverse of {@link viewToTransform} inside the bounds). */
export function transformToView(t: PaneTransform, pane: Size, content: Size | null | undefined): ZoomView {
  if (!(pane.w > 0) || !(pane.h > 0) || !(t.scale > 0)) return FIT_VIEW;
  const fit = fitRect(pane, content);
  return {
    zoom: t.scale,
    cx: ((pane.w / 2 - t.x) / t.scale - fit.x) / fit.w,
    cy: ((pane.h / 2 - t.y) / t.scale - fit.y) / fit.h,
  };
}

/**
 * The part of the media a pane shows for a view, as fractions of the media's
 * width and height (may extend past 0..1 into the letterbox).
 */
export function visibleContent(view: ZoomView, pane: Size, content: Size | null | undefined): Rect {
  const t = viewToTransform(view, pane, content);
  const fit = fitRect(pane, content);
  const at = (sx: number, sy: number) => [((sx - t.x) / t.scale - fit.x) / fit.w, ((sy - t.y) / t.scale - fit.y) / fit.h];
  const [x0, y0] = at(0, 0);
  const [x1, y1] = at(pane.w, pane.h);
  return { x: x0!, y: y0!, w: x1! - x0!, h: y1! - y0! };
}
