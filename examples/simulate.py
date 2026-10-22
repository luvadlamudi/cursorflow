"""Simulated visitors, for trying cursorflow before a site has real traffic.

Writes batches in the same format the collector stores locally, so the trainer can run on them:

    python examples/simulate.py 150 > .cursorflow/batches.jsonl
    cursorflow-train

Every number below is made up. The point is to exercise the whole pipeline (cleaning, stops, the
chain, the curves, the replay) with something that behaves roughly like people do, not to stand in
for real data. Edit SITE and visit() to match the data-flow and data-flow-section names on your site.
"""

import json
import sys
import uuid
from datetime import datetime, timezone

import numpy as np

rng = np.random.default_rng(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
VW, VH, MAX_SCROLL = 1440, 900, 4200

# Where each tagged element sits on screen once its section is scrolled to:
# name -> (section, x, y, width, height, scroll as a share of the page).
SITE = {
    "ask-claude": ("hero", 1040, 750, 80, 30, 0.0),
    "project-cursorflow": ("projects", 60, 220, 420, 300, 0.24),
    "project-aria": ("projects", 510, 220, 420, 300, 0.24),
    "project-kaizen": ("projects", 960, 220, 420, 300, 0.24),
    "more-work": ("projects", 600, 620, 220, 48, 0.36),
    "project-txcr": ("projects", 60, 300, 420, 300, 0.45),
    "about-bio": ("about", 620, 300, 700, 380, 0.62),
    "contact-email": ("contact", 60, 480, 520, 40, 1.0),
}
SECTIONS = {"hero": 0.0, "projects": 0.24, "about": 0.6, "contact": 1.0}


def motion(a: complex, b: complex, t0: float, ms: float) -> list:
    """A human-ish move: minimum-jerk speed profile, a slight bow, a small overshoot, some jitter."""
    n = max(4, int(ms / 66))
    s = np.linspace(0, 1, n)
    p = 10 * s**3 - 15 * s**4 + 6 * s**5 + 0.07 * np.sin(np.pi * s) ** 2 * (s > 0.55)
    d = b - a
    z = a + p * d + 1j * d * rng.normal(0.1, 0.05) * np.sin(np.pi * s)
    z += rng.normal(0, 2.5, n) + 1j * rng.normal(0, 2.5, n)
    return [["m", int(t0 + i * ms / (n - 1)), round(z[i].real), round(z[i].imag)] for i in range(n)]


class Visitor:
    def __init__(self):
        self.events = [["r", 0, "/"], ["s", 0, 0, MAX_SCROLL], ["v", 0, "hero"]]
        self.t = 400.0
        self.pos = complex(VW * rng.uniform(0.4, 0.7), VH * rng.uniform(0.35, 0.6))
        self.section = "hero"
        self.events.append(["m", int(self.t), round(self.pos.real), round(self.pos.imag)])

    def scroll_to(self, name: str):
        if name == self.section:
            return
        start, end = SECTIONS[self.section], SECTIONS[name]
        for k in range(1, 7):
            self.t += 120
            self.events.append(["s", int(self.t), int((start + (end - start) * k / 6) * MAX_SCROLL), MAX_SCROLL])
        self.events.append(["v", int(self.t - 200), name])
        self.section = name

    def move(self, target: complex):
        ms = rng.uniform(450, 950)
        self.events.extend(motion(self.pos, target, self.t, ms))
        self.t += ms
        self.pos = target

    def hover(self, name: str, dwell: float, click: bool = False):
        section, x, y, w, h, scroll = SITE[name]
        self.scroll_to(section)
        self.events.append(["s", int(self.t), int(scroll * MAX_SCROLL), MAX_SCROLL])
        self.move(complex(x + w / 2, y + min(h / 2, 120)) + complex(*rng.normal(0, 12, 2)))
        self.events.append(["e", int(self.t - 100), name, x, y, w, h])
        if click:
            self.events.append(["c", int(self.t + dwell * 0.7), round(self.pos.real), round(self.pos.imag)])
        self.t += dwell
        self.events.append(["l", int(self.t), name])

    def read(self, section: str, ms: float):
        self.scroll_to(section)
        self.move(complex(VW * rng.uniform(0.55, 0.8), VH * rng.uniform(0.4, 0.6)))
        self.t += ms
        self.events.append(["m", int(self.t), round(self.pos.real), round(self.pos.imag)])

    def idle(self, ms: float):
        self.t += ms
        self.events.append(["i", int(self.t), int(ms)])


def visit() -> list:
    v = Visitor()
    if rng.random() < 0.55:
        v.hover("ask-claude", rng.uniform(900, 2200))
    v.read("projects", rng.uniform(1600, 3000))
    for name in rng.choice(["project-cursorflow", "project-aria", "project-kaizen"], size=rng.integers(2, 4), replace=False):
        v.hover(name, rng.uniform(1500, 4000))
    # Some people leave once they've seen the work.
    if rng.random() < 0.2:
        return v.events
    if rng.random() < 0.6:
        v.hover("more-work", rng.uniform(300, 700), click=True)
        v.hover("project-txcr", rng.uniform(1200, 3000))
    v.read("about", rng.uniform(6000, 18000))
    # A few walk away mid-read; the trainer should stop counting when they did.
    if rng.random() < 0.1:
        v.idle(30000)
        return v.events
    if rng.random() < 0.6:
        v.hover("about-bio", rng.uniform(2000, 6000))
    if rng.random() < 0.35:
        v.hover("project-aria", rng.uniform(1500, 3000))
    v.read("contact", rng.uniform(1600, 4000))
    if rng.random() < 0.4:
        v.hover("contact-email", rng.uniform(600, 1500))
    return sorted(v.events, key=lambda e: e[1])


if __name__ == "__main__":
    now = datetime.now(timezone.utc).isoformat()
    for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 150):
        print(json.dumps({"sid": str(uuid.uuid4()), "vw": VW, "vh": VH, "events": visit(), "at": now}))
