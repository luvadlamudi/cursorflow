"""Averaging cursor trajectories.

Each move from one anchor to the next is redrawn in a shared frame (start at 0, target at 1, as
complex numbers) and resampled evenly in time. A plain mean of those curves would flatten every
overshoot and hesitation into a straight line, because people do them at slightly different
moments. DTW Barycenter Averaging (DBA) lines the curves up first, then averages, so the shape survives.
"""

import numpy as np

POINTS = 24


def normalize(moves: np.ndarray) -> np.ndarray | None:
    """(n, 3) samples of t, x, y -> POINTS complex numbers from 0 to 1, evenly spaced in time."""
    t = moves[:, 0]
    z = moves[:, 1] + 1j * moves[:, 2]
    span = z[-1] - z[0]
    if abs(span) < 60 or t[-1] <= t[0]:
        return None
    grid = np.linspace(t[0], t[-1], POINTS)
    resampled = np.interp(grid, t, z.real) + 1j * np.interp(grid, t, z.imag)
    return (resampled - z[0]) / span


def dtw(a: np.ndarray, b: np.ndarray) -> tuple[float, list[tuple[int, int]]]:
    n, m = len(a), len(b)
    cost = np.abs(a[:, None] - b[None, :])
    acc = np.full((n + 1, m + 1), np.inf)
    acc[0, 0] = 0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            acc[i, j] = cost[i - 1, j - 1] + min(acc[i - 1, j], acc[i, j - 1], acc[i - 1, j - 1])
    path, i, j = [], n, m
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        step = np.argmin([acc[i - 1, j - 1], acc[i - 1, j], acc[i, j - 1]])
        i, j = (i - 1, j - 1) if step == 0 else (i - 1, j) if step == 1 else (i, j - 1)
    return float(acc[n, m]), path[::-1]


def dba(curves: list[np.ndarray], start: np.ndarray, iterations: int = 10) -> np.ndarray:
    avg = start.copy()
    for _ in range(iterations):
        sums = np.zeros(len(avg), dtype=complex)
        counts = np.zeros(len(avg))
        for c in curves:
            for i, j in dtw(avg, c)[1]:
                sums[i] += c[j]
                counts[i] += 1
        avg = sums / counts
    return avg


def average(curves: list[np.ndarray], limit: int = 80) -> tuple[np.ndarray, int]:
    """The DBA average of `curves` after dropping outliers. Returns the curve and how many were used."""
    rng = np.random.default_rng(0)
    if len(curves) > limit:
        curves = [curves[i] for i in rng.choice(len(curves), limit, replace=False)]

    # The medoid is the real curve closest to all the others: a robust place to start from, and
    # the reference for deciding which curves are outliers.
    dist = np.array([[dtw(a, b)[0] if i < j else 0 for j, b in enumerate(curves)] for i, a in enumerate(curves)])
    dist = dist + dist.T
    medoid = int(np.argmin(dist.sum(axis=1)))
    d = dist[medoid]
    q1, q3 = np.percentile(d, [25, 75])
    kept = [c for c, x in zip(curves, d) if x <= q3 + 1.5 * (q3 - q1)]

    return dba(kept, curves[medoid]), len(kept)
