"""Where people go: stops, the common ways through the site, attention and exits."""

from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from .curves import average, normalize
from .sessions import Session, segments

# Passing over an element on the way somewhere else isn't a stop; resting on it or clicking it is.
MIN_HOVER_MS = 250
# Time on a section without pointing at anything counts as reading once it lasts this long.
MIN_READ_MS = 1500
# How many common ways through the site to keep.
PATTERNS = 3
# Each section is cut into this many equal bands, top to bottom, for attention.
BANDS = 10


@dataclass
class Visit:
    kind: str  # "hover" or "read"
    route: str
    anchor: str
    section: str
    enter: float
    leave: float
    clicked: bool = False
    rest: tuple[float, float] | None = None

    @property
    def key(self) -> str:
        return f"{self.route}#{'@' if self.kind == 'read' else ''}{self.anchor}"


def hovers_and_sections(s: Session):
    """Hover stops, plus the spans of time each section spent in the middle of the screen."""
    hovers, spans = [], []
    route, open_, section = "/", None, None
    current = ""
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
            route, current = e[2], ""
        elif kind == "v":
            close_section(t)
            section, current = (route, e[2], t), e[2]
        elif kind == "e":
            open_ = Visit("hover", route, e[2], current, t, t)
        elif kind == "c" and open_:
            open_.clicked = True
        elif kind == "l" and open_ and open_.anchor == e[2]:
            open_.leave = t
            if open_.leave - open_.enter >= MIN_HOVER_MS or open_.clicked:
                hovers.append(open_)
            open_ = None
    if s.events:
        close_section(s.events[-1][1])
    return hovers, spans


def reads(s: Session, hovers: list[Visit], spans) -> list[Visit]:
    """Stretches of a section's time on screen that no hover stop accounts for."""
    out = []
    moves = s.moves
    for route, section, start, end in spans:
        busy = sorted((h.enter, h.leave) for h in hovers if h.leave > start and h.enter < end)
        cursor = start
        for lo, hi in busy + [(end, end)]:
            if lo - cursor >= MIN_READ_MS:
                inside = moves[(moves[:, 0] >= cursor) & (moves[:, 0] <= lo)] if len(moves) else moves
                before = moves[moves[:, 0] <= cursor] if len(moves) else moves
                xy = np.median(inside[:, 1:], axis=0) if len(inside) else before[-1, 1:] if len(before) else None
                rest = None if xy is None else (float(xy[0] / s.vw), float(xy[1] / s.vh))
                out.append(Visit("read", route, section, section, cursor, lo, rest=rest))
            cursor = max(cursor, hi)
    return out


def visits(s: Session) -> list[Visit]:
    """Every stop in the session, in order, with back-to-back repeats of the same stop merged."""
    hovers, spans = hovers_and_sections(s)
    out: list[Visit] = []
    for v in sorted(hovers + reads(s, hovers, spans), key=lambda v: v.enter):
        if out and out[-1].key == v.key:
            out[-1].leave, out[-1].clicked = v.leave, out[-1].clicked or v.clicked
        else:
            out.append(v)
    return out


def attention(s: Session) -> tuple[dict, dict]:
    """Milliseconds each band of each section spent on screen, and when each section was first seen.

    Time only counts while the visitor is there: from an idle event back to their next input,
    nothing accrues, and the stretch before an idle event is cut back to their last real input.
    """
    edges = np.linspace(0, 1, BANDS + 1)
    totals: dict[tuple, np.ndarray] = defaultdict(lambda: np.zeros(BANDS))
    first: dict[tuple, float] = {}
    windows: dict[tuple, tuple[float, float]] = {}
    route, last, present = "/", None, True
    for e in s.events:
        kind, t = e[0], e[1]
        if last is not None and present:
            dt = t - last - (e[2] if kind == "i" else 0)
            if dt > 0:
                for key, (a, b) in windows.items():
                    overlap = np.clip(np.minimum(edges[1:], b) - np.maximum(edges[:-1], a), 0, None) * BANDS
                    totals[key] += dt * overlap
        last = t
        present = kind != "i"
        if kind == "r":
            route = e[2]
            windows.clear()
        elif kind == "w":
            key = (route, e[2])
            if e[4] > e[3]:
                windows[key] = (e[3], e[4])
                first.setdefault(key, t)
            else:
                windows.pop(key, None)
    return totals, first


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


def edit_distance(a: list[str], b: list[str]) -> int:
    d = np.arange(len(b) + 1)
    for i, x in enumerate(a, 1):
        prev, d[0] = d.copy(), i
        for j, y in enumerate(b, 1):
            d[j] = min(prev[j] + 1, d[j - 1] + 1, prev[j - 1] + (x != y))
    return int(d[-1])


