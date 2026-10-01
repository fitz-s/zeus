# Created: 2026-05-31
# Last reused or audited: 2026-10-01
# Authority basis: PLAN_CONTINUOUS_REDECISION_MAX_ALPHA_2026-05-31.md (v2, review-resolved) +
#   GOAL #36 expanded (continuous entry+exit, evidence-gated). Implements P1 (belief cache) + P2
#   (cheap screen + enqueue). screen_exit/screen_exit_cancel deleted Wave 3 (zero live callers —
#   exit path is Position.evaluate_exit in src/state/portfolio.py).
#   2026-06-12 RESURRECTION (operator: "continuous redecision没有作用中"): P1 re-enabled
#   DEADLOCK-FREE — the belief is buffered in-process by the kernel (no DB write there) and
#   persisted by the reactor through its EXISTING world conn inside the open SAVEPOINT (NOT a second
#   connection, NOT a separate commit). P2 screen wired to a scheduler job + the reactor now CONSUMES
#   EDLI_REDECISION_PENDING. Flat constants replaced by the canonical price-dependent fee model +
#   documented economic bases.
#   2026-10-01: resting-order value pulls (belief-decay, moved-book, confirmed-value refresh,
#   family optimum shift) deleted; open ENTRY rest value is the C3 standing valuation.
#   2026-06-17: entry admission cooldown keys use stable market identity
#   (city,target_date,metric,bin_label,direction), not dynamic EDLI family hashes.
#
# DAEMON-SAFE BACKING (critical): assert_db_matches_registry (table_registry.py:285) is STRICT
# set-equality on TABLE NAMES (extra COLUMNS are permitted — subset semantics). So this module adds
# NO new table: the belief cache reuses the already-registered probability_trace_fact (synthesized
# 'edli_belief:' decision_id; trace_status='complete') plus an additive condition_ids_json column
# (idempotent ALTER in db.py; column-subset-safe). The act-once-per-edge dedup is IN-MEMORY
# (reactor-held acted_state dict), not a table. Submit-safe: never submits an order directly; it
# screens cached belief × fresh price and returns re-decisions for the reactor to route through the
# existing pending cert path (so _refresh_pending_family_snapshots fires just-in-time → fresh price;
# critic SEV-1 stale-price hole closed structurally).
"""Continuous re-decision: cached belief × fresh price → cheap edge screen → enqueue + evidence exit."""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from contextlib import contextmanager, nullcontext
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from src.contracts.probability_arithmetic import one_minus
from src.data.replacement_forecast_readiness import (
    SOURCE_ID as LIVE_REPLACEMENT_POSTERIOR_SOURCE_ID,
)
from src.events.forecast_completeness import posterior_admits_spine_members
from src.events.opportunity_event import OpportunityEvent

logger = logging.getLogger(__name__)


@dataclass
class SqliteDeadlineFence:
    """One screen-cycle deadline; an old handler cannot interrupt a later cycle."""

    deadline_monotonic: float
    generation: int
    stage: str = "belief_scan"
    cancel_requested: Callable[[], bool] | None = None
    active_generation: int = field(init=False)

    def __post_init__(self) -> None:
        self.active_generation = self.generation

    def expired(self) -> bool:
        if self.active_generation != self.generation:
            return False
        if time.monotonic() >= self.deadline_monotonic:
            return True
        if self.cancel_requested is None:
            return False
        try:
            return bool(self.cancel_requested())
        except Exception:
            # A monitor-priority probe is a safety boundary.  An unreadable
            # probe cannot authorize a broad read to keep running.
            return True

    def deactivate(self) -> None:
        self.active_generation = -1


_sqlite_deadline_state: dict[sqlite3.Connection, tuple[int, int, SqliteDeadlineFence]] = {}


@contextmanager
def sqlite_deadline_bound(
    conn: sqlite3.Connection,
    fence: SqliteDeadlineFence,
):
    """Bound busy waits and VM work, restoring an outer handler exactly once."""

    if fence.expired():
        raise sqlite3.OperationalError("interrupted")
    nested = _sqlite_deadline_state.get(conn)
    if nested is not None:
        depth, previous_busy, outer_fence = nested
        _sqlite_deadline_state[conn] = (depth + 1, previous_busy, outer_fence)
        try:
            if fence.expired():
                raise sqlite3.OperationalError("interrupted")
            yield
        finally:
            depth, previous_busy, outer_fence = _sqlite_deadline_state[conn]
            _sqlite_deadline_state[conn] = (depth - 1, previous_busy, outer_fence)
        return
    previous_busy = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
    remaining_ms = max(
        1,
        int((fence.deadline_monotonic - time.monotonic()) * 1_000),
    )
    conn.execute(f"PRAGMA busy_timeout = {remaining_ms}")
    conn.set_progress_handler(lambda: int(fence.expired()), 1_000)
    _sqlite_deadline_state[conn] = (1, previous_busy, fence)
    try:
        yield
    finally:
        _sqlite_deadline_state.pop(conn, None)
        try:
            if conn.in_transaction:
                conn.rollback()
            conn.set_progress_handler(None, 0)
            conn.execute(f"PRAGMA busy_timeout = {previous_busy}")
        except sqlite3.Error:
            # The caller may have closed its short-lived RO connection first.
            pass


def _fee_at(price: float) -> float:
    """Canonical price-dependent Polymarket taker fee for ``price`` (probability units).

    Operator law: no unsupported hardcoded values. The flat 1¢ haircut the module shipped with
    over-charged near 0.5 (true fee 1.25¢) and over-charged ~3x near 0.9 (true fee 0.45¢) — both
    distort the edge screen. The single fee authority is ``execution_price.polymarket_fee``
    (fee_rate * p * (1-p); docs.polymarket.com/trading/fees). Fail-soft to the 0.5 worst case
    (max of the parabola) for a price outside (0,1) so the screen stays CONSERVATIVE, never
    fabricating edge from a degenerate quote."""
    from src.contracts.execution_price import polymarket_fee

    if not (0.0 < float(price) < 1.0):
        return polymarket_fee(0.5)  # parabola maximum = most conservative haircut
    return polymarket_fee(float(price))


# Default tick size (probability units) used only when an older test/schema row
# does not carry executable_market_snapshots.min_tick_size. Live screens must use
# the per-book tick: current weather markets commonly quote at 0.001 near the
# tails, and charging a fixed 0.01 tick makes cheap YES opportunities vanish.
TICK_SIZE: float = 0.01
# IMPROVE_DELTA economic basis: the smallest edge improvement worth re-deciding on. A re-decision
# costs a full cert run + a potential cancel/replace round-trip; an improvement below the round-trip
# friction is noise. Friction floor = 2*tick (the book must move at least one tick AND our re-quote
# clears one tick) plus one fee-quantum of slack ≈ the worst-case fee swing across a one-tick move.
# 2*0.01 = 0.02. This REPLACES the prior bare 0.02 magic number with a derived quantity.
IMPROVE_DELTA: float = 2.0 * TICK_SIZE
# Submit-side quote freshness bound. Resting ENTRY orders are never cancelled by age; their
# value is owned by the C3 standing valuation (src.execution.staleness_cancel).
PRE_SUBMIT_MAX_QUOTE_AGE_MS: float = 1000.0
# Price-channel held/candidate quote refresh writes execution_feasibility_evidence
# every scheduler tick, while executable_market_snapshots only moves on substrate
# refresh. Continuous redecision may consume the former as a live book witness,
# but only under a short TTL so a quiet or failed sidecar cannot fabricate fresh
# price from an old row.
FEASIBILITY_QUOTE_FRESHNESS_SECONDS: float = 90.0
# The "a real maker window happened" floor: a terminal unfilled ENTRY rest that
# rested at least this long arms rest-then-cross escalation for its token
# (event_reactor_adapter._family_rest_state). It cancels nothing.
REST_VALUE_REFRESH_MIN_AGE_SECONDS: float = 5.0 * 60.0
REDECISION_EVENT_TYPE: str = "EDLI_REDECISION_PENDING"
_BELIEF_PREFIX: str = "edli_belief:"
_EPS: float = 1e-9
_DEFAULT_LATEST_BELIEF_SCAN_LIMIT: int = 5_000
EntryScreenKey = tuple[str, str, str]
StableEntryScreenKey = tuple[str, str, str, str, str]
FamilyRedecisionScreenKey = tuple[str, str, str, str]
RedecisionScreenKey = EntryScreenKey | StableEntryScreenKey | FamilyRedecisionScreenKey
FULL_DECISION_FAMILY_REFUTATION_COOLDOWN_SECONDS: float = 30.0 * 60.0

_TERMINAL_NO_VALUE_SQL = """
    (
        rejection_stage = 'TRADE_SCORE'
        AND (
            rejection_reason IN ('TRADE_SCORE_NON_POSITIVE', 'TRADE_SCORE_BLOCKED')
         OR rejection_reason LIKE 'TRADE_SCORE_NON_POSITIVE:%'
         OR rejection_reason LIKE 'TRADE_SCORE_BLOCKED:%'
         OR rejection_reason = 'FDR_REJECTED'
         OR rejection_reason LIKE 'FDR_REJECTED:%'
         OR rejection_reason LIKE 'EVENT_BOUND_ALL_CANDIDATES_REJECTED:%'
         OR rejection_reason LIKE 'EVENT_BOUND_CANDIDATE_REJECTED:%'
         OR rejection_reason LIKE 'SUBMIT_ABORTED_EDGE_REVERSED:%'
        )
    )
 OR (
        rejection_stage = 'EXECUTION_RECEIPT'
        AND (
            rejection_reason LIKE 'TAKER_QUALITY_PROOF_NOT_PASSED:%'
         OR rejection_reason LIKE 'entry_taker_quality:%'
        )
    )
 OR (
        rejection_stage = 'EXECUTOR_EXPRESSIBILITY'
        AND (
            rejection_reason LIKE 'EDLI_LIVE_CERTIFICATE_BUILD_FAILED:NO_SUBMIT_CERTIFICATE_REJECTED:%'
        )
    )
"""
_FORECAST_ONLY_NO_VALUE_REFUTATION_GUARD_SQL = "COALESCE(executable_snapshot_id, '') = ''"
_NO_VALUE_FORECAST_EVENT_TYPES = frozenset(
    {"FORECAST_SNAPSHOT_READY", REDECISION_EVENT_TYPE}
)


@dataclass(frozen=True)
class PriceQuote:
    price: float
    freshness_deadline: str  # ISO-8601 with offset
    tick_size: float = TICK_SIZE


