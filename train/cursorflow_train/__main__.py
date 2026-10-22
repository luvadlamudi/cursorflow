"""cursorflow-train: read every batch, clean it, fit the model, save it.

Uses DATABASE_URL when it's set, otherwise the local .cursorflow folder.
"""

import argparse
import os
from pathlib import Path

from .flow import train
from .sessions import build_sessions, clean
from .store import load_batches, now_iso, save_model


def main():
    parser = argparse.ArgumentParser(prog="cursorflow-train", description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--dir", type=Path, default=Path(".cursorflow"), help="local folder when there's no database")
    parser.add_argument("--dry-run", action="store_true", help="print the summary without saving")
    args = parser.parse_args()

    batches = load_batches(args.database_url, args.dir)
    if not batches:
        print("No batches yet. Nothing to train on.")
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


if __name__ == "__main__":
    main()