def patterns(sequences: list[list[str]], k: int = PATTERNS, limit: int = 400) -> list[tuple[list[str], float]]:
    """The common ways through the site, each as a real visit, with the share of visits like it.

    Visits are grouped by how alike their stop sequences are (edit distance, scaled by length so
    long visits don't dominate) using k-medoids, and each group is represented by its medoid: the
    actual visit closest to everyone else in it. Building a path from the single most likely next
    stop at each step would favor whatever choice is least spread out (one project page everyone
    can click into beats four cards people pick between), so it can describe a visit nobody made.
    """
    rng = np.random.default_rng(0)
    if len(sequences) > limit:
        sequences = [sequences[i] for i in rng.choice(len(sequences), limit, replace=False)]
    n = len(sequences)
    dist = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            dist[i, j] = dist[j, i] = edit_distance(sequences[i], sequences[j]) / max(len(sequences[i]), len(sequences[j]))

    # k-medoids: start from the most central visit, add the ones farthest from those picked, then
    # alternate assigning visits to their nearest medoid and moving each medoid to its group's center.
    k = min(k, n)
    medoids = [int(np.argmin(dist.sum(axis=1)))]
    while len(medoids) < k:
        medoids.append(int(np.argmax(dist[:, medoids].min(axis=1))))
    for _ in range(20):
        groups = np.argmin(dist[:, medoids], axis=1)
        updated = []
        for g in range(k):
            members = np.where(groups == g)[0]
            updated.append(int(members[np.argmin(dist[np.ix_(members, members)].sum(axis=1))]) if len(members) else medoids[g])
        if updated == medoids:
            break
        medoids = updated
    groups = np.argmin(dist[:, medoids], axis=1)
    found = [(sequences[m], float(np.mean(groups == g))) for g, m in enumerate(medoids)]
    return sorted(found, key=lambda x: -x[1])


def train(sessions: list[Session]) -> dict:
    per_session = [visits(s) for s in sessions]

    stats: dict[str, list[Visit]] = defaultdict(list)
    sequences = []
    for vs in per_session:
        if not vs:
            continue
        sequences.append([v.key for v in vs])
        for v in vs:
            stats[v.key].append(v)

    support = max(2, round(0.02 * len(sessions)))
    found = patterns(sequences) if sequences else []
    walks = [walk for walk, _ in found]

    def summarize_stop(key: str) -> dict:
        vs = stats[key]
        stop = {
            "kind": vs[0].kind,
            "route": vs[0].route,
            "anchor": vs[0].anchor,
            "section": Counter(v.section for v in vs).most_common(1)[0][0],
            "dwell": int(np.median([v.leave - v.enter for v in vs])),
            "click": sum(v.clicked for v in vs) > len(vs) / 2,
            "visits": len(vs),
        }
        rests = [v.rest for v in vs if v.rest]
        if vs[0].kind == "read" and rests:
            x, y = np.median(rests, axis=0)
            stop["rest"] = {"x": round(float(x), 4), "y": round(float(y), 4)}
        return stop

    # Each stop shows the median over every visit that made it, not just the medoid's own timing.
    paths = [{"share": round(share, 3), "stops": [summarize_stop(k) for k in walk]} for walk, share in found]

    # Attention: time per band of each section, summed over visits, plus the average time a visit
    # spends on the section, and the order sections are usually reached in.
    band_ms: dict[tuple, np.ndarray] = defaultdict(lambda: np.zeros(BANDS))
    reached: Counter = Counter()
    first_seen: dict[tuple, list] = defaultdict(list)
    for s in sessions:
        totals, first = attention(s)
        for key, ms in totals.items():
            band_ms[key] += ms
        for key, t in first.items():
            reached[key] += 1
            first_seen[key].append(t)
    sections = []
    for key, ms in band_ms.items():
        if reached[key] < support or ms.max() <= 0:
            continue
        sections.append({
            "route": key[0],
            "section": key[1],
            # Each band holds the time it was on screen; the section's time is its best-seen band.
            "seconds": round(float(ms.max()) / reached[key] / 1000, 1),
            "bands": [round(float(x), 3) for x in ms / ms.max()],
            "_order": float(np.median(first_seen[key])),
        })
    sections.sort(key=lambda x: (x["route"] != "/", x["route"], x["_order"]))
    for x in sections:
        del x["_order"]

    # Where visits end, by section: of the visits that reached a section, the share whose last stop
    # was there. A high rate on a section that isn't meant to be the end is where people get stuck
    # or lose interest.
    ended: Counter = Counter()
    seen_in: Counter = Counter()
    for vs in per_session:
        if not vs:
            continue
        for key in {(v.route, v.section) for v in vs if v.section}:
            seen_in[key] += 1
        if vs[-1].section:
            ended[(vs[-1].route, vs[-1].section)] += 1
    exits = [
        {"route": r, "section": sec, "rate": round(ended[(r, sec)] / n, 3), "visits": n}
        for (r, sec), n in seen_in.items()
        if n >= max(support, 5)
    ]
    exits.sort(key=lambda x: -x["rate"])

    # Learned curves for each step of every pattern, plus one average of every move for the rest.
    wanted = {f"{a}>{b}" for walk in walks for a, b in zip(walk, walk[1:])}
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
        "patterns": paths,
        "moves": moves,
        "fallback": fallback,
        "attention": sections,
        "exits": exits,
    }