@dataclass(frozen=True)
class CachedBelief:
    family_id: str
    city: str
    target_date: str
    snapshot_id: str
    calibrator_model_hash: str
    bin_labels: list[str]
    p_posterior_vec: list[float]
    recorded_at: str
    # Parallel to bin_labels: the executable condition_id per bin (empty string when the bin had
    # no market at decision time). The P2 screen needs this to join a cached belief to the freshest
    # executable_market_snapshots row (keyed by condition_id). Defaulted empty for backward-compat
    # with rows cached before the resurrection.
    condition_ids: list[str] = None  # type: ignore[assignment]
    # Parallel to bin_labels: conservative lower-bound probability for each side.
    # Entry redecision must screen on this, not on point posterior optimism.
    q_lcb_yes_vec: list[float | None] | None = None
    q_lcb_no_vec: list[float | None] | None = None
    # The family's temperature metric ("high"/"low"). Parsed from the family_id (position 4 of the
    # pipe-separated id) so the P2 job can build the (city, target_date, metric) family key for the
    # FSR re-emit restriction without re-deriving topology. Empty when unparseable.
    metric: str = ""
    # Certificate validity across forecast issues (FINAL_SPEC §certificate validity). Both ISO-8601
    # UTC, defaulted None so every existing constructor / cached-before-this-change row is preserved.
    #   valid_until: the instant past which this belief is no longer a valid decision basis —
    #     min(τ_next − Δ_cancel, market close, probability freshness). Enqueue/screen treat a belief
    #     past valid_until exactly like a stale-freshness reject (CERT_EXPIRED); a resting maker order
    #     is pulled once now is within Δ_cancel of it (CERT_EXPIRY_PULL).
    #   next_authoritative_issue_at: the raw τ_next (next authoritative forecast-issue availability).
    #     None means the next-issue schedule is unverified/missing → a NEW forecast-conditioned entry
    #     fails closed (no enqueue); exit/monitor of existing positions is never blocked by this.
    valid_until: str | None = None
    next_authoritative_issue_at: str | None = None


@dataclass(frozen=True)
class EnqueuedRedecision:
    family_id: str
    bin_label: str
    direction: str
    edge: float
    event_type: str = REDECISION_EVENT_TYPE


@dataclass(frozen=True)
class FullEconomicsReject:
    execution_price: float | None
    q_lcb_5pct: float | None
    trade_score: float | None
    created_at: str
    rejection_reason: str = ""


@dataclass(frozen=True)
class RecentNoValueEventRefutation:
    event_id: str
    rejection_reason: str
    created_at: str
    evidence_match: str


@dataclass(frozen=True)
class RepriceDecision:
    """Cancel decision for a RESTING order whose sealed decision current strategy
    policy no longer admits. ``action`` is always ``CANCEL_REPLACE``; ``reason`` is
    the policy block. Value-based keep/resize/cancel is the C3 standing valuation
    (src.execution.staleness_cancel). This module never submits."""
    family_id: str
    bin_label: str
    side: str
    action: str
    reason: str
    detail: float = 0.0


def _no_value_refutation_event_types_compatible(
    active_event_type: str, regret_event_type: str
) -> bool:
    active = str(active_event_type or "").strip()
    regret = str(regret_event_type or "").strip()
    if active in _NO_VALUE_FORECAST_EVENT_TYPES:
        return not regret or regret in _NO_VALUE_FORECAST_EVENT_TYPES
    if active == "DAY0_EXTREME_UPDATED":
        return regret == "DAY0_EXTREME_UPDATED"
    return bool(active and regret and active == regret)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            is not None
        )
    except sqlite3.Error:
        return False


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _belief_decision_id(family_id: str, snapshot_id: str, calibrator_model_hash: str) -> str:
    # family_id is pipe-separated (no ':'); snapshot_id / calib hashes carry no ':'. Encode all three
    # so the read can recover provenance. Parsed via rsplit(':', 2) below.
    return f"{_BELIEF_PREFIX}{family_id}:{snapshot_id}:{calibrator_model_hash}"


def _prefix_upper_bound(prefix: str) -> str:
    """Return the exclusive upper bound for a SQLite text-prefix range."""
    if not prefix:
        raise ValueError("prefix must be non-empty")
    return prefix[:-1] + chr(ord(prefix[-1]) + 1)


def _parse_belief_decision_id(decision_id: str) -> tuple[str, str, str] | None:
    if not decision_id.startswith(_BELIEF_PREFIX):
        return None
    body = decision_id[len(_BELIEF_PREFIX):]
    parts = body.rsplit(":", 2)
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]  # (family_id, snapshot_id, calibrator_model_hash)


def ensure_belief_cache_schema(conn: sqlite3.Connection) -> None:
    """Create a MINIMAL probability_trace_fact if absent (unit tests). Live already has the full,
    registered table — CREATE TABLE IF NOT EXISTS is a no-op there (column-shape is subset-checked)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS probability_trace_fact (
            trace_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL UNIQUE,
            trace_status TEXT NOT NULL,
            missing_reason_json TEXT NOT NULL DEFAULT '[]',
            recorded_at TEXT NOT NULL,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            decision_snapshot_id TEXT,
            bin_labels_json TEXT,
            p_posterior_json TEXT,
            condition_ids_json TEXT,
            q_lcb_yes_json TEXT,
            q_lcb_no_json TEXT
        )
        """
    )
    # Live DBs predate condition_ids_json; ALTER catches them (duplicate-column on fresh DBs is the
    # expected no-op). Column-subset-safe per assert_db_matches_registry (extra columns permitted).
    try:
        conn.execute("ALTER TABLE probability_trace_fact ADD COLUMN condition_ids_json TEXT;")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE probability_trace_fact ADD COLUMN temperature_metric TEXT;")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE probability_trace_fact ADD COLUMN q_lcb_yes_json TEXT;")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE probability_trace_fact ADD COLUMN q_lcb_no_json TEXT;")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def _has_condition_ids_column(
    conn: sqlite3.Connection,
    *,
    deadline_fence: SqliteDeadlineFence | None = None,
) -> bool:
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(probability_trace_fact)").fetchall()}
    except sqlite3.Error:
        if deadline_fence is not None:
            raise
        return False
    return "condition_ids_json" in cols


def _has_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    *,
    deadline_fence: SqliteDeadlineFence | None = None,
) -> bool:
    try:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        if deadline_fence is not None:
            raise
        return False
    return column in cols


def _json_float_or_none_vec(values: list[object] | None) -> str | None:
    if values is None:
        return None
    out: list[float | None] = []
    for value in values:
        try:
            out.append(None if value is None else float(value))
        except (TypeError, ValueError):
            out.append(None)
    return json.dumps(out)


def write_belief_row(
    conn: sqlite3.Connection,
    *,
    family_id: str,
    city: str,
    target_date: str,
    snapshot_id: str,
    calibrator_model_hash: str,
    bin_labels: list[str],
    p_posterior_vec: list[float],
    recorded_at: str,
    temperature_metric: str = "",
    condition_ids: list[str] | None = None,
    q_lcb_yes_vec: list[object] | None = None,
    q_lcb_no_vec: list[object] | None = None,
) -> None:
    """Write ONE belief row through the GIVEN connection — NO commit, NO new connection.

    This is the DEADLOCK-FREE primitive (resurrection 2026-06-12). The reactor calls it while it
    already holds the world write lock inside its open SAVEPOINT, so the row lands in the SAME
    transaction the reactor's decision rows use and is released by the reactor's own per-event
    commit. The original ``persist_belief_live`` opened a SECOND world connection and committed
    WHILE this lock was held → SQLite self-deadlock that HUNG process_pending. The cure is the
    structural one: the caller owns the transaction; this function never touches it.

    Idempotent per (family, snapshot, calibrator) — a newer snapshot writes a new row; the screen
    reads latest. condition_ids is parallel to bin_labels (empty string for bins with no market)."""
    decision_id = _belief_decision_id(family_id, snapshot_id, calibrator_model_hash)
    cond_json = json.dumps([str(c or "") for c in (condition_ids or [])])
    metric = str(temperature_metric or _metric_from_family_id(family_id) or "").strip()
    has_metric_col = _has_temperature_metric_column(conn)
    has_yes_lcb_col = _has_column(conn, "probability_trace_fact", "q_lcb_yes_json")
    has_no_lcb_col = _has_column(conn, "probability_trace_fact", "q_lcb_no_json")
    q_lcb_yes_json = _json_float_or_none_vec(q_lcb_yes_vec)
    q_lcb_no_json = _json_float_or_none_vec(q_lcb_no_vec)
    if _has_condition_ids_column(conn):
        metric_col = ", temperature_metric" if has_metric_col else ""
        metric_placeholder = ", ?" if has_metric_col else ""
        metric_update = ", temperature_metric=excluded.temperature_metric" if has_metric_col else ""
        q_lcb_cols = ""
        q_lcb_placeholders = ""
        q_lcb_update = ""
        if has_yes_lcb_col:
            q_lcb_cols += ", q_lcb_yes_json"
            q_lcb_placeholders += ", ?"
            q_lcb_update += ", q_lcb_yes_json=excluded.q_lcb_yes_json"
        if has_no_lcb_col:
            q_lcb_cols += ", q_lcb_no_json"
            q_lcb_placeholders += ", ?"
            q_lcb_update += ", q_lcb_no_json=excluded.q_lcb_no_json"
        values = [
            "trace_" + decision_id, decision_id, recorded_at,
            city, target_date, snapshot_id,
            json.dumps(list(bin_labels)), json.dumps([float(x) for x in p_posterior_vec]),
            cond_json,
        ]
        if has_metric_col:
            values.insert(5, metric)
        if has_yes_lcb_col:
            values.append(q_lcb_yes_json)
        if has_no_lcb_col:
            values.append(q_lcb_no_json)
        conn.execute(
            f"""
            INSERT INTO probability_trace_fact
                (trace_id, decision_id, trace_status, missing_reason_json, recorded_at,
                 city, target_date{metric_col}, decision_snapshot_id, bin_labels_json, p_posterior_json,
                 condition_ids_json{q_lcb_cols})
            VALUES (?, ?, 'complete', '[]', ?, ?, ?{metric_placeholder}, ?, ?, ?, ?{q_lcb_placeholders})
            ON CONFLICT(decision_id) DO UPDATE SET
                recorded_at=excluded.recorded_at,
                bin_labels_json=excluded.bin_labels_json,
                p_posterior_json=excluded.p_posterior_json,
                condition_ids_json=excluded.condition_ids_json
                {metric_update}
                {q_lcb_update}
            """,
            tuple(values),
        )
    else:
        # Legacy DB not yet migrated: write without condition_ids (P2 screen will skip price-join
        # for these rows). Never fail-closed on a missing optional column.
        metric_col = ", temperature_metric" if has_metric_col else ""
        metric_placeholder = ", ?" if has_metric_col else ""
        metric_update = ", temperature_metric=excluded.temperature_metric" if has_metric_col else ""
        values = [
            "trace_" + decision_id, decision_id, recorded_at,
            city, target_date, snapshot_id,
            json.dumps(list(bin_labels)), json.dumps([float(x) for x in p_posterior_vec]),
        ]
        if has_metric_col:
            values.insert(5, metric)
        conn.execute(
            f"""
            INSERT INTO probability_trace_fact
                (trace_id, decision_id, trace_status, missing_reason_json, recorded_at,
                 city, target_date{metric_col}, decision_snapshot_id, bin_labels_json, p_posterior_json)
            VALUES (?, ?, 'complete', '[]', ?, ?, ?{metric_placeholder}, ?, ?, ?)
            ON CONFLICT(decision_id) DO UPDATE SET
                recorded_at=excluded.recorded_at,
                bin_labels_json=excluded.bin_labels_json,
                p_posterior_json=excluded.p_posterior_json
                {metric_update}
            """,
            tuple(values),
        )


