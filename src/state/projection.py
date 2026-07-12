from __future__ import annotations

import sqlite3

from src.architecture.decorators import capability
from src.state.lifecycle_manager import LifecyclePhase, TERMINAL_STATES


POSITION_EVENT_ENVS = ("live", "test", "replay", "backtest")


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
    "last_monitor_best_bid",
    "last_monitor_best_ask",
    "last_monitor_market_vig",
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
    # F1 (docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F1, 2026-05-28): chain-observed
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
         WHERE (token_id = ? OR no_token_id = ?)
           AND position_id != ?
           AND phase IN (?, ?, ?, ?, ?)
         LIMIT 1
        """,
        (token_id, token_id, exclude_position_id, *_F109_OPEN_PHASES),
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


class MissingRealizedPnlOnCloseError(ValueError):
    """Raised when upsert_position_current detects a position transitioning
    into an economically_closed/settled phase for the first time without a
    realized_pnl_usd value.

    R0-a (close-economics unification, 2026-07-08): this is the structural
    backstop for the bug class where a close path builds its own
    Position/SimpleNamespace stand-in and forgets to attach "pnl" before
    projecting (Bug A/B, 2026-07-07 — ~91% of settled positions were left
    with NULL/0.0 realized_pnl_usd). Every close path must compute
    realized_pnl_usd via src.state.close_economics.compute_realized_pnl_usd
    before reaching this write; this makes forgetting it a loud write-time
    failure instead of a silent NULL discovered only by later audit.

    Only the write that performs the FIRST transition into a close phase is
    checked — a position already sitting in an absorbing phase (e.g. a
    chain-mirror size correction re-touching an already-settled row, see
    apply_size_correction_finding) is not re-checked here, so historical
    legacy rows with a pre-existing NULL do not start raising on unrelated
    re-writes. Use the R0-a backfill script to repair those.
    """

    def __init__(self, *, position_id: str, phase: str):
        super().__init__(
            f"MissingRealizedPnl: position_id={position_id!r} phase={phase!r} — "
            f"realized_pnl_usd must be non-NULL when a position first transitions "
            f"into phase={phase!r}. Compute it via "
            f"src.state.close_economics.compute_realized_pnl_usd before building the projection."
        )
        self.position_id = position_id
        self.phase = phase


# Phases that require a non-empty condition_id. These are the phases where
# the position is still active and CTF operations may be needed.
_CONDITION_ID_REQUIRED_PHASES = frozenset(_F109_OPEN_PHASES)
# R0-a: the two phases build_position_current_projection / chain_mirror_reconciler
# treat as "this position has a durable close economics record" (Bug #128 /
# Bug A/B). VOIDED and ADMIN_CLOSED are deliberately excluded: those closes
# are not economic close-and-realize-P&L events (a voided market or an admin
# manual close may have no meaningful realized economics), so they are out of
# this guard's scope.
_REALIZED_PNL_REQUIRED_PHASES = frozenset(
    {LifecyclePhase.ECONOMICALLY_CLOSED.value, LifecyclePhase.SETTLED.value}
)
# T5 (docs/rebuild/quarantine_excision_2026-07-11.md): 'quarantined' retired
# from LifecyclePhase; the T5 schema migration has run and the
# position_current CHECK no longer admits the literal, so the mixed-epoch
# bridge that used to keep the bare string literal in this raw-SQL reopen
# guard is retired.
_ABSORBING_POSITION_PHASES = frozenset(
    set(TERMINAL_STATES) | {LifecyclePhase.ECONOMICALLY_CLOSED.value}
)
_MONITOR_REFRESH_PRESERVED_COLUMNS = frozenset(
    {
        "market_id",
        "token_id",
        "no_token_id",
        "condition_id",
        "order_id",
        "size_usd",
        "shares",
        "cost_basis_usd",
        "entry_price",
        "p_posterior",
        "entry_ci_width",
        "entry_method",
        "fill_authority",
        "recovery_authority",
        "chain_state",
        "chain_shares",
        "chain_avg_price",
        "chain_cost_basis_usd",
        "chain_seen_at",
        "chain_absence_at",
    }
)
_MONITOR_SNAPSHOT_COLUMNS = frozenset(
    {
        "last_monitor_prob",
        "last_monitor_prob_is_fresh",
        "last_monitor_edge",
        "last_monitor_market_price",
        "last_monitor_market_price_is_fresh",
        "last_monitor_best_bid",
        "last_monitor_best_ask",
        "last_monitor_market_vig",
    }
)
_CHAIN_PROJECTION_EVENT_TYPES = frozenset({"CHAIN_SIZE_CORRECTED", "CHAIN_SYNCED"})
_CHAIN_OBSERVATION_COLUMNS = (
    "chain_state",
    "chain_shares",
    "chain_avg_price",
    "chain_cost_basis_usd",
    "chain_seen_at",
    "chain_absence_at",
)
_PENDING_EXIT_AUTHORITY_PRESERVING_EVENT_TYPES = frozenset(
    {"MONITOR_REFRESHED", *_CHAIN_PROJECTION_EVENT_TYPES}
)
_MONITOR_REFRESH_PROTECTED_PENDING_EXIT_STATUSES = frozenset(
    {
        "exit_intent",
        "retry_pending",
        "backoff_exhausted",
        "sell_pending",
        "sell_placed",
        "sell_pending_confirmation",
    }
)
_MONITOR_REFRESH_PROTECTED_PENDING_EXIT_COLUMNS = frozenset(
    {
        "phase",
        "order_status",
        "exit_reason",
        "exit_retry_count",
        "next_exit_retry_at",
    }
)


def _preserve_existing_pending_exit_authority(
    conn: sqlite3.Connection, projection: dict
) -> dict:
    if (
        projection.get("_canonical_event_type")
        not in _PENDING_EXIT_AUTHORITY_PRESERVING_EVENT_TYPES
    ):
        return projection
    position_id = str(projection.get("position_id") or "")
    if not position_id:
        return projection
    current_columns = table_columns(conn, "position_current")
    selected = tuple(
        column
        for column in CANONICAL_POSITION_CURRENT_COLUMNS
        if column in _MONITOR_REFRESH_PROTECTED_PENDING_EXIT_COLUMNS
        and column in current_columns
    )
    if not selected:
        return projection
    row = conn.execute(
        f"SELECT {', '.join(selected)} FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    if row is None:
        return projection
    current = {column: row[index] for index, column in enumerate(selected)}
    if (
        str(current.get("phase") or "") == LifecyclePhase.PENDING_EXIT.value
        and str(projection.get("phase") or "") != LifecyclePhase.PENDING_EXIT.value
        and str(current.get("order_status") or "")
        in _MONITOR_REFRESH_PROTECTED_PENDING_EXIT_STATUSES
    ):
        merged = dict(projection)
        for column in selected:
            merged[column] = current[column]
        return merged
    return projection


def _preserve_existing_monitor_refresh_authority(
    conn: sqlite3.Connection, projection: dict
) -> dict:
    if projection.get("_canonical_event_type") != "MONITOR_REFRESHED":
        return projection
    position_id = str(projection.get("position_id") or "")
    if not position_id:
        return projection
    current_columns = table_columns(conn, "position_current")
    preserved = tuple(
        column
        for column in CANONICAL_POSITION_CURRENT_COLUMNS
        if column in _MONITOR_REFRESH_PRESERVED_COLUMNS and column in current_columns
    )
    pending_exit_guard_columns = tuple(
        column
        for column in CANONICAL_POSITION_CURRENT_COLUMNS
        if column in _MONITOR_REFRESH_PROTECTED_PENDING_EXIT_COLUMNS
        and column in current_columns
    )
    selected = tuple(dict.fromkeys((*preserved, *pending_exit_guard_columns)))
    if not selected:
        return projection
    row = conn.execute(
        f"SELECT {', '.join(selected)} FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    if row is None:
        return projection
    merged = dict(projection)
    current = {column: row[index] for index, column in enumerate(selected)}
    for column in preserved:
        merged[column] = current[column]
    if (
        str(current.get("phase") or "") == LifecyclePhase.PENDING_EXIT.value
        and str(projection.get("phase") or "") != LifecyclePhase.PENDING_EXIT.value
        and str(current.get("order_status") or "")
        in _MONITOR_REFRESH_PROTECTED_PENDING_EXIT_STATUSES
    ):
        for column in pending_exit_guard_columns:
            merged[column] = current[column]
    if _has_positive_chain_observation(merged) and str(
        merged.get("chain_state") or ""
    ) in {"", "unknown", "local_only"}:
        merged["chain_state"] = "synced"
    return merged


def _preserve_existing_chain_authority_without_new_observation(
    conn: sqlite3.Connection, projection: dict
) -> dict:
    """Keep newer positive chain truth across stale open-position replays.

    Recovery and fill bridges may replay an older position projection after a
    chain reconciliation has already observed the held token.  A replay with
    neither ``chain_seen_at`` nor ``chain_absence_at`` carries no new chain
    observation, so it cannot erase the existing positive observation.  A real
    positive or negative observation carries its own timestamp and remains
    authoritative.  Terminal projections are excluded because close/settlement
    writers own their chain-field semantics.
    """

    if str(projection.get("phase") or "") not in _F109_OPEN_PHASES:
        return projection
    if str(projection.get("chain_seen_at") or "") or str(
        projection.get("chain_absence_at") or ""
    ):
        return projection
    position_id = str(projection.get("position_id") or "")
    if not position_id:
        return projection
    current_columns = table_columns(conn, "position_current")
    selected = tuple(
        column for column in _CHAIN_OBSERVATION_COLUMNS if column in current_columns
    )
    if "chain_seen_at" not in selected or "chain_shares" not in selected:
        return projection
    row = conn.execute(
        f"SELECT {', '.join(selected)} FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    if row is None:
        return projection
    current = {column: row[index] for index, column in enumerate(selected)}
    if not _has_positive_chain_observation(current):
        return projection
    merged = dict(projection)
    for column in selected:
        merged[column] = current[column]
    return merged


def _preserve_existing_monitor_snapshot_for_chain_projection(
    conn: sqlite3.Connection, projection: dict
) -> dict:
    """Keep fresh monitor truth across chain-only projection writes.

    Chain reconciliation events update venue/chain exposure truth. They are not a
    monitor refresh and must not erase the last fresh belief/quote snapshot just
    because the in-memory reconciliation object did not carry monitor fields.
    """

    if projection.get("_canonical_event_type") not in _CHAIN_PROJECTION_EVENT_TYPES:
        return projection
    position_id = str(projection.get("position_id") or "")
    if not position_id:
        return projection
    current_columns = table_columns(conn, "position_current")
    preserved = tuple(
        column
        for column in CANONICAL_POSITION_CURRENT_COLUMNS
        if column in _MONITOR_SNAPSHOT_COLUMNS and column in current_columns
    )
    if not preserved:
        return projection
    row = conn.execute(
        f"SELECT {', '.join(preserved)} FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    if row is None:
        return projection

    current = {column: row[index] for index, column in enumerate(preserved)}
    merged = dict(projection)
    if bool(current.get("last_monitor_prob_is_fresh")):
        for column in ("last_monitor_prob", "last_monitor_prob_is_fresh", "last_monitor_edge"):
            if column in current:
                merged[column] = current[column]
    if bool(current.get("last_monitor_market_price_is_fresh")):
        for column in (
            "last_monitor_market_price",
            "last_monitor_market_price_is_fresh",
            "last_monitor_best_bid",
            "last_monitor_best_ask",
            "last_monitor_market_vig",
        ):
            if column in current:
                merged[column] = current[column]
    return merged


def _has_positive_chain_observation(projection: dict) -> bool:
    try:
        chain_shares = float(projection.get("chain_shares") or 0.0)
        chain_avg_price = float(projection.get("chain_avg_price") or 0.0)
        chain_cost_basis = float(projection.get("chain_cost_basis_usd") or 0.0)
    except (TypeError, ValueError):
        return False
    if chain_shares <= 0.0:
        return False
    if str(projection.get("chain_absence_at") or ""):
        return False
    if not str(projection.get("chain_seen_at") or "") and (
        chain_avg_price <= 0.0 or chain_cost_basis <= 0.0
    ):
        return False
    return True


def _projection_allows_terminal_restore_exposure(projection: dict) -> bool:
    """T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT PHASE
    LAW): a confirmed-fill/chain-absence-conflict or terminal-restore-exposure
    repair (src.state.chain_reconciliation._preserve_confirmed_fill_chain_absence_conflict
    / _restore_terminal_chain_exposure_if_available / the false-phantom-void
    inline repair) now writes the position's TRUE phase (active/pending_exit)
    directly — never a quarantine scar — even when the EXISTING row is a
    terminal/absorbing phase (e.g. voided) that chain/local evidence proves
    was wrong. Pre-T5 this same repair wrote phase='quarantined' (itself an
    absorbing phase per _ABSORBING_POSITION_PHASES), so the F109 guard's
    outer condition never even fired for it; writing the TRUE open phase now
    needs its own escape hatch, keyed on the same REVIEW_REQUIRED event type +
    positive chain/local evidence signal the redecision-quarantine escape
    hatch above already uses."""
    if str(projection.get("phase") or "") not in {"active", "pending_exit"}:
        return False
    if str(projection.get("_canonical_event_type") or "") != "REVIEW_REQUIRED":
        return False
    for field in ("chain_shares", "shares"):
        try:
            value = float(projection.get(field) or 0.0)
        except (TypeError, ValueError):
            continue
        if value > 0.01:
            return True
    return False


@capability("canonical_position_write", lease=True)
def upsert_position_current(conn: sqlite3.Connection, projection: dict) -> None:
    projection = _preserve_existing_monitor_refresh_authority(conn, projection)
    projection = _preserve_existing_chain_authority_without_new_observation(
        conn, projection
    )
    projection = _preserve_existing_pending_exit_authority(conn, projection)
    projection = _preserve_existing_monitor_snapshot_for_chain_projection(conn, projection)
    # F109 writer-side idempotency check (2026-05-17).
    # Runs before INSERT so the race window with the partial UNIQUE INDEX is
    # tight. If a same-token open-phase row exists with a *different*
    # position_id, this is a duplicate-open attempt — raise. The partial
    # UNIQUE INDEX added by migration 202605_position_current_idempotent_open_per_token
    # is the hard floor that catches any race that slips past this check;
    # sqlite3.IntegrityError from the INDEX will propagate through the
    # caller's SAVEPOINT and roll back the entire entry.
    candidate_phase = str(projection.get("phase") or "")
    candidate_token = projection.get("token_id") or projection.get("no_token_id")
    candidate_position_id = str(projection.get("position_id") or "")
    if candidate_position_id and candidate_phase not in _ABSORBING_POSITION_PHASES:
        existing_phase_row = conn.execute(
            "SELECT phase FROM position_current WHERE position_id = ?",
            (candidate_position_id,),
        ).fetchone()
        existing_phase = str(existing_phase_row[0] if existing_phase_row else "")
        if existing_phase in _ABSORBING_POSITION_PHASES and not (
            _projection_allows_terminal_restore_exposure(projection)
        ):
            raise ValueError(
                "position_current absorbing non-open phase cannot be reopened: "
                f"position_id={candidate_position_id!r} "
                f"existing_phase={existing_phase!r} candidate_phase={candidate_phase!r}"
            )

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

    # R0-a (close-economics unification, 2026-07-08): fail-closed guard for a
    # position transitioning into a close phase (economically_closed/settled)
    # for the first time without realized_pnl_usd. See
    # MissingRealizedPnlOnCloseError. Only checked when realized_pnl_usd is
    # actually missing (cheap on the happy path) and only for the write that
    # performs the FIRST transition into an absorbing phase — a re-write of an
    # already-absorbing row (e.g. a chain-mirror size correction touching an
    # already-settled position) is not re-checked, so legacy NULL rows do not
    # start raising on unrelated writes.
    if (
        candidate_phase in _REALIZED_PNL_REQUIRED_PHASES
        and projection.get("realized_pnl_usd") is None
        and candidate_position_id
    ):
        prior_phase_row = conn.execute(
            "SELECT phase FROM position_current WHERE position_id = ?",
            (candidate_position_id,),
        ).fetchone()
        prior_phase = str(prior_phase_row[0] if prior_phase_row else "")
        if prior_phase not in _ABSORBING_POSITION_PHASES:
            raise MissingRealizedPnlOnCloseError(
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
