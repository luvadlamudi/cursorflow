"""cursorflow-train: read recent batches, clean them, fit the model, save it.

Each run trains from scratch on the last --days days of visits, then deletes anything older, so
the model follows how the site is used now and the database stays the same size. Uses
DATABASE_URL when it's set, otherwise the local .cursorflow folder.
"""

import argparse
import os
from pathlib import Path

from .flow import train
from .sessions import build_sessions, clean
from .store import load_batches, now_iso, prune, save_model


def main():
    parser = argparse.ArgumentParser(prog="cursorflow-train", description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--dir", type=Path, default=Path(".cursorflow"), help="local folder when there's no database")
    parser.add_argument(
        "--days", type=int, default=int(os.environ.get("CURSORFLOW_DAYS", 30)), help="train on this many days of visits, 0 for all"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the summary without saving or deleting anything")
    args = parser.parse_args()

    batches = load_batches(args.database_url, args.dir, args.days)
    if not batches:
        print(f"No batches in the last {args.days} days. Nothing to train on." if args.days else "No batches yet. Nothing to train on.")
        return

    raw = build_sessions(batches)
    sessions, report = clean(raw)
    print(
        f"{report['total']} sessions: {report['short']} too short, {report['scripted']} scripted, "
        f"{report['outliers']} outliers, {report['kept']} kept"
    )
    if not sessions:
        print("No sessions left after cleaning. Nothing to train on.")
        return

    fitted = train(sessions)
    model = {
        "version": 1,
        "trainedAt": now_iso(),
        "days": args.days,
        "since": min(b["at"] for b in batches).isoformat(timespec="seconds"),
        "sessions": {"total": report["total"], "kept": report["kept"]},
        "points": sum(len(s.moves) for s in raw),
        **fitted,
    }
    for i, p in enumerate(model["patterns"], 1):
        steps = [f"{'read ' if s['kind'] == 'read' else ''}{s['anchor']} ({s['dwell'] / 1000:.1f}s)" for s in p["stops"]]
        print(f"Pattern {i} ({p['share']:.0%} of visits): {' -> '.join(steps)}")
    print(f"Learned moves: {len(model['moves'])}, fallback from {model['fallback']['samples']} moves")
    for x in model["attention"]:
        bars = "".join(" .:-=+*#%@"[min(9, int(b * 9.99))] for b in x["bands"])
        print(f"  {x['route']} {x['section']:<10} {x['seconds']:>5.1f}s  [{bars}]")
    for x in model["exits"]:
        print(f"  {x['rate']:.0%} of visits that reached {x['route']} {x['section']} ended there ({x['visits']})")

    if not args.dry_run:
        print(f"Saved to {save_model(model, args.database_url, args.dir)}")
        prune(args.database_url, args.dir, args.days)


if __name__ == "__main__":
    main()
