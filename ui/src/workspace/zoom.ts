import { useEffect, useRef } from 'react';

// Cairn 1's zoomable panes: a view is independent of a pane's size, so every pane of a card
// shows the same point of its image at the same zoom.
type View = { zoom: number; x: number; y: number };
type Pane = { show: () => void };
export class SharedZoom {
  view: View = { zoom: 1, x: 0.5, y: 0.5 };
  readonly panes = new Set<Pane>();
  set(view: View) {
    this.view = view;
    this.panes.forEach((pane) => pane.show());
  }
  reset() {
    this.set({ zoom: 1, x: 0.5, y: 0.5 });
  }
}

const maxZoom = 64;
const wheelSpeed = 0.003;
/** A source pixel that covers more than this many screen pixels is drawn crisp. */
const crispAbove = 1.5;

/** Wheel zooms at the pointer, a drag pans, a double click fits again. */
export function useZoomPane(shared: SharedZoom) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const pane = host.current!;
    const layer = pane.firstElementChild as HTMLElement;
    /** The image as it is fitted into the pane, before any zoom. */
    const fitted = () => {
      const { width, height } = pane.getBoundingClientRect();
      const image = pane.querySelector<HTMLImageElement>('.workspace-held-layer:not(.pending) img') ?? undefined;
      const [w, h] = [image?.naturalWidth || width, image?.naturalHeight || height];
      const scale = Math.min(width / w, height / h);
      return { width, height, w: w * scale, h: h * scale, scale };
    };
    const offset = (size: number, image: number, zoom: number, center: number) => {
      const free = size / 2 - zoom * ((size - image) / 2 + center * image);
      return Math.min(0, Math.max(size - size * zoom, free));
    };
    const place = () => {
      const { view } = shared;
      const fit = fitted();
      return {
        ...fit,
        left: offset(fit.width, fit.w, view.zoom, view.x),
        top: offset(fit.height, fit.h, view.zoom, view.y),
      };
    };
    const show = () => {
      const { zoom } = shared.view;
      const { left, top, scale } = place();
      layer.style.transform = zoom === 1 ? '' : `translate(${left}px, ${top}px) scale(${zoom})`;
      pane.classList.toggle('is-zoomed', zoom > 1);
      pane.classList.toggle('is-crisp', zoom * scale > crispAbove);
    };
    /** The view in which the pane's content stands at `left`, `top`. */
    const stand = (zoom: number, left: number, top: number) => {
      const { width, height, w, h } = fitted();
      const x = Math.min(0, Math.max(width - width * zoom, left));
      const y = Math.min(0, Math.max(height - height * zoom, top));
      shared.set({
        zoom,
        x: ((width / 2 - x) / zoom - (width - w) / 2) / w,
        y: ((height / 2 - y) / zoom - (height - h) / 2) / h,
      });
    };
    const wheel = (event: WheelEvent) => {
      const { zoom } = shared.view;
      const next = Math.min(
        maxZoom,
        Math.max(1, zoom * Math.exp(-event.deltaY * (event.deltaMode ? 16 : 1) * wheelSpeed)),
      );
      // Fitted already: the page scrolls on.
      if (next === zoom) return;
      event.preventDefault();
      const box = pane.getBoundingClientRect();
      const [x, y] = [event.clientX - box.left, event.clientY - box.top];
      const { left, top } = place();
      stand(next, x - ((x - left) / zoom) * next, y - ((y - top) / zoom) * next);
    };
    const press = (event: PointerEvent) => {
      if (event.button || shared.view.zoom === 1 || (event.target as Element).closest('button')) return;
      event.preventDefault();
      const { left, top } = place();
      const move = (e: PointerEvent) =>
        stand(shared.view.zoom, left + e.clientX - event.clientX, top + e.clientY - event.clientY);
      const stop = () => {
        pane.removeEventListener('pointermove', move);
        pane.removeEventListener('pointerup', stop);
        pane.removeEventListener('pointercancel', stop);
      };
      pane.setPointerCapture(event.pointerId);
      pane.addEventListener('pointermove', move);
      pane.addEventListener('pointerup', stop);
      pane.addEventListener('pointercancel', stop);
    };
    const reset = () => shared.reset();
    const member = { show };
    shared.panes.add(member);
    const resize = new ResizeObserver(show);
    resize.observe(pane);
    pane.addEventListener('load', show, true);
    pane.addEventListener('wheel', wheel, { passive: false });
    pane.addEventListener('pointerdown', press);
    pane.addEventListener('dblclick', reset);
    return () => {
      shared.panes.delete(member);
      resize.disconnect();
      pane.removeEventListener('load', show, true);
      pane.removeEventListener('wheel', wheel);
      pane.removeEventListener('pointerdown', press);
      pane.removeEventListener('dblclick', reset);
    };
  }, [shared]);
  return host;
}
