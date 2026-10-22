"""Simulated visitors, for trying cursorflow before a site has real traffic.

Writes batches in the same format the collector stores locally, so the trainer can run on them:

    python examples/simulate.py 200 > .cursorflow/batches.jsonl
    cursorflow-train

Every number below is made up. The point is to exercise the whole pipeline (cleaning, stops,
patterns, attention, exits, the curves, the replay) with something that behaves roughly like people
do, not to stand in for real data. Edit PAGES and visit() to match your own site.
"""

import json
import sys
import uuid
from datetime import datetime, timezone

import numpy as np

rng = np.random.default_rng(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
VW, VH = 1440, 900

# Each page ("/work/" covers every project page): its height, its sections (name, top, height,
# fixed to the screen?) in page order, and where its tagged elements sit (x, y, width, height, in
# page coordinates, or screen coordinates for elements inside a fixed section).
PAGES = {
    "/": {
        "height": 4300,
        "sections": [("landing", 0, VH, True), ("projects", 900, 1600, False), ("about", 2500, 1100, False), ("contact", 3600, 700, False)],
        "elements": {
            "ask-claude": (1040, 750, 80, 30, True),
            "ask-chatgpt": (1200, 750, 90, 30, True),
            "project-aria": (510, 1150, 420, 330, False),
            "project-kaizen": (960, 1300, 420, 300, False),
            "project-cursorflow": (960, 1700, 420, 300, False),
            "project-thinktank": (60, 1250, 420, 300, False),
            "more-work": (600, 2350, 220, 48, False),
            "about-portrait": (60, 2700, 420, 520, False),
            "about-bio": (620, 2650, 700, 380, False),
            "social-github": (300, 3260, 80, 20, False),
            "social-linkedin": (120, 3260, 90, 20, False),
            "contact-email": (60, 3950, 520, 40, False),
        },
    },
    "/work/": {
        "height": 3600,
        "sections": [("project", 100, 700, False), ("writeup", 1500, 1800, False)],
        "elements": {"back": (24, 110, 160, 40, False), "next-project": (24, 3400, 900, 120, False)},
    },
}
PROJECTS = {"project-aria": 0.35, "project-kaizen": 0.28, "project-thinktank": 0.2, "project-cursorflow": 0.17}


def motion(a: complex, b: complex, t0: float, ms: float) -> list:
    """A human-ish move: minimum-jerk speed profile, a slight bow, a small overshoot, some jitter."""
    n = max(4, int(ms / 66))
    s = np.linspace(0, 1, n)
    p = 10 * s**3 - 15 * s**4 + 6 * s**5 + 0.07 * np.sin(np.pi * s) ** 2 * (s > 0.55)
    d = b - a
    z = a + p * d + 1j * d * rng.normal(0.1, 0.05) * np.sin(np.pi * s)
    z += rng.normal(0, 2.5, n) + 1j * rng.normal(0, 2.5, n)
    return [["m", int(t0 + i * ms / (n - 1)), round(z[i].real), round(z[i].imag)] for i in range(n)]


def subtract(pieces, cut):
    a, b = cut
    out = []
    for x, y in pieces:
        out += [p for p in ((x, min(y, a)), (max(x, b), y)) if p[1] - p[0] >= 1]
    return out


class Visitor:
    def __init__(self):
        self.events, self.t = [], 0.0
        self.windows, self.center = {}, None
        self.pos = complex(VW * rng.uniform(0.4, 0.7), VH * rng.uniform(0.35, 0.6))
        self.go("/", 0)
        self.t = 400.0
        self.add(["m", self.t, round(self.pos.real), round(self.pos.imag)])

    def add(self, e):
        e[1] = int(e[1])
        self.events.append(e)

    def page(self):
        return PAGES["/" if self.route == "/" else "/work/"]

    def go(self, route: str, y: float):
        self.route, self.y, self.windows, self.center = route, None, {}, None
        self.add(["r", self.t, route])
        self.set_scroll(y)

    def set_scroll(self, y: float):
        page = self.page()
        self.y = float(np.clip(y, 0, page["height"] - VH))
        self.add(["s", self.t, int(self.y), page["height"] - VH])
        # Same rule as the tracker: later sections cover earlier ones.
        seen, covered, center = {}, [], None
        for name, top, h, fixed in reversed(page["sections"]):
            vt = top if fixed else top - self.y
            a, b = max(0, vt), min(VH, vt + h)
            if b - a < 1:
                continue
            pieces = [(a, b)]
            for c in covered:
                pieces = subtract(pieces, c)
            covered.append((a, b))
            if not pieces:
                continue
            lo, hi = min(p[0] for p in pieces), max(p[1] for p in pieces)
            seen[name] = (round((lo - vt) / h, 2), round((hi - vt) / h, 2))
            if center is None and any(p[0] <= VH / 2 <= p[1] for p in pieces):
                center = name
        for name, w in seen.items():
            if self.windows.get(name) != w:
                self.add(["w", self.t, name, *w])
        for name in self.windows:
            if name not in seen:
                self.add(["w", self.t, name, 0, 0])
        self.windows = seen
        if center and center != self.center:
            self.center = center
            self.add(["v", self.t, center])

    def scroll(self, y: float, ms: float = 700):
        start = self.y
        for k in range(1, 8):
            self.t += ms / 7
            e = 1 - (1 - k / 7) ** 3
            self.set_scroll(start + (y - start) * e)

    def move(self, target: complex):
        ms = rng.uniform(450, 950)
        self.events.extend(motion(self.pos, target, self.t, ms))
        self.t += ms
        self.pos = target

    def hover(self, name: str, dwell: float, click: bool = False):
        x, y, w, h, fixed = self.page()["elements"][name]
        if not fixed:
            # Bring it comfortably on screen first, the way people scroll to what they're reading.
            if y - self.y < VH * 0.1 or y + min(h, 300) - self.y > VH * 0.9:
                self.scroll(y - VH * 0.35)
            y -= self.y
        self.move(complex(x + w / 2, y + min(h / 2, 120)) + complex(*rng.normal(0, 12, 2)))
        self.add(["e", self.t - 100, name, x, int(y), w, h])
        if click:
            self.add(["c", self.t + dwell * 0.7, round(self.pos.real), round(self.pos.imag)])
        self.t += dwell
        self.add(["l", self.t, name])

    def read(self, ms: float, scroll_by: float = 0):
        """Reading: the cursor drifts somewhere out of the way while the page scrolls slowly."""
        self.move(complex(VW * rng.uniform(0.55, 0.8), VH * rng.uniform(0.4, 0.6)))
        steps = max(1, int(ms / 1500))
        for _ in range(steps):
            self.t += ms / steps
            if scroll_by:
                self.scroll(self.y + scroll_by / steps, 400)
        self.add(["m", self.t, round(self.pos.real), round(self.pos.imag)])

    def section_top(self, name: str) -> float:
        return next(top for n, top, _, _ in self.page()["sections"] if n == name)

    def idle(self, ms: float):
        self.t += ms
        self.add(["i", self.t, int(ms)])


def pick_projects(k: int) -> list[str]:
    names = list(PROJECTS)
    return list(rng.choice(names, size=k, replace=False, p=np.array(list(PROJECTS.values()))))


def visit() -> list:
    v = Visitor()
    if rng.random() < 0.5:
        v.hover("ask-claude" if rng.random() < 0.7 else "ask-chatgpt", rng.uniform(900, 2400))

    v.scroll(v.section_top("projects") - 60)
    v.read(rng.uniform(1600, 3200))
    for name in pick_projects(int(rng.integers(2, 5))):
        v.hover(name, rng.uniform(1300, 4200))
    if rng.random() < 0.3:
        v.hover("more-work", rng.uniform(300, 700), click=True)

    # Some open a project, read it, and come back.
    if rng.random() < 0.3:
        name = pick_projects(1)[0]
        v.hover(name, rng.uniform(600, 1500), click=True)
        v.go(f"/work/{name.removeprefix('project-')}", 0)
        v.read(rng.uniform(3000, 6000))
        v.scroll(v.section_top("writeup") - 100)
        v.read(rng.uniform(8000, 22000), scroll_by=1200)
        if rng.random() < 0.15:
            return v.events
        v.go("/", PAGES["/"]["sections"][1][1])

    # And some have seen enough once they've seen the work.
    if rng.random() < 0.15:
        return v.events

    # About is taller than the screen: people start with it in full view, then read down.
    v.scroll(v.section_top("about") - 40)
    if rng.random() < 0.45:
        v.hover("about-portrait", rng.uniform(1000, 3000))
    v.read(rng.uniform(4000, 9000))
    if rng.random() < 0.5:
        v.hover("about-bio", rng.uniform(2000, 5000))
    v.scroll(v.y + 260)
    v.read(rng.uniform(2500, 7000))
    if rng.random() < 0.1:
        v.idle(30000)
        return v.events
    if rng.random() < 0.35:
        v.hover("social-github" if rng.random() < 0.6 else "social-linkedin", rng.uniform(500, 1400))

    v.scroll(PAGES["/"]["height"])
    v.read(rng.uniform(1600, 4000))
    if rng.random() < 0.4:
        v.hover("contact-email", rng.uniform(600, 1500))

    # A few go back up for one more look at a project.
    if rng.random() < 0.2:
        v.hover(pick_projects(1)[0], rng.uniform(1500, 3000))
    return sorted(v.events, key=lambda e: e[1])


if __name__ == "__main__":
    now = datetime.now(timezone.utc).isoformat()
    for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 200):
        print(json.dumps({"sid": str(uuid.uuid4()), "vw": VW, "vh": VH, "events": visit(), "at": now}))
