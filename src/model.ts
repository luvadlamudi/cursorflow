// What the tracker sends. Each event is a short array to keep batches small; times are ms since
// the session started, positions are viewport pixels.
export type Event =
  | ["m", number, number, number] // move: t, x, y
  | ["s", number, number, number] // scroll: t, scrollY, max scrollY
  | ["e", number, string, number, number, number, number] // enter an anchor: t, id, x, y, w, h
  | ["l", number, string] // leave an anchor: t, id
  | ["c", number, number, number] // click: t, x, y
  | ["r", number, string] // route: t, pathname
  | ["v", number, string] // section now in the middle of the screen: t, id
  | ["w", number, string, number, number] // visible part of a section, as shares of its height: t, id, from, to
  | ["i", number, number]; // went idle (no input, or the tab was hidden): t, ms since the last input

export type Batch = {
  sid: string;
  vw: number;
  vh: number;
  events: Event[];
};

// One stop on a typical visit. A "hover" stop is resting on or clicking a tagged element; a "read"
// stop is time spent on a section without pointing at anything, which is usually reading.
export type Stop = {
  kind: "hover" | "read";
  route: string;
  // The element's data-flow name, or for a read stop, the section's data-flow-section name.
  anchor: string;
  // The section it's in.
  section: string;
  // How long people usually rest here, in ms.
  dwell: number;
  // Whether most visits end in a click.
  click: boolean;
  // For read stops: where the cursor usually rests, as a share of the viewport.
  rest?: { x: number; y: number };
  visits: number;
};

// The usual shape of a move between two anchors, drawn in a frame where the start is (0, 0) and
// the target is (1, 0). Points are evenly spaced in time, so their spacing carries the speed.
export type Move = {
  ms: number;
  curve: [number, number][];
  samples: number;
};

// Time spent on a section, and where within it. The section is cut into equal bands from top to
// bottom, and each band gets the time it was on screen. Bands are shares of the section, not
// pixels, so visits at any window size or zoom level add up in the same places.
export type Attention = {
  route: string;
  section: string;
  // Average seconds on screen per visit that reached it.
  seconds: number;
  // Each band's time relative to the busiest band (1).
  bands: number[];
};

export type Pattern = {
  // Share of visits that went this way.
  share: number;
  stops: Stop[];
};

// Of the visits that reached a section, the share that ended there.
export type Exit = { route: string; section: string; rate: number; visits: number };

export type FlowModel = {
  version: 1;
  // Set on models trained from simulated visits (examples/simulate.py) rather than real ones.
  sample?: boolean;
  trainedAt: string;
  since: string;
  sessions: { total: number; kept: number };
  points: number;
  // Where the cursor usually is when a page loads, as a share of the viewport.
  start: { x: number; y: number };
  // The common ways through the site, most common first. Each is a real visit, the one most like
  // the others in its group, with each stop's median time across everyone.
  patterns: Pattern[];
  // Keyed by stopKey pairs: "route#anchor>route#anchor", with "@" before a section name.
  moves: Record<string, Move>;
  // The average of every move, for pairs without enough data of their own.
  fallback: Move;
  // Sections in the order people usually reach them.
  attention: Attention[];
  // Highest exit rate first.
  exits: Exit[];
};

export const stopKey = (s: Pick<Stop, "kind" | "route" | "anchor">) => `${s.route}#${s.kind === "read" ? "@" : ""}${s.anchor}`;
