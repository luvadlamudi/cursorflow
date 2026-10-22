import type { Batch, Event } from "./model";

export type TrackOptions = {
  // Where batches are posted. Default "/api/flow".
  endpoint?: string;
  // How often the cursor position is sampled. Default 66ms (about 15 per second).
  sampleMs?: number;
  // How often batches are sent while the tab is open. Default 15s.
  flushMs?: number;
  // Narrower windows are treated as tablets and skipped. Default 1024.
  minWidth?: number;
  // With no mouse, scroll or key input for this long, the visitor counts as idle and tracking
  // pauses until they're back. Default 30s.
  idleMs?: number;
};

const SESSION_KEY = "cursorflow:sid";
const START_KEY = "cursorflow:t0";

// Mouse-and-keyboard devices only, never inside a frame (the replay embeds the site), and never
// when the browser asks not to be tracked.
export function canTrack(minWidth = 1024) {
  if (typeof window === "undefined" || window.self !== window.top) return false;
  const nav = navigator as Navigator & { globalPrivacyControl?: boolean };
  if (nav.webdriver || nav.doNotTrack === "1" || nav.globalPrivacyControl) return false;
  return matchMedia("(hover: hover) and (pointer: fine)").matches && innerWidth >= minWidth;
}

// Starts tracking and returns a function that stops it. Safe to call on any device: where
// tracking doesn't apply it does nothing.
export function track(options: TrackOptions = {}): () => void {
  const { endpoint = "/api/flow", sampleMs = 66, flushMs = 15000, minWidth = 1024, idleMs = 30000 } = options;
  if (!canTrack(minWidth)) return () => {};

  // One session per tab: it survives reloads and route changes, but a new tab is a new visitor.
  const sid = sessionStorage.getItem(SESSION_KEY) ?? crypto.randomUUID();
  const t0 = Number(sessionStorage.getItem(START_KEY)) || Date.now();
  sessionStorage.setItem(SESSION_KEY, sid);
  sessionStorage.setItem(START_KEY, String(t0));
  const now = () => Date.now() - t0;

  let events: Event[] = [];
  let x = 0;
  let y = 0;
  let moved = false;
  let scroll = -1;
  let route = "";
  let anchor: Element | null = null;
  let section = "";
  let lastInput = Date.now();
  let sampler = 0;

  const setAnchor = (el: Element | null) => {
    if (el === anchor) return;
    if (anchor) events.push(["l", now(), anchor.getAttribute("data-flow")!]);
    anchor = el;
    if (!el) return;
    // The rect is read once per enter, not per sample, so tracking never forces layout in a loop.
    const r = el.getBoundingClientRect();
    events.push(["e", now(), el.getAttribute("data-flow")!, Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]);
  };

  // Any input marks the visitor as present, and wakes the sampler if they'd gone idle.
  const active = () => {
    lastInput = Date.now();
    if (!sampler) {
      sampler = window.setInterval(sample, sampleMs);
      sample();
    }
  };

  // Listeners only note the latest state; the sampler below decides what gets kept.
  const onMove = (e: MouseEvent) => {
    x = e.clientX;
    y = e.clientY;
    moved = true;
    active();
  };
  const onOver = (e: MouseEvent) => setAnchor((e.target as Element).closest?.("[data-flow]") ?? null);
  const onOut = (e: MouseEvent) => !e.relatedTarget && setAnchor(null);
  const onClick = (e: MouseEvent) => {
    events.push(["c", now(), e.clientX, e.clientY]);
    active();
  };

  // Which section sits on the line across the middle of the screen. An observer reports it without
  // any layout reads. When several match (a fixed hero under the content), the last one in the
  // page wins, since it's drawn on top.
  const crossing = new Set<Element>();
  const sections = new IntersectionObserver(
    (entries) => {
      for (const e of entries) e.isIntersecting ? crossing.add(e.target) : crossing.delete(e.target);
      const all = Array.from(document.querySelectorAll("[data-flow-section]")).filter((el) => crossing.has(el));
      const id = all.at(-1)?.getAttribute("data-flow-section") ?? "";
      if (id && id !== section) {
        section = id;
        events.push(["v", now(), id]);
      }
    },
    { rootMargin: "-50% 0px -50% 0px" },
  );
  const watchSections = () => {
    sections.disconnect();
    crossing.clear();
    document.querySelectorAll("[data-flow-section]").forEach((el) => sections.observe(el));
  };

  // Stops sampling, and records when the visitor was last actually there, so nobody gets credited
  // with reading a section they walked away from.
  const idle = (since: number) => {
    window.clearInterval(sampler);
    sampler = 0;
    events.push(["i", now(), since]);
    setAnchor(null);
    flush();
  };

  function sample() {
    if (Date.now() - lastInput > idleMs) return idle(Date.now() - lastInput);
    if (location.pathname !== route) {
      route = location.pathname;
      section = "";
      events.push(["r", now(), route]);
      // The new page may still be rendering; give it a moment before looking for its sections.
      setTimeout(watchSections, 300);
    }
    if (moved) {
      moved = false;
      events.push(["m", now(), x, y]);
    }
    const s = Math.round(scrollY);
    if (s !== scroll) {
      scroll = s;
      events.push(["s", now(), s, document.documentElement.scrollHeight - innerHeight]);
    }
  }

  function flush() {
    if (!events.length) return;
    const batch: Batch = { sid, vw: innerWidth, vh: innerHeight, events };
    events = [];
    navigator.sendBeacon(endpoint, JSON.stringify(batch));
  }

  // A hidden tab counts as idle straight away; coming back is just more input.
  const onVisibility = () => {
    if (document.visibilityState === "hidden") sampler ? idle(0) : flush();
  };

  const opts = { passive: true, capture: true };
  addEventListener("mousemove", onMove, opts);
  addEventListener("mouseover", onOver, opts);
  addEventListener("mouseout", onOut, opts);
  addEventListener("click", onClick, opts);
  addEventListener("scroll", active, opts);
  addEventListener("keydown", active, opts);
  addEventListener("pagehide", flush);
  document.addEventListener("visibilitychange", onVisibility);
  const flusher = window.setInterval(flush, flushMs);
  active();

  return () => {
    removeEventListener("mousemove", onMove, opts);
    removeEventListener("mouseover", onOver, opts);
    removeEventListener("mouseout", onOut, opts);
    removeEventListener("click", onClick, opts);
    removeEventListener("scroll", active, opts);
    removeEventListener("keydown", active, opts);
    removeEventListener("pagehide", flush);
    document.removeEventListener("visibilitychange", onVisibility);
    sections.disconnect();
    window.clearInterval(sampler);
    window.clearInterval(flusher);
    flush();
  };
}
