"""Reading batches and saving models, from Postgres or from the local .cursorflow folder."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODELS_TABLE = """
create table if not exists cursorflow_models (
  id bigserial primary key,
  trained_at timestamptz not null default now(),
  model jsonb not null
)"""
# Older models are deleted once there are this many newer ones.
KEEP_MODELS = 30


def cutoff(days: int) -> datetime | None:
    return datetime.now(timezone.utc) - timedelta(days=days) if days > 0 else None


def load_batches(database_url: str | None, folder: Path, days: int) -> list[dict]:
    """Batches from the last `days` days (all of them when `days` is 0)."""
    since = cutoff(days)
    if database_url:
        import psycopg

        with psycopg.connect(database_url) as conn:
            rows = conn.execute(
                "select session_id, viewport_w, viewport_h, events, received_at from cursorflow_batches"
                " where %s::timestamptz is null or received_at >= %s order by id",
                [since, since],
            ).fetchall()
        return [{"sid": r[0], "vw": r[1], "vh": r[2], "events": r[3], "at": r[4]} for r in rows]

    file = folder / "batches.jsonl"
    if not file.exists():
        return []
    batches = [json.loads(line) for line in file.read_text().splitlines() if line.strip()]
    for b in batches:
        b["at"] = datetime.fromisoformat(b["at"].replace("Z", "+00:00"))
    return [b for b in batches if since is None or b["at"] >= since]


def prune(database_url: str | None, folder: Path, days: int) -> None:
    """Deletes batches older than the window, and all but the newest models.

    Nothing outside the window is ever trained on again, so keeping it would only grow the
    database and slow down every run.
    """
    since = cutoff(days)
    if database_url:
        import psycopg

        with psycopg.connect(database_url) as conn:
            if since:
                conn.execute("delete from cursorflow_batches where received_at < %s", [since])
            conn.execute(
                "delete from cursorflow_models where id not in"
                " (select id from cursorflow_models order by trained_at desc limit %s)",
                [KEEP_MODELS],
            )
        return

    file = folder / "batches.jsonl"
    if since and file.exists():
        lines = [line for line in file.read_text().splitlines() if line.strip()]
        recent = [line for line in lines if datetime.fromisoformat(json.loads(line)["at"].replace("Z", "+00:00")) >= since]
        file.write_text("".join(line + "\n" for line in recent))


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
