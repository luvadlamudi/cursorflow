# cursorflow

See how people actually move through your site.

Analytics on small sites, like portfolios and landing pages, usually stops at page views and clicks. cursorflow tracks how desktop visitors move: what they hover, what they read, how far down each section they get, and where they leave. A nightly job turns that into a model, and a player replays the common paths on a live copy of your site.

It's running on [lucasvad.com/work/cursorflow](https://lucasvad.com/work/cursorflow).

## How it works

The tracker samples the cursor about 15 times a second, records which tagged elements and sections people spend time on, and sends small batches in the background. It only runs on desktop, pauses when someone goes idle, and stores no cookies, IPs or user agents.

The trainer runs three algorithms. An Isolation Forest drops outlier sessions by speed, curvature and activity. k-medoids, over the edit distance between visits' sequences of stops, groups visits by how alike they are and keeps the real visit at the center of each group, along with how many visits it represents. DTW Barycenter Averaging averages cursor motion between stops, keeping the overshoots and hesitations a plain average would flatten. It also measures how much of each section people read, and where visits end.

Retraining runs on a schedule and starts from scratch each time, on the last 30 days of visits. Anything older is deleted, so the model follows how the site is used now and the database stays the same size. Set the window with `--days`.

## Setup

These steps assume Next.js on Vercel.

1. Install it, and let Next compile it (it ships TypeScript):

   ```bash
   pnpm add github:luvadlamudi/cursorflow
   ```

   ```js
   // next.config.js
   module.exports = { transpilePackages: ["cursorflow"] };
   ```

2. Add a Postgres database. On Vercel, add Neon from the Storage tab and it sets `DATABASE_URL`.

3. Add the endpoint:

   ```ts
   // app/api/flow/route.ts
   import { collect } from "cursorflow/server";

   export const POST = collect({ databaseUrl: process.env.DATABASE_URL });
   ```

4. Start the tracker once, in a client component in your root layout:

   ```tsx
   useEffect(() => track(), []); // import { track } from "cursorflow/track"
   ```

5. Tag what you want to learn about:

   ```tsx
   <section data-flow-section="about">
     <a data-flow="resume" href="/resume.pdf">Resume</a>
   </section>
   ```

6. Copy [examples/train.yml](examples/train.yml) to `.github/workflows/` and add `DATABASE_URL` as a repository secret. It retrains daily at 09:00 UTC on the last 30 days; change the `cron` line or `--days` to suit your site.

7. Show it. Load the model on the server and mount the player in a client component:

   ```ts
   const model = await latestModel({ databaseUrl: process.env.DATABASE_URL }); // "cursorflow/server"
   useEffect(() => replay(ref.current!, model), [model]); // "cursorflow/replay"
   ```

   The player has to run on the same site it replays. If your site has an intro animation, skip it when `window.self !== window.top`, or it plays every time the player loads a page.

## License

MIT
