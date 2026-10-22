import { stopKey, type FlowModel, type Move, type Stop } from "./model";

export type ReplayOptions = {
  // The site to replay on. It has to be same-origin, since the player reads the page to find the
  // anchors. Default: the current origin.
  site?: string;
  // The desktop the replay pretends to be. Default 1440 x 900.
  width?: number;
  height?: number;
  // How long to let a page settle after it loads, before the cursor starts. Default 1200ms.
  settleMs?: number;
  // Called as the cursor reaches each stop. Long stops play faster than real time (see `played`),
  // so show `stop.dwell` if you want the real number.
  onStop?: (stop: Stop, index: number) => void;
};

type Point = { x: number; y: number };

const CURSOR = `<svg width="22" height="22" viewBox="0 0 22 22" fill="none"><path d="M3 2.5v15.2l4.1-3.9 2.6 6 2.8-1.2-2.6-5.9h5.7L3 2.5Z" fill="#fff" stroke="#000" stroke-width="1.3" stroke-linejoin="round"/></svg>`;

// Real dwell times are kept up to a few seconds; past that they're compressed, so a 20 second read
// still feels long next to a quick hover without stalling the replay.
const played = (dwell: number) => Math.min(6000, dwell <= 2500 ? dwell : 2500 + (dwell - 2500) * 0.2);

const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const nextFrame = () => new Promise<number>((r) => requestAnimationFrame(r));

