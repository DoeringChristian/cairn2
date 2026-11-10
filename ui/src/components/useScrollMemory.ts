import { useEffect, useLayoutEffect, useRef } from 'react';
import { useLocation, useNavigationType } from 'react-router-dom';

const storageKey = 'cairn:scroll';
const remembered = 100;
const patience = 10_000;

function positions(): [string, number][] {
  try {
    return JSON.parse(sessionStorage.getItem(storageKey) || '[]');
  } catch {
    return [];
  }
}

function remember(entry: string, top: number) {
  try {
    const kept = positions().filter(([key]) => key !== entry);
    sessionStorage.setItem(storageKey, JSON.stringify([...kept.slice(1 - remembered), [entry, top]]));
  } catch {
    // Without session storage, pages simply open at the top.
  }
}

/** Each history entry keeps its scroll position across back, forward and reload. Pages load their
 * content after rendering, so a restore waits for the page to grow until the reader scrolls. */
export function useScrollMemory() {
  const location = useLocation();
  const navigation = useNavigationType();
  const restoring = useRef(false);
  const pathname = useRef(location.pathname);
  // A freshly loaded page's entry has the key "default", whatever its URL.
  const entry = `${location.key}:${location.pathname}${location.search}`;

  useEffect(() => {
    history.scrollRestoration = 'manual';
  }, []);

  useEffect(() => {
    let frame = 0;
    const save = () => {
      if (restoring.current) return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => remember(entry, scrollY));
    };
    addEventListener('scroll', save, { passive: true });
    return () => {
      removeEventListener('scroll', save);
      cancelAnimationFrame(frame);
    };
  }, [entry]);

  useLayoutEffect(() => {
    const moved = pathname.current !== location.pathname;
    pathname.current = location.pathname;
    if (navigation !== 'POP') {
      if (navigation === 'PUSH' && moved) scrollTo(0, 0);
      return;
    }
    const top = positions().find(([key]) => key === entry)?.[1];
    if (!top) return;
    restoring.current = true;
    const attempt = () => {
      scrollTo(0, top);
      if (Math.abs(scrollY - top) < 2) stop();
    };
    const growth = new ResizeObserver(attempt);
    const timer = setTimeout(() => stop(), patience);
    const inputs = ['wheel', 'touchstart', 'keydown', 'pointerdown'];
    function stop() {
      restoring.current = false;
      growth.disconnect();
      clearTimeout(timer);
      inputs.forEach((type) => removeEventListener(type, stop));
    }
    inputs.forEach((type) => addEventListener(type, stop, { passive: true }));
    growth.observe(document.body);
    attempt();
    return stop;
  }, [entry]);
}
