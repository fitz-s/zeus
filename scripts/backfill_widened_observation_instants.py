#!/usr/bin/env python3
# Created: 2026-07-16
# Authority basis: defect-2 fix (f1d135901, src/data/observation_instants_writer.py
#                  _monotone_widening) — this script is the one-shot backfill of the
#                  cells that were frozen BEFORE that fix landed.
"""One-shot backfill for the observation_instants revisions quarantine predating
defect-2's monotone-widening fix (f1d135901).

Before that fix, ``observation_instants_writer.insert_rows`` recorded ANY
payload-hash-changed re-fetch of an hour bucket as a quarantined
``observation_revisions`` row (reason ``payload_hash_mismatch``) and left the
main row frozen at its first-seen value — including the legitimate case where
WU/Ogimet backfilled MORE raw observations into the SAME bucket and the
bucket's true running_max/running_min could only be revealed to be wider,
never narrower. The fix makes new writes self-heal; this script folds the
pre-fix quarantine backlog forward using the IDENTICAL predicate
(``_monotone_widening``), so historical cells converge to the same state a
live re-fetch would have produced had the fix always been in place.

Confirmed zero live P&L overlap: every market these cells belong to has
already settled, and positions were sized/exited against the frozen (narrow)
value at the time — this is a training-integrity backfill only. calibration/
de-bias walk-forward learning reads observation_instants, and a systematically
narrow running_max/running_min teaches it a biased extreme.

Algorithm, per (city, source, utc_timestamp) cell:
  1. Fetch the CURRENT main row.
  2. Replay every ``payload_hash_mismatch`` revision recorded against that
     cell, in chronological order, folding each one in via
     ``_monotone_widening`` exactly as the live writer would have (same
     identity-match + non-narrowing check). Revisions that fail the check
     (different identity, or a narrower value) are skipped, not applied —
     they were genuine disagreements, not backfill completions.
  3. If the folded result is wider than what's currently stored, it is a
     backfill candidate.

Idempotent by construction: a candidate's folded state converges to the
cell's true widest-ever value, so re-running after --apply finds nothing
left to widen (the current row already equals the fold).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import STATE_DIR
from src.data.observation_instants_writer import (
    _INSERT_COLUMNS,
    _insert_revision,
    _monotone_widening,
    _payload_hash_from_provenance,
    _UPDATE_WIDENED_SQL,
    _widened_provenance_json,
)

DEFAULT_DB = STATE_DIR / "zeus-world.db"
SOURCE_REASON = "payload_hash_mismatch"
BACKFILL_REASON = "backfill_monotone_widening_2026-07-16"
# Legacy label: revisions recorded before the 2026-05-29 v1/v2 table
# consolidation (observation_instants_writer.py docstring) carry the old
# table_name value. Both refer to the SAME physical table this script reads.
_TABLE_NAME_VALUES = ("observation_instants", "observation_instants_v2")


def _fold_cell(current: dict, incoming_candidates: list[dict]) -> tuple[dict, list[dict]]:
    """Replay ``incoming_candidates`` against ``current`` via _monotone_widening.

    Mirrors exactly what insert_rows' widening branch does, one candidate at
    a time, folding forward. Returns the final folded row plus the subset of
    candidates that were actually identity-matching + non-narrowing.
    """
    folded = dict(current)
    applied: list[dict] = []
    for incoming in incoming_candidates:
        if not _monotone_widening(folded, incoming):
            continue
        widened_provenance = _widened_provenance_json(folded, incoming)
        folded = dict(folded)
        folded["running_max"] = incoming["running_max"]
        folded["running_min"] = incoming["running_min"]
        folded["observation_count"] = incoming["observation_count"]
        folded["provenance_json"] = widened_provenance
        folded["imported_at"] = incoming["imported_at"]
        applied.append(incoming)
    return folded, applied


def find_widening_backfill_candidates(conn: sqlite3.Connection) -> list[dict]:
    """Scan the pre-fix quarantine and return one entry per widenable cell."""
    columns_sql = ", ".join(f"oi.{c}" for c in _INSERT_COLUMNS)
    placeholders = ", ".join("?" for _ in _TABLE_NAME_VALUES)
    rows = conn.execute(
        f"""
        SELECT oi.id, {columns_sql}, rev.incoming_row_json
        FROM observation_revisions rev
        JOIN observation_instants oi
          ON oi.city = rev.city AND oi.source = rev.source AND oi.utc_timestamp = rev.utc_timestamp
        WHERE rev.reason = ? AND rev.table_name IN ({placeholders})
        ORDER BY oi.city, oi.source, oi.utc_timestamp, rev.recorded_at, rev.id
        """,
        (SOURCE_REASON, *_TABLE_NAME_VALUES),
    ).fetchall()
    names = ["id", *_INSERT_COLUMNS, "incoming_row_json"]

    candidates: list[dict] = []
    cell_key: tuple | None = None
    current: dict | None = None
    incoming_list: list[dict] = []

    def flush() -> None:
        if current is None:
            return
        folded, applied = _fold_cell(current, incoming_list)
        if not applied:
            return
        if (
            folded["running_max"] == current["running_max"]
            and folded["running_min"] == current["running_min"]
        ):
            return
        candidates.append(
            {
                "city": current["city"],
                "source": current["source"],
                "utc_timestamp": current["utc_timestamp"],
                "before": {
                    "running_max": current["running_max"],
                    "running_min": current["running_min"],
                    "observation_count": current["observation_count"],
                },
                "after": {
                    "running_max": folded["running_max"],
                    "running_min": folded["running_min"],
                    "observation_count": folded["observation_count"],
                },
                "n_revisions_examined": len(incoming_list),
                "n_revisions_applied": len(applied),
                "_current_row": current,
                "_folded_row": folded,
            }
        )

    for raw in rows:
        row = dict(zip(names, raw))
        key = (row["city"], row["source"], row["utc_timestamp"])
        if key != cell_key:
            flush()
            cell_key = key
            current = {k: row[k] for k in ("id", *_INSERT_COLUMNS)}
            incoming_list = []
        incoming_list.append(json.loads(row["incoming_row_json"]))
    flush()
    return candidates


def apply_backfill(conn: sqlite3.Connection, candidates: list[dict]) -> int:
    """Write each candidate's folded state + an audit revision row. Returns rows updated."""
    updated = 0
    for candidate in candidates:
        current = candidate["_current_row"]
        folded = candidate["_folded_row"]
        conn.execute(
            _UPDATE_WIDENED_SQL,
            (
                folded["running_max"],
                folded["running_min"],
                folded["observation_count"],
                folded["provenance_json"],
                folded["imported_at"],
                current["id"],
            ),
        )
        _insert_revision(
            conn,
            existing=current,
            incoming=folded,
            existing_payload_hash=_payload_hash_from_provenance(current["provenance_json"]),
            incoming_payload_hash=_payload_hash_from_provenance(folded["provenance_json"]),
            reason=BACKFILL_REASON,
        )
        updated += 1
    return updated


