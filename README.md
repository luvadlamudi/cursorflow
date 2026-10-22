# cursorflow

Learn how people actually move through your site, then replay a typical visit.

cursorflow records cursor movement, scrolling, hovers, clicks and what's on screen during desktop visits, cleans out the noise, and trains a small model of how people browse: what they look at first, what they linger on or read, when they go back up, what they click, and the shape of the motion in between. A replay player then plays a typical visit on a live copy of your site, with a ghost cursor that moves, pauses and overshoots the way your visitors do.

You can see it running on [lucasvad.com/work/cursorflow](https://lucasvad.com/work/cursorflow).

## Why

Most sites that aren't big products, like portfolios, landing pages and small storefronts, don't have a real analytics setup, and a full product-analytics or session-replay platform is overkill for them. What they usually have tells you page views and which links got clicked. It doesn't tell you the route people took to get there, or where they gave up.

That route is what matters when the site is selling something, even if it's just you. Do people read the part you care about, or skim past it? Do they open the projects, or only hover them? Is there a spot where visits keep ending, and is that where you meant them to end? Watching one person's recording answers that for one person. cursorflow looks at everyone at once, like watching a swarm instead of a single bird, and boils it down to two things you can act on:

- **A typical visit**, replayed on your actual site: where people go first, what they linger on or read, when they scroll back up, and what they click.
- **Where visits end**: for each stop, the share of visits that left the site right after it. A high rate on your contact section is fine. A high rate halfway through your best project means people are getting stuck or losing interest there.

Change the page, let it retrain, and see if the flow changes.

## How it works

1. **Track.** A small script samples the cursor about 15 times a second, notes when it enters or leaves elements you've tagged with `data-flow`, and which `data-flow-section` is in the middle of the screen. Listeners only record the latest position, and batches go out with `sendBeacon` every 15 seconds and when the tab is hidden, so it stays off the main thread's critical path. It only runs on mouse-and-keyboard devices at least 1024px wide, and never when the browser sends Do Not Track or Global Privacy Control.
2. **Collect.** One route handler validates batches and stores them in Postgres (or in a local file while you're trying it out).
3. **Train.** A Python job, on whatever schedule you like:
   - drops sessions that are too short, that move in perfectly straight lines (scripts), and the strangest 5% by an isolation forest over speed, curvature and activity;
   - turns each session into a list of stops: hover stops (elements someone rested on for at least 250ms or clicked) and read stops (1.5s or more on a section without pointing at anything), then fits a first-order Markov chain over them;
   - samples 4,000 visits from the chain, revisits allowed, keeps the ones about as long as a real visit, and picks the one with the highest average log probability per step. Greedily taking the most likely next stop would loop through the same few places; this finds a visit that's a normal length and made of common moves;
   - redraws every move between two stops in a shared frame and averages them with DTW Barycenter Averaging, after dropping curves that sit far from the medoid. A plain average would flatten the overshoot and hesitation, because people do them at slightly different moments.
4. **Replay.** The player loads your site in a scaled-down iframe and walks the visit: it scrolls to where people usually are, moves along the learned curve, waits the usual dwell time (long reads play faster), and shows a click where most people click. Clicks are shown rather than performed, so the page never jumps or leaves your site; when the next stop is on another page, the player loads it. It also sends synthetic pointer events, so hover effects built in JavaScript react to the ghost.

Nothing identifying is stored: no IP, no user agent, no cookies. A session is a random ID in `sessionStorage` that ends with the tab.

## Edge cases

What the pipeline does about the messy parts of real traffic:

| Case | What happens |
| --- | --- |
| Visitor walks away | After 30s with no mouse, scroll or key input, the tracker records when they were last there and stops sampling until they're back. The trainer ends any hover or read at that moment, so an abandoned tab doesn't count as a 10 minute read. |
| Tab hidden | Counts as idle immediately, and the batch is sent. |
| Coming back hours later | A gap over 30 minutes in the same tab starts a new visit in training. |
| Phones and tablets | Not tracked. Touch has no hover and no cursor path, so the data wouldn't mean the same thing. |
| Bots and scripts | Headless browsers (`navigator.webdriver`) aren't tracked, and sessions that move in perfectly straight lines are dropped in training. |
| Odd sessions | The strangest 5% of sessions by speed, curvature and activity are dropped by an isolation forest, once there are at least 20 to compare. |
| Cursor leaving the window | A jump faster than 10px per ms, or a pause over a second, splits the motion, so teleports never become part of a learned curve. |
| Different screen sizes | Stops are named elements and sections, not pixel positions, and curves are stored relative to their start and end, so they replay on any layout. |
| Layouts that render twice | Give every copy the same name; the player uses the visible one. |
| Batches arriving out of order | Events are sorted by their own clock, not by when they arrived. |
| Rare paths | A move has to be seen at least twice, or in 2% of sessions, to be part of the typical visit, and a move needs three examples before it gets its own curve (otherwise it uses the average of all moves). |
| Renaming a `data-flow` tag | Old and new names are different stops until old data ages out. Keep names stable. |
| Replay inside your own site | The tracker never runs in an iframe, so the replay doesn't record itself. |
| No traffic yet | `examples/simulate.py` generates fake visits so you can see the whole pipeline work. Models trained on them are marked `sample`. |
| Do Not Track, Global Privacy Control | Respected: nothing is recorded. |

## Setup

These steps assume Next.js on Vercel, but every piece is plain JavaScript or Python and works anywhere.

### 1. Install

```bash
pnpm add github:luvadlamudi/cursorflow
```

The package ships TypeScript source, so let Next compile it:

```js
// next.config.js
module.exports = { transpilePackages: ["cursorflow"] };
```

### 2. Database

Any Postgres works. On Vercel, add Neon from the Storage tab and it sets `DATABASE_URL` for you. The tables are created on the first request.

Without `DATABASE_URL`, batches are appended to `.cursorflow/batches.jsonl` and the model is read from `.cursorflow/model.json`, which is handy for trying it locally. Add `.cursorflow` to your `.gitignore`.

### 3. Collect

```ts
// app/api/flow/route.ts
import { collect } from "cursorflow/server";

export const POST = collect({ databaseUrl: process.env.DATABASE_URL });
```

### 4. Track

```tsx
// app/FlowTracker.tsx
"use client";

import { useEffect } from "react";
import { track } from "cursorflow/track";

export default function FlowTracker() {
  useEffect(() => track(), []);
  return null;
}
```

Render `<FlowTracker />` once in your root layout, then tag the things worth learning about:

```tsx
<a data-flow="nav-projects" href="#projects">Projects</a>
<article data-flow={`project-${slug}`}>...</article>
```

Then tag the big sections, so the model can tell when someone is reading rather than pointing:

```tsx
<section data-flow-section="about">...</section>
```

Pick elements a person would deliberately go to: links, buttons, cards. Names need to be stable, since the model is keyed by them. If an element is rendered more than once for different layouts, give every copy the same name; the player uses whichever one is visible.

`track()` takes options for the endpoint, the sampling rate, the flush interval and the minimum width. See [src/track.ts](src/track.ts).

### 5. Train on a schedule

Copy [examples/train.yml](examples/train.yml) into your site's `.github/workflows/` folder and add `DATABASE_URL` as a repository secret. It retrains every day at 09:00 UTC; change the `cron` line to pick your own time (the [crontab guru](https://crontab.guru) helps). You can also run it on demand from the Actions tab.

To train by hand:

```bash
pip install "cursorflow-train @ git+https://github.com/luvadlamudi/cursorflow#subdirectory=train"
cursorflow-train            # uses DATABASE_URL, or ./.cursorflow when it's unset
cursorflow-train --dry-run  # prints the summary without saving a model
```

No traffic yet? Generate some fake visits and train on those (edit the element names in the script to match yours):

```bash
python examples/simulate.py 150 > .cursorflow/batches.jsonl
cursorflow-train
```

Training reads every batch each time. That's fine for a personal site; if yours gets heavy traffic, add a date filter in [train/cursorflow_train/store.py](train/cursorflow_train/store.py).

### 6. Replay

Load the newest model on the server:

```ts
import { latestModel } from "cursorflow/server";

const model = await latestModel({ databaseUrl: process.env.DATABASE_URL }); // null before the first run
```

Then mount the player in a client component:

```tsx
"use client";

import { useEffect, useRef } from "react";
import { replay } from "cursorflow/replay";
import type { FlowModel } from "cursorflow";

export default function FlowReplay({ model }: { model: FlowModel }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => replay(ref.current!, model), [model]);
  return <div ref={ref} />;
}
```

The player sizes itself to its container's width at a 1440 × 900 aspect ratio, and pauses while it's off screen. The site it loads has to be on the same origin, because the player reads the page to find each anchor. The model also carries the numbers for a caption (`since`, `sessions`, `points`, `trainedAt`), and `exits`, the stops visits most often end after.

Two things to check on your own site: the tracker already ignores visits inside an iframe, but an intro animation or preloader will play every time the player loads a page, so skip it when `window.self !== window.top`.

## Repo layout

```
src/model.ts      types for events, batches and the model
src/track.ts      the browser tracker
src/server.ts     the collect handler and latestModel()
src/replay.ts     the replay player
train/            the Python trainer (cursorflow-train)
examples/         the scheduled training workflow and the visit simulator
```

## License

MIT
