from __future__ import annotations

import sqlite3

from src.architecture.decorators import capability


POSITION_EVENT_ENVS = ("live", "test", "replay", "backtest", "shadow")


CANONICAL_POSITION_CURRENT_COLUMNS = (
    "position_id",
    "phase",
    "trade_id",
    "market_id",
    "city",
    "cluster",
    "target_date",
    "bin_label",
    "direction",
    "unit",
    "size_usd",
    "shares",
    "cost_basis_usd",
    "entry_price",
    "p_posterior",
    "entry_ci_width",
    "exit_retry_count",
    "next_exit_retry_at",
    "last_monitor_prob",
    "last_monitor_prob_is_fresh",
    "last_monitor_edge",
    "last_monitor_market_price",
    "last_monitor_market_price_is_fresh",
    "decision_snapshot_id",
    "entry_method",
    "strategy_key",
    "edge_source",
    "discovery_mode",
    "chain_state",
    "token_id",
    "no_token_id",
    "condition_id",
    "order_id",
    "order_status",
    "updated_at",
    "temperature_metric",
    # PR D0b (Finding D0/D2-wire, Part-2 audit, 2026-05-27): durable
    # authority projection. NULL on legacy rows (ALTER TABLE ADD COLUMN
    # default). Populated by build_position_current_projection() from
    # Position.fill_authority / .chain_shares / .chain_verified_at /
    # .last_chain_absence_observed_at. recovery_authority is derived at
    # rescue time and persisted alongside; for non-rescue projections it
    # stays NULL.
    "fill_authority",
    "recovery_authority",
    "chain_shares",
    # F1 (docs/findings_2026_05_28.md §F1, 2026-05-28): chain-observed
    # economics on position_current so balance-only rescued positions
    # survive daemon restart with the right exposure on
    # `Position.effective_exposure()`. ALTER TABLE ADD COLUMN is additive
    # on legacy DBs via _ensure_position_current_authority_columns.
    "chain_avg_price",
    "chain_cost_basis_usd",
    "chain_seen_at",
    "chain_absence_at",
    # BUG #128 (SEV1, 2026-06-02): durable realized-P&L projection. Pre-fix the
    # realized P&L computed in src.state.portfolio._compute_realized_pnl lived
    # ONLY on the in-memory Position object + positions.json recent_exits[]; a
    # filled+settled order left NO queryable position_current P&L record. These
    # columns persist the close economics through build_position_current_projection
    # so every canonical close path (settlement + economic close) records P&L
    # durably. NULL on open/legacy rows; ALTER TABLE ADD COLUMN additive on legacy
    # DBs via _ensure_position_current_authority_columns.
    "realized_pnl_usd",
    "exit_price",
    "settlement_price",
    "settled_at",
    "exit_reason",
)


def normalize_position_event_env(value: object, *, default: str | None = None) -> str:
    env = str(value if value not in (None, "") else (default or "")).strip().lower()
    if env not in POSITION_EVENT_ENVS:
        raise ValueError(f"position event env={env!r} is invalid")
    return env


def ordered_values(payload: dict, columns: tuple[str, ...]) -> tuple:
    return tuple(payload.get(column) for column in columns)


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def require_payload_fields(payload: dict, columns: tuple[str, ...], *, label: str) -> None:
    missing = [column for column in columns if column not in payload]
    if missing:
        raise ValueError(f"{label} missing fields: {missing}")


def validate_event_projection_pair(event: dict, projection: dict) -> None:
    if event.get("position_id") != projection.get("position_id"):
        raise ValueError("event/projection position_id mismatch")
    if event.get("strategy_key") != projection.get("strategy_key"):
        raise ValueError("event/projection strategy_key mismatch")
    phase_after = event.get("phase_after")
    if phase_after and projection.get("phase") and phase_after != projection.get("phase"):
        raise ValueError("event/projection phase mismatch")
    snapshot_id = event.get("snapshot_id")
    decision_snapshot_id = projection.get("decision_snapshot_id")
    if snapshot_id and decision_snapshot_id and snapshot_id != decision_snapshot_id:
        raise ValueError("event/projection snapshot mismatch")
    order_id = event.get("order_id")
    projection_order_id = projection.get("order_id")
    if order_id and projection_order_id and order_id != projection_order_id:
        raise ValueError("event/projection order_id mismatch")


def validate_event_projection_batch(events: list[dict], projection: dict) -> None:
    if not events:
        raise ValueError("event batch must not be empty")
    for event in events:
        if event.get("position_id") != projection.get("position_id"):
            raise ValueError("event/projection position_id mismatch")
        if event.get("strategy_key") != projection.get("strategy_key"):
            raise ValueError("event/projection strategy_key mismatch")
    final_phase = events[-1].get("phase_after")
    if final_phase and projection.get("phase") and final_phase != projection.get("phase"):
        raise ValueError("event/projection phase mismatch")


# F109 (2026-05-17) — phases for which at most ONE position_current row may
# exist per token_id at a time. Mirrors db.OPEN_EXPOSURE_PHASES but is duplicated
# locally so projection.py does not pull in src.state.db (cycle risk).
_F109_OPEN_PHASES = ("pending_entry", "active", "day0_window", "pending_exit", "unknown")


