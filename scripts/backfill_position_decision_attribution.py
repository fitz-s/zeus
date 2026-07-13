# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md Round-2 delta
#   adjudication §(c) ("edli_live_profit_audit: LX-T3 or R7?") + LX-E packet
#   (position_decision_attribution rehome + EDLI receipts).
"""Backfill position_decision_attribution for positions predating the LX-E hook.

CONSERVATIVE BY DESIGN — exact links only. The current settlement-time bridge
(``src.analysis.settlement_skill_attribution._resolve_cert_hash_for_position``, the
legacy fallback path) picks the LATEST ``edli_live_profit_audit`` row for a
(condition_id, direction) pair; 14 such pairs map to more than one distinct
certificate hash (a real re-decision on the same market), so "latest row" there is
a guess. This backfill never repeats that guess. It links a position to its
decision certificate via the EXACT match:

    position_current.position_id
      -> venue_commands.command_id   (its own ENTRY command, earliest by created_at)
      -> edli_live_profit_audit.execution_command_id  (the SAME command)
      -> expected_edge_source_certificate_hash

When that exact join resolves to exactly ONE distinct non-empty certificate hash,
the position is written ATTRIBUTED. When it resolves to zero or more than one
distinct hash, or the position has no ENTRY command at all, the position is written
UNATTRIBUTABLE with a named reason — NEVER the (condition_id, direction) latest-row
guess. Every historical position gets an explicit outcome; nothing is left silently
unresolved for a future guess to fill in.

Dry-run by default. Pass --apply to commit the writes.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import ZEUS_WORLD_DB_PATH, get_trade_connection  # noqa: E402
from src.state.schema.position_decision_attribution_schema import ensure_table  # noqa: E402

REASON_NO_ENTRY_COMMAND = "no_entry_command_found"
REASON_NO_AUDIT_ROW = "no_audit_row_for_command"
REASON_AMBIGUOUS_MULTI_HASH = "ambiguous_multi_hash_for_command"


@dataclass
class BackfillOutcome:
    position_id: str
    command_id: Optional[str]
    resolution: str
    decision_certificate_hash: Optional[str]
    resolution_reason: Optional[str]


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _attach_world_read_only(conn) -> None:
    attached = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
    if "world" in attached:
        return
    world_uri = f"file:{ZEUS_WORLD_DB_PATH}?mode=ro"
    conn.execute("ATTACH DATABASE ? AS world", (world_uri,))


def _entry_command_for_position(conn, position_id: str) -> Optional[str]:
    """The position's earliest ENTRY venue_commands.command_id, or None.

    A position may in principle carry more than one ENTRY-kind command row (retried
    submissions under distinct idempotency keys); the earliest by created_at is the
    real decision command.
    """
    row = conn.execute(
        """
        SELECT command_id
        FROM venue_commands
        WHERE position_id = ? AND intent_kind = 'ENTRY'
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (position_id,),
    ).fetchone()
    return str(row[0]) if row is not None else None


def _distinct_cert_hashes_for_command(conn, command_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT expected_edge_source_certificate_hash
        FROM world.edli_live_profit_audit
        WHERE execution_command_id = ?
          AND expected_edge_source_certificate_hash IS NOT NULL
          AND expected_edge_source_certificate_hash <> ''
        """,
        (command_id,),
    ).fetchall()
    return [str(r[0]) for r in rows]


def compute_outcome(conn, position_id: str) -> BackfillOutcome:
    """Resolve ONE position's exact-link outcome (never a latest-row guess)."""
    command_id = _entry_command_for_position(conn, position_id)
    if command_id is None:
        return BackfillOutcome(
            position_id=position_id,
            command_id=None,
            resolution="UNATTRIBUTABLE",
            decision_certificate_hash=None,
            resolution_reason=REASON_NO_ENTRY_COMMAND,
        )
    hashes = _distinct_cert_hashes_for_command(conn, command_id)
    if len(hashes) == 1:
        return BackfillOutcome(
            position_id=position_id,
            command_id=command_id,
            resolution="ATTRIBUTED",
            decision_certificate_hash=hashes[0],
            resolution_reason="exact_command_link",
        )
    if len(hashes) == 0:
        return BackfillOutcome(
            position_id=position_id,
            command_id=command_id,
            resolution="UNATTRIBUTABLE",
            decision_certificate_hash=None,
            resolution_reason=REASON_NO_AUDIT_ROW,
        )
    return BackfillOutcome(
        position_id=position_id,
        command_id=command_id,
        resolution="UNATTRIBUTABLE",
        decision_certificate_hash=None,
        resolution_reason=REASON_AMBIGUOUS_MULTI_HASH,
    )


def _positions_missing_attribution(conn) -> list[str]:
    ensure_table(conn)
    rows = conn.execute(
        """
        SELECT position_id FROM position_current
        WHERE position_id NOT IN (SELECT position_id FROM position_decision_attribution)
        """
    ).fetchall()
    return [str(r[0]) for r in rows]


def write_outcome(conn, outcome: BackfillOutcome, *, now_iso: str) -> None:
    conn.execute(
        """
        INSERT INTO position_decision_attribution (
            attribution_id, position_id, command_id, decision_certificate_hash,
            resolution, resolution_reason, source, intent_kind, created_at,
            schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, 'BACKFILL', 'ENTRY', ?, 1)
        ON CONFLICT(position_id) DO NOTHING
        """,
        (
            uuid.uuid4().hex[:16],
            outcome.position_id,
            outcome.command_id,
            outcome.decision_certificate_hash,
            outcome.resolution,
            outcome.resolution_reason,
            now_iso,
        ),
    )


def run_backfill(conn, *, apply: bool) -> dict:
    ensure_table(conn)
    _attach_world_read_only(conn)
    position_ids = _positions_missing_attribution(conn)
    now_iso = _utc_now_iso()

    outcomes: list[BackfillOutcome] = [compute_outcome(conn, pid) for pid in position_ids]
    for outcome in outcomes:
        write_outcome(conn, outcome, now_iso=now_iso)

    if apply:
        conn.commit()
    else:
        conn.rollback()

    reason_counts = Counter(
        o.resolution_reason for o in outcomes if o.resolution == "UNATTRIBUTABLE"
    )
    return {
        "considered": len(outcomes),
        "attributed": sum(1 for o in outcomes if o.resolution == "ATTRIBUTED"),
        "unattributable": sum(1 for o in outcomes if o.resolution == "UNATTRIBUTABLE"),
        "unattributable_by_reason": dict(reason_counts),
        "applied": apply,
    }


def _cli(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill position_decision_attribution with EXACT command-id links "
            "only (never the ambiguous condition_id/direction latest-row guess). "
            "Dry-run by default."
        ),
    )
    parser.add_argument(
        "--apply", action="store_true", default=False,
        help="Commit the writes. Without this flag the backfill runs read-only "
        "(rolled back at the end) and only prints the summary.",
    )
    args = parser.parse_args(argv)

    conn = get_trade_connection(write_class="bulk" if args.apply else None)
    try:
        stats = run_backfill(conn, apply=args.apply)
    finally:
        conn.close()
    mode = "APPLIED" if args.apply else "DRY-RUN (no writes committed; re-run with --apply)"
    print(f"backfill_position_decision_attribution [{mode}]: {stats}")


if __name__ == "__main__":
    _cli()
