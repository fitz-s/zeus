"""DT#1 / INV-17 choke point: DB commit precedes JSON export.

Public symbols:
  commit_then_export(conn, *, db_op, json_exports) -> int | None
  detect_stale_portfolio(json_payload, conn) -> bool
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Callable, Sequence

logger = logging.getLogger(__name__)


def commit_then_export(
    conn: sqlite3.Connection,
    *,
    db_op: Callable[[], "int | None"],
    json_exports: Sequence[Callable[[], None]] = (),
    defer_commit: bool = False,
) -> "int | None":
    """DT#1 / INV-17 choke point.

    Contract:
      1. Call db_op() inside a transaction. On exception, rollback and re-raise.
      2. Commit (db_op's return value is the committed artifact_id or None).
      3. Only after commit, fire each json_export in order (no arguments passed).
      4. If a json_export raises, LOG the exception (logger.exception) but do
         NOT re-raise — DB is authoritative, stale JSON is recoverable.
      5. Return the artifact_id.

    Note: json_exports are zero-argument callables. Callers that need the
    artifact_id in their export should capture it via a closure over a
    mutable container (e.g. a list) updated by db_op, or pass a lambda.

    defer_commit (2026-05-11 antibody): when True, skip the per-call commit
    AND skip json_exports. The caller is responsible for: (a) wrapping the
    batched calls in an outer BEGIN/COMMIT, (b) firing any required JSON
    exports AFTER the batched commit. Only safe when the caller does NOT
    rely on per-row durability and produces zero json_exports per row.
    Required to avoid O(N) fsync wall-clock in bulk-ingest loops (per debug
    session: 364 commits × ~50ms-1s fsync = 5-10 min observed wedge).
    """
    artifact_id: "int | None" = None
    try:
        artifact_id = db_op()
        if not defer_commit:
            conn.commit()
    except Exception:
        try:
            if not defer_commit:
                conn.rollback()
        except Exception:
            pass
        raise

    if defer_commit:
        # Caller owns commit + json_exports. Return artifact_id only.
        return artifact_id

    for export_fn in json_exports:
        try:
            export_fn()
        except Exception:
            logger.exception(
                "JSON export failed after DB commit (artifact_id=%s); "
                "DB is authoritative — stale JSON is recoverable.",
                artifact_id,
            )

    return artifact_id


def detect_stale_portfolio(json_payload: dict, conn: sqlite3.Connection) -> bool:
    """Return True if positions.json's last_committed_artifact_id is behind
    the DB's most recent decision_log.id.

    Returns False if the JSON has no last_committed_artifact_id (legacy file,
    cannot detect drift — be conservative, assume fresh).
    """
    last_committed = json_payload.get("last_committed_artifact_id")
    if last_committed is None:
        return False

    row = conn.execute("SELECT MAX(id) FROM decision_log").fetchone()
    if row is None or row[0] is None:
        return False

    max_db_id: int = row[0]
    return int(last_committed) < max_db_id
