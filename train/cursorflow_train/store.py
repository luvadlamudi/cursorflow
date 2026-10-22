"""Reading batches and saving models, from Postgres or from the local .cursorflow folder."""

import json
from datetime import datetime, timezone
from pathlib import Path

MODELS_TABLE = """
create table if not exists cursorflow_models (
  id bigserial primary key,
  trained_at timestamptz not null default now(),
  model jsonb not null
)"""


def load_batches(database_url: str | None, folder: Path) -> list[dict]:
    if database_url:
        import psycopg

        with psycopg.connect(database_url) as conn:
            rows = conn.execute(
                "select session_id, viewport_w, viewport_h, events, received_at from cursorflow_batches order by id"
            ).fetchall()
        return [{"sid": r[0], "vw": r[1], "vh": r[2], "events": r[3], "at": r[4]} for r in rows]

    file = folder / "batches.jsonl"
    if not file.exists():
        return []
    batches = [json.loads(line) for line in file.read_text().splitlines() if line.strip()]
    for b in batches:
        b["at"] = datetime.fromisoformat(b["at"].replace("Z", "+00:00"))
    return batches


def save_model(model: dict, database_url: str | None, folder: Path) -> str:
    if database_url:
        import psycopg
        from psycopg.types.json import Jsonb

        with psycopg.connect(database_url) as conn:
            conn.execute(MODELS_TABLE)
            conn.execute("insert into cursorflow_models (model) values (%s)", [Jsonb(model)])
        return "cursorflow_models"

    folder.mkdir(parents=True, exist_ok=True)
    out = folder / "model.json"
    out.write_text(json.dumps(model))
    return str(out)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
