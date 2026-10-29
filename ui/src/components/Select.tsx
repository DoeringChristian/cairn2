import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from "react";

import { createPortal } from "react-dom";

const unplaced:CSSProperties={visibility:"hidden",position:"fixed",width:"max-content",minWidth:0,maxWidth:"calc(100vw - 16px)"};

export type Choice = { value: string; label: string; note?: string };

/** A select in the report theme: a button and a listbox of our own instead of the system menu.
 *  Keyboard: arrows/Home/End move, Enter/Space choose, Escape closes, typing jumps to a label. */
export function Select({ label, value, choices, onChange, disabled=false }: { label: string; value: string; choices: Choice[]; onChange: (value: string) => void; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [place, setPlace] = useState<CSSProperties>(unplaced);
  const id = useId();
  const current = choices.find(c => c.value === value) ?? choices[0];
  const show = () => { setActive(Math.max(0, choices.findIndex(c => c.value === value))); setOpen(true); };
  const choose = (index: number) => { setOpen(false); root.current?.querySelector("button")?.focus(); if (choices[index] && choices[index].value !== value) onChange(choices[index].value); };
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node) && !list.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  useLayoutEffect(() => {
    if (!open) return;
    const natural = list.current!.scrollWidth;
    // Fixed placement keeps the menu inside the viewport, flipping above when space below is short.
    const position = () => {
      const box = root.current!.getBoundingClientRect(), margin = 8, gap = 6;
      const width = Math.min(Math.max(natural, box.width), innerWidth - 2 * margin);
      const below = innerHeight - box.bottom - gap - margin, above = box.top - gap - margin;
      const up = below < 160 && above > below;
      setPlace({ position: "fixed", left: Math.max(margin, Math.min(box.left, innerWidth - margin - width)), right: "auto", width, minWidth: 0,
        maxHeight: Math.min(320, up ? above : below), top: up ? "auto" : box.bottom + gap, bottom: up ? innerHeight - box.top + gap : "auto" });
    };
    position();
    addEventListener("resize", position);
    addEventListener("scroll", position, true);
    return () => { removeEventListener("resize", position); removeEventListener("scroll", position, true); setPlace(unplaced); };
  }, [open]);
  useEffect(() => { if (open) list.current?.children[active]?.scrollIntoView({ block: "nearest" }); }, [open, active]);
  const onKey = (event: React.KeyboardEvent) => {
    const last = choices.length - 1;
    const move: Record<string, number> = { ArrowDown: Math.min(last, active + 1), ArrowUp: Math.max(0, active - 1), Home: 0, End: last, PageDown: Math.min(last, active + 8), PageUp: Math.max(0, active - 8) };
    if (event.key in move) { event.preventDefault(); if (!open) show(); else setActive(move[event.key]); return; }
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); if (open) choose(active); else show(); return; }
    if (event.key === "Escape" && open) { event.preventDefault(); setOpen(false); return; }
    if (event.key === "Tab") { setOpen(false); return; }
    if (event.key.length === 1 && /\S/.test(event.key)) {
      const key = event.key.toLowerCase(), from = open ? active + 1 : 0;
      const hit = [...choices.slice(from), ...choices.slice(0, from)].findIndex(c => c.label.toLowerCase().includes(key));
      if (hit >= 0) { if (!open) show(); setActive((from + hit) % choices.length); }
    }
  };
  return (
    <div className="select" ref={root}>
      <button type="button" disabled={disabled} className="select-button" aria-label={label} aria-haspopup="listbox" aria-expanded={open} aria-controls={id}
        aria-activedescendant={open ? `${id}-${active}` : undefined} onClick={() => (open ? setOpen(false) : show())} onKeyDown={onKey}>
        <span className="select-value">{current?.label}</span>
      </button>
      {open && createPortal(
        <ul className="select-list" role="listbox" id={id} aria-label={label} ref={list} style={place}>
          {choices.map((c, i) => (
            <li key={c.value} id={`${id}-${i}`} role="option" aria-selected={c.value === value} className={i === active ? "active" : undefined}
              onPointerEnter={() => setActive(i)} onPointerDown={e => { if (e.pointerType === 'mouse') e.preventDefault(); }} onClick={() => choose(i)}>
              <span>{c.label}</span>{c.note && <small>{c.note}</small>}
            </li>
          ))}
        </ul>, root.current?.closest("dialog") ?? document.body
      )}
    </div>
  );
}
