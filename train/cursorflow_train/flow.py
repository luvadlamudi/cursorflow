"""Where people go: stops, a Markov chain over them, and a typical visit sampled from it."""

from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from .curves import average, normalize
from .sessions import Session, segments

# Passing over an element on the way somewhere else isn't a stop; resting on it or clicking it is.
MIN_HOVER_MS = 250
# Time on a section without pointing at anything counts as reading once it lasts this long.
MIN_READ_MS = 1500
# Walks sampled from the chain when looking for the typical visit.
WALKS = 4000
MAX_STOPS = 24
START, END = "<start>", "<end>"


@dataclass
class Visit:
    kind: str  # "hover" or "read"
    route: str
    anchor: str
    enter: float
    leave: float
    clicked: bool = False
    scroll: float = 0.0
    rest: tuple[float, float] | None = None

    @property
    def key(self) -> str:
        return f"{self.route}#{'@' if self.kind == 'read' else ''}{self.anchor}"


def hovers_and_sections(s: Session):
    """Hover stops, plus the spans of time each section spent in the middle of the screen."""
    hovers, spans = [], []
    route, scroll, open_, section = "/", 0.0, None, None
    scrolls = [(0.0, 0.0)]
    # The section someone was on when they went idle, picked up again when they're back.
    paused = None

    def close_section(t):
        nonlocal section
        if section and t > section[2]:
            spans.append((*section, t))
        section = None

    for e in s.events:
        kind, t = e[0], e[1]
        if paused and kind != "i":
            section, paused = (route, paused, t), None
        if kind == "i":
            # Everything open stops when the visitor was last actually there, not when the idle
            # timer noticed.
            last = t - e[2]
            if open_ and last - open_.enter >= MIN_HOVER_MS:
                open_.leave = last
                hovers.append(open_)
            open_ = None
            if section:
                paused = section[1]
            close_section(last)
        elif kind == "r":
            paused = None
            close_section(t)
            route = e[2]
        elif kind == "v":
            close_section(t)
            section = (route, e[2], t)
        elif kind == "s":
            scroll = e[2] / e[3] if e[3] > 0 else 0.0
            scrolls.append((t, scroll))
        elif kind == "e":
            open_ = Visit("hover", route, e[2], t, t, scroll=scroll)
        elif kind == "c" and open_:
            open_.clicked = True
        elif kind == "l" and open_ and open_.anchor == e[2]:
            open_.leave = t
            if open_.leave - open_.enter >= MIN_HOVER_MS or open_.clicked:
                hovers.append(open_)
            open_ = None
    if s.events:
        close_section(s.events[-1][1])
    return hovers, spans, scrolls


def reads(s: Session, hovers: list[Visit], spans, scrolls) -> list[Visit]:
    """Stretches of a section's time on screen that no hover stop accounts for."""
    out = []
    t_scroll = np.array([t for t, _ in scrolls])
    moves = s.moves
    for route, section, start, end in spans:
        busy = sorted((h.enter, h.leave) for h in hovers if h.leave > start and h.enter < end)
        cursor = start
        for lo, hi in busy + [(end, end)]:
            if lo - cursor >= MIN_READ_MS:
                mid = (cursor + lo) / 2
                scroll = scrolls[max(0, np.searchsorted(t_scroll, mid, side="right") - 1)][1]
                inside = moves[(moves[:, 0] >= cursor) & (moves[:, 0] <= lo)] if len(moves) else moves
                before = moves[moves[:, 0] <= cursor] if len(moves) else moves
                xy = np.median(inside[:, 1:], axis=0) if len(inside) else before[-1, 1:] if len(before) else None
                rest = None if xy is None else (float(xy[0] / s.vw), float(xy[1] / s.vh))
                out.append(Visit("read", route, section, cursor, lo, scroll=scroll, rest=rest))
            cursor = max(cursor, hi)
    return out


def visits(s: Session) -> list[Visit]:
    """Every stop in the session, in order, with back-to-back repeats of the same stop merged."""
    hovers, spans, scrolls = hovers_and_sections(s)
    out: list[Visit] = []
    for v in sorted(hovers + reads(s, hovers, spans, scrolls), key=lambda v: v.enter):
        if out and out[-1].key == v.key:
            out[-1].leave, out[-1].clicked = v.leave, out[-1].clicked or v.clicked
        else:
            out.append(v)
    return out


def transition_moves(s: Session, a: Visit, b: Visit) -> np.ndarray | None:
    """The cursor's last motion into `b`: from where it was resting to where it settles.

    The motion is found from `b` outward rather than from `a`'s end, since people often start
    moving before they leave one thing, and only head for their reading spot after a section
    has already scrolled in.
    """
    for seg in segments(s):
        t = seg[:, 0]
        if len(t) < 4 or t[-1] < b.enter or t[0] > b.leave:
            continue
        speed = np.r_[0, np.hypot(*np.diff(seg[:, 1:], axis=0).T) / np.maximum(np.diff(t), 1)]
        hi = min(int(np.searchsorted(t, b.enter)), len(t) - 1)
        # Back to where the motion began: the cursor only reports samples while it moves, so a
        # pause shows up as a gap in time.
        lo = hi
        while lo > 0 and t[lo] - t[lo - 1] < 250 and t[lo - 1] >= a.enter:
            lo -= 1
        # Forward until the cursor slows down inside the target, so the overshoot and settle are
        # part of the move.
        while hi < len(t) - 1 and t[hi + 1] - t[hi] < 250 and t[hi] < b.leave and speed[hi + 1] >= 0.15:
            hi += 1
        part = seg[lo : hi + 1]
        if len(part) >= 4 and 150 <= part[-1, 0] - part[0, 0] <= 4000:
            return part
    return None