def cache_belief(
    conn: sqlite3.Connection,
    *,
    family_id: str,
    city: str,
    target_date: str,
    snapshot_id: str,
    calibrator_model_hash: str,
    bin_labels: list[str],
    p_posterior_vec: list[float],
    recorded_at: str,
    temperature_metric: str = "",
    condition_ids: list[str] | None = None,
    q_lcb_yes_vec: list[object] | None = None,
    q_lcb_no_vec: list[object] | None = None,
) -> None:
    """Standalone (test / offline) belief writer: writes the row AND commits on its own connection.

    NEVER call this from inside the reactor's write window — it commits, which on a held world lock
    is the exact deadlock the resurrection removed. The reactor path uses ``write_belief_row``
    (no commit). This entry point is for isolated/:memory: connections that own their transaction."""
    if q_lcb_yes_vec is None:
        q_lcb_yes_vec = [float(x) for x in p_posterior_vec]
    if q_lcb_no_vec is None:
        q_lcb_no_vec = [one_minus(float(x)) for x in p_posterior_vec]
    write_belief_row(
        conn,
        family_id=family_id, city=city, target_date=target_date,
        temperature_metric=temperature_metric,
        snapshot_id=snapshot_id, calibrator_model_hash=calibrator_model_hash,
        bin_labels=bin_labels, p_posterior_vec=p_posterior_vec, recorded_at=recorded_at,
        condition_ids=condition_ids,
        q_lcb_yes_vec=q_lcb_yes_vec,
        q_lcb_no_vec=q_lcb_no_vec,
    )
    conn.commit()


def _metric_from_family_id(family_id: str) -> str:
    """Extract the temperature_metric from a pipe-separated family_id.

    Both make_hypothesis_family_id ("hyp|cycle_mode|city|target_date|metric|...") and
    make_edge_family_id ("edge|cycle_mode|city|target_date|metric|strategy_key|...") place the
    metric at index 4. Returns "" if the id is too short / not pipe-separated."""
    parts = family_id.split("|")
    if len(parts) > 4 and parts[4] in ("high", "low"):
        return parts[4]
    if len(parts) == 3 and parts[2] in ("high", "low"):
        return parts[2]
    return ""


def _metric_from_bin_labels(bin_labels: list[object]) -> str:
    for label in bin_labels:
        text = str(label or "").lower()
        if "highest temperature" in text:
            return "high"
        if "lowest temperature" in text:
            return "low"
    return ""


def _stable_entry_screen_key(
    belief: CachedBelief,
    *,
    bin_label: str,
    direction: str,
) -> StableEntryScreenKey | None:
    """Stable identity for entry backoff across dynamic EDLI family hashes."""

    city = str(belief.city or "").strip()
    target_date = str(belief.target_date or "").strip()
    metric = str(belief.metric or _metric_from_family_id(belief.family_id) or "").strip()
    label = str(bin_label or "").strip()
    side = str(direction or "").strip()
    if not (city and target_date and metric in {"high", "low"} and label and side):
        return None
    return (city, target_date, metric, label, side)


def _stable_family_screen_key(belief: CachedBelief) -> FamilyRedecisionScreenKey | None:
    city = str(belief.city or "").strip()
    target_date = str(belief.target_date or "").strip()
    metric = str(belief.metric or _metric_from_family_id(belief.family_id) or "").strip()
    if not (city and target_date and metric in {"high", "low"}):
        return None
    return ("family", city, target_date, metric)


def _has_temperature_metric_column(
    conn: sqlite3.Connection,
    *,
    deadline_fence: SqliteDeadlineFence | None = None,
) -> bool:
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(probability_trace_fact)").fetchall()}
    except sqlite3.Error:
        if deadline_fence is not None:
            raise
        return False
    return "temperature_metric" in cols


# Certificate validity (ultimate_alpha group D). ``ecmwf_open_data`` is the live authoritative
# forecast source (forecast_live_daemon FORECAST_LIVE_SOURCE_HEALTH_SOURCE_IDS); its release-calendar
# cycle schedule bounds a belief's validity (τ_next). Its two ingest tracks map 1:1 to the
# temperature metric. Kept as the calendar's own key strings (release_calendar is the authority and
# treats these opaquely; src/data/ecmwf_open_data.py::TRACKS is the canonical definition).
_CERT_FORECAST_SOURCE_ID: str = "ecmwf_open_data"
_CERT_TRACK_BY_METRIC: dict[str, str] = {"high": "mx2t6_high", "low": "mn2t6_low"}


@lru_cache(maxsize=1)
def _cert_calendar_entries():
    """Process-lifetime cache of the release-calendar registry (deployed machine law, static per
    process). Loaded once so deriving valid_until per belief row never re-parses the YAML — a belief
    scan constructs up to ZEUS_REDECISION_BELIEF_SCAN_LIMIT rows per tick. None on load failure →
    valid_until fails closed to None (never blocks belief construction)."""
    try:
        from src.data.release_calendar import load_calendar_config

        return load_calendar_config()
    except Exception:
        return None


def _derive_valid_until(metric: str, recorded_at: str) -> str | None:
    """τ_next-derived certificate validity for a belief (ultimate_alpha group D).

    ``valid_until`` = the next authoritative ``ecmwf_open_data`` forecast issue strictly after the
    belief's own issue instant (its ``recorded_at`` — the reactor stamps this with the decision
    time). Once that instant passes a newer authoritative issue SHOULD exist, so the belief is no
    longer a valid decision basis (enqueue/screen treat it as CERT_EXPIRED; a resting order is pulled
    CERT_EXPIRY_PULL). Returns None — the belief never expires by THIS mechanism, ordinary freshness
    gates alone govern — when the calendar cannot vouch: unknown metric, a RECONSTRUCTED-tier source
    (next_authoritative_issue_at fails closed), or an unparseable recorded_at."""
    track = _CERT_TRACK_BY_METRIC.get(str(metric or "").strip())
    if track is None:
        return None
    entries = _cert_calendar_entries()
    if entries is None:
        return None
    issue_ts = _decision_time_utc(recorded_at)
    if issue_ts is None:
        return None
    try:
        from src.data.release_calendar import next_authoritative_issue_at

        tau_next = next_authoritative_issue_at(
            _CERT_FORECAST_SOURCE_ID, track, issue_ts, entries=entries
        )
    except Exception:
        return None
    return tau_next.isoformat() if tau_next is not None else None


def _row_to_belief(row: sqlite3.Row) -> CachedBelief | None:
    parsed = _parse_belief_decision_id(row["decision_id"])
    if parsed is None or not row["p_posterior_json"] or not row["bin_labels_json"]:
        return None
    family_id, snapshot_id, calib = parsed
    bin_labels = json.loads(row["bin_labels_json"])
    try:
        cond_raw = row["condition_ids_json"]
    except (IndexError, KeyError):
        cond_raw = None
    try:
        row_metric = str(row["temperature_metric"] or "").strip()
    except (IndexError, KeyError):
        row_metric = ""
    condition_ids = json.loads(cond_raw) if cond_raw else []
    try:
        q_lcb_yes_raw = row["q_lcb_yes_json"]
    except (IndexError, KeyError):
        q_lcb_yes_raw = None
    try:
        q_lcb_no_raw = row["q_lcb_no_json"]
    except (IndexError, KeyError):
        q_lcb_no_raw = None
    q_lcb_yes_vec = json.loads(q_lcb_yes_raw) if q_lcb_yes_raw else None
    q_lcb_no_vec = json.loads(q_lcb_no_raw) if q_lcb_no_raw else None
    metric = row_metric or _metric_from_family_id(family_id) or _metric_from_bin_labels(bin_labels)
    # DERIVE-ON-CONSTRUCT: valid_until is a pure function of (metric-derived source track, issue
    # instant), not a persisted column. next_authoritative_issue_at == valid_until here (τ_next with
    # no Δ_cancel buffer); the field stays the raw τ_next so a future min-formula can diverge.
    valid_until = _derive_valid_until(metric, str(row["recorded_at"] or ""))
    return CachedBelief(
        family_id=family_id,
        city=row["city"] or "",
        target_date=row["target_date"] or "",
        snapshot_id=snapshot_id,
        calibrator_model_hash=calib,
        bin_labels=bin_labels,
        p_posterior_vec=json.loads(row["p_posterior_json"]),
        recorded_at=row["recorded_at"],
        condition_ids=list(condition_ids),
        q_lcb_yes_vec=list(q_lcb_yes_vec) if q_lcb_yes_vec is not None else None,
        q_lcb_no_vec=list(q_lcb_no_vec) if q_lcb_no_vec is not None else None,
        metric=metric,
        valid_until=valid_until,
        next_authoritative_issue_at=valid_until,
    )


def latest_cached_belief(
    conn: sqlite3.Connection,
    *,
    family_id: str,
    at_or_before: str | datetime | None = None,
) -> CachedBelief | None:
    cols = "decision_id, recorded_at, city, target_date, bin_labels_json, p_posterior_json"
    if _has_condition_ids_column(conn):
        cols += ", condition_ids_json"
    if _has_temperature_metric_column(conn):
        cols += ", temperature_metric"
    if _has_column(conn, "probability_trace_fact", "q_lcb_yes_json"):
        cols += ", q_lcb_yes_json"
    if _has_column(conn, "probability_trace_fact", "q_lcb_no_json"):
        cols += ", q_lcb_no_json"
    prefix = _BELIEF_PREFIX + str(family_id) + ":"
    rows = conn.execute(
        f"SELECT {cols} FROM probability_trace_fact "
        "WHERE decision_id >= ? AND decision_id < ?",
        (prefix, _prefix_upper_bound(prefix)),
    ).fetchall()
    if not rows:
        return None
    if at_or_before is not None:
        cutoff = _decision_time_utc(at_or_before)
        if cutoff is None:
            return None
        rows = [
            row
            for row in rows
            if (recorded := _decision_time_utc(str(row["recorded_at"] or ""))) is not None
            and recorded <= cutoff
        ]
        if not rows:
            return None
    latest = max(rows, key=lambda row: str(row["recorded_at"] or ""))
    return _row_to_belief(latest)


def _decision_time_utc(decision_time: str | datetime | None) -> datetime | None:
    if decision_time is None:
        return None
    try:
        dt = decision_time if isinstance(decision_time, datetime) else _parse(str(decision_time))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _belief_forecast_only_admissible(
    belief: CachedBelief,
    *,
    decision_time_utc: datetime | None,
) -> bool:
    if decision_time_utc is None:
        return False
    metric = str(belief.metric or _metric_from_family_id(belief.family_id) or "").strip()
    if metric not in {"high", "low"}:
        return False
    try:
        from src.strategy.market_phase import market_phase_admits

        return market_phase_admits(
            city=str(belief.city or "").strip(),
            target_date=str(belief.target_date or "").strip(),
            metric=metric,
            decision_time=decision_time_utc,
            market_row={},
        )
    except Exception:
        return False


