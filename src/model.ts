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
  // How long people usually rest here, in ms.
  dwell: number;
  // Whether most visits end in a click.
  click: boolean;
  // Where the page is usually scrolled to, as a share of the scrollable height.
  scroll: number;
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
  // A typical visit, sampled from the model (see the trainer). Stops can repeat, the way people
  // scroll back up to something.
  path: Stop[];
  // Keyed by stopKey pairs: "route#anchor>route#anchor", with "@" before a section name.
  moves: Record<string, Move>;
  // The average of every move, for pairs without enough data of their own.
  fallback: Move;
  // The stops people most often leave the site from, highest exit rate first.
  exits: Exit[];
};

// Of the visits that reached a stop, how many ended right after it.
export type Exit = Pick<Stop, "kind" | "route" | "anchor"> & { rate: number; visits: number };

export const stopKey = (s: Pick<Stop, "kind" | "route" | "anchor">) => `${s.route}#${s.kind === "read" ? "@" : ""}${s.anchor}`;
