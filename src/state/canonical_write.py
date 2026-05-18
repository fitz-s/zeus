"""DT#1 / INV-17 choke point: DB commit precedes JSON export.

Public symbols:
  commit_then_export(conn, *, db_op, json_exports) -> int | None
  detect_stale_portfolio(json_payload, conn) -> bool
  transition_phase(conn, position, *, event_type, reason, error, source_module) -> bool
"""
# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/operations/task_2026-05-17_post_karachi_remediation/
#                  LIFECYCLE_FINDINGS_DEFERRED.md (WAVE-3 Batch B, F108 reframe)
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


# ---------------------------------------------------------------------------
# transition_phase — single writer for pending_exit phase mutations
# ---------------------------------------------------------------------------
#
# Moved from src/state/db.py to here (WAVE-3 Batch B bot review fix, 2026-05-18)
# so that the K0 db.py layer does not import K2 src.engine.lifecycle_events.
# src/state/canonical_write.py may freely import from src.engine because it is
# not the raw DB substrate; db.py re-exports this symbol for backwards compat.
def transition_phase(
    conn: sqlite3.Connection | None,
    position: object,
    *,
    event_type: str,
    reason: str,
    error: str,
    source_module: str = "src.execution.exit_lifecycle",
) -> bool:
    """Atomically transition a position into pending_exit + emit canonical event.

    Returns True iff (a) conn was provided AND (b) projection.phase resolves to
    `pending_exit` AND (c) the append+project SAVEPOINT committed.

    Single-writer property: every pending_exit phase mutation flows through
    here; the position_events row and the phase column in position_current are
    written together inside one SAVEPOINT. No other call path performs that
    atomic pairing.
    """
    if conn is None:
        return False
    try:
        import copy as _copy
        import json as _json
        from datetime import datetime as _dt, timezone as _tz

        from src.engine.lifecycle_events import build_position_current_projection
        from src.state.db import append_many_and_project
        from src.state.lifecycle_manager import (
            fold_lifecycle_phase,
            phase_for_runtime_position,
        )

        trade_id = str(getattr(position, "trade_id", "") or "")
        if not trade_id:
            return False
        sequence_no_row = conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
            (trade_id,),
        ).fetchone()
        sequence_no = int(sequence_no_row[0] or 0) + 1
        occurred_at = _dt.now(_tz.utc).isoformat()
        phase_before = phase_for_runtime_position(
            state=getattr(position, "pre_exit_state", "") or "holding",
        ).value
        phase_after = fold_lifecycle_phase(phase_before, "pending_exit").value
        projection_position = _copy.copy(position)
        if not any(
            getattr(projection_position, field, "")
            for field in (
                "last_exit_at",
                "chain_verified_at",
                "day0_entered_at",
                "entered_at",
                "order_posted_at",
            )
        ):
            projection_position.order_posted_at = occurred_at
        projection = build_position_current_projection(projection_position)
        if projection.get("phase") != "pending_exit":
            return False
        projection["updated_at"] = occurred_at
        payload = {
            "status": getattr(position, "exit_state", ""),
            "exit_reason": getattr(position, "exit_reason", "") or reason,
            "error": error or getattr(position, "last_exit_error", ""),
            "retry_count": getattr(position, "exit_retry_count", 0),
            "next_retry_at": getattr(position, "next_exit_retry_at", ""),
            "last_exit_order_id": getattr(position, "last_exit_order_id", ""),
        }
        env = str(getattr(position, "env", "") or "live")
        if env not in {"live", "test", "replay", "backtest", "shadow"}:
            env = "live"
        event = {
            "event_id": f"{trade_id}:phase_transition:{sequence_no}",
            "position_id": trade_id,
            "event_version": 1,
            "sequence_no": sequence_no,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "phase_before": phase_before,
            "phase_after": phase_after,
            "strategy_key": str(
                getattr(position, "strategy_key", "") or getattr(position, "strategy", "") or ""
            ),
            "decision_id": None,
            "snapshot_id": getattr(position, "decision_snapshot_id", "") or None,
            "order_id": getattr(position, "last_exit_order_id", "") or None,
            "command_id": None,
            "caused_by": "transition_phase",
            "idempotency_key": f"{trade_id}:phase_transition:{sequence_no}",
            "venue_status": str(getattr(position, "exit_state", "") or "rejected"),
            "source_module": source_module,
            "env": env,
            "payload_json": _json.dumps(payload, default=str, sort_keys=True),
        }
        append_many_and_project(conn, [event], projection)
        return True
    except Exception as exc:
        logger.warning(
            "transition_phase failed for %s (event_type=%s): %s",
            getattr(position, "trade_id", ""),
            event_type,
            exc,
        )
        return False