def filter_beliefs_forecast_only_admissible(
    beliefs: list[CachedBelief],
    *,
    decision_time: str | datetime | None,
) -> list[CachedBelief]:
    """Apply the same forecast-only admissibility filter that
    ``_all_latest_beliefs(forecast_only_admissible=True)`` applies inline (see the loop
    at the end of that function), to an already-fetched belief list.

    Entry admission is a strict subset of management admission: the two
    ``_all_latest_beliefs`` calls in ``run_edli_continuous_redecision_screen_cycle``
    (src/events/reactor.py) were previously two independent full scans of the same
    ``probability_trace_fact`` decision_id-prefix range that differed only in this
    filter. A caller can fetch the superset (management) scan once and derive the
    entry-admissible subset from it with this function, halving per-cycle DB I/O
    with byte-identical results (same underlying predicate, ``_belief_forecast_only_admissible``).
    """

    decision_time_utc = _decision_time_utc(decision_time)
    return [
        belief
        for belief in beliefs
        if _belief_forecast_only_admissible(belief, decision_time_utc=decision_time_utc)
    ]


def _belief_venue_closed(belief: CachedBelief, *, decision_time_utc: datetime | None) -> bool:
    if decision_time_utc is None:
        return False
    try:
        from src.strategy.market_phase import family_venue_closed

        return family_venue_closed(
            city=str(belief.city or "").strip(),
            target_date=str(belief.target_date or "").strip(),
            now_utc=decision_time_utc,
        )
    except Exception:
        return False