def first_positions(s: Session) -> list[tuple[float, float]]:
    """Where the cursor is first seen after each page load, as a share of the viewport."""
    out, waiting = [], False
    for e in s.events:
        if e[0] == "r":
            waiting = True
        elif e[0] == "m" and waiting:
            out.append((e[2] / s.vw, e[3] / s.vh))
            waiting = False
    return out


def typical_visit(sequences: list[list[str]], transitions: dict[str, Counter], support: int) -> list[str]:
    """The most likely visit of a typical length.

    Walks are sampled from the chain, revisits allowed, and only those about as long as a real
    visit (the median number of stops) are kept. Of those, the one with the highest average log
    probability per step wins: a visit that's both a normal length and made of common moves. Picking
    the single most likely next stop every time would instead loop through the same few places.
    """
    rng = np.random.default_rng(0)
    target = int(np.clip(np.median([len(s) for s in sequences]), 3, 14))
    tolerance = max(1, round(target * 0.25))

    options = {}
    for state, nexts in transitions.items():
        keys = [k for k, n in nexts.items() if n >= support or k == END]
        counts = np.array([nexts[k] for k in keys], dtype=float)
        if keys:
            options[state] = (keys, counts / counts.sum())

    best, best_score = [], -np.inf
    for _ in range(WALKS):
        walk, at, logp = [], START, 0.0
        while at in options and len(walk) < MAX_STOPS:
            keys, p = options[at]
            i = rng.choice(len(keys), p=p)
            logp += np.log(p[i])
            if keys[i] == END:
                break
            walk.append(keys[i])
            at = keys[i]
        if abs(len(walk) - target) > tolerance:
            continue
        score = logp / (len(walk) + 1)
        if score > best_score:
            best, best_score = walk, score
    return best


def train(sessions: list[Session]) -> dict:
    per_session = [visits(s) for s in sessions]

    # First-order Markov chain: how often each stop leads to each other stop, or to leaving.
    transitions: dict[str, Counter] = defaultdict(Counter)
    stats: dict[str, list[Visit]] = defaultdict(list)
    sequences = []
    for vs in per_session:
        if not vs:
            continue
        keys = [v.key for v in vs]
        sequences.append(keys)
        for a, b in zip([START] + keys, keys + [END]):
            transitions[a][b] += 1
        for v in vs:
            stats[v.key].append(v)

    support = max(2, round(0.02 * len(sessions)))
    walk = typical_visit(sequences, transitions, support) if sequences else []

    def summarize_stop(key: str) -> dict:
        vs = stats[key]
        stop = {
            "kind": vs[0].kind,
            "route": vs[0].route,
            "anchor": vs[0].anchor,
            "dwell": int(np.median([v.leave - v.enter for v in vs])),
            "click": sum(v.clicked for v in vs) > len(vs) / 2,
            "scroll": round(float(np.median([v.scroll for v in vs])), 4),
            "visits": len(vs),
        }
        rests = [v.rest for v in vs if v.rest]
        if vs[0].kind == "read" and rests:
            x, y = np.median(rests, axis=0)
            stop["rest"] = {"x": round(float(x), 4), "y": round(float(y), 4)}
        return stop

    path = [summarize_stop(k) for k in walk]

    # Where visits end: of the visits that reached each stop, the share that left right after it.
    # A high rate on something that isn't meant to be the end (a pricing table, a signup button) is
    # where people get stuck or lose interest.
    exits = []
    for key, nexts in transitions.items():
        total = sum(nexts.values())
        if key == START or total < max(support, 5):
            continue
        v = stats[key][0]
        exits.append({"kind": v.kind, "route": v.route, "anchor": v.anchor, "rate": round(nexts[END] / total, 3), "visits": total})
    exits = sorted(exits, key=lambda x: -x["rate"])[:5]

    # Learned curves for each step of the path, plus one average of every move for the rest.
    wanted = {f"{a}>{b}" for a, b in zip(walk, walk[1:])}
    by_pair: dict[str, list] = defaultdict(list)
    everything = []
    for s, vs in zip(sessions, per_session):
        for a, b in zip(vs, vs[1:]):
            part = transition_moves(s, a, b)
            curve = normalize(part) if part is not None else None
            if curve is None:
                continue
            item = (curve, part[-1, 0] - part[0, 0])
            everything.append(item)
            if f"{a.key}>{b.key}" in wanted:
                by_pair[f"{a.key}>{b.key}"].append(item)

    def summarize_move(items):
        curve, used = average([c for c, _ in items])
        return {
            "ms": int(np.median([ms for _, ms in items])),
            "curve": [[round(z.real, 4), round(z.imag, 4)] for z in curve],
            "samples": used,
        }

    moves = {k: summarize_move(v) for k, v in by_pair.items() if len(v) >= 3}
    fallback = summarize_move(everything) if len(everything) >= 3 else {
        "ms": 600,
        "curve": [[x, 0.0] for x in np.linspace(0, 1, 24).round(4).tolist()],
        "samples": 0,
    }

    starts = [p for s in sessions for p in first_positions(s)]
    start = np.median(starts, axis=0) if starts else np.array([0.5, 0.5])

    return {
        "start": {"x": round(float(start[0]), 4), "y": round(float(start[1]), 4)},
        "path": path,
        "moves": moves,
        "fallback": fallback,
        "exits": exits,
    }
