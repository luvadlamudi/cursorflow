import fs from "node:fs/promises";
import path from "node:path";
import postgres from "postgres";
import type { Batch, FlowModel } from "./model";

export type ServerOptions = {
  // Postgres connection string. Without one, batches and the model live in local files instead,
  // which is enough to try everything out in development.
  databaseUrl?: string;
  // Folder for the local files. Default ".cursorflow".
  dir?: string;
};

export const SCHEMA = `
create table if not exists cursorflow_batches (
  id bigserial primary key,
  session_id text not null,
  viewport_w int not null,
  viewport_h int not null,
  events jsonb not null,
  received_at timestamptz not null default now()
);
create table if not exists cursorflow_models (
  id bigserial primary key,
  trained_at timestamptz not null default now(),
  model jsonb not null
);`;

const MAX_BYTES = 64_000;
const MAX_EVENTS = 4000;
const KINDS = new Set(["m", "s", "e", "l", "c", "r", "v", "i"]);

let sql: postgres.Sql | undefined;
let ready: Promise<unknown> | undefined;

// One connection per server instance, and the tables are created the first time it's used.
async function db(url: string) {
  sql ??= postgres(url, { max: 1 });
  ready ??= sql.unsafe(SCHEMA);
  await ready;
  return sql;
}

function valid(b: Batch) {
  return (
    typeof b?.sid === "string" &&
    b.sid.length <= 64 &&
    Number.isFinite(b.vw) &&
    Number.isFinite(b.vh) &&
    Array.isArray(b.events) &&
    b.events.length > 0 &&
    b.events.length <= MAX_EVENTS &&
    b.events.every((e) => Array.isArray(e) && KINDS.has(e[0]) && typeof e[1] === "number")
  );
}

// A route handler for the tracker's batches: (Request) => Response, so it drops into Next.js,
// Hono, Remix, or anything else built on web requests.
export function collect(options: ServerOptions = {}) {
  const dir = options.dir ?? ".cursorflow";
  return async (req: Request) => {
    // Only the site itself may post.
    const origin = req.headers.get("origin");
    if (origin && new URL(origin).host !== new URL(req.url).host) return new Response(null, { status: 403 });

    const text = await req.text();
    if (text.length > MAX_BYTES) return new Response(null, { status: 413 });
    let batch: Batch;
    try {
      batch = JSON.parse(text);
    } catch {
      return new Response(null, { status: 400 });
    }
    if (!valid(batch)) return new Response(null, { status: 400 });

    if (options.databaseUrl) {
      const q = await db(options.databaseUrl);
      await q`insert into cursorflow_batches (session_id, viewport_w, viewport_h, events)
              values (${batch.sid}, ${Math.round(batch.vw)}, ${Math.round(batch.vh)}, ${q.json(batch.events as never)})`;
    } else {
      await fs.mkdir(dir, { recursive: true });
      await fs.appendFile(path.join(dir, "batches.jsonl"), JSON.stringify({ ...batch, at: new Date().toISOString() }) + "\n");
    }
    return new Response(null, { status: 204 });
  };
}

// The newest trained model, or null before the first training run.
export async function latestModel(options: ServerOptions = {}): Promise<FlowModel | null> {
  if (options.databaseUrl) {
    const q = await db(options.databaseUrl);
    const [row] = await q`select model from cursorflow_models order by trained_at desc limit 1`;
    return (row?.model as FlowModel) ?? null;
  }
  try {
    return JSON.parse(await fs.readFile(path.join(options.dir ?? ".cursorflow", "model.json"), "utf8"));
  } catch {
    return null;
  }
}