def _all_latest_beliefs(
    conn: sqlite3.Connection,
    *,
    decision_time: str | datetime | None = None,
    scan_limit: int | None = None,
    forecast_only_admissible: bool = False,
    family_keys: set[tuple[str, str, str]] | None = None,
    deadline_fence: SqliteDeadlineFence | None = None,
) -> list[CachedBelief]:
    # Schema probes are SQLite work too: keep them behind the same absolute
    # fence as the large trace query.  A fenced caller must see a real SQLite
    # failure, never a silently fabricated "column absent" answer.
    with (
        sqlite_deadline_bound(conn, deadline_fence)
        if deadline_fence is not None
        else nullcontext()
    ):
        cols = "decision_id, recorded_at, city, target_date, bin_labels_json, p_posterior_json"
        if deadline_fence is not None and deadline_fence.expired():
            raise sqlite3.OperationalError("interrupted")
        if _has_condition_ids_column(conn, deadline_fence=deadline_fence):
            cols += ", condition_ids_json"
        if _has_temperature_metric_column(conn, deadline_fence=deadline_fence):
            cols += ", temperature_metric"
        if _has_column(
            conn, "probability_trace_fact", "q_lcb_yes_json", deadline_fence=deadline_fence
        ):
            cols += ", q_lcb_yes_json"
        if _has_column(
            conn, "probability_trace_fact", "q_lcb_no_json", deadline_fence=deadline_fence
        ):
            cols += ", q_lcb_no_json"
    requested_families = None
    if family_keys is not None:
        requested_families = {
            (
                str(city or "").strip(),
                str(target_date or "").strip(),
                str(metric or "").strip(),
            )
            for city, target_date, metric in family_keys
            if str(city or "").strip()
            and str(target_date or "").strip()
            and str(metric or "").strip() in {"high", "low"}
        }
        if not requested_families:
            return []
    if scan_limit is None:
        try:
            scan_limit = int(
                os.environ.get(
                    "ZEUS_REDECISION_BELIEF_SCAN_LIMIT",
                    str(_DEFAULT_LATEST_BELIEF_SCAN_LIMIT),
                )
            )
        except (TypeError, ValueError):
            scan_limit = _DEFAULT_LATEST_BELIEF_SCAN_LIMIT
    scan_limit = max(1, int(scan_limit))
    with (
        sqlite_deadline_bound(conn, deadline_fence)
        if deadline_fence is not None
        else nullcontext()
    ):
        if requested_families is None:
            rows = conn.execute(
                f"""
            WITH latest_trace AS MATERIALIZED (
                SELECT trace_id, recorded_at
                  FROM probability_trace_fact
                 WHERE decision_id >= ?
                   AND decision_id < ?
                 ORDER BY recorded_at DESC, trace_id DESC
                 LIMIT ?
            )
            SELECT {', '.join(f'p.{column.strip()}' for column in cols.split(','))}
              FROM latest_trace latest
              JOIN probability_trace_fact p
                ON p.trace_id = latest.trace_id
             ORDER BY latest.recorded_at DESC, latest.trace_id DESC
            """,
                (_BELIEF_PREFIX, _prefix_upper_bound(_BELIEF_PREFIX), scan_limit),
            ).fetchall()
        else:
            has_metric = _has_temperature_metric_column(
                conn, deadline_fence=deadline_fence
            )
            requested = (
                requested_families
                if has_metric
                else {(city, target_date) for city, target_date, _metric in requested_families}
            )
            request_columns = "city, target_date, metric" if has_metric else "city, target_date"
            request_extract = (
                "json_extract(value, '$[0]'), json_extract(value, '$[1]'), "
                "json_extract(value, '$[2]')"
                if has_metric
                else "json_extract(value, '$[0]'), json_extract(value, '$[1]')"
            )
            metric_join = "AND p.temperature_metric = r.metric" if has_metric else ""
            rows = conn.execute(
                f"""
            WITH requested({request_columns}) AS (
                SELECT {request_extract}
                  FROM json_each(?)
            )
            SELECT {', '.join(f'p.{column.strip()}' for column in cols.split(','))}
              FROM requested r
              CROSS JOIN probability_trace_fact p
             WHERE p.city = r.city
               AND p.target_date = r.target_date
               {metric_join}
               AND p.decision_id >= ?
               AND p.decision_id < ?
             ORDER BY p.recorded_at DESC, p.trace_id DESC
             LIMIT ?
            """,
                (
                    json.dumps(tuple(requested), separators=(",", ":")),
                    _BELIEF_PREFIX,
                    _prefix_upper_bound(_BELIEF_PREFIX),
                    scan_limit,
                ),
            ).fetchall()
    decision_time_utc = _decision_time_utc(decision_time)
    seen: set[RedecisionScreenKey] = set()
    out: list[CachedBelief] = []
    for row_index, row in enumerate(rows):
        if deadline_fence is not None and (
            row_index % 32 == 0 and deadline_fence.expired()
        ):
            raise sqlite3.OperationalError("interrupted")
        belief = _row_to_belief(row)
        if belief is None:
            continue
        if requested_families is not None and (
            str(belief.city or "").strip(),
            str(belief.target_date or "").strip(),
            str(belief.metric or "").strip(),
        ) not in requested_families:
            continue
        if forecast_only_admissible and not _belief_forecast_only_admissible(
            belief,
            decision_time_utc=decision_time_utc,
        ):
            continue
        if _belief_venue_closed(belief, decision_time_utc=decision_time_utc):
            continue
        dedupe_key: RedecisionScreenKey = _stable_family_screen_key(belief) or (
            belief.family_id,
            "",
            "",
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(belief)
    if deadline_fence is not None and deadline_fence.expired():
        raise sqlite3.OperationalError("interrupted")
    return out


def enqueue_live_redecisions(
    conn: sqlite3.Connection,
    *,
    decision_time: str,
    price_lookup: dict[tuple[str, str, str], PriceQuote],
    min_edge: float,
    acted_state: dict[RedecisionScreenKey, float] | None = None,
    recent_full_economics_rejections: dict[RedecisionScreenKey, FullEconomicsReject] | None = None,
    beliefs: list[CachedBelief] | None = None,
) -> list[EnqueuedRedecision]:
    """Screen live entry pairs against FRESH price and conservative q_lcb evidence.

    Stale price (freshness_deadline <= decision_time) is skipped (no phantom edge). acted_state is an
    optional IN-MEMORY dict (the reactor holds it across cycles): a pair re-fires only when its edge
    improves past IMPROVE_DELTA vs the last acted edge — a short price wiggle does NOT re-fire.
    Recent full-economics no-value rejects block the same pair until price or q_lcb improves.

    Certificate validity (ultimate_alpha group D): a belief whose ``valid_until`` has passed is not a
    valid decision basis — skipped exactly like stale freshness, logged as CERT_EXPIRED on each
    screen cycle while it remains the latest belief. A stale certificate never revives on book
    improvement; only a NEW belief (new forecast snapshot) re-opens the pair.
    """
    dt = _parse(decision_time)
    out: list[EnqueuedRedecision] = []
    for belief in beliefs if beliefs is not None else _all_latest_beliefs(
        conn,
        decision_time=decision_time,
    ):
        if _belief_certificate_expired(belief, dt):
            logger.info(
                "EDLI entry screen: CERT_EXPIRED family=%s snapshot=%s valid_until=%s — "
                "belief past certificate validity; awaiting next forecast issue",
                belief.family_id, belief.snapshot_id, belief.valid_until,
            )
            continue
        family_key = _stable_family_screen_key(belief)
        for idx, label in enumerate(belief.bin_labels):
            if idx >= len(belief.p_posterior_vec):
                continue
            q_lcb_yes = _vec_float_at(belief.q_lcb_yes_vec, idx)
            q_lcb_no = _vec_float_at(belief.q_lcb_no_vec, idx)
            for direction in ("buy_yes", "buy_no"):
                legacy_key: EntryScreenKey = (belief.family_id, label, direction)
                stable_key = _stable_entry_screen_key(
                    belief,
                    bin_label=label,
                    direction=direction,
                )
                quote = price_lookup.get(legacy_key)
                if quote is None:
                    continue
                if _parse(quote.freshness_deadline) <= dt:
                    continue  # STALE → no phantom edge (R7)
                conservative_q = q_lcb_yes if direction == "buy_yes" else q_lcb_no
                if conservative_q is None:
                    continue
                posterior_q = (
                    float(belief.p_posterior_vec[idx])
                    if direction == "buy_yes"
                    else one_minus(float(belief.p_posterior_vec[idx]))
                )
                score = _entry_screen_robust_trade_score(
                    q_posterior=posterior_q,
                    q_lcb_5pct=float(conservative_q),
                    price=float(quote.price),
                    tick_size=quote.tick_size,
                )
                if score < min_edge - _EPS:
                    continue
                rejection = None
                if recent_full_economics_rejections is not None:
                    if stable_key is not None:
                        rejection = recent_full_economics_rejections.get(stable_key)
                    if rejection is None:
                        rejection = recent_full_economics_rejections.get(legacy_key)
                candidate_refutation_cleared = False
                if rejection is not None and _full_economics_reject_still_blocks(
                    rejection,
                    current_execution_price=_all_in_cost(float(quote.price)),
                    current_q_lcb=float(conservative_q),
                    improve_delta=_improve_delta_for_tick(quote.tick_size),
                ):
                    continue
                if rejection is not None:
                    candidate_refutation_cleared = True
                family_rejection = (
                    recent_full_economics_rejections.get(family_key)
                    if family_key is not None and recent_full_economics_rejections is not None
                    else None
                )
                if (
                    family_rejection is not None
                    and not (
                        candidate_refutation_cleared
                        and _candidate_refutation_is_at_least_as_fresh(rejection, family_rejection)
                    )
                    and _full_decision_family_refutation_still_blocks(
                        family_rejection,
                        decision_time=decision_time,
                    )
                ):
                    continue
                if acted_state is not None:
                    acted_key: RedecisionScreenKey = stable_key or legacy_key
                    last = acted_state.get(acted_key)
                    if last is not None and score <= last + _improve_delta_for_tick(quote.tick_size) + _EPS:
                        continue  # not materially improved → do not re-fire (anti price-noise)
                    acted_state[acted_key] = score
                out.append(EnqueuedRedecision(belief.family_id, label, direction, score))
    return out


def _belief_certificate_expired(belief: CachedBelief, now) -> bool:
    """True when the belief's certificate validity boundary has passed.

    ``valid_until`` is nullable (pre-migration rows / callers not yet
    computing it): None means no validity boundary is declared and the
    ordinary freshness gates alone govern — this helper only ENFORCES a
    declared boundary, it never invents one (fail-closed on missing τ_next
    happens where NEW forecast-conditioned exposure is created, not here).
    """
    if not belief.valid_until:
        return False
    try:
        return _parse(belief.valid_until) <= now
    except (TypeError, ValueError):
        return True  # a declared-but-unparseable boundary is not a valid basis


def _vec_float_at(values: list[float | None] | None, idx: int) -> float | None:
    if values is None or idx >= len(values):
        return None
    try:
        value = values[idx]
        if value is None:
            return None
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not (0.0 <= out <= 1.0):
        return None
    return out


def _full_economics_reject_still_blocks(
    rejection: FullEconomicsReject,
    *,
    current_execution_price: float,
    current_q_lcb: float,
    improve_delta: float = IMPROVE_DELTA,
) -> bool:
    reason = str(rejection.rejection_reason or "")
    execution_quality_reject = _is_execution_quality_rejection_reason(reason)
    if (
        rejection.trade_score is not None
        and rejection.trade_score > 0.0
        and not execution_quality_reject
        and not reason.startswith("FDR_REJECTED")
    ):
        return False
    price_improved = (
        rejection.execution_price is not None
        and current_execution_price <= float(rejection.execution_price) - float(improve_delta) + _EPS
    )
    belief_improved = (
        rejection.q_lcb_5pct is not None
        and current_q_lcb >= float(rejection.q_lcb_5pct) + float(improve_delta) - _EPS
    )
    return not (price_improved or belief_improved)


def _is_execution_quality_rejection_reason(reason: str) -> bool:
    """Final-submit quality failures are redecision backoff evidence.

    These rows can have a positive cheap-screen trade_score, but they still prove the
    current executable path is not a confirmed trading-value candidate. They should
    re-enter only after the price or q_lcb has materially improved.
    """

    return (
        reason.startswith("TAKER_QUALITY_PROOF_NOT_PASSED")
        or reason.startswith("entry_taker_quality:")
    )


def _is_family_level_redecision_refutation(reason: str) -> bool:
    """Return true when a prior full path proves this family is not actionable yet.

    These rows do not identify one bin/direction. They are still live evidence
    that the same family must not keep entering continuous entry redecision until
    either the short family cooldown expires or fresh evidence creates a new
    decision attempt.
    """

    if _is_operational_non_value_summary(reason):
        return False
    return (
        reason.startswith("TRADE_SCORE_NON_POSITIVE")
        or reason.startswith("TRADE_SCORE_BLOCKED")
        or reason.startswith("FDR_REJECTED")
        or reason.startswith("EVENT_BOUND_ALL_CANDIDATES_REJECTED:")
        or reason.startswith(
            "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:NO_SUBMIT_CERTIFICATE_REJECTED:"
        )
    )


def _is_operational_non_value_summary(reason: str) -> bool:
    """Reasons that say "not submit-able right now", not "no economic value".

    Active-order duplicate suppression and held-position monitor ownership are
    enforced at the submit/monitor boundary from current state. Persisted summary
    rows carrying those labels must not become family-level no-value evidence;
    doing so freezes unrelated bins/directions in the same weather family until
    cooldown even though the original rejection was operational ownership.
    """

    reason_text = str(reason or "")
    if not reason_text.startswith("EVENT_BOUND_ALL_CANDIDATES_REJECTED:"):
        return False
    operational_markers = (
        "EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED",
        "held_family_monitor_owned",
        "held_position_monitor_owned",
        "OPEN_POSITION_SAME_FAMILY_MONITOR_OWNED",
        "OPEN_POSITION_SAME_MARKET_MONITOR_OWNED",
    )
    return any(marker in reason_text for marker in operational_markers)


def _full_decision_family_refutation_still_blocks(
    rejection: FullEconomicsReject,
    *,
    decision_time: str,
) -> bool:
    try:
        rejected_at = _parse(str(rejection.created_at))
        now = _parse(str(decision_time))
    except (TypeError, ValueError):
        return True
    return (now - rejected_at).total_seconds() < FULL_DECISION_FAMILY_REFUTATION_COOLDOWN_SECONDS


def _candidate_refutation_is_at_least_as_fresh(
    candidate_rejection: FullEconomicsReject | None,
    family_rejection: FullEconomicsReject,
) -> bool:
    family_reason = str(family_rejection.rejection_reason or "")
    if (
        family_reason.startswith("FDR_REJECTED")
        or family_reason.startswith("EVENT_BOUND_ALL_CANDIDATES_REJECTED:")
    ):
        return False
    if candidate_rejection is None:
        return False
    try:
        candidate_time = _parse(str(candidate_rejection.created_at))
        family_time = _parse(str(family_rejection.created_at))
    except (TypeError, ValueError):
        return False
    return candidate_time >= family_time


def _all_in_cost(price: float) -> float:
    return float(price) + _fee_at(float(price))


def _quote_tick_size(quote_or_tick: object = None) -> float:
    try:
        if isinstance(quote_or_tick, PriceQuote):
            tick = float(quote_or_tick.tick_size)
        elif quote_or_tick is None:
            tick = TICK_SIZE
        else:
            tick = float(quote_or_tick)
    except (TypeError, ValueError):
        tick = TICK_SIZE
    if not math.isfinite(tick) or tick <= 0.0:
        return TICK_SIZE
    return tick


def _improve_delta_for_tick(tick_size: object = None) -> float:
    return 2.0 * _quote_tick_size(tick_size)


def _entry_screen_c95_cost(price: float, *, tick_size: object = None) -> float:
    """Conservative screen-side approximation of the final gate's c_cost_95pct.

    The final EDLI submit gate scores on ``c_cost_95pct`` rather than the raw
    top-book price. The screen only has the freshest top quote, not the full
    depth curve, so it must be conservative: all-in top cost plus one tick. This
    mirrors ``_execution_price_from_snapshot`` for ordinary taker quotes and
    prevents deterministic TRADE_SCORE_NON_POSITIVE redecision admissions.
    """

    return min(0.999999, _all_in_cost(float(price)) + _quote_tick_size(tick_size))


def _entry_screen_robust_trade_score(
    *,
    q_posterior: float,
    q_lcb_5pct: float,
    price: float,
    tick_size: object = None,
) -> float:
    """Screen with the same robust-cost sign contract as final submission.

    ``p_fill_lcb`` is set to 1.0 because the screen is an admission filter, not
    the fill-policy authority. Multiplying by any positive fill probability does
    not change the sign; the final gate still computes the executable
    side-specific fill LCB from the full snapshot before any order can submit.
    """

    c95 = _entry_screen_c95_cost(float(price), tick_size=tick_size)
    return min(float(q_lcb_5pct) - c95, float(q_posterior) - c95)


def _optional_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _invalid_probability_bound_reject(q_live: object, q_lcb_5pct: object) -> bool:
    """Return True when a regret row carries an impossible q_lcb>q_live pair.

    Such rows are provenance/input corruption, not full-economics evidence. They
    must not become redecision backoff, otherwise one bad receipt can keep
    tradeable fresh evidence from re-entering the full reactor.
    """
    q = _optional_float(q_live)
    lcb = _optional_float(q_lcb_5pct)
    if q is None or lcb is None:
        return False
    return lcb > q + _EPS


def read_recent_full_economics_rejections(
    conn: sqlite3.Connection,
    *,
    lookback_hours: float = 24.0,
) -> dict[RedecisionScreenKey, FullEconomicsReject]:
    """Latest terminal full-economics no-value rejection per candidate.

    This is live evidence backoff, not a strategy cap. A cheaper fresh price or a
    higher q_lcb clears it and sends the pair through the full reactor again.
    """
    if not _table_exists(conn, "no_trade_regret_events"):
        return {}
    try:
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(no_trade_regret_events)").fetchall()
        }
    except sqlite3.Error:
        return {}
    required = {
        "family_id", "city", "target_date", "metric", "bin_label", "direction",
        "rejection_stage", "rejection_reason", "c_fee_adjusted", "q_lcb_5pct",
        "trade_score", "created_at",
    }
    if not required.issubset(cols):
        return {}
    from datetime import timedelta, timezone as _timezone

    cutoff = (datetime.now(_timezone.utc) - timedelta(hours=max(0.0, lookback_hours))).isoformat()
    q_live_select = ", q_live" if "q_live" in cols else ", NULL AS q_live"
    try:
        rows = conn.execute(
            f"""
            SELECT family_id, city, target_date, metric, bin_label, direction,
                   c_fee_adjusted, q_lcb_5pct, trade_score, created_at, rejection_reason
                   {q_live_select}
             FROM no_trade_regret_events
             WHERE ({_TERMINAL_NO_VALUE_SQL})
               AND (
                    (
                        bin_label IS NOT NULL AND bin_label != ''
                        AND direction IS NOT NULL AND direction != ''
                    )
                    OR rejection_reason LIKE 'EVENT_BOUND_ALL_CANDIDATES_REJECTED:%'
                    OR rejection_reason LIKE 'EDLI_LIVE_CERTIFICATE_BUILD_FAILED:NO_SUBMIT_CERTIFICATE_REJECTED:%'
               )
               AND created_at >= ?
             ORDER BY created_at DESC
            """,
            (cutoff,),
        ).fetchall()
    except sqlite3.Error:
        return {}
    out: dict[RedecisionScreenKey, FullEconomicsReject] = {}
    for row in rows:
        if _invalid_probability_bound_reject(row[11], row[7]):
            continue
        family_id = str(row[0] or "").strip()
        legacy_key: EntryScreenKey = (family_id, str(row[4]), str(row[5]))
        stable_key: StableEntryScreenKey | None = None
        city = str(row[1] or "").strip()
        target_date = str(row[2] or "").strip()
        metric = str(row[3] or "").strip()
        bin_label = str(row[4] or "").strip()
        direction = str(row[5] or "").strip()
        if city and target_date and metric in {"high", "low"} and bin_label and direction:
            stable_key = (city, target_date, metric, bin_label, direction)
        rejection = FullEconomicsReject(
            execution_price=_optional_float(row[6]),
            q_lcb_5pct=_optional_float(row[7]),
            trade_score=_optional_float(row[8]),
            created_at=str(row[9] or ""),
            rejection_reason=str(row[10] or ""),
        )
        if stable_key is not None and stable_key not in out:
            out[stable_key] = rejection
        if family_id and bin_label and direction and legacy_key not in out:
            out[legacy_key] = rejection
        reason = rejection.rejection_reason
        if (
            city
            and target_date
            and metric in {"high", "low"}
            and _is_family_level_redecision_refutation(reason)
        ):
            family_key: FamilyRedecisionScreenKey = ("family", city, target_date, metric)
            if family_key not in out:
                out[family_key] = rejection
    return out


