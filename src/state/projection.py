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
    "last_monitor_prob",
    "last_monitor_edge",
    "last_monitor_market_price",
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
_CONDITION_ID_REQUIRED_PHASES = frozenset(
    {"pending_entry", "active", "day0_window", "pending_exit", "unknown"}
)


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
    conn.execute(
        f"""
        INSERT INTO position_current ({", ".join(CANONICAL_POSITION_CURRENT_COLUMNS)})
        VALUES ({", ".join(["?"] * len(CANONICAL_POSITION_CURRENT_COLUMNS))})
        ON CONFLICT(position_id) DO UPDATE SET
            phase=excluded.phase,
            trade_id=excluded.trade_id,
            market_id=excluded.market_id,
            city=excluded.city,
            cluster=excluded.cluster,
            target_date=excluded.target_date,
            bin_label=excluded.bin_label,
            direction=excluded.direction,
            unit=excluded.unit,
            size_usd=excluded.size_usd,
            shares=excluded.shares,
            cost_basis_usd=excluded.cost_basis_usd,
            entry_price=excluded.entry_price,
            p_posterior=excluded.p_posterior,
            last_monitor_prob=excluded.last_monitor_prob,
            last_monitor_edge=excluded.last_monitor_edge,
            last_monitor_market_price=excluded.last_monitor_market_price,
            decision_snapshot_id=excluded.decision_snapshot_id,
            entry_method=excluded.entry_method,
            strategy_key=excluded.strategy_key,
            edge_source=excluded.edge_source,
            discovery_mode=excluded.discovery_mode,
            chain_state=excluded.chain_state,
            token_id=excluded.token_id,
            no_token_id=excluded.no_token_id,
            condition_id=excluded.condition_id,
            order_id=excluded.order_id,
            order_status=excluded.order_status,
            updated_at=excluded.updated_at,
            temperature_metric=excluded.temperature_metric
        """,
        ordered_values(projection, CANONICAL_POSITION_CURRENT_COLUMNS),
    )
