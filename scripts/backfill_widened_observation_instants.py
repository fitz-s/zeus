#!/usr/bin/env python3
# Created: 2026-07-16
# Lifecycle: created=2026-07-16; last_reviewed=2026-10-09; last_reused=2026-10-09
# Last reused/audited: 2026-10-09 (offline canonical fixtures only)
# Purpose: Inspect historical widening candidates without enabling an unproved repair.
# Reuse: Operational apply is blocked pending reviewed settled/no-live-exposure proof.
# Authority basis: defect-2 fix (f1d135901, src/data/observation_instants_writer.py
#                  _monotone_widening) — this script is the one-shot backfill of the
#                  cells that were frozen BEFORE that fix landed.
#                  PLAN.md, finite_evidence_probability_symmetry, 2026-10-09 consumer repair.
"""One-shot backfill for the observation_instants revisions quarantine predating
defect-2's monotone-widening fix (f1d135901).

Before that fix, ``observation_instants_writer.insert_rows`` recorded ANY
payload-hash-changed re-fetch of an hour bucket as a quarantined
``observation_revisions`` row (reason ``payload_hash_mismatch``) and left the
main row frozen at its first-seen value — including the legitimate case where
WU/Ogimet backfilled MORE raw observations into the SAME bucket and the
bucket's true running_max/running_min could only be revealed to be wider,
never narrower. The fix makes new writes self-heal; this script folds the
pre-fix quarantine backlog using the current ``_monotone_widening`` predicate.
Only cells whose folded extrema widen are candidates for this historical repair.

The original repair required settled markets and zero live P&L overlap, but
never enforced either precondition. Operational ``--apply`` and the public
``apply_backfill`` API now refuse until a separately reviewed repair establishes
that proof. The private write primitive is retained for isolated fixture
verification; it supplies no settlement or live-exposure authority. Both tables
remain WORLD_CLASS, as declared by architecture/db_table_ownership.yaml.

Algorithm, per (city, source, utc_timestamp) cell:
  1. Fetch the CURRENT main row.
  2. Replay every ``payload_hash_mismatch`` revision recorded against that
     cell, in chronological order, folding each one in via
     ``_monotone_widening`` using the current writer's predicate (same
     identity-match + non-narrowing check). Revisions that fail the check
     (different identity, or a narrower value) are skipped, not applied —
     they were genuine disagreements, not backfill completions.
  3. If the folded result is wider than what's currently stored, it is a
     backfill candidate.

Accepted revisions carry temp_current and source_file exactly as recorded;
missing or ambiguous snapshot metadata is refused, never reconstructed.
Candidate selection remains extrema-only: this is not a replay of every
current-writer update category. A second scan after fixture application finds
no remaining widening candidate.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter
from dataclasses import fields
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import STATE_DIR
from src.data.observation_instants_writer import (
    ObsV2Row,
    _INSERT_COLUMNS,
    _fetch_existing,
    _insert_revision,
    _material_differences,
    _monotone_widening,
    _payload_hash_from_provenance,
    _UPDATE_CURRENT_SQL,
    _widened_provenance_json,
)

DEFAULT_DB = STATE_DIR / "zeus-world.db"
SOURCE_REASON = "payload_hash_mismatch"
BACKFILL_REASON = "backfill_monotone_widening_2026-07-16"
APPLY_BLOCKED_REASON = (
    "Operational backfill apply is disabled: settled-only scope and zero live "
    "P&L overlap have not been proved. A separately reviewed repair is required."
)
# Legacy label: revisions recorded before the 2026-05-29 v1/v2 table
# consolidation (observation_instants_writer.py docstring) carry the old
# table_name value. Both refer to the SAME physical table this script reads.
_TABLE_NAME_VALUES = ("observation_instants", "observation_instants_v2")


def _unique_json_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Ambiguous historical metadata: duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid historical metadata: non-finite JSON value {value}")


def _load_json_object(value: str) -> dict:
    result = json.loads(
        value, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant
    )
    if not isinstance(result, dict):
        raise ValueError("Historical metadata must be a JSON object")
    return result


def _validate_row_metadata(row: dict) -> None:
    """Require an explicit current-writer snapshot; never fill historical gaps.

    SCOPE: the scanned repair batch. DRAIN: recover the original complete,
    unambiguous snapshots for separate review. RESET: all snapshots validate.
    Explicit nulls remain null where the current typed writer allows them.
    """
    missing = set(_INSERT_COLUMNS) - row.keys()
    if missing:
        raise ValueError(f"Missing historical metadata: {', '.join(sorted(missing))}")
    provenance = _load_json_object(row["provenance_json"])
    for column in ("temp_current", "running_max", "running_min"):
        value = row[column]
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError(f"Invalid historical metadata: {column}")
    source_file = row["source_file"]
    if source_file is not None and (not isinstance(source_file, str) or not source_file.strip()):
        raise ValueError("Invalid historical metadata: source_file")
    if "latest_temp" in provenance:
        latest = provenance["latest_temp"]
        if latest is not None and (type(latest) not in (int, float) or not math.isfinite(latest)):
            raise ValueError("Invalid historical metadata: latest_temp")
        if latest != row["temp_current"]:
            raise ValueError("Ambiguous historical metadata: latest_temp disagrees with temp_current")
    if "raw_obs_count" in provenance and (
        type(provenance["raw_obs_count"]) is not int
        or provenance["raw_obs_count"] != row["observation_count"]
    ):
        raise ValueError("Ambiguous historical metadata: raw_obs_count disagrees with observation_count")
    ObsV2Row(**{field.name: row[field.name] for field in fields(ObsV2Row)})


def _fold_cell(current: dict, incoming_candidates: list[dict]) -> tuple[dict, list[dict]]:
    """Replay ``incoming_candidates`` against ``current`` via _monotone_widening.

    Copies every field updated by insert_rows' widening branch, one candidate
    at a time. Returns the final folded row plus the subset of
    candidates that were actually identity-matching + non-narrowing.
    """
    _validate_row_metadata(current)
    folded = dict(current)
    applied: list[dict] = []
    for incoming in incoming_candidates:
        _validate_row_metadata(incoming)
        if (
            _payload_hash_from_provenance(folded["provenance_json"])
            == _payload_hash_from_provenance(incoming["provenance_json"])
        ):
            if _material_differences(folded, incoming):
                raise ValueError("Historical payload_hash reused with changed material fields")
            continue
        if not _monotone_widening(folded, incoming):
            continue
        widened_provenance = _widened_provenance_json(folded, incoming)
        folded = dict(folded)
        folded["temp_current"] = incoming["temp_current"]
        folded["running_max"] = incoming["running_max"]
        folded["running_min"] = incoming["running_min"]
        folded["observation_count"] = incoming["observation_count"]
        folded["provenance_json"] = widened_provenance
        folded["imported_at"] = incoming["imported_at"]
        folded["source_file"] = incoming["source_file"]
        applied.append(incoming)
    return folded, applied


def find_widening_backfill_candidates(conn: sqlite3.Connection) -> list[dict]:
    """Scan the pre-fix quarantine and return one entry per widenable cell."""
    columns_sql = ", ".join(f"oi.{c}" for c in _INSERT_COLUMNS)
    placeholders = ", ".join("?" for _ in _TABLE_NAME_VALUES)
    rows = conn.execute(
        f"""
        SELECT oi.id, {columns_sql}, rev.incoming_row_json, rev.incoming_payload_hash
        FROM observation_revisions rev
        JOIN observation_instants oi
          ON oi.city = rev.city AND oi.source = rev.source AND oi.utc_timestamp = rev.utc_timestamp
        WHERE rev.reason = ? AND rev.table_name IN ({placeholders})
        ORDER BY oi.city, oi.source, oi.utc_timestamp, rev.recorded_at, rev.id
        """,
        (SOURCE_REASON, *_TABLE_NAME_VALUES),
    ).fetchall()
    names = ["id", *_INSERT_COLUMNS, "incoming_row_json", "incoming_payload_hash"]

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
        incoming = _load_json_object(row["incoming_row_json"])
        _validate_row_metadata(incoming)
        if _payload_hash_from_provenance(incoming["provenance_json"]) != row["incoming_payload_hash"]:
            raise ValueError("Ambiguous historical metadata: revision payload hash disagrees with snapshot")
        incoming_list.append(incoming)
    flush()
    return candidates


def apply_backfill(conn: sqlite3.Connection, candidates: list[dict]) -> int:
    """Refuse operational writes until the historical repair scope is proved.

    SCOPE: all operational application. DRAIN: a separately reviewed repair
    proves settled-only scope and no live exposure. RESET: that repair replaces
    this refusal; no flag, path or environment value confers authority here.
    """
    raise RuntimeError(APPLY_BLOCKED_REASON)


def _write_backfill_rows(conn: sqlite3.Connection, candidates: list[dict]) -> int:
    """Private SQL primitive; its caller owns the BULK lock and SAVEPOINT.

    SCOPE: this fixture batch. DRAIN: rescan stale candidates or investigate
    conflicting audit history. RESET: snapshots still match and each update
    has its own new audit row; any failure rolls back the whole batch.
    """
    updated = 0
    for candidate in candidates:
        current = candidate["_current_row"]
        folded = candidate["_folded_row"]
        if _fetch_existing(conn, current) != current:
            raise ValueError("Backfill candidate changed since scan; refusing stale snapshot")
        _validate_row_metadata(folded)
        cursor = conn.execute(
            _UPDATE_CURRENT_SQL,
            (
                folded["temp_current"],
                folded["running_max"],
                folded["running_min"],
                folded["observation_count"],
                folded["provenance_json"],
                folded["imported_at"],
                folded["source_file"],
                current["id"],
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("Backfill candidate no longer identifies exactly one row")
        _insert_revision(
            conn,
            existing=current,
            incoming=folded,
            existing_payload_hash=_payload_hash_from_provenance(current["provenance_json"]),
            incoming_payload_hash=_payload_hash_from_provenance(folded["provenance_json"]),
            reason=BACKFILL_REASON,
        )
        if conn.execute("SELECT changes()").fetchone()[0] != 1:
            raise ValueError("Backfill audit revision already exists; refusing unaudited update")
        updated += 1
    return updated


def _apply_backfill_transaction(db: Path, candidates: list[dict]) -> int:
    """Unexposed write mechanics for private fixture verification, not authority.

    The public API and CLI never call this primitive. The BULK lock does not
    prove exclusion of LIVE writers or settled/no-live-exposure scope.
    """
    from src.state.db_writer_lock import WriteClass, db_writer_lock

    with db_writer_lock(db, WriteClass.BULK):
        conn = sqlite3.connect(f"file:{db}?mode=rw", uri=True, timeout=30.0)
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("SAVEPOINT sp_backfill_widened_observation_instants")
            try:
                updated = _write_backfill_rows(conn, candidates)
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT sp_backfill_widened_observation_instants")
                conn.execute("RELEASE SAVEPOINT sp_backfill_widened_observation_instants")
                raise
            conn.execute("RELEASE SAVEPOINT sp_backfill_widened_observation_instants")
            conn.commit()
        finally:
            conn.close()
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
        parser.error(APPLY_BLOCKED_REASON)
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