def recent_no_value_event_refutations(
    conn: sqlite3.Connection,
    events: Sequence[OpportunityEvent],
    *,
    decision_time: datetime | None = None,
    cooldown_seconds: float = FULL_DECISION_FAMILY_REFUTATION_COOLDOWN_SECONDS,
) -> dict[str, RecentNoValueEventRefutation]:
    """Batch same-evidence terminal no-value refutations for intake events.

    This is an admission de-duplication guard, not an edge/no-edge cap. It only
    suppresses a newly minted ordinary FSR/DAY0 event when the same
    city/target/metric evidence identity has already reached a terminal
    full-economics no-trade decision inside the cooldown. ``EDLI_REDECISION_PENDING``
    is emitted only after the continuous screen sees current value/rest evidence,
    so it is not emit-suppressed here; the reactor owns the full redecision.
    Day0 is a separate observation lane and only Day0 no-value can refute Day0.
    """

    now = decision_time.astimezone(timezone.utc) if decision_time is not None else datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=max(0.0, float(cooldown_seconds)))).isoformat()
    event_inputs: dict[
        tuple[str, str, str],
        list[tuple[OpportunityEvent, str, str]],
    ] = {}
    for event in events:
        if event.event_type not in {"FORECAST_SNAPSHOT_READY", "DAY0_EXTREME_UPDATED"}:
            continue
        try:
            payload = json.loads(event.payload_json)
        except (TypeError, ValueError):
            continue
        city = str(payload.get("city") or "").strip()
        target_date = str(payload.get("target_date") or "").strip()
        metric = str(payload.get("metric") or "").strip()
        if not (city and target_date and metric):
            continue
        event_inputs.setdefault((city, target_date, metric), []).append(
            (
                event,
                str(event.causal_snapshot_id or "").strip(),
                str(event.payload_hash or "").strip(),
            )
        )
    if not event_inputs:
        return {}

    family_values_sql = ", ".join("(?, ?, ?)" for _ in event_inputs)
    family_params = tuple(value for family in event_inputs for value in family)
    rows_by_family: dict[tuple[str, str, str], list[sqlite3.Row | tuple]] = {}
    try:
        rows = conn.execute(
            f"""
            WITH requested_families(city, target_date, metric) AS (
                VALUES {family_values_sql}
            ), ranked AS (
                SELECT n.city,
                       n.target_date,
                       n.metric,
                       n.event_id,
                       n.rejection_reason,
                       n.created_at,
                       n.causal_snapshot_id AS regret_causal_snapshot_id,
                       e.causal_snapshot_id AS event_causal_snapshot_id,
                       e.payload_hash,
                       e.event_type,
                       ROW_NUMBER() OVER (
                           PARTITION BY n.city, n.target_date, n.metric
                           ORDER BY n.created_at DESC
                       ) AS family_rank
                  FROM requested_families f
                  JOIN no_trade_regret_events n
                    ON n.city = f.city
                   AND n.target_date = f.target_date
                   AND n.metric = f.metric
                  LEFT JOIN opportunity_events e ON e.event_id = n.event_id
                 WHERE n.created_at >= ?
                   AND ({_TERMINAL_NO_VALUE_SQL})
                   AND ({_FORECAST_ONLY_NO_VALUE_REFUTATION_GUARD_SQL})
            )
            SELECT city,
                   target_date,
                   metric,
                   event_id,
                   rejection_reason,
                   created_at,
                   regret_causal_snapshot_id,
                   event_causal_snapshot_id,
                   payload_hash,
                   event_type
              FROM ranked
             WHERE family_rank <= 25
             ORDER BY created_at DESC
            """,
            (*family_params, cutoff),
        ).fetchall()
    except sqlite3.Error:
        rows = ()
    for row in rows:
        family = (str(row[0] or ""), str(row[1] or ""), str(row[2] or ""))
        if family not in event_inputs:
            continue
        family_rows = rows_by_family.setdefault(family, [])
        if len(family_rows) < 25:
            family_rows.append(row)

    refutations: dict[str, RecentNoValueEventRefutation] = {}
    for family, family_events in event_inputs.items():
        rows = rows_by_family.get(family, ())
        for event, causal_snapshot_id, payload_digest in family_events:
            for row in rows:
                reason = str(row[4] or "")
                if _is_operational_non_value_summary(reason):
                    continue
                row_event_type = str(row[9] or "").strip()
                if not _no_value_refutation_event_types_compatible(event.event_type, row_event_type):
                    continue
                prior_payload_hash = str(row[8] or "").strip()
                if payload_digest and prior_payload_hash and payload_digest == prior_payload_hash:
                    evidence_match = "payload_hash"
                else:
                    prior_causal = str(row[7] or row[6] or "").strip()
                    if not (
                        causal_snapshot_id
                        and prior_causal
                        and causal_snapshot_id == prior_causal
                    ):
                        continue
                    evidence_match = "causal_snapshot_id"
                refutations[event.event_id] = RecentNoValueEventRefutation(
                    event_id=str(row[3] or ""),
                    rejection_reason=reason,
                    created_at=str(row[5] or ""),
                    evidence_match=evidence_match,
                )
                break
    return refutations


def recent_no_value_event_refutation(
    conn: sqlite3.Connection,
    event: OpportunityEvent,
    *,
    decision_time: datetime | None = None,
    cooldown_seconds: float = FULL_DECISION_FAMILY_REFUTATION_COOLDOWN_SECONDS,
) -> RecentNoValueEventRefutation | None:
    return recent_no_value_event_refutations(
        conn,
        (event,),
        decision_time=decision_time,
        cooldown_seconds=cooldown_seconds,
    ).get(event.event_id)


_OPPOSITE_SIDE: dict[str, str] = {"buy_yes": "buy_no", "buy_no": "buy_yes"}


def select_exit_order_mode(
    *,
    held_side: str,
    exit_reservation: float,
    actionable_payload: dict,
    quote_payload: dict,
    best_bid: float | None,
    best_ask: float | None,
    executable_snapshot,
) -> str:
    """§4.6 6b — route the EXIT order through the SAME entry-spine order-mode machinery.

    An exit is an entry into the OPPOSITE side, gated by the same §1 governor maker/taker + §2 EV +
    reservation-cap law the entry wave built. This delegates to the entry selector
    (``event_reactor_adapter._select_edli_order_mode``) — it does NOT duplicate the maker/taker logic.
    The held side is flipped to the opposite direction, and the order is capped at the EXIT reservation
    (the break-even of remaining belief edge — the price at which holding >= exiting), so the exit can
    never pay through it (no panic-dump).
    """
    from src.engine.event_reactor_adapter import _select_edli_order_mode

    exit_payload = dict(actionable_payload)
    exit_payload["direction"] = _OPPOSITE_SIDE.get(held_side, held_side)
    exit_payload["c_fee_adjusted"] = float(exit_reservation)  # reservation cap = no pay-through
    return _select_edli_order_mode(
        actionable_payload=exit_payload,
        quote_payload=quote_payload,
        best_bid=best_bid,
        best_ask=best_ask,
        executable_snapshot=executable_snapshot,
    )


# ── P2 SCREEN ORCHESTRATION (resurrection 2026-06-12) ──────────────────────────────────────────
# The reactor-held in-memory dedup state for the entry screen. Held across cycles by the scheduler
# job module (process-global) so a price wiggle does not re-fire (anti price-noise, R6).
def read_freshest_executable_prices(
    trade_conn: sqlite3.Connection,
    *,
    condition_ids: set[str],
) -> dict[tuple[str, str], PriceQuote]:
    """Build a ``(condition_id, direction) → PriceQuote`` map from the freshest already-captured
    ``executable_market_snapshots`` rows. NO new HTTP — reads only what the warm/fast lanes persisted.

    ``executable_market_snapshots`` is native to the selected outcome token: a NO row's
    ``orderbook_top_ask`` is the cost to buy NO, not a YES ask. Prefer native
    selected-token rows for each side and infer the opposite-side quote only from
    the same binary market identity when that side has not been captured. Each quote carries the source
    snapshot's ``freshness_deadline`` so the screen's stale-price guard (R7) is
    exact. Crossed or non-finite books are skipped (no phantom edge)."""
    if not condition_ids:
        return {}
    out: dict[tuple[str, str], PriceQuote] = {}
    try:
        cols = {row[1] for row in trade_conn.execute(
            "PRAGMA table_info(executable_market_snapshots)").fetchall()}
    except sqlite3.Error:
        cols = set()
    token_sides: dict[tuple[str, str], str] = {}
    if {
        "condition_id",
        "orderbook_top_bid",
        "orderbook_top_ask",
        "freshness_deadline",
        "captured_at",
        "selected_outcome_token_id",
        "yes_token_id",
        "no_token_id",
    }.issubset(cols):
        rows = _freshest_executable_price_rows_by_condition(trade_conn, condition_ids=condition_ids)
        token_sides = _condition_side_tokens(rows)
        for cid, side_books in _side_books_by_condition(rows).items():
            for side, book in side_books.items():
                if 0.0 < book["ask"] < 1.0:
                    _merge_price_quote(
                        out,
                        (cid, side),
                        PriceQuote(
                            price=book["ask"],
                            freshness_deadline=str(book["freshness_deadline"]),
                            tick_size=float(book.get("tick_size", TICK_SIZE)),
                        ),
                    )
    for key, quote in _freshest_feasibility_quotes_by_condition(
        trade_conn,
        token_sides=token_sides,
        quote_column="ask",
    ).items():
        _merge_price_quote(out, key, quote)
    return out





def _merge_price_quote(
    quotes: dict[tuple[str, str], PriceQuote],
    key: tuple[str, str],
    quote: PriceQuote,
) -> None:
    existing = quotes.get(key)
    if existing is None:
        quotes[key] = quote
        return
    try:
        existing_deadline = _parse(existing.freshness_deadline).astimezone(timezone.utc)
        new_deadline = _parse(quote.freshness_deadline).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return
    if new_deadline >= existing_deadline:
        quotes[key] = quote


