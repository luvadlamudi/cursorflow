"""Turning raw batches into sessions, and throwing out the ones that don't look like people."""

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from sklearn.ensemble import IsolationForest

MIN_DURATION_MS = 3000
MIN_MOVES = 20
# Faster than this between two samples means the cursor left the window and came back somewhere else.
TELEPORT_PX_PER_MS = 10
# Someone who comes back to a tab after this long is starting a new visit, not continuing the old one.
NEW_VISIT_GAP_MS = 30 * 60 * 1000


@dataclass
class Session:
    sid: str
    vw: int
    vh: int
    events: list
    moves: np.ndarray = field(init=False)  # (n, 3): t, x, y

    def __post_init__(self):
        m = [e[1:4] for e in self.events if e[0] == "m"]
        self.moves = np.array(m, dtype=float).reshape(-1, 3)

    @property
    def duration(self) -> float:
        return self.events[-1][1] - self.events[0][1] if self.events else 0

    def count(self, kind: str) -> int:
        return sum(1 for e in self.events if e[0] == kind)


def build_sessions(batches: list[dict]) -> list[Session]:
    grouped = defaultdict(list)
    for b in batches:
        grouped[b["sid"]].append(b)
    sessions = []
    for sid, parts in grouped.items():
        # Batches can arrive out of order (a beacon on page hide can beat the timer's), so events are
        # sorted by their own clock rather than by arrival.
        events = sorted((e for p in parts for e in p["events"]), key=lambda e: e[1])
        visit = []
        for e in events:
            if visit and e[1] - visit[-1][1] > NEW_VISIT_GAP_MS:
                sessions.append(Session(sid, parts[-1]["vw"], parts[-1]["vh"], visit))
                visit = []
            visit.append(e)
        if visit:
            sessions.append(Session(sid, parts[-1]["vw"], parts[-1]["vh"], visit))
    return sessions


def features(s: Session) -> list[float]:
    """Per-session numbers the outlier model looks at."""
    t, xy = s.moves[:, 0], s.moves[:, 1:]
    steps = np.diff(xy, axis=0)
    dt = np.maximum(np.diff(t), 1)
    diag = np.hypot(s.vw, s.vh)
    speed = np.hypot(*steps.T) / dt / diag * 1000
    angles = np.arctan2(steps[:, 1], steps[:, 0])
    turning = np.abs(np.angle(np.exp(1j * np.diff(angles)))) if len(angles) > 1 else np.zeros(1)
    minutes = max(s.duration / 60000, 1 / 60)
    return [
        np.log(s.duration),
        float(np.mean(speed)),
        float(np.std(speed) / (np.mean(speed) + 1e-9)),
        float(np.mean(turning)),
        s.count("c") / minutes,
        s.count("e") / minutes,
        s.count("s") / minutes,
    ]


def clean(sessions: list[Session]) -> tuple[list[Session], dict]:
    """Drops sessions that are too short, look scripted, or sit far from everyone else."""
    report = {"total": len(sessions), "short": 0, "scripted": 0, "outliers": 0}

    kept = []
    for s in sessions:
        if s.duration < MIN_DURATION_MS or len(s.moves) < MIN_MOVES:
            report["short"] += 1
            continue
        # People never move in perfectly straight lines; replayed or scripted cursors often do.
        if features(s)[3] < 0.01:
            report["scripted"] += 1
            continue
        kept.append(s)

    # An isolation forest needs a crowd to compare against before it can call anyone unusual. It
    # trims the strangest 5%; "auto" was cutting closer to a quarter of perfectly normal visits.
    if len(kept) >= 20:
        X = np.array([features(s) for s in kept])
        labels = IsolationForest(contamination=0.05, random_state=0).fit_predict(X)
        report["outliers"] = int((labels == -1).sum())
        kept = [s for s, label in zip(kept, labels) if label == 1]

    report["kept"] = len(kept)
    return kept, report


def segments(s: Session, gap_ms: float = 1000) -> list[np.ndarray]:
    """Splits the session's moves wherever the cursor teleported or went quiet for a while."""
    if len(s.moves) < 2:
        return [s.moves]
    t, xy = s.moves[:, 0], s.moves[:, 1:]
    dt = np.maximum(np.diff(t), 1)
    speed = np.hypot(*np.diff(xy, axis=0).T) / dt
    breaks = np.where((speed > TELEPORT_PX_PER_MS) | (np.diff(t) > gap_ms))[0] + 1
    return np.split(s.moves, breaks)