// Plays the model's path on a live, scaled-down copy of the site inside `container`. Returns a
// function that stops it.
export function replay(container: HTMLElement, model: FlowModel, options: ReplayOptions = {}): () => void {
  const { site = location.origin, width: W = 1440, height: H = 900, settleMs = 1200, onStop } = options;
  let alive = true;
  let visible = true;

  container.style.position ||= "relative";
  container.style.overflow = "hidden";

  // The page and the cursor share a stage at desktop size, and the stage is scaled to fit.
  const stage = document.createElement("div");
  stage.style.cssText = `position:absolute;left:0;top:0;width:${W}px;height:${H}px;transform-origin:0 0`;
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.tabIndex = -1;
  frame.style.cssText = `width:${W}px;height:${H}px;border:0;pointer-events:none;display:block`;
  const cursor = document.createElement("div");
  cursor.innerHTML = CURSOR;
  cursor.style.cssText = "position:absolute;left:0;top:0;transform-origin:0 0;will-change:transform;z-index:2";
  stage.append(frame, cursor);
  container.append(stage);

  let scale = 1;
  const fit = () => {
    scale = container.clientWidth / W;
    stage.style.transform = `scale(${scale})`;
  };
  // The height follows the width through the aspect ratio. Setting a height directly would let an
  // aspect-ratio container size its width from it, shrinking the stage on every resize.
  container.style.aspectRatio = `${W} / ${H}`;
  const resize = new ResizeObserver(fit);
  resize.observe(container);
  fit();

  // Nothing runs while the player is off screen.
  const seen = new IntersectionObserver(([e]) => (visible = e.isIntersecting));
  seen.observe(container);
  const awake = async () => {
    while (alive && !visible) await sleep(300);
    if (!alive) throw new Error("stopped");
  };

  let pos: Point = { x: model.start.x * W, y: model.start.y * H };
  let hovered: Element | null = null;
  let route = "";

  const place = (p: Point) => {
    pos = p;
    // The cursor is counter-scaled so it stays a normal size however small the stage gets.
    cursor.style.transform = `translate(${p.x}px, ${p.y}px) scale(${1 / scale})`;
    hover(p);
  };

  // Synthetic pointer events, so the page's own hover effects (JS-driven ones, not CSS :hover)
  // react to the ghost the way they would to a person.
  const hover = (p: Point) => {
    const win = frame.contentWindow as (Window & typeof globalThis) | null;
    const doc = frame.contentDocument;
    if (!win || !doc) return;
    const el = doc.elementFromPoint(p.x, p.y);
    const init = { bubbles: true, clientX: p.x, clientY: p.y, pointerType: "mouse" };
    if (el !== hovered) {
      hovered?.dispatchEvent(new win.PointerEvent("pointerout", { ...init, relatedTarget: el }));
      hovered?.dispatchEvent(new win.MouseEvent("mouseout", { ...init, relatedTarget: el }));
      el?.dispatchEvent(new win.PointerEvent("pointerover", { ...init, relatedTarget: hovered }));
      el?.dispatchEvent(new win.MouseEvent("mouseover", { ...init, relatedTarget: hovered }));
      hovered = el;
    }
    el?.dispatchEvent(new win.PointerEvent("pointermove", init));
    el?.dispatchEvent(new win.MouseEvent("mousemove", init));
  };

  const load = async (next: string, force = false) => {
    if (next === route && !force) return;
    route = next;
    hovered = null;
    const loaded = new Promise((r) => frame.addEventListener("load", r, { once: true }));
    frame.src = site + next;
    await loaded;
    await sleep(settleMs);
  };

  const scrollTo = async (y: number, ms = 800) => {
    const win = frame.contentWindow!;
    const from = win.scrollY;
    const max = win.document.documentElement.scrollHeight - H;
    const to = Math.max(0, Math.min(max, y));
    if (Math.abs(to - from) < 4) return;
    const t0 = performance.now();
    for (let t = 0; t < 1; ) {
      await awake();
      t = Math.min(1, (performance.now() - t0) / ms);
      win.scrollTo(0, from + (to - from) * ease(t));
      await nextFrame();
    }
  };

  // The first visible copy of an anchor (responsive layouts often render the same thing twice).
  const find = (stop: Stop) => {
    const attr = stop.kind === "read" ? "data-flow-section" : "data-flow";
    const els = frame.contentDocument?.querySelectorAll(`[${attr}="${CSS.escape(stop.anchor)}"]`) ?? [];
    for (const el of Array.from(els)) {
      const r = el.getBoundingClientRect();
      if (r.width && r.height) return el;
    }
    return null;
  };

  // Follows the learned curve from where the cursor is to `to`, rotated and stretched to fit.
  const moveTo = async (to: Point, move: Move) => {
    const a = pos;
    const dx = to.x - a.x;
    const dy = to.y - a.y;
    const n = move.curve.length - 1;
    const t0 = performance.now();
    for (let t = 0; t < 1; ) {
      await awake();
      t = Math.min(1, (performance.now() - t0) / move.ms);
      const i = Math.min(n - 1, Math.floor(t * n));
      const f = t * n - i;
      const cx = move.curve[i][0] + (move.curve[i + 1][0] - move.curve[i][0]) * f;
      const cy = move.curve[i][1] + (move.curve[i + 1][1] - move.curve[i][1]) * f;
      place({ x: a.x + cx * dx - cy * dy, y: a.y + cx * dy + cy * dx });
      await nextFrame();
    }
    place(to);
  };

  // Clicks are shown, not performed, so the page never jumps, opens a menu over itself, or leaves
  // the site. When the next stop is on another page, the player loads it.
  const click = () => {
    const ring = document.createElement("div");
    ring.style.cssText = `position:absolute;left:${pos.x}px;top:${pos.y}px;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;border:2px solid #fff;pointer-events:none;z-index:1`;
    stage.append(ring);
    ring.animate([{ transform: "scale(0.2)", opacity: 1 }, { transform: "scale(1)", opacity: 0 }], { duration: 600, easing: "ease-out" }).onfinish = () => ring.remove();
  };

  const play = async () => {
    await load(model.path[0]?.route ?? "/", true);
    place({ x: model.start.x * W, y: model.start.y * H });
    while (alive) {
      let prev: Stop | null = null;

      for (const [i, stop] of model.path.entries()) {
        await awake();
        await load(stop.route);
        const doc = frame.contentDocument!;
        await scrollTo(stop.scroll * (doc.documentElement.scrollHeight - H));

        const el = find(stop);
        if (!el) continue;
        let r = el.getBoundingClientRect();
        // Layouts differ, so make sure the thing is actually on screen: a section has to cross the
        // middle of the screen (that's what the tracker counted), an element just has to be visible.
        const off = stop.kind === "read" ? r.top > H / 2 || r.bottom < H / 2 : r.bottom < 0 || r.top > H;
        if (off) {
          // A section's top goes a fifth of the way down the screen; an element goes to the middle.
          const offset = stop.kind === "read" ? -H * 0.2 : r.height / 2 - H / 2;
          await scrollTo(frame.contentWindow!.scrollY + r.top + offset);
          r = el.getBoundingClientRect();
        }
        const target =
          stop.kind === "read"
            ? { x: (stop.rest?.x ?? 0.6) * W, y: (stop.rest?.y ?? 0.5) * H }
            : { x: r.left + r.width / 2, y: Math.max(40, Math.min(H - 40, r.top + Math.min(r.height / 2, 120))) };
        const move = (prev && model.moves[`${stopKey(prev)}>${stopKey(stop)}`]) || model.fallback;
        await moveTo(target, move);
        onStop?.(stop, i);
        await sleep(played(stop.dwell));
        if (stop.click) {
          click();
          await sleep(500);
        }
        prev = stop;
      }
      // Instead of reloading, the next loop scrolls back up and carries on from where the cursor is.
      await sleep(2000);
      await load(model.path[0]?.route ?? "/");
      await scrollTo(0, 1400);
    }
  };

  play().catch(() => {});

  return () => {
    alive = false;
    resize.disconnect();
    seen.disconnect();
    stage.remove();
  };
}