def _freshest_feasibility_quotes_by_condition(
    trade_conn: sqlite3.Connection,
    *,
    token_sides: dict[tuple[str, str], str],
    quote_column: str,
    ttl_seconds: float = FEASIBILITY_QUOTE_FRESHNESS_SECONDS,
) -> dict[tuple[str, str], PriceQuote]:
    if quote_column not in {"bid", "ask"} or not token_sides:
        return {}
    try:
        latest_cols = {
            row[1]
            for row in trade_conn.execute(
                "PRAGMA table_info(execution_feasibility_latest)"
            ).fetchall()
        }
    except sqlite3.Error:
        return {}
    required = {
        "condition_id",
        "outcome_label",
        "direction",
        "quote_seen_at",
        "created_at",
        "best_bid_before",
        "best_ask_before",
    }
    latest_available = required.issubset(latest_cols)
    if not latest_available:
        return {}
    out: dict[tuple[str, str], PriceQuote] = {}

    def _merge_rows(rows: list[sqlite3.Row | tuple], *, condition_id: str, expected_side: str) -> bool:
        merged = False
        for row in rows:
            row_condition_id = str(_row_cell(row, 0, "condition_id") or "").strip()
            if row_condition_id != condition_id:
                continue
            side = _feasibility_row_side(row)
            if side != expected_side:
                continue
            try:
                bid = float(_row_cell(row, 5, "best_bid_before"))
                ask = float(_row_cell(row, 6, "best_ask_before"))
            except (TypeError, ValueError):
                continue
            if not _valid_book(bid, ask):
                continue
            seen_at = _parse_feasibility_quote_time(row)
            if seen_at is None:
                continue
            deadline = seen_at + timedelta(seconds=max(0.0, float(ttl_seconds)))
            price = ask if quote_column == "ask" else bid
            _merge_price_quote(
                out,
                (condition_id, side),
                PriceQuote(
                    price=price,
                    freshness_deadline=deadline.isoformat(),
                    tick_size=TICK_SIZE,
                ),
            )
            merged = True
        return merged

    seen_tokens: set[str] = set()
    for key, raw_token_id in sorted(token_sides.items()):
        condition_id, expected_side = key
        token_id = str(raw_token_id or "").strip()
        if not condition_id or not token_id or token_id in seen_tokens:
            continue
        seen_tokens.add(token_id)
        rows = trade_conn.execute(
            """
            SELECT condition_id,
                   outcome_label,
                   direction,
                   quote_seen_at,
                   created_at,
                   best_bid_before,
                   best_ask_before
              FROM execution_feasibility_latest
             WHERE token_id = ?
             ORDER BY created_at DESC
             LIMIT 4
            """,
            (token_id,),
        ).fetchall()
        _merge_rows(rows, condition_id=condition_id, expected_side=expected_side)
    return out


def _condition_side_tokens(rows: list[sqlite3.Row | tuple]) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for row in rows:
        condition_id = str(_row_cell(row, 0, "condition_id") or "").strip()
        selected = str(_row_cell(row, 4, "selected_outcome_token_id") or "").strip()
        side = _selected_side(row)
        if not condition_id or side is None or not selected:
            continue
        out.setdefault((condition_id, side), selected)
    return out


def _feasibility_row_side(row: sqlite3.Row | tuple) -> str | None:
    outcome = str(_row_cell(row, 1, "outcome_label") or "").strip().upper()
    if outcome == "YES":
        return "buy_yes"
    if outcome == "NO":
        return "buy_no"
    direction = str(_row_cell(row, 2, "direction") or "").strip().lower()
    if direction in {"buy_yes", "sell_yes"}:
        return "buy_yes"
    if direction in {"buy_no", "sell_no"}:
        return "buy_no"
    return None


def _parse_feasibility_quote_time(row: sqlite3.Row | tuple) -> datetime | None:
    for index, key in ((3, "quote_seen_at"), (4, "created_at")):
        raw = str(_row_cell(row, index, key) or "").strip()
        if not raw:
            continue
        try:
            parsed = _parse(raw)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _freshest_executable_price_rows_by_condition(
    trade_conn: sqlite3.Connection,
    *,
    condition_ids: set[str],
) -> list[sqlite3.Row | tuple]:
    """Return newest native-side snapshot price rows per condition via bounded index seeks.

    The previous window query sorted every matching snapshot in a growing
    high-frequency table. Continuous redecision only needs the newest YES and
    newest NO selected-token rows per condition, so use the existing
    ``(condition_id, captured_at DESC)`` index directly and keep the scheduler
    cycle bounded by the number of live conditions it is actually screening.
    """

    rows: list[sqlite3.Row | tuple] = []
    try:
        cols = {row[1] for row in trade_conn.execute(
            "PRAGMA table_info(executable_market_snapshots)").fetchall()}
    except sqlite3.Error:
        return rows
    outcome_select = "outcome_label" if "outcome_label" in cols else "NULL AS outcome_label"
    tick_select = "min_tick_size" if "min_tick_size" in cols else f"{TICK_SIZE!r} AS min_tick_size"
    seen: set[str] = set()
    for raw_condition_id in sorted(condition_ids):
        condition_id = str(raw_condition_id or "").strip()
        if not condition_id or condition_id in seen:
            continue
        seen.add(condition_id)
        predicates = ["condition_id = ?"]
        if "enable_orderbook" in cols:
            predicates.append("COALESCE(enable_orderbook, 1) = 1")
        if "closed" in cols:
            predicates.append("COALESCE(closed, 0) = 0")
        if "accepting_orders" in cols:
            predicates.append("COALESCE(accepting_orders, 1) = 1")
        if _table_exists(trade_conn, "executable_market_snapshot_invalidations"):
            predicates.append(
                """
                NOT EXISTS (
                    SELECT 1
                      FROM executable_market_snapshot_invalidations inv
                     WHERE inv.invalidated_at >= executable_market_snapshots.captured_at
                       AND (
                            inv.condition_id = executable_market_snapshots.condition_id
                            OR inv.token_id = executable_market_snapshots.selected_outcome_token_id
                            OR inv.token_id = executable_market_snapshots.yes_token_id
                            OR inv.token_id = executable_market_snapshots.no_token_id
                       )
                )
                """
            )
        where_clause = " AND ".join(predicates)
        condition_rows = trade_conn.execute(
            """
            SELECT condition_id,
                   orderbook_top_bid,
                   orderbook_top_ask,
                   freshness_deadline,
                   selected_outcome_token_id,
                   yes_token_id,
                   no_token_id,
                   {outcome_select},
                   {tick_select}
              FROM executable_market_snapshots
             WHERE {where_clause}
             ORDER BY captured_at DESC, snapshot_id DESC
             LIMIT 12
            """.format(
                outcome_select=outcome_select,
                tick_select=tick_select,
                where_clause=where_clause,
            ),
            (condition_id,),
        ).fetchall()
        rows.extend(condition_rows)
    return rows


def _row_cell(row: sqlite3.Row | tuple, index: int, key: str) -> object:
    try:
        return row[key] if hasattr(row, "keys") else row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _selected_side(row: sqlite3.Row | tuple) -> str | None:
    outcome = str(_row_cell(row, 7, "outcome_label") or "").strip().upper()
    if outcome == "YES":
        return "buy_yes"
    if outcome == "NO":
        return "buy_no"
    selected = str(_row_cell(row, 4, "selected_outcome_token_id") or "").strip()
    yes_token = str(_row_cell(row, 5, "yes_token_id") or "").strip()
    no_token = str(_row_cell(row, 6, "no_token_id") or "").strip()
    if selected and yes_token and selected == yes_token:
        return "buy_yes"
    if selected and no_token and selected == no_token:
        return "buy_no"
    return None


def _valid_book(bid: float, ask: float) -> bool:
    return 0.0 < bid < ask < 1.0


def _row_tick_size(row: sqlite3.Row | tuple) -> float:
    return _quote_tick_size(_row_cell(row, 8, "min_tick_size"))


def _side_books_by_condition(
    rows: list[sqlite3.Row | tuple],
) -> dict[str, dict[str, dict[str, float | str]]]:
    """Return native books plus binary-complement inferred books by condition and buy side."""

    native: dict[tuple[str, str], dict[str, float | str]] = {}
    inferred: dict[tuple[str, str], dict[str, float | str]] = {}
    for row in rows:
        cid = str(_row_cell(row, 0, "condition_id") or "").strip()
        deadline = str(_row_cell(row, 3, "freshness_deadline") or "").strip()
        side = _selected_side(row)
        if not cid or not deadline or side not in {"buy_yes", "buy_no"}:
            continue
        try:
            bid = float(_row_cell(row, 1, "orderbook_top_bid"))
            ask = float(_row_cell(row, 2, "orderbook_top_ask"))
        except (TypeError, ValueError):
            continue
        if not _valid_book(bid, ask):
            continue
        tick = _row_tick_size(row)
        native.setdefault(
            (cid, side),
            {"bid": bid, "ask": ask, "freshness_deadline": deadline, "tick_size": tick},
        )
        opposite = _OPPOSITE_SIDE[side]
        inferred_bid = one_minus(ask)
        inferred_ask = one_minus(bid)
        if _valid_book(inferred_bid, inferred_ask):
            inferred.setdefault(
                (cid, opposite),
                {
                    "bid": inferred_bid,
                    "ask": inferred_ask,
                    "freshness_deadline": deadline,
                    "tick_size": tick,
                },
            )

    out: dict[str, dict[str, dict[str, float | str]]] = {}
    condition_ids = {cid for cid, _side in set(native) | set(inferred)}
    for cid in condition_ids:
        for side in ("buy_yes", "buy_no"):
            book = native.get((cid, side)) or inferred.get((cid, side))
            if book is not None:
                out.setdefault(cid, {})[side] = book
    return out


def screen_entry_redecisions(
    world_conn: sqlite3.Connection,
    trade_conn: sqlite3.Connection,
    *,
    decision_time: str,
    min_edge: float,
    acted_state: dict[RedecisionScreenKey, float] | None = None,
    beliefs: list[CachedBelief] | None = None,
) -> list[EnqueuedRedecision]:
    """P2 ENTRY screen end-to-end: cached beliefs (world) × freshest executable prices (trade) →
    cheap edge screen → re-decisions. Joins each belief's per-bin condition_ids to the price map, so
    the ``(family_id, bin_label, direction)`` price_lookup ``enqueue_live_redecisions`` consumes is
    keyed correctly without any market-topology re-derivation.

    Pure read on both DBs. NO HTTP, NO writes. The reactor's scheduler job owns ``acted_state``."""
    if beliefs is None:
        beliefs = _all_latest_beliefs(
            world_conn,
            decision_time=decision_time,
            forecast_only_admissible=True,
        )
    # Collect every condition_id referenced by a cached belief (one price read for the batch).
    all_cids: set[str] = set()
    for belief in beliefs:
        all_cids.update(c for c in (belief.condition_ids or []) if c)
    price_by_cid = read_freshest_executable_prices(trade_conn, condition_ids=all_cids)
    recent_rejections = read_recent_full_economics_rejections(world_conn)
    # Re-key the price map onto (family_id, bin_label, direction) the screen expects.
    price_lookup: dict[tuple[str, str, str], PriceQuote] = {}
    for belief in beliefs:
        conds = belief.condition_ids or []
        for idx, label in enumerate(belief.bin_labels):
            if idx >= len(conds):
                continue
            cid = str(conds[idx] or "")
            if not cid:
                continue
            for direction in ("buy_yes", "buy_no"):
                quote = price_by_cid.get((cid, direction))
                if quote is not None:
                    price_lookup[(belief.family_id, label, direction)] = quote
    return enqueue_live_redecisions(
        world_conn,
        decision_time=decision_time,
        price_lookup=price_lookup,
        min_edge=min_edge,
        acted_state=acted_state,
        recent_full_economics_rejections=recent_rejections,
        beliefs=beliefs,
    )