def _stats(candidates: list[dict]) -> dict:
    city_counts = Counter(c["city"] for c in candidates)
    widen_max = [c["after"]["running_max"] - c["before"]["running_max"] for c in candidates]
    widen_min = [c["before"]["running_min"] - c["after"]["running_min"] for c in candidates]
    return {
        "cells": len(candidates),
        "revisions_examined": sum(c["n_revisions_examined"] for c in candidates),
        "revisions_applied": sum(c["n_revisions_applied"] for c in candidates),
        "top_cities": city_counts.most_common(10),
        "running_max_widening_c": {
            "max": max(widen_max) if widen_max else 0.0,
            "mean": sum(widen_max) / len(widen_max) if widen_max else 0.0,
        },
        "running_min_widening_c": {
            "max": max(widen_min) if widen_min else 0.0,
            "mean": sum(widen_min) / len(widen_min) if widen_min else 0.0,
        },
    }


def _public_view(candidate: dict) -> dict:
    return {k: v for k, v in candidate.items() if not k.startswith("_")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.apply:
        from src.state.db_writer_lock import WriteClass, db_writer_lock

        with db_writer_lock(args.db, WriteClass.BULK):
            conn = sqlite3.connect(str(args.db), timeout=30.0)
            conn.execute("PRAGMA busy_timeout = 30000")
            try:
                candidates = find_widening_backfill_candidates(conn)
                conn.execute("SAVEPOINT sp_backfill_widened_observation_instants")
                try:
                    updated = apply_backfill(conn, candidates)
                except Exception:
                    conn.execute("ROLLBACK TO SAVEPOINT sp_backfill_widened_observation_instants")
                    conn.execute("RELEASE SAVEPOINT sp_backfill_widened_observation_instants")
                    raise
                conn.execute("RELEASE SAVEPOINT sp_backfill_widened_observation_instants")
                conn.commit()
            finally:
                conn.close()
        result = {
            "dry_run": False,
            "rows_updated": updated,
            "cells": [_public_view(c) for c in candidates],
            "stats": _stats(candidates),
        }
    else:
        conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=30.0)
        try:
            candidates = find_widening_backfill_candidates(conn)
        finally:
            conn.close()
        result = {
            "dry_run": True,
            "rows_updated": 0,
            "cells": [_public_view(c) for c in candidates],
            "stats": _stats(candidates),
        }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