class DuplicatePositionOpenError(RuntimeError):
    """Raised when upsert_position_current detects an existing live row on the
    same token_id during a fresh INSERT.

    Carries the existing position_id so callers can log/correlate. The error
    is intentionally a hard failure: by the time projection.py runs the
    caller has already written trade_decisions + execution_report in the
    same SAVEPOINT. Returning the existing position_id quietly would leave
    those rows dangling. Raise cleanly; let SAVEPOINT rollback undo
    everything.
    """

    def __init__(self, *, attempted_position_id: str, existing_position_id: str, token_id: str):
        super().__init__(
            f"F109: token={token_id} already has open-phase row "
            f"position_id={existing_position_id}; refusing to open a parallel row "
            f"position_id={attempted_position_id}"
        )
        self.attempted_position_id = attempted_position_id
        self.existing_position_id = existing_position_id
        self.token_id = token_id


def _find_existing_open_row(
    conn: sqlite3.Connection, *, token_id: str, exclude_position_id: str
) -> str | None:
    """Return position_id of any existing open-phase row for the same token.

    Returns None if no other open-phase row exists. Excludes the candidate
    itself so that pure UPSERT-on-same-position_id paths are unaffected.
    """
    row = conn.execute(
        """
        SELECT position_id FROM position_current
         WHERE token_id = ?
           AND position_id != ?
           AND phase IN (?, ?, ?, ?, ?)
         LIMIT 1
        """,
        (token_id, exclude_position_id, *_F109_OPEN_PHASES),
    ).fetchone()
    return str(row[0]) if row is not None else None


class NullConditionIdOnOpenPhaseError(ValueError):
    """Raised when upsert_position_current detects condition_id=NULL on an open-phase row.

    Fix B (2026-05-19): positions in open phases require condition_id for CTF
    operations (balanceOf, redeemPositions). A NULL at this phase means the entry
    write-path did not populate it — fail closed rather than silently allowing a
    row that will break exit/redemption later.

    Closed phases (voided, settled, admin_closed, etc.) remain permissive because
    legacy rows may predate the condition_id backfill.
    """

    def __init__(self, *, position_id: str, phase: str):
        super().__init__(
            f"NullConditionId: position_id={position_id!r} phase={phase!r} — "
            f"condition_id must be non-empty for open-phase position writes. "
            f"Check that the entry write-path populates condition_id from the executable_market_snapshot."
        )
        self.position_id = position_id
        self.phase = phase


# Phases that require a non-empty condition_id. These are the phases where
# the position is still active and CTF operations may be needed.
_CONDITION_ID_REQUIRED_PHASES = frozenset(_F109_OPEN_PHASES)


@capability("canonical_position_write", lease=True)
def upsert_position_current(conn: sqlite3.Connection, projection: dict) -> None:
    # F109 writer-side idempotency check (2026-05-17).
    # Runs before INSERT so the race window with the partial UNIQUE INDEX is
    # tight. If a same-token open-phase row exists with a *different*
    # position_id, this is a duplicate-open attempt — raise. The partial
    # UNIQUE INDEX added by migration 202605_position_current_idempotent_open_per_token
    # is the hard floor that catches any race that slips past this check;
    # sqlite3.IntegrityError from the INDEX will propagate through the
    # caller's SAVEPOINT and roll back the entire entry.
    candidate_phase = str(projection.get("phase") or "")
    candidate_token = projection.get("token_id")
    candidate_position_id = str(projection.get("position_id") or "")

    # Fix B (2026-05-19): fail-closed guard for NULL condition_id on open phases.
    # This makes the category of "CTF operation fails because condition_id is NULL"
    # impossible at write-time rather than detectable only at sell/redeem time.
    if candidate_phase in _CONDITION_ID_REQUIRED_PHASES:
        candidate_condition_id = projection.get("condition_id")
        if not candidate_condition_id:
            raise NullConditionIdOnOpenPhaseError(
                position_id=candidate_position_id,
                phase=candidate_phase,
            )

    if (
        candidate_phase in _F109_OPEN_PHASES
        and candidate_token
        and candidate_position_id
    ):
        existing = _find_existing_open_row(
            conn,
            token_id=str(candidate_token),
            exclude_position_id=candidate_position_id,
        )
        if existing is not None:
            raise DuplicatePositionOpenError(
                attempted_position_id=candidate_position_id,
                existing_position_id=existing,
                token_id=str(candidate_token),
            )
    # PR #352 (Part-3 audit, bot #7 on PR #351 + Part-4 Finding 1, 2026-05-27):
    # the ON CONFLICT update set is GENERATED from CANONICAL_POSITION_CURRENT_COLUMNS
    # (minus the position_id conflict key), not hand-maintained. The original
    # hand-written list omitted the D0b authority columns (fill_authority,
    # recovery_authority, chain_shares, chain_seen_at, chain_absence_at), so the
    # rescue path — which UPDATEs an existing pending-entry row — wrote authority
    # on first INSERT only and left it stale on every conflict, defeating the
    # durable-authority guarantee. Generating the set makes that drift category
    # impossible: any future canonical column is updated on conflict automatically.
    _update_cols = [c for c in CANONICAL_POSITION_CURRENT_COLUMNS if c != "position_id"]
    _update_set = ",\n            ".join(f"{c}=excluded.{c}" for c in _update_cols)
    conn.execute(
        f"""
        INSERT INTO position_current ({", ".join(CANONICAL_POSITION_CURRENT_COLUMNS)})
        VALUES ({", ".join(["?"] * len(CANONICAL_POSITION_CURRENT_COLUMNS))})
        ON CONFLICT(position_id) DO UPDATE SET
            {_update_set}
        """,
        ordered_values(projection, CANONICAL_POSITION_CURRENT_COLUMNS),
    )