def entry_substrate_refresh_scope(
    trade_conn: sqlite3.Connection,
    *,
    beliefs: list[CachedBelief],
    decision_time: str | datetime,
    max_families: int = 6,
    min_edge: float = 0.01,
    refresh_margin: float = 0.0,
    max_conditions_per_family: int = 2,
) -> dict[tuple[str, str, str], set[str]]:
    """Families whose live beliefs cannot be screened because price substrate is stale.

    The entry screen must not score stale books. But using that same stale-book
    rejection as the only refresh trigger deadlocks discovery: once every book
    expires, no family reaches confirmation refresh and entry trading collapses
    to whichever family happens to have a fresh sidecar row. This helper is a
    read-only input-refresh selector: it asks for fresh executable books for
    open belief families with missing or expired YES/NO quotes, then the normal
    post-refresh screen decides whether any edge exists. It never emits a
    redecision by itself.
    """

    if not beliefs:
        return {}
    try:
        limit = max(1, int(max_families))
    except (TypeError, ValueError):
        limit = 60
    dt = _decision_time_utc(decision_time)
    if dt is None:
        return {}
    all_cids: set[str] = set()
    for belief in beliefs:
        all_cids.update(
            str(c or "").strip()
            for c in (belief.condition_ids or [])
            if str(c or "").strip()
        )
    price_by_cid = read_freshest_executable_prices(trade_conn, condition_ids=all_cids)
    try:
        per_family_limit = max(1, int(max_conditions_per_family))
    except (TypeError, ValueError):
        per_family_limit = 2
    try:
        refresh_floor = float(min_edge) - max(0.0, float(refresh_margin))
    except (TypeError, ValueError):
        refresh_floor = -0.01
    out: dict[tuple[str, str, str], set[str]] = {}
    for belief in beliefs:
        metric = str(
            belief.metric
            or _metric_from_family_id(belief.family_id)
            or _metric_from_bin_labels(belief.bin_labels)
            or ""
        ).strip()
        family_key = (
            str(belief.city or "").strip(),
            str(belief.target_date or "").strip(),
            metric,
        )
        if not (family_key[0] and family_key[1] and family_key[2] in {"high", "low"}):
            continue
        condition_ids = [str(c or "").strip() for c in (belief.condition_ids or [])]
        ranked_refresh: list[tuple[float, str]] = []
        for idx, condition_id in enumerate(condition_ids):
            if not condition_id:
                continue
            best_refresh_score: float | None = None
            for direction in ("buy_yes", "buy_no"):
                quote = price_by_cid.get((condition_id, direction))
                q_lcb = (
                    _vec_float_at(belief.q_lcb_yes_vec, idx)
                    if direction == "buy_yes"
                    else _vec_float_at(belief.q_lcb_no_vec, idx)
                )
                if q_lcb is None:
                    continue
                try:
                    posterior = (
                        float(belief.p_posterior_vec[idx])
                        if direction == "buy_yes"
                        else one_minus(float(belief.p_posterior_vec[idx]))
                    )
                except (IndexError, TypeError, ValueError):
                    posterior = float(q_lcb)
                if quote is None:
                    # A missing executable quote is not evidence of value. The
                    # previous 0.50 placeholder made high-probability NO legs on
                    # almost every non-winning bin outrank an actually priced YES
                    # edge, expanding confirm-refresh into broad family warming.
                    # Broad discovery owns missing substrate; this live money
                    # path only refreshes candidates whose current/stale price can
                    # still prove a near-edge before the post-refresh screen emits.
                    continue
                try:
                    if _parse(quote.freshness_deadline).astimezone(timezone.utc) <= dt:
                        score = _entry_screen_robust_trade_score(
                            q_posterior=posterior,
                            q_lcb_5pct=float(q_lcb),
                            price=float(quote.price),
                            tick_size=quote.tick_size,
                        )
                        best_refresh_score = (
                            score
                            if best_refresh_score is None
                            else max(best_refresh_score, score)
                        )
                except (TypeError, ValueError):
                    best_refresh_score = (
                        float(q_lcb) - 0.5
                        if best_refresh_score is None
                        else max(best_refresh_score, float(q_lcb) - 0.5)
                    )
            if best_refresh_score is not None and best_refresh_score >= refresh_floor:
                ranked_refresh.append((best_refresh_score, condition_id))
        if ranked_refresh:
            ranked_refresh.sort(reverse=True)
            out[family_key] = {
                condition_id
                for _score, condition_id in ranked_refresh[:per_family_limit]
            }
            if len(out) >= limit:
                break
    return out


def _latest_posterior_source_cycle_for_family(
    forecasts_conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    decision_time: str,
) -> str | None:
    latest = _latest_posterior_for_family(
        forecasts_conn,
        city=city,
        target_date=target_date,
        metric=metric,
        decision_time=decision_time,
    )
    return None if latest is None else latest[1]


def _latest_posterior_for_family(
    forecasts_conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    decision_time: str,
) -> tuple[object, str] | None:
    """(posterior_id or None, source_cycle_time) of the family's latest live posterior."""
    if not _table_exists(forecasts_conn, "forecast_posteriors"):
        return None
    columns = _table_columns(forecasts_conn, "forecast_posteriors")
    required = {"city", "target_date", "temperature_metric", "source_cycle_time"}
    if not required.issubset(columns):
        return None
    if "runtime_layer" not in columns:
        return None
    predicates = ["city = ?", "target_date = ?", "temperature_metric = ?"]
    params: list[object] = [city, target_date, metric]
    predicates.append("runtime_layer = 'live'")
    if "source_id" in columns:
        predicates.append("source_id = ?")
        params.append(LIVE_REPLACEMENT_POSTERIOR_SOURCE_ID)
    if "source_available_at" in columns:
        predicates.append("source_available_at <= ?")
        params.append(decision_time)
    if "computed_at" in columns:
        predicates.append("computed_at <= ?")
        params.append(decision_time)
    order_fields = ["source_cycle_time DESC"]
    if "computed_at" in columns:
        order_fields.append("computed_at DESC")
    id_select = "NULL"
    if "posterior_id" in columns:
        order_fields.append("posterior_id DESC")
        id_select = "posterior_id"
    try:
        row = forecasts_conn.execute(
            f"""
            SELECT {id_select}, source_cycle_time
              FROM forecast_posteriors
             WHERE {' AND '.join(predicates)}
             ORDER BY {', '.join(order_fields)}
             LIMIT 1
            """,
            tuple(params),
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None or row[1] is None:
        return None
    cycle = str(row[1]).strip()
    return (row[0], cycle) if cycle else None


def _raw_model_member_count_for_cycle(
    forecasts_conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    source_cycle_time: str,
    decision_time: str,
) -> int:
    if not _table_exists(forecasts_conn, "raw_model_forecasts"):
        return 0
    columns = _table_columns(forecasts_conn, "raw_model_forecasts")
    required = {"model", "city", "target_date", "metric", "source_cycle_time", "forecast_value_c"}
    if not required.issubset(columns):
        return 0
    cycle_date = str(source_cycle_time or "")[:10]
    if len(cycle_date) != 10:
        return 0
    predicates = [
        "city = ?",
        "target_date = ?",
        "metric = ?",
        "date(source_cycle_time) = ?",
        "forecast_value_c IS NOT NULL",
    ]
    params: list[object] = [city, target_date, metric, cycle_date]
    if "source_available_at" in columns:
        predicates.append("source_available_at <= ?")
        params.append(decision_time)
    try:
        row = forecasts_conn.execute(
            f"""
            SELECT COUNT(DISTINCT model)
              FROM raw_model_forecasts
             WHERE {' AND '.join(predicates)}
            """,
            tuple(params),
        ).fetchone()
    except sqlite3.Error:
        return 0
    try:
        return int(row[0] or 0) if row is not None else 0
    except (TypeError, ValueError):
        return 0


def filter_redecisions_with_spine_members(
    forecasts_conn: sqlite3.Connection,
    redecisions: list[EnqueuedRedecision],
    *,
    beliefs: list[CachedBelief],
    decision_time: str,
) -> list[EnqueuedRedecision]:
    """Keep only entry redecisions whose full q-kernel spine inputs can be served.

    The cheap entry screen proves fresh price plus conservative q_lcb edge; the downstream
    q-kernel also requires raw_model_forecasts provider members on the same posterior
    source-cycle date: three, or the posterior's own certified carrier count. Without
    that second proof, the reactor only emits SPINE_INPUTS_UNAVAILABLE:MU_SIGMA_NOT_STASHED
    and clogs the live lane. Held positions are intentionally outside this entry filter;
    monitor/exit owns hold/exit/shift.
    """
    if not redecisions:
        return []
    by_family = {belief.family_id: belief for belief in beliefs}
    availability: dict[tuple[str, str, str], bool] = {}
    out: list[EnqueuedRedecision] = []
    for rd in redecisions:
        belief = by_family.get(rd.family_id)
        if belief is None:
            continue
        family = _stable_family_screen_key(belief)
        if family is None:
            continue
        _, city, target_date, metric = family
        key = (city, target_date, metric)
        ok = availability.get(key)
        if ok is None:
            latest = _latest_posterior_for_family(
                forecasts_conn,
                city=city,
                target_date=target_date,
                metric=metric,
                decision_time=decision_time,
            )
            count = (
                _raw_model_member_count_for_cycle(
                    forecasts_conn,
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    source_cycle_time=latest[1],
                    decision_time=decision_time,
                )
                if latest
                else 0
            )
            ok = latest is not None and posterior_admits_spine_members(
                forecasts_conn, posterior_id=latest[0], member_count=count,
            )
            availability[key] = ok
        if ok:
            out.append(rd)
    return out


def screened_family_keys(
    world_conn: sqlite3.Connection,
    redecisions: list[EnqueuedRedecision],
    *,
    beliefs: list[CachedBelief] | None = None,
) -> set[tuple[str, str, str]]:
    """Map firing redecisions → the ``(city, target_date, metric)`` family keys the P2 job feeds to
    the FSR re-emitter's ``restrict_to_families``. Resolved from each redecision's family_id via the
    cached belief (city/target_date/metric), so only screened families re-emit — never the universe."""
    by_family: dict[str, tuple[str, str, str]] = {}
    for belief in beliefs if beliefs is not None else _all_latest_beliefs(world_conn):
        by_family[belief.family_id] = (belief.city, belief.target_date, belief.metric)
    out: set[tuple[str, str, str]] = set()
    for rd in redecisions:
        key = by_family.get(rd.family_id)
        if key is not None and all(key):
            out.add(key)
    return out


@dataclass(frozen=True)
class OpenRest:
    """One open maker rest joined to the belief snapshot it was priced on. Built by the scheduler
    job from venue_commands + venue_order_facts (the rest) and the command's decision belief."""
    command_id: str
    venue_order_id: str
    family_id: str
    bin_label: str
    side: str
    condition_id: str
    resting_posterior: float
    resting_snapshot_id: str
    limit_price: float
    quote_age_ms: float
    created_at: str = ""
    fact_state: str = ""
    matched_size: float | None = None
    min_order_size: float | None = None
    city: str = ""
    target_date: str = ""
    metric: str = ""
