"""EDLI opportunity event reactor.

This module intentionally has no venue-adapter import. Execution side effects
must flow through injected final-intent/executor seams owned by `src.engine` and
`src.execution`.
"""

# Last reused/audited: 2026-06-12
# Authority basis (2026-06-12 external deep-review): registered TRANSIENT money-path
#   reason bases LIVE_DEPTH_AUTHORITY_MISSING (FINDING-A taker-depth twin-authority),
#   BANKROLL_FREE_CASH_MISSING (FINDING-B free-cash bound under injected provider) and
#   QLCB_COVERAGE_AUTHORITY_FAULT (FINDING-C settlement-coverage shrinker fail-closed)
#   for the event_reactor_adapter fail-closed fixes.
# Authority basis (2026-06-11 operator follow-up): (a) SOURCE_TRUTH intake gate
#   defers to the serving authority — coverage PARTIAL/BLOCKED passes through,
#   dead-letter only for junk run identity (twin-authority #8, 16:33:51Z six-city
#   incident); (b) price-race aborts (SUBMIT_ABORTED_PRICE_MOVED, would_cross_book
#   certificate failure) classify TRANSIENT → bounded requeue (Miami/NYC 16:22Z);
#   (c) pre-event cycle-budget check caps overrun to one in-flight event;
#   (d) claim-storm kill (17:51Z): Window A BEGIN IMMEDIATE (busy handler engaged
#   deterministically, BUSY_SNAPSHOT category closed), lock-bounce rollback +
#   claim_lock_bounces visibility, pre-fetch dangling-txn guard. Root cause was
#   main._edli_pending_entity_keys leaking PRAGMA busy_timeout=250 onto the
#   shared claim conn (now save/restore-scoped).
# Prior: #95 SEV-2.1 — world_write_mutex MUST NOT be held across the
#   injected submit callable's network I/O (JIT /book HTTP fetch + venue order
#   POST). _process_event_unit split into two committed world-DB write windows
#   around the network submit boundary; contract is db.py world_write_lock /
#   world_write_mutex ("never hold across HTTP") + INV-37.
#   P1 ZERO-SUBMIT FIX B (2026-06-05, iron-rule-1, co-cause): _finalize_reservation
#   commits/rolls back the adapter's PROVISIONAL per-cycle in-flight reservation
#   so a candidate rejected downstream of Kelly (DECISION_CERTIFICATE /
#   EXECUTOR_EXPRESSIBILITY) never inflates corr/raw committed for later
#   same-cycle candidates (INV-K7 preserved for emitted bets).
#   MAJOR #5 (2026-06-05): the network-submit ``except`` now also rolls back the
#   provisional reservation (_finalize_reservation emitted=False) so a _submit
#   that raises AFTER reserve() — mid-submit DB/HTTP fault — cannot orphan a live
#   reservation that over-counts committed for the next same-cycle event.

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field, replace as dataclass_replace
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from src.decision_kernel import claims
from src.decision_kernel.compiler import NoSubmitProofBundle
from src.events.day0_authority import normalize_day0_live_authority_status
from src.events.event_store import EventStore, GLOBAL_WINNER_TARGETED_CLAIM
from src.events.opportunity_event import OpportunityEvent, assert_available_for_decision
from src.state.db import get_trade_connection_read_only, get_world_connection_read_only, world_write_mutex
from src.strategy.live_inference.live_admission import (
    live_buy_no_conservative_evidence_rejection_reason,
    replacement_no_bound_expected_from_parents,
)

UTC = timezone.utc

DEFAULT_REACTOR_CYCLE_BUDGET_SECONDS = 22.0
DEFAULT_REACTOR_FETCH_BATCH_LIMIT = 50
DEFAULT_SNAPSHOT_BLOCK_RETRY_DELAY_SECONDS = 60.0
DEFAULT_SNAPSHOT_BLOCK_RETRY_MAX_DELAY_SECONDS = 600.0
DEFAULT_RUNTIME_AUTHORITY_RETRY_DELAY_SECONDS = 300.0
DEFAULT_REACTOR_CLAIM_BUSY_TIMEOUT_MS = 750
DEFAULT_REACTOR_LANE_FAIRNESS_FETCH_MIN_EXTRA = 50
DEFAULT_REACTOR_LANE_FAIRNESS_FETCH_MULTIPLIER = 4
MARKET_CHANNEL_CONTINUITY_FILENAME = "market-channel-continuity.json"


def _portfolio_snapshot_submit_gate(
    *,
    live_submit_effective: bool,
    snapshot_required: bool,
    snapshot_available: bool,
) -> tuple[bool, str | None]:
    if snapshot_required and not snapshot_available:
        return False, "live_submit_effective_false:portfolio_state_unavailable"
    return live_submit_effective, None


def _is_sqlite_lock_error(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return (
        "database is locked" in message
        or "database table is locked" in message
        or "database is busy" in message
    )


def _cycle_budget_seconds() -> float | None:
    """Per-cycle wall-clock budget for process_pending (E1 / STEP 8).

    Default 30.0s; override via ``ZEUS_REACTOR_CYCLE_BUDGET_SECONDS``. A value of
    0 or negative disables the budget (unbounded cycle, legacy behavior). A
    malformed env value falls back to the default rather than crashing the
    reactor.
    """
    raw = os.environ.get("ZEUS_REACTOR_CYCLE_BUDGET_SECONDS")
    if raw is None:
        return DEFAULT_REACTOR_CYCLE_BUDGET_SECONDS
    try:
        budget = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_REACTOR_CYCLE_BUDGET_SECONDS
    return budget if budget > 0 else None


def _fetch_batch_limit() -> int:
    """Pagination size for unbounded ``process_pending(limit=None)`` cycles.

    This is not a work cap: pending events stay pending and the cycle budget
    decides when to return. Keeping each read page small prevents one large
    world-DB fetch from spending the whole scheduler interval before the budget
    guard can run.
    """
    raw = os.environ.get("ZEUS_REACTOR_FETCH_BATCH_LIMIT")
    if raw is None:
        return DEFAULT_REACTOR_FETCH_BATCH_LIMIT
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_REACTOR_FETCH_BATCH_LIMIT
    return max(1, min(250, limit))


def _lane_fairness_fetch_limit(process_limit: int) -> int:
    """Overfetch enough rows for cross-lane fairness before applying work limit.

    ``fetch_pending`` is tier ordered: live Day0 rows can legitimately sort ahead
    of ordinary forecast rows.  The reactor's cross-lane interleave can only
    protect the forecast/redecision lane if both lanes are present in the fetched
    page.  Keep ``process_limit`` as the hard per-cycle work cap, but request a
    slightly wider page so a small live limit (for cadence) does not truncate the
    forecast lane before interleaving.
    """

    try:
        limit = int(process_limit)
    except (TypeError, ValueError):
        limit = DEFAULT_REACTOR_FETCH_BATCH_LIMIT
    limit = max(1, limit)
    return min(
        250,
        max(
            limit,
            limit * DEFAULT_REACTOR_LANE_FAIRNESS_FETCH_MULTIPLIER,
            limit + DEFAULT_REACTOR_LANE_FAIRNESS_FETCH_MIN_EXTRA,
        ),
    )


def _reactor_claim_busy_timeout_ms() -> int:
    """SQLite busy timeout for the pre-submit claim window.

    The live reactor must not spend a whole cycle waiting for another writer
    before it has emitted any order. A claim lock miss is retryable because the
    event remains pending; keep the wait long enough to absorb ordinary
    millisecond-scale WAL overlap, but short enough that redecision/day0 cadence
    survives a stuck writer.
    """

    raw = os.environ.get("ZEUS_REACTOR_CLAIM_BUSY_TIMEOUT_MS")
    if raw is None:
        return DEFAULT_REACTOR_CLAIM_BUSY_TIMEOUT_MS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_REACTOR_CLAIM_BUSY_TIMEOUT_MS
    return max(1, min(30_000, value))


def _sqlite_busy_timeout_ms(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA busy_timeout").fetchone()
    return int(row[0]) if row is not None else 0


@contextlib.contextmanager
def _scoped_sqlite_busy_timeout(conn: sqlite3.Connection, timeout_ms: int):
    previous = _sqlite_busy_timeout_ms(conn)
    conn.execute(f"PRAGMA busy_timeout = {int(timeout_ms)}")
    try:
        yield
    finally:
        conn.execute(f"PRAGMA busy_timeout = {previous}")


def _snapshot_block_retry_delay_seconds(*, attempt_count: int = 1) -> float:
    """Retry floor for executable-snapshot substrate blocks.

    A blocked family is already delegated to the substrate refresher. Reclaiming
    the same event immediately only spends decision budget before that refresh
    can land. The delay is horizon-bounded; it does not terminalize or suppress
    the event. Repeated substrate failures back off so one uncapturable Day0
    family cannot spend a Tier-0 slot every scheduler minute.
    """

    raw = os.environ.get("ZEUS_SNAPSHOT_BLOCK_RETRY_DELAY_SECONDS")
    if raw is None:
        delay = DEFAULT_SNAPSHOT_BLOCK_RETRY_DELAY_SECONDS
    else:
        try:
            delay = float(raw)
        except (TypeError, ValueError):
            delay = DEFAULT_SNAPSHOT_BLOCK_RETRY_DELAY_SECONDS
    base_delay = max(5.0, min(300.0, delay))
    max_raw = os.environ.get("ZEUS_SNAPSHOT_BLOCK_RETRY_MAX_DELAY_SECONDS")
    try:
        max_delay = (
            float(max_raw)
            if max_raw is not None
            else DEFAULT_SNAPSHOT_BLOCK_RETRY_MAX_DELAY_SECONDS
        )
    except (TypeError, ValueError):
        max_delay = DEFAULT_SNAPSHOT_BLOCK_RETRY_MAX_DELAY_SECONDS
    max_delay = max(base_delay, min(1800.0, max_delay))
    try:
        attempt = int(attempt_count or 1)
    except (TypeError, ValueError):
        attempt = 1
    multiplier = max(1, min(attempt, 10))
    return min(max_delay, base_delay * multiplier)


def _runtime_authority_retry_delay_seconds(reason: str | None = None) -> float:
    """Retry floor for runtime authority blocks that cannot clear intra-cycle.

    Entry pauses and live-health entry-authority gaps are control/runtime state,
    not fresh market facts. Immediate reclaims only replay the same fail-closed
    block while the authority surface is still dark or intentionally paused.
    Keep the floor bounded so recovery still re-decides surviving events soon
    after the operator/runtime state clears.
    """

    raw = os.environ.get("ZEUS_RUNTIME_AUTHORITY_RETRY_DELAY_SECONDS")
    if raw is None:
        delay = DEFAULT_RUNTIME_AUTHORITY_RETRY_DELAY_SECONDS
    else:
        try:
            delay = float(raw)
        except (TypeError, ValueError):
            delay = DEFAULT_RUNTIME_AUTHORITY_RETRY_DELAY_SECONDS
    bounded = max(30.0, min(900.0, delay))
    if _is_current_wealth_retry_reason(reason):
        return min(60.0, bounded)
    return bounded


DEFAULT_REACTOR_DRAIN_BUDGET_SECONDS = 10.0
DEFAULT_SUBSTRATE_SIDECAR_HEARTBEAT_MAX_AGE_SECONDS = 75.0


def _drain_budget_seconds() -> float | None:
    """Per-cycle wall-clock budget for the END-of-cycle substrate-refresh DRAIN
    (``_drain_substrate_refreshes``).

    BACKGROUND-I/O TIME BUDGET, not a money-path cap. The drain refreshes the
    executable-snapshot substrate for EVERY family blocked this cycle; with ~49
    blocked families that is ~49 /book network fetches per cycle, which is the
    cycle-overrun root cause (#83 redecide_block_2026-06-16 §3): the reactor is
    APScheduler-scheduled every 60s but the cycle wall-time blows past 60s, so the
    schedule COALESCES into 3-13 min real gaps → ~1 family decided per cycle → the
    49-city harvest crosses far too slowly for continuous fills.

    SCHEDULE MATH (justifies the 10.0s default): the reactor runs on an
    ``interval, minutes=1`` job (``src/main.py:9486``, 60s). The per-cycle DECISION
    budget is 30s (``_cycle_budget_seconds`` / ``ZEUS_REACTOR_CYCLE_BUDGET_SECONDS``).
    A 10s drain budget gives 30s decision + 10s drain = 40s, leaving ~20s headroom
    for fetch_pending reads, status-pulse writes, scheduler dispatch and connection
    teardown — so the whole cycle fits inside the 60s schedule with margin and the
    coalescing stops. This is the SAME kind of bound as the warm-cycle refresher's
    ``ZEUS_REACTOR_REFRESH_BUDGET_SECONDS`` (default 17.0s inside a 20s interval,
    ``src/main.py:3504``): a wall-clock budget on background substrate I/O.

    When the budget is spent the drain STOPS after finishing the current family
    (never mid-network) and leaves the unreached families in ``_pending_*`` for the
    NEXT cycle; the drain's fair-cursor rotation (held-position families always
    first, then round-robin) guarantees bounded-cycle coverage with no starvation —
    exactly the "future per-cycle fan-out cap" the drain ordering comment already
    anticipates. Default 10.0s; override via ``ZEUS_REACTOR_DRAIN_BUDGET_SECONDS``.
    A value of 0 or negative disables the budget (unbounded drain, legacy behavior).
    A malformed env value falls back to the default rather than crashing the reactor.
    """
    raw = os.environ.get("ZEUS_REACTOR_DRAIN_BUDGET_SECONDS")
    if raw is None:
        return DEFAULT_REACTOR_DRAIN_BUDGET_SECONDS
    try:
        budget = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_REACTOR_DRAIN_BUDGET_SECONDS
    return budget if budget > 0 else None


def _truthy_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _substrate_sidecar_owns_broad_refresh(*, now: datetime | None = None) -> bool:
    """True when the dedicated substrate observer is fresh enough to own broad warmup.

    The live split moved market-substrate warming out of ``src.main``. Keeping the
    old end-of-cycle broad drain in the reactor duplicates the sidecar's writer and
    turns stale executable prices into DB-lock stalls. The reactor still supports a
    bounded fallback when the sidecar is absent/stale, and the adapter's targeted
    single-family refresh remains available for the selected stale row.
    """

    if _truthy_env(os.environ.get("ZEUS_REACTOR_FORCE_BROAD_SUBSTRATE_DRAIN")):
        return False
    if str(os.environ.get("ZEUS_SUBSTRATE_SIDECAR_OWNS_BROAD_REFRESH", "1")).strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False

    path = os.environ.get(
        "ZEUS_SUBSTRATE_OBSERVER_HEARTBEAT_PATH",
        os.path.join(os.getcwd(), "state", "daemon-heartbeat-substrate-observer.json"),
    )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        alive_raw = str(payload.get("alive_at") or "").strip()
        alive_at = _parse_utc_instant(alive_raw)
        if alive_at is None:
            return False
        checked_at = now.astimezone(UTC) if now is not None else datetime.now(UTC)
        age_seconds = (checked_at - alive_at).total_seconds()
    except Exception:
        return False
    try:
        max_age = float(
            os.environ.get(
                "ZEUS_SUBSTRATE_SIDECAR_HEARTBEAT_MAX_AGE_SECONDS",
                str(DEFAULT_SUBSTRATE_SIDECAR_HEARTBEAT_MAX_AGE_SECONDS),
            )
        )
    except (TypeError, ValueError):
        max_age = DEFAULT_SUBSTRATE_SIDECAR_HEARTBEAT_MAX_AGE_SECONDS
    return 0.0 <= age_seconds <= max(1.0, max_age)


def _operator_disarm_active() -> bool:
    """Horizon (c): operator env kill-switch for in-flight money-path transients.

    Truthy ``ZEUS_REACTOR_TRANSIENT_DISARM`` => the operator has disarmed the
    transient requeue lane; in-flight transients terminalize with an honest
    OPERATOR_DISARM horizon instead of spinning. Unset/empty/"0"/"false"/"no"
    => armed (normal indefinite requeue). A malformed value is treated as armed
    (fail-open to requeue — a typo must never silently burn live events).
    """
    raw = os.environ.get(_TRANSIENT_DISARM_ENV)
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DRY_EXECUTION_RECEIPT_TERMINAL_STATUSES = frozenset({"SUBMIT_DISABLED", "NOT_SUBMITTED_DRY_RUN"})
LIVE_EXECUTION_RECEIPT_TERMINAL_STATUSES = frozenset({
    "SUBMITTED",
    "REJECTED",
    "TIMEOUT_UNKNOWN",
    "PRE_SUBMIT_ERROR",
    "POST_SUBMIT_UNKNOWN",
})
EXECUTION_RECEIPT_TERMINAL_STATUSES = DRY_EXECUTION_RECEIPT_TERMINAL_STATUSES | LIVE_EXECUTION_RECEIPT_TERMINAL_STATUSES
EDLI_PROCESSING_REACTOR_MODES = frozenset({"live", "live_no_submit"})
# Continuous re-decision resurrection (2026-06-12): the forecast decision lane includes the
# price-driven re-decision type. Mirrors src.engine.event_reactor_adapter._FORECAST_DECISION_EVENT_TYPES
# and src.events.continuous_redecision.REDECISION_EVENT_TYPE (literal here to avoid an import cycle:
# continuous_redecision lazily imports the adapter which imports this module). An
# EDLI_REDECISION_PENDING event carries the same FSR-shaped payload and gets the same structural
# source-truth dead-letter treatment as a forecast snapshot event.
_FORECAST_DECISION_EVENT_TYPES = frozenset({"FORECAST_SNAPSHOT_READY", "EDLI_REDECISION_PENDING"})


def _fair_lane_interleave(events: list) -> list:
    """Round-robin the forecast-decision lane against the rest (day0) 1:1.

    fetch_pending returns all Tier-0 DAY0_EXTREME_UPDATED before any Tier-1
    FORECAST_SNAPSHOT_READY; under a bounded per-cycle decision budget (~3-4 slow
    decisions, and in the live degenerate case where ONE decision eats the whole 45s
    budget, effectively ~1) the day0 lane consumes the whole budget and the
    forecast/spine harvest lane is never processed. This interleaves the two DECISION
    lanes 1:1 so each gets a fair half of the budget. The forecast lane and the rest
    each keep their incoming (per-city-fair) order; only the cross-lane alternation is
    added.

    FORECAST-FIRST (2026-06-16, live zero-fill root cause): the forecast harvest lane
    (the q-kernel spine — the operator's alpha target) takes the FIRST slot. Live
    2026-06-16: 176 FORECAST_SNAPSHOT_READY families sat attempt_count=0 (never claimed)
    while the reactor claimed ~2 day0 families/cycle, because day0 held the first slot
    and a single slow day0 decision exhausted the 45s budget before the alternation ever
    reached a forecast family (processed=0 forecast decisions / 12min). When the budget
    only completes ~1 decision, whichever lane holds the first slot is the ONLY lane that
    runs — so the harvest lane must hold it. Order-only: each family is decided on its
    own fresh inputs, so processing order does not affect decision correctness; this only
    changes which lane is guaranteed budget under starvation.
    """
    forecast = [e for e in events if getattr(e, "event_type", None) in _FORECAST_DECISION_EVENT_TYPES]
    if not forecast:
        return events  # nothing to protect — keep the cheap fast path unchanged
    rest = [e for e in events if getattr(e, "event_type", None) not in _FORECAST_DECISION_EVENT_TYPES]
    if not rest:
        return events
    out: list = []
    i = j = 0
    while i < len(forecast) or j < len(rest):
        if i < len(forecast):
            out.append(forecast[i])
            i += 1
        if j < len(rest):
            out.append(rest[j])
            j += 1
    return out

# Event types that may carry explicit venue-closed evidence. Static city/date
# geometry is not enough to terminalize them: Gamma endDate is a resolution
# timestamp, while live order-entry authority is closed + accepting_orders.
_VENUE_CLOSE_HORIZON_EVENT_TYPES = _FORECAST_DECISION_EVENT_TYPES | frozenset(
    {"DAY0_EXTREME_UPDATED"}
)

Gate = Callable[[OpportunityEvent], bool]
ExecutableSnapshotGate = Callable[[OpportunityEvent, datetime], bool]
Reject = Callable[[OpportunityEvent, str, str], None]


@dataclass(frozen=True)
class EventSubmissionReceipt:
    """Proof that an executor-facing intent belongs to the current EDLI event.

    ``proof_accepted`` means the EDLI reactor accepted the event-bound
    money-path proof. ``submitted`` is reserved for real executor/venue submit
    semantics and must stay false for ``side_effect_status=NO_SUBMIT``.
    """

    submitted: bool
    event_id: str
    causal_snapshot_id: str | None = None
    city: str | None = None
    target_date: str | None = None
    metric: str | None = None
    condition_id: str | None = None
    token_id: str | None = None
    outcome_label: str | None = None
    candidate_id: str | None = None
    executable_snapshot_id: str | None = None
    family_id: str | None = None
    bin_label: str | None = None
    direction: str | None = None
    q_live: float | None = None
    q_lcb_5pct: float | None = None
    c_fee_adjusted: float | None = None
    c_cost_95pct: float | None = None
    p_fill_lcb: float | None = None
    trade_score: float | None = None
    native_quote_available: bool | None = None
    source_status: str | None = None
    family_complete: bool | None = None
    trade_score_positive: bool = False
    fdr_pass: bool = False
    fdr_family_id: str | None = None
    fdr_hypothesis_count: int = 0
    kelly_pass: bool = False
    kelly_execution_price_type: str | None = None
    kelly_price_fee_deducted: bool = False
    kelly_size_usd: float = 0.0
    kelly_cost_basis_id: str | None = None
    kelly_decision_id: str | None = None
    risk_decision_id: str | None = None
    final_intent_id: str | None = None
    neg_risk: bool = False
    side_effect_status: str = "NO_SUBMIT"
    reason: str = ""
    proof_accepted: bool | None = None
    decision_proof_bundle: Any | None = field(default=None, repr=False, compare=False)
    # Mainstream-agreement gate fields (#135, 2026-06-03).
    # None = gate not evaluated (flag OFF or evaluation error).
    mainstream_agreement_pass: bool | None = None
    mainstream_agreement_fail_reason: str | None = None
    mainstream_point: float | None = None
    mainstream_delta: float | None = None
    mainstream_bin_label: str | None = None
    mainstream_source: str | None = None
    mainstream_fetched_at_utc: str | None = None
    # B2 (PR-4, 2026-06-03): edge-axis plumbing.
    # alpha_gap = q_live - c_fee_adjusted (direction-adjusted posterior minus
    # executable market price).  Positive = our estimate exceeds the ask price.
    # NULL when c_fee_adjusted is NULL (no executable quote available — fail-closed;
    # the Phase-2 gate will handle NULL explicitly).  Read-only observation column;
    # no selection or gate behavior here.
    alpha_gap: float | None = None
    # #120 (2026-06-04): which calibrator produced q_live for this receipt.
    # "emos" = EMOS sole-calibrator served this (city,season,metric) cell;
    # "bias_platt"/"platt" = the maze fallback (bias-corrected / plain Platt).
    # None = not tagged (gate path / error receipts). Persisted in receipt_json
    # ONLY when set (omit-when-None for hash stability) so 06-05+ settlement can
    # attribute EMOS-cells vs maze-cells per city — the PROMOTE evidence.
    q_source: str | None = None
    # Day0 remaining-window probability authority. This is the proof that a same-day
    # observed extreme was used as a settlement mask/bound while q/q_lcb came from
    # remaining-day forecast members, not from treating the observed bin as a
    # hard-fact probability model. None on non-Day0 and legacy receipts.
    day0_probability_authority: dict[str, Any] | None = None
    # qkernel spine execution economics certificate. This is distinct from q_live /
    # q_lcb_5pct, which are receipt-facing probability provenance. When present,
    # execution sizing is accountable to this guarded payoff-space certificate.
    # Omitted when None so non-qkernel receipts keep stable JSON.
    qkernel_execution_economics: dict[str, Any] | None = None
    # B3 authority stamp propagated from the proof (qkernel_spine_bridge sets
    # selection_authority_applied="qkernel_spine" while PRESERVING q_source as the
    # probability track label). The taker-quality proof reads this to recognize a
    # spine-selected taker under the live "replacement_0_1" q_source.
    selection_authority_applied: str | None = None
    # B3 identity key: the reactor's _candidate_bin_id(proof) — byte-identical to the
    # qkernel cert's bin_id — so the taker-quality proof can match the cert to THIS
    # selected leg across the two candidate_id namespaces (qkernel SIDE:bin_id:route_id
    # vs reactor family_id:condition_id). Threaded the same way q_source / the stamp are.
    candidate_bin_id: str | None = None
    strategy_key: str | None = None
    # Live execution-quality floors proven by the selected strategy profile.
    # These travel with new event-bound receipts so the final pre-submit append
    # can verify the actual submitted order size/price clears the strategy's
    # minimum executable profit and edge-density bar. None on legacy receipts;
    # omit-when-None in receipt_json keeps existing hashes stable.
    min_entry_price: float | None = None
    min_expected_profit_usd: float | None = None
    min_submit_edge_density: float | None = None
    # Telemetry-only Opportunity Book selector evidence. Omitted from receipt_json
    # when None so pre-book receipts keep byte-identical hashes.
    opportunity_book: dict[str, Any] | None = None
    replacement_forecast: dict[str, Any] | None = None
    unit: str | None = None
    q_lcb_calibration_source: str | None = None
    # Independently-materialized YES-bin posterior for the SAME settlement bin as
    # this candidate (== yes_q from the q-vector; NEVER a 1-price / 1-q_no
    # complement). It is the buy-NO conservative-evidence gate input: the ADAPTER
    # gate (event_reactor_adapter.py) evaluates the gate WITH this value, then the
    # post-submit receipt-level re-enforcement (_receipt_money_path_blocker) MUST
    # see the SAME value — without this field the receipt-level gate defaulted the
    # posterior to None and rejected every buy_no with
    # ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING (Shanghai 32°C 2026-06-11,
    # docs/evidence/settlement_guard/2026-06-11_yesq_wiring_plan.md). None on
    # buy-YES / canonical / legacy receipts; omitted-when-None from receipt_json so
    # those receipts keep byte-identical hashes.
    same_bin_yes_posterior: float | None = None
    # Twin-authority reconciliation #7 (2026-06-11; selected-leg repair 2026-06-30):
    # settlement-backward coverage VERDICT status for the exact condition+direction
    # ("LICENSED"/"UNLICENSED"/"INSUFFICIENT_DATA"; None on canonical/legacy receipts).
    # The adapter admission gate and receipt-level re-enforcement must see the same
    # selected-leg value.
    # Omitted-when-None from receipt_json so existing hashes stay byte-stable.
    settlement_coverage_status: str | None = None
    # Selected-leg proof that replacement_0_1 constructed native NO from the same
    # posterior as YES: q_no=1-q_yes, raw_lcb_no=1-ucb_yes, and any served
    # settlement shrink only lowers that raw bound. None on canonical/legacy/YES
    # receipts; the admission helper revalidates every scalar and identity hash.
    replacement_no_bound_certificate: dict[str, Any] | None = None
    # H2_E2E (REAUDIT_0_1.md §2/§4): typed carriers so every replacement_0_1 order
    # is SQL-reconstructable forecast(posterior_id) -> ... -> fill WITHOUT
    # JSON_EXTRACT. None on canonical/legacy receipts (observability only — these
    # never change a trading decision and are omitted-when-None from receipt_json
    # so existing-row hashes stay byte-stable).
    posterior_id: int | None = None
    probability_authority: str | None = None
    # P0 mode-authority (operator review 2026-06-10): the selected proof's maker/taker
    # execution_mode_intent and its maker limit price are FIRST-CLASS receipt fields, not
    # opportunity-book decoration. They are PROVEN through submit recapture under that
    # mode's economics and are the SOLE mode authority for the final command builder, which
    # must NOT re-decide the mode. None on legacy / non-priced receipts. The final builder
    # fails closed (SUBMIT_ABORTED_MODE_FLIPPED) when this is missing at the final stage —
    # an unproven mode never defaults to a taker submit.
    execution_mode_intent: str | None = None
    maker_limit_price: float | None = None
    # K4.0 REST-THEN-CROSS (consolidated overhaul 2026-06-11): the policy verdict
    # that produced execution_mode_intent (POLICY_* from mode_consistent_ev) and the
    # escalation deadline for REST decisions. The final command builder's fresh-mode
    # witness subordinates its EV-override to this policy; the settlement loop
    # groups fill outcomes by it (hazard-curve + lambda recalibration). None on
    # legacy receipts (omit-when-None keeps existing hashes stable).
    rest_then_cross_policy: str | None = None
    rest_escalation_deadline_minutes: float | None = None
    # DecisionProvenanceEnvelope (operator law 2026-06-11): the complete decision-time provenance
    # blob (canonical JSON) for this no-submit decision. None on legacy receipts (omit-when-None
    # from receipt_json keeps existing receipt_hash byte-stable). Observability only; never gates.
    envelope_json: str | None = None
    # P1 BELIEF CACHE (continuous re-decision resurrection 2026-06-12): the family belief captured
    # during decision (YES q-posterior + condition_id per bin + evidence snapshot identity). The
    # reactor persists this through its OWN world conn inside the open SAVEPOINT (Window B) — the
    # deadlock-free P1 write. compare=False + repr=False so it NEVER affects receipt equality or
    # the receipt hash (it is internal plumbing, never serialized into receipt_json). None on every
    # legacy / gate-reject receipt that never reached candidate-proof generation.
    belief_payload: "dict[str, Any] | None" = field(default=None, repr=False, compare=False)
    # Cross-family auction transport. The adapter prepares the complete family
    # probability/token/book set without reserving or submitting; the reactor compares
    # every prepared family before one winner may reserve. Internal only: never serialized.
    prepared_global_family: "Any | None" = field(default=None, repr=False, compare=False)
    # Exact cross-family winner certificate. Present only on the one event selected
    # from a complete current universe; the live certificate builder consumes its
    # immutable shares/limit/book/wealth identities and may not re-size it locally.
    global_actuation: "Any | None" = field(default=None, repr=False, compare=False)
    # Submit-time curve observed by the side-effect-free winner preflight when it
    # supersedes the selected curve. Internal only: the global runtime overlays it
    # into the frozen complete universe and reruns the same optimizer.
    global_jit_candidate: "Any | None" = field(default=None, repr=False, compare=False)
    # Candidate-local executable probability bound discovered by winner preflight.
    # Internal only: the global runtime feeds it back into the same complete auction
    # and re-sizes/re-ranks before any venue side effect.
    global_jit_payoff_q_lcb: "float | None" = field(
        default=None,
        repr=False,
        compare=False,
    )
    # Direct reduce-only executor boundary facts.  They are internal control
    # evidence: reactor disposition and live counters must never infer venue
    # contact or ACK from an outcome string.
    venue_call_started: bool = field(default=False, repr=False, compare=False)
    venue_ack_received: bool = field(default=False, repr=False, compare=False)
    venue_command_id: str | None = field(default=None, repr=False, compare=False)
    venue_command_state: str | None = field(default=None, repr=False, compare=False)
    venue_order_type: str | None = field(default=None, repr=False, compare=False)
    # D1 FILL-UP LEASE CONTEXT (2026-06-22 lifecycle consult REQ-20260622-060011).
    # When this receipt is an APPROVED same-token fill-up (stake overridden to the
    # residual delta), the family-rebalance lease intent_id + the owned-exposure
    # identity are carried here so the live submit wrapper can run the pre-submit
    # family-exposure reread and advance the lease to a terminal status (COMPLETE on
    # ack / ABORTED on a late abort). compare=False + repr=False so it NEVER affects
    # receipt equality or the receipt hash (internal plumbing, never serialized into
    # receipt_json). None on EVERY non-fill-up receipt — the fresh-entry path never
    # populates it, so the entry path is byte-identical.
    fill_up_lease_payload: "dict[str, Any] | None" = field(default=None, repr=False, compare=False)
    # D2 SHIFT-BIN LEASE CONTEXT (2026-06-22 lifecycle consult REQ-20260622-060011).
    # The close-before-open carrier. Two shapes, both keyed by the SHIFT_BIN lease
    # intent_id + old-leg identity (old_position_id / old_token_id):
    #   - phase="EXIT_OLD_LEG": this receipt is a TYPED NO-SUBMIT for the new bin. The
    #     live submit wrapper submits the reduce-only exit for the OLD token via the
    #     existing exit path (execute_exit_order) and advances the lease EXIT_SUBMITTED
    #     / EXIT_PARTIAL / EXIT_UNKNOWN by the exit OrderResult — NEVER a new-bin entry.
    #   - phase="ENTER_NEW_BIN": the old leg is proven closed; this receipt IS a real
    #     counter-entry submit, and the wrapper advances the lease COMPLETE on ack.
    # compare=False + repr=False so it NEVER affects receipt equality or the receipt
    # hash. None on EVERY fresh-entry / fill-up receipt — the entry + D1 paths never
    # populate it, so both are byte-identical.
    shift_bin_lease_payload: "dict[str, Any] | None" = field(default=None, repr=False, compare=False)
    # SUBMIT-LANE STAMP (silent-trade-kill antibody 2026-06-12; root cause
    # /tmp/allpass_nosubmit_rootcause.md). Records WHICH submit adapter actually
    # ran this decision so a full-pass receipt emitted by the no-submit adapter
    # during a live-arm degrade can never be confused with a genuine
    # decision-declined no-submit. Values:
    #   "LIVE"              — live adapter, real_order_submit_enabled True (the real
    #                          submit lane; either SUBMITTED / SUBMIT_DISABLED build /
    #                          a TYPED NO_SUBMIT abort — never the default reason).
    #   "SUBMIT_DISABLED"   — live adapter, real_order_submit_enabled False (the
    #                          submit-disabled-bridge build lane).
    #   "NO_SUBMIT_ADAPTER" — the no-submit adapter ran (control-blocked live-submit lane). On a
    #                          full-pass its reason names the live block cause that drove
    #                          the selector off the live lane (NO_SUBMIT_ADAPTER_LANE:
    #                          <cause>), NEVER the default literal.
    # This is DECISION provenance (which lane decided), not transport metadata, so it
    # IS serialized into receipt_json. None on legacy / pre-stamp receipts; omit-when-
    # None in receipt_json keeps existing receipt_hash byte-stable, and readers MUST
    # tolerate its absence (only NEW writes carry/enforce it).
    submit_lane: str | None = None
    # C2 SELECTION SHRINKAGE TELEMETRY (task #60, 2026-06-13). The trading-path
    # FDR/BH gate consumes degenerate {0,1} p-values (a no-op multiplicity
    # correction; event_reactor_adapter.py:9854/9876) and, even with continuous
    # p-values, mutually-exclusive bins violate PRDS so BH is invalid and FDR is
    # the wrong objective (not bankroll log growth). The replacement (authority
    # statistical_calibration_addendum_2026-06-13 A2/D3) is the posterior
    # local-false-sign-rate + correlation-aware EB selection shrinkage +
    # expected-log-utility license. These columns carry the NEW quantities on
    # every adapter receipt:
    #   lfsr                     — posterior P(edge <= 0 | D), the p-value
    #                              replacement (small = confident positive edge).
    #   edge_shrunk              — winner's-curse-corrected (EB-shrunk) edge.
    #   edge_shrunk_posterior_sd — posterior SD of the shrunk edge.
    #   selection_authority      — which gate DECIDED: "BH_FDR" (flag OFF, the
    #                              current behavior) | "EB_SHRINKAGE" (flag ON).
    # These are DECISION provenance fields. Current live selection remains unchanged
    # unless selection_authority explicitly records an EB_SHRINKAGE gate result; the
    # fields are serialized into receipt_json so later attribution can audit the
    # selected order without feeding this data back into the same decision.
    # None on legacy / gate-reject receipts; omit-when-None keeps existing
    # receipt_hash byte-stable, mirroring submit_lane / envelope_json travel.
    lfsr: float | None = None
    edge_shrunk: float | None = None
    edge_shrunk_posterior_sd: float | None = None
    selection_authority: str | None = None
    # F1 (2026-07-04): hierarchical settlement-coverage calibrator provenance
    # (src/calibration/settlement_coverage_hierarchy.py). ``q_live``/``q_lcb_5pct``
    # above BECOME the EXECUTABLE pair when the flag is ON (the values Kelly/
    # admission actually consume); ``q_live_raw``/``q_lcb_raw`` carry the FROZEN
    # raw certificate unchanged (audit law — the raw pair is never mutated).
    # ``coverage_hierarchy_level`` names which cohort licensed the verdict
    # (LOCAL_SHIELD/STRATEGY_BUCKET/STRATEGY_SUPERBUCKET/CROSS_STRATEGY/GLOBAL);
    # None when no cohort reached a licensed verdict (flag OFF, or a genuine
    # INSUFFICIENT_DATA no-op — both leave q_live/q_lcb_5pct untouched). All
    # None on legacy / flag-OFF receipts; omit-when-None in receipt_json keeps
    # existing receipt_hash byte-stable (mirrors submit_lane / lfsr above).
    q_live_raw: float | None = None
    q_lcb_raw: float | None = None
    coverage_hierarchy_level: str | None = None
    coverage_hierarchy_cohort_key: str | None = None
    coverage_hierarchy_n: int | None = None
    coverage_hierarchy_wins: int | None = None
    coverage_hierarchy_estimator: str | None = None

    def __post_init__(self) -> None:
        if self.proof_accepted is None:
            object.__setattr__(self, "proof_accepted", bool(self.submitted))


def _is_global_reduce_only_exit_receipt(receipt: EventSubmissionReceipt) -> bool:
    """Recognize only the exact global SELL handoff already owned by exit law."""

    actuation = receipt.global_actuation
    decision = getattr(actuation, "decision", None)
    candidate = getattr(decision, "candidate", None)
    side = str(getattr(candidate, "side", "") or "")
    return bool(
        receipt.submitted
        and receipt.proof_accepted is True
        and receipt.side_effect_status == "EXIT_SUBMITTED"
        and receipt.venue_call_started
        and bool(receipt.venue_command_id)
        and receipt.venue_order_type == "FAK"
        and str(receipt.reason or "").startswith(
            ("GLOBAL_SELL_EXIT:", "GLOBAL_SELL_EXIT_UNKNOWN:")
        )
        and getattr(candidate, "action", "BUY") == "SELL"
        and str(getattr(actuation, "winner_event_id", "") or "")
        == receipt.event_id
        and str(getattr(candidate, "token_id", "") or "") == receipt.token_id
        and receipt.direction == f"sell_{side.lower()}"
        and side in {"YES", "NO"}
    )


Submit = Callable[[OpportunityEvent, datetime], bool | None | EventSubmissionReceipt]


class LiveLaneDarkInvariantError(RuntimeError):
    """Raised at the no-submit persist boundary when a full-pass receipt would be
    booked as accepted while the live lane was nominally armed and stamped LIVE.

    The combination (nominally-armed live daemon + proof_accepted=True +
    side_effect_status=NO_SUBMIT + submit_lane="LIVE") is IMPOSSIBLE for a genuine
    full-pass: the live lane either submits, produces an execution terminal, or
    returns a typed pre-venue abort with proof_accepted=False. If this combination
    reaches persistence the live lane silently ate a tradeable full-pass entry —
    the 2026-06-12 11:51-12:12Z silent-kill incident — so we RAISE instead of
    persisting a kill indistinguishable from normal no-submit accounting.

    A no-submit receipt produced by the legitimate control-blocked lane carries
    submit_lane="NO_SUBMIT_ADAPTER" + a named live block cause and is NOT impacted by
    this invariant (it persists, honestly labelled).
    """


@dataclass
class ReactorConfig:
    reactor_mode: str = "live_no_submit"
    real_order_submit_enabled: bool = False
    # Task #102 (BEST-ORDER SELECTION): book-wide edge-zone admission gate, the
    # LAST step in the money-path. DEFAULT FALSE => byte-identical to today (the
    # gate is computed only when this flag is True). When True, a candidate is
    # admitted ONLY if its honest (q_lcb-based) after-cost EV-per-dollar clears
    # Scope-aware claim tier (2026-06-11 anti-starvation). True (default) =
    # historical behaviour: DAY0_EXTREME_UPDATED ranks at the top claim tier
    # (realized obs = freshest tradeable alpha). False is reserved for tests or
    # historical replay scopes where Day0 is not a live entry lane. Derived from
    # the scope via src.events.event_priority.day0_is_tradeable_for_scope.
    day0_is_tradeable: bool = True
    # SUBMIT-LANE INVARIANT (silent-trade-kill antibody 2026-06-12). The SAME
    # operator-arm authority the main.py submit-adapter selector reads
    # (edli_cfg["edli_live_operator_authorized"] is True, via require_operator_arm).
    # Threaded here so the no-submit persist boundary can recognise a NOMINALLY-ARMED
    # live daemon and refuse to silently book a full-pass receipt stamped LIVE as a
    # NO_SUBMIT accepted terminal. NOT a second authority: it is the same flag value,
    # passed in, read-only. Default False => byte-identical legacy behaviour (the
    # invariant only fires when the operator has actually armed AND reactor_mode=live).
    edli_live_operator_authorized: bool = False


# An executable market snapshot for the family may simply not be captured yet on the cycle
# the reactor reaches the event (the targeted refresh and the reactor share a cycle). That is
# a TRANSIENT condition, not a terminal rejection: the event is requeued and retried on a later
# cycle (after capture) rather than being consumed.
#
# EVENT-HORIZON TERMINALIZATION (operator law 2026-06-12, "no caps of any kind";
# "重试次数不是市场事实" — a retry count is not a market fact). The retry loop is
# NOT bounded by an attempt cap. An attempt cap burns a live-positive-EV event
# because the SUBSTRATE was unlucky N times, while the EVENT itself is still
# timely — a cap disguised as a safety check (external consult BLOCKER verdict).
# A transient event requeues INDEFINITELY until an explicit SEMANTIC terminal
# (a horizon) fires:
#   (a) TIMELINESS_FLOOR_PAST — the event is no longer timely. Reuses the SINGLE
#       existing timeliness authority (EventStore._is_timely / _strictly_past_in_tz):
#       a forecast-decision event whose target LOCAL day has strictly ended can
#       neither produce a receipt nor needs the reactor; it has crossed its
#       market horizon. NO second clock is invented — the reactor asks the store
#       the same question fetch_pending asks on the read floor. This same floor
#       is ALSO why infinite requeue is safe and cannot leak: once the event is
#       strictly-past, fetch_pending stops returning it (read-floor drop) and
#       archive_expired_candidates sweeps it terminal; the explicit dead-letter
#       here just labels WHY at the moment the reactor still holds the claim.
#       (a) also subsumes the "market/family delisted/closed" horizon (b): the
#       settlement-day-end floor IS the authoritative market-closed signal the
#       reactor can read cheaply. We deliberately do NOT build a new venue/delist
#       probe (operator no-new-probe guard) — if a cheaper authoritative delist
#       signal is later surfaced, it joins this same horizon predicate.
#   (c) OPERATOR_DISARM — the operator turned the lane off. The reactor_mode flip
#       (REACTOR_NOT_LIVE) already consumes events in _process_one_pre_submit
#       BEFORE retry; the explicit env disarm (ZEUS_REACTOR_TRANSIENT_DISARM)
#       gives operations a kill-switch that terminalizes in-flight transients
#       with an honest cause instead of letting them spin.
#
# When a horizon fires the dead-letter label says WHY
# (MONEY_PATH_HORIZON_EXPIRED:<horizon>:<last_reason>), NEVER an attempt count.
_EXECUTABLE_SNAPSHOT_RETRY = "RETRY_EXECUTABLE_SNAPSHOT_PENDING"
_PRE_SUBMIT_WORLD_WRITE_LOCK_RETRY = "WORLD_WRITE_LOCK_BUSY_PRE_SUBMIT"
_POST_SUBMIT_WORLD_WRITE_LOCK_RETRY = "WORLD_WRITE_LOCK_BUSY_POST_SUBMIT"

# K2.1: once-per-process-per-base warning dedup for unregistered rejection-reason
# bases (see _write_regret). Module-level so every reactor instance shares it.
_UNREGISTERED_REJECTION_BASES_WARNED: set[str] = set()

# Log hygiene only (NOT behavior): a perpetually-requeued event would log every
# cycle. We log at attempt 1, then every Nth attempt — the requeue count is kept
# solely to dedupe logs, never to terminalize.
_TRANSIENT_REQUEUE_LOG_EVERY = 50

# Operator-disarm env kill-switch (horizon (c)). When set truthy, every in-flight
# money-path transient terminalizes with MONEY_PATH_HORIZON_EXPIRED:OPERATOR_DISARM
# on its next cycle instead of requeueing. Unset/empty/"0"/"false" => armed
# (normal indefinite requeue until a timeliness/operator horizon).
_TRANSIENT_DISARM_ENV = "ZEUS_REACTOR_TRANSIENT_DISARM"
# Sentinel returned by _process_one when a FORECAST_SNAPSHOT_READY event has been dead-lettered
# due to non-live-eligible window authority. The dead-letter + reject writes are done inside
# _process_one; process_pending must NOT double-count or attempt mark_processed on this path.
_FSR_PARTIAL_DEAD_LETTER = "FSR_PARTIAL_DEAD_LETTER"

# ALWAYS-DECIDABLE invariant (operator law 2026-06-12 "RULE 1 在任何情况下都生效…
# 从来都不应该有一个找不到机会的时间点出现"): a TRANSIENT SUBSTRATE block must trigger that
# substrate's refresh as part of the SAME handling, so requeue-WITHOUT-refresh-attempt is
# structurally impossible for refreshable substrate classes. The reactor used to classify an
# executable-snapshot block as a transient and requeue it forever (until horizon) without ever
# making the substrate fresh — the decision-time family_snapshot_refresher lived PAST the
# reactor's gate and was never reached for an event blocked AT the gate. Build 1 threads the
# SAME refresher into the reactor and invokes it after the blocked event's unit-of-work closes
# (no network inside the world SAVEPOINT / trade read txn — three-phase law).
#
# Debounce window: DERIVED from the snapshot freshness window itself, never a bare magic number.
# The freshness window is FRESHNESS_WINDOW_DEFAULT (30s); we debounce per-family at HALF that
# window. Rationale: a refresh writes rows fresh for the full window, so a second refresh of the
# same family is worthless until the captured rows are at least half-aged — refreshing more often
# only burns /book fetches while the prior capture is still fresh. Half-window (not full) keeps a
# safety margin so the next decision cycle still finds genuinely-fresh rows even after capture +
# election latency.
from src.contracts.executable_market_snapshot import (  # noqa: E402
    FRESHNESS_WINDOW_DEFAULT as _SNAPSHOT_FRESHNESS_WINDOW,
)

_FAMILY_REFRESH_DEBOUNCE_SECONDS = max(1.0, _SNAPSHOT_FRESHNESS_WINDOW.total_seconds() / 2.0)


@dataclass
class ReactorResult:
    processed: int = 0
    rejected: int = 0
    proof_accepted: int = 0
    dead_lettered: int = 0
    retried: int = 0
    # VISIBILITY (2026-06-11 claim-storm incident): claim() lock bounces were
    # silently folded into ``retried`` — a 0/250 storm cycle was indistinguishable
    # from 250 honest snapshot-pending retries (reasons=[]). Counted separately so
    # the status pulse / logs expose lock contention as lock contention.
    claim_lock_bounces: int = 0
    rejection_reasons: list[str] = field(default_factory=list)
    # ALWAYS-DECIDABLE invariant (2026-06-12): how many family-substrate refreshes the reactor
    # invoked this cycle in response to transient substrate blocks, and how many single-family
    # cycle-advance reseeds it enqueued for stale/absent posterior blocks. Visibility only — the
    # invariant is enforced structurally, these counters just make it observable in the status
    # pulse (a transient-requeue cycle with snapshot_refreshes==0 would be the regression).
    snapshot_refreshes: int = 0
    cycle_advance_enqueues: int = 0
    day0_hourly_refreshes: int = 0
    # DRAIN BUDGET (#83, 2026-06-16): how many families the end-of-cycle substrate-refresh
    # drain did NOT reach this cycle because the per-cycle drain wall-clock budget
    # (ZEUS_REACTOR_DRAIN_BUDGET_SECONDS) was spent. Visibility only — those families stay in
    # _pending_* and are refreshed on a later cycle via the fair-cursor rotation (no starvation).
    # A persistently-nonzero value means the drain budget is too small for the blocked-family
    # count; it is NOT a money-path cap (decisions and submits are untouched by the drain).
    drained_truncated: int = 0

    @property
    def submitted(self) -> int:
        return self.proof_accepted


@dataclass(frozen=True)
class GlobalBatchSubmitResult:
    """Opaque batch-actuation result: all events finalized, at most one venue call."""

    receipts: Mapping[str, EventSubmissionReceipt]
    winner_event_id: str | None
    venue_submit_count: int
    next_claim_event: OpportunityEvent | None = None

    def __post_init__(self) -> None:
        if self.venue_submit_count not in {0, 1}:
            raise ValueError("global batch may start at most one venue submit")
        if self.winner_event_id is None and self.venue_submit_count != 0:
            raise ValueError("venue submit requires one selected winner")
        if self.next_claim_event is not None and (
            self.winner_event_id is not None
            or self.venue_submit_count != 0
            or self.next_claim_event.event_id in self.receipts
        ):
            raise ValueError("next global claim must be unclaimed and side-effect free")
        if self.winner_event_id is not None and self.winner_event_id not in self.receipts:
            raise ValueError("global batch winner must have one event-bound receipt")
        if any(key != receipt.event_id for key, receipt in self.receipts.items()):
            raise ValueError("global batch receipt keys must match receipt event identities")
        submitted_ids = {
            receipt.event_id for receipt in self.receipts.values() if receipt.submitted
        }
        if len(submitted_ids) > 1:
            raise ValueError("global batch may contain at most one submitted receipt")
        if submitted_ids and (
            self.venue_submit_count != 1 or submitted_ids != {self.winner_event_id}
        ):
            raise ValueError("submitted receipt must be the one global winner")


class OpportunityEventReactor:
    def __init__(
        self,
        store: EventStore,
        *,
        source_truth_gate: Gate,
        executable_snapshot_gate: ExecutableSnapshotGate,
        riskguard_gate: Gate,
        final_intent_submit: Submit,
        reject: Reject,
        config: ReactorConfig | None = None,
        regret_ledger: Any | None = None,
        decision_provenance_hook: Any | None = None,
        family_snapshot_refresher: "Callable[..., bool] | None" = None,
        cycle_advance_enqueuer: "Callable[..., bool] | None" = None,
        day0_hourly_refresher: "Callable[..., bool] | None" = None,
        held_family_provider: "Callable[[], frozenset[tuple[str, str, str]]] | None" = None,
        family_market_absence_provider: "Callable[..., bool] | None" = None,
    ) -> None:
        self._store = store
        self._source_truth_gate = source_truth_gate
        self._executable_snapshot_gate = executable_snapshot_gate
        self._riskguard_gate = riskguard_gate
        self._submit = final_intent_submit
        self._reject = reject
        self._config = config or ReactorConfig()
        self._regret_ledger = regret_ledger
        # ALWAYS-DECIDABLE invariant (operator law 2026-06-12). The SAME decision-time targeted
        # family snapshot refresher the adapter uses (main._edli_decision_family_snapshot_refresher,
        # injected so the reactor never imports venue code — architecture ban, no_bypass test). When
        # an event is classified EXECUTABLE_SNAPSHOT-transient AT the reactor gate, the reactor
        # invokes this to capture FRESH books for THAT family — AFTER the event's unit-of-work
        # closes and BEFORE the next claim (no network inside the world SAVEPOINT or the trade read
        # txn). Absent (legacy callers / most tests) => no refresh, byte-identical pre-invariant
        # behavior (the event still requeues, the horizon still bounds it).
        self._family_snapshot_refresher = family_snapshot_refresher
        # Build 2: single-family cycle-advance reseed enqueuer (reuses the cycle-advance
        # re-materialization lane scoped to ONE family) for a posterior-staleness block. Same
        # discipline: no network/heavy work in txn, debounced, fail-soft. Absent => no-op.
        self._cycle_advance_enqueuer = cycle_advance_enqueuer
        # Day0 remaining-day q depends on persisted high-resolution hourly vectors,
        # not executable CLOB snapshots. Missing vectors use this separate
        # substrate refresher so weather-carrier faults do not refresh the wrong
        # upstream surface.
        self._day0_hourly_refresher = day0_hourly_refresher
        # ORDERING (operator correction 2026-06-12): refresh fan-out is NOT liquidity-ordered —
        # opportunity is uncorrelated with liquidity (small markets can carry denser sophisticated
        # competition; liquidity's only role stays in sizing/fill). The ONLY ordering bias is
        # HELD-POSITION-FIRST: a family with money at risk RIGHT NOW (the exit monitor reads its
        # belief) is refreshed before new-money families. ``held_family_provider`` returns the
        # current held (city, target_date, metric) set, read-only and fail-soft (absent / raising =>
        # no held bias, pure fair rotation). The reactor owns zeus-world only; this provider is
        # injected (it reads zeus_trades.position_current) so the reactor never opens a trades conn.
        self._held_family_provider = held_family_provider
        # Live venue-listing absence proof, injected from the daemon warm lane. This is deliberately
        # narrower than "no cached topology": it may return True only after the Gamma/topology
        # refresher has current evidence that the family has no listed Polymarket market. The reactor
        # uses it to stop infinite EXECUTABLE_SNAPSHOT_BLOCKED retries for untradeable families while
        # preserving normal retry behavior for locks, stale books, or not-yet-harvested probes.
        self._family_market_absence_provider = family_market_absence_provider
        # Per-family debounce: family-key -> last successful refresh-attempt monotonic time. The
        # window is DERIVED from the snapshot freshness window (half of it), never a magic number.
        self._family_refresh_last_at: dict[str, float] = {}
        self._family_cycle_advance_last_at: dict[str, float] = {}
        self._family_day0_hourly_last_at: dict[str, float] = {}
        # FAIR-CURSOR fan-out (Wave1B precedent): blocked families discovered this cycle queue here
        # in encounter order; the cursor rotates which family is refreshed FIRST across cycles so no
        # single family monopolizes the per-cycle refresh fan-out and ALL blocked families are
        # covered across a bounded number of cycles (no numeric drop-cap on the candidate set).
        self._family_refresh_cursor: int = 0
        # DecisionProvenanceEnvelope (operator law 2026-06-11): an OPTIONAL fail-soft accessor
        # returning (bundle, forecast_conn) for the event being rejected, so the regret envelope
        # can carry the full forecast data-combination + per-input ages. None => the envelope is
        # still built from the world-DB snapshot + receipt economics + the FULL rejection reason
        # (never less than that). Observability only; never gates. See
        # docs/evidence/settlement_guard/2026-06-11_decision_provenance_plan.md.
        self._decision_provenance_hook = decision_provenance_hook
        self._family_logged: set[str] = set()
        # Last transient money-path reason per event_id (external review finding
        # 2026-06-11): price-race requeues (SUBMIT_ABORTED_PRICE_MOVED /
        # SUBMIT_ABORTED_MODE_FLIPPED / would_cross_book / source-recapture)
        # share the executable-snapshot retry disposition, so exhaustion used to
        # dead-letter as EXECUTABLE_SNAPSHOT_BLOCKED — masking the actual
        # category and hiding submit-race churn from the ledgers. The dict keeps
        # the honest cause for the terminal label. In-memory only: lost on
        # restart the label degrades to the generic snapshot reason, never lies
        # about a cause it does not know.
        self._transient_requeue_reasons: dict[str, str] = {}
        self._pending_snapshot_refreshes: list[tuple[str, str, str]] = []
        self._pending_cycle_advances: list[tuple[str, str, str]] = []
        self._pending_day0_hourly_refreshes: list[tuple[str, str, str]] = []
        self._claim_contention_seen = False
        # Per-event requeue counter — LOG HYGIENE ONLY (operator law 2026-06-12:
        # a retry count is not a market fact and MUST NOT terminalize). Used
        # solely to dedupe the requeue log line (log at attempt 1, then every
        # _TRANSIENT_REQUEUE_LOG_EVERY). In-memory; lost on restart (the count
        # resets, the behavior does not — there is no cap to lose).
        self._transient_requeue_counts: dict[str, int] = {}
        from src.events.no_submit_receipts import EdliNoSubmitReceiptLedger
        from src.decision_kernel.compiler import DecisionCompiler
        from src.decision_kernel.ledger import DecisionCertificateLedger
        from src.events.live_cap import LiveCapLedger

        self._no_submit_receipt_ledger = EdliNoSubmitReceiptLedger(store.conn)
        self._decision_compiler = DecisionCompiler()
        self._decision_certificate_ledger = DecisionCertificateLedger(store.conn)
        self._decision_certificate_ledger.ensure_schema()
        self._live_cap_ledger = LiveCapLedger(store.conn)

    def process_pending(
        self,
        *,
        decision_time: datetime,
        limit: int | None = 100,
        targeted_event_ids: frozenset[str] = frozenset(),
        targeted_only: bool = False,
    ) -> ReactorResult:
        result = ReactorResult()
        # ALWAYS-DECIDABLE invariant (2026-06-12): families blocked on a refreshable substrate
        # THIS cycle, accumulated during processing and drained AFTER all per-event units of work
        # close (no network inside any open world/trade txn). Per-cycle scope so a family that
        # un-blocks stops being refreshed.
        self._pending_snapshot_refreshes: list[tuple[str, str, str]] = []
        self._pending_cycle_advances: list[tuple[str, str, str]] = []
        self._pending_day0_hourly_refreshes: list[tuple[str, str, str]] = []
        self._claim_contention_seen = False
        # E1 (STEP 8): per-cycle wall-clock budget. A cycle must not run unbounded;
        # once the budget is exceeded, stop after the current event and leave the
        # rest PENDING (not consumed, not dropped) for the next cycle. This caps a
        # cycle so the scheduler never hits "max running instances reached" and
        # fresh candidates (freshest-target-first, STEP 3) are reached promptly.
        # Default 22s; override via ZEUS_REACTOR_CYCLE_BUDGET_SECONDS.
        budget = _cycle_budget_seconds()
        cycle_start = time.monotonic()
        batch_limit = _fetch_batch_limit() if limit is None else max(1, int(limit))
        remaining = None if limit is None else batch_limit
        try:
          while remaining is None or remaining > 0:
            # fetch_pending is a READ — WAL permits concurrent readers, so it is NOT
            # taken under the world-DB write mutex.  limit=None means drain the current
            # admissible queue in batches; the batch size is pagination, not a total cap.
            #
            # STALE-SNAPSHOT GUARD (2026-06-11 claim-storm): the read must NEVER run
            # inside a dangling write txn on this conn — that pins a read snapshot
            # that any concurrent writer's commit turns stale, after which every
            # claim() UPDATE fails SQLITE_BUSY_SNAPSHOT instantly (busy handler
            # bypassed). The claim lock-bounce path now rolls back, but this guard
            # makes the CATEGORY impossible for any future path that leaks a txn.
            if getattr(self._store.conn, "in_transaction", False):
                with contextlib.suppress(Exception):
                    self._store.conn.rollback()
                import logging as _logging

                _logging.getLogger("zeus.events.reactor").warning(
                    "reactor: rolled back dangling world txn before fetch_pending "
                    "(stale-snapshot guard)"
                )
            request_limit = batch_limit if remaining is None else min(batch_limit, remaining)
            fetch_limit = (
                request_limit
                if remaining is None
                else _lane_fairness_fetch_limit(request_limit)
            )
            fetch_kwargs = {
                "decision_time": decision_time.astimezone(UTC).isoformat(),
                "limit": fetch_limit,
                "day0_is_tradeable": self._config.day0_is_tradeable,
            }
            if targeted_event_ids:
                fetch_kwargs["targeted_event_ids"] = targeted_event_ids
            if targeted_only:
                fetch_kwargs["targeted_only"] = True
            events = self._store.fetch_pending(**fetch_kwargs)
            if not events:
                break
            # FAIR LANE INTERLEAVE (2026-06-15). The per-cycle wall-clock budget completes
            # only ~3-4 family decisions (p99=59s each), and fetch_pending returns ALL
            # Tier-0 DAY0_EXTREME_UPDATED before ANY Tier-1 FORECAST_SNAPSHOT_READY — so the
            # whole budget is consumed by the day0 lane and the forecast/spine lane is
            # STARVED of processing budget every cycle (measured 2026-06-15: 0 FSR ever
            # claimed while day0 monopolized the budget). This is the cross-lane gap in the
            # event_priority anti-starvation design (which fairly round-robins WITHIN a tier
            # but lets Tier-0 fully precede Tier-1). Interleave the two DECISION lanes 1:1 so
            # each gets a fair half of the bounded budget — day0 keeps the first slot (its
            # realized-observation freshness bias) but no longer starves the spine. Order-only
            # (each family is decided on its own fresh inputs; processing order is immaterial
            # to correctness); within-lane fetch order — and thus the per-city fairness — is
            # preserved. Channel events are already excluded by fetch_pending.
            if callable(getattr(self._submit, "process_global_batch", None)):
                # fetch_pending already owns the complete stale-recovery,
                # targeted-winner, and cross-lane order. A second forecast-first
                # weave here would move a targeted forecast ahead of stale Day0
                # recovery and permit new exposure before unknown in-flight work.
                attempted = self._process_global_event_batch(
                    events,
                    decision_time=decision_time,
                    result=result,
                    budget=budget,
                    cycle_start=cycle_start,
                    remaining=remaining,
                )
                if remaining is not None:
                    remaining -= attempted
                # One global auction epoch may start at most one venue submit. Do not
                # page into a second auction inside the same reactor cycle.
                return result
            events = _fair_lane_interleave(events)
            for event in events:
                # PRE-EVENT budget check (2026-06-11 cadence guard): if the budget
                # is ALREADY spent, stop BEFORE claiming another event. The
                # post-event check below cannot interrupt a long event mid-flight
                # (live: a 22-candidate family decision ran p99=59s, max=460s vs a
                # 30s budget), and without this pre-check every event in the
                # already-fetched batch could extend the overrun by another full
                # decision. Caps the worst-case overrun to ONE in-flight event;
                # the rest stay PENDING (not consumed, not dropped) for the next
                # cycle.
                if budget is not None and (time.monotonic() - cycle_start) >= budget:
                    return result
                self._process_event_unit(event, decision_time=decision_time, result=result)
                if remaining is not None:
                    remaining -= 1
                if budget is not None and (time.monotonic() - cycle_start) >= budget:
                    return result
                if remaining is not None and remaining <= 0:
                    return result
            if len(events) < request_limit:
                break
        finally:
            # ALWAYS-DECIDABLE drain (2026-06-12): runs on EVERY exit path (normal completion,
            # budget overrun early-return, batch-limit early-return). At this point no per-event
            # world/trade txn is open (each unit-of-work committed + released its mutex before the
            # loop body returned), so the refresher's network I/O is structurally outside any txn.
            with contextlib.suppress(Exception):
                self._drain_substrate_refreshes(result=result)
        return result

    def _process_global_event_batch(
        self,
        events: Sequence[OpportunityEvent],
        *,
        decision_time: datetime,
        result: ReactorResult,
        budget: float | None,
        cycle_start: float,
        remaining: int | None,
    ) -> int:
        """Claim/gate all epoch events, then let one opaque adapter auction act once."""

        claimed: list[OpportunityEvent] = []
        claim_generations: dict[str, str] = {}
        claim_lock_bounced_event_ids: set[str] = set()
        attempted = 0
        for event in events:
            if remaining is not None and attempted >= remaining:
                break
            if budget is not None and (time.monotonic() - cycle_start) >= budget:
                break
            attempted += 1
            claim_generation = self._process_event_unit(
                event,
                decision_time=decision_time,
                result=result,
                defer_submit=True,
            )
            if claim_generation is not None:
                claimed.append(event)
                claim_generations[event.event_id] = claim_generation
        if not claimed:
            return attempted

        process_batch = getattr(self._submit, "process_global_batch")

        def _claim_unpaged_winner(event: OpportunityEvent) -> bool:
            generation, lock_bounced = self._claim_global_winner_for_actuation(
                event,
                current_batch_claim_generations=dict(claim_generations),
                result=result,
            )
            if lock_bounced:
                claim_lock_bounced_event_ids.add(event.event_id)
            if generation is None:
                return False
            claimed.append(event)
            claim_generations[event.event_id] = generation
            return True

        def _finalization_time(event: OpportunityEvent) -> datetime:
            raw = claim_generations.get(event.event_id)
            if not raw:
                return decision_time
            try:
                claimed_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return decision_time
            if claimed_at.tzinfo is None:
                return decision_time
            return max(decision_time, claimed_at.astimezone(UTC))

        try:
            batch_result = process_batch(
                tuple(claimed),
                decision_time.astimezone(UTC),
                claim_unpaged_winner=_claim_unpaged_winner,
            )
            if not isinstance(batch_result, GlobalBatchSubmitResult):
                raise TypeError("global batch adapter returned an invalid result")
            claimed_ids = frozenset(event.event_id for event in claimed)
            if set(batch_result.receipts) != set(claimed_ids):
                raise ValueError("global batch receipts do not cover exactly the claimed epoch")
            if (
                batch_result.winner_event_id is not None
                and batch_result.winner_event_id not in claimed_ids
            ):
                raise ValueError("global batch winner is not a claimed event")
            if batch_result.next_claim_event is not None:
                next_claim_event = batch_result.next_claim_event
                claim_lock_bounced = (
                    next_claim_event.event_id in claim_lock_bounced_event_ids
                )
                if claim_lock_bounced:
                    logging.getLogger("zeus.events.reactor").info(
                        "global winner bounded durable queue retry after same-cycle claim lock-bounce: "
                        "target=%s (selection snapshot released; no venue side effect)",
                        next_claim_event.event_id,
                    )
                queued = self._queue_global_winner_for_claim(
                    next_claim_event,
                    current_batch_claim_generations=claim_generations,
                    result=result,
                )
                if not queued:
                    logging.getLogger("zeus.events.reactor").info(
                        "global winner claim deferred: target=%s; "
                        "a claim lease changed or the target is not currently pending",
                        next_claim_event.event_id,
                    )
        except Exception as exc:  # noqa: BLE001 - every claimed event must close its unit
            for event in claimed:
                self._finalize_deferred_event_unit(
                    event,
                    EventSubmissionReceipt(
                        False,
                        event.event_id,
                        event.causal_snapshot_id,
                        reason=f"GLOBAL_BATCH_FAILED:{type(exc).__name__}:{exc}",
                        proof_accepted=False,
                    ),
                    decision_time=_finalization_time(event),
                    result=result,
                )
            return attempted

        # A real venue call creates a must-settle result. Persist that winner
        # before side-effect-free losers, regardless of the claim/page order.
        # The losers are retryable projections; they must not delay durable
        # ownership of an external side effect.
        finalization_events = list(claimed)
        if batch_result.venue_submit_count == 1:
            finalization_events.sort(
                key=lambda event: event.event_id != batch_result.winner_event_id
            )

        finalization_lock_busy = False
        for event in finalization_events:
            receipt = batch_result.receipts[event.event_id]
            side_effect_possible = bool(
                receipt.submitted
                or receipt.venue_call_started
                or receipt.side_effect_status != "NO_SUBMIT"
                or (
                    batch_result.venue_submit_count == 1
                    and event.event_id == batch_result.winner_event_id
                )
            )
            if finalization_lock_busy and not side_effect_possible:
                # The same external writer is still expected to own SQLite's
                # single WAL writer slot. Do not make every NO_SUBMIT loser pay
                # the same busy timeout; its processing lease is the durable
                # retry path once the writer clears.
                with contextlib.suppress(Exception):
                    self._finalize_reservation(event, emitted=False)
                result.rejection_reasons.append(_POST_SUBMIT_WORLD_WRITE_LOCK_RETRY)
                result.retried += 1
                continue
            finalized = self._finalize_deferred_event_unit(
                event,
                receipt,
                decision_time=_finalization_time(event),
                result=result,
                wait_ms=None if side_effect_possible else _reactor_claim_busy_timeout_ms(),
            )
            if not finalized:
                finalization_lock_busy = True
        return attempted

    def _queue_global_winner_for_claim(
        self,
        event: OpportunityEvent,
        *,
        current_batch_claim_generations: dict[str, str],
        result: ReactorResult,
        wait_ms: int | None = None,
    ) -> bool:
        """Persist the auction winner's next legal claim outside submit I/O."""

        wait_ms = (
            _reactor_claim_busy_timeout_ms()
            if wait_ms is None
            else max(0, int(wait_ms))
        )
        mutex = world_write_mutex()
        if not mutex.acquire(timeout=wait_ms / 1000.0):
            result.claim_lock_bounces += 1
            logging.getLogger("zeus.events.reactor").warning(
                "global winner queue mutex-bounce event_id=%s "
                "claim_mutex_timeout_ms=%s (target stays unqueued; no venue side effect)",
                event.event_id,
                wait_ms,
            )
            return False
        try:
            if self._store.conn.in_transaction:
                raise RuntimeError("GLOBAL_WINNER_QUEUE_WORLD_TXN_OPEN")
            try:
                with _scoped_sqlite_busy_timeout(self._store.conn, wait_ms):
                    self._store.conn.execute("BEGIN IMMEDIATE")
                    queued = self._store.prioritize_global_winner(
                        event,
                        current_batch_claim_generations=current_batch_claim_generations,
                    )
                    self._store.conn.commit()
                    return queued
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self._store.conn.rollback()
                if _is_sqlite_lock_error(exc):
                    result.claim_lock_bounces += 1
                    logging.getLogger("zeus.events.reactor").warning(
                        "global winner queue lock-bounce event_id=%s "
                        "claim_busy_timeout_ms=%s exc=%s "
                        "(rolled back; target stays unqueued; no venue side effect)",
                        event.event_id,
                        wait_ms,
                        exc,
                    )
                    return False
                raise
        finally:
            mutex.release()

    def _claim_global_winner_for_actuation(
        self,
        event: OpportunityEvent,
        *,
        current_batch_claim_generations: dict[str, str],
        result: ReactorResult,
    ) -> tuple[str | None, bool]:
        """Atomically materialize and claim one unpaged cut-time winner."""

        wait_ms = _reactor_claim_busy_timeout_ms()
        mutex = world_write_mutex()
        if not mutex.acquire(timeout=wait_ms / 1000.0):
            result.claim_lock_bounces += 1
            logging.getLogger("zeus.events.reactor").warning(
                "global winner claim mutex-bounce event_id=%s "
                "claim_mutex_timeout_ms=%s (target stays unclaimed; no venue side effect)",
                event.event_id,
                wait_ms,
            )
            return None, True
        try:
            if self._store.conn.in_transaction:
                raise RuntimeError("GLOBAL_WINNER_CLAIM_WORLD_TXN_OPEN")
            try:
                with _scoped_sqlite_busy_timeout(self._store.conn, wait_ms):
                    self._store.conn.execute("BEGIN IMMEDIATE")
                    if not self._store.prioritize_global_winner(
                        event,
                        current_batch_claim_generations=current_batch_claim_generations,
                    ):
                        self._store.conn.rollback()
                        return None, False
                    claimed_at = datetime.now(UTC).isoformat()
                    if not self._store.claim(event.event_id, claimed_at=claimed_at):
                        self._store.conn.rollback()
                        return None, False
                    self._store.conn.commit()
                    return claimed_at, False
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self._store.conn.rollback()
                if _is_sqlite_lock_error(exc):
                    result.claim_lock_bounces += 1
                    logging.getLogger("zeus.events.reactor").warning(
                        "global winner claim lock-bounce event_id=%s "
                        "claim_busy_timeout_ms=%s exc=%s "
                        "(rolled back; target stays unclaimed; no venue side effect)",
                        event.event_id,
                        wait_ms,
                        exc,
                    )
                    return None, True
                raise
        finally:
            mutex.release()

    def _process_event_unit(
        self,
        event: OpportunityEvent,
        *,
        decision_time: datetime,
        result: ReactorResult,
        defer_submit: bool = False,
    ) -> str | None:
        """Process ONE event as TWO serialized world-DB write units around the
        network submit boundary (#95 SEV-2.1).

        EDLI live-canary contention fix (2026-05-31): the EDLI reactor and the
        market-channel ingestor are two in-process WAL writers on zeus-world.db.
        Each event's world WRITE UNIT (claim → ledger writes → mark → commit) is
        serialized against the ingestor via the process-global world-DB write
        mutex (``world_write_mutex`` in db.py). Without it a contended write waited
        out the 30 s busy_timeout → "database is locked" → the reactor cycle
        hung/skipped (status=FAILED).

        SEV-2.1 split (2026-06-01): the injected ``self._submit`` callable performs
        NETWORK I/O — the JIT ``/book`` HTTP fetch (main._edli_pre_submit_jit_book_
        quote_provider) and the venue order POST (executor). Holding the world
        mutex AND an open world-DB transaction (the WAL write lock, opened by
        ``claim()``) across that I/O serialized every world write behind slow
        network calls → WAL lock starvation. The contract on ``world_write_lock`` /
        ``world_write_mutex`` (db.py) is explicit: NEVER hold across HTTP. We honour
        it by committing the pre-submit world write unit (claim + gate/reject
        writes) and releasing the mutex BEFORE the network submit, running
        ``self._submit`` with NO mutex and NO open world txn, then re-acquiring the
        mutex for a SECOND world write unit (post-submit ledger writes + mark).

        Per-window ``_commit_event_unit`` commits so the WAL write lock is released
        between windows (and between events) and the ingestor gets frequent write
        windows. ``fetch_pending`` (a read) stays OUTSIDE the lock.
        """
        # ---- Window A: pre-submit world write unit (claim + gates) under mutex ----
        # Every event contends on the same writer. After one full wait expires,
        # later events probe without waiting until one succeeds; that success ends
        # the contention episode and restores the normal wait for the next event.
        contention_seen = bool(getattr(self, "_claim_contention_seen", False))
        claim_wait_ms = 0 if contention_seen else _reactor_claim_busy_timeout_ms()
        mutex = world_write_mutex()
        if not mutex.acquire(timeout=claim_wait_ms / 1000.0):
            self._claim_contention_seen = True
            result.claim_lock_bounces += 1
            result.retried += 1
            if not contention_seen:
                import logging as _logging

                _logging.getLogger("zeus.events.reactor").warning(
                    "reactor claim mutex-bounce event_id=%s claim_mutex_timeout_ms=%s "
                    "(event stays pending; no venue side effect attempted)",
                    event.event_id,
                    claim_wait_ms,
                )
            return
        pre_disposition: str | None
        should_submit = False
        try:
            with _scoped_sqlite_busy_timeout(
                self._store.conn, claim_wait_ms
            ):
                try:
                    # CLAIM-STORM FIX (2026-06-11 17:51Z): acquire the WAL write lock
                    # DETERMINISTICALLY with BEGIN IMMEDIATE instead of letting
                    # claim()'s UPDATE upgrade lazily. Live cadence fix
                    # (2026-06-26): this pre-submit claim uses a scoped short busy
                    # timeout, not the connection's default 30 s timeout. A claim
                    # miss has emitted no order and leaves the event pending, so it
                    # must be a fast retryable bounce rather than spending a whole
                    # redecision/day0 scheduler interval behind another writer.
                    if not self._store.conn.in_transaction:
                        self._store.conn.execute("BEGIN IMMEDIATE")
                    claim_generation = decision_time.astimezone(UTC).isoformat()
                    claimed = self._store.claim(
                        event.event_id,
                        claimed_at=claim_generation,
                    )
                except Exception as exc:
                    if _is_sqlite_lock_error(exc):
                        # CLAIM-STORM FIX (storm amplifier): ALWAYS roll back. The old
                        # path returned with the implicit txn left OPEN on the store
                        # conn; the next fetch_pending then read INSIDE that dangling
                        # txn, pinning a stale snapshot, and every subsequent claim
                        # failed BUSY_SNAPSHOT instantly => the whole-cycle 0/250
                        # bounce storm. Rollback resets the conn so the next event
                        # starts a fresh txn under the scoped busy handler.
                        _was_in_txn = bool(getattr(self._store.conn, "in_transaction", False))
                        with contextlib.suppress(Exception):
                            self._store.conn.rollback()
                        self._claim_contention_seen = True
                        result.claim_lock_bounces += 1
                        result.retried += 1
                        if not contention_seen:
                            import logging as _logging

                            _logging.getLogger("zeus.events.reactor").warning(
                                "reactor claim lock-bounce event_id=%s txn_open_at_bounce=%s "
                                "claim_busy_timeout_ms=%s exc=%s "
                                "(rolled back; event stays pending; counted in claim_lock_bounces)",
                                event.event_id,
                                _was_in_txn,
                                claim_wait_ms,
                                exc,
                            )
                        return
                    raise
            self._claim_contention_seen = False
            if not claimed:
                # Claim lost (another worker / lease not yet stale): release any
                # open txn and the mutex; nothing to process this cycle.
                self._commit_event_unit()
                return
            try:
                self._store.conn.execute("SAVEPOINT edli_reactor_event")
                pre_disposition, should_submit = self._process_one_pre_submit(
                    event,
                    decision_time=decision_time,
                    result=result,
                    global_batch=defer_submit,
                )
                if not should_submit:
                    self._finalize_disposition(
                        event, pre_disposition, decision_time=decision_time, result=result
                    )
                    self._store.conn.execute("RELEASE SAVEPOINT edli_reactor_event")
                    self._commit_event_unit()
                    return
                # All gates passed: commit the claim/pre-submit write unit so the
                # WAL write lock is released BEFORE we touch the network. No world
                # writes happened in the pre-submit gate-pass path beyond claim.
                self._store.conn.execute("RELEASE SAVEPOINT edli_reactor_event")
                self._commit_event_unit()
            except Exception as exc:
                if _is_sqlite_lock_error(exc):
                    with contextlib.suppress(Exception):
                        self._store.conn.execute("ROLLBACK TO SAVEPOINT edli_reactor_event")
                        self._store.conn.execute("RELEASE SAVEPOINT edli_reactor_event")
                    with contextlib.suppress(Exception):
                        if getattr(self._store.conn, "in_transaction", False):
                            self._store.conn.rollback()
                    result.rejection_reasons.append(_PRE_SUBMIT_WORLD_WRITE_LOCK_RETRY)
                    result.retried += 1
                    return
                self._dead_letter_unknown(event, exc, decision_time=decision_time, result=result)
                return
        finally:
            mutex.release()

        if defer_submit:
            return claim_generation

        # ---- Network submit: NO mutex held, NO open world txn (WAL lock free) ----
        # In production self._submit performs the JIT /book HTTP fetch and the
        # venue order POST. This MUST run outside the world write lock (#95).
        try:
            submit_result = self._submit(event, decision_time.astimezone(UTC))
        except Exception as exc:
            mutex.acquire()
            try:
                # MAJOR #5 (P1 zero-submit, network-submit window, 2026-06-05): the
                # adapter reserves this event's stake PROVISIONALLY *inside*
                # _submit (event_reactor_adapter.py ~1097), before the unguarded
                # receipt-build / serialize / proof-bundle steps. If any of those
                # raises (sqlite3.Error / KeyError / AttributeError on a live
                # HTTP/DB fault), control lands HERE — and without this rollback the
                # reservation is orphaned-but-LIVE in the ledger, over-counting
                # committed for the NEXT same-cycle event → under-sizes / re-zeros
                # later candidates = the exact zero-submit symptom this fix kills.
                # Symmetric to the post-submit window (~383). Idempotent: a _submit
                # that raised BEFORE reserve leaves nothing to roll back —
                # PortfolioReservationLedger.rollback is a no-op for an unknown
                # event_id — and the whole call is suppressed so a rollback failure
                # never masks the original submit exception.
                with contextlib.suppress(Exception):
                    self._finalize_reservation(event, emitted=False)
                self._dead_letter_unknown(event, exc, decision_time=decision_time, result=result)
            finally:
                mutex.release()
            return

        # ---- Window B: post-submit world write unit (ledgers + mark) under mutex ----
        mutex.acquire()
        try:
            try:
                with _scoped_sqlite_busy_timeout(
                    self._store.conn, _reactor_claim_busy_timeout_ms()
                ):
                    # Window A committed and released the WAL write lock; this conn has
                    # no open txn. Open one with BEGIN IMMEDIATE so the WAL write lock is
                    # acquired DETERMINISTICALLY up front (under busy_timeout) rather
                    # than lazily on the first DML — mirrors the claim()-first discipline
                    # of Window A and avoids an immediate "database is locked" when a
                    # concurrent writer holds the WAL write lock at first-DML time.
                    if not self._store.conn.in_transaction:
                        self._store.conn.execute("BEGIN IMMEDIATE")
                    self._store.conn.execute("SAVEPOINT edli_reactor_event")
                    # FIX B (P1 zero-submit co-cause): capture the accept counter
                    # BEFORE post-submit so we can tell whether THIS event was
                    # actually EMITTED (committed) vs rejected downstream of Kelly.
                    _accepted_before = result.proof_accepted
                    post_disposition = self._process_one_post_submit(
                        event, submit_result, decision_time=decision_time, result=result
                    )
                    # FIX B: finalize the per-cycle in-flight reservation. The adapter
                    # PROVISIONALLY reserved this event's stake when it passed
                    # Kelly+RiskGuard; commit it ONLY if the reactor emitted it
                    # (proof_accepted advanced), else roll it back so a candidate
                    # rejected at DECISION_CERTIFICATE / EXECUTOR_EXPRESSIBILITY (or a
                    # transient retry) never inflates corr/raw committed for the next
                    # sequential event. Runs before RELEASE so it shares this unit.
                    self._finalize_reservation(
                        event, emitted=result.proof_accepted > _accepted_before
                    )
                    # Honour the post-submit disposition exactly as the legacy
                    # single-pass flow did: a transient (_EXECUTABLE_SNAPSHOT_RETRY)
                    # requeues without consuming; a terminal accept/reject (None) marks
                    # the event processed and counts it. ``_finalize_disposition`` runs
                    # inside this open savepoint.
                    self._finalize_disposition(
                        event,
                        post_disposition,
                        decision_time=decision_time,
                        result=result,
                        proof_emitted=result.proof_accepted > _accepted_before,
                    )
                    self._store.conn.execute("RELEASE SAVEPOINT edli_reactor_event")
                    self._commit_event_unit()
            except Exception as exc:
                if _is_sqlite_lock_error(exc):
                    with contextlib.suppress(Exception):
                        self._store.conn.execute("ROLLBACK TO SAVEPOINT edli_reactor_event")
                        self._store.conn.execute("RELEASE SAVEPOINT edli_reactor_event")
                    with contextlib.suppress(Exception):
                        self._finalize_reservation(event, emitted=False)
                    result.rejection_reasons.append(_POST_SUBMIT_WORLD_WRITE_LOCK_RETRY)
                    # If the lock failure happened before the savepoint opened
                    # (for example BEGIN IMMEDIATE in Window B), try to return the
                    # event to pending with a visible reason. If the same external
                    # writer still holds the WAL write lock, fall back to the
                    # existing stale-lease path rather than dead-lettering a live
                    # money event for infrastructure contention.
                    with contextlib.suppress(Exception):
                        if getattr(self._store.conn, "in_transaction", False):
                            self._store.conn.rollback()
                    try:
                        # The writer just remained busy for the full Window-B
                        # wait. Requeue is an optional fast path; retrying the
                        # same wait immediately only doubles tail latency. A
                        # zero-wait probe preserves immediate recovery when the
                        # lock cleared at the boundary, otherwise the existing
                        # stale processing lease remains the durable fallback.
                        with _scoped_sqlite_busy_timeout(self._store.conn, 0):
                            self._store.requeue_pending(
                                event.event_id,
                                last_error=_POST_SUBMIT_WORLD_WRITE_LOCK_RETRY,
                            )
                            self._commit_event_unit()
                    except Exception as requeue_exc:
                        if not _is_sqlite_lock_error(requeue_exc):
                            raise
                        with contextlib.suppress(Exception):
                            self._store.conn.rollback()
                    result.retried += 1
                    return
                with contextlib.suppress(Exception):
                    self._store.conn.execute("ROLLBACK TO SAVEPOINT edli_reactor_event")
                    self._store.conn.execute("RELEASE SAVEPOINT edli_reactor_event")
                # FIX B: an exception means this event was NOT emitted — roll back
                # its provisional reservation so it can't leak into the next event.
                with contextlib.suppress(Exception):
                    self._finalize_reservation(event, emitted=False)
                self._dead_letter_unknown(event, exc, decision_time=decision_time, result=result)
        finally:
            mutex.release()

    def _finalize_deferred_event_unit(
        self,
        event: OpportunityEvent,
        submit_result: EventSubmissionReceipt,
        *,
        decision_time: datetime,
        result: ReactorResult,
        wait_ms: int | None = None,
    ) -> bool:
        """Window B for an event whose Window A joined a global auction epoch."""

        mutex = world_write_mutex()
        lock_wait_ms = (
            _reactor_claim_busy_timeout_ms() if wait_ms is None else max(0, wait_ms)
        )
        acquired = (
            mutex.acquire()
            if wait_ms is None
            else mutex.acquire(timeout=lock_wait_ms / 1000.0)
        )
        if not acquired:
            with contextlib.suppress(Exception):
                self._finalize_reservation(event, emitted=False)
            result.rejection_reasons.append(_POST_SUBMIT_WORLD_WRITE_LOCK_RETRY)
            result.retried += 1
            return False
        try:
            try:
                with _scoped_sqlite_busy_timeout(
                    self._store.conn, lock_wait_ms
                ):
                    if not self._store.conn.in_transaction:
                        self._store.conn.execute("BEGIN IMMEDIATE")
                    self._store.conn.execute("SAVEPOINT edli_reactor_event")
                    accepted_before = result.proof_accepted
                    disposition = self._process_one_post_submit(
                        event,
                        submit_result,
                        decision_time=decision_time,
                        result=result,
                    )
                    emitted = result.proof_accepted > accepted_before
                    self._finalize_reservation(event, emitted=emitted)
                    self._finalize_disposition(
                        event,
                        disposition,
                        decision_time=decision_time,
                        result=result,
                        proof_emitted=emitted,
                    )
                    self._store.conn.execute("RELEASE SAVEPOINT edli_reactor_event")
                    self._commit_event_unit()
            except Exception as exc:
                if _is_sqlite_lock_error(exc):
                    with contextlib.suppress(Exception):
                        self._store.conn.execute("ROLLBACK TO SAVEPOINT edli_reactor_event")
                        self._store.conn.execute("RELEASE SAVEPOINT edli_reactor_event")
                    with contextlib.suppress(Exception):
                        self._finalize_reservation(event, emitted=False)
                    with contextlib.suppress(Exception):
                        if getattr(self._store.conn, "in_transaction", False):
                            self._store.conn.rollback()
                    try:
                        with _scoped_sqlite_busy_timeout(self._store.conn, 0):
                            self._store.requeue_pending(
                                event.event_id,
                                last_error=_POST_SUBMIT_WORLD_WRITE_LOCK_RETRY,
                            )
                            self._commit_event_unit()
                    except Exception as requeue_exc:
                        if not _is_sqlite_lock_error(requeue_exc):
                            raise
                        with contextlib.suppress(Exception):
                            self._store.conn.rollback()
                    result.rejection_reasons.append(_POST_SUBMIT_WORLD_WRITE_LOCK_RETRY)
                    result.retried += 1
                    return False
                with contextlib.suppress(Exception):
                    self._store.conn.execute("ROLLBACK TO SAVEPOINT edli_reactor_event")
                    self._store.conn.execute("RELEASE SAVEPOINT edli_reactor_event")
                with contextlib.suppress(Exception):
                    self._finalize_reservation(event, emitted=False)
                self._dead_letter_unknown(
                    event,
                    exc,
                    decision_time=decision_time,
                    result=result,
                )
            return True
        finally:
            mutex.release()

    def _finalize_reservation(self, event: OpportunityEvent, *, emitted: bool) -> None:
        """Commit or roll back this event's PROVISIONAL in-flight reservation.

        FIX B (P1 zero-submit co-cause, 2026-06-05). The submit adapter reserves
        a candidate's stake provisionally the moment it passes Kelly + RiskGuard.
        Passing Kelly is NOT emission — the receipt can still be rejected at
        DECISION_CERTIFICATE / EXECUTOR_EXPRESSIBILITY (or requeued transiently)
        in the post-submit phase. This finalizes that provisional reserve:

          - ``emitted=True``  (proof_accepted advanced): commit — the stake is
            real same-cycle in-flight capital the NEXT event must net (INV-K7).
          - ``emitted=False`` (rejected / retried / errored): rollback — the
            stake never reached the venue this cycle, so it must NOT inflate
            corr_committed_usd / raw_committed_usd for later candidates.

        The ledger is exposed by the adapter on the injected submit callable as
        ``reservation_ledger``. Absent it (legacy list-backed adapters / tests),
        this is a no-op — the pre-FIX-B append-only behavior is preserved.
        """
        ledger = getattr(self._submit, "reservation_ledger", None)
        if ledger is None:
            return
        event_id = getattr(event, "event_id", None)
        if event_id is None:
            return
        if emitted:
            ledger.commit(event_id)
        else:
            ledger.rollback(event_id)

    def _transient_horizon_terminal(
        self, event: OpportunityEvent, *, decision_time: datetime
    ) -> tuple[str, str] | None:
        """Return the EVENT-HORIZON terminal for a transient block, or None to requeue.

        Operator law 2026-06-12 ("no caps"; "重试次数不是市场事实"): a transient
        money-path block requeues INDEFINITELY until a SEMANTIC horizon fires.
        This replaces the attempt-count terminalization. Returns
        ``(horizon_label, detail)`` when a horizon has been crossed, else None.

        Horizons (in precedence order):
          (c) OPERATOR_DISARM — the operator env kill-switch is set. Checked first
              so a disarm terminalizes everything in-flight immediately.
          (d) EVENT_EXPIRES_AT_PAST or payload-level SELECTION_DEADLINE_PAST — the
              event's own execution window is past. A selection_deadline embedded in an
              EXECUTABLE_SNAPSHOT_STALE reason is NOT an event horizon; it is stale price
              evidence and must refresh/requeue.
          (b) MARKET_VENUE_CLOSED — only explicit venue evidence says
              ``closed=true`` and ``accepting_orders=false``. Static Gamma endDate
              / F1 timing cannot terminalize a money-path event.
          (a) TIMELINESS_FLOOR_PAST — the event is no longer timely. Delegates to
              the SINGLE existing timeliness authority (EventStore._is_timely):
              a forecast-decision event whose target LOCAL day is strictly past
              has crossed its market horizon. This is the SAME predicate
              fetch_pending applies on its read floor — no second clock.

        Non-family-keyed events (no city+target_date) have no timeliness floor of
        their own — for them only the operator disarm horizon applies; absent that
        they requeue until consumed by another terminal path. They cannot leak the
        queue: the cross-city round-robin in fetch_pending interleaves fresh events
        fairly (see _note_transient_requeue docstring).
        """
        # (c) Operator disarm — highest precedence kill-switch.
        if _operator_disarm_active():
            return ("OPERATOR_DISARM", f"{_TRANSIENT_DISARM_ENV} set")

        deadline_horizon = _event_deadline_horizon(
            event,
            decision_time=decision_time,
            transient_reason=self._last_transient_requeue_reason(event),
        )
        if deadline_horizon is not None:
            return deadline_horizon

        # (b) Venue-close floor. This is evidence-based, not time-derived.
        venue_closed = self._venue_market_closed_horizon(event, decision_time=decision_time)
        if venue_closed is not None:
            return venue_closed

        venue_not_listed = self._venue_market_not_listed_horizon(event)
        if venue_not_listed is not None:
            return venue_not_listed

        # (a) Timeliness floor — reuse the store's single authority. _is_timely
        # returns True for non-forecast-decision events (no floor) and for any
        # event still within its tradeable window; only a strictly-past
        # forecast-decision event returns False -> horizon crossed. Fail-soft: if
        # the authority is unavailable (legacy store) we requeue (no terminal),
        # never burn the event on a missing predicate.
        is_timely_fn = getattr(self._store, "_is_timely", None)
        if callable(is_timely_fn):
            try:
                timely = is_timely_fn(event, decision_time.astimezone(UTC))
            except Exception:
                timely = True
            if not timely:
                return ("TIMELINESS_FLOOR_PAST", "target local day strictly past")
        return None

    def _venue_market_closed_horizon(
        self, event: OpportunityEvent, *, decision_time: datetime
    ) -> tuple[str, str] | None:
        """Terminalize only on explicit venue-closed evidence.

        Gamma ``endDate``/the old F1 12:00Z anchor is not enough: live weather
        markets can remain ``closed=false`` and ``acceptingOrders=true`` after
        that timestamp. A static city/date calculation therefore cannot burn a
        money-path event. The executable snapshot and submit layers already use
        the decomposed venue authority: orderbook enabled, not closed, and
        accepting_orders not explicitly false.
        """
        if event.event_type not in _VENUE_CLOSE_HORIZON_EVENT_TYPES:
            return None
        payload = _payload_dict(event)
        def _venue_bool(value: object) -> bool | None:
            if isinstance(value, bool):
                return value
            if value is None:
                return None
            text = str(value).strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off"}:
                return False
            return None

        closed = _venue_bool(payload.get("closed") or payload.get("market_closed"))
        accepting = _venue_bool(
            payload.get("accepting_orders")
            if "accepting_orders" in payload
            else payload.get("acceptingOrders")
        )
        if closed is True and accepting is False:
            return ("MARKET_VENUE_CLOSED", "explicit venue closed=true accepting_orders=false")
        return None

    def _venue_market_not_listed_horizon(
        self, event: OpportunityEvent
    ) -> tuple[str, str] | None:
        """Terminal horizon for a family proven unlisted by the live venue-discovery lane.

        This is not a topology-cache miss. It fires only for an executable-snapshot block whose
        injected provider has current Gamma-empty/no-listed-market evidence for this exact
        (city, target_date, metric). Network failures, lock contention, time-box misses, stale books,
        and missing providers return None so the event keeps requeueing.
        """
        if self._family_market_absence_provider is None:
            return None
        last_reason = self._last_transient_requeue_reason(event)
        if _money_path_reason_base(last_reason or "") != "EXECUTABLE_SNAPSHOT_BLOCKED":
            return None
        family = self._family_identity(event)
        if family is None:
            return None
        city, target_date, metric = family
        try:
            absent = bool(
                self._family_market_absence_provider(
                    city=city,
                    target_date=target_date,
                    metric=metric,
                )
            )
        except Exception:
            return None
        if not absent:
            return None
        return (
            "VENUE_MARKET_NOT_LISTED",
            f"Gamma/topology has no listed Polymarket market for {city}/{target_date}/{metric}",
        )

    def _last_transient_requeue_reason(self, event: OpportunityEvent) -> str | None:
        """Return the transient cause from memory or durable processing state.

        ``_transient_requeue_reasons`` is process-local. After a daemon restart,
        the durable retry cause lives in ``opportunity_event_processing.last_error``.
        Losing it lets a stale executable-snapshot ``selection_deadline`` be read
        as an event horizon instead of price evidence.
        """

        event_id = str(getattr(event, "event_id", "") or "")
        if not event_id:
            return None
        live_reason = self._transient_requeue_reasons.get(event_id)
        if live_reason:
            return live_reason
        try:
            stored_reason = self._store.processing_last_error(event_id)
        except Exception:
            return None
        if stored_reason and _is_transient_money_path_reason(stored_reason):
            self._transient_requeue_reasons[event_id] = stored_reason
            return stored_reason
        return None

    @staticmethod
    def _family_identity(event: OpportunityEvent) -> tuple[str, str, str] | None:
        """The (city, target_date, metric) family key from the event payload, or None when the
        event is not a forecast-decision family event (no refreshable substrate). Fail-soft: a
        malformed payload yields None and the always-decidable refresh is simply skipped."""
        payload = _payload_dict(event)
        city = str(payload.get("city") or "").strip()
        target_date = str(payload.get("target_date") or "").strip()
        metric = str(payload.get("metric") or "").strip()
        if not city or not target_date or metric not in {"high", "low"}:
            return None
        return (city, target_date, metric)

    def _record_substrate_block(
        self, event: OpportunityEvent, *, kind: str
    ) -> None:
        """ALWAYS-DECIDABLE invariant (2026-06-12): record that THIS event was blocked on a
        REFRESHABLE substrate this cycle so the post-unit-of-work drain refreshes that substrate.

        ``kind`` is ``"snapshot"`` (executable-snapshot block -> family_snapshot_refresher),
        ``"posterior"`` (stale/absent replacement posterior -> single-family cycle-advance reseed),
        or ``"day0_hourly"`` (Day0 remaining-day hourly-vector substrate). De-duplicated per family
        per cycle (a family blocked by many bins refreshes ONCE). The intents are drained AFTER the
        event's unit-of-work closes (no network in any open txn).
        """
        family = self._family_identity(event)
        if family is None:
            return
        if kind == "snapshot":
            bucket = self._pending_snapshot_refreshes
        elif kind == "posterior":
            bucket = self._pending_cycle_advances
        elif kind == "day0_hourly":
            bucket = self._pending_day0_hourly_refreshes
        else:
            return
        if family not in bucket:
            bucket.append(family)

    def _drain_substrate_refreshes(self, *, result: ReactorResult) -> None:
        """Invoke the substrate refreshers for every family blocked this cycle, OUTSIDE any world
        write SAVEPOINT / trade read txn (the drain runs at end-of-cycle in process_pending, where
        no per-event txn is open — the structural no-network-in-txn guarantee).

        FAIR-CURSOR fan-out: rotate which blocked family is refreshed first across cycles so no one
        family monopolizes; ALL families NOT reached this cycle (whether by the wall-clock budget
        below or skipped by debounce) are covered on later cycles (bounded-cycle coverage, no
        starvation). Per-family debounce (window derived from the snapshot freshness window) skips a
        family refreshed too recently. Fail-soft: a refresh failure logs once and never raises — the
        event already requeued and the horizon bounds it.

        DRAIN BUDGET (#83, 2026-06-16): the drain is bounded by a per-cycle wall-clock budget
        (ZEUS_REACTOR_DRAIN_BUDGET_SECONDS, default 10.0s) so ~49 blocked-family /book fetches can
        no longer blow the cycle past its 60s schedule and coalesce it into multi-minute gaps. This
        is a BACKGROUND-I/O time budget — identical in kind to the warm-cycle
        ZEUS_REACTOR_REFRESH_BUDGET_SECONDS — NOT a money-path cap/throttle/allowlist/notional
        limit: decisions, the 30s decision budget, the fair rotation order, and every money-path
        gate are untouched; only the background refresh fan-out is time-bounded. The budget is
        SHARED across both buckets and HELD-position families are drained FIRST (money at risk),
        so a budget can never starve a held family's refresh.
        """
        sidecar_owns_broad = _substrate_sidecar_owns_broad_refresh()
        if sidecar_owns_broad:
            blocked = (
                len(self._pending_snapshot_refreshes)
                + len(self._pending_cycle_advances)
                + len(self._pending_day0_hourly_refreshes)
            )
            if blocked:
                import logging as _logging

                _logging.getLogger("zeus.events.reactor").info(
                    "always-decidable broad drain delegated to substrate observer sidecar; "
                    "draining targeted blocked-family refreshes locally deferred_families=%d",
                    blocked,
                )
        # HELD-POSITION set, computed ONCE per cycle (fail-soft): families with money at risk now.
        held = self._held_families_failsoft()
        if held:
            import logging as _logging

            _logging.getLogger("zeus.events.reactor").debug(
                "always-decidable drain ordering: held-position-first (%d held), then fair "
                "rotation; basis=position_current", len(held),
            )
        # SHARED per-cycle drain deadline (monotonic). None => budget disabled (legacy unbounded
        # drain). Day0 hourly vectors drain BEFORE executable snapshots: a DAY0 event that has
        # already proven its weather-carrier gap must not wait behind CLOB snapshot refresh I/O
        # before it can re-price the observed local day. Held families are still first within each
        # bucket.
        drain_budget = _drain_budget_seconds()
        drain_deadline = (time.monotonic() + drain_budget) if drain_budget is not None else None
        self._drain_one_bucket(
            self._pending_day0_hourly_refreshes,
            refresher=self._day0_hourly_refresher,
            last_at=self._family_day0_hourly_last_at,
            counter_attr="day0_hourly_refreshes",
            label="day0-hourly",
            result=result,
            held=held,
            deadline=drain_deadline,
        )
        self._drain_one_bucket(
            self._pending_snapshot_refreshes,
            refresher=self._family_snapshot_refresher,
            last_at=self._family_refresh_last_at,
            counter_attr="snapshot_refreshes",
            label="snapshot",
            result=result,
            held=held,
            deadline=drain_deadline,
        )
        self._drain_one_bucket(
            self._pending_cycle_advances,
            refresher=self._cycle_advance_enqueuer,
            last_at=self._family_cycle_advance_last_at,
            counter_attr="cycle_advance_enqueues",
            label="cycle-advance",
            result=result,
            held=held,
            deadline=drain_deadline,
        )
        # NOTE: _drain_one_bucket clears each bucket on full drain and RETAINS only the
        # budget-truncated remainder (#83), so a blanket clear here is intentionally absent —
        # it would discard the unreached families the budget deferred to a later cycle.

    def _held_families_failsoft(self) -> frozenset[tuple[str, str, str]]:
        """Current held (city, target_date, metric) families, or empty on absence/error. Read-only,
        fail-soft: a provider that raises or is absent yields no held bias (pure fair rotation)."""
        if self._held_family_provider is None:
            return frozenset()
        try:
            return frozenset(self._held_family_provider())
        except Exception:  # noqa: BLE001 — held-position bias is best-effort, never fatal
            return frozenset()

    def _drain_one_bucket(
        self,
        families: list[tuple[str, str, str]],
        *,
        refresher: "Callable[..., bool] | None",
        last_at: dict[str, float],
        counter_attr: str,
        label: str,
        result: ReactorResult,
        held: "frozenset[tuple[str, str, str]]" = frozenset(),
        deadline: "float | None" = None,
    ) -> None:
        if refresher is None or not families:
            return
        import logging as _logging

        _log = _logging.getLogger("zeus.events.reactor")
        # ORDERING (operator correction 2026-06-12): held-position families FIRST (money at risk),
        # then FAIR ROTATION over the rest. Rationale for fair rotation as the new-money order:
        # under the per-cycle drain wall-clock budget (#83, 2026-06-16) the cursor decides WHICH
        # family is touched first within the cycle — exactly the "future per-cycle fan-out cap" this
        # comment already anticipated. Fair (round-robin) rotation gives the best WORST-CASE
        # time-to-full-coverage under that cap: every family advances to the front within n cycles,
        # so no family can be starved past n cycles — a bounded, liquidity-blind guarantee.
        # Staleness-first was rejected because it would re-introduce a per-family priority signal the
        # operator's RULE-1 (every family decidable) does not want. HELD-position families sort
        # FIRST in ``ordered`` and the budget is only checked AFTER each refresh completes, so a
        # held family is never budget-starved.
        held_fams = [f for f in families if f in held]
        rest = [f for f in families if f not in held]
        n = len(rest)
        start = self._family_refresh_cursor % n if n else 0
        rotated_rest = rest[start:] + rest[:start]
        # Advance the cursor only over the rotated (non-held) set; held families are ordering-
        # exempt (always first) so they do not consume rotation slots.
        self._family_refresh_cursor = (self._family_refresh_cursor + 1) % n if n else 0
        ordered = held_fams + rotated_rest
        n_held = len(held_fams)
        now = time.monotonic()
        unreached: list[tuple[str, str, str]] = []
        budget_truncated = False
        for idx, (city, target_date, metric) in enumerate(ordered):
            # DRAIN BUDGET (#83): stop BEFORE invoking the refresher once the shared per-cycle
            # wall-clock budget is spent — so we always finish the CURRENT family first and never
            # cut a /book fetch mid-network. The families not reached are retained in the bucket
            # (below) for a later cycle; the fair-cursor rotation guarantees they reach the front
            # within bounded cycles. The budget can ONLY truncate the NON-HELD rotation tail:
            #   * idx < n_held  -> a HELD-position family (money at risk): NEVER truncated, always
            #     refreshed even if the budget is already spent (the operator's held-first law).
            #   * idx == n_held -> the FIRST non-held family: always attempted so a budget-exhausted
            #     cycle still makes one unit of new-money progress (no total stall).
            #   * idx >  n_held -> truncatable once the budget is spent.
            if (
                deadline is not None
                and idx > n_held  # past all held families AND past the first non-held one
                and time.monotonic() >= deadline
            ):
                unreached = list(ordered[idx:])
                budget_truncated = True
                break
            key = f"{city}|{target_date}|{metric}"
            prev = last_at.get(key)
            if prev is not None and (now - prev) < _FAMILY_REFRESH_DEBOUNCE_SECONDS:
                # Debounced: a refresh of this family within the window is still fresh; skip.
                continue
            # Mark BEFORE the call so a slow/failing refresh still debounces the next cycle (the
            # window is the minimum spacing between ATTEMPTS, not between successes).
            last_at[key] = now
            try:
                refreshed = bool(
                    refresher(city=city, target_date=target_date, metric=metric)
                )
            except Exception as exc:  # noqa: BLE001 — fail-soft: never raise into the cycle
                _log.warning(
                    "always-decidable %s refresh failed for %s/%s/%s (fail-soft; event "
                    "already requeued, horizon-bounded): %s",
                    label, city, target_date, metric, exc,
                )
                continue
            if refreshed:
                setattr(result, counter_attr, getattr(result, counter_attr) + 1)
        # BUDGET TRUNCATION (#83): retain the unreached families IN the bucket so they are visible
        # and carried (the caller drops the blanket clear). Visibility counter records how many were
        # deferred; the next cycle's fair rotation advances them toward the front (no starvation).
        if budget_truncated:
            families[:] = unreached
            result.drained_truncated += len(unreached)
            _log.info(
                "always-decidable %s drain hit per-cycle budget (ZEUS_REACTOR_DRAIN_BUDGET_"
                "SECONDS); deferred %d famil%s to a later cycle (held-first preserved; "
                "fair-rotation guarantees bounded coverage; NOT a money-path cap)",
                label, len(unreached), "y" if len(unreached) == 1 else "ies",
            )
        else:
            families.clear()

    def _note_transient_requeue(self, event: OpportunityEvent) -> None:
        """Bump the per-event requeue counter and log with dedup (LOG HYGIENE ONLY).

        The count NEVER terminalizes (operator law: a retry count is not a market
        fact). It exists solely so a perpetually-requeued event does not log every
        cycle: log at attempt 1, then every _TRANSIENT_REQUEUE_LOG_EVERY.

        STARVATION GUARD (design note, verified in the test suite): infinite
        requeue cannot let one event starve the queue. requeue_pending returns the
        event to 'pending' WITHOUT resetting fetch_pending's ordering authority,
        whose PRIMARY cross-city key is the per-(tier, city) round-robin rank
        (_city_round): a budget of K reaches K DISTINCT cities per cycle, so a
        single perpetually-transient city event can never preempt fresh events
        from other cities. The retry-debt tiebreak (attempt_count>0 sorts ahead)
        only applies WITHIN the same (target_date, available_at) across the same
        round — it interleaves, it does not monopolize.
        """
        event_id = getattr(event, "event_id", None)
        if event_id is None:
            return
        count = self._transient_requeue_counts.get(event_id, 0) + 1
        self._transient_requeue_counts[event_id] = count
        if count == 1 or (count % _TRANSIENT_REQUEUE_LOG_EVERY) == 0:
            import logging as _logging

            reason = self._transient_requeue_reasons.get(event_id, "EXECUTABLE_SNAPSHOT_PENDING")
            _logging.getLogger("zeus.events.reactor").info(
                "reactor: money-path transient requeued (no cap; horizon-bounded) "
                "event_id=%s count=%d reason=%s",
                event_id,
                count,
                reason,
            )

    def _finalize_disposition(
        self,
        event: OpportunityEvent,
        disposition: str | None,
        *,
        decision_time: datetime,
        result: ReactorResult,
        proof_emitted: bool = False,
    ) -> None:
        """Apply the terminal/retry book-keeping for a window disposition.

        Runs INSIDE the caller's open savepoint (Window A or Window B, mutex
        held). The gate/reject/decision ledger writes for these dispositions were
        already emitted by ``_process_one_pre_submit`` / ``_process_one_post_submit``;
        here we only add the retry/dead-letter/mark-processed accounting that the
        legacy single-pass ``_process_event_unit`` did identically for both phases.
        """
        if disposition == _FSR_PARTIAL_DEAD_LETTER:
            # PARTIAL FSR: reject + dead_letter writes already committed upstream.
            # Only release the savepoint; do NOT call mark_processed.
            return
        if disposition == _EXECUTABLE_SNAPSHOT_RETRY:
            horizon = self._transient_horizon_terminal(event, decision_time=decision_time)
            if horizon is not None:
                # EVENT-HORIZON TERMINAL (operator law 2026-06-12). The event is
                # terminalized because a SEMANTIC horizon fired — the event is no
                # longer timely (its market settled) or the operator disarmed the
                # lane — NEVER because it was retried N times. The label carries
                # the horizon AND the last honest transient cause; an attempt
                # count never appears in the terminal evidence.
                horizon_label, horizon_detail = horizon
                last_transient = self._transient_requeue_reasons.pop(event.event_id, None)
                self._transient_requeue_counts.pop(event.event_id, None)
                cause = last_transient or "EXECUTABLE_SNAPSHOT_PENDING"
                reason_label = f"MONEY_PATH_HORIZON_EXPIRED:{horizon_label}:{cause}"
                error_message = (
                    f"money-path transient terminalized at event horizon "
                    f"({horizon_label}{(': ' + horizon_detail) if horizon_detail else ''}); "
                    f"last reason: {cause}"
                )
                self._reject_event(
                    event, "EXECUTABLE_QUOTE", reason_label, result, decision_time=decision_time
                )
                self._store.mark_dead_letter(
                    event,
                    failure_stage="MONEY_PATH_HORIZON_EXPIRED",
                    error_message=error_message,
                    created_at=decision_time.astimezone(UTC).isoformat(),
                )
                result.dead_lettered += 1
            else:
                # Transient block, NO horizon: requeue for retry next cycle
                # (after capture completes / book settles / risk clears). Do NOT
                # consume the event the way mark_processed would. There is NO
                # attempt cap — the event requeues until a horizon terminal fires.
                last_reason = self._last_transient_requeue_reason(event)
                retry_not_before = None
                if _money_path_reason_base(last_reason or "") in {
                    "EXECUTABLE_SNAPSHOT_BLOCKED",
                    "EXECUTABLE_SNAPSHOT_STALE",
                } or _is_day0_hourly_refresh_reason(last_reason):
                    try:
                        snapshot_block_attempts = self._store.attempt_count(event.event_id)
                    except Exception:
                        snapshot_block_attempts = 1
                    retry_not_before = (
                        decision_time.astimezone(UTC)
                        + timedelta(
                            seconds=_snapshot_block_retry_delay_seconds(
                                attempt_count=snapshot_block_attempts
                            )
                        )
                    ).isoformat()
                elif _is_runtime_authority_retry_reason(last_reason):
                    retry_not_before = (
                        decision_time.astimezone(UTC)
                        + timedelta(
                            seconds=_runtime_authority_retry_delay_seconds(last_reason)
                        )
                    ).isoformat()
                self._note_transient_requeue(event)
                processing_error = (
                    GLOBAL_WINNER_TARGETED_CLAIM
                    if str(event.source or "").startswith(
                        "global_auction_winner_target:"
                    )
                    else last_reason
                )
                self._store.requeue_pending(
                    event.event_id,
                    not_before=retry_not_before,
                    last_error=processing_error,
                )
                result.retried += 1
                result.rejection_reasons.append(last_reason or "EXECUTABLE_SNAPSHOT_PENDING")
            return
        # disposition is None: a pre-submit gate rejected the event (its reject
        # ledgers were written in _process_one_pre_submit). The legacy single-pass
        # flow marked such drained-rejection events processed and counted them as
        # ``processed`` (the event is consumed, not retried). Preserve that exactly.
        self._transient_requeue_reasons.pop(event.event_id, None)
        self._transient_requeue_counts.pop(event.event_id, None)
        if not proof_emitted and not self._terminal_rejection_evidence_exists(event.event_id):
            self._reject_event(
                event,
                "UNKNOWN_REVIEW_REQUIRED",
                "UNKNOWN_REVIEW_REQUIRED:PROCESSED_WITHOUT_DECISION_EVIDENCE",
                result,
                decision_time=decision_time,
            )
        self._store.mark_processed(event.event_id, processed_at=decision_time.astimezone(UTC).isoformat())
        result.processed += 1

    def _terminal_rejection_evidence_exists(self, event_id: str) -> bool:
        for table in (
            "edli_no_submit_receipts",
            "decision_compile_failures",
            "no_trade_regret_events",
        ):
            try:
                if self._store.conn.execute(
                    f"SELECT 1 FROM {table} WHERE event_id = ? LIMIT 1",
                    (event_id,),
                ).fetchone():
                    return True
            except sqlite3.Error:
                continue
        return False

    def _dead_letter_unknown(
        self,
        event: OpportunityEvent,
        exc: BaseException,
        *,
        decision_time: datetime,
        result: ReactorResult,
    ) -> None:
        """Emit the UNKNOWN_REVIEW_REQUIRED dead-letter world write unit.

        Caller MUST hold the world mutex; this opens no savepoint of its own so it
        is safe both from Window A's open savepoint (after rollback) and from a
        freshly-acquired mutex with no open txn.
        """
        with contextlib.suppress(Exception):
            self._store.conn.execute("ROLLBACK TO SAVEPOINT edli_reactor_event")
            self._store.conn.execute("RELEASE SAVEPOINT edli_reactor_event")
        self._reject(event, "UNKNOWN_REVIEW_REQUIRED", str(exc))
        self._write_compile_failure(
            event,
            "UNKNOWN_REVIEW_REQUIRED",
            str(exc),
            decision_time=decision_time,
        )
        self._write_regret(event, "UNKNOWN_REVIEW_REQUIRED", str(exc), decision_time=decision_time)
        self._store.mark_dead_letter(
            event,
            failure_stage="UNKNOWN_REVIEW_REQUIRED",
            error_message=str(exc),
            created_at=decision_time.astimezone(UTC).isoformat(),
        )
        self._commit_event_unit()
        result.dead_lettered += 1

    def _commit_event_unit(self) -> None:
        """Commit the current event's world-DB write unit and release the WAL write lock.

        EDLI live-canary contention fix (2026-05-31): the reactor and the
        market-channel ingestor are two in-process writers on the same WAL
        zeus-world.db. Previously the reactor opened one implicit DEFERRED
        transaction at the first ``claim()`` and held the single WAL *write* lock
        for the WHOLE cycle (~330 s, incl. HTTP submit work), committing only at
        cycle end. The ingestor thread then blocked the full 30 s busy_timeout on
        every write → "database is locked" → the reactor cycle hung/skipped.

        Committing PER EVENT releases the WAL write lock between events so the
        ingestor (and any other world writer) gets frequent windows to write,
        instead of waiting out a cycle-long lock. The process-global world-DB
        write mutex (``world_write_lock`` in db.py) additionally guarantees the
        two threads never hold the SQLite write lock concurrently, so a contended
        write waits cleanly on the Python mutex rather than crashing on a
        busy_timeout "database is locked".

        Real sqlite3 connections implement ``.commit()``; test doubles may not, so
        we tolerate AttributeError. Failure-soft: a commit error here is logged by
        the caller's exception path (the savepoint write already succeeded; the
        next cycle re-commits or the lease reclaims).
        """
        commit = getattr(self._store.conn, "commit", None)
        if callable(commit):
            commit()

    def _persist_belief_cache(
        self,
        receipt: "EventSubmissionReceipt | bool | None",
        *,
        decision_time: datetime,
    ) -> None:
        """P1 belief cache write — DEADLOCK-FREE (continuous re-decision resurrection 2026-06-12).

        Writes the family belief captured during decision (receipt.belief_payload) through THIS
        reactor's own world connection, inside the ALREADY-OPEN Window B SAVEPOINT. It opens NO new
        connection and issues NO commit — the reactor's own _commit_event_unit releases the row with
        the event's decision rows. This is the structural cure for the 2026-05-31 self-deadlock,
        where persist_belief_live opened a SECOND world connection and committed while this conn held
        the WAL write lock → SQLite hung process_pending.

        Fail-soft: any error is swallowed (a belief-cache miss must never break the decision). The
        write must NOT execute its own SAVEPOINT/commit — it is a bare INSERT on the open txn."""
        if not isinstance(receipt, EventSubmissionReceipt):
            return
        belief = getattr(receipt, "belief_payload", None)
        if not belief:
            return
        try:
            from src.events.continuous_redecision import write_belief_row

            write_belief_row(
                self._store.conn,
                family_id=str(belief.get("family_id") or ""),
                city=str(belief.get("city") or ""),
                target_date=str(belief.get("target_date") or ""),
                temperature_metric=str(
                    belief.get("temperature_metric") or belief.get("metric") or ""
                ),
                snapshot_id=str(belief.get("snapshot_id") or ""),
                calibrator_model_hash=str(belief.get("calibrator_model_hash") or "identity"),
                bin_labels=list(belief.get("bin_labels") or []),
                p_posterior_vec=list(belief.get("p_posterior_vec") or []),
                recorded_at=decision_time.astimezone(UTC).isoformat(),
                condition_ids=list(belief.get("condition_ids") or []),
                q_lcb_yes_vec=list(belief.get("q_lcb_yes_vec") or []),
                q_lcb_no_vec=list(belief.get("q_lcb_no_vec") or []),
            )
        except Exception:  # noqa: BLE001 — belief cache is non-critical; never break the decision
            import logging as _logging

            _logging.getLogger("zeus.events.reactor").debug(
                "belief cache write failed (fail-soft)", exc_info=True
            )

    def _process_one_pre_submit(
        self,
        event: OpportunityEvent,
        *,
        decision_time: datetime,
        result: ReactorResult,
        global_batch: bool = False,
    ) -> tuple[str | None, bool]:
        """Pre-submit gate phase (#95 SEV-2.1).

        Runs every gate that does NOT require the network submit. Returns
        ``(disposition, should_submit)``:
          * ``should_submit is True`` (disposition ``None``) → all gates passed;
            the caller commits the pre-submit world write unit, releases the
            mutex, and invokes the (network) submit OUTSIDE the lock.
          * ``should_submit is False`` → terminal/retry; ``disposition`` is one of
            ``None`` (a gate reject, its ledgers already written here),
            ``_FSR_PARTIAL_DEAD_LETTER`` or ``_EXECUTABLE_SNAPSHOT_RETRY``.

        Any world-DB ledger write here happens inside Window A (mutex held,
        savepoint open) — none of these paths touch the network.
        """
        assert_available_for_decision(event, decision_time)
        # DELETED 2026-06-12 (gate inventory D2): market-channel event types
        # stopped reaching this queue 2026-06-06 (upstream routing); the
        # adapter's final boundary scope gate fail-closes any straggler.
        # Antibody: tests/engine/test_event_reactor_no_bypass.py boundary test.
        if event.event_type in _FORECAST_DECISION_EVENT_TYPES:
            # EDLI_REDECISION_PENDING (price-driven forecast re-decision) carries the same FSR-shaped
            # payload and gets the same structural source-truth dead-letter treatment.
            # SERVE-FRESHEST-ELIGIBLE RECONCILIATION (2026-06-11, twin-authority #8).
            #
            # The event's coverage statuses describe the run the PRODUCER minted the
            # event against — typically the NEWEST run, which is PARTIAL/BLOCKED for
            # the whole cycle-build window (4x/day, every cycle). The bundle the
            # money path actually trades on is chosen by the SERVING AUTHORITY
            # (replacement_forecast_bundle_reader: tradeable-latest, 没有新的就用老的 —
            # a newer not-yet-eligible run NEVER blocks serving the freshest ELIGIBLE
            # older run; staleness/readiness BRANDS provenance, never blocks). The
            # adapter's read (_replacement_authority_probability_and_fdr_proof →
            # _latest_replacement_readiness + read_replacement_forecast_bundle) is
            # keyed by (city, target_date, metric) — NOT pinned to this event's
            # source_run — so the event's coverage statuses are ADVISORY here, not
            # binding. Dead-lettering on them was the SAME serve-freshest rule
            # re-implemented (wrongly) at a second site: live 2026-06-11T16:33:51Z
            # all six live-eligible cities were dead-lettered in one second on the
            # 12Z build window (coverage PARTIAL/BLOCKED) while a COMPLETE 06Z
            # posterior was servable — Miami had gone to SUBMIT on exactly that 06Z
            # substrate 12 minutes earlier.
            #
            # The gate now DEFERS to the reader: coverage PARTIAL/BLOCKED passes
            # THROUGH; when nothing eligible exists the adapter rejects honestly
            # with the full reason chain (REPLACEMENT_0_1_LIVE_BUNDLE_
            # BLOCKED + provenance envelope) — the certificate contract reads its
            # statuses from the SERVED bundle at proof time, never from this event
            # payload. Dead-letter remains ONLY for structurally junk payloads
            # (source_run_completeness_status outside {COMPLETE, PARTIAL} =
            # malformed/unknown producer state — no serving authority can vouch
            # for an event whose own run identity is unparseable).
            try:
                payload = json.loads(event.payload_json) if isinstance(event.payload_json, str) else event.payload_json
                src_completeness = str(payload.get("source_run_completeness_status", "") or "")
            except Exception:
                src_completeness = ""
            if src_completeness not in {"COMPLETE", "PARTIAL"}:
                error_msg = (
                    "FSR payload structurally junk (run identity unparseable): "
                    f"source_run_completeness_status={src_completeness!r} not in "
                    "{'COMPLETE', 'PARTIAL'}; dead-lettering"
                )
                self._reject_event(event, "SOURCE_TRUTH", "FSR_WINDOW_AUTHORITY_NOT_LIVE_ELIGIBLE", result, decision_time=decision_time)
                self._store.mark_dead_letter(
                    event,
                    failure_stage="FSR_WINDOW_AUTHORITY_NOT_LIVE_ELIGIBLE",
                    error_message=error_msg,
                    created_at=decision_time.astimezone(UTC).isoformat(),
                )
                result.dead_lettered += 1
                return _FSR_PARTIAL_DEAD_LETTER, False
        if self._config.reactor_mode not in EDLI_PROCESSING_REACTOR_MODES:
            self._reject_event(event, "LIVE_CAP", "REACTOR_NOT_LIVE", result, decision_time=decision_time)
            return None, False
        if event.event_type == "DAY0_EXTREME_UPDATED" and not _day0_hard_fact_payload_live_eligible(event):
            self._reject_event(event, "SOURCE_TRUTH", "DAY0_HARD_FACT_AUTHORITY_BLOCKED", result, decision_time=decision_time)
            return None, False
        if not self._source_truth_gate(event):
            self._reject_event(event, "SOURCE_TRUTH", "SOURCE_TRUTH_BLOCKED", result, decision_time=decision_time)
            return None, False
        if (
            not global_batch
            and not self._executable_snapshot_gate(event, decision_time.astimezone(UTC))
        ):
            # Transient: the family's executable snapshots may not be captured yet this cycle.
            # Signal a retry instead of consuming the event (see process_pending).
            #
            # ALWAYS-DECIDABLE invariant (operator law 2026-06-12): a transient SUBSTRATE block
            # MUST trigger that substrate's refresh as part of the SAME handling — requeue-without-
            # refresh-attempt is structurally impossible here now. Record the blocked family; the
            # end-of-cycle drain captures FRESH books for it (outside any txn — three-phase law) so
            # the NEXT cycle finds the substrate fresh and the event PROCESSES instead of spinning
            # against the gate until horizon. This is the fix for "an event blocked AT the reactor
            # gate never reaches the refresher" — the refresher is now reached for exactly it.
            self._transient_requeue_reasons[event.event_id] = "EXECUTABLE_SNAPSHOT_BLOCKED"
            self._record_substrate_block(event, kind="snapshot")
            return _EXECUTABLE_SNAPSHOT_RETRY, False
        self._log_family_once(event)
        if not self._riskguard_gate(event):
            # TRANSIENT REQUEUE, never terminal consumption (2026-06-12
            # riskguard-storm incident): the gate fails closed to RED on a
            # STALE/missing/locked risk_state read as well as on an honest risk
            # halt. Terminally rejecting here let every transient writer gap
            # (daemon-restart boot windows, the chain_state poison-row crash,
            # dependency_db_locked) mass-consume the pending queue — 1100+
            # events burned on 2026-06-12 while risk truth was GREEN. Requeue
            # with the shared horizon-bounded disposition instead: NOTHING
            # submits while blocked (the gate is not weakened), and a sustained
            # genuine halt terminalizes only when an event horizon fires, carrying
            # the honest RISK_GUARD_BLOCKED cause.
            self._transient_requeue_reasons[event.event_id] = "RISK_GUARD_BLOCKED"
            return _EXECUTABLE_SNAPSHOT_RETRY, False
        return None, True

    def _process_one_post_submit(
        self,
        event: OpportunityEvent,
        submit_result: "bool | None | EventSubmissionReceipt",
        *,
        decision_time: datetime,
        result: ReactorResult,
    ) -> str | None:
        """Post-submit phase (#95 SEV-2.1): consumes the submit receipt and writes
        the decision/receipt ledgers. Runs inside Window B (mutex held, savepoint
        open). ``submit_result`` was produced by the network submit OUTSIDE the
        lock. Returns a disposition (``None`` for terminal/accepted,
        ``_EXECUTABLE_SNAPSHOT_RETRY`` for a transient requeue) interpreted by the
        caller exactly as the legacy single-pass flow did.
        """
        receipt = _submission_receipt(event, submit_result)
        # P1 BELIEF CACHE (continuous re-decision resurrection 2026-06-12): persist the family
        # belief captured during decision THROUGH THIS conn, inside the already-open Window B
        # SAVEPOINT — no second connection, no separate commit (that was the 2026-05-31 deadlock).
        # The reactor's per-event _commit_event_unit releases it with the decision rows. Persisted
        # regardless of accept/reject (the belief is what we believed, independent of the trade
        # outcome). Best-effort + fail-soft: a cache-write hiccup must never break the decision.
        self._persist_belief_cache(receipt, decision_time=decision_time)
        if receipt is None or not _receipt_matches_event(event, receipt):
            reason = receipt.reason if receipt is not None and receipt.reason else "EVENT_SUBMISSION_RECEIPT_MISSING_OR_UNBOUND"
            return self._reject_or_retry_post_submit(
                event,
                "EXECUTOR_EXPRESSIBILITY",
                reason,
                result,
                receipt=receipt,
                decision_time=decision_time,
            )
        if (
            receipt.proof_accepted is False
            and str(receipt.reason or "").startswith("EDLI_LIVE_CERTIFICATE_BUILD_FAILED:")
        ):
            if _is_transient_money_path_reason(receipt.reason):
                if _certificate_build_failed_is_book_authority_gap(str(receipt.reason)):
                    self._write_regret(
                        event,
                        "EXECUTOR_EXPRESSIBILITY",
                        receipt.reason,
                        receipt=receipt,
                        decision_time=decision_time,
                    )
                self._transient_requeue_reasons[event.event_id] = str(receipt.reason)
                if (
                    _event_deadline_horizon(
                        event,
                        decision_time=decision_time,
                        transient_reason=receipt.reason,
                    )
                    is None
                    and _is_executable_snapshot_refresh_reason(str(receipt.reason))
                ):
                    self._record_substrate_block(event, kind="snapshot")
                return _EXECUTABLE_SNAPSHOT_RETRY
            return self._reject_or_retry_post_submit(
                event,
                "EXECUTOR_EXPRESSIBILITY",
                receipt.reason,
                result,
                receipt=receipt,
                decision_time=decision_time,
            )
        if _is_global_reduce_only_exit_receipt(receipt):
            # The global SELL certificate is persisted with the canonical
            # EXIT_INTENT/venue command in the trade DB by exit_lifecycle.  It is
            # not an entry DecisionCertificate and must not be reinterpreted by
            # entry-only trade-score/FDR/Kelly/final-intent checks here.
            if not self._config.real_order_submit_enabled:
                return self._reject_or_retry_post_submit(
                    event,
                    "EXECUTOR_EXPRESSIBILITY",
                    "GLOBAL_SELL_LIVE_SIDE_EFFECT_FORBIDDEN",
                    result,
                    receipt=receipt,
                    decision_time=decision_time,
                )
            result.proof_accepted += 1
            return None
        proof_stage, proof_reason = _receipt_money_path_blocker(receipt, self._config)
        if proof_stage is not None:
            return self._reject_or_retry_post_submit(
                event,
                proof_stage,
                proof_reason,
                result,
                receipt=receipt,
                decision_time=decision_time,
            )
        if receipt.side_effect_status in LIVE_EXECUTION_RECEIPT_TERMINAL_STATUSES and not self._config.real_order_submit_enabled:
            return self._reject_or_retry_post_submit(
                event,
                "EXECUTOR_EXPRESSIBILITY",
                receipt.reason or "EDLI_REAL_ORDER_SIDE_EFFECT_FORBIDDEN",
                result,
                receipt=receipt,
                decision_time=decision_time,
            )
        if receipt.side_effect_status not in {"NO_SUBMIT"} | EXECUTION_RECEIPT_TERMINAL_STATUSES and not self._config.real_order_submit_enabled:
            return self._reject_or_retry_post_submit(
                event,
                "EXECUTOR_EXPRESSIBILITY",
                receipt.reason or "EDLI_REAL_ORDER_SUBMIT_DISABLED",
                result,
                receipt=receipt,
                decision_time=decision_time,
            )
        if receipt.side_effect_status == "NO_SUBMIT":
            # PRICE-RACE NO_SUBMIT STATES (2026-06-11 live): SUBMIT_ABORTED_MODE_FLIPPED
            # arrives as a VERIFIED no-submit STATE (P0-1 design), not a rejection — so
            # the transient classifier in _reject_or_retry_post_submit never saw it and
            # the event was terminally consumed as proof_accepted while the FRESH book
            # carried positive EV (Busan 17:30:20Z, q_lcb 0.828 vs fresh ask 0.77,
            # twice in one cycle). A transient-classified reason on a NO_SUBMIT receipt
            # is the SAME stale-decision-vs-fresh-book race: requeue (horizon-bounded
            # via the shared disposition — no attempt cap) so the next cycle
            # re-decides on the fresh book and prices the fresh mode from the
            # start. The receipt is NOT persisted for the aborted attempt — the requeued
            # decision writes its own honest receipt.
            if _is_transient_money_path_reason(receipt.reason):
                if receipt.reason:
                    self._transient_requeue_reasons[event.event_id] = str(receipt.reason)
                    if (
                        _event_deadline_horizon(
                            event,
                            decision_time=decision_time,
                            transient_reason=receipt.reason,
                        )
                        is None
                        and _is_executable_snapshot_refresh_reason(str(receipt.reason))
                    ):
                        self._record_substrate_block(event, kind="snapshot")
                return _EXECUTABLE_SNAPSHOT_RETRY
            proof_bundle = receipt.decision_proof_bundle
            if proof_bundle is None:
                compile_result = self._decision_compiler.compile_no_submit(
                    event,
                    decision_time=decision_time,
                    mode="NO_SUBMIT",
                    proof_bundle=None,
                )
                self._decision_certificate_ledger.persist_failures(compile_result.failures)
                reason = (
                    compile_result.failures[0].reason_code
                    if compile_result.failures
                    else "NO_SUBMIT_PROOF_BUNDLE_REQUIRED"
                )
                return self._reject_or_retry_post_submit(
                    event,
                    "DECISION_CERTIFICATE",
                    reason,
                    result,
                    receipt=receipt,
                    decision_time=decision_time,
                )
            compile_result = self._decision_compiler.compile_no_submit(
                event,
                decision_time=decision_time,
                mode="NO_SUBMIT",
                proof_bundle=proof_bundle,
            )
            self._decision_certificate_ledger.persist_all(compile_result.certificates)
            self._decision_certificate_ledger.persist_failures(compile_result.failures)
            if compile_result.status != "VERIFIED":
                # KILLER 2 (2026-05-31): surface the UNDERLYING failing assertion
                # (CompileFailure.reason_detail), not just the opaque stage reason_code.
                # 147/308 positive-edge contested candidates died here as bare
                # NO_SUBMIT_CERTIFICATE_REJECTED with no diagnosable sub-reason in the
                # regret stream; the real reason was only in decision_compile_failures.
                failure = compile_result.failures[0] if compile_result.failures else None
                detail = getattr(failure, "reason_detail", None) if failure else None
                # TRANSIENT causality class (same family as SOURCE_CAPTURED_AFTER_DECISION_TIME,
                # #43): a parent certificate's source_available_at was bumped past this cycle's
                # decision_time by a later forecast re-ingest. This is NOT a terminal safety
                # rejection — on the next cycle decision_time advances past the source's
                # available time and the proof verifies. Requeue (bounded by retry cap →
                # dead-letter) instead of terminally dropping the positive-edge candidate.
                # 129/174 of the DECISION_CERTIFICATE rejections are exactly this.
                reason = failure.reason_code if failure else "NO_SUBMIT_CERTIFICATE_REJECTED"
                if detail:
                    reason = f"{reason}:{detail}"
                return self._reject_or_retry_post_submit(
                    event,
                    "DECISION_CERTIFICATE",
                    reason,
                    result,
                    receipt=receipt,
                    decision_time=decision_time,
                )
            # SUBMIT-LANE PERSIST-BOUNDARY INVARIANT (silent-trade-kill antibody
            # 2026-06-12; /tmp/allpass_nosubmit_rootcause.md). A VERIFIED, full-pass
            # (proof_accepted=True) NO_SUBMIT receipt is about to be booked as an
            # accepted terminal. If the daemon is NOMINALLY ARMED (reactor_mode=live AND
            # the operator arm is on — read the SAME way the main.py selector reads it,
            # no second authority) the receipt MUST NOT carry submit_lane="LIVE": the
            # live lane never produces a full-pass NO_SUBMIT with proof_accepted; a
            # typed pre-venue abort is proof_accepted=False and is routed through
            # visible rejection/retry classification. submit_lane="LIVE" here means
            # the live lane silently ate a tradeable entry
            # (the 11:51-12:12Z incident). Raise rather than persist the kill. Receipts
            # from the honest control-blocked lane (submit_lane="NO_SUBMIT_ADAPTER" + named
            # cause) and legacy pre-stamp receipts (submit_lane=None) pass through.
            self._assert_no_submit_lane_invariant(receipt)
            self._no_submit_receipt_ledger.insert_idempotent(receipt, decision_time=decision_time)
        elif receipt.side_effect_status in EXECUTION_RECEIPT_TERMINAL_STATUSES:
            certificates = _execution_receipt_certificate_bundle(receipt)
            if not certificates:
                return self._reject_or_retry_post_submit(
                    event,
                    "EXECUTION_RECEIPT",
                    receipt.reason or "EXECUTION_RECEIPT_CERTIFICATE_REQUIRED",
                    result,
                    receipt=receipt,
                    decision_time=decision_time,
                )
            self._decision_certificate_ledger.persist_all(certificates)
            if receipt.side_effect_status in DRY_EXECUTION_RECEIPT_TERMINAL_STATUSES:
                self._no_submit_receipt_ledger.insert_idempotent(
                    dataclass_replace(receipt, side_effect_status="NO_SUBMIT"),
                    decision_time=decision_time,
                )
            # FAILED-WITHOUT-SIDE-EFFECT terminals are VISIBLE rejections
            # (live 2026-06-12 00:52-01:13Z: five maker intents died
            # status=PRE_SUBMIT_ERROR and were silently counted proof_accepted —
            # no regret row, no dead letter; the wall was only discoverable by
            # reading certificate payloads). REJECTED / PRE_SUBMIT_ERROR carry
            # venue_call_started=False semantics (no live order), so they route
            # through the regret ledger with the executor's reason.
            # TIMEOUT_UNKNOWN / POST_SUBMIT_UNKNOWN stay proof_accepted — a
            # venue order may exist and the reconcile sweep owns those.
            if receipt.side_effect_status in {"REJECTED", "PRE_SUBMIT_ERROR"}:
                reason = receipt.reason or receipt.side_effect_status
                return self._reject_or_retry_post_submit(
                    event,
                    "EXECUTION_RECEIPT",
                    reason,
                    result,
                    receipt=receipt,
                    decision_time=decision_time,
                )
                return None
        result.proof_accepted += 1

    def _assert_no_submit_lane_invariant(self, receipt: EventSubmissionReceipt) -> None:
        """Refuse to persist a full-pass NO_SUBMIT receipt stamped LIVE on an armed
        live daemon (silent-trade-kill antibody 2026-06-12).

        Backward compatible: legacy receipts carry submit_lane=None and pass through;
        only a NEW write that is simultaneously (a) on a nominally-armed live daemon,
        (b) proof_accepted, (c) NO_SUBMIT, and (d) stamped LIVE trips the invariant —
        the impossible combination that, in the incident, silently booked $16 Kelly
        full-pass candidates as accepted no-submits with zero signal the live lane was
        dark.
        """
        nominally_armed = (
            self._config.reactor_mode == "live"
            and bool(self._config.edli_live_operator_authorized)
        )
        if not nominally_armed:
            return
        if (
            receipt.proof_accepted is True
            and receipt.side_effect_status == "NO_SUBMIT"
            and receipt.submit_lane == "LIVE"
        ):
            raise LiveLaneDarkInvariantError(
                "LIVE_LANE_DARK_FULL_PASS_NO_SUBMIT: a proof_accepted NO_SUBMIT receipt "
                f"stamped submit_lane=LIVE reached the persist boundary on an armed live "
                f"daemon (reactor_mode=live, operator_authorized=True). event_id="
                f"{receipt.event_id} final_intent_id={receipt.final_intent_id} reason="
                f"{receipt.reason!r}. The live lane never produces a full-pass NO_SUBMIT — "
                "this is a silently-consumed tradeable entry."
            )

    def _reject_or_retry_post_submit(
        self,
        event: OpportunityEvent,
        stage: str,
        reason: str,
        result: ReactorResult,
        *,
        receipt: EventSubmissionReceipt | None,
        decision_time: datetime,
    ) -> str | None:
        # ALWAYS-DECIDABLE invariant — Build 2 (operator law 2026-06-12): a family blocked because
        # its replacement posterior is STALE or ABSENT (the adapter raises
        # REPLACEMENT_0_1_LIVE_READINESS_MISSING / ..._BUNDLE_BLOCKED) is a REFRESHABLE
        # SUBSTRATE block — the belief substrate needs re-materialization, not an endless requeue
        # against an unchanging posterior. Record it for the end-of-cycle drain, which enqueues a
        # single-family cycle-advance reseed (outside any txn, debounced, fail-soft). The
        # disposition below is unchanged: a transient reason still requeues, a terminal one still
        # rejects — the enqueue is an ADDITIONAL same-handling action, never a behavior change.
        if _is_posterior_staleness_reason(reason):
            self._record_substrate_block(event, kind="posterior")
        if _is_transient_money_path_reason(reason):
            if (
                _event_deadline_horizon(
                    event,
                    decision_time=decision_time,
                    transient_reason=reason,
                )
                is None
            ):
                if _is_day0_hourly_refresh_reason(reason):
                    self._record_substrate_block(event, kind="day0_hourly")
                elif _is_executable_snapshot_refresh_reason(reason):
                    self._record_substrate_block(event, kind="snapshot")
            # Transient: the forecast source was re-ingested after this cycle's
            # decision moment, or the selected executable price expired between
            # the pre-submit family identity gate and the adapter's JIT scoring.
            # Requeue for the next cycle instead of terminally consuming the
            # opportunity (horizon-bounded — no attempt cap; see
            # _transient_horizon_terminal).
            self._transient_requeue_reasons[event.event_id] = str(reason)
            return _EXECUTABLE_SNAPSHOT_RETRY
        self._persist_terminal_no_submit_receipt(
            receipt,
            decision_time=decision_time,
        )
        self._reject_event(event, stage, reason, result, receipt=receipt, decision_time=decision_time)
        return None

    def _persist_terminal_no_submit_receipt(
        self,
        receipt: EventSubmissionReceipt | None,
        *,
        decision_time: datetime,
    ) -> None:
        """Materialize terminal no-submit decisions before rejection bookkeeping.

        ``decision_certificates`` still distinguish VERIFIED no-submit certificates from
        rejected terminal decisions. The receipt ledger is the per-event decision trace that
        live observability, attribution, and redecision audits query; terminal economic
        no-submits must not disappear just because the money-path blocker later writes a
        compile failure/regret row. Transient blockers are filtered by the caller and remain
        pending with no terminal receipt.
        """
        if receipt is None:
            return
        if receipt.side_effect_status != "NO_SUBMIT" or receipt.proof_accepted is not True:
            return
        self._no_submit_receipt_ledger.insert_idempotent(
            receipt,
            decision_time=decision_time,
        )

    def _reject_event(
        self,
        event: OpportunityEvent,
        stage: str,
        reason: str,
        result: ReactorResult,
        *,
        receipt: EventSubmissionReceipt | None = None,
        decision_time: datetime | None = None,
    ) -> None:
        self._reject(event, stage, reason)
        if decision_time is not None:
            self._write_compile_failure(event, stage, reason, decision_time=decision_time, receipt=receipt)
        self._write_regret(event, stage, reason, receipt=receipt, decision_time=decision_time)
        result.rejected += 1
        result.rejection_reasons.append(reason)

    def _write_compile_failure(
        self,
        event: OpportunityEvent,
        stage: str,
        reason: str,
        *,
        decision_time: datetime,
        receipt: EventSubmissionReceipt | None = None,
    ) -> None:
        from src.decision_kernel.ledger import CompileFailure

        parent_hashes = ()
        if receipt is not None and receipt.final_intent_id:
            parent_hashes = (receipt.final_intent_id,)
        self._decision_certificate_ledger.persist_failures(
            (
                CompileFailure(
                    event_id=event.event_id,
                    decision_time=decision_time.astimezone(UTC),
                    mode="NO_SUBMIT",
                    claim_type="no_submit_dry_run_decision",
                    stage=stage,
                    reason_code=reason,
                    parent_hashes=parent_hashes,
                ),
            )
        )

    def _write_regret(
        self,
        event: OpportunityEvent,
        stage: str,
        reason: str,
        *,
        receipt: EventSubmissionReceipt | None = None,
        decision_time: datetime | None = None,
    ) -> None:
        if self._regret_ledger is None:
            return
        from src.strategy.live_inference.no_trade_regret import NoTradeRegretEvent

        # K2.1 runtime sensor (consolidated overhaul 2026-06-11): every rejection
        # reason BASE must be a declared member of the typed registry. The AST CI
        # antibody covers literal emit sites; THIS warning covers dynamic paths —
        # above all the dead-letter lane where str(exc) becomes the reason (raw
        # exception text in rejection_reason is the disease the registry kills).
        # Warn once per base per process; never block the write (truth preserved).
        from src.contracts.rejection_reasons import (
            base_reason,
            is_registered_rejection_reason,
        )

        _base = base_reason(reason)
        if not is_registered_rejection_reason(_base) and _base not in _UNREGISTERED_REJECTION_BASES_WARNED:
            _UNREGISTERED_REJECTION_BASES_WARNED.add(_base)
            import logging as _logging

            _logging.getLogger("zeus.events.reactor").warning(
                "UNREGISTERED_REJECTION_REASON base=%r (full=%r stage=%r): not in "
                "src/contracts/rejection_reasons.py — register it with a category "
                "or fix the emit site (raw exception text is never a valid reason)",
                _base,
                str(reason)[:200],
                stage,
            )

        payload = _payload_dict(event)
        envelope_json = self._build_regret_envelope_json(
            event, stage, reason, receipt=receipt, decision_time=decision_time, payload=payload
        )
        reason_text = str(reason or "")
        family_level_all_rejected = reason_text.startswith(
            "EVENT_BOUND_ALL_CANDIDATES_REJECTED:"
        )
        family_level_qkernel_no_trade = reason_text.startswith("QKERNEL_SPINE_NO_TRADE:")
        family_level_no_trade = family_level_all_rejected or family_level_qkernel_no_trade
        condition_id = _receipt_or_payload(receipt, payload, "condition_id")
        token_id = _receipt_or_payload(receipt, payload, "token_id")
        outcome_label = _receipt_or_payload(receipt, payload, "outcome_label")
        bin_label = _receipt_or_payload(receipt, payload, "bin_label")
        direction = _receipt_or_payload(receipt, payload, "direction")
        q_live = _optional_float(_receipt_or_payload(receipt, payload, "q_live"))
        q_lcb_5pct = _optional_float(_receipt_or_payload(receipt, payload, "q_lcb_5pct"))
        c_fee_adjusted = _optional_float(
            _receipt_or_payload(receipt, payload, "c_fee_adjusted")
        )
        c_cost_95pct = _optional_float(
            _receipt_or_payload(receipt, payload, "c_cost_95pct")
        )
        p_fill_lcb = _optional_float(_receipt_or_payload(receipt, payload, "p_fill_lcb"))
        trade_score = _optional_float(_receipt_or_payload(receipt, payload, "trade_score"))
        native_quote_available = _optional_bool(
            _receipt_or_payload(receipt, payload, "native_quote_available")
        )
        executable_snapshot_id = _receipt_or_payload(
            receipt, payload, "executable_snapshot_id"
        )
        if receipt is not None:
            qkernel_economics = _qkernel_regret_economics(receipt)
            if qkernel_economics is not None:
                q_lcb_5pct = qkernel_economics["q_lcb_5pct"]
                c_fee_adjusted = qkernel_economics["c_fee_adjusted"]
                c_cost_95pct = qkernel_economics["c_cost_95pct"]
                trade_score = qkernel_economics["trade_score"]
        if family_level_no_trade:
            condition_id = None
            token_id = None
            outcome_label = None
            bin_label = None
            direction = None
            q_live = None
            q_lcb_5pct = None
            c_fee_adjusted = None
            c_cost_95pct = None
            p_fill_lcb = None
            trade_score = None
            native_quote_available = None
            executable_snapshot_id = None
        self._regret_ledger.insert_idempotent(
            NoTradeRegretEvent(
                event_id=event.event_id,
                rejection_stage=stage,  # type: ignore[arg-type]
                rejection_reason=reason,
                regret_bucket=_regret_bucket_for(reason),  # type: ignore[arg-type]
                envelope_json=envelope_json,
                market_slug=payload.get("market_slug"),
                condition_id=condition_id,
                token_id=token_id,
                outcome_label=outcome_label,
                decision_time=decision_time.astimezone(UTC).isoformat() if decision_time is not None else None,
                city=_receipt_or_payload(receipt, payload, "city"),
                target_date=_receipt_or_payload(receipt, payload, "target_date"),
                metric=_receipt_or_payload(receipt, payload, "metric"),
                observation_time=payload.get("observation_time"),
                decision_seq=_optional_int(payload.get("decision_seq")),
                family_id=_receipt_or_payload(receipt, payload, "family_id"),
                bin_label=bin_label,
                direction=direction,
                q_live=q_live,
                q_lcb_5pct=q_lcb_5pct,
                c_fee_adjusted=c_fee_adjusted,
                c_cost_95pct=c_cost_95pct,
                p_fill_lcb=p_fill_lcb,
                trade_score=trade_score,
                native_quote_available=native_quote_available,
                source_status=_receipt_or_payload(receipt, payload, "source_status"),
                family_complete=_optional_bool(_receipt_or_payload(receipt, payload, "family_complete")),
                hypothetical_order_type=payload.get("hypothetical_order_type"),
                hypothetical_fill_status=payload.get("hypothetical_fill_status"),
                hypothetical_fill_price=_optional_float(payload.get("hypothetical_fill_price")),
                causal_snapshot_id=event.causal_snapshot_id,
                executable_snapshot_id=executable_snapshot_id,
            )
        )
        _edli_note_day0_pause_rejection(event.event_type, reason_text)
        if family_level_no_trade:
            for candidate_row in _all_candidates_rejected_candidate_rows(receipt, family_reason=reason_text):
                candidate_reason = str(candidate_row["rejection_reason"])
                self._regret_ledger.insert_idempotent(
                    NoTradeRegretEvent(
                        event_id=event.event_id,
                        rejection_stage=stage,  # type: ignore[arg-type]
                        rejection_reason=candidate_reason,
                        regret_bucket=_regret_bucket_for(candidate_reason),  # type: ignore[arg-type]
                        envelope_json=envelope_json,
                        market_slug=payload.get("market_slug"),
                        condition_id=candidate_row.get("condition_id"),
                        token_id=candidate_row.get("token_id"),
                        outcome_label=candidate_row.get("outcome_label"),
                        decision_time=(
                            decision_time.astimezone(UTC).isoformat()
                            if decision_time is not None
                            else None
                        ),
                        city=_receipt_or_payload(receipt, payload, "city"),
                        target_date=_receipt_or_payload(receipt, payload, "target_date"),
                        metric=_receipt_or_payload(receipt, payload, "metric"),
                        observation_time=payload.get("observation_time"),
                        decision_seq=_optional_int(payload.get("decision_seq")),
                        family_id=candidate_row.get("family_id")
                        or _receipt_or_payload(receipt, payload, "family_id"),
                        bin_label=candidate_row.get("bin_label"),
                        direction=candidate_row.get("direction"),
                        q_live=candidate_row.get("q_live"),
                        q_lcb_5pct=candidate_row.get("q_lcb_5pct"),
                        c_fee_adjusted=candidate_row.get("c_fee_adjusted"),
                        c_cost_95pct=candidate_row.get("c_cost_95pct"),
                        p_fill_lcb=candidate_row.get("p_fill_lcb"),
                        trade_score=candidate_row.get("trade_score"),
                        native_quote_available=candidate_row.get("native_quote_available"),
                        source_status=_receipt_or_payload(receipt, payload, "source_status"),
                        family_complete=_optional_bool(
                            _receipt_or_payload(receipt, payload, "family_complete")
                        ),
                        hypothetical_order_type=payload.get("hypothetical_order_type"),
                        hypothetical_fill_status=payload.get("hypothetical_fill_status"),
                        hypothetical_fill_price=_optional_float(
                            payload.get("hypothetical_fill_price")
                        ),
                        causal_snapshot_id=event.causal_snapshot_id,
                        executable_snapshot_id=candidate_row.get("executable_snapshot_id"),
                    )
                )

    def _build_regret_envelope_json(
        self,
        event: OpportunityEvent,
        stage: str,
        reason: str,
        *,
        receipt: EventSubmissionReceipt | None,
        decision_time: datetime | None,
        payload: dict[str, Any],
    ) -> str | None:
        """Fail-soft DecisionProvenanceEnvelope JSON for a rejection (operator law 2026-06-11).

        NEVER raises and NEVER alters the decision — a build failure simply yields None and the
        rejection still records its full reason in the typed columns.

        PRIMARY path (production): the adapter's receipt-builder wrapper has ALREADY assembled the
        envelope materials at decision time (served bundle, per-input ages, anchor transport, the
        selected executable snapshot row — all bound IN the adapter where forecast/trade conns
        live) and attached them as receipt.envelope_json. Here we only MERGE the final rejection
        {stage, reason FULL TEXT} into those materials.

        FALLBACK path: receipts without an attached envelope (pre-receipt rejections, foreign
        receipt builders) get the minimal envelope built from what the reactor can reach.
        """
        try:
            from src.contracts.decision_provenance import (
                build_decision_provenance_envelope,
                envelope_to_json,
            )

            if receipt is not None and getattr(receipt, "envelope_json", None):
                try:
                    materials = json.loads(receipt.envelope_json)
                    if isinstance(materials, dict):
                        # FULL TEXT — storage never truncates (operator law).
                        materials["rejection"] = {"stage": stage, "reason": reason}
                        return json.dumps(materials, sort_keys=True, separators=(",", ":"), default=str)
                except (ValueError, TypeError):
                    pass  # unreadable materials -> rebuild minimally below

            bundle = None
            forecast_conn = None
            if self._decision_provenance_hook is not None:
                try:
                    hook_result = self._decision_provenance_hook(event, receipt, decision_time)
                    if hook_result is not None:
                        bundle, forecast_conn = hook_result
                except Exception:  # noqa: BLE001 — hook is best-effort, never fatal
                    bundle, forecast_conn = None, None

            snapshot_row = None
            snapshot_id = _receipt_or_payload(receipt, payload, "executable_snapshot_id")
            if snapshot_id:
                try:
                    # Use a cursor-local row_factory so the shared connection's
                    # row_factory is never mutated — eliminates the save/restore
                    # concurrency footgun (same class as the 2026-06-11 claim storm
                    # PRAGMA busy_timeout leak, src/events/reactor.py header §(d)).
                    _snap_cur = self._store.conn.cursor()
                    _snap_cur.row_factory = sqlite3.Row
                    _snap_cur.execute(
                        "SELECT snapshot_id, captured_at, orderbook_top_bid, orderbook_top_ask, "
                        "market_end_at, condition_id FROM executable_market_snapshots "
                        "WHERE snapshot_id = ?",
                        (str(snapshot_id),),
                    )
                    snapshot_row = _snap_cur.fetchone()
                except sqlite3.Error:
                    snapshot_row = None

            economics = {
                "q_live": _receipt_or_payload(receipt, payload, "q_live"),
                "q_lcb_5pct": _receipt_or_payload(receipt, payload, "q_lcb_5pct"),
                "c_fee_adjusted": _receipt_or_payload(receipt, payload, "c_fee_adjusted"),
                "trade_score": _receipt_or_payload(receipt, payload, "trade_score"),
                "kelly_size_usd": getattr(receipt, "kelly_size_usd", None) if receipt is not None else None,
            }
            envelope = build_decision_provenance_envelope(
                forecast_conn,
                self._store.conn,
                bundle=bundle,
                decision_time=decision_time if decision_time is not None else datetime.now(UTC),
                condition_id=_receipt_or_payload(receipt, payload, "condition_id"),
                token_id=_receipt_or_payload(receipt, payload, "token_id"),
                executable_snapshot_row=snapshot_row,
                economics=economics,
                direction=_receipt_or_payload(receipt, payload, "direction"),
                mainstream=None,
                rejection={"stage": stage, "reason": reason},
                # city/target_date from the event payload so time-to-settlement is populated even
                # for early-stage rejections (EVENT_FILTER / SOURCE_TRUTH) that have no bundle yet.
                city=_receipt_or_payload(receipt, payload, "city"),
                target_date=_receipt_or_payload(receipt, payload, "target_date"),
            )
            return envelope_to_json(envelope)
        except Exception:  # noqa: BLE001 — provenance is observability; never fail a rejection write
            return None

    def _log_family_once(self, event: OpportunityEvent) -> None:
        family_key = event.entity_key.rsplit("|", 1)[0]
        self._family_logged.add(family_key)

    def family_log_count(self) -> int:
        return len(self._family_logged)

def _execution_receipt_certificate_bundle(receipt: EventSubmissionReceipt) -> tuple[Any, ...]:
    bundle = receipt.decision_proof_bundle
    if not isinstance(bundle, tuple):
        return ()
    if not any(getattr(cert, "certificate_type", None) == claims.EXECUTION_RECEIPT for cert in bundle):
        return ()
    return bundle


def _payload_dict(event: OpportunityEvent) -> dict[str, Any]:
    try:
        parsed = json.loads(event.payload_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _forecast_event_family(
    event: OpportunityEvent,
) -> tuple[str, str, str] | None:
    payload = _payload_dict(event)
    family = (
        str(payload.get("city") or "").strip(),
        str(payload.get("target_date") or "").strip(),
        str(payload.get("metric") or "").strip(),
    )
    return family if all(family) else None


def _rank_forecast_wake_events(
    events: list[OpportunityEvent],
    family_order: list[tuple[str, str, str]],
) -> list[OpportunityEvent]:
    rank = {family: index for index, family in enumerate(family_order)}
    fallback = len(rank)
    return sorted(
        events,
        key=lambda event: (
            rank.get(_forecast_event_family(event), fallback),
            event.event_id,
        ),
    )


def _parse_utc_instant(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _deadline_from_reason(reason: object, field_name: str) -> datetime | None:
    text = str(reason or "")
    marker = f"{field_name}="
    start = text.find(marker)
    if start < 0:
        return None
    tail = text[start + len(marker) :]
    for delimiter in (":decision_time=", " ", ",", ";", "|"):
        split_at = tail.find(delimiter)
        if split_at > 0:
            tail = tail[:split_at]
            break
    return _parse_utc_instant(tail)


def _event_deadline_horizon(
    event: OpportunityEvent,
    *,
    decision_time: datetime,
    transient_reason: object | None = None,
) -> tuple[str, str] | None:
    decision_time_utc = decision_time.astimezone(UTC)
    expires_at = _parse_utc_instant(getattr(event, "expires_at", None))
    if expires_at is not None and expires_at <= decision_time_utc:
        return ("EVENT_EXPIRES_AT_PAST", f"expires_at={expires_at.isoformat()}")

    payload = _payload_dict(event)
    payload_selection_deadline = _parse_utc_instant(payload.get("selection_deadline"))
    if payload_selection_deadline is not None and payload_selection_deadline <= decision_time_utc:
        return (
            "SELECTION_DEADLINE_PAST",
            f"selection_deadline={payload_selection_deadline.isoformat()}",
        )

    reason_selection_deadline = _deadline_from_reason(transient_reason, "selection_deadline")
    reason_base = _money_path_reason_base(str(transient_reason or ""))
    if (
        reason_base != "EXECUTABLE_SNAPSHOT_STALE"
        and not _reason_wraps_executable_snapshot_selection_deadline(transient_reason)
        and reason_selection_deadline is not None
        and reason_selection_deadline <= decision_time_utc
    ):
        return (
            "SELECTION_DEADLINE_PAST",
            f"selection_deadline={reason_selection_deadline.isoformat()}",
        )
    return None


def _reason_wraps_executable_snapshot_selection_deadline(reason: object | None) -> bool:
    text = str(reason or "")
    return (
        "EXECUTABLE_SNAPSHOT_STALE" in text
        and "selection_deadline=" in text
    )


def _receipt_or_payload(
    receipt: EventSubmissionReceipt | None,
    payload: dict[str, Any],
    field_name: str,
) -> Any:
    if receipt is not None and hasattr(receipt, field_name):
        value = getattr(receipt, field_name)
        if value is not None:
            return value
    return payload.get(field_name)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes"}:
        return True
    if lowered in {"0", "false", "no"}:
        return False
    return None


def _all_candidates_rejected_candidate_rows(
    receipt: EventSubmissionReceipt | None,
    *,
    family_reason: str | None = None,
) -> list[dict[str, Any]]:
    if receipt is None or not isinstance(receipt.opportunity_book, dict):
        return []
    raw_candidates = receipt.opportunity_book.get("candidates")
    if not isinstance(raw_candidates, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            continue
        execution_price = _optional_float(raw.get("execution_price"))
        trade_score = _optional_float(raw.get("trade_score"))
        missing_reason = str(raw.get("missing_reason") or "").strip()
        candidate_id = str(raw.get("candidate_id") or "").strip()
        qkernel_economics = _candidate_qkernel_regret_economics(raw)
        qkernel_family_reason = str(family_reason or "").startswith("QKERNEL_SPINE_NO_TRADE:")
        if (
            execution_price is None
            or execution_price <= 0.0
            or not candidate_id
        ):
            continue
        if qkernel_family_reason and qkernel_economics is not None:
            candidate_missing_reason = (
                missing_reason
                if missing_reason.startswith("QKERNEL_")
                else str(family_reason or "").strip()
            )
            q_lcb_5pct = qkernel_economics["q_lcb_5pct"]
            c_fee_adjusted = qkernel_economics["c_fee_adjusted"]
            c_cost_95pct = qkernel_economics["c_cost_95pct"]
            candidate_trade_score = qkernel_economics["trade_score"]
            q_live = qkernel_economics["q_live"]
        else:
            if (
                trade_score is None
                or trade_score <= 0.0
                or not missing_reason
            ):
                continue
            candidate_missing_reason = missing_reason
            q_lcb_5pct = _optional_float(raw.get("q_lcb_5pct"))
            c_fee_adjusted = execution_price
            c_cost_95pct = _optional_float(raw.get("c_cost_95pct")) or execution_price
            candidate_trade_score = trade_score
            q_live = _optional_float(raw.get("q_posterior"))
        reason = (
            "EVENT_BOUND_CANDIDATE_REJECTED:"
            f"{candidate_missing_reason}:candidate_id={candidate_id}"
        )
        out.append(
            {
                "rejection_reason": reason,
                "family_id": raw.get("family_id"),
                "condition_id": raw.get("condition_id"),
                "token_id": raw.get("token_id"),
                "outcome_label": raw.get("outcome_label"),
                "bin_label": raw.get("bin_label"),
                "direction": raw.get("direction"),
                "q_live": q_live,
                "q_lcb_5pct": q_lcb_5pct,
                "c_fee_adjusted": c_fee_adjusted,
                "c_cost_95pct": c_cost_95pct,
                "p_fill_lcb": _optional_float(raw.get("p_fill_lcb")),
                "trade_score": candidate_trade_score,
                "native_quote_available": _optional_bool(raw.get("native_quote_available")),
                "executable_snapshot_id": receipt.executable_snapshot_id,
            }
        )
    return out


def _candidate_qkernel_regret_economics(raw: Mapping[str, Any]) -> dict[str, float] | None:
    cert = raw.get("qkernel_execution_economics")
    if not isinstance(cert, Mapping):
        return None
    try:
        payoff_q_point = float(cert["payoff_q_point"])
        payoff_q_lcb = float(cert["payoff_q_lcb"])
        cost = float(cert["cost"])
        edge_lcb = float(cert["edge_lcb"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (payoff_q_point, payoff_q_lcb, cost, edge_lcb)):
        return None
    if not (0.0 <= payoff_q_lcb <= payoff_q_point <= 1.0):
        return None
    return {
        "q_live": payoff_q_point,
        "q_lcb_5pct": payoff_q_lcb,
        "c_fee_adjusted": cost,
        "c_cost_95pct": cost,
        "trade_score": edge_lcb,
    }


def _qkernel_regret_economics(receipt: EventSubmissionReceipt) -> dict[str, float] | None:
    """Queryable no-trade columns for qkernel-selected receipts.

    ``q_live`` / ``q_lcb_5pct`` on the receipt are selected-side probability
    provenance. A qkernel-selected route is economically licensed by its guarded
    payoff-space certificate: ``payoff_q_point``, ``payoff_q_lcb``, ``cost`` and
    ``edge_lcb``. Project those values into the regret table's scalar economic columns
    so operators and continuous-redecision screens do not compare preserved proof
    probabilities against a qkernel route score.
    """

    cert = receipt.qkernel_execution_economics
    if not isinstance(cert, dict):
        return None
    try:
        payoff_q_point = float(cert["payoff_q_point"])
        payoff_q_lcb = float(cert["payoff_q_lcb"])
        cost = float(cert["cost"])
        edge_lcb = float(cert["edge_lcb"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (payoff_q_point, payoff_q_lcb, cost, edge_lcb)):
        return None
    if not (0.0 <= payoff_q_lcb <= payoff_q_point <= 1.0):
        return None
    return {
        "q_live": payoff_q_point,
        "q_lcb_5pct": payoff_q_lcb,
        "c_fee_adjusted": cost,
        "c_cost_95pct": cost,
        "trade_score": edge_lcb,
    }


def _submission_receipt(
    event: OpportunityEvent,
    submit_result: bool | None | EventSubmissionReceipt,
) -> EventSubmissionReceipt | None:
    if isinstance(submit_result, EventSubmissionReceipt):
        return submit_result
    if submit_result is False:
        return EventSubmissionReceipt(
            submitted=False,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            reason="NO_SUBMIT_PROOF_FALSE",
        )
    if submit_result is None:
        return EventSubmissionReceipt(
            submitted=False,
            proof_accepted=True,
            event_id=event.event_id,
            causal_snapshot_id=event.causal_snapshot_id,
            reason="legacy_injected_test_submit",
        )
    if submit_result is True:
        return None
    return None


def _receipt_matches_event(event: OpportunityEvent, receipt: EventSubmissionReceipt) -> bool:
    if receipt.event_id != event.event_id:
        return False
    if event.causal_snapshot_id and receipt.causal_snapshot_id != event.causal_snapshot_id:
        return False
    payload = _payload_dict(event)
    for field in ("city", "target_date", "metric"):
        expected = payload.get(field)
        observed = getattr(receipt, field)
        if expected and observed != expected:
            return False
    actuation = receipt.global_actuation
    candidate = getattr(getattr(actuation, "decision", None), "candidate", None)
    if actuation is not None:
        return bool(
            candidate is not None
            and str(getattr(actuation, "winner_event_id", "") or "")
            == event.event_id
            and receipt.condition_id
            == str(getattr(candidate, "condition_id", "") or "")
            and receipt.token_id == str(getattr(candidate, "token_id", "") or "")
            and receipt.family_id == str(getattr(candidate, "family_key", "") or "")
        )
    for field in ("condition_id", "token_id"):
        expected = payload.get(field)
        observed = getattr(receipt, field)
        if expected and observed != expected:
            return False
    executable_snapshot_id = payload.get("executable_snapshot_id")
    if executable_snapshot_id and receipt.executable_snapshot_id != executable_snapshot_id:
        return False
    return True


def _receipt_money_path_blocker(
    receipt: EventSubmissionReceipt,
    config: "ReactorConfig | None" = None,
) -> tuple[str | None, str]:
    if receipt.side_effect_status == "COMMAND_CREATED":
        return "EXECUTOR_EXPRESSIBILITY", receipt.reason or "EDLI_REAL_ORDER_SIDE_EFFECT_FORBIDDEN"
    if not receipt.trade_score_positive:
        return "TRADE_SCORE", receipt.reason or "TRADE_SCORE_BLOCKED"
    if not receipt.fdr_pass or not receipt.fdr_family_id or receipt.fdr_hypothesis_count <= 0:
        return "FDR", receipt.reason or "FDR_REJECTED"
    if receipt.kelly_execution_price_type != "ExecutionPrice" or receipt.kelly_price_fee_deducted is not True:
        return "KELLY", receipt.reason or "EDLI_KELLY_PROOF_MISSING"
    if not receipt.kelly_cost_basis_id:
        return "KELLY", receipt.reason or "EDLI_KELLY_COST_BASIS_MISSING"
    if not receipt.kelly_pass or receipt.kelly_size_usd <= 0.0:
        return "KELLY", receipt.reason or "KELLY_TOO_SMALL"
    if not receipt.final_intent_id:
        return "EXECUTOR_EXPRESSIBILITY", receipt.reason or "FINAL_INTENT_RECEIPT_MISSING"
    has_live_admission_inputs = any(
        value is not None
        for value in (receipt.q_live, receipt.q_lcb_5pct, receipt.c_fee_adjusted, receipt.trade_score, receipt.direction)
    )
    if has_live_admission_inputs:
        # C1/C2 redundant re-checks DELETED 2026-06-14 (gate-mass collapse Tier-C):
        # live_lcb_consistency + live_capital_efficiency are already enforced upstream
        # at candidate_evaluation.py (admitted requires both admissible) and the receipt
        # carries verbatim copies, so neither could ever fire for an admitted candidate.
        # The buy_no stanza below STAYS — its same_bin_yes_posterior /
        # settlement_coverage_status come from a distinct receipt-provenance path.
        proof_bundle = receipt.decision_proof_bundle
        # The adapter constructs this typed bundle only after the replacement
        # NO certificate has matched its forecast/candidate parents and qkernel
        # pre/post probability mapping. Reconstructing the proof again here
        # created a second authority plane and rejected a valid W3 monotone
        # tightening after the authoritative bundle had already passed.
        # Legacy/synthetic receipts without the typed bundle retain the
        # fail-closed receipt gate.
        if not (
            receipt.direction == "buy_no"
            and receipt.probability_authority == "replacement_0_1"
            and (
                isinstance(proof_bundle, NoSubmitProofBundle)
                or bool(_execution_receipt_certificate_bundle(receipt))
            )
        ):
            replacement_expected = replacement_no_bound_expected_from_parents(
                getattr(getattr(proof_bundle, "forecast_authority", None), "payload", None),
                getattr(getattr(proof_bundle, "candidate_evidence", None), "payload", None),
            )
            buy_no_conservative_reason = live_buy_no_conservative_evidence_rejection_reason(
                direction=receipt.direction,
                q_direction=receipt.q_live,
                q_lcb=receipt.q_lcb_5pct,
                execution_price=receipt.c_fee_adjusted,
                q_lcb_calibration_source=receipt.q_lcb_calibration_source,
                # The same independently-materialized YES-bin posterior the ADAPTER gate
                # evaluated against (proof.same_bin_yes_posterior). Carrying it on the
                # receipt closes the proof->receipt input-loss that rejected every buy_no
                # with ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING. NEVER a
                # 1-price / 1-q_no complement — the field is the q-vector YES mass.
                same_bin_yes_posterior=receipt.same_bin_yes_posterior,
                # Twin-authority reconciliation #7: the SAME family coverage verdict the
                # adapter gate evaluated (carried on the receipt; single computation).
                settlement_coverage_status=receipt.settlement_coverage_status,
                replacement_no_bound_certificate=receipt.replacement_no_bound_certificate,
                replacement_no_bound_expected=replacement_expected,
                qkernel_execution_economics=receipt.qkernel_execution_economics,
                probability_authority=receipt.probability_authority,
                posterior_id=receipt.posterior_id,
                condition_id=receipt.condition_id,
            )
            if buy_no_conservative_reason is not None:
                return "TRADE_SCORE", buy_no_conservative_reason
    if receipt.proof_accepted is False:
        return "EXECUTOR_EXPRESSIBILITY", receipt.reason or "NO_SUBMIT_PROOF_FALSE"
    # Task #102 — optional book-wide edge-zone admission. The always-on live
    # DELETED 2026-06-12 (operator no-caps law; gate inventory D3): the
    # edge_zone_admission "extra tightening" was a second, knob-configurable
    # EV bar over the always-on capital-efficiency check — a cap-style
    # throttle. Flag was OFF in live config since introduction.
    return None, ""


# ---------------------------------------------------------------------------
# Money-path transient classifier — EXPLICIT reactor-owned table (operator law
# 2026-06-12; replaces the string-contains classifier that rotted silently when
# a reason was renamed). The contract for full typed-enum-at-emission is a named
# follow-up (the adapter's emission sites are owned by another file); here the
# reactor owns an exhaustive, enumerable table and fails OPEN to TRANSIENT on an
# unknown reason with a LOUD log — a misspelled/renamed reason must NEVER
# silently terminal-burn a live-positive-EV event; the loud log is the antibody
# that gets the table updated.
# ---------------------------------------------------------------------------

# A reason whose BASE (text before the first ':') is in this set is TRANSIENT
# (stale-decision-vs-fresh-book races and source re-ingestion races). The retry
# re-runs the full gate chain and re-prices from scratch — it NEVER resubmits the
# same envelope (no-verbatim-retry venue rule untouched), and in every case the
# venue order was never placed (PRICE_MOVED aborts pre-POST, MODE_FLIPPED refuses
# the stale plan, would_cross_book fails the pre-submit revalidation certificate).
TRANSIENT_MONEY_PATH_REASONS: frozenset[str] = frozenset({
    # Forecast-source re-ingested AFTER this cycle's decision moment.
    "SOURCE_CAPTURED_AFTER_DECISION_TIME",
    # Replacement posterior substrate is missing/stale relative to live inputs.
    # These can appear bare or nested under LIVE_INFERENCE_INPUTS_MISSING. The
    # cure is a same-family posterior cycle-advance plus redecision, not terminal
    # burn of the opportunity event.
    "REPLACEMENT_0_1_LIVE_READINESS_MISSING",
    "REPLACEMENT_0_1_LIVE_BUNDLE_BLOCKED",
    "REPLACEMENT_0_1_LIVE_INPUT_LAG",
    "REPLACEMENT_LIVE_INPUT_LAG",
    # Read-boundary compatibility for queued/durable pre-cutover reasons.
    "REPLACEMENT_0_1_LIVE_AUTHORITY_READINESS_MISSING",
    "REPLACEMENT_0_1_LIVE_AUTHORITY_BUNDLE_BLOCKED",
    # Executable family snapshot not captured yet / went stale this cycle.
    "EXECUTABLE_SNAPSHOT_BLOCKED",
    "EXECUTABLE_SNAPSHOT_STALE",
    # Day0 remaining-day q needs persisted high-resolution hourly weather
    # vectors. Missing vectors are a refreshable weather-substrate fault, not a
    # no-edge conclusion and not a CLOB executable-snapshot fault.
    "DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE",
    # Taker race: JIT recapture found all-in cost above max + bounded tolerance
    # (Miami 16:22:35Z — EV at the NEW price still strongly positive).
    "SUBMIT_ABORTED_PRICE_MOVED",
    # Maker/taker mode race: proof priced a MAKER rest into an empty own-ask; by
    # submit the book grew a live ask (fresh_mode=TAKER) and P0-1 refused the
    # stale-mode plan (17:23:33Z four cities, fresh ask carried +6..+19% EV).
    "SUBMIT_ABORTED_MODE_FLIPPED",
    # SUBMIT-LANE DEGRADE (silent-trade-kill antibody 2026-06-12;
    # /tmp/allpass_nosubmit_rootcause.md). A FULL-PASS candidate decided on the
    # no-submit (degrade) adapter while the live lane was dark this cycle
    # (live_submit_effective False / operator_arm None during a crash-loop). The
    # live-lane-dark condition is intrinsically TRANSIENT — it clears when the
    # allocator/portfolio/operator arm recovers. Requeue the candidate (re-decide
    # on the next cycle's substrate, on the live lane if it is back up) rather than
    # terminally consuming a tradeable $16-Kelly full-pass entry as an "accepted"
    # no-submit. This is the strongest form of the fix: the silent-kill category is
    # impossible because the entry is never consumed while the lane is degraded.
    # Honest no-edge declines on the same adapter keep their SPECIFIC reason
    # (FDR_REJECTED / TRADE_SCORE_NON_POSITIVE / ...) and stay terminal — only the
    # full-pass-default rewrite carries this base.
    "NO_SUBMIT_ADAPTER_LANE",
    # FINDING-A (external review 2026-06-12): the FINAL taker size was being swept
    # from the elected DB snapshot's DEPTH while the limit price was the FRESH
    # submit-time witness price (which carries no depth). When the elected snapshot's
    # top-of-book no longer agrees with the witnessed touch, the depth that would be
    # swept is UNWITNESSED (stale-size vs fresh-price twin authority). The adapter
    # fails CLOSED with this base rather than sizing from the stale depth; the book
    # divergence is intrinsically TRANSIENT (a fresh snapshot capture re-establishes
    # matching price+depth authority), so the candidate requeues with a refresh —
    # mirroring EXECUTABLE_SNAPSHOT_STALE's shape.
    "LIVE_DEPTH_AUTHORITY_MISSING",
    # FINDING-B (external review 2026-06-12): under an injected bankroll provider the
    # free-cash one-time bound could not be resolved and was silently set to None (no
    # clamp). When a live free-cash AUTHORITY is wired (free_cash_usd_provider) but
    # returns None, the adapter fails CLOSED with this base rather than sizing unclamped.
    # The wallet free-cash record is a cycle-warmed cached read, so a cold/absent read is
    # TRANSIENT (the next warm cycle repopulates it) — requeue, never size unclamped.
    "BANKROLL_FREE_CASH_MISSING",
    # FINDING-C (external review 2026-06-12): the settlement-coverage q_lcb shrinker is
    # a live SAFETY gate (it can only LOWER an unlicensed bound). "No historical data" is
    # a typed INSUFFICIENT_DATA verdict (keeps the lcb, fine); a STRUCTURAL exception in
    # the coverage authority previously failed OPEN (kept the UNSHRUNK bound). With the
    # coverage gate ON it now fails CLOSED with this base. The fault is TRANSIENT (a
    # coverage-table/DB read that threw re-runs clean next cycle) — requeue, never size
    # on the unlicensed bound.
    "QLCB_COVERAGE_AUTHORITY_FAULT",
    # Pre-venue SQLite writer contention in executor persistence. The venue POST
    # boundary has not been crossed, so no order can exist; re-run the full event
    # decision on the next cycle instead of terminally burning a valuable intent.
    "pre_submit_db_locked_transient",
    # Synchronous Polymarket 400 submit rejection with no venue order id. The
    # submit crossed the HTTP boundary but the venue rejected before creating an
    # order. This is a stale maker-price/mode race; release the command/cap and
    # re-decide from a fresh book instead of treating it as unknown string drift.
    "venue_rejected_400",
    # Operator/manual entry pause is a pre-venue control state. No new order is
    # posted while active; once cleared, the event must be re-decided from fresh
    # belief/price evidence rather than burned as terminal or logged as an
    # unknown fail-open reason.
    "entries_paused",
    # Live-entry authority is a fail-closed runtime health surface. A missing or
    # stale authority surface blocks new entries but can clear when the sidecar /
    # daemon / deployment head realigns; retry later, never burn the event.
    "live_health_entry_authority",
    # Continuous redecision coordination: another live action already owns this
    # family, or the old leg is still exiting. The cure is state advancement from
    # that existing action, not burning the family forever.
    "SHIFT_BIN_CONCURRENT_FAMILY_LEASE",
    "FILL_UP_CONCURRENT_FAMILY_LEASE",
    "SHIFT_BIN_EXIT_OLD_LEG_PENDING",
    # The complete auction selected a current family whose event was outside the
    # bounded claimed page. The reactor materializes that family as the next legal
    # claim; the current claimed page must remain pending until that claim runs.
    "GLOBAL_WINNER_AWAITS_CLAIM",
    "GLOBAL_REAUCTION_WINNER_AWAITS_CLAIM",
    # The complete current-epoch auction either found no positive BUY/SELL action
    # (CASH/HOLD wins) or could not finish because a refreshable current-input
    # authority changed underneath it.  Both require a fresh full-auction pass;
    # neither may fall through the UNKNOWN fail-open classifier.
    "GLOBAL_AUCTION_NO_TRADE",
    "GLOBAL_AUCTION_FAILED",
    "GLOBAL_AUCTION_SUPERSEDED_BY_NEW_FACT",
    # Preflight exhausted without proving the exact complete-auction CASH/HOLD
    # terminal below.  Preserve the event until the missing/changed authority is
    # rebuilt and a complete decision exists.
    "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED",
    # A family excluded from the current complete auction because its current
    # probability/source bundle is not yet admissible must be reconsidered
    # after that substrate advances.  The wrapper is itself the stable reactor
    # reason; inner diagnostics are intentionally more specific and variable.
    "GLOBAL_FAMILY_INELIGIBLE",
})

# A reason whose BASE is in this set is TERMINAL (a genuine, non-race rejection)
# and must NOT requeue. Kept EXPLICIT and EXHAUSTIVE so the classifier never
# fail-opens a KNOWN terminal into a requeue: every reason base that reaches
# _reject_or_retry_post_submit today (the money-path blocker bases from
# _receipt_money_path_blocker, the live-admission rejection bases, the no-submit
# / execution-receipt certificate codes) is enumerated here. A base in NEITHER
# table is genuinely novel and triggers the fail-open loud-log path — so a
# RENAMED TRANSIENT race never silently terminal-burns, while a known terminal
# stays terminal even though the default is fail-open.
# Runtime-only terminal reason bases that are NOT registered RejectionReason
# enum members but DO reach this classifier (reactor/admission/decision-kernel
# internal reasons synthesized at the call site). Enumerated explicitly; unioned
# below with the registry-derived terminals so the terminal set is complete.
_RUNTIME_TERMINAL_MONEY_PATH_REASONS: frozenset[str] = frozenset({
    # --- _receipt_money_path_blocker terminal bases (reactor.py) ---
    "EDLI_REAL_ORDER_SIDE_EFFECT_FORBIDDEN",
    "EDLI_REAL_ORDER_SUBMIT_DISABLED",
    # Duplicate active live order suppression is a correct final disposition for
    # this event: the existing live order owns the family until it fills/cancels.
    # Requeueing the same event only clogs the redecision lane.
    "EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED",
    "TRADE_SCORE_BLOCKED",
    "EDLI_KELLY_PROOF_MISSING",
    "EDLI_KELLY_COST_BASIS_MISSING",
    "KELLY_TOO_SMALL",
    "FINAL_INTENT_RECEIPT_MISSING",
    # --- live-admission rejection bases (src/strategy/live_inference/live_admission.py) ---
    "ADMISSION_WIN_RATE_FLOOR",
    "ADMISSION_LCB_CONSISTENCY",
    "ADMISSION_CAPITAL_EFFICIENCY",
    "ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE",
    # --- no-submit / execution-receipt certificate codes (src/decision_kernel) ---
    "NO_SUBMIT_PROOF_BUNDLE_REQUIRED",
    "EXECUTION_RECEIPT_CERTIFICATE_REQUIRED",
    "EVENT_PERSISTED_AFTER_DECISION_TIME",
    "REPLAY_COUNTERFACTUAL_NOT_PROMOTABLE_TO_NO_SUBMIT",
    # Execution-receipt FAILED-WITHOUT-SIDE-EFFECT terminals (routed via
    # receipt.reason or the bare status when reason is empty).
    "REJECTED",
    "PRE_SUBMIT_ERROR",
    # Pre-venue collateral/allowance failed before a submit can be attempted.
    # No order exists, and replaying the same event cannot cure wallet allowance;
    # capital/allowance recovery should surface as fresh candidates/redecision.
    "pre_submit_collateral_reservation_failed",
    # Polymarket rejected the signed order as a deterministic Safe-signature 400.
    # The adapter already retries once after signer-bound L2 credential refresh.
    # Requeueing the same event repeats the same invalid signed request and then
    # usually degrades into an idempotency collision; fresh price/belief movement
    # must arrive as a new event.
    "venue_auth_invalid_signature_400",
    # Existing command ownership for this idempotency key is a final disposition
    # for this event. ACKED/FILLED/PARTIAL commands are already projected through
    # lifecycle recovery; REJECTED/CANCELLED/EXPIRED commands should be replaced
    # only by a fresh redecision event with a fresh executable identity.
    "idempotency_collision",
    # Receipt missing or not bound to this event (submit returned True / a
    # non-matching receipt): a structural expressibility failure, not a race.
    "EVENT_SUBMISSION_RECEIPT_MISSING_OR_UNBOUND",
    # --- q-kernel single-spine no-trade (src/engine/qkernel_spine_bridge.py +
    #     src/engine/event_reactor_adapter.py decision seam) ---
    # The spine emits one wrapped reason base, "QKERNEL_SPINE_NO_TRADE:<inner>",
    # for every no-trade it returns (inner ∈ the bridge/engine vocabulary:
    # SPINE_INPUTS_UNAVAILABLE, SPINE_NO_SELECTION, NO_POSITIVE_EDGE_CANDIDATE,
    # NO_EXECUTABLE_ROUTE_CANDIDATE, MARKET_INCOHERENT_BLOCK_LIVE,
    # PREDICTIVE_DISTRIBUTION_NOT_LIVE_ELIGIBLE, NO_DIRECTION_LAW_CANDIDATE,
    # NO_TRADE_ROUTE_NOT_DIRECTLY_EXECUTABLE, QKERNEL_LEAD_BUCKET_NOT_REPLAYED,
    # SPINE_WIRING_FAULT). EVERY such no-trade is TERMINAL
    # for THIS event, exactly like the legacy honest-no-edge declines (FDR_REJECTED,
    # TRADE_SCORE_NON_POSITIVE): the spine re-prices the whole family from a FRESH
    # book on the NEXT forecast snapshot, which arrives as a NEW event — so the
    # recovery path is a fresh event, NOT a requeue of this one. Requeueing instead
    # would double-churn the same event every cycle against an unchanged decision
    # substrate (the historic day0 forecast-spine requeue storm, monitor b9w56vec6).
    # Genuine intra-cycle execution races (PRICE_MOVED / MODE_FLIPPED) are classified
    # later at the SUBMIT stage under their own transient bases and are unaffected.
    "QKERNEL_SPINE_NO_TRADE",
    # Actual-submit qkernel quality is evaluated after live sizing/recapture/venue
    # constraints. A below-floor result is a terminal no-trade verdict for this
    # event; fresh market/belief movement must arrive as a new event, not a
    # requeue of the same failed economics.
    "QKERNEL_ACTUAL_SUBMIT_QUALITY_FLOOR",
    # Structural event-type contract violations are terminal for the event. They
    # cannot become valuable by re-running the same payload and must not clog
    # continuous redecision.
    "unsupported live candidate event type",
    "unsupported EDLI event type for inference",
    # Continuous redecision evaluated successfully but found no action worth
    # submitting for this event. Future price/belief movement arrives as a new
    # redecision event; requeueing this one only clogs the lane.
    "FILL_UP_NO_SUBMIT",
    "SHIFT_BIN_NO_SUBMIT",
    # Queue carriers do not enlarge the family feasible set. Once an earlier
    # claimed carrier owns the family in this epoch, a duplicate carrier is
    # terminal for this event and must not requeue into an ambiguity loop.
    "GLOBAL_DUPLICATE_FAMILY_CARRIER",
    # The complete current-epoch auction compared this event's action against
    # the winning action and chose the latter.  Requeueing the same carrier
    # cannot add evidence or enlarge the feasible set; fresh evidence/price
    # movement arrives through a fresh event and a new auction epoch.
    "GLOBAL_NOT_SELECTED",
    # A complete q/book/wealth auction retained every BUY/SELL action and proved
    # the whole executable set non-positive. CASH/HOLD is the decision for this
    # epoch; recurring producers create the next event, so requeueing this one
    # only duplicates a completed comparison.
    "GLOBAL_PREFLIGHT_HOLD_CASH_OPTIMAL",
})


def _registry_terminal_money_path_reasons() -> frozenset[str]:
    """Terminal reason bases derived from the CANONICAL registry (single authority:
    src.contracts.rejection_reasons.RejectionReason). A reason base is TERMINAL iff
    it is a registered reason that is NOT in the transient set and NOT the
    certificate-build family (which is sub-classified). Deriving the terminal set
    from the registry — instead of hand-listing it — makes it complete-by-
    construction: a newly-added RejectionReason is automatically terminal, so the
    fail-open default never silently flips a registered terminal into a requeue.
    Only genuinely UNREGISTERED, never-seen reasons fall to the fail-open path.

    Fail-soft: if the registry import is unavailable (it imports only stdlib, so
    this should never fail), the runtime terminal set alone still governs.
    """
    try:
        from src.contracts.rejection_reasons import RejectionReason
    except Exception:
        return frozenset()
    bases = {member.value for member in RejectionReason}
    return frozenset(
        bases
        - TRANSIENT_MONEY_PATH_REASONS
        - {"EDLI_LIVE_CERTIFICATE_BUILD_FAILED"}
    )


# The exhaustive terminal table: registry-derived terminals (complete by
# construction) ∪ the runtime-only terminal bases above. A reason base in
# NEITHER this set nor TRANSIENT_MONEY_PATH_REASONS is genuinely novel and
# triggers the fail-open loud-log path — so a RENAMED TRANSIENT race never
# silently terminal-burns, while every KNOWN terminal stays terminal.
TERMINAL_MONEY_PATH_REASONS: frozenset[str] = (
    _registry_terminal_money_path_reasons() | _RUNTIME_TERMINAL_MONEY_PATH_REASONS
)


def _money_path_reason_base(reason: str) -> str:
    """The classifier key for a reason: text before the first ':' (the qualified
    reason carries a human suffix, e.g. 'SUBMIT_ABORTED_PRICE_MOVED: recaptured
    all-in cost ...'). Whitespace-stripped; never lexicographically compared."""
    return reason.split(":", 1)[0].strip()


def _certificate_build_failed_is_transient(reason: str) -> bool:
    """EDLI_LIVE_CERTIFICATE_BUILD_FAILED is a reason FAMILY: the would_cross_book
    sub-reason (maker flavor of the price race) and DB-lock blips are TRANSIENT;
    every OTHER certificate build failure stays TERMINAL. This sub-discrimination
    is intrinsic to the reason (the qualifier lives in the suffix), so it cannot
    be a flat base-set entry — it is an explicit, named sub-classifier."""
    suffix_lower = reason.lower()
    return (
        "would_cross_book" in suffix_lower
        or _certificate_build_failed_is_book_authority_gap(reason)
        or _certificate_build_failed_is_maker_book_witness_race(reason)
        or _certificate_build_failed_is_taker_reservation_race(reason)
        or "database is locked" in suffix_lower
        or "database table is locked" in suffix_lower
        or "database is busy" in suffix_lower
    )


def _certificate_build_failed_is_book_authority_gap(reason: str) -> bool:
    suffix_lower = reason.lower()
    return (
        "pre_submit_book_authority_missing" in suffix_lower
        or "pre_submit_book_authority_stale" in suffix_lower
        or "pre_submit_book_authority_jit_" in suffix_lower
    )


def _certificate_build_failed_is_maker_book_witness_race(reason: str) -> bool:
    suffix_lower = reason.lower()
    return "maker_book_fresh_witness_disagreement" in suffix_lower


def _certificate_build_failed_is_taker_reservation_race(reason: str) -> bool:
    suffix_lower = reason.lower()
    return (
        "taker_buy_touch_exceeds_reservation" in suffix_lower
        or "taker_sell_touch_below_reservation" in suffix_lower
    )


def _is_day0_hourly_refresh_reason(reason: str | None) -> bool:
    if not reason:
        return False
    segments = [seg.strip() for seg in str(reason).split(":")]
    return "DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE" in segments


def _is_runtime_authority_retry_reason(reason: str | None) -> bool:
    if not reason:
        return False
    segments = [seg.strip() for seg in str(reason).split(":")]
    return any(
        seg
        in {
            "entries_paused",
            "live_health_entry_authority",
            "CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS",
        }
        for seg in segments
    )


def _is_current_wealth_retry_reason(reason: str | None) -> bool:
    if not reason:
        return False
    return "CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS" in {
        seg.strip() for seg in str(reason).split(":")
    }


def _is_executable_snapshot_refresh_reason(reason: str | None) -> bool:
    """True when a transient money-path reason is cured by fresh book/substrate capture."""

    if not reason:
        return False
    if _is_day0_hourly_refresh_reason(reason):
        return False
    segments = [seg.strip() for seg in str(reason).split(":")]
    if any(
        seg
        in {
            "EXECUTABLE_SNAPSHOT_BLOCKED",
            "EXECUTABLE_SNAPSHOT_STALE",
            "SUBMIT_ABORTED_PRICE_MOVED",
            "SUBMIT_ABORTED_MODE_FLIPPED",
            "LIVE_DEPTH_AUTHORITY_MISSING",
        }
        for seg in segments
    ):
        return True
    base = _money_path_reason_base(str(reason))
    if base == "EDLI_LIVE_CERTIFICATE_BUILD_FAILED":
        suffix_lower = str(reason).lower()
        return (
            "would_cross_book" in suffix_lower
            or _certificate_build_failed_is_book_authority_gap(str(reason))
            or _certificate_build_failed_is_maker_book_witness_race(str(reason))
            or _certificate_build_failed_is_taker_reservation_race(str(reason))
        )
    return False


def _is_transient_money_path_reason(reason: str | None) -> bool:
    """Classify a money-path rejection reason as TRANSIENT (requeue) vs TERMINAL
    (consume), via an EXPLICIT reactor-owned table — never substring soup.

    Decision order:
      1. Empty/None              -> TERMINAL (no reason to requeue).
      2. ANY ':'-delimited segment in TRANSIENT_MONEY_PATH_REASONS -> TRANSIENT.
         A reason can be a CHAIN where a transient cause is nested in a terminal-
         looking wrapper, e.g.
         "LIVE_INFERENCE_INPUTS_MISSING:...:SOURCE_CAPTURED_AFTER_DECISION_TIME":
         a transient cause ANYWHERE in the chain means "re-decide on a fresh
         substrate" and wins. This is an EXPLICIT segment membership check, not a
         substring scan — each segment is matched against the closed transient set.
      3. EDLI_LIVE_CERTIFICATE_BUILD_FAILED:* -> named sub-classifier
         (would_cross_book / db-lock = TRANSIENT; else TERMINAL).
      4. base in TERMINAL_MONEY_PATH_REASONS  -> TERMINAL.
      5. UNKNOWN base -> LOUD log + default TRANSIENT (fail-open to requeue).
         A renamed/misspelled reason must never silently terminal-burn a
         live-positive-EV event; the loud log is the antibody that gets the
         table fixed. The event still terminalizes correctly later via an
         EVENT-HORIZON terminal (timeliness floor / operator disarm), so
         fail-open does not leak — it just refuses to BURN on a string typo.
    """
    if not reason:
        return False
    # (2) Any nested transient segment wins (explicit segment membership).
    segments = [seg.strip() for seg in reason.split(":")]
    if any(seg in TRANSIENT_MONEY_PATH_REASONS for seg in segments):
        return True
    base = _money_path_reason_base(reason)
    if base == "EDLI_LIVE_CERTIFICATE_BUILD_FAILED":
        return _certificate_build_failed_is_transient(reason)
    if base in TERMINAL_MONEY_PATH_REASONS:
        return False
    # UNKNOWN reason base — fail open to TRANSIENT, loudly. Dedup per-base
    # per-process (reuse the unregistered-base warn set so a flood of one renamed
    # reason logs once, not every cycle).
    if base not in _UNREGISTERED_REJECTION_BASES_WARNED:
        _UNREGISTERED_REJECTION_BASES_WARNED.add(base)
        import logging as _logging

        _logging.getLogger("zeus.events.reactor").error(
            "reactor: UNKNOWN money-path reason base %r not in the transient/terminal "
            "table — defaulting TRANSIENT (fail-open requeue). Add it to "
            "TRANSIENT_MONEY_PATH_REASONS or TERMINAL_MONEY_PATH_REASONS. Full reason: %s",
            base,
            reason,
        )
    return True


# ALWAYS-DECIDABLE invariant — Build 2 (operator law 2026-06-12). Reason BASES that mean "this
# family's replacement-posterior BELIEF SUBSTRATE is stale or absent" — a REFRESHABLE block whose
# cure is re-materializing the posterior onto a fresher model cycle (the cycle-advance reseed
# lane), NOT requeueing against the same unchanging posterior. Explicit closed set (segment
# membership, never substring soup): the adapter raises these from its readiness/bundle gate
# or live-input lag gate. A reason CHAIN that nests one of these ANYWHERE
# (e.g. wrapped in a stage prefix) still counts — the belief substrate is the root cause.
_POSTERIOR_STALENESS_REASON_BASES = frozenset(
    {
        "REPLACEMENT_0_1_LIVE_READINESS_MISSING",
        "REPLACEMENT_0_1_LIVE_BUNDLE_BLOCKED",
        "REPLACEMENT_0_1_LIVE_INPUT_LAG",
        # Older replacement gate path in event_reactor_adapter.py. If it reaches
        # the live reactor, the cure is still the same posterior cycle-advance
        # reseed; this is read-boundary handling, not a producer alias.
        "REPLACEMENT_LIVE_INPUT_LAG",
        # Pre-cutover queued/durable reason bases. These are read-boundary
        # compatibility only; producers now emit the LIVE_READINESS/BUNDLE names
        # above. Old rows still need the same single-family reseed cure.
        "REPLACEMENT_0_1_LIVE_AUTHORITY_READINESS_MISSING",
        "REPLACEMENT_0_1_LIVE_AUTHORITY_BUNDLE_BLOCKED",
    }
)


def _is_posterior_staleness_reason(reason: str | None) -> bool:
    """True iff ``reason`` indicates the replacement posterior (belief substrate) is stale/absent,
    so the always-decidable handler should enqueue a single-family cycle-advance reseed. Explicit
    segment membership against the closed set; empty/None => False (nothing to refresh)."""
    if not reason:
        return False
    segments = [seg.strip() for seg in str(reason).split(":")]
    return any(seg in _POSTERIOR_STALENESS_REASON_BASES for seg in segments)


def _day0_hard_fact_payload_live_eligible(event: OpportunityEvent) -> bool:
    payload = _payload_dict(event)
    return (
        payload.get("source_match_status") == "MATCH"
        and payload.get("local_date_status") == "MATCH"
        and payload.get("station_match_status") == "MATCH"
        and payload.get("dst_status") == "UNAMBIGUOUS"
        and payload.get("metric_match_status") == "MATCH"
        and payload.get("rounding_status") == "MATCH"
        and payload.get("source_authorized_status", "AUTHORIZED") == "AUTHORIZED"
        and normalize_day0_live_authority_status(payload.get("live_authority_status")) == "live"
    )


def _regret_bucket_for(reason: str) -> str:
    reason_text = str(reason or "")
    if reason_text == "FDR_REJECTED" or reason_text.startswith("FDR_REJECTED:"):
        return "FDR_REJECTED"
    if reason_text in {"KELLY_TOO_SMALL"}:
        return "KELLY_TOO_SMALL"
    try:
        from src.contracts.rejection_reasons import classify_rejection_reason, lookup_rejection_reason

        if lookup_rejection_reason(reason_text) is not None:
            return classify_rejection_reason(reason_text).value
    except Exception:
        pass
    if "RISK" in reason_text:
        return "RISK_CAP"
    if "QUOTE" in reason_text or "SNAPSHOT" in reason_text:
        return "QUOTE_UNAVAILABLE"
    if "SOURCE" in reason_text or "DAY0_HARD_FACT" in reason_text:
        return "SOURCE_WRONG"
    if "FAMILY" in reason_text:
        return "FAMILY_INCOMPLETE"
    if "LEAK" in reason_text or "AVAILABLE_AT" in reason_text:
        return "LEAKAGE_BLOCKED"
    return "UNKNOWN_REVIEW_REQUIRED"


# ---------------------------------------------------------------------------
# Day0 hourly-vector refresh cluster (R4-b2 extraction from src/main.py,
# 2026-07-08 main.py slimming). Refreshes Day0 high-resolution hourly vectors
# off the trading reactor cadence; opportunistically fetches Open-Meteo and
# writes zeus-forecasts.db without pinning the live event reactor. Scheduled
# from src.main's ``edli_day0_hourly_refresh`` job (thin delegating hook there).
# ---------------------------------------------------------------------------


def _edli_reactor_held_family_provider():
    """ALWAYS-DECIDABLE invariant — ordering (operator correction 2026-06-12). Build the read-only,
    fail-soft provider of currently-HELD (city, target_date, metric) families so the reactor's
    refresh fan-out refreshes money-at-risk families FIRST (then liquidity-blind fair rotation —
    NO liquidity ordering). Reads zeus_trades.position_current via a short-lived mode=ro connection
    per call (the reactor owns zeus-world only; the trades read is injected so the reactor never
    opens a trades conn). Absent trades DB / any error => empty set (no held bias). Returns None
    when the trades DB path is unconfigured."""
    from src.state.db import _zeus_trade_db_path

    try:
        trades_path = _zeus_trade_db_path()
    except Exception:
        return None
    if not trades_path:
        return None

    def _provider():
        import sqlite3 as _sqlite3
        from pathlib import Path as _Path

        from src.data.replacement_cycle_advance_trigger import _held_position_families

        p = _Path(str(trades_path))
        if not p.exists():
            return frozenset()
        conn_t = _sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5.0)
        try:
            return frozenset(_held_position_families(conn_t))
        finally:
            conn_t.close()

    return _provider


def _edli_current_held_position_family_keys() -> set[tuple[str, str, str]]:
    """Current held-position families for monitor and duplicate-entry suppression.

    Any family with real position_current exposure must keep receiving position-monitor
    attention even when no new-entry edge fires. Future/pre-settlement held exposure
    also re-enters EDLI_REDECISION_PENDING so the full family selector can exercise
    the already-owned-token fill-up / close-before-open shift lane. Same-day Day0
    remains on the observation-aware monitor lane because forecast-only redecision
    is phase-closed once the target local day starts.
    Fail-soft matches the reactor held-family provider; a read failure must not crash the daemon.
    """

    provider = _edli_reactor_held_family_provider()
    if provider is None:
        return set()
    try:
        raw_families = provider()
    except Exception as exc:  # noqa: BLE001
        import logging as _logging

        _logging.getLogger("zeus.events.reactor").warning(
            "edli_redecision_screen: held-position family read failed; held families not admitted this tick: %r",
            exc,
        )
        return set()
    out: set[tuple[str, str, str]] = set()
    for family in raw_families or ():
        try:
            city, target_date, metric = family
        except (TypeError, ValueError):
            continue
        key = (str(city or "").strip(), str(target_date or "").strip(), str(metric or "").strip())
        if all(key):
            out.add(key)
    return out


def _edli_day0_hourly_priority_families() -> list[tuple[str, str, str]]:
    """Money-path families that should drive Day0 hourly-vector refresh order."""
    import logging as _logging

    from src.main import _pending_family_rows_for_refresh

    _log = _logging.getLogger("zeus.events.reactor")

    families: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(raw: Iterable[tuple[object, object, object]]) -> None:
        for city, target_date, metric in raw or ():
            key = _substrate_refresh_family_key(city, target_date, metric)
            if key and all(key) and key not in seen:
                seen.add(key)
                families.append(key)

    # Held money is the first refresh consumer. Pending event queues can grow
    # large when the reactor is behind; putting them first lets stale candidates
    # delay fresh held-position Day0 probabilities.
    add(sorted(_edli_current_held_position_family_keys()))

    try:
        world_ro = get_world_connection_read_only()
        try:
            rows = _pending_family_rows_for_refresh(
                world_ro,
                consumer_name="edli_reactor_v1",
                event_window_limit=int(os.environ.get("ZEUS_DAY0_HOURLY_PRIORITY_EVENT_WINDOW_LIMIT", "2000")),
            )
        finally:
            world_ro.close()
        add(
            (
                row[0],
                row[1],
                row[2],
            )
            for row in rows
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("edli_day0_hourly_refresh: pending-family priority read failed: %s", exc)

    try:
        trade_ro = get_trade_connection_read_only()
        try:
            add(_open_rest_family_rows_for_refresh(trade_ro))
        finally:
            trade_ro.close()
    except Exception as exc:  # noqa: BLE001
        _log.warning("edli_day0_hourly_refresh: open-rest priority read failed: %s", exc)

    return families


_DAY0_HOURLY_REFRESH_CURSOR = 0


def _day0_hourly_refresh_max_cities(*, priority_city_count: int) -> int:
    try:
        configured = int(os.environ.get("ZEUS_DAY0_HOURLY_REFRESH_MAX_CITIES", "1"))
    except (TypeError, ValueError):
        configured = 1
    try:
        priority_cap = int(os.environ.get("ZEUS_DAY0_HOURLY_REFRESH_PRIORITY_CITY_CAP", "3"))
    except (TypeError, ValueError):
        priority_cap = 1
    if priority_city_count <= 0:
        return max(0, configured)
    return max(0, max(configured, min(int(priority_city_count), max(0, priority_cap))))


def _day0_hourly_refresh_budget_seconds() -> float:
    try:
        return max(0.25, float(os.environ.get("ZEUS_DAY0_HOURLY_REFRESH_BUDGET_SECONDS", "6.0")))
    except (TypeError, ValueError):
        return 6.0


def _day0_hourly_fetch_timeout_seconds() -> float:
    try:
        return max(0.25, float(os.environ.get("ZEUS_DAY0_HOURLY_FETCH_TIMEOUT_SECONDS", "1.5")))
    except (TypeError, ValueError):
        return 1.5


def _rotate_day0_refresh_segment(items: list[Any], cursor: int) -> list[Any]:
    if not items:
        return []
    offset = int(cursor) % len(items)
    return items[offset:] + items[:offset]


def _edli_rotate_day0_hourly_refresh_order(
    ordered: list[Any],
    *,
    priority_city_count: int,
    cursor: int,
) -> list[Any]:
    priority = ordered[: max(0, int(priority_city_count))]
    rest = ordered[max(0, int(priority_city_count)) :]
    return (
        _rotate_day0_refresh_segment(priority, cursor)
        + _rotate_day0_refresh_segment(rest, cursor)
    )


def _edli_order_day0_hourly_refresh_cities(
    cities: list[Any],
    *,
    decision_time: datetime,
    priority_families: Iterable[tuple[str, str, str]],
) -> tuple[list[Any], int]:
    """Put same-local-day money-path cities before the static universe sweep."""
    from src.main import _substrate_refresh_family_text_key

    by_name_key = {
        _substrate_refresh_family_text_key(getattr(city, "name", "")): city
        for city in cities
        if str(getattr(city, "name", "") or "").strip()
    }
    priority_city_keys: list[str] = []
    seen_priority: set[str] = set()
    for city_name, target_date, metric in priority_families or ():
        if metric not in {"high", "low"}:
            continue
        city = by_name_key.get(_substrate_refresh_family_text_key(city_name))
        if city is None:
            continue
        try:
            local_date = decision_time.astimezone(ZoneInfo(str(getattr(city, "timezone")))).date().isoformat()
        except Exception:  # noqa: BLE001
            continue
        if str(target_date or "").strip() != local_date:
            continue
        key = _substrate_refresh_family_text_key(getattr(city, "name", ""))
        if key and key not in seen_priority:
            seen_priority.add(key)
            priority_city_keys.append(key)

    ordered: list[Any] = []
    emitted: set[str] = set()
    for key in priority_city_keys:
        city = by_name_key.get(key)
        if city is not None:
            ordered.append(city)
            emitted.add(key)
    for city in cities:
        key = _substrate_refresh_family_text_key(getattr(city, "name", ""))
        if key not in emitted:
            ordered.append(city)
            emitted.add(key)
    return ordered, len(priority_city_keys)


def run_edli_day0_hourly_refresh_cycle(*, trading_lane_active: bool) -> None:
    """Scheduler entrypoint (R4-b2 extraction from src/main.py::_edli_day0_hourly_refresh_cycle).

    Refresh Day0 high-resolution hourly vectors off the trading reactor cadence.
    These vectors improve remaining-day Day0 pricing, but fetching Open-Meteo
    and writing ``zeus-forecasts.db`` must not pin the live event reactor. The
    reactor consumes whatever is already fresh; this side job opportunistically
    refreshes the carrier and yields whenever the trading reactor/redecision
    lane is active.

    ``trading_lane_active`` is injected from src.main after atomic admission
    against the reactor, redecision, and held-monitor scheduling primitives.
    """
    global _DAY0_HOURLY_REFRESH_CURSOR

    import logging as _logging

    from src.config import settings

    _log = _logging.getLogger("zeus.events.reactor")
    _settings_source = settings._data if hasattr(settings, "_data") else settings
    edli_cfg = _settings_source.get("edli", {}) if isinstance(_settings_source, dict) else {}
    if not edli_cfg.get("enabled"):
        return
    if trading_lane_active:
        _log.info("edli_day0_hourly_refresh deferred: trading lane active")
        return
    try:
        from src.config import runtime_cities as _rc
        from src.data.day0_hourly_vectors import maybe_refresh_day0_hourly_vectors

        decision_time = datetime.now(timezone.utc)
        priority_families = _edli_day0_hourly_priority_families()
        ordered_cities, priority_city_count = _edli_order_day0_hourly_refresh_cities(
            _rc(),
            decision_time=decision_time,
            priority_families=priority_families,
        )
        ordered_cities = _edli_rotate_day0_hourly_refresh_order(
            ordered_cities,
            priority_city_count=priority_city_count,
            cursor=_DAY0_HOURLY_REFRESH_CURSOR,
        )
        max_cities = _day0_hourly_refresh_max_cities(
            priority_city_count=priority_city_count,
        )
        stats = maybe_refresh_day0_hourly_vectors(
            ordered_cities,
            decision_time=decision_time,
            budget_s=_day0_hourly_refresh_budget_seconds(),
            max_cities=max_cities,
            timeout_s=_day0_hourly_fetch_timeout_seconds(),
            persist_lock_blocking=False,
            return_stats=True,
        )
        vectors_written = int(getattr(stats, "vectors_written", stats))
        cities_attempted = int(getattr(stats, "cities_attempted", 0) or 0)
        if cities_attempted > 0 and ordered_cities:
            _DAY0_HOURLY_REFRESH_CURSOR = (
                _DAY0_HOURLY_REFRESH_CURSOR + cities_attempted
            ) % max(1, len(ordered_cities))
        if vectors_written or priority_city_count:
            _log.info(
                "edli_day0_hourly_refresh: vectors_written=%d priority_cities=%d "
                "max_cities=%d cities_attempted=%d skipped_throttle=%d "
                "incomplete_expected_bundles=%d budget_exhausted=%s cursor=%d",
                vectors_written,
                priority_city_count,
                max_cities,
                cities_attempted,
                int(getattr(stats, "cities_skipped_throttle", 0) or 0),
                int(getattr(stats, "incomplete_expected_bundles", 0) or 0),
                bool(getattr(stats, "budget_exhausted", False)),
                _DAY0_HOURLY_REFRESH_CURSOR,
            )
    except Exception as _vec_exc:  # noqa: BLE001 — additive lane, fail-soft
        _log.warning("EDLI day0 hourly-vector refresh failed (non-fatal): %r", _vec_exc)


def _substrate_refresh_city_alias_to_name() -> dict[str, str]:

    from src.main import _substrate_refresh_family_text_key

    from src.config import cities_by_name as _refresh_cities_by_name

    alias_to_name: dict[str, str] = {}
    for _city in _refresh_cities_by_name.values():
        for _surface in (
            _city.name,
            *_city.aliases,
            *_city.slug_names,
        ):
            _key = _substrate_refresh_family_text_key(_surface)
            if _key:
                alias_to_name[_key] = _city.name
    return alias_to_name

def _substrate_refresh_canonical_city_name(city: object) -> str:

    from src.main import _substrate_refresh_family_text_key

    raw = str(getattr(city, "name", None) or city or "").strip()
    return _substrate_refresh_city_alias_to_name().get(
        _substrate_refresh_family_text_key(raw),
        raw,
    )

def _substrate_refresh_family_key(
    city: object,
    target_date: object,
    metric: object,
) -> tuple[str, str, str]:

    from src.main import (
        _substrate_refresh_canonical_metric,
        _substrate_refresh_family_text_key,
    )

    return (
        _substrate_refresh_family_text_key(
            _substrate_refresh_canonical_city_name(city)
        ),
        str(target_date or "").strip(),
        _substrate_refresh_canonical_metric(metric),
    )


@dataclass(frozen=True)
class _Day0LiveFamilyAdmission:
    admitted_families: frozenset[tuple[str, str, str]]
    expiry_safe: bool
    scan_cities: frozenset[str] = frozenset()

    def __call__(self, observation: dict[str, Any]) -> bool:
        family = _substrate_refresh_family_key(
            observation.get("city"),
            observation.get("target_date"),
            observation.get("metric"),
        )
        return all(family) and family in self.admitted_families

def _edli_day0_live_family_admission(
    forecasts_conn,
    trade_conn,
    *,
    decision_time: datetime | None = None,
) -> _Day0LiveFamilyAdmission:
    """Build the Day0 execution admission set from live market/exposure truth.

    Day0 observations are valid observation facts, but live execution events must be
    market-backed or tied to already-owned risk. Otherwise the reactor spends claim and
    substrate budget on families where no order can ever be placed.
    """
    import logging as _logging
    from src.config import cities_by_name
    from src.main import _substrate_refresh_family_text_key
    _log = _logging.getLogger("zeus.events.reactor")

    decision_time_utc = (
        datetime.now(timezone.utc)
        if decision_time is None
        else decision_time.astimezone(timezone.utc)
    )

    def _market_family_is_current_local_day(city: object, target_date: object) -> bool:
        city_name = _substrate_refresh_canonical_city_name(city)
        city_cfg = cities_by_name.get(city_name)
        city_tz = str(getattr(city_cfg, "timezone", "") or "")
        if not city_tz:
            return False
        try:
            target_local_date = date.fromisoformat(str(target_date or "").strip())
            decision_local_date = decision_time_utc.astimezone(ZoneInfo(city_tz)).date()
        except Exception:
            return False
        return target_local_date == decision_local_date

    target_floor = (decision_time_utc.date() - timedelta(days=1)).isoformat()
    target_ceiling = (decision_time_utc.date() + timedelta(days=1)).isoformat()
    market_families: set[tuple[str, str, str]] = set()
    market_surface_read_ok = False
    try:
        table_row = forecasts_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_events' LIMIT 1"
        ).fetchone()
        if table_row is not None:
            market_city_scope = sorted(
                {
                    str(surface).strip()
                    for city_cfg in cities_by_name.values()
                    for surface in (
                        getattr(city_cfg, "name", ""),
                        *(getattr(city_cfg, "aliases", ()) or ()),
                        *(getattr(city_cfg, "slug_names", ()) or ()),
                    )
                    if str(surface).strip()
                }
            )
            placeholders = ",".join("?" for _ in market_city_scope)
            rows = forecasts_conn.execute(
                f"""
                SELECT DISTINCT city, target_date, temperature_metric
                  FROM market_events
                 WHERE city IN ({placeholders})
                   AND temperature_metric IN ('high', 'low')
                   AND target_date BETWEEN ? AND ?
                """,
                (*market_city_scope, target_floor, target_ceiling),
            ).fetchall()
            market_surface_read_ok = True
            for city, target_date, metric in rows:
                if not _market_family_is_current_local_day(city, target_date):
                    continue
                family = _substrate_refresh_family_key(city, target_date, metric)
                if all(family):
                    market_families.add(family)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "EDLI day0 live family admission: market_events read failed; "
            "Day0 execution emission restricted to current exposure families: %r",
            exc,
        )

    exposure_families: set[tuple[str, str, str]] = set()
    exposure_surface_read_ok = False
    try:
        trade_tables = {
            str(row[0])
            for row in trade_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        exposure_surface_read_ok = {
            "position_current",
            "venue_commands",
            "venue_order_facts",
        }.issubset(trade_tables)
    except Exception as exc:  # noqa: BLE001
        _log.warning("EDLI day0 live family admission: trade exposure surface probe failed: %r", exc)

    def _add_families(raw: Iterable[tuple[object, object, object]]) -> None:
        for city, target_date, metric in raw or ():
            family = _substrate_refresh_family_key(city, target_date, metric)
            if all(family):
                exposure_families.add(family)

    try:
        _add_families(_open_rest_family_rows_for_refresh(trade_conn))
    except Exception as exc:  # noqa: BLE001
        _log.warning("EDLI day0 live family admission: open-rest family read failed: %r", exc)
    try:
        from src.data.replacement_cycle_advance_trigger import _held_position_families

        _add_families(_held_position_families(trade_conn))
    except Exception as exc:  # noqa: BLE001
        _log.warning("EDLI day0 live family admission: held-position family read failed: %r", exc)

    admitted = frozenset(market_families | exposure_families)
    admitted_city_keys = {family[0] for family in admitted}
    return _Day0LiveFamilyAdmission(
        admitted_families=admitted,
        expiry_safe=market_surface_read_ok and exposure_surface_read_ok,
        scan_cities=frozenset(
            city_name
            for city_name in cities_by_name
            if _substrate_refresh_family_text_key(city_name) in admitted_city_keys
        ),
    )


@dataclass(frozen=True)
class OperatorArm:
    """FIX-2b (PR_SPEC.md §2) operator-arm token for the EDLI live-submit boundary.

    A capability token that is constructible ONLY through ``require_operator_arm``
    after asserting ``edli_live_operator_authorized is True``. The EDLI live submit
    adapter requires this token (regardless of mode — canary included) before any
    real venue submit. Absent the token, the live adapter's submit guard fails closed
    with ``OPERATOR_ARM_REQUIRED`` and main.py selects the no-submit adapter.

    Frozen + presence-typed so "armed without operator authorization" is
    unconstructable rather than merely flag-OFF. The token is applied EXACTLY at the
    EDLI boundary; the mainline executor (execute_final_intent / _live_order) never
    constructs this adapter and so is untouched by this gate.
    """

    authorized: bool = True

def require_operator_arm(edli_cfg: dict) -> "OperatorArm | None":
    """Mint an ``OperatorArm`` token IFF the operator has explicitly authorized live.

    Mirrors the strict assert pattern at ``_assert_edli_live_promotion_artifact``
    (main.py:567): only the literal ``True`` for ``edli_live_operator_authorized``
    authorizes — any other value (missing, False, truthy-non-bool) returns ``None``.
    Returning ``None`` (rather than raising) lets the live-builder selector degrade to
    the no-submit adapter fail-closed instead of crashing the daemon boot.
    """

    if edli_cfg.get("edli_live_operator_authorized") is True:
        return OperatorArm(authorized=True)
    return None

def _build_edli_status_pulse(
    *,
    started_at: str,
    completed_at: str,
    candidates: int,
    processed: int,
    proof_accepted: int,
    rejected: int,
    retried: int,
    dead_lettered: int,
    rejection_reason_counts: dict,
    risk_level: str,
    submit_disabled_effective_mode: bool,
    live_submit_attempts: int,
    live_venue_acks: int = 0,
) -> dict:
    """Build the EDLI reactor status pulse dict.

    FIX-4 (P2, 2026-06-09): separates proof_accepted from live_submit_attempts.
    ``proof_accepted`` counts events whose money-path proof was accepted (i.e.,
    final intent was built). ``live_submit_attempts`` counts ONLY actual venue
    submit calls made this cycle — 0 when the live-submit lane is not selected. Dashboards
    MUST NOT treat proof_accepted as evidence of a venue interaction.

    ``live_venue_acks`` counts venue responses where ``venue_ack_received`` is
    True (successful ACK from the exchange).  Always <= live_submit_attempts.
    """
    return {
        "mode": "edli_event_reactor",
        "started_at": started_at,
        "completed_at": completed_at,
        "candidates": candidates,
        "candidates_evaluated": candidates,
        "processed": processed,
        "proof_accepted": proof_accepted,
        "final_intents_built": proof_accepted,
        "submit_attempts": live_submit_attempts,
        "venue_acks": live_venue_acks,
        "no_trades": rejected + retried + dead_lettered,
        "rejected": rejected,
        "retried": retried,
        "dead_lettered": dead_lettered,
        "rejection_reason_counts": rejection_reason_counts,
        "top_no_trade_reasons": rejection_reason_counts,
        "deterministic_rejections": (
            {"real_order_submit_disabled": proof_accepted}
            if submit_disabled_effective_mode and proof_accepted > 0
            else {}
        ),
        "risk_level": risk_level,
    }

def _open_rest_family_rows_for_refresh(trade_conn) -> list[tuple[str, str, str]]:
    """Families with live unfilled ENTRY rests that need fresh executable books.

    Pending opportunity events are not the only source of live money-at-risk
    freshness demand. Once an ENTRY maker rest is live, duplicate suppression can
    correctly prevent new entry events for that token, leaving no pending event to
    keep the book warm. Use bounded latest-fact seeks over the small command set
    so the substrate warmer can keep open rests re-priceable without scanning the
    full order-fact history.
    """
    from src.contracts.canonical_lifecycle import VenueOrderStatus

    from src.execution.staleness_cancel import OPEN_REST_FACT_STATES

    try:
        command_cols = {
            str(row[1])
            for row in trade_conn.execute("PRAGMA table_info(venue_commands)").fetchall()
        }
        fact_cols = {
            str(row[1])
            for row in trade_conn.execute("PRAGMA table_info(venue_order_facts)").fetchall()
        }
        token_select = "token_id" if "token_id" in command_cols else "'' AS token_id"
        snapshot_select = "snapshot_id" if "snapshot_id" in command_cols else "'' AS snapshot_id"
        state_select = "state" if "state" in command_cols else "'' AS state"
        state_filter = (
            "AND state IN ('ACKED', 'POST_ACKED', 'PARTIAL')" if "state" in command_cols else ""
        )
        remaining_select = "remaining_size" if "remaining_size" in fact_cols else "NULL AS remaining_size"
        commands = trade_conn.execute(
            f"""
            SELECT command_id, position_id, venue_order_id, {token_select}, {snapshot_select}, {state_select}
              FROM venue_commands
             WHERE intent_kind = 'ENTRY'
               AND venue_order_id IS NOT NULL
               AND venue_order_id != ''
               {state_filter}
            """
        ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    open_states = set(OPEN_REST_FACT_STATES)
    for row in commands:
        venue_order_id = str(row[2] or "")
        if not venue_order_id:
            continue
        try:
            fact = trade_conn.execute(
                f"""
                SELECT state, {remaining_select}
                  FROM venue_order_facts
                 WHERE venue_order_id = ?
                 ORDER BY local_sequence DESC
                 LIMIT 1
                """,
                (venue_order_id,),
            ).fetchone()
        except Exception:  # noqa: BLE001
            continue
        if fact is None or str(fact[0] or "") not in open_states:
            continue
        remaining_value = fact[1] if len(fact) > 1 else None
        raw_remaining = "" if remaining_value is None else str(remaining_value).strip()
        if raw_remaining:
            try:
                if float(raw_remaining) <= 0.000001:
                    continue
            except ValueError:
                continue
        if str(fact[0] or "") == VenueOrderStatus.PARTIALLY_MATCHED and not raw_remaining:
            continue
        position_id = str(row[1] or "")
        family: tuple[str, str, str] | None = None
        try:
            pos = trade_conn.execute(
                """
                SELECT city, target_date, temperature_metric
                  FROM position_current
                 WHERE position_id = ?
                   AND phase IN ('pending_entry', 'active', 'day0_window')
                 LIMIT 1
                """,
                (position_id,),
            ).fetchone() if position_id else None
        except Exception:  # noqa: BLE001
            pos = None
        if pos is not None:
            family = (
                str(pos[0] or "").strip(),
                str(pos[1] or "").strip(),
                str(pos[2] or "").strip(),
            )
        if not family or not all(family):
            family = _open_rest_family_from_snapshot(
                trade_conn,
                token_id=str(row[3] or ""),
                snapshot_id=str(row[4] or ""),
            )
        if family and all(family) and family not in seen:
            seen.add(family)
            out.append(family)
    return out

def _open_rest_family_from_snapshot(
    trade_conn,
    *,
    token_id: str,
    snapshot_id: str,
) -> tuple[str, str, str] | None:
    """Resolve an ACKED rest's family even before position_current projection exists."""

    try:
        snap_cols = {
            str(row[1])
            for row in trade_conn.execute("PRAGMA table_info(executable_market_snapshots)").fetchall()
        }
    except Exception:  # noqa: BLE001
        return None
    slug_cols = [col for col in ("event_id", "event_slug") if col in snap_cols]
    if not slug_cols:
        return None
    select_slug = slug_cols[0] if len(slug_cols) == 1 else "COALESCE(" + ", ".join(slug_cols) + ")"
    predicates: list[str] = []
    params: list[str] = []
    if snapshot_id and "snapshot_id" in snap_cols:
        predicates.append("snapshot_id = ?")
        params.append(snapshot_id)
    if token_id:
        for col in ("selected_outcome_token_id", "yes_token_id", "no_token_id"):
            if col in snap_cols:
                predicates.append(f"{col} = ?")
                params.append(token_id)
    if not predicates:
        return None
    snapshot_order = "CASE WHEN snapshot_id = ? THEN 0 ELSE 1 END" if "snapshot_id" in snap_cols else "1"
    query_params = [*params]
    if "snapshot_id" in snap_cols:
        query_params.append(snapshot_id)
    try:
        row = trade_conn.execute(
            f"""
            SELECT {select_slug} AS market_slug
              FROM executable_market_snapshots
             WHERE {" OR ".join(predicates)}
             ORDER BY
               {snapshot_order},
               captured_at DESC
             LIMIT 1
            """,
            tuple(query_params),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    return _weather_family_from_market_slug(str(row[0] or ""))

def _weather_family_from_market_slug(slug: str) -> tuple[str, str, str] | None:
    text = str(slug or "").strip().lower()
    prefixes = (
        ("highest-temperature-in-", "high"),
        ("lowest-temperature-in-", "low"),
    )
    metric = ""
    rest = ""
    for prefix, candidate_metric in prefixes:
        if text.startswith(prefix):
            metric = candidate_metric
            rest = text[len(prefix):]
            break
    if not rest or "-on-" not in rest:
        return None
    city_slug, date_slug = rest.rsplit("-on-", 1)
    parts = date_slug.split("-")
    if len(parts) != 3:
        return None
    month_name, day_text, year_text = parts
    month_map = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    try:
        target_date = date(
            int(year_text),
            month_map[month_name],
            int(day_text),
        ).isoformat()
    except Exception:  # noqa: BLE001
        return None
    try:
        from src.config import runtime_cities_by_name

        city_by_slug: dict[str, str] = {}
        for name, city in runtime_cities_by_name().items():
            aliases = set(getattr(city, "slug_names", ()) or ())
            aliases.add(str(name).lower().replace(" ", "-"))
            aliases.add(str(getattr(city, "name", name)).lower().replace(" ", "-"))
            for alias in aliases:
                if alias:
                    city_by_slug[str(alias).lower()] = str(getattr(city, "name", name) or name)
        city = city_by_slug.get(city_slug)
    except Exception:  # noqa: BLE001
        city = None
    if not city:
        city = city_slug.replace("-", " ").title()
    return (city, target_date, metric)

def _replacement_forecast_refit_decision_from_settings():

    import logging as _logging
    from src.main import _settings_section
    _log = _logging.getLogger("zeus.events.reactor")

    from src.config import PROJECT_ROOT
    from src.data.replacement_forecast_refit_handoff import refit_decision_from_handoff_payload

    cfg = _settings_section("replacement_forecast_live", {}) or {}
    raw_path = cfg.get("refit_handoff_path") or "state/replacement_forecast_live/refit_handoff.json"
    path = Path(str(raw_path))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 - fail closed at switch decision
        _log.warning("replacement forecast refit handoff unreadable: %s", exc)
        return None
    if not isinstance(payload, dict):
        _log.warning("replacement forecast refit handoff must be a JSON object: %s", path)
        return None
    try:
        return refit_decision_from_handoff_payload(payload)
    except Exception as exc:  # noqa: BLE001 - fail closed at switch decision
        _log.warning("replacement forecast refit handoff invalid: %s", exc)
        return None

def _sqlite_table_names(conn) -> tuple[str, ...]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
    names: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            names.append(str(row["name"]))
        else:
            names.append(str(row[0]))
    return tuple(sorted(names))

def _current_live_fact_status(relative_path: str) -> str:
    from src.config import PROJECT_ROOT

    path = PROJECT_ROOT / relative_path
    try:
        first_lines = path.read_text(encoding="utf-8").splitlines()[:20]
    except OSError:
        return "STALE_FOR_LIVE"
    for line in first_lines:
        if line.startswith("Status:"):
            return "CURRENT_FOR_LIVE" if "CURRENT_FOR_LIVE" in line else "STALE_FOR_LIVE"
    return "STALE_FOR_LIVE"

def run_edli_event_reactor_cycle(
    *,
    active_lock,
    producer_wake_reason: str | None = None,
    producer_wake_event_ids: tuple[str, ...] = (),
    producer_wake_families: tuple[tuple[str, str, str], ...] = (),
) -> bool:
    """EDLI event-reactor cycle body (R4-b3 extraction from src/main.py::
    _edli_event_reactor_cycle, 2026-07-08). main.py's scheduler hook is now a
    thin delegating call.

    Cut 10 wires daemon scheduling and schema/config readiness. The live-money
    submit adapter still uses injected gates; until an event is explicitly
    accepted by those gates, this job is conservative and side-effect free.

    ``active_lock`` is the ``threading.Lock`` main.py calls
    ``_edli_reactor_active_lock``, injected from src.main: it is a cross-job
    scheduling-coordination primitive (5+ other EDLI jobs read its
    ``.locked()`` state), so main.py -- the dispatcher -- retains ownership
    of the Lock object itself. This cycle owns the acquire/release lifecycle
    around its own run, exactly as it did before the extraction.
    """
    import logging as _logging
    from src.main import (
        _defer_for_held_position_monitor,
        _edli_acquire_mutex,
        _edli_bounded_positive_int,
        _edli_build_forecast_snapshot_events,
        _edli_emit_lock_timeout_seconds,
        _edli_is_sqlite_lock_error,
        _edli_next_redecision_source,
        _edli_pending_entity_keys,
        _edli_refresh_global_allocator_for_live_bridge,
        _settings_section,
        _start_venue_background_maintenance_after_reactor_if_required,
    )
    from src.data.replacement_forecast_production import _replacement_forecast_runtime_flags_from_settings
    from src.state.portfolio import load_runtime_open_portfolio

    _log = _logging.getLogger("zeus.events.reactor")

    edli_cfg = _settings_section("edli", {})
    committed_day0_wake = (
        producer_wake_reason == "day0_extreme_event_committed"
    )
    committed_price_wake = (
        producer_wake_reason == "market_price_advanced"
        and bool(producer_wake_event_ids)
    )
    committed_event_wake = committed_day0_wake or committed_price_wake
    forecast_wake_family_order: list[tuple[str, str, str]] = []
    forecast_wake_families: set[tuple[str, str, str]] = set()
    for raw_family in producer_wake_families:
        if len(raw_family) != 3:
            continue
        family = (
            str(raw_family[0] or "").strip(),
            str(raw_family[1] or "").strip(),
            str(raw_family[2] or "").strip(),
        )
        if all(family) and family not in forecast_wake_families:
            forecast_wake_families.add(family)
            forecast_wake_family_order.append(family)
    targeted_forecast_wake = (
        producer_wake_reason == "forecast_posterior_advanced"
        and bool(forecast_wake_families)
    )
    producer_fast_path = committed_event_wake or targeted_forecast_wake
    from src.runtime.reactor_wake import reactor_urgent_wake_revision

    maintenance_urgent_revision = reactor_urgent_wake_revision()

    def _urgent_wake_pending() -> bool:
        current = reactor_urgent_wake_revision()
        return current is not None and current != maintenance_urgent_revision

    if not edli_cfg.get("enabled") or not edli_cfg.get("event_writer_enabled"):
        return False
    if _defer_for_held_position_monitor("edli_event_reactor"):
        return False
    if active_lock.locked():
        _log.warning("EDLI reactor skipped: previous EDLI reactor cycle is still running")
        return False
    import sqlite3  # transient world-DB lock classification for fail-soft emit boundary
    from src.engine.event_reactor_adapter import (
        edli_source_truth_gate,
        event_bound_live_adapter_from_trade_conn,
        event_bound_no_submit_adapter_from_trade_conn,
        executable_snapshot_gate_from_trade_conn,
        replacement_forecast_baseline_bundle_provider_from_forecast_conn,
        riskguard_allows_new_entries,
    )
    from src.engine.event_bound_final_intent import submit_event_bound_final_intent_via_existing_executor
    from src.events.event_priority import day0_is_tradeable_for_scope
    from src.events.event_store import EventStore
    from src.risk_allocator import snapshot_global_auction_capital_authority
    from src.riskguard.riskguard import get_current_level
    from src.state.db import ZEUS_FORECASTS_DB_PATH, get_forecasts_connection_read_only, get_trade_connection_with_world_required, get_world_connection
    from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger

    if not active_lock.acquire(blocking=False):
        _log.warning("EDLI reactor skipped: previous EDLI reactor cycle is still running")
        return False
    if not producer_fast_path and _urgent_wake_pending():
        active_lock.release()
        return False
    if _defer_for_held_position_monitor("edli_event_reactor"):
        active_lock.release()
        return False
    try:
        conn = get_world_connection()
    except Exception:
        active_lock.release()
        raise
    # K1: the calibration authority is split — platt_models lives in the world DB (this conn's
    # main) while calibration_pairs lives in the forecasts DB. get_calibrator reads BOTH, so the
    # calibration_conn must have forecasts attached for the unqualified calibration_pairs read to
    # resolve; otherwise every live decision fails CALIBRATION_AUTHORITY_MISSING:calibration store
    # unavailable. Read-only attach (no cross-DB write), idempotent.
    try:
        _attached_dbs = {row[1] for row in conn.execute("PRAGMA database_list").fetchall()}
        if "forecasts" not in _attached_dbs:
            conn.execute("ATTACH DATABASE ? AS forecasts", (str(ZEUS_FORECASTS_DB_PATH),))
    except Exception as _attach_exc:  # noqa: BLE001 - non-fatal; calibration will fail-closed if unresolved
        _log.warning("EDLI reactor: ATTACH forecasts to calibration conn failed (non-fatal): %r", _attach_exc)
    try:
        forecasts_conn = get_forecasts_connection_read_only()
    except Exception:
        conn.close()
        active_lock.release()
        raise
    # Warm the in-process bankroll-of-record cache from the durable collateral
    # ledger snapshot so the per-event no-submit Kelly proof can read
    # bankroll_provider.cached() without performing venue/RPC I/O inside the
    # reactor. The post-trade-capital sidecar owns wallet refreshes; this live
    # scheduler consumes only local fresh truth. Non-fatal — Kelly fails closed
    # (KELLY_PROOF_MISSING) if the ledger is absent/stale/degraded.
    try:
        from src.runtime import bankroll_provider as _bankroll_provider

        _bk_warm = _bankroll_provider.warm_from_collateral_snapshot()
        if _bk_warm is None:
            _log.error(
                "EDLI reactor: bankroll ledger warm returned None — cache cold, Kelly will "
                "fail closed (KELLY_PROOF_MISSING). Collateral snapshot is missing, stale, "
                "or degraded."
            )
    except Exception as _bk_exc:  # noqa: BLE001
        _log.warning("EDLI reactor: bankroll cache warm failed (non-fatal): %r", _bk_exc)
    try:
        from src.state.db import world_write_mutex as _world_write_mutex

        _stage_started = time.monotonic()

        def _log_stage(stage: str) -> None:
            nonlocal _stage_started
            _now_mono = time.monotonic()
            _elapsed = _now_mono - _stage_started
            if _elapsed >= 1.0:
                _log.info("EDLI reactor stage completed: %s elapsed_s=%.3f", stage, _elapsed)
            _stage_started = _now_mono

        now = datetime.now(timezone.utc)
        received_at = now.isoformat()
        forecast_emit_limit = _edli_positive_int_or_unbounded(
            edli_cfg, "forecast_snapshot_emit_limit", default=12, maximum=20
        )
        day0_emit_limit = _edli_bounded_positive_int(edli_cfg, "day0_catchup_emit_limit", default=20, maximum=100)
        # Live cadence invariant: full coverage is achieved by fair rotation across
        # continuous cycles, not by processing an unbounded queue in one cycle. The
        # unbounded 2026-06-12 setting let stale substrate / slow JIT book events hold
        # one reactor run past the 60s scheduler cadence, so the next run skipped and
        # entry/day0/redecision stalled. Bound per-cycle work; events not reached stay
        # pending and are reached by EventStore's city/lane fairness.
        proof_limit = _edli_bounded_positive_int(
            edli_cfg,
            "reactor_process_limit",
            default=12,
            maximum=50,
        )
        store = EventStore(conn)
        targeted_event_ids = set(producer_wake_event_ids)
        try:
            from src.config import runtime_cities as _runtime_cities
            from src.data.day0_fast_obs import get_fast_obs_emitter

            _synced_reports = get_fast_obs_emitter().sync_from_ledger(
                conn,
                _runtime_cities(),
                as_of=now,
            )
            if _synced_reports:
                _log.info(
                    "EDLI reactor synced %d new METAR ledger reports",
                    _synced_reports,
                )
        except Exception as _day0_sync_exc:  # noqa: BLE001
            _log.warning(
                "EDLI reactor METAR ledger sync failed; fast-tail consumers fail closed: %r",
                _day0_sync_exc,
            )
        _log_stage("day0_ledger_sync")
        _day0_family_admission: _Day0LiveFamilyAdmission | None = None
        if (
            not producer_fast_path
            and edli_cfg.get("day0_extreme_trigger_enabled")
            and edli_cfg.get("day0_authority_catchup_scanner_enabled", False)
        ):
            try:
                from src.state.db import get_trade_connection_read_only as _get_trade_ro

                _day0_admission_trade_conn = _get_trade_ro()
                try:
                    _day0_family_admission = _edli_day0_live_family_admission(
                        forecasts_conn,
                        _day0_admission_trade_conn,
                        decision_time=now,
                    )
                finally:
                    _day0_admission_trade_conn.close()
            except Exception as _day0_admission_exc:  # noqa: BLE001
                _log.warning(
                    "EDLI day0 live family admission build failed; Day0 execution emit "
                    "will be restricted this cycle and unmarketed pending expiry skipped: %r",
                    _day0_admission_exc,
                )
                _day0_family_admission = _Day0LiveFamilyAdmission(
                    admitted_families=frozenset(),
                    expiry_safe=False,
                )
        _prune_mutex = _world_write_mutex()
        _prune_lock_timeout_s = _edli_prune_lock_timeout_seconds(edli_cfg)
        _prune_acquired = (
            False
            if producer_fast_path
            else _prune_mutex.acquire(timeout=_prune_lock_timeout_s)
        )
        if _prune_acquired:
            try:
                _edli_prune_pending_working_set(
                    store,
                    decision_time=now,
                    day0_family_admission=_day0_family_admission,
                    urgent_wake_pending=_urgent_wake_pending,
                )
                conn.commit()
            finally:
                _prune_mutex.release()
        elif not producer_fast_path:
            _log.warning(
                "EDLI reactor prune skipped: world write mutex unavailable after %.3fs; "
                "deferring maintenance so the money-path reactor can drain events.",
                _prune_lock_timeout_s,
            )
        else:
            _log.info(
                "EDLI reactor producer wake: skipping maintenance prune "
                "before processing fresh fact"
            )
        _log_stage("pending_prune")
        if not producer_fast_path and _urgent_wake_pending():
            _log.info(
                "EDLI reactor maintenance preempted after prune by urgent producer wake"
            )
            return False
        _fsr_events = []
        if (
            not committed_event_wake
            and edli_cfg.get("forecast_snapshot_trigger_enabled")
        ):
            try:
                _fair_source = _edli_next_redecision_source()
                _fsr_pending = set()
                if not targeted_forecast_wake:
                    _pending_key_budget_s = _edli_forecast_snapshot_build_budget_seconds(
                        edli_cfg
                    )
                    _fsr_pending = _edli_pending_entity_keys(
                        conn,
                        event_types=("FORECAST_SNAPSHOT_READY",),
                        max_rows_per_status=_edli_prune_batch_limit(edli_cfg),
                        deadline_monotonic=(
                            time.monotonic() + _pending_key_budget_s
                            if _pending_key_budget_s > 0
                            else None
                        ),
                        cancelled=_urgent_wake_pending,
                    )
                _fsr_events = _edli_build_forecast_snapshot_events(
                    conn,
                    decision_time=now,
                    received_at=received_at,
                    limit=None if targeted_forecast_wake else forecast_emit_limit,
                    source=_fair_source,
                    already_pending_keys=_fsr_pending,
                    suppress_recent_no_value_refutations=True,
                    budget_seconds=_edli_forecast_snapshot_build_budget_seconds(edli_cfg),
                    restrict_to_families=(
                        forecast_wake_families if targeted_forecast_wake else None
                    ),
                    cancelled=_urgent_wake_pending,
                )
                if targeted_forecast_wake:
                    _fsr_events = _rank_forecast_wake_events(
                        _fsr_events,
                        forecast_wake_family_order,
                    )
                _log_stage("forecast_snapshot_build")
            except sqlite3.OperationalError as _emit_lock_exc:
                if "locked" in str(_emit_lock_exc).lower() or "busy" in str(_emit_lock_exc).lower():
                    _log.warning(
                        "EDLI reactor: forecast-snapshot build hit transient DB lock "
                        "(%r) — skipping emit this cycle, draining already-queued candidates.",
                        _emit_lock_exc,
                    )
                else:
                    raise
        if not producer_fast_path and _urgent_wake_pending():
            _log.info(
                "EDLI reactor maintenance preempted after forecast discovery "
                "by urgent producer wake"
            )
            return False
        # EDLI live contention fix (2026-05-31): the FSR/Day0/redecision
        # EMIT block writes opportunity_events to the WAL zeus-world.db shared
        # in-process with the market-channel ingestor. Serialize the whole
        # prune+emit+commit unit under the process-global world-DB write mutex so it
        # never holds the WAL write lock concurrently with the ingestor. Forecast
        # selection/no-value refutation has already completed; the data-ingest
        # daemon exclusively owns Day0 source HTTP. This mutex covers only
        # prune/write/commit.
        # Explicit acquire/finally (not ``with``) to avoid reindenting the block.
        _emit_mutex = _world_write_mutex()
        _emit_lock_timeout_s = _edli_emit_lock_timeout_seconds(edli_cfg)
        _emit_acquired = (
            False
            if committed_event_wake
            else _edli_acquire_mutex(_emit_mutex, timeout=_emit_lock_timeout_s)
        )
        if _emit_acquired:
            try:
                if edli_cfg.get("forecast_snapshot_trigger_enabled"):
                    # FAIL-SOFT (2026-05-31): the FSR event-emit is the queue-FILL step, writing
                    # opportunity_events to the WAL world DB shared with the market-channel
                    # ingestor and CollateralLedger heartbeat. Under live load that DB hits
                    # transient "database is locked" past the 30s busy_timeout. A locked-out
                    # emit must NOT crash the whole reactor cycle — the cycle should still drain
                    # candidates already queued from prior cycles. Catch ONLY the transient lock
                    # (narrow, by message) and continue; real schema/logic faults still propagate.
                    try:
                        from src.events.event_writer import EventWriter

                        _fsr_write_results = EventWriter(conn).write_many(_fsr_events)
                        if targeted_forecast_wake:
                            targeted_event_ids.update(
                                result.event_id
                                for result in _fsr_write_results[:proof_limit]
                            )
                        _log_stage("forecast_snapshot_emit")
                    except sqlite3.OperationalError as _emit_lock_exc:
                        if "locked" in str(_emit_lock_exc).lower() or "busy" in str(_emit_lock_exc).lower():
                            _log.warning(
                                "EDLI reactor: forecast-snapshot emit hit transient world-DB lock "
                                "(%r) — skipping emit this cycle, draining already-queued candidates.",
                                _emit_lock_exc,
                            )
                        else:
                            raise
                # Continuous re-decision admission is intentionally NOT all-universe here.
                # The dedicated screen job below owns EDLI_REDECISION_PENDING and admits only
                # families with confirmed trade value, maker rests needing action, or held
                # positions with money at risk. The reactor still emits ordinary
                # FORECAST_SNAPSHOT_READY candidates for new-entry discovery above.
                if (
                    not producer_fast_path
                    and edli_cfg.get("day0_extreme_trigger_enabled")
                    and edli_cfg.get("day0_authority_catchup_scanner_enabled", False)
                ):
                    _day0_trade_conn = get_trade_connection_with_world_required(write_class=None)
                    try:
                        try:
                            _edli_emit_day0_extreme_events(
                                conn,
                                _day0_trade_conn,
                                decision_time=now,
                                received_at=received_at,
                                limit=day0_emit_limit,
                                # Stamp scope-aware emission priority. Production live
                                # scope makes Day0 tradeable.
                                day0_is_tradeable=day0_is_tradeable_for_scope(
                                    str(edli_cfg.get("edli_live_scope") or "forecast_plus_day0")
                                ),
                                budget_seconds=_edli_day0_emit_budget_seconds(edli_cfg),
                                family_admission=_day0_family_admission,
                                urgent_wake_pending=_urgent_wake_pending,
                            )
                            _log_stage("day0_emit")
                        except sqlite3.OperationalError as _day0_emit_lock_exc:
                            if _edli_is_sqlite_lock_error(_day0_emit_lock_exc):
                                _log.warning(
                                    "EDLI reactor: day0 emit still locked after bounded retry "
                                    "(%r) — skipping Day0 emit this cycle and draining already-queued candidates.",
                                    _day0_emit_lock_exc,
                                )
                            else:
                                raise
                    finally:
                        _day0_trade_conn.close()
                # Commit the emit WRITE UNIT (FSR + redecision + day0 → opportunity_events)
                # while still holding the world-DB write mutex, so the WAL write lock is
                # released by the COMMIT before any other writer (ingestor / collateral
                # heartbeat) can interleave. No HTTP/venue work runs inside this block.
                conn.commit()
                _log_stage("emit_commit")
            finally:
                _emit_mutex.release()
        elif not committed_event_wake:
            _log.warning(
                "EDLI reactor emit skipped: world write mutex unavailable after %.3fs; "
                "draining already-queued candidates so heartbeat/monitor/redecision keep cadence.",
                _emit_lock_timeout_s,
            )
            _log_stage("emit_lock_skipped")
        else:
            _log.info(
                "EDLI reactor committed producer wake: reason=%s skipping "
                "forecast/day0 discovery and draining %d durable events immediately",
                producer_wake_reason,
                len(targeted_event_ids),
            )
            _log_stage("committed_event_fast_path")
        if not producer_fast_path and _urgent_wake_pending():
            _log.info(
                "EDLI reactor maintenance preempted after emit by urgent producer wake"
            )
            return False
        # THROUGHPUT STRUCTURAL FIX (2026-06-01): the executable-snapshot refresh
        # (_refresh_pending_family_snapshots) runs a full-universe Gamma scan
        # (find_weather_markets → _get_active_events, benchmarked ~76s COLD; TTL 300s
        # so it re-ran nearly every cycle) + per-token CLOB /book capture across all
        # pending-family bins. Running it INLINE here made the reactor cycle wall-clock
        # blow past the 1-min APScheduler interval (overlapping triggers coalesced/
        # skipped → 0 completed cycles → 0 receipts/trades despite the live submit path
        # being CODE-CLEAR to the venue POST boundary). It is now DECOUPLED into the
        # dedicated _edli_market_substrate_warm_cycle job (mirroring _edli_bankroll_warm_cycle,
        # #45), so this reactor cycle reads ALREADY-captured snapshots (DB-only,
        # microseconds) and reaches process_pending → submit in seconds. Decision
        # semantics are UNCHANGED: a family not yet captured by the warm job still
        # requeues via the reactor's existing EXECUTABLE_SNAPSHOT_RETRY path (fail-closed).
        trade_conn = get_trade_connection_with_world_required(write_class=None)

        regret_ledger = NoTradeRegretLedger(conn)
        reactor_mode = str(edli_cfg.get("reactor_mode", "live"))
        edli_live_scope = str(edli_cfg.get("edli_live_scope") or "forecast_plus_day0")
        real_order_submit_enabled = bool(edli_cfg.get("real_order_submit_enabled", False))
        submit_disabled_effective_mode = reactor_mode == "live_no_submit"
        live_bridge_mode = reactor_mode == "live"
        real_submit_effective = real_order_submit_enabled if reactor_mode == "live" else False
        # Configure the process-wide risk allocator/governor BEFORE the submit adapter is
        # built so the live submit path's select_global_order_type does not raise
        # AllocationDenied("allocator_not_configured"). The legacy discover cycle wires this
        # via refresh_global_allocator; the EDLI cycle does not run that cycle, so without
        # this seam every canary order silently blocks (see /tmp/edli_submit_gate_trace.md).
        # FAIL-CLOSED: if the refresh cannot source a trustworthy drawdown (wallet unreachable
        # / baseline undefined / exception), block THIS cycle to the no-submit adapter rather
        # than submit live with an unconfigured-but-proceeding allocator.
        # SUBMIT-LANE STAMP (silent-trade-kill antibody 2026-06-12): track the TYPED
        # cause whenever a live block clears live_submit_effective so the no-submit adapter
        # can name it on every full-pass receipt it consumes (single source of truth —
        # the same value that drove the selector off the live lane). None => no live block
        # (the live lane was simply not configured for this reactor_mode).
        _live_lane_block_cause: str | None = None
        live_submit_effective = live_bridge_mode or submit_disabled_effective_mode
        _auction_capital_authority = None
        # Task #107 (portfolio/multi Kelly): source one canonical exposure
        # snapshot per reactor cycle. Terminal history and operator/recovery
        # surfaces are irrelevant to sizing, so the decision path reads only
        # runtime-open rows through this cycle's existing trade connection.
        _portfolio_state_provider = None
        _portfolio_snapshot = None
        try:
            _portfolio_snapshot = load_runtime_open_portfolio(trade_conn)
            _portfolio_state_provider = lambda: _portfolio_snapshot  # noqa: E731 — cycle-scoped closure
        except Exception as _portfolio_exc:  # noqa: BLE001 — mode-sensitive fail-closed below
            _log.warning(
                "EDLI reactor: portfolio snapshot load failed; no-submit telemetry may observe "
                "with single-asset sizing, but real-submit will fail closed: %r",
                _portfolio_exc,
            )
        live_submit_effective, _portfolio_snapshot_block = _portfolio_snapshot_submit_gate(
            live_submit_effective=live_submit_effective,
            snapshot_required=real_submit_effective,
            snapshot_available=_portfolio_state_provider is not None,
        )
        if _portfolio_snapshot_block is not None:
            _live_lane_block_cause = _portfolio_snapshot_block
            _log.error(
                "EDLI reactor: real submit disabled this cycle because portfolio_state_unavailable"
            )
        if live_submit_effective:
            _alloc_refresh = _edli_refresh_global_allocator_for_live_bridge(
                trade_conn,
                portfolio_snapshot=_portfolio_snapshot,
            )
            if live_bridge_mode and not _alloc_refresh.get("configured"):
                live_submit_effective = False
                _alloc_reason = _alloc_refresh.get("entry", {}).get("reason") or "allocator_not_configured"
                _live_lane_block_cause = f"live_submit_effective_false:allocator_refresh:{_alloc_reason}"
                _log.error(
                    "EDLI reactor: live-bridge allocator refresh did not configure "
                    "(fail_closed=%r reason=%r) — selecting NO-SUBMIT this cycle.",
                    _alloc_refresh.get("fail_closed"),
                    _alloc_refresh.get("entry", {}).get("reason"),
                )
            elif _alloc_refresh.get("configured"):
                try:
                    _auction_capital_authority = (
                        snapshot_global_auction_capital_authority()
                    )
                except Exception as _capacity_exc:  # noqa: BLE001 - incoherent pair blocks live lane
                    live_submit_effective = False
                    _live_lane_block_cause = (
                        "live_submit_effective_false:"
                        f"capital_authority_snapshot:{type(_capacity_exc).__name__}:"
                        f"{_capacity_exc}"
                    )
                    _log.error(
                        "EDLI reactor: allocator refresh reported configured but "
                        "capacity authority snapshot failed; selecting NO-SUBMIT "
                        "this cycle: %r",
                        _capacity_exc,
                    )
        # The FSR/redecision emit phase intentionally uses the cycle-start timestamp for
        # event identity. Decision certificates are built later, after DB-backed substrate
        # and portfolio reads; use the actual processing timestamp so fresh executable/book
        # parent certificates are never later than the decision they support.
        process_pending_decision_time = datetime.now(timezone.utc)
        replacement_forecast_runtime_flags = _replacement_forecast_runtime_flags_from_settings()
        replacement_forecast_refit_decision = _replacement_forecast_refit_decision_from_settings()
        # DEAD-PROMOTION-APPARATUS REMOVAL (2026-06-16): the runtime-policy resolver and
        # switch-decision evaluator ignore these evidence objects after the live runtime
        # flag path moved to runtime_layer='live'. None is behavior-identical to the deleted parsers.
        replacement_forecast_promotion_evidence = None
        replacement_forecast_capital_objective_evidence = None
        replacement_forecast_baseline_bundle_provider = replacement_forecast_baseline_bundle_provider_from_forecast_conn(
            forecasts_conn
        )
        replacement_forecast_world_tables = _sqlite_table_names(conn)
        from src.data.replacement_forecast_live_switch_surface import (
            CURRENT_DATA_FACT_FILE,
            CURRENT_SOURCE_FACT_FILE,
        )

        replacement_forecast_source_fact_status = _current_live_fact_status(CURRENT_SOURCE_FACT_FILE)
        replacement_forecast_data_fact_status = _current_live_fact_status(CURRENT_DATA_FACT_FILE)
        # FIX-2b (PR_SPEC.md §2): mint the operator-arm token IFF edli_live_operator_authorized
        # is True. The live submit adapter is selected ONLY when (live_submit_effective AND
        # operator_arm is not None); otherwise the no-submit adapter is chosen. This gates
        # EVERY real submit (canary included) at the EDLI boundary by TYPE. The mainline
        # executor never constructs this adapter, so the 293-order mainline is untouched.
        operator_arm = require_operator_arm(edli_cfg)
        # SUBMIT-LANE STAMP + CYCLE-LEVEL LIVE-BLOCK SIGNAL (silent-trade-kill antibody
        # 2026-06-12; /tmp/allpass_nosubmit_rootcause.md). The selector picks the live
        # adapter ONLY when (live_submit_effective AND operator_arm is not None); else
        # the no-submit adapter. Resolve the TYPED cause once, here, so it is
        # the single source of truth threaded onto the control-blocked lane's receipts.
        _edli_live_operator_authorized = edli_cfg.get("edli_live_operator_authorized") is True
        _live_lane_selected = bool(live_submit_effective and operator_arm is not None)
        if operator_arm is None and _live_lane_block_cause is None:
            _live_lane_block_cause = "operator_arm_none"
        if _live_lane_block_cause is None and not _live_lane_selected:
            # live_submit_effective was False without a tracked live block.
            _live_lane_block_cause = f"live_lane_unselected:reactor_mode={reactor_mode}"
        _no_submit_live_block_cause = _live_lane_block_cause or "live_lane_unselected"
        # LOUD cycle-level live-block signal: the live lane is dark THIS cycle while the
        # operator has nominally armed it (reactor_mode=live + operator_authorized). The
        # crash-loop incident ran ~50 min on the no-submit lane with the arm on and NO
        # decision-lane signal. One ERROR per cycle here makes it impossible to miss.
        if not _live_lane_selected and _edli_live_operator_authorized and reactor_mode == "live":
            _log.error(
                "LIVE LANE DARK: no-submit adapter selected while operator arm is on "
                "(reactor_mode=live, edli_live_operator_authorized=True) — cause=%s. "
                "Full-pass candidates this cycle are consumed on the NO_SUBMIT_ADAPTER "
                "lane (receipts stamped with this cause); the live lane submitted nothing.",
                _no_submit_live_block_cause,
            )
        # Decision-triggered targeted substrate marker: when the adapter sees stale
        # executable prices, it marks the family for sidecar capture and returns
        # False so the stale event requeues fail-closed. Snapshot writes are owned
        # by the substrate-observer daemon, not the decision critical path.
        _decision_family_snapshot_refresher = _edli_decision_family_snapshot_refresher(
            forecasts_conn
        )
        # ALWAYS-DECIDABLE invariant (operator law 2026-06-12): a blocked event must
        # create visible refresh work. The substrate-observer sidecar owns broad
        # universe warming, but an event that just blocked on stale executable
        # evidence needs a targeted family recapture; otherwise stale events can
        # requeue forever while broad warming rotates past them.
        _reactor_family_snapshot_refresher = _decision_family_snapshot_refresher
        _reactor_cycle_advance_enqueuer = _edli_reactor_cycle_advance_enqueuer()
        _reactor_day0_hourly_refresher = _edli_reactor_day0_hourly_refresher()
        _reactor_family_market_absence_provider = (
            _edli_reactor_family_market_absence_provider()
        )
        _live_jit_book_quote_provider = (
            _edli_pre_submit_jit_book_quote_provider(
                trade_conn,
                max_quote_age_ms=int(
                    edli_cfg.get("pre_submit_max_quote_age_ms", 1000) or 1000
                ),
            )
            if live_submit_effective
            else None
        )
        submit_adapter = (
            event_bound_live_adapter_from_trade_conn(
                trade_conn,
                live_cap_conn=conn,
                live_order_schema_initialized=True,
                forecast_conn=forecasts_conn,
                topology_conn=forecasts_conn,
                calibration_conn=conn,
                get_current_level=get_current_level,
                portfolio_state_provider=_portfolio_state_provider,
                real_order_submit_enabled=real_submit_effective,
                durable_submit_outbox_enabled=bool(edli_cfg.get("durable_submit_outbox_enabled", False)),
                replacement_forecast_runtime_flags=replacement_forecast_runtime_flags,
                replacement_forecast_baseline_bundle_provider=replacement_forecast_baseline_bundle_provider,
                replacement_forecast_world_tables=replacement_forecast_world_tables,
                replacement_forecast_source_fact_status=replacement_forecast_source_fact_status,
                replacement_forecast_data_fact_status=replacement_forecast_data_fact_status,
                replacement_forecast_refit_decision=replacement_forecast_refit_decision,
                replacement_forecast_promotion_evidence=replacement_forecast_promotion_evidence,
                replacement_forecast_capital_objective_evidence=replacement_forecast_capital_objective_evidence,
                pre_submit_authority_provider=_edli_pre_submit_authority_provider_from_book_evidence_conn(
                    trade_conn,
                    edli_cfg,
                    # GATE #84: in live-submit mode the pre-submit authority pulls a
                    # just-in-time live book for the selected candidate so quote_age
                    # reflects observation-to-submit latency, not the venue's coarse
                    # book-change stamp on the shared feasibility feed.
                    book_quote_provider=_live_jit_book_quote_provider,
                ),
                pre_submit_book_quote_provider=_live_jit_book_quote_provider,
                executor_submit=lambda final_intent_cert, execution_command_cert: submit_event_bound_final_intent_via_existing_executor(
                    final_intent_cert=final_intent_cert,
                    execution_command_cert=execution_command_cert,
                    conn=trade_conn,
                    snapshot_conn=trade_conn,
                    decision_time=process_pending_decision_time,
                ),
                operator_arm=operator_arm,
                # Production live scope: forecast and Day0 share the same
                # submit boundary.
                edli_live_scope=edli_live_scope,
                family_snapshot_refresher=_decision_family_snapshot_refresher,
                auction_capital_authority=_auction_capital_authority,
            )
            if (live_submit_effective and operator_arm is not None)
            else event_bound_no_submit_adapter_from_trade_conn(
                trade_conn,
                forecast_conn=forecasts_conn,
                topology_conn=forecasts_conn,
                calibration_conn=conn,
                get_current_level=get_current_level,
                portfolio_state_provider=_portfolio_state_provider,
                replacement_forecast_runtime_flags=replacement_forecast_runtime_flags,
                replacement_forecast_baseline_bundle_provider=replacement_forecast_baseline_bundle_provider,
                replacement_forecast_world_tables=replacement_forecast_world_tables,
                replacement_forecast_source_fact_status=replacement_forecast_source_fact_status,
                replacement_forecast_data_fact_status=replacement_forecast_data_fact_status,
                replacement_forecast_refit_decision=replacement_forecast_refit_decision,
                replacement_forecast_promotion_evidence=replacement_forecast_promotion_evidence,
                replacement_forecast_capital_objective_evidence=replacement_forecast_capital_objective_evidence,
                family_snapshot_refresher=_decision_family_snapshot_refresher,
                # SUBMIT-LANE STAMP: name the live-block cause that selected this lane so a
                # full-pass receipt consumed here can never be confused with a genuine
                # decision-declined no-submit (single source of truth from the selector).
                live_block_cause=_no_submit_live_block_cause,
            )
        )

        reactor = OpportunityEventReactor(
            store,
            source_truth_gate=edli_source_truth_gate,
            executable_snapshot_gate=executable_snapshot_gate_from_trade_conn(
                trade_conn,
                topology_conn=forecasts_conn,
            ),
            riskguard_gate=riskguard_allows_new_entries(get_current_level=get_current_level),
            final_intent_submit=submit_adapter,
            reject=lambda _event, _stage, _reason: None,
            regret_ledger=regret_ledger,
            # ALWAYS-DECIDABLE invariant (operator law 2026-06-12): the reactor refreshes a blocked
            # family's substrate as part of the SAME handling (Build 1 snapshot refresher + Build 2
            # single-family cycle-advance reseed), so requeue-without-refresh is structurally
            # impossible for refreshable substrate classes.
            family_snapshot_refresher=_reactor_family_snapshot_refresher,
            cycle_advance_enqueuer=_reactor_cycle_advance_enqueuer,
            day0_hourly_refresher=_reactor_day0_hourly_refresher,
            # Held-position families are refreshed FIRST (money at risk); NO liquidity ordering
            # (operator correction 2026-06-12). Fail-soft read-only provider on zeus_trades.
            held_family_provider=_edli_reactor_held_family_provider(),
            # Current Gamma-empty/no-listed-market proof terminalizes only the blocked event; a
            # future event for the same family can still process if the venue lists later.
            family_market_absence_provider=_reactor_family_market_absence_provider,
            config=ReactorConfig(
                reactor_mode=reactor_mode,
                real_order_submit_enabled=real_order_submit_enabled,
                # Task #102 book-wide edge-zone admission. Absent key => default
                # False => byte-identical legacy money-path (the operator owns
                # config/settings.json; this reads it without writing it).
                # Scope-aware claim tier. Production live scope makes Day0
                # tradeable and rank as fresh alpha.
                day0_is_tradeable=day0_is_tradeable_for_scope(edli_live_scope),
                # SUBMIT-LANE PERSIST-BOUNDARY INVARIANT (silent-trade-kill antibody
                # 2026-06-12): the SAME operator-arm authority the selector above reads,
                # threaded so the reactor's no-submit persist boundary can recognise a
                # nominally-armed live daemon and refuse to silently book a LIVE-stamped
                # full-pass NO_SUBMIT. Not a second authority — the same flag value.
                edli_live_operator_authorized=_edli_live_operator_authorized,
            ),
        )
        _log_stage("reactor_construct")
        _rr = reactor.process_pending(
            decision_time=process_pending_decision_time,
            limit=proof_limit,
            targeted_event_ids=frozenset(targeted_event_ids),
            targeted_only=producer_fast_path and bool(targeted_event_ids),
        )
        _log_stage("process_pending")
        # Canonical event/finalization truth must commit before the derived status
        # pulse opens independent readers. A slow pulse cannot retain the world
        # writer or leave a targeted winner invisibly stuck in processing.
        conn.commit()
        _rejection_counts = dict(Counter(_rr.rejection_reasons))
        _edli_candidates = int(_rr.proof_accepted + _rr.rejected + _rr.retried + _rr.dead_lettered)
        # FIX-4 (P2): read the per-cycle live-submit and venue-ack counters from
        # the adapter.  The live adapter exposes _live_submit_count and
        # _live_ack_count (mutable 1-element lists) after FIX-4; the no-submit
        # adapter and any legacy adapter do not, so getattr returns [0] for both
        # → live_submit_attempts=0 and live_venue_acks=0 (correct for no-submit
        # cycles).
        # FAIL-SOFT counter read (Copilot PR#404): honor the FIX-4 closure
        # counter when it has the expected 1-element-list shape; any other
        # shape (legacy adapter, int, empty list, None) reads as 0 instead of
        # crashing the status-pulse write.
        _live_submit_count_ref = getattr(submit_adapter, "_live_submit_count", [0])
        try:
            _live_submit_attempts = int(_live_submit_count_ref[0])
        except (TypeError, IndexError, KeyError, ValueError):
            _live_submit_attempts = 0
        _live_ack_count_ref = getattr(submit_adapter, "_live_ack_count", [0])
        _live_venue_acks = int(_live_ack_count_ref[0])
        try:
            from src.observability.status_summary import write_cycle_pulse

            write_cycle_pulse(
                _build_edli_status_pulse(
                    started_at=process_pending_decision_time.isoformat(),
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    candidates=_edli_candidates,
                    processed=int(_rr.processed),
                    proof_accepted=int(_rr.proof_accepted),
                    rejected=int(_rr.rejected),
                    retried=int(_rr.retried),
                    dead_lettered=int(_rr.dead_lettered),
                    rejection_reason_counts=_rejection_counts,
                    risk_level=get_current_level().value,
                    submit_disabled_effective_mode=submit_disabled_effective_mode,
                    live_submit_attempts=_live_submit_attempts,
                    live_venue_acks=_live_venue_acks,
                )
            )
        except Exception as exc:
            _log.error(
                "EDLI reactor: status pulse failed (non-fatal): %s",
                exc,
                exc_info=True,
            )
        _log.info(
            "EDLI reactor cycle result: processed=%d proof_accepted=%d rejected=%d retried=%d dead=%d "
            "claim_lock_bounces=%d reasons=%r",
            _rr.processed, _rr.proof_accepted, _rr.rejected, _rr.retried, _rr.dead_lettered,
            getattr(_rr, "claim_lock_bounces", 0), _rr.rejection_reasons[:8],
        )
    finally:
        try:
            trade_conn.close()
        except NameError:
            pass
        try:
            forecasts_conn.close()
        except NameError:
            pass
        conn.close()
        active_lock.release()
        _start_venue_background_maintenance_after_reactor_if_required()
    return True

def _edli_positive_int_or_unbounded(
    config: dict, key: str, *, default: int, maximum: int
) -> int | None:
    raw = config.get(key, default)
    if raw is False or raw is None:
        raw = default
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"", "false", "none", "default"}:
            raw = default
        elif normalized in {"no_cap", "uncapped", "unbounded", "unlimited"}:
            return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(1, min(maximum, value))

def _edli_expire_unready_forecast_snapshot_pending(
    world_conn,
    forecasts_conn,
    *,
    decision_time: str,
) -> int:
    """Expire replacement FSR rows whose current latest posterior is not spine-ready.

    Pending FSR rows are admission work, not durable facts. Under the replacement lane an
    ``rmf-...`` event is consumable only when the family's latest posterior still matches that
    neutral id and has at least three same-cycle raw_model_forecasts members. If the latest
    posterior has advanced to a cycle without raw-model members, keeping the old pending row
    alive only burns reactor budget and produces MU_SIGMA_NOT_STASHED no-trades.
    """

    try:
        from src.events.triggers.forecast_snapshot_ready import REPLACEMENT_0_1_PRODUCT_ID
    except Exception:  # noqa: BLE001
        return 0
    try:
        rows = world_conn.execute(
            """
            SELECT e.event_id,
                   e.causal_snapshot_id,
                   json_extract(e.payload_json, '$.city') AS city,
                   json_extract(e.payload_json, '$.target_date') AS target_date,
                   json_extract(e.payload_json, '$.metric') AS metric
              FROM opportunity_event_processing p
                   INDEXED BY idx_opportunity_event_processing_status
              JOIN opportunity_events e ON e.event_id = p.event_id
             WHERE p.consumer_name = 'edli_reactor_v1'
               AND p.processing_status = 'pending'
               AND e.event_type = 'FORECAST_SNAPSHOT_READY'
               AND e.causal_snapshot_id LIKE 'rmf-%'
            """
        ).fetchall()
    except Exception:  # noqa: BLE001
        return 0
    expire_ids: list[str] = []
    candidates: list[tuple[str, str, str, str, str]] = []
    for row in rows:
        try:
            event_id = str(row[0] or "")
            causal_snapshot_id = str(row[1] or "")
            city = str(row[2] or "").strip()
            target_date = str(row[3] or "").strip()
            metric = str(row[4] or "").strip()
        except Exception:  # noqa: BLE001
            continue
        if not (event_id and causal_snapshot_id and city and target_date and metric):
            continue
        candidates.append((event_id, causal_snapshot_id, city, target_date, metric))
    if not candidates:
        return 0

    family_keys = sorted({(city, target_date, metric) for _, _, city, target_date, metric in candidates})
    latest_cycle_by_family: dict[tuple[str, str, str], str] = {}
    _FORECAST_FAMILY_CHUNK = 250
    for start in range(0, len(family_keys), _FORECAST_FAMILY_CHUNK):
        chunk = family_keys[start : start + _FORECAST_FAMILY_CHUNK]
        family_values = ",".join("(?, ?, ?)" for _ in chunk)
        params: list[Any] = []
        for city, target_date, metric in chunk:
            params.extend([city, target_date, metric])
        params.extend([REPLACEMENT_0_1_PRODUCT_ID, decision_time, decision_time])
        try:
            latest_rows = forecasts_conn.execute(
                f"""
                WITH families(city, target_date, metric) AS (
                    VALUES {family_values}
                )
                SELECT family.city,
                       family.target_date,
                       family.metric,
                       (
                           SELECT posterior.source_cycle_time
                             FROM forecast_posteriors AS posterior
                            WHERE posterior.product_id = ?
                              AND posterior.runtime_layer = 'live'
                              AND posterior.city = family.city
                              AND posterior.target_date = family.target_date
                              AND posterior.temperature_metric = family.metric
                              AND (
                                  posterior.source_available_at IS NULL
                                  OR posterior.source_available_at <= ?
                              )
                              AND (
                                  posterior.computed_at IS NULL
                                  OR posterior.computed_at <= ?
                              )
                            ORDER BY posterior.source_cycle_time DESC,
                                     posterior.computed_at DESC,
                                     posterior.posterior_id DESC
                            LIMIT 1
                       ) AS source_cycle_time
                  FROM families AS family
                """,
                tuple(params),
            ).fetchall()
        except Exception:  # noqa: BLE001
            continue
        for latest in latest_rows:
            key = (str(latest[0] or ""), str(latest[1] or ""), str(latest[2] or ""))
            if key not in latest_cycle_by_family and latest[3] is not None:
                latest_cycle_by_family[key] = str(latest[3] or "")

    member_count_by_family_cycle: dict[tuple[str, str, str, str], int] = {}
    families_by_cycle_date: dict[str, list[tuple[str, str, str]]] = {}
    for key, source_cycle_time in latest_cycle_by_family.items():
        cycle_date = str(source_cycle_time or "")[:10]
        if len(cycle_date) == 10:
            families_by_cycle_date.setdefault(cycle_date, []).append(key)
    for cycle_date, keys in families_by_cycle_date.items():
        try:
            cycle_start = f"{cycle_date}T00:00:00+00:00"
            cycle_end = f"{(date.fromisoformat(cycle_date) + timedelta(days=1)).isoformat()}T00:00:00+00:00"
        except ValueError:
            continue
        for start in range(0, len(keys), _FORECAST_FAMILY_CHUNK):
            chunk = keys[start : start + _FORECAST_FAMILY_CHUNK]
            family_values = ",".join("(?, ?, ?)" for _ in chunk)
            params: list[Any] = []
            for city, target_date, metric in chunk:
                params.extend([city, target_date, metric])
            params.extend([cycle_start, cycle_end, decision_time])
            try:
                count_rows = forecasts_conn.execute(
                    f"""
                    WITH families(city, target_date, metric) AS (
                        VALUES {family_values}
                    )
                    SELECT family.city,
                           family.target_date,
                           family.metric,
                           (
                               SELECT COUNT(DISTINCT forecast.model)
                                 FROM raw_model_forecasts AS forecast
                                      INDEXED BY idx_raw_model_forecasts_endpoint_family_cycle_members
                                WHERE forecast.endpoint = 'single_runs'
                                  AND forecast.city = family.city
                                  AND forecast.target_date = family.target_date
                                  AND forecast.metric = family.metric
                                  AND forecast.source_cycle_time >= ?
                                  AND forecast.source_cycle_time < ?
                                  AND forecast.source_available_at <= ?
                                  AND forecast.forecast_value_c IS NOT NULL
                           ) AS member_count
                      FROM families AS family
                    """,
                    tuple(params),
                ).fetchall()
            except Exception:  # noqa: BLE001
                continue
            for count_row in count_rows:
                key = (
                    str(count_row[0] or ""),
                    str(count_row[1] or ""),
                    str(count_row[2] or ""),
                    cycle_date,
                )
                member_count_by_family_cycle[key] = int(count_row[3] or 0)

    for event_id, causal_snapshot_id, city, target_date, metric in candidates:
        latest_cycle = latest_cycle_by_family.get((city, target_date, metric))
        if latest_cycle is None:
            expire_ids.append(event_id)
            continue
        cycle_date = str(latest_cycle or "")[:10]
        current_causal = f"rmf-{city}|{target_date}|{metric}|{cycle_date}"
        if len(cycle_date) != 10 or causal_snapshot_id != current_causal:
            expire_ids.append(event_id)
            continue
        member_count = member_count_by_family_cycle.get((city, target_date, metric, cycle_date), 0)
        if member_count < 3:
            expire_ids.append(event_id)
    if not expire_ids:
        return 0
    now = str(decision_time)
    changed = 0
    for start in range(0, len(expire_ids), 250):
        chunk = expire_ids[start : start + 250]
        placeholders = ",".join("?" for _ in chunk)
        cur = world_conn.execute(
            f"""
            UPDATE opportunity_event_processing
               SET processing_status = 'expired',
                   processed_at = ?,
                   updated_at = ?,
                   last_error = 'FORECAST_ADMISSION_EXPIRED:latest_posterior_spine_unavailable'
             WHERE consumer_name = 'edli_reactor_v1'
               AND processing_status = 'pending'
               AND event_id IN ({placeholders})
            """,
            (now, now, *chunk),
        )
        changed += int(cur.rowcount or 0)
    return changed

def _edli_prune_batch_limit(config: dict) -> int:

    from src.main import _edli_bounded_positive_int

    return _edli_bounded_positive_int(
        config,
        "reactor_prune_batch_limit",
        default=5_000,
        maximum=5_000,
    )

def _edli_prune_interval_seconds(config: dict) -> float:
    raw = config.get("reactor_prune_interval_seconds", 60)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 180.0
    return max(0.0, value)

def _edli_prune_budget_seconds(config: dict) -> float:
    raw = config.get("reactor_prune_budget_seconds", 6.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 6.0
    return max(0.0, min(value, 20.0))

def _edli_forecast_snapshot_build_budget_seconds(config: dict) -> float:
    raw = config.get("reactor_forecast_snapshot_build_budget_seconds", 8.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 8.0
    return max(0.0, min(value, 20.0))

def _edli_day0_emit_budget_seconds(config: dict) -> float:
    raw = config.get("reactor_day0_emit_budget_seconds", 8.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 8.0
    return max(0.0, min(value, 20.0))

def _edli_day0_emit_busy_timeout_ms(config: dict) -> int:
    raw = config.get("reactor_day0_emit_busy_timeout_ms", 750)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 750
    return max(1, min(value, 5_000))

def _edli_sqlite_busy_timeout_ms(conn) -> int | None:
    try:
        row = conn.execute("PRAGMA busy_timeout").fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None

def _edli_set_sqlite_busy_timeout_ms(conn, value: int | None) -> None:
    if value is None:
        return
    try:
        conn.execute("PRAGMA busy_timeout = %d" % max(1, int(value)))
    except Exception:  # noqa: BLE001
        pass

def _edli_prune_lock_timeout_seconds(config: dict) -> float:
    raw = config.get("reactor_prune_lock_timeout_seconds", 0.5)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.5
    return max(0.0, min(value, 5.0))

def _edli_prune_busy_timeout_ms(config: dict) -> int:
    raw = config.get("reactor_prune_busy_timeout_ms", 750)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 750
    return max(1, min(value, 5_000))

def _edli_unready_fsr_prune_min_active_pending(config: dict) -> int:
    raw = config.get("reactor_unready_fsr_prune_min_active_pending", 1_000)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 1_000
    return max(1, min(value, 50_000))

def _edli_active_rmf_forecast_snapshot_pending_count(world_conn, *, limit: int) -> int:
    """Count active replacement FSR rows up to ``limit`` for maintenance gating.

    The spine-readiness sweep opens the forecasts DB and cross-checks live
    posteriors/raw members. That is valuable backlog hygiene when the FSR queue
    is large, but it is not a per-cycle decision prerequisite for a tiny active
    set. Keep the gate on the world queue only so small live queues can keep
    reaching candidate evaluation even when forecast ingestion is busy.
    """

    bounded_limit = max(1, min(int(limit or 1), 50_000))
    try:
        row = world_conn.execute(
            """
            SELECT COUNT(*)
              FROM (
                    SELECT 1
                      FROM opportunity_event_processing p
                           INDEXED BY idx_opportunity_event_processing_status
                      JOIN opportunity_events e ON e.event_id = p.event_id
                     WHERE p.consumer_name = 'edli_reactor_v1'
                       AND p.processing_status = 'pending'
                       AND e.event_type = 'FORECAST_SNAPSHOT_READY'
                       AND e.causal_snapshot_id LIKE 'rmf-%'
                     LIMIT ?
                   )
            """,
            (bounded_limit,),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return bounded_limit
    return int(row[0] or 0) if row is not None else 0

_EDLI_LAST_PRUNE_MONOTONIC: float | None = None
_EDLI_DAY0_PAUSE_RECOVERY_PENDING: bool | None = None


def _edli_note_day0_pause_rejection(event_type: str, reason: str) -> None:
    """Schedule the recovery scan only when a durable Day0 pause verdict exists."""

    global _EDLI_DAY0_PAUSE_RECOVERY_PENDING
    if event_type == "DAY0_EXTREME_UPDATED" and (
        "entries_paused" in reason or "pause_entries" in reason
    ):
        _EDLI_DAY0_PAUSE_RECOVERY_PENDING = True


def _edli_prune_pending_working_set(
    store,
    *,
    decision_time: datetime,
    day0_family_admission: _Day0LiveFamilyAdmission | None = None,
    urgent_wake_pending: Callable[[], bool] | None = None,
) -> None:
    """Prune stale/superseded rows before snapshotting the redecision skip set.

    Backlog pruning is maintenance, not trade decision logic. Keep it explicit
    opt-in so a slow sweep cannot pin the reactor worker and stop live candidate
    evaluation.

    R4-b3 extraction (2026-07-08): moved from src/main.py alongside the reactor
    cycle that is its sole caller (``run_edli_event_reactor_cycle``).
    """
    import logging as _logging
    from src.main import (
        _edli_clear_sqlite_progress_handler,
        _edli_install_sqlite_deadline,
        _settings_section,
    )

    _log = _logging.getLogger("zeus.events.reactor")

    global _EDLI_DAY0_PAUSE_RECOVERY_PENDING, _EDLI_LAST_PRUNE_MONOTONIC
    edli_cfg = _settings_section("edli", {})
    # ANTIBODY (2026-06-08, operator directive): the working-set prune is NON-OPTIONAL.
    # It is the ONLY drain of the pending opportunity_event_processing set (archive_expired_
    # candidates + archive_superseded_channel_events). Gating it behind an off-able flag
    # (reactor_prune_enabled, default off) is exactly what let the working set grow unbounded
    # when the flag was off — slowing fetch_pending and (before the market_discovery
    # decoupling) silently collapsing executable-substrate coverage -> zero trades, with
    # nothing connecting cause to effect. A necessary maintenance sweep must not be silently
    # switchable off. It now ALWAYS runs, bounded only by its own interval/batch limits below;
    # the legacy reactor_prune_enabled flag is ignored.
    interval_s = _edli_prune_interval_seconds(edli_cfg)
    now_mono = time.monotonic()
    if (
        interval_s > 0
        and _EDLI_LAST_PRUNE_MONOTONIC is not None
        and now_mono - _EDLI_LAST_PRUNE_MONOTONIC < interval_s
    ):
        return
    _EDLI_LAST_PRUNE_MONOTONIC = now_mono
    batch_limit = _edli_prune_batch_limit(edli_cfg)
    budget_s = _edli_prune_budget_seconds(edli_cfg)
    prune_started = time.monotonic()
    saved_busy_timeout_ms: int | None = None

    try:
        row = store.conn.execute("PRAGMA busy_timeout").fetchone()
        saved_busy_timeout_ms = int(row[0]) if row is not None else None
        store.conn.execute("PRAGMA busy_timeout = %d" % _edli_prune_busy_timeout_ms(edli_cfg))
    except Exception:  # noqa: BLE001
        saved_busy_timeout_ms = None

    def _log_prune_step(step: str, started: float, count: int | None = None) -> None:
        elapsed = time.monotonic() - started
        if elapsed >= 1.0:
            count_suffix = "" if count is None else f" count={count}"
            _log.info("EDLI reactor prune step completed: %s elapsed_s=%.3f%s", step, elapsed, count_suffix)

    def _restore_busy_timeout() -> None:
        nonlocal saved_busy_timeout_ms
        if saved_busy_timeout_ms is None:
            return
        try:
            store.conn.execute("PRAGMA busy_timeout = %d" % saved_busy_timeout_ms)
        except Exception:  # noqa: BLE001
            pass
        saved_busy_timeout_ms = None

    prune_deadline = (prune_started + budget_s) if budget_s > 0 else None
    _edli_install_sqlite_deadline(
        store.conn,
        deadline_monotonic=prune_deadline,
        cancelled=urgent_wake_pending,
    )

    def _budget_exhausted(next_step: str) -> bool:
        if urgent_wake_pending is not None and urgent_wake_pending():
            _log.info(
                "EDLI reactor prune preempted before %s by urgent producer wake",
                next_step,
            )
            _restore_busy_timeout()
            return True
        if budget_s <= 0:
            return False
        elapsed = time.monotonic() - prune_started
        if elapsed < budget_s:
            return False
        _log.warning(
            "EDLI reactor prune budget exhausted before %s elapsed_s=%.3f budget_s=%.3f; "
            "deferring remaining maintenance so the money-path reactor can drain events.",
            next_step,
            elapsed,
            budget_s,
        )
        _restore_busy_timeout()
        return True

    try:
        if _budget_exhausted("archive_orphan_processing_rows"):
            return
        _step_started = time.monotonic()
        _orphan_archived = store.archive_orphan_processing_rows(batch_limit=batch_limit)
        _log_prune_step("archive_orphan_processing_rows", _step_started, _orphan_archived)
        if _orphan_archived:
            _log.info(
                "EDLI reactor: archived %d orphan opportunity_event_processing rows "
                "(missing opportunity_events provenance) → 'expired'; active working "
                "set no longer includes unclaimable IDs (batch_limit=%d)",
                _orphan_archived,
                batch_limit,
            )
    except Exception as _orphan_sweep_exc:  # noqa: BLE001 — fail-soft
        _log.warning(
            "EDLI reactor: archive_orphan_processing_rows sweep failed "
            "(non-fatal; joined readers still ignore orphan IDs): %r",
            _orphan_sweep_exc,
        )

    try:
        if _budget_exhausted("archive_expired_candidates"):
            return
        _step_started = time.monotonic()
        _archived = store.archive_expired_candidates(
            decision_time=decision_time.isoformat(),
            batch_limit=batch_limit,
        )
        _log_prune_step("archive_expired_candidates", _step_started, _archived)
        if _archived:
            _log.info(
                "EDLI reactor: archived %d expired (target-local-day-ended) "
                "candidates → 'expired' (excluded from future scans; batch_limit=%d)",
                _archived,
                batch_limit,
            )
    except Exception as _sweep_exc:  # noqa: BLE001 — fail-soft; read floor still guards
        _log.warning(
            "EDLI reactor: archive_expired_candidates sweep failed (non-fatal; "
            "fetch_pending read floor still drops strictly-past rows): %r",
            _sweep_exc,
    )

    try:
        if _budget_exhausted("archive_unmarketed_day0_events"):
            return
        _step_started = time.monotonic()
        _d0_unmarketed_archived = 0
        if day0_family_admission is not None and day0_family_admission.expiry_safe:
            _d0_unmarketed_archived = store.archive_unmarketed_day0_events(
                admitted_families=set(day0_family_admission.admitted_families),
                normalizer=_substrate_refresh_family_key,
                batch_limit=batch_limit,
            )
        _log_prune_step("archive_unmarketed_day0_events", _step_started, _d0_unmarketed_archived)
        if _d0_unmarketed_archived:
            _log.info(
                "EDLI reactor: expired %d unmarketed DAY0_EXTREME_UPDATED execution "
                "events with no market topology or live exposure; observation provenance "
                "kept, but non-executable Day0 facts no longer claim money-path budget",
                _d0_unmarketed_archived,
            )
    except Exception as _d0_unmarketed_sweep_exc:  # noqa: BLE001 — fail-soft
        _log.warning(
            "EDLI reactor: archive_unmarketed_day0_events sweep failed (non-fatal): %r",
            _d0_unmarketed_sweep_exc,
        )

    try:
        if _budget_exhausted("requeue_processed_day0_entries_paused"):
            return
        _step_started = time.monotonic()
        try:
            from src.control.control_plane import is_entries_paused as _entries_paused_now

            _entries_paused_currently = bool(_entries_paused_now())
        except Exception as _pause_read_exc:  # noqa: BLE001
            _log.warning(
                "EDLI reactor: entries_paused state unavailable during Day0 pause "
                "requeue sweep; skipping recovery this cycle: %r",
                _pause_read_exc,
            )
            _entries_paused_currently = True
        _day0_pause_recovered = 0
        if _entries_paused_currently:
            _EDLI_DAY0_PAUSE_RECOVERY_PENDING = True
        elif _EDLI_DAY0_PAUSE_RECOVERY_PENDING is not False:
            recovery_limit = min(batch_limit, 1000)
            _day0_pause_recovered = store.requeue_processed_day0_entries_paused(
                decision_time=decision_time.isoformat(),
                batch_limit=recovery_limit,
            )
            _EDLI_DAY0_PAUSE_RECOVERY_PENDING = (
                _day0_pause_recovered >= recovery_limit
            )
        _log_prune_step("requeue_processed_day0_entries_paused", _step_started, _day0_pause_recovered)
        if _day0_pause_recovered:
            _log.warning(
                "EDLI reactor: requeued %d DAY0 events whose latest verdict was "
                "entries_paused/pause_entries; same observation facts will re-decide "
                "after the pause cleared",
                _day0_pause_recovered,
            )
    except Exception as _day0_pause_recovery_exc:  # noqa: BLE001 — fail-soft
        _log.warning(
            "EDLI reactor: processed Day0 entries_paused recovery sweep failed "
            "(non-fatal; normal pending events still drain): %r",
            _day0_pause_recovery_exc,
        )

    try:
        if _budget_exhausted("requeue_false_static_venue_close_day0_dead_letters"):
            return
        _step_started = time.monotonic()
        _static_close_recovered = store.requeue_false_static_venue_close_day0_dead_letters(
            decision_time=decision_time.isoformat(),
            batch_limit=min(batch_limit, 1000),
        )
        _log_prune_step(
            "requeue_false_static_venue_close_day0_dead_letters",
            _step_started,
            _static_close_recovered,
        )
        if _static_close_recovered:
            _log.warning(
                "EDLI reactor: requeued %d DAY0 events falsely dead-lettered by "
                "old static F1 venue-close horizon",
                _static_close_recovered,
            )
    except Exception as _static_close_recovery_exc:  # noqa: BLE001 — fail-soft
        _log.warning(
            "EDLI reactor: false static venue-close DAY0 recovery sweep failed "
            "(non-fatal; normal pending events still drain): %r",
            _static_close_recovery_exc,
        )

    try:
        if _budget_exhausted("requeue_false_executable_snapshot_deadline_day0_dead_letters"):
            return
        _step_started = time.monotonic()
        _snapshot_deadline_recovered = (
            store.requeue_false_executable_snapshot_deadline_day0_dead_letters(
                decision_time=decision_time.isoformat(),
                batch_limit=min(batch_limit, 1000),
            )
        )
        _log_prune_step(
            "requeue_false_executable_snapshot_deadline_day0_dead_letters",
            _step_started,
            _snapshot_deadline_recovered,
        )
        if _snapshot_deadline_recovered:
            _log.warning(
                "EDLI reactor: requeued %d DAY0 events falsely dead-lettered by "
                "old executable-snapshot selection-deadline horizon logic",
                _snapshot_deadline_recovered,
            )
    except Exception as _snapshot_deadline_recovery_exc:  # noqa: BLE001 — fail-soft
        _log.warning(
            "EDLI reactor: false executable-snapshot selection-deadline DAY0 "
            "recovery sweep failed (non-fatal; normal pending events still drain): %r",
            _snapshot_deadline_recovery_exc,
        )

    try:
        if _budget_exhausted("archive_superseded_channel_events"):
            return
        _step_started = time.monotonic()
        _ch_archived = store.archive_superseded_channel_events(batch_limit=batch_limit)
        _log_prune_step("archive_superseded_channel_events", _step_started, _ch_archived)
        if _ch_archived:
            _log.info(
                "EDLI reactor: archived %d superseded channel events "
                "(BEST_BID_ASK_CHANGED/BOOK_SNAPSHOT/NEW_MARKET_DISCOVERED) → "
                "'expired'; pending channel-event scan reduced (batch_limit=%d)",
                _ch_archived,
                batch_limit,
            )
    except Exception as _ch_sweep_exc:  # noqa: BLE001 — fail-soft
        _log.warning(
            "EDLI reactor: archive_superseded_channel_events sweep failed "
            "(non-fatal): %r",
            _ch_sweep_exc,
        )

    # DAY0 supersession (2026-06-15): keep only the latest DAY0_EXTREME_UPDATED per
    # (city, target_date, metric). Day0 was in NEITHER drain sweep, so stale duplicates
    # (measured 1972 pending rows / 152 families) piled up at Tier-0 claim priority and
    # starved the tradeable FORECAST_SNAPSHOT_READY (spine) lane to zero decisions.
    # Past-local-day day0 is handled by archive_expired_candidates (now day0-aware).
    try:
        if _budget_exhausted("archive_superseded_day0_events"):
            return
        _step_started = time.monotonic()
        _d0_archived = store.archive_superseded_day0_events(batch_limit=batch_limit)
        _log_prune_step("archive_superseded_day0_events", _step_started, _d0_archived)
        if _d0_archived:
            _log.info(
                "EDLI reactor: archived %d superseded DAY0_EXTREME_UPDATED events "
                "(keep-latest per city/target_date/metric) → 'expired'; Tier-0 day0 "
                "claim backlog drained so tradeable FSR is no longer starved "
                "(batch_limit=%d)",
                _d0_archived,
                batch_limit,
            )
    except Exception as _d0_sweep_exc:  # noqa: BLE001 — fail-soft
        _log.warning(
            "EDLI reactor: archive_superseded_day0_events sweep failed (non-fatal): %r",
            _d0_sweep_exc,
    )

    try:
        if _budget_exhausted("expire_unready_forecast_snapshot_pending"):
            return
        _step_started = time.monotonic()
        _unready_fsr_min_active = _edli_unready_fsr_prune_min_active_pending(edli_cfg)
        _active_rmf_fsr_pending = _edli_active_rmf_forecast_snapshot_pending_count(
            store.conn,
            limit=_unready_fsr_min_active,
        )
        _unready_fsr_archived = 0
        if _active_rmf_fsr_pending >= _unready_fsr_min_active:
            from src.state.db import get_forecasts_connection_read_only as _get_forecasts_ro

            _forecasts_ro = _get_forecasts_ro()
            try:
                _unready_fsr_archived = _edli_expire_unready_forecast_snapshot_pending(
                    store.conn,
                    _forecasts_ro,
                    decision_time=decision_time.astimezone(timezone.utc).isoformat(),
                )
            finally:
                _forecasts_ro.close()
            _log_prune_step(
                "expire_unready_forecast_snapshot_pending",
                _step_started,
                _unready_fsr_archived,
            )
            if _unready_fsr_archived:
                _log.info(
                    "EDLI reactor: expired %d forecast-snapshot pending rows whose latest "
                    "posterior lacks same-cycle raw-model spine members; reactor will not "
                    "spend proof budget on MU_SIGMA_NOT_STASHED candidates",
                    _unready_fsr_archived,
                )
    except Exception as _spine_ready_sweep_exc:  # noqa: BLE001 — fail-soft
        _log.warning(
            "EDLI reactor: replacement FSR spine-readiness sweep failed (non-fatal): %r",
            _spine_ready_sweep_exc,
    )

    try:
        if _budget_exhausted("archive_terminal_last_error_events"):
            return
        _step_started = time.monotonic()
        _terminal_last_error_archived = store.archive_terminal_last_error_events(
            batch_limit=batch_limit,
        )
        _log_prune_step(
            "archive_terminal_last_error_events",
            _step_started,
            _terminal_last_error_archived,
        )
        if _terminal_last_error_archived:
            _log.warning(
                "EDLI reactor: expired %d pending events with terminal durable "
                "last_error verdicts; stale retry debt no longer suppresses fresh "
                "forecast events (batch_limit=%d)",
                _terminal_last_error_archived,
                batch_limit,
            )
    except Exception as _terminal_last_error_sweep_exc:  # noqa: BLE001 — fail-soft
        _log.warning(
            "EDLI reactor: terminal last_error sweep failed (non-fatal): %r",
            _terminal_last_error_sweep_exc,
    )

    try:
        if _budget_exhausted("archive_recent_no_value_refuted_events"):
            return
        _step_started = time.monotonic()
        _no_value_refuted_archived = store.archive_recent_no_value_refuted_events(
            decision_time=decision_time.astimezone(timezone.utc).isoformat(),
            batch_limit=batch_limit,
        )
        _log_prune_step("archive_recent_no_value_refuted_events", _step_started, _no_value_refuted_archived)
        if _no_value_refuted_archived:
            _log.info(
                "EDLI reactor: expired %d already-queued FSR/Day0 events refuted by "
                "same-evidence terminal no-trade receipts; reactor proof budget no "
                "longer replays known no-value families (batch_limit=%d)",
                _no_value_refuted_archived,
                batch_limit,
            )
    except Exception as _no_value_refutation_sweep_exc:  # noqa: BLE001 — fail-soft
        _log.warning(
            "EDLI reactor: recent no-value refutation sweep failed (non-fatal): %r",
            _no_value_refutation_sweep_exc,
    )

    try:
        if _budget_exhausted("ignore_channel_cache_events"):
            return
        _step_started = time.monotonic()
        _ch_ignored = store.ignore_channel_cache_events(batch_limit=batch_limit)
        _log_prune_step("ignore_channel_cache_events", _step_started, _ch_ignored)
        if _ch_ignored:
            _log.info(
                "EDLI reactor: ignored %d channel cache events "
                "(BEST_BID_ASK_CHANGED/BOOK_SNAPSHOT/NEW_MARKET_DISCOVERED) after "
                "quote-cache/feasibility ingestion; excluded from submit reactor "
                "working set (batch_limit=%d)",
                _ch_ignored,
                batch_limit,
            )
    except Exception as _ch_ignore_exc:  # noqa: BLE001 — fail-soft
        _log.warning(
            "EDLI reactor: ignore_channel_cache_events sweep failed "
            "(non-fatal): %r",
            _ch_ignore_exc,
    )

    try:
        if _budget_exhausted("archive_invalid_forecast_snapshot_events"):
            return
        _step_started = time.monotonic()
        _invalid_fsr_archived = store.archive_invalid_forecast_snapshot_events(
            batch_limit=batch_limit,
        )
        _log_prune_step("archive_invalid_forecast_snapshot_events", _step_started, _invalid_fsr_archived)
        if _invalid_fsr_archived:
            _log.info(
                "EDLI reactor: archived %d invalid forecast-snapshot/redecision "
                "events with impossible carrier counts → 'expired'; malformed live "
                "carriers removed from active decision working set (batch_limit=%d)",
                _invalid_fsr_archived,
                batch_limit,
            )
    except Exception as _invalid_fsr_sweep_exc:  # noqa: BLE001 — fail-soft
        _log.warning(
            "EDLI reactor: archive_invalid_forecast_snapshot_events sweep failed "
            "(non-fatal): %r",
            _invalid_fsr_sweep_exc,
    )

    try:
        if _budget_exhausted("archive_superseded_forecast_snapshot_events"):
            return
        _step_started = time.monotonic()
        _fsr_archived = store.archive_superseded_forecast_snapshot_events(
            batch_limit=batch_limit,
        )
        _log_prune_step("archive_superseded_forecast_snapshot_events", _step_started, _fsr_archived)
        if _fsr_archived:
            _log.info(
                "EDLI reactor: archived %d superseded forecast-snapshot redecision "
                "events → 'expired'; newest active event per forecast family retained "
                "(batch_limit=%d)",
                _fsr_archived,
                batch_limit,
            )
    except Exception as _fsr_sweep_exc:  # noqa: BLE001 — fail-soft
        _log.warning(
            "EDLI reactor: archive_superseded_forecast_snapshot_events sweep failed "
            "(non-fatal): %r",
            _fsr_sweep_exc,
        )
    finally:
        _edli_clear_sqlite_progress_handler(store.conn)
        _restore_busy_timeout()

def _reactor_day0_hourly_refresh_interval_seconds() -> float:
    raw = os.environ.get(
        "ZEUS_REACTOR_DAY0_HOURLY_REFRESH_INTERVAL_SECONDS",
        "300.0",
    )
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return 300.0

def _reactor_day0_hourly_refresh_budget_seconds() -> float:
    raw = os.environ.get(
        "ZEUS_REACTOR_DAY0_HOURLY_REFRESH_BUDGET_SECONDS",
        os.environ.get("ZEUS_DAY0_HOURLY_REFRESH_BUDGET_SECONDS", "2.5"),
    )
    try:
        return max(0.25, float(raw))
    except (TypeError, ValueError):
        return 2.5

def _reactor_day0_hourly_fetch_timeout_seconds() -> float:
    raw = os.environ.get(
        "ZEUS_REACTOR_DAY0_HOURLY_FETCH_TIMEOUT_SECONDS",
        os.environ.get("ZEUS_DAY0_HOURLY_FETCH_TIMEOUT_SECONDS", "1.5"),
    )
    try:
        return max(0.25, float(raw))
    except (TypeError, ValueError):
        return 1.5

def _edli_emit_day0_extreme_events(
    world_conn,
    trade_conn,
    *,
    decision_time: datetime,
    received_at: str,
    limit: int,
    day0_is_tradeable: bool = True,
    budget_seconds: float | None = None,
    family_admission: _Day0LiveFamilyAdmission | None = None,
    urgent_wake_pending: Callable[[], bool] | None = None,
) -> int:
    """Emit DB-only Day0 catch-up events.

    The data-ingest source clock exclusively owns fast METAR capture and direct
    event emission. This reactor path scans only durable authority and
    observation-instant state while holding the world-write mutex.
    """
    import logging as _logging
    from src.main import (
        _edli_clear_sqlite_progress_handler,
        _edli_install_sqlite_deadline,
        _settings_section,
    )
    _log = _logging.getLogger("zeus.events.reactor")

    from src.events.event_writer import EventWriter
    from src.events.triggers.day0_extreme_updated import Day0ExtremeUpdatedTrigger

    deadline_monotonic = (
        time.monotonic() + float(budget_seconds)
        if budget_seconds is not None and float(budget_seconds) > 0
        else None
    )
    edli_cfg = _settings_section("edli", {})
    day0_busy_timeout_ms = _edli_day0_emit_busy_timeout_ms(edli_cfg)
    saved_world_busy_timeout_ms = _edli_sqlite_busy_timeout_ms(world_conn)
    saved_trade_busy_timeout_ms = _edli_sqlite_busy_timeout_ms(trade_conn)
    _edli_set_sqlite_busy_timeout_ms(world_conn, day0_busy_timeout_ms)
    _edli_set_sqlite_busy_timeout_ms(trade_conn, day0_busy_timeout_ms)
    _edli_install_sqlite_deadline(
        world_conn,
        deadline_monotonic=deadline_monotonic,
        cancelled=urgent_wake_pending,
    )
    _edli_install_sqlite_deadline(
        trade_conn,
        deadline_monotonic=deadline_monotonic,
        cancelled=urgent_wake_pending,
    )
    try:
        trigger = Day0ExtremeUpdatedTrigger(
            EventWriter(world_conn),
            day0_is_tradeable=day0_is_tradeable,
            suppress_recent_no_value_refutations=True,
            family_admission=family_admission,
            scan_cities=(
                family_admission.scan_cities
                if family_admission is not None
                else None
            ),
        )
        authority_results, observation_results = _edli_scan_day0_with_lock_retry(
            trigger=trigger,
            world_conn=world_conn,
            trade_conn=trade_conn,
            decision_time=decision_time,
            received_at=received_at,
            limit=limit,
        )
        _log.info(
            "EDLI day0 catch-up emit: day0_authority_emitted=%d "
            "day0_observation_instants_emitted=%d admitted_families=%d",
            len(authority_results),
            len(observation_results),
            0 if family_admission is None else len(family_admission.admitted_families),
        )
        return len(authority_results) + len(observation_results)
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            if urgent_wake_pending is not None and urgent_wake_pending():
                _log.info("EDLI day0 catch-up preempted by urgent producer wake")
            else:
                _log.warning(
                    "EDLI day0 emit budget exhausted after %.3fs; skipping remaining "
                    "Day0 catch-up this cycle and draining already-queued candidates.",
                    float(budget_seconds or 0.0),
                )
            return 0
        raise
    finally:
        _edli_clear_sqlite_progress_handler(trade_conn)
        _edli_clear_sqlite_progress_handler(world_conn)
        _edli_set_sqlite_busy_timeout_ms(trade_conn, saved_trade_busy_timeout_ms)
        _edli_set_sqlite_busy_timeout_ms(world_conn, saved_world_busy_timeout_ms)

def _edli_scan_day0_with_lock_retry(
    *,
    trigger,
    world_conn,
    trade_conn,
    decision_time: datetime,
    received_at: str,
    limit: int,
) -> tuple[list, list]:

    import logging as _logging
    from src.main import _edli_is_sqlite_lock_error
    _log = _logging.getLogger("zeus.events.reactor")

    import sqlite3

    retry_delays = _edli_day0_emit_lock_retry_delays()
    for attempt in range(1, len(retry_delays) + 2):
        try:
            authority_results = trigger.scan_authority_rows(
                observation_conn=trade_conn,
                settlement_semantics=_edli_day0_settlement_semantics,
                decision_time=decision_time,
                received_at=received_at,
                limit=limit,
            )
            observation_results = trigger.scan_observation_instants_rows(
                observation_conn=world_conn,
                settlement_semantics=_edli_day0_settlement_semantics,
                decision_time=decision_time,
                received_at=received_at,
                limit=limit,
            )
            return authority_results, observation_results
        except sqlite3.OperationalError as exc:
            if not _edli_is_sqlite_lock_error(exc) or attempt > len(retry_delays):
                raise
            _log.warning(
                "EDLI day0 emit hit transient world-DB lock; retrying in %.1fs "
                "(attempt %d/%d): %r",
                retry_delays[attempt - 1],
                attempt,
                len(retry_delays) + 1,
                exc,
            )
            time.sleep(retry_delays[attempt - 1])
    raise RuntimeError("unreachable day0 emit retry state")

def _edli_day0_emit_lock_retry_delays() -> tuple[float, ...]:
    raw = os.environ.get("ZEUS_DAY0_EMIT_LOCK_RETRY_SECONDS", "1.0,2.0")
    delays: list[float] = []
    for piece in raw.split(","):
        text = piece.strip()
        if not text:
            continue
        try:
            delay_s = float(text)
        except ValueError:
            continue
        if delay_s > 0:
            delays.append(min(delay_s, 10.0))
    return tuple(delays)

def _edli_day0_settlement_semantics(observation: dict):
    """Resolve Day0 settlement semantics from authority payload fields."""

    from src.contracts.settlement_semantics import SettlementSemantics

    station = str(observation.get("station_id") or observation.get("city") or "UNKNOWN")
    unit = str(observation.get("settlement_unit") or "F").upper()
    rounding_rule = str(observation.get("rounding_rule") or "wmo_half_up")
    precision_raw = observation.get("settlement_precision")
    try:
        precision = float(precision_raw) if precision_raw is not None else 1.0
    except (TypeError, ValueError):
        precision = 1.0
    if unit not in {"F", "C"}:
        unit = "F"
    if rounding_rule not in {"wmo_half_up", "floor", "ceil", "oracle_truncate"}:
        rounding_rule = "wmo_half_up"
    return SettlementSemantics(
        resolution_source=f"EDLI_DAY0_{station}",
        measurement_unit=unit,
        precision=precision,
        rounding_rule=rounding_rule,
        finalization_time="12:00:00Z",
    )

def _edli_pre_submit_inner_io_timeout_seconds() -> float:
    """Network timeout used inside the outer pre-submit timeout guard.

    The outer guard is a daemon-protection circuit breaker.  Inner venue/RPC
    calls must time out first; otherwise the guard returns while the worker
    thread keeps blocking in TLS/SDK I/O and the live reactor eventually skips
    cycles with leaked pre-submit workers.
    """
    import logging as _logging
    from src.main import _edli_pre_submit_clob_timeout_seconds
    _log = _logging.getLogger("zeus.events.reactor")

    outer = _edli_pre_submit_clob_timeout_seconds()
    raw = os.environ.get("ZEUS_PRE_SUBMIT_INNER_IO_TIMEOUT_SECONDS")
    if raw not in (None, ""):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            _log.warning(
                "Invalid ZEUS_PRE_SUBMIT_INNER_IO_TIMEOUT_SECONDS=%r; deriving from outer timeout",
                raw,
            )
        else:
            if value > 0 and (value * 2.0) < outer:
                return value
            _log.warning(
                "Invalid ZEUS_PRE_SUBMIT_INNER_IO_TIMEOUT_SECONDS=%r; must be positive and < half outer timeout %.3fs",
                raw,
                outer,
            )
    return max(0.01, min(2.0, outer * 0.35))

def _edli_run_pre_submit_clob_call(label: str, fn, *, seconds: float | None = None):

    from src.main import _edli_pre_submit_clob_timeout_seconds

    from src.runtime.timeout_guard import run_with_timeout

    return run_with_timeout(
        fn,
        seconds=seconds if seconds is not None else _edli_pre_submit_clob_timeout_seconds(),
        label=f"pre_submit_{label}",
    )

def _edli_pre_submit_jit_outer_timeout_seconds() -> float:

    import logging as _logging
    from src.main import _edli_pre_submit_clob_timeout_seconds
    _log = _logging.getLogger("zeus.events.reactor")

    raw = os.environ.get("ZEUS_PRE_SUBMIT_JIT_OUTER_TIMEOUT_SECONDS")
    if raw not in (None, ""):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            _log.warning(
                "Invalid ZEUS_PRE_SUBMIT_JIT_OUTER_TIMEOUT_SECONDS=%r; using strict default",
                raw,
            )
        else:
            if value > 0:
                return min(value, _edli_pre_submit_clob_timeout_seconds())
    # The submit-time JIT /book read is the primary pre-submit book authority.
    # The former 1.6s cap was below observed live CLOB tail latency and caused
    # armed live cycles to fall back to stale DB rows, globally blocking orders.
    # Keep a small reserve for the post-book provenance/balance checks while
    # still letting a warm public /book request complete under the outer guard.
    outer = _edli_pre_submit_clob_timeout_seconds()
    return max(0.25, min(4.5, outer * 0.85))

def _edli_fresh_projected_pre_submit_book(
    trade_conn,
    token_id: str,
    *,
    max_quote_age_ms: int,
):
    """Return one fresh durable WS book without fabricating observation time."""

    if trade_conn is None or max_quote_age_ms <= 0:
        return None
    try:
        row = trade_conn.execute(
            """
            SELECT snapshot.orderbook_depth_json,
                   snapshot.captured_at,
                   latest.freshness_deadline
              FROM executable_market_snapshot_latest AS latest
                   INDEXED BY idx_snapshot_latest_selected_token_captured
              JOIN executable_market_snapshots AS snapshot
                ON snapshot.snapshot_id = latest.snapshot_id
             WHERE latest.selected_outcome_token_id = ?
            """,
            (str(token_id),),
        ).fetchone()
        if row is None:
            return None
        captured_at = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
        freshness_deadline = datetime.fromisoformat(
            str(row[2]).replace("Z", "+00:00")
        )
        if captured_at.tzinfo is None or freshness_deadline.tzinfo is None:
            return None
        captured_at = captured_at.astimezone(timezone.utc)
        freshness_deadline = freshness_deadline.astimezone(timezone.utc)
        checked_at = datetime.now(timezone.utc)
        age_ms = (checked_at - captured_at).total_seconds() * 1000.0
        if (
            age_ms < 0.0
            or age_ms > float(max_quote_age_ms)
            or freshness_deadline < checked_at
        ):
            return None
        book = json.loads(str(row[0] or ""))
        if (
            not isinstance(book, Mapping)
            or str(book.get("asset_id") or book.get("token_id") or "").strip()
            != str(token_id)
            or not str(book.get("hash") or "").strip()
            or not isinstance(book.get("bids"), list)
            or not isinstance(book.get("asks"), list)
        ):
            return None
        return dict(book), captured_at, "price_channel_projection"
    except (json.JSONDecodeError, sqlite3.Error, TypeError, ValueError):
        return None


def _edli_continuity_proven_pre_submit_book(
    trade_conn,
    token_id: str,
    *,
    max_quote_age_ms: int,
    continuity_path: Path | None = None,
):
    """Return the current-generation WS depth while its continuity proof is live."""

    if trade_conn is None or max_quote_age_ms <= 0:
        return None
    try:
        if continuity_path is None:
            from src.config import state_path

            continuity_path = state_path(MARKET_CHANNEL_CONTINUITY_FILENAME)
        proof = json.loads(continuity_path.read_text(encoding="utf-8"))
        if not isinstance(proof, dict):
            return None
        connected_at = _parse_utc_instant(proof.get("connected_at"))
        observed_at = _parse_utc_instant(proof.get("observed_at"))
        checked_at = datetime.now(timezone.utc)
        if (
            proof.get("schema_version") != 1
            or proof.get("channel") != "market_channel"
            or proof.get("connected") is not True
            or connected_at is None
            or observed_at is None
            or connected_at > observed_at
        ):
            return None
        proof_age_ms = (checked_at - observed_at).total_seconds() * 1000.0
        if proof_age_ms < 0.0 or proof_age_ms > float(max_quote_age_ms):
            return None
        row = trade_conn.execute(
            """
            SELECT quote_seen_at,
                   book_hash_before,
                   depth_before_json
              FROM execution_feasibility_latest
             WHERE token_id = ?
               AND direction IN ('buy_yes', 'buy_no')
             ORDER BY quote_seen_at DESC
             LIMIT 1
            """,
            (str(token_id),),
        ).fetchone()
        if row is None or row[2] in (None, ""):
            return None
        quote_seen_at = _parse_utc_instant(row[0])
        if (
            quote_seen_at is None
            or quote_seen_at < connected_at
            or quote_seen_at > observed_at
        ):
            return None
        depth = json.loads(str(row[2]))
        book_hash = str(row[1] or "").strip()
        if (
            not isinstance(depth, Mapping)
            or not isinstance(depth.get("bids"), list)
            or not isinstance(depth.get("asks"), list)
            or not book_hash
        ):
            return None
        return (
            {
                "asset_id": str(token_id),
                "hash": book_hash,
                "bids": list(depth["bids"]),
                "asks": list(depth["asks"]),
            },
            observed_at,
            "price_channel_continuity",
        )
    except (json.JSONDecodeError, OSError, sqlite3.Error, TypeError, ValueError):
        return None


def _edli_pre_submit_jit_book_quote_provider(
    trade_conn=None,
    *,
    max_quote_age_ms: int = 1000,
    continuity_path: Path | None = None,
):
    """Build the selected-token pre-submit book authority (GATE #84).

    A sub-SLO durable price-channel projection is already current venue truth;
    otherwise the existing warm ``/book`` fetch remains the fail-closed fallback.

    Uses a WARM, REUSED client (see ``_edli_pre_submit_jit_clob_client``) so the
    TLS connection stays warm across submit candidates instead of paying a cold
    handshake per call. A failed fetch propagates to the caller, which returns
    ``None`` and falls back/requeues; httpx reopens a fresh pooled connection on
    the next fetch, so a transiently-dead socket costs at most one requeue.
    """

    last_observation = {}

    def _fetch(token_id: str):
        projected = _edli_continuity_proven_pre_submit_book(
            trade_conn,
            token_id,
            max_quote_age_ms=max_quote_age_ms,
            continuity_path=continuity_path,
        )
        if projected is None:
            projected = _edli_fresh_projected_pre_submit_book(
                trade_conn,
                token_id,
                max_quote_age_ms=max_quote_age_ms,
            )
        if projected is not None:
            last_observation[str(token_id)] = projected
            return projected
        clob = _edli_pre_submit_jit_clob_client()
        book = _edli_run_pre_submit_clob_call(
            "jit_book",
            lambda: clob.get_orderbook_snapshot(token_id),
            seconds=_edli_pre_submit_jit_outer_timeout_seconds(),
        )
        observation = (
            (book, datetime.now(timezone.utc), "clob_jit_book")
            if trade_conn is not None
            else book
        )
        last_observation[str(token_id)] = observation
        return observation

    def _consume_last(token_id: str):
        return last_observation.pop(str(token_id), None)

    _fetch.consume_last = _consume_last

    return _fetch

def _edli_decision_family_snapshot_refresher(topology_conn):
    """Build the decision-triggered targeted executable-substrate refresher.

    Broad warming remains sidecar-owned. When the selected row is already stale
    inside the money path, the decision needs a current executable book, not just
    an asynchronous marker. The refresher first asks the substrate producer for
    an exact scoped refresh under the shared cross-process lock. Only a failed
    inline refresh leaves a priority marker for sidecar retry, so the fallback
    cannot take the lock from the selected winner. It returns True only after a
    fresh condition read proves the selected scope is current.
    """
    import logging as _logging
    _log = _logging.getLogger("zeus.events.reactor")

    def _refresh(
        *,
        city,
        target_date,
        metric,
        condition_ids=(),
        selected_token_id=None,
        force_refresh=False,
    ):
        family = (
            str(city or "").strip(),
            str(target_date or "").strip(),
            str(metric or "").strip(),
        )
        if not family[0] or not family[1] or family[2] not in {"high", "low"}:
            return False
        clean_condition_ids = tuple(
            str(condition_id or "").strip()
            for condition_id in (condition_ids or ())
            if str(condition_id or "").strip()
        )
        summary = None
        try:
            from src.data.substrate_observer import refresh_money_path_substrate_now

            refresh_budget_s = max(
                1.0,
                float(os.environ.get("ZEUS_DECISION_TARGETED_REFRESH_BUDGET_SECONDS", "8.0")),
            )
            snapshot_reserve_s = min(
                max(
                    0.5,
                    float(
                        os.environ.get(
                            "ZEUS_DECISION_TARGETED_REFRESH_SNAPSHOT_RESERVE_SECONDS",
                            "2.0",
                        )
                    ),
                ),
                max(0.1, refresh_budget_s - 0.1),
            )
            summary = refresh_money_path_substrate_now(
                families=[family],
                condition_ids=clean_condition_ids,
                reason="decision_triggered_targeted_refresh",
                refresh_budget_seconds=refresh_budget_s,
                snapshot_reserve_seconds=snapshot_reserve_s,
                include_money_risk_families=False,
                force_refresh=bool(force_refresh),
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "decision family refresh: inline substrate refresh failed for %s/%s/%s: %r",
                family[0],
                family[1],
                family[2],
                exc,
            )
            summary = {"status": "inline_error", "error": str(exc)}
        proved_fresh = False
        if clean_condition_ids:
            try:
                refresh_completed = (
                    (
                        str((summary or {}).get("status") or "") == "refreshed"
                        and int((summary or {}).get("forced_condition_count") or 0)
                        == len(clean_condition_ids)
                        and int((summary or {}).get("attempted") or 0) >= 2
                        and int((summary or {}).get("inserted") or 0) >= 2
                        and int((summary or {}).get("prefetched_orderbook_count") or 0) >= 2
                    )
                    if force_refresh
                    else True
                )
                proved_fresh = refresh_completed and family in (
                    _edli_families_with_fresh_scoped_executable_substrate(
                        {family: set(clean_condition_ids)},
                        now_utc=datetime.now(timezone.utc),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                _log.debug(
                    "decision family refresh: scoped freshness proof failed for %s/%s/%s: %r",
                    family[0],
                    family[1],
                    family[2],
                    exc,
                )
                proved_fresh = False
        else:
            status = str((summary or {}).get("status") or "")
            proved_fresh = status in {"refreshed", "all_fresh"}
        if not proved_fresh:
            try:
                from src.data.substrate_priority import (
                    mark_money_path_substrate_priority,
                )

                mark_money_path_substrate_priority(
                    reason="decision_triggered_targeted_refresh",
                    ttl_seconds=45.0,
                    families=[family],
                    condition_ids=condition_ids,
                    force_refresh_condition_ids=(condition_ids if force_refresh else ()),
                    merge_existing=not force_refresh,
                )
            except Exception as exc:  # noqa: BLE001
                _log.debug(
                    "decision family refresh: priority marker write failed: %r",
                    exc,
                )
        _log.info(
            "decision family refresh completed: %s/%s/%s condition_ids=%d "
            "proved_fresh=%s summary=%r",
            family[0],
            family[1],
            family[2],
            len(clean_condition_ids),
            proved_fresh,
            summary,
        )
        return bool(proved_fresh)

    return _refresh

def _edli_reactor_day0_hourly_refresher():
    """Build the reactor-drain refresher for Day0 remaining-day weather vectors."""
    import logging as _logging
    _log = _logging.getLogger("zeus.events.reactor")

    def _refresh(*, city, target_date, metric, **_ignored):
        family = (
            str(city or "").strip(),
            str(target_date or "").strip(),
            str(metric or "").strip(),
        )
        if not family[0] or not family[1] or family[2] not in {"high", "low"}:
            return False
        try:
            from src.config import runtime_cities_by_name
            from src.data.day0_hourly_vectors import maybe_refresh_day0_hourly_vectors

            city_obj = runtime_cities_by_name().get(family[0])
            if city_obj is None:
                _log.warning(
                    "reactor day0-hourly refresh skipped: city config missing for %s/%s/%s",
                    family[0],
                    family[1],
                    family[2],
                )
                return False
            stats = maybe_refresh_day0_hourly_vectors(
                [city_obj],
                decision_time=datetime.now(timezone.utc),
                interval_s=_reactor_day0_hourly_refresh_interval_seconds(),
                budget_s=_reactor_day0_hourly_refresh_budget_seconds(),
                max_cities=1,
                timeout_s=_reactor_day0_hourly_fetch_timeout_seconds(),
                persist_lock_blocking=False,
                return_stats=True,
            )
            vectors_written = int(getattr(stats, "vectors_written", stats) or 0)
            cities_attempted = int(getattr(stats, "cities_attempted", 0) or 0)
            _log.info(
                "reactor day0-hourly refresh attempted for %s/%s/%s: vectors_written=%d "
                "cities_attempted=%d incomplete_expected_bundles=%d",
                family[0],
                family[1],
                family[2],
                vectors_written,
                cities_attempted,
                int(getattr(stats, "incomplete_expected_bundles", 0) or 0),
            )
            return vectors_written > 0
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "reactor day0-hourly refresh failed for %s/%s/%s (fail-soft): %r",
                family[0],
                family[1],
                family[2],
                exc,
            )
            return False

    return _refresh

def _edli_reactor_family_market_absence_provider():
    """Build the reactor's live venue-listing absence proof provider.

    The only authority this provider exposes is durable market-unavailable
    evidence written by the substrate-observer sidecar. Plain missing topology,
    lock-busy, time-boxed probes, or network errors do not write that evidence
    and therefore do not terminalize reactor events.
    """

    def _is_absent(*, city, target_date, metric, **_ignored):
        try:
            from src.data.market_absence_evidence import has_recent_market_unavailable_evidence

            return has_recent_market_unavailable_evidence(
                city=city,
                target_date=target_date,
                metric=metric,
            )
        except Exception:
            return False

    return _is_absent

def _edli_reactor_cycle_advance_enqueuer():
    """ALWAYS-DECIDABLE invariant — Build 2 (operator law 2026-06-12). Build the single-family
    cycle-advance reseed enqueuer the reactor invokes when a family is blocked on a STALE/absent
    replacement posterior. Reuses the SAME cycle-advance re-materialization lane (seed builder +
    seed_dir the materialize cycle drains + idempotency marker) scoped to ONE family.

    Reads forecast_db / seed_dir / raw_manifest_dir from the live materialization queue config
    (the same source the poll-lane batch trigger uses). Returns None when the lane is not
    configured (no seed_dir) so the reactor simply skips the enqueue (fail-soft). The callable is
    fail-soft itself: any error returns a status dict, never raises into the reactor cycle.

    LOCK LAW: the enqueuer opens its OWN short-lived forecast-DB write connection inside the
    single-family function; the reactor invokes it from the end-of-cycle drain where NO per-event
    world/trade txn is open — no DB connection is held across this call from the reactor side.
    """
    from src.data.replacement_forecast_production import (
        _replacement_forecast_live_materialization_queue_config,
    )

    cfg = _replacement_forecast_live_materialization_queue_config()
    forecast_db = cfg.get("forecast_db")
    seed_dir = cfg.get("seed_dir")
    raw_manifest_dir = cfg.get("raw_manifest_dir")
    if forecast_db is None or seed_dir is None or raw_manifest_dir is None:
        return None

    def _enqueue(*, city, target_date, metric):
        from src.data.replacement_cycle_advance_trigger import (
            enqueue_single_family_cycle_advance_reseed,
        )

        report = enqueue_single_family_cycle_advance_reseed(
            forecast_db=Path(str(forecast_db)),
            seed_dir=Path(str(seed_dir)),
            raw_manifest_dir=Path(str(raw_manifest_dir)),
            city=city,
            target_date=target_date,
            metric=metric,
        )
        return bool(report.get("enqueued"))

    return _enqueue

def _edli_pre_submit_book_from_jit_fetch(
    book_quote_provider,
    *,
    token_id: str,
    side: str | None = None,
    limit_price: float | None = None,
    size: float | None = None,
    post_only: bool = False,
):
    """JIT single-token book fetch for the SELECTED candidate at submit time.

    GATE #84 root cause: the shared market-channel feasibility feed stamps
    ``quote_seen_at`` with the venue book-CHANGE timestamp (1s resolution, often
    minutes stale for slow weather books), and only refreshes a given token when
    its WS tick arrives (median per-candidate gap ~11s). The 1000ms pre-submit
    bound is a SUBMIT-TIME observation-freshness bound, so for the one selected
    candidate we pull its live book ``now`` and anchor freshness to OUR
    observation time — the FOK crosses against exactly this book.

    Returns ``(best_bid, best_ask, book_hash, observed_at, authority_id)`` on a
    usable executable book, or ``None`` only when the fetch itself fails. The
    caller treats that as a hard no-submit: cached feasibility rows cannot prove
    submit-time truth. For a taker limit intent, ``size`` is ConditionalToken
    shares, so the same JIT response must cover that share count at prices within
    the limit.
    """
    import logging as _logging
    _log = _logging.getLogger("zeus.events.reactor")

    if book_quote_provider is None:
        return None
    try:
        response = book_quote_provider(token_id)
        book_authority_id = "clob_jit_book"
        observed_at = None
        if isinstance(response, tuple):
            if len(response) != 3:
                raise ValueError("PRE_SUBMIT_BOOK_AUTHORITY_JIT_RESPONSE_INVALID")
            response, observed_at, book_authority_id = response
        message = dict(response)
    except Exception as exc:  # noqa: BLE001 - JIT fetch failure must not fabricate freshness
        _log.warning("EDLI pre-submit JIT book fetch failed for %s: %s", token_id, exc)
        return None
    if observed_at is not None:
        if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
            raise ValueError("PRE_SUBMIT_BOOK_AUTHORITY_JIT_TIME_INVALID")
        observed_at = observed_at.astimezone(timezone.utc)
        if observed_at > datetime.now(timezone.utc):
            raise ValueError("PRE_SUBMIT_BOOK_AUTHORITY_JIT_TIME_FROM_FUTURE")
    if not str(book_authority_id or "").strip():
        raise ValueError("PRE_SUBMIT_BOOK_AUTHORITY_JIT_SOURCE_MISSING")
    response_token_id = str(message.get("asset_id") or message.get("token_id") or "").strip()
    if not response_token_id or response_token_id != str(token_id):
        raise ValueError(
            "PRE_SUBMIT_BOOK_AUTHORITY_JIT_TOKEN_MISMATCH:"
            f"requested_token_id={token_id}:response_token_id={response_token_id or 'missing'}"
        )
    best_bid = _edli_book_best_price(message.get("bids"), best="bid")
    best_ask = _edli_book_best_price(message.get("asks"), best="ask")
    book_hash = str(message.get("hash") or "")
    normalized_side = str(side or "").upper()
    if normalized_side not in {"BUY", "SELL"}:
        raise ValueError(
            "PRE_SUBMIT_BOOK_AUTHORITY_JIT_SIDE_INVALID:"
            f"token_id={token_id}:side={normalized_side or 'missing'}"
        )
    if limit_price is not None and (
        not math.isfinite(float(limit_price))
        or not 0.0 < float(limit_price) <= 1.0
    ):
        raise ValueError(
            "PRE_SUBMIT_BOOK_AUTHORITY_JIT_LIMIT_INVALID:"
            f"token_id={token_id}:limit_price={limit_price}"
        )
    if size is not None and (
        not math.isfinite(float(size)) or float(size) <= 0.0
    ):
        raise ValueError(
            "PRE_SUBMIT_BOOK_AUTHORITY_JIT_SIZE_INVALID:"
            f"token_id={token_id}:size={size}"
        )
    if normalized_side == "BUY" and best_ask is None:
        raise ValueError(
            "PRE_SUBMIT_BOOK_AUTHORITY_JIT_BUY_ASK_MISSING:"
            f"token_id={token_id}:book_hash={book_hash or 'missing'}:best_bid={best_bid}"
        )
    if normalized_side == "SELL" and best_bid is None:
        raise ValueError(
            "PRE_SUBMIT_BOOK_AUTHORITY_JIT_SELL_BID_MISSING:"
            f"token_id={token_id}:book_hash={book_hash or 'missing'}:best_ask={best_ask}"
        )
    if not book_hash:
        raise ValueError(
            "PRE_SUBMIT_BOOK_AUTHORITY_JIT_HASH_MISSING:"
            f"token_id={token_id}:best_bid={best_bid}:best_ask={best_ask}"
        )
    if best_bid is not None and best_ask is not None and best_bid >= best_ask:
        # Crossed/locked book is not a usable pre-submit authority.
        raise ValueError(
            "PRE_SUBMIT_BOOK_AUTHORITY_JIT_CROSSED_BOOK:"
            f"token_id={token_id}:best_bid={best_bid}:best_ask={best_ask}"
        )
    if not post_only and limit_price is not None and size is not None:
        levels = message.get("asks") if normalized_side == "BUY" else message.get("bids")
        executable_size, executable_notional = _edli_book_executable_depth(
            levels,
            side=normalized_side,
            limit_price=float(limit_price),
        )
        required_size = float(size)
        if executable_size + 1e-9 < required_size:
            raise ValueError(
                "PRE_SUBMIT_BOOK_AUTHORITY_JIT_DEPTH_INSUFFICIENT:"
                f"token_id={token_id}:side={normalized_side}:limit_price={float(limit_price):.6f}:"
                f"required_size={required_size:.6f}:executable_size={executable_size:.6f}:"
                f"executable_notional={executable_notional:.6f}:"
                f"book_hash={book_hash}"
            )
    return (
        best_bid,
        best_ask,
        book_hash,
        observed_at or datetime.now(timezone.utc),
        str(book_authority_id),
    )

def _edli_book_best_price(levels, *, best: str):
    if not levels:
        return None
    parsed = []
    for level in levels:
        raw = level.get("price") if isinstance(level, dict) else (level[0] if level else None)
        if raw in (None, ""):
            continue
        try:
            price = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(price) and 0.0 < price <= 1.0:
            parsed.append(price)
    if not parsed:
        return None
    return max(parsed) if best == "bid" else min(parsed)


def _edli_book_executable_depth(
    levels,
    *,
    side: str,
    limit_price: float,
) -> tuple[float, float]:
    """Return executable shares and maker notional within one limit."""

    shares = 0.0
    notional = 0.0
    for level in levels or ():
        if isinstance(level, dict):
            raw_price = level.get("price")
            raw_size = level.get("size")
        else:
            raw_price = level[0] if level else None
            raw_size = level[1] if level and len(level) > 1 else None
        try:
            price = float(raw_price)
            level_size = float(raw_size)
        except (TypeError, ValueError):
            continue
        if not (
            math.isfinite(price)
            and 0.0 < price <= 1.0
            and math.isfinite(level_size)
            and level_size > 0.0
        ):
            continue
        if side == "BUY" and price <= limit_price + 1e-12:
            shares += level_size
            notional += price * level_size
        elif side == "SELL" and price + 1e-12 >= limit_price:
            shares += level_size
            notional += price * level_size
    return shares, notional

def _edli_pre_submit_authority_provider_from_book_evidence_conn(
    book_evidence_conn, edli_cfg, *, book_quote_provider=None
):
    """Build EDLI's production pre-submit authority provider.

    The provider consumes quote evidence, heartbeat/user-channel guards, and
    wallet allowance truth; missing authority remains fail-closed before command
    creation.

    ``book_quote_provider`` (GATE #84) is a just-in-time single-token
    ``/book`` fetch (``token_id -> dict``). When wired in live/canary mode it is
    the PRIMARY book authority: for the selected candidate at submit time we pull
    its live book and anchor ``quote_seen_at`` to our observation instant
    (``checked_at``), so the 1000ms freshness bound reflects observation-to-submit
    latency rather than the venue's coarse book-change stamp. Cached DB feasibility
    evidence remains useful for screening but never licenses venue submission.
    """
    from src.main import _row_get

    from src.engine.event_reactor_adapter import PreSubmitAuthorityWitness

    max_quote_age_ms = int(edli_cfg.get("pre_submit_max_quote_age_ms", 1000) or 1000)
    balance_check_enabled = bool(edli_cfg.get("pre_submit_balance_allowance_check_enabled", True))
    venue_summary_cache: dict[str, object] | None = None
    pusd_collateral_payload_cache: dict[str, object] | None = None
    full_collateral_payload_cache: dict[str, object] | None = None

    def _cached_venue_summary(checked_at: datetime) -> dict[str, object]:
        nonlocal venue_summary_cache
        if venue_summary_cache is None:
            venue_summary_cache = _edli_venue_connectivity_authority_summary(checked_at)
        return venue_summary_cache

    def _cached_collateral_payload(side: str) -> dict[str, object]:
        nonlocal full_collateral_payload_cache, pusd_collateral_payload_cache
        normalized_side = str(side or "").upper()
        if normalized_side == "BUY" and pusd_collateral_payload_cache is None:
            from src.data.polymarket_client import PolymarketClient

            with PolymarketClient(public_http_timeout=_edli_pre_submit_inner_io_timeout_seconds()) as clob:
                adapter = clob._ensure_v2_adapter()
                pusd_payload_fn = getattr(adapter, "get_pusd_collateral_payload", None)
                if not callable(pusd_payload_fn):
                    pusd_payload_fn = adapter.get_collateral_payload
                pusd_collateral_payload_cache = dict(
                    _edli_run_pre_submit_clob_call(
                        "collateral_payload",
                        pusd_payload_fn,
                    )
                )
        if normalized_side == "BUY":
            return pusd_collateral_payload_cache
        if full_collateral_payload_cache is None:
            from src.data.polymarket_client import PolymarketClient

            with PolymarketClient(public_http_timeout=_edli_pre_submit_inner_io_timeout_seconds()) as clob:
                adapter = clob._ensure_v2_adapter()
                full_collateral_payload_cache = dict(
                    _edli_run_pre_submit_clob_call(
                        "collateral_payload",
                        adapter.get_collateral_payload,
                    )
                )
        return full_collateral_payload_cache

    def _provider(final_intent, _executable_snapshot, decision_time):
        del decision_time
        checked_at = datetime.now(timezone.utc)
        intent = final_intent.payload
        token_id = str(intent["token_id"])

        side = str(intent.get("side") or "").upper()
        has_limit_price = intent.get("limit_price") not in (None, "")
        has_size = intent.get("size") not in (None, "")
        if has_limit_price != has_size:
            raise ValueError(
                "PRE_SUBMIT_BOOK_AUTHORITY_JIT_INTENT_INCOMPLETE:"
                f"token_id={token_id}:has_limit_price={has_limit_price}:has_size={has_size}"
            )

        heartbeat_summary = _edli_heartbeat_authority_summary()
        user_ws_summary = _edli_user_ws_authority_summary(checked_at)
        venue_summary = _cached_venue_summary(checked_at)
        balance_status, balance_authority_id = _edli_balance_allowance_status(
            final_intent,
            checked_at,
            enabled=balance_check_enabled,
            collateral_payload=_cached_collateral_payload(str(intent.get("side") or "")),
        )
        # Price is read last so slow venue/balance checks cannot age the book
        # before the full-depth curve binds to this same observation.
        raw_book: dict[str, object] = {}

        def _capture_book(selected_token_id: str):
            if book_quote_provider is None:
                raise ValueError("PRE_SUBMIT_BOOK_AUTHORITY_JIT_REQUIRED")
            response = book_quote_provider(selected_token_id)
            current = dict(response[0] if isinstance(response, tuple) else response)
            raw_book.clear()
            raw_book.update(current)
            return response

        jit = _edli_pre_submit_book_from_jit_fetch(
            _capture_book,
            token_id=token_id,
            side=side,
            limit_price=float(intent["limit_price"]) if has_limit_price else None,
            size=float(intent["size"]) if has_size else None,
            post_only=intent.get("post_only") is True,
        )
        if jit is None:
            raise ValueError("PRE_SUBMIT_BOOK_AUTHORITY_JIT_REQUIRED")
        best_bid, best_ask, book_hash, book_observed_at, book_authority_id = jit
        checked_at = datetime.now(timezone.utc)
        quote_seen_at = book_observed_at.astimezone(timezone.utc).isoformat()

        return PreSubmitAuthorityWitness(
            quote_seen_at=quote_seen_at,
            book_hash=book_hash,
            current_best_bid=best_bid,
            current_best_ask=best_ask,
            tick_size=float(intent["tick_size"]),
            min_order_size=float(intent["min_order_size"]),
            neg_risk=bool(intent.get("neg_risk", False)),
            heartbeat_status="OK" if heartbeat_summary["allow_submit"] else "BLOCKED",
            user_ws_status="OK" if user_ws_summary["allow_submit"] else "BLOCKED",
            venue_connectivity_status="OK" if venue_summary["allow_submit"] else "BLOCKED",
            balance_allowance_status=balance_status,
            book_authority_id=book_authority_id,
            book_captured_at=quote_seen_at,
            heartbeat_authority_id=str(heartbeat_summary["authority_id"]),
            heartbeat_checked_at=checked_at.isoformat(),
            user_ws_authority_id=str(user_ws_summary["authority_id"]),
            user_ws_checked_at=checked_at.isoformat(),
            venue_connectivity_authority_id=str(venue_summary["authority_id"]),
            venue_connectivity_checked_at=checked_at.isoformat(),
            balance_allowance_authority_id=balance_authority_id,
            balance_allowance_checked_at=checked_at.isoformat(),
            orderbook_depth_jsonb=json.dumps(
                raw_book,
                sort_keys=True,
                separators=(",", ":"),
            ),
            checked_at=checked_at.isoformat(),
            max_quote_age_ms=max_quote_age_ms,
        )

    return _provider

def _edli_latest_pre_submit_book_row(
    book_evidence_conn,
    *,
    token_id: str,
    side: str | None = None,
    decision_time: datetime,
):
    normalized_side = str(side or "").upper()
    side_filter = ""
    if normalized_side == "BUY":
        side_filter = "AND best_ask_before IS NOT NULL"
    elif normalized_side == "SELL":
        side_filter = "AND best_bid_before IS NOT NULL"
    else:
        side_filter = "AND best_bid_before IS NOT NULL AND best_ask_before IS NOT NULL"
    latest_sql = f"""
        SELECT quote_seen_at, book_hash_before, best_bid_before, best_ask_before
        FROM execution_feasibility_latest
        WHERE token_id = ?
          AND quote_seen_at <= ?
          {side_filter}
          AND COALESCE(book_hash_before, '') != ''
        ORDER BY quote_seen_at DESC
        LIMIT 1
        """
    try:
        latest_row = book_evidence_conn.execute(
            latest_sql,
            (token_id, decision_time.isoformat()),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "execution_feasibility_latest" not in str(exc):
            raise
        latest_row = None
    if latest_row is not None:
        return latest_row
    return book_evidence_conn.execute(
        f"""
        SELECT quote_seen_at, book_hash_before, best_bid_before, best_ask_before
        FROM execution_feasibility_evidence
        WHERE token_id = ?
          AND quote_seen_at <= ?
          {side_filter}
          AND COALESCE(book_hash_before, '') != ''
        ORDER BY quote_seen_at DESC
        LIMIT 1
        """,
        (token_id, decision_time.isoformat()),
    ).fetchone()

def _edli_heartbeat_authority_summary() -> dict[str, object]:
    from src.control.heartbeat_supervisor import summary as heartbeat_summary

    summary = heartbeat_summary()
    return {
        "authority_id": "heartbeat_supervisor",
        "allow_submit": bool(summary.get("entry", {}).get("allow_submit", False)),
    }

def _edli_user_ws_authority_summary(checked_at: datetime) -> dict[str, object]:
    from src.control.ws_gap_guard import summary as ws_summary

    summary = ws_summary(now=checked_at)
    return {
        "authority_id": "ws_gap_guard",
        "allow_submit": bool(summary.get("entry", {}).get("allow_submit", False)),
    }

def _edli_venue_connectivity_authority_summary(checked_at: datetime) -> dict[str, object]:
    from src.data.polymarket_client import PolymarketClient

    with PolymarketClient(public_http_timeout=_edli_pre_submit_inner_io_timeout_seconds()) as clob:
        _edli_run_pre_submit_clob_call("venue_preflight", clob.v2_preflight)
    return {
        "authority_id": "polymarket_v2_preflight",
        "allow_submit": True,
        "checked_at": checked_at.isoformat(),
    }

def _edli_balance_allowance_status(
    final_intent,
    checked_at: datetime,
    *,
    enabled: bool,
    collateral_payload: dict[str, object] | None = None,
) -> tuple[str, str]:
    if not enabled:
        raise ValueError("PRE_SUBMIT_ALLOWANCE_CHECK_DISABLED")
    from src.data.polymarket_client import PolymarketClient

    intent = final_intent.payload
    side = str(intent.get("side") or "").upper()
    token_id = str(intent.get("token_id") or "")
    size = float(intent.get("size") or 0.0)
    notional = float(intent.get("notional_usd") or 0.0)
    if collateral_payload is None:
        with PolymarketClient(public_http_timeout=_edli_pre_submit_inner_io_timeout_seconds()) as clob:
            adapter = clob._ensure_v2_adapter()
            collateral = _edli_run_pre_submit_clob_call(
                "collateral_payload",
                adapter.get_collateral_payload,
            )
    else:
        collateral = collateral_payload
    if side == "BUY":
        balance_micro = int(collateral.get("pusd_balance_micro") or 0)
        allowance_micro = int(collateral.get("pusd_allowance_micro") or 0)
        required_micro = int(round(notional * 1_000_000))
        if balance_micro < required_micro:
            raise ValueError("PRE_SUBMIT_PUSD_BALANCE_INSUFFICIENT")
        if allowance_micro < required_micro:
            raise ValueError("PRE_SUBMIT_PUSD_ALLOWANCE_INSUFFICIENT")
        return "OK", "polymarket_wallet_readonly"
    if side == "SELL":
        balances = collateral.get("ctf_token_balances_units") or {}
        allowances = collateral.get("ctf_token_allowances_units") or {}
        token_balance = float(balances.get(token_id, 0) or 0)
        token_allowance = float(allowances.get(token_id, 0) or 0)
        if token_balance < size:
            raise ValueError("PRE_SUBMIT_CTF_BALANCE_INSUFFICIENT")
        if token_allowance < size:
            raise ValueError("PRE_SUBMIT_CTF_ALLOWANCE_INSUFFICIENT")
        return "OK", "polymarket_wallet_readonly"
    raise ValueError(f"PRE_SUBMIT_SIDE_UNSUPPORTED:{side}")

def _row_float(row, key: str) -> float | None:

    from src.main import _row_get

    value = _row_get(row, key)
    if value in (None, ""):
        return None
    return float(value)


# ---------------------------------------------------------------------------
# R4-b4 (2026-07-08 main.py slimming): continuous-redecision-screen cluster,
# extracted from src/main.py::_edli_continuous_redecision_screen_cycle and its
# exclusive helpers. main.py's scheduler hook is now a thin delegating call
# (see run_edli_continuous_redecision_screen_cycle below for the injected-lock
# rationale). ``_edli_redecision_confirm_refresh_lock`` guards the money-path
# confirmation-refresh priority marker; its only acquire/release site was (and
# remains) inside _edli_refresh_continuous_money_path_families below, so it
# moves with the cluster rather than staying in main.py. The three
# _REDECISION_*_GRACE_SECONDS constants below are likewise exclusive to this
# cluster's rest-pull/pending-expiry helpers (no other main.py reader).
# ---------------------------------------------------------------------------
_edli_redecision_confirm_refresh_lock = threading.Lock()
_REDECISION_REST_PULL_EXPIRY_GRACE_SECONDS = 20 * 60
_REDECISION_PENDING_EXPIRY_GRACE_SECONDS = 300
_REDECISION_FRESH_SCREEN_SUPERSEDE_GRACE_SECONDS = 75


_edli_redecision_screen_belief_cursor: int = 0
# Wave-1 2026-06-12: fixed per-cycle re-decision/screen batch fed to the WRAPPING fair
# cursor (CoverageFairnessRequest.select_rows). Replaces the deleted redecision_max_per_cycle
# settings cap. The cursor wraps modulo the family count, so this batch reaches EVERY family
# within ceil(N/batch) cycles and never silently drops the tail. Sized to sweep the full live
# family universe (~108 city×metric families) within ~2 cycles at the ~60-90s reactor cadence.
_EDLI_REDECISION_FAIR_BATCH: int = 60


def _edli_belief_family_key(belief) -> tuple[str, str, str, str]:
    return (
        str(getattr(belief, "city", "") or "").strip(),
        str(getattr(belief, "target_date", "") or "").strip(),
        str(getattr(belief, "metric", "") or "").strip(),
        str(getattr(belief, "family_id", "") or "").strip(),
    )


def _edli_redecision_screen_belief_batch(
    beliefs: list,
    *,
    max_families: int,
) -> tuple[list, set[tuple[str, str, str, str]], int]:
    """Return the fair-cursor entry-screen belief slice for this tick.

    The redecision screen used to feed every cached belief into the price reader,
    which meant a live table with millions of executable snapshots could keep one
    scheduler worker busy for minutes before the reactor reached any event. This
    is a fairness cursor, not an edge cap: it wraps through the complete belief
    universe over successive ticks and bounds the per-tick DB read surface.
    """
    global _edli_redecision_screen_belief_cursor
    if not beliefs:
        return [], set(), 0
    ordered = sorted(beliefs, key=_edli_belief_family_key)
    total = len(ordered)
    if max_families <= 0 or max_families >= total:
        keys = {_edli_belief_family_key(b) for b in ordered}
        _edli_redecision_screen_belief_cursor = 0
        return ordered, keys, total
    start = _edli_redecision_screen_belief_cursor % total
    selected = [ordered[(start + i) % total] for i in range(max_families)]
    _edli_redecision_screen_belief_cursor = (start + max_families) % total
    keys = {_edli_belief_family_key(b) for b in selected}
    return selected, keys, total


def _edli_filter_beliefs_to_family_keys(
    beliefs: list,
    family_keys: set[tuple[str, str, str, str]],
) -> list:
    if not family_keys:
        return []
    return [belief for belief in beliefs if _edli_belief_family_key(belief) in family_keys]


def _edli_open_maker_rests_for_screen(trade_conn, world_conn, *, beliefs=None) -> "list":
    """Build OpenRest entries for §4.5 rest management: every OPEN maker ENTRY rest joined to its
    decision belief via condition_id. Pure read on both DBs.

    The rest's condition_id (token_id → executable_market_snapshots) joins to the belief's
    per-bin condition_ids → (family_id, bin_label, resting_posterior, resting_snapshot_id). The
    resting_posterior is the belief's posterior at that bin from the LATEST cached belief whose
    snapshot matches the rest's pricing snapshot (anti-twitch: screen_reprice fires only when the
    LATEST belief is from a NEWER snapshot than the rest's). When the bin/belief cannot be resolved
    the rest still gets the book/stale checks (which need no posterior)."""
    from datetime import datetime, timezone
    from src.events.continuous_redecision import OpenRest, _all_latest_beliefs
    from src.execution.staleness_cancel import OPEN_REST_FACT_STATES

    now = datetime.now(timezone.utc)
    try:
        fact_cols = {str(row[1]) for row in trade_conn.execute("PRAGMA table_info(venue_order_facts)").fetchall()}
    except Exception:  # noqa: BLE001
        fact_cols = set()
    try:
        command_cols = {str(row[1]) for row in trade_conn.execute("PRAGMA table_info(venue_commands)").fetchall()}
    except Exception:  # noqa: BLE001
        command_cols = set()
    matched_select = "matched_size" if "matched_size" in fact_cols else "NULL AS matched_size"
    command_state_filter = (
        "AND state IN ('ACKED', 'POST_ACKED', 'PARTIAL')" if "state" in command_cols else ""
    )
    command_rows = trade_conn.execute(
        f"""
        SELECT command_id, venue_order_id, token_id, market_id,
               side, price, snapshot_id, created_at
          FROM venue_commands
         WHERE intent_kind = 'ENTRY'
           {command_state_filter}
           AND venue_order_id IS NOT NULL AND venue_order_id != ''
        """
    ).fetchall()
    rows = []
    if command_rows:
        fact_sql = f"""
            SELECT state, {matched_select}
              FROM venue_order_facts
             WHERE venue_order_id = ?
             ORDER BY local_sequence DESC
             LIMIT 1
        """
        open_states = set(OPEN_REST_FACT_STATES)
        for vc in command_rows:
            latest_fact = trade_conn.execute(fact_sql, (vc[1],)).fetchone()
            if latest_fact is None:
                continue
            fact_state = str(latest_fact[0] or "")
            if fact_state not in open_states:
                continue
            rows.append(tuple(vc) + (fact_state, latest_fact[1]))
    if not rows:
        return []
    # Resolve token_id -> condition_id and held-side direction from the freshest
    # executable_market_snapshots row. BOOK_MOVED checks are direction-specific:
    # buy_yes rests compare against YES best bid; buy_no rests compare against
    # native NO best bid.
    token_ids = {str(r[2] or "") for r in rows if r[2]}
    cond_by_token: dict[str, str] = {}
    side_by_token: dict[str, str] = {}
    min_order_by_token: dict[str, object] = {}
    if token_ids:
        try:
            latest_cols = {
                str(row[1])
                for row in trade_conn.execute(
                    "PRAGMA table_info(executable_market_snapshot_latest)"
                ).fetchall()
            }
            latest_min_order_select = (
                ", min_order_size" if "min_order_size" in latest_cols else ", NULL AS min_order_size"
            )
            tph = ",".join("?" for _ in token_ids)
            for cr in trade_conn.execute(
                f"""
                SELECT selected_outcome_token_id, condition_id, yes_token_id, no_token_id,
                       captured_at{latest_min_order_select}
                FROM executable_market_snapshot_latest
                WHERE selected_outcome_token_id IN ({tph})
                   OR yes_token_id IN ({tph})
                   OR no_token_id IN ({tph})
                ORDER BY captured_at DESC
                """,
                (*tuple(token_ids), *tuple(token_ids), *tuple(token_ids)),
            ).fetchall():
                selected = str(cr[0] or "")
                cond = str(cr[1] or "")
                yes_token = str(cr[2] or "")
                no_token = str(cr[3] or "")
                min_order_size = cr[5]
                for token, side in (
                    (selected, "buy_no" if selected and selected == no_token else "buy_yes"),
                    (yes_token, "buy_yes"),
                    (no_token, "buy_no"),
                ):
                    if token and token in token_ids and token not in cond_by_token:
                        cond_by_token[token] = cond
                        side_by_token[token] = side
                        min_order_by_token[token] = min_order_size
        except Exception:  # noqa: BLE001 — token→condition resolution is best-effort
            cond_by_token = {}
            side_by_token = {}
            min_order_by_token = {}
        tokens_with_partial_fill: set[str] = set()
        for row in rows:
            token = str(row[2] or "")
            if not token:
                continue
            try:
                matched = float(row[9]) if row[9] is not None else 0.0
            except (TypeError, ValueError):
                matched = 0.0
            if matched > 0.0:
                tokens_with_partial_fill.add(token)
        fallback_token_ids = {
            token
            for token in token_ids
            if token not in cond_by_token
            or (
                token in tokens_with_partial_fill
                and min_order_by_token.get(token) in (None, "")
            )
        }
        if fallback_token_ids:
            try:
                snapshot_cols = {
                    str(row[1])
                    for row in trade_conn.execute(
                        "PRAGMA table_info(executable_market_snapshots)"
                    ).fetchall()
                }
                snapshot_min_order_select = (
                    ", min_order_size"
                    if "min_order_size" in snapshot_cols
                    else ", NULL AS min_order_size"
                )
                tph = ",".join("?" for _ in fallback_token_ids)
                for cr in trade_conn.execute(
                    f"""
                    SELECT selected_outcome_token_id, condition_id, yes_token_id, no_token_id,
                           captured_at{snapshot_min_order_select}
                    FROM executable_market_snapshots
                    WHERE selected_outcome_token_id IN ({tph})
                       OR yes_token_id IN ({tph})
                       OR no_token_id IN ({tph})
                    ORDER BY captured_at DESC
                    """,
                    (
                        *tuple(fallback_token_ids),
                        *tuple(fallback_token_ids),
                        *tuple(fallback_token_ids),
                    ),
                ).fetchall():
                    selected = str(cr[0] or "")
                    cond = str(cr[1] or "")
                    yes_token = str(cr[2] or "")
                    no_token = str(cr[3] or "")
                    min_order_size = cr[5]
                    for token, side in (
                        (selected, "buy_no" if selected and selected == no_token else "buy_yes"),
                        (yes_token, "buy_yes"),
                        (no_token, "buy_no"),
                    ):
                        if token and token in fallback_token_ids:
                            cond_by_token.setdefault(token, cond)
                            side_by_token.setdefault(token, side)
                            if min_order_by_token.get(token) in (None, ""):
                                min_order_by_token[token] = min_order_size
            except Exception:  # noqa: BLE001 — token→condition resolution is best-effort
                pass
    if beliefs is None:
        beliefs = _all_latest_beliefs(world_conn)
    # Index belief bins by condition_id → (belief, bin_label, posterior).
    bin_by_cond: dict[str, tuple] = {}
    for belief in beliefs:
        conds = belief.condition_ids or []
        for idx, label in enumerate(belief.bin_labels):
            if idx < len(conds) and conds[idx]:
                if idx < len(belief.p_posterior_vec):
                    bin_by_cond[str(conds[idx])] = (belief, label, float(belief.p_posterior_vec[idx]))
    out = []
    for r in rows:
        command_id, venue_order_id, token_id, market_id, side, price, snap_id, created_at, fact_state, matched_size = (
            str(r[0] or ""), str(r[1] or ""), str(r[2] or ""), str(r[3] or ""),
            str(r[4] or ""), r[5], str(r[6] or ""), str(r[7] or ""), str(r[8] or ""), r[9],
        )
        cond = cond_by_token.get(token_id, "")
        belief_hit = bin_by_cond.get(cond)
        family_id = belief_hit[0].family_id if belief_hit else ""
        city = str(getattr(belief_hit[0], "city", "") or "") if belief_hit else ""
        target_date = str(getattr(belief_hit[0], "target_date", "") or "") if belief_hit else ""
        metric = str(getattr(belief_hit[0], "metric", "") or "") if belief_hit else ""
        bin_label = belief_hit[1] if belief_hit else ""
        resting_posterior = belief_hit[2] if belief_hit else 0.0
        # quote_age_ms from the command's creation (the order has rested since created_at).
        try:
            from datetime import datetime as _dt
            age_ms = max(0.0, (now - _dt.fromisoformat(created_at)).total_seconds() * 1000.0) if created_at else 0.0
        except Exception:  # noqa: BLE001
            age_ms = 0.0
        screen_side = side_by_token.get(token_id, "buy_yes")
        if not (cond and family_id and bin_label and snap_id):
            continue
        resting_held_side_posterior = (
            1.0 - resting_posterior if screen_side == "buy_no" else resting_posterior
        )
        try:
            min_order_size = float(min_order_by_token[token_id])
        except (KeyError, TypeError, ValueError):
            min_order_size = None
        out.append(
            OpenRest(
                command_id=command_id,
                venue_order_id=venue_order_id,
                family_id=family_id,
                bin_label=bin_label,
                side=screen_side,
                condition_id=cond,
                resting_posterior=resting_held_side_posterior,
                resting_snapshot_id=snap_id,
                limit_price=float(price) if price is not None else 0.0,
                quote_age_ms=age_ms,
                created_at=created_at,
                fact_state=fact_state,
                matched_size=None if matched_size is None else float(matched_size),
                min_order_size=min_order_size,
                city=city,
                target_date=target_date,
                metric=metric,
            )
        )
    return out


def _edli_family_key_from_belief(belief: Any) -> tuple[str, str, str] | None:
    key = (
        str(getattr(belief, "city", "") or "").strip(),
        str(getattr(belief, "target_date", "") or "").strip(),
        str(getattr(belief, "metric", "") or "").strip(),
    )
    if all(key) and key[2] in {"high", "low"}:
        return key
    return None


def _edli_redecision_condition_scope(
    redecisions: Iterable[Any],
    beliefs: Iterable[Any],
) -> dict[tuple[str, str, str], set[str]]:
    """Map screened entry candidates to the exact condition_ids that need fresh books."""

    by_family_id = {str(getattr(belief, "family_id", "") or ""): belief for belief in beliefs}
    out: dict[tuple[str, str, str], set[str]] = {}
    for redecision in redecisions or ():
        belief = by_family_id.get(str(getattr(redecision, "family_id", "") or ""))
        if belief is None:
            continue
        family_key = _edli_family_key_from_belief(belief)
        if family_key is None:
            continue
        label = str(getattr(redecision, "bin_label", "") or "")
        bin_labels = list(getattr(belief, "bin_labels", None) or ())
        condition_ids = list(getattr(belief, "condition_ids", None) or ())
        for idx, candidate_label in enumerate(bin_labels):
            if str(candidate_label or "") != label or idx >= len(condition_ids):
                continue
            condition_id = str(condition_ids[idx] or "").strip()
            if condition_id:
                out.setdefault(family_key, set()).add(condition_id)
    return out


def _edli_merge_condition_scopes(
    *scopes: dict[tuple[str, str, str], set[str]],
) -> dict[tuple[str, str, str], set[str]]:
    """Union condition scopes without mutating the caller-owned maps."""

    out: dict[tuple[str, str, str], set[str]] = {}
    for scope in scopes:
        for family_key, condition_ids in (scope or {}).items():
            clean = {
                str(condition_id or "").strip()
                for condition_id in condition_ids
                if str(condition_id or "").strip()
            }
            if clean:
                out.setdefault(family_key, set()).update(clean)
    return out


def _edli_rest_pull_condition_scope(
    rest_pulls: Iterable[tuple[Any, Any]],
    beliefs: Iterable[Any],
) -> dict[tuple[str, str, str], set[str]]:
    """Map live maker-rest pulls to the exact condition_ids being cancelled/repriced.

    A family-optimum replacement pull cancels the current rest so the existing
    reactor can re-certify a sibling. The confirmation refresh must therefore
    prioritize both the cancelled condition and the replacement condition, or
    the next pass can cancel correctly but still lack fresh substrate for the
    better sibling.
    """

    by_family_id = {str(getattr(belief, "family_id", "") or ""): belief for belief in beliefs}
    out: dict[tuple[str, str, str], set[str]] = {}
    for rest, decision in rest_pulls or ():
        family_key = _edli_family_key_from_rest(rest)
        if family_key is None:
            belief = by_family_id.get(str(getattr(rest, "family_id", "") or ""))
            family_key = _edli_family_key_from_belief(belief) if belief is not None else None
        if family_key is None:
            continue
        condition_id = str(getattr(rest, "condition_id", "") or "").strip()
        if condition_id:
            out.setdefault(family_key, set()).add(condition_id)
        replacement_condition_id = str(
            getattr(decision, "replacement_condition_id", "") or ""
        ).strip()
        if replacement_condition_id:
            out.setdefault(family_key, set()).add(replacement_condition_id)
    return out


def _edli_open_rest_condition_scope(
    open_rests: Iterable[Any],
    beliefs: Iterable[Any],
) -> dict[tuple[str, str, str], set[str]]:
    """Map all live maker rests to condition_ids that need price refresh.

    A rest cannot decide whether to cancel/reprice from stale books. This scope is
    intentionally built before ``screen_resting_orders`` so the confirmation
    refresh can make the rest screen's price inputs current.
    """

    by_family_id = {str(getattr(belief, "family_id", "") or ""): belief for belief in beliefs}
    out: dict[tuple[str, str, str], set[str]] = {}
    for rest in open_rests or ():
        family_key = _edli_family_key_from_rest(rest)
        if family_key is None:
            belief = by_family_id.get(str(getattr(rest, "family_id", "") or ""))
            family_key = _edli_family_key_from_belief(belief) if belief is not None else None
        if family_key is None:
            continue
        condition_id = str(getattr(rest, "condition_id", "") or "").strip()
        if condition_id:
            out.setdefault(family_key, set()).add(condition_id)
    return out


def _edli_family_key_from_rest(rest: Any) -> tuple[str, str, str] | None:
    city = str(getattr(rest, "city", "") or "").strip()
    target_date = str(getattr(rest, "target_date", "") or "").strip()
    metric = str(getattr(rest, "metric", "") or "").strip()
    if city and target_date and metric:
        return (city, target_date, metric)
    return None


def _edli_condition_latest_snapshot_executable(trade_conn, condition_id: str) -> bool:
    """Return False only when the latest known substrate says this condition cannot trade."""

    clean_condition_id = str(condition_id or "").strip()
    if not clean_condition_id:
        return False
    try:
        cols = {
            str(row[1])
            for row in trade_conn.execute("PRAGMA table_info(executable_market_snapshots)").fetchall()
        }
    except sqlite3.Error:
        return True
    required = {"condition_id", "captured_at", "snapshot_id"}
    if not required.issubset(cols):
        return True
    selected_cols = [
        "closed" if "closed" in cols else "0 AS closed",
        "enable_orderbook" if "enable_orderbook" in cols else "1 AS enable_orderbook",
        "accepting_orders" if "accepting_orders" in cols else "1 AS accepting_orders",
    ]
    try:
        row = trade_conn.execute(
            f"""
            SELECT {", ".join(selected_cols)}
              FROM executable_market_snapshots
             WHERE condition_id = ?
             ORDER BY captured_at DESC, snapshot_id DESC
             LIMIT 1
            """,
            (clean_condition_id,),
        ).fetchone()
    except sqlite3.Error:
        return True
    if row is None:
        return True

    def _truthy(value: object, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes"}:
            return True
        if text in {"0", "false", "no"}:
            return False
        return default

    closed = _truthy(row[0], False)
    enable_orderbook = _truthy(row[1], True)
    accepting_orders = _truthy(row[2], True)
    return bool(not closed and enable_orderbook and accepting_orders)


def _edli_current_held_position_condition_scope() -> dict[tuple[str, str, str], set[str]]:
    """Current held-position condition_ids for scoped redecision freshness admission."""
    import logging as _logging

    _log = _logging.getLogger("zeus.events.reactor")

    from src.state.db import get_trade_connection_read_only

    out: dict[tuple[str, str, str], set[str]] = {}
    trade_ro = None
    try:
        trade_ro = get_trade_connection_read_only()
        try:
            cols = {
                str(row[1])
                for row in trade_ro.execute("PRAGMA table_info(position_current)").fetchall()
            }
        except sqlite3.Error:
            return {}
        required = {
            "city",
            "target_date",
            "temperature_metric",
            "phase",
            "condition_id",
            "chain_state",
            "chain_shares",
        }
        if not required.issubset(cols):
            return {}
        from src.contracts.position_truth import CURRENT_MONEY_RISK_CHAIN_STATES

        # T5 (docs/rebuild/quarantine_excision_2026-07-11.md): this used to
        # also OR in a phase='quarantined' branch — retired, DB CHECK no
        # longer admits the literal post-migration.
        chain_state_values = tuple(sorted(CURRENT_MONEY_RISK_CHAIN_STATES))
        chain_placeholders = ",".join("?" for _ in chain_state_values)
        rows = trade_ro.execute(
            f"""
            SELECT city, target_date, temperature_metric, condition_id
              FROM position_current
             WHERE phase IN ('active', 'day0_window', 'pending_exit')
               AND COALESCE(chain_state, '') IN ({chain_placeholders})
               AND condition_id IS NOT NULL
               AND TRIM(condition_id) != ''
               AND COALESCE(chain_shares, 0) > 0.000001
            """,
            chain_state_values,
        ).fetchall()
        for row in rows:
            family_key = (
                str(row[0] or "").strip(),
                str(row[1] or "").strip(),
                str(row[2] or "").strip(),
            )
            condition_id = str(row[3] or "").strip()
            if all(family_key) and family_key[2] in {"high", "low"} and condition_id:
                if not _edli_condition_latest_snapshot_executable(trade_ro, condition_id):
                    continue
                out.setdefault(family_key, set()).add(condition_id)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "edli_redecision_screen: held-position condition scope read failed; "
            "held condition freshness not admitted this tick: %r",
            exc,
        )
        return {}
    finally:
        if trade_ro is not None:
            try:
                trade_ro.close()
            except Exception:  # noqa: BLE001
                pass
    return out
def _edli_current_held_position_family_condition_scope(
    families: set[tuple[str, str, str]] | None = None,
) -> dict[tuple[str, str, str], set[str]]:
    """Full family condition scope for held-position redecision.

    Held-position redecision is a family optimization problem, not an old-token
    refresh.  A stale or unrefreshed sibling can be the best fill-up/shift target,
    so the confirmation producer must refresh the complete executable family.
    """
    import logging as _logging

    _log = _logging.getLogger("zeus.events.reactor")

    held_families = (
        set(_edli_current_held_position_condition_scope())
        if families is None
        else set(families)
    )
    clean_families = {
        (str(city or "").strip(), str(target_date or "").strip(), str(metric or "").strip())
        for city, target_date, metric in held_families
        if str(city or "").strip()
        and str(target_date or "").strip()
        and str(metric or "").strip() in {"high", "low"}
    }
    if not clean_families:
        return {}

    from src.data.market_topology_rows import _event_family_market_topology_rows
    from src.state.db import get_forecasts_connection_read_only, get_trade_connection_read_only

    forecasts_ro = get_forecasts_connection_read_only()
    trade_ro = get_trade_connection_read_only()
    try:
        out: dict[tuple[str, str, str], set[str]] = {}
        for family in sorted(clean_families):
            city, target_date, metric = family
            try:
                topology_rows = _event_family_market_topology_rows(
                    forecasts_ro,
                    {"city": city, "target_date": target_date, "metric": metric},
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "edli_redecision_screen: held-family topology read failed; "
                    "family not admitted for full redecision this tick: city=%r "
                    "target_date=%r metric=%r error=%r",
                    city,
                    target_date,
                    metric,
                    exc,
                )
                continue
            for row in topology_rows or ():
                condition_id = str(row.get("condition_id") or "").strip()
                if not condition_id:
                    continue
                if not _edli_condition_latest_snapshot_executable(trade_ro, condition_id):
                    continue
                out.setdefault(family, set()).add(condition_id)
        return out
    finally:
        try:
            forecasts_ro.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            trade_ro.close()
        except Exception:  # noqa: BLE001
            pass
def _edli_families_with_fresh_scoped_executable_substrate(
    condition_scope: dict[tuple[str, str, str], set[str]],
    *,
    now_utc: datetime,
) -> set[tuple[str, str, str]]:
    """Families whose scoped money-path conditions have fresh YES and NO books.

    Continuous redecision is triggered by specific entry candidates, maker rests,
    and held positions. A PARTIAL refresh should therefore prove the exact
    conditions that are about to re-enter the money path, not require every
    topology bin in a large weather family to refresh in the same tick.
    """

    clean_scope: dict[tuple[str, str, str], set[str]] = {}
    for family, condition_ids in (condition_scope or {}).items():
        try:
            city, target_date, metric = family
        except (TypeError, ValueError):
            continue
        family_key = (
            str(city or "").strip(),
            str(target_date or "").strip(),
            str(metric or "").strip(),
        )
        clean_condition_ids = {
            str(condition_id or "").strip()
            for condition_id in condition_ids or set()
            if str(condition_id or "").strip()
        }
        if all(family_key) and family_key[2] in {"high", "low"} and clean_condition_ids:
            clean_scope.setdefault(family_key, set()).update(clean_condition_ids)
    if not clean_scope:
        return set()
    from src.main import _condition_buy_sides_fresh
    from src.state.db import get_trade_connection_read_only

    fresh_at_iso = now_utc.isoformat()
    trade_ro = get_trade_connection_read_only()
    try:
        out: set[tuple[str, str, str]] = set()
        for family, condition_ids in sorted(clean_scope.items()):
            if all(_condition_buy_sides_fresh(trade_ro, cid, fresh_at_iso) for cid in sorted(condition_ids)):
                out.add(family)
        return out
    finally:
        try:
            trade_ro.close()
        except Exception:  # noqa: BLE001
            pass


def _edli_refresh_continuous_money_path_families(
    families: set[tuple[str, str, str]],
    *,
    priority_condition_ids: Iterable[str] | None = None,
) -> dict:
    """Prioritize current continuous-money-path families before redecision emit.

    Continuous redecision is a consumer.  The substrate-observer daemon is the
    executable-snapshot producer.  This function only marks live-money families
    for priority sidecar capture, then the caller independently admits families
    whose scoped executable substrate is already fresh.
    """
    import logging as _logging

    _log = _logging.getLogger("zeus.events.reactor")

    clean_families = {
        (str(city or "").strip(), str(target_date or "").strip(), str(metric or "").strip())
        for city, target_date, metric in families or set()
        if str(city or "").strip()
        and str(target_date or "").strip()
        and str(metric or "").strip() in {"high", "low"}
    }
    priority_conditions = {
        str(condition_id or "").strip()
        for condition_id in (priority_condition_ids or ())
        if str(condition_id or "").strip()
    }
    if not clean_families:
        return {"status": "no_families", "families_requested": 0}
    if not _edli_redecision_confirm_refresh_lock.acquire(blocking=False):
        return {
            "status": "skipped_lock_busy",
            "families_requested": len(clean_families),
            "lock": "edli_redecision_confirm_refresh",
        }
    try:
        try:
            from src.data.substrate_priority import mark_money_path_substrate_priority

            request = mark_money_path_substrate_priority(
                reason="continuous_redecision_confirm_refresh",
                ttl_seconds=35.0,
                families=clean_families,
                condition_ids=priority_conditions,
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "edli_redecision_screen: substrate priority marker write failed: %r",
                exc,
            )
            return {
                "status": "priority_marker_failed",
                "families_requested": len(clean_families),
                "reason": str(exc),
            }
        request_id = ""
        if isinstance(request, dict):
            request_id = str(request.get("request_id") or "").strip()
        return {
            "status": "priority_marked",
            "families_requested": len(clean_families),
            "priority_condition_count": len(priority_conditions),
            "executable_substrate_coverage_status": "READ_FILTER_REQUIRED",
            "priority_request_id": request_id,
        }
    finally:
        try:
            _edli_redecision_confirm_refresh_lock.release()
        except RuntimeError:
            pass


def _edli_redecision_priority_condition_limit() -> int:
    raw = os.environ.get(
        "ZEUS_REDECISION_PRIORITY_CONDITION_LIMIT",
        os.environ.get("ZEUS_MARKET_DISCOVERY_PRIORITY_DIRECT_CLOB_PREFETCH_MAX_CONDITIONS", "32"),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 32
    return max(1, min(500, value))


def _edli_confirm_priority_condition_ids(
    *,
    rest_condition_scope: dict[tuple[str, str, str], set[str]],
    held_condition_scope: dict[tuple[str, str, str], set[str]],
    entry_condition_scope: dict[tuple[str, str, str], set[str]],
    entry_refresh_condition_scope: dict[tuple[str, str, str], set[str]],
    open_rest_condition_scope: dict[tuple[str, str, str], set[str]],
    full_family_refresh_families: set[tuple[str, str, str]] | None = None,
    limit: int | None = None,
) -> list[str]:
    """Return a bounded, ordered money-path condition frontier for sidecar capture."""

    condition_limit = _edli_redecision_priority_condition_limit() if limit is None else max(1, int(limit))
    full_family_refresh = {
        (str(city or "").strip(), str(target_date or "").strip(), str(metric or "").strip())
        for city, target_date, metric in (full_family_refresh_families or set())
        if str(city or "").strip()
        and str(target_date or "").strip()
        and str(metric or "").strip() in {"high", "low"}
    }
    ordered: list[str] = []
    seen: set[str] = set()

    def _add_scope(scope: dict[tuple[str, str, str], set[str]]) -> None:
        for family_key in sorted(scope or {}):
            try:
                normalized_family = tuple(str(part or "").strip() for part in family_key)
            except TypeError:
                normalized_family = ("", "", "")
            if normalized_family in full_family_refresh:
                continue
            for condition_id in sorted(scope.get(family_key) or set()):
                clean = str(condition_id or "").strip()
                if not clean or clean in seen:
                    continue
                seen.add(clean)
                ordered.append(clean)
                if len(ordered) >= condition_limit:
                    return

    for scope in (
        rest_condition_scope,
        held_condition_scope,
        entry_condition_scope,
        entry_refresh_condition_scope,
        open_rest_condition_scope,
    ):
        if len(ordered) >= condition_limit:
            break
        _add_scope(scope)
    return ordered


def _edli_reemittable_forecast_family_keys(
    families: set[tuple[str, str, str]],
    *,
    decision_time: datetime,
    log_context: str,
) -> set[tuple[str, str, str]]:
    """Families that may enter forecast redecision this tick.

    Day0 / post-trading families may still be managed by their owning lanes
    (held positions by chain-sync/exit monitor, new entry discovery by ordinary
    FSR when phase-admissible). They must not be logged as forecast re-emitted
    or keep stale EDLI_REDECISION_PENDING rows alive, because the FSR trigger
    will drop them with the same forecast-only phase predicate.
    """
    import logging as _logging

    _log = _logging.getLogger("zeus.events.reactor")

    if not families:
        return set()
    from src.strategy.market_phase import market_phase_admits

    out: set[tuple[str, str, str]] = set()
    for city, target_date, metric in families:
        try:
            admitted = market_phase_admits(
                city=city,
                target_date=target_date,
                metric=metric,
                decision_time=decision_time,
                market_row={},
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "edli_redecision_screen: %s phase read failed; "
                "family not forecast-reemitted this tick: city=%r target_date=%r metric=%r error=%r",
                log_context,
                city,
                target_date,
                metric,
                exc,
            )
            continue
        if admitted:
            out.add((city, target_date, metric))
    return out
def _edli_entry_redecision_family_keys(
    raw_entry_families: set[tuple[str, str, str]],
    held_families: set[tuple[str, str, str]],
    *,
    decision_time: datetime,
) -> set[tuple[str, str, str]]:
    """New-entry redecision families after removing already-held exposure.

    Fresh entry and held redecision have different safety semantics. New-entry
    screening excludes held families so it cannot duplicate owned exposure. Held
    families that are still forecast-lane admissible are re-emitted separately by
    _edli_reemittable_held_position_family_keys and enter the reactor with
    allow_same_family_monitor_owned=True, where fill-up and shift-bin leases own
    the only permitted same-family side effects.
    """

    return _edli_reemittable_forecast_family_keys(
        set(raw_entry_families or set()) - set(held_families or set()),
        decision_time=decision_time,
        log_context="entry-screen",
    )


def _edli_reemittable_held_position_family_keys(
    families: set[tuple[str, str, str]],
    *,
    decision_time: datetime,
) -> set[tuple[str, str, str]]:
    """Held-position families eligible for full pre-settlement redecision.

    Monitor refresh owns the cheap hold/direct-sell check for all active positions.
    It does not own same-family fill-up or close-before-open shift execution. While
    a held family is still in the forecast-lane admit phase, re-emit it through
    EDLI_REDECISION_PENDING so the existing reactor path runs with
    allow_same_family_monitor_owned=True and the family-rebalance lease enforces
    one active fill-up/shift per family. Day0/phase-closed held positions are left
    on the observation-aware monitor path.
    """

    return _edli_reemittable_forecast_family_keys(
        set(families or set()),
        decision_time=decision_time,
        log_context="held-redecision",
    )


def _redecision_payload_origin(payload: Mapping[str, Any]) -> str:
    return str(payload.get("redecision_origin") or "").strip().lower()


def _preserve_recent_rest_pull_redecision(
    payload: Mapping[str, Any],
    *,
    event_created_at: str,
    decision_dt: datetime,
) -> bool:
    """Keep cancel/reprice redecision rows alive long enough for the fresh screen.

    A pulled maker rest is removed from the open-rest input set as soon as the
    terminal cancel/no-fill fact is reconciled. The follow-on redecision event is
    the durable continuity proof for that family; expiring it on the next generic
    no-edge screen erases the price-management chain before the reactor can
    reprice/re-submit/decline from current evidence.
    """

    if _redecision_payload_origin(payload) != "rest_pull":
        return False
    try:
        created_dt = datetime.fromisoformat(str(event_created_at).replace("Z", "+00:00"))
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        age_seconds = (decision_dt - created_dt.astimezone(timezone.utc)).total_seconds()
    except Exception:  # noqa: BLE001
        return False
    return 0.0 <= age_seconds < float(_REDECISION_REST_PULL_EXPIRY_GRACE_SECONDS)


def _edli_supersede_pending_redecisions_for_rest_pull_families(
    world_conn,
    rest_pull_families: set[tuple[str, str, str]],
    *,
    decision_time: str,
) -> int:
    """Expire generic pending redecision rows that would suppress a rest-pull emit.

    A live OPEN maker rest that has fired ``rest_pull`` is command-management
    evidence: the order must be cancelled/repriced through a durable
    ``redecision_origin=rest_pull`` row. A generic market-price or entry-screen
    pending event for the same family is not equivalent because the rest may
    disappear from the open-rest set after cancel; if that generic row is the one
    preserved, the cancel/reprice continuity proof can be lost.

    Only unclaimed ``pending`` rows are superseded. Claimed/processing rows may
    already be inside the reactor and are left to the normal lease/stale paths.
    """

    clean_families = {
        (str(city or "").strip(), str(target_date or "").strip(), str(metric or "").strip())
        for city, target_date, metric in rest_pull_families or set()
        if str(city or "").strip()
        and str(target_date or "").strip()
        and str(metric or "").strip() in {"high", "low"}
    }
    if not clean_families:
        return 0
    from src.events.continuous_redecision import REDECISION_EVENT_TYPE as _REDECISION_EVENT_TYPE

    try:
        rows = world_conn.execute(
            """
            SELECT e.event_id, e.payload_json
              FROM opportunity_event_processing p
                   INDEXED BY idx_opportunity_event_processing_status
              JOIN opportunity_events e ON e.event_id = p.event_id
             WHERE p.consumer_name = 'edli_reactor_v1'
               AND p.processing_status = 'pending'
               AND e.event_type = ?
             ORDER BY p.updated_at ASC
             LIMIT 5000
            """,
            (_REDECISION_EVENT_TYPE,),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return 0
    expire_ids: list[str] = []
    for row in rows:
        try:
            event_id = str(row[0] or "")
            payload = json.loads(str(row[1] or "{}"))
            family = (
                str(payload.get("city") or "").strip(),
                str(payload.get("target_date") or "").strip(),
                str(payload.get("metric") or "").strip(),
            )
        except Exception:  # noqa: BLE001
            continue
        if not event_id or family not in clean_families:
            continue
        if _redecision_payload_origin(payload) == "rest_pull":
            continue
        expire_ids.append(event_id)
    if not expire_ids:
        return 0
    now = str(decision_time)
    changed = 0
    for start in range(0, len(expire_ids), 250):
        chunk = expire_ids[start : start + 250]
        placeholders = ",".join("?" for _ in chunk)
        cur = world_conn.execute(
            f"""
            UPDATE opportunity_event_processing
               SET processing_status = 'expired',
                   processed_at = ?,
                   updated_at = ?,
                   last_error = 'REDECISION_SUPERSEDED_BY_REST_PULL:open_rest_requires_cancel_reprice'
             WHERE consumer_name = 'edli_reactor_v1'
               AND processing_status = 'pending'
               AND event_id IN ({placeholders})
            """,
            (now, now, *chunk),
        )
        changed += int(cur.rowcount or 0)
    return changed


def _edli_plan_unadmitted_redecision_expiry(
    world_conn,
    admitted_families: set[tuple[str, str, str]],
    *,
    decision_time: str,
    supersede_stale_admitted: bool = False,
    claim_grace_seconds: float | None = None,
) -> dict[str, list[tuple[str, str, int, str | None, str]]]:
    """Discover redecision rows eligible for expiry without taking a write lock.

    Fresh pending rows are not safe to expire immediately: the screen may emit a
    row seconds before the next reactor claim cycle. Pending rows must survive a
    claim grace window; processing rows are eligible only after the EventStore
    claim lease has expired. An in-flight reactor event must not be terminalized
    by the screen job that emitted it.
    """

    from src.events.continuous_redecision import REDECISION_EVENT_TYPE as _REDECISION_EVENT_TYPE

    try:
        decision_dt = datetime.fromisoformat(str(decision_time).replace("Z", "+00:00"))
        if decision_dt.tzinfo is None:
            decision_dt = decision_dt.replace(tzinfo=timezone.utc)
        decision_dt = decision_dt.astimezone(timezone.utc)
        if claim_grace_seconds is None:
            pending_grace_seconds = (
                _REDECISION_FRESH_SCREEN_SUPERSEDE_GRACE_SECONDS
                if supersede_stale_admitted
                else _REDECISION_PENDING_EXPIRY_GRACE_SECONDS
            )
            processing_grace_seconds = _REDECISION_PENDING_EXPIRY_GRACE_SECONDS
        else:
            pending_grace_seconds = claim_grace_seconds
            processing_grace_seconds = claim_grace_seconds
        pending_grace_seconds = max(0.0, float(pending_grace_seconds))
        processing_grace_seconds = max(0.0, float(processing_grace_seconds))
        stale_processing_cutoff = (
            decision_dt - timedelta(seconds=processing_grace_seconds)
        ).isoformat()
        pending_admission_cutoff = (
            decision_dt - timedelta(seconds=pending_grace_seconds)
        ).isoformat()
    except Exception:  # noqa: BLE001
        decision_dt = datetime.now(timezone.utc)
        stale_processing_cutoff = ""
        pending_admission_cutoff = ""

    try:
        candidate_generations: dict[str, tuple[str, str, int, str | None, str]] = {}
        if pending_admission_cutoff:
            for row in world_conn.execute(
                """
                    SELECT p.event_id, p.processing_status, p.attempt_count,
                           p.claimed_at, p.updated_at
                     FROM opportunity_event_processing p
                           INDEXED BY idx_opportunity_event_processing_status
                     WHERE p.consumer_name = 'edli_reactor_v1'
                       AND p.processing_status = 'pending'
                     ORDER BY p.updated_at ASC
                     LIMIT 5000
                """,
            ).fetchall():
                event_id = str(row[0] or "")
                if event_id:
                    candidate_generations[event_id] = (
                        event_id,
                        str(row[1] or ""),
                        int(row[2] or 0),
                        None if row[3] is None else str(row[3]),
                        str(row[4] or ""),
                    )
        if stale_processing_cutoff:
            for row in world_conn.execute(
                """
                    SELECT p.event_id, p.processing_status, p.attempt_count,
                           p.claimed_at, p.updated_at
                      FROM opportunity_event_processing p
                           INDEXED BY idx_opportunity_event_processing_pending_retry_floor
                     WHERE p.consumer_name = 'edli_reactor_v1'
                       AND p.processing_status = 'processing'
                       AND p.claimed_at IS NOT NULL
                       AND p.claimed_at <= ?
                     ORDER BY p.claimed_at ASC
                     LIMIT 5000
                """,
                (stale_processing_cutoff,),
            ).fetchall():
                event_id = str(row[0] or "")
                if event_id:
                    candidate_generations[event_id] = (
                        event_id,
                        str(row[1] or ""),
                        int(row[2] or 0),
                        None if row[3] is None else str(row[3]),
                        str(row[4] or ""),
                    )
        candidate_ids = list(candidate_generations)
        rows = []
        for start in range(0, len(candidate_ids), 250):
            chunk = candidate_ids[start : start + 250]
            placeholders = ",".join("?" for _ in chunk)
            rows.extend(
                world_conn.execute(
                    f"""
                    SELECT e.event_id, e.payload_json, e.created_at
                      FROM opportunity_events e
                     WHERE e.event_type = ?
                       AND e.created_at <= ?
                       AND e.received_at <= ?
                       AND e.event_id IN ({placeholders})
                    """,
                    (
                        _REDECISION_EVENT_TYPE,
                        pending_admission_cutoff,
                        pending_admission_cutoff,
                        *chunk,
                    ),
                ).fetchall()
            )
    except Exception:  # noqa: BLE001
        return {}
    expire_by_reason: dict[str, list[tuple[str, str, int, str | None, str]]] = {}
    for row in rows:
        try:
            event_id = str(row[0] or "")
            payload = json.loads(str(row[1] or "{}"))
            event_created_at = str(row[2] or "")
            family = (
                str(payload.get("city") or "").strip(),
                str(payload.get("target_date") or "").strip(),
                str(payload.get("metric") or "").strip(),
            )
        except Exception:  # noqa: BLE001
            continue
        generation = candidate_generations.get(event_id)
        if generation is None or not all(family):
            continue
        if family not in admitted_families:
            if _preserve_recent_rest_pull_redecision(
                payload,
                event_created_at=event_created_at,
                decision_dt=decision_dt,
            ):
                continue
            reason = "REDECISION_ADMISSION_EXPIRED:no_current_edge_or_rest_reprice_value"
            expire_by_reason.setdefault(reason, []).append(generation)
        elif supersede_stale_admitted:
            reason = "REDECISION_SUPERSEDED_BY_FRESH_SCREEN:stale_pending_claim_grace_elapsed"
            expire_by_reason.setdefault(reason, []).append(generation)
    return expire_by_reason


def _edli_apply_unadmitted_redecision_expiry(
    world_conn,
    expire_by_reason: dict[str, list[tuple[str, str, int, str | None, str]]],
    *,
    decision_time: str,
) -> int:
    """Apply a precomputed expiry plan with claim-state CAS predicates."""

    if not expire_by_reason:
        return 0
    now = str(decision_time)
    changed = 0
    for reason, generations in expire_by_reason.items():
        for start in range(0, len(generations), 250):
            chunk = generations[start : start + 250]
            generation_predicates = " OR ".join(
                "(event_id = ? AND processing_status = ? AND attempt_count = ? "
                "AND claimed_at IS ? AND updated_at = ?)"
                for _ in chunk
            )
            generation_params = tuple(
                value for generation in chunk for value in generation
            )
            cur = world_conn.execute(
                f"""
                UPDATE opportunity_event_processing
                   SET processing_status = 'expired',
                       processed_at = ?,
                       updated_at = ?,
                       last_error = ?
                 WHERE consumer_name = 'edli_reactor_v1'
                   AND ({generation_predicates})
                """,
                (now, now, reason, *generation_params),
            )
            changed += int(cur.rowcount or 0)
    return changed


def _edli_expire_unadmitted_redecision_pending(
    world_conn,
    admitted_families: set[tuple[str, str, str]],
    *,
    decision_time: str,
    supersede_stale_admitted: bool = False,
    claim_grace_seconds: float | None = None,
) -> int:
    """Discover then expire rows; callers holding a mutex must use the split API."""

    plan = _edli_plan_unadmitted_redecision_expiry(
        world_conn,
        admitted_families,
        decision_time=decision_time,
        supersede_stale_admitted=supersede_stale_admitted,
        claim_grace_seconds=claim_grace_seconds,
    )
    return _edli_apply_unadmitted_redecision_expiry(
        world_conn,
        plan,
        decision_time=decision_time,
    )


def _edli_redecision_family_keys_from_entity_keys(
    entity_keys: set[str],
) -> set[tuple[str, str, str]]:
    """Extract (city, target_date, metric) keys from pending redecision entity keys."""

    out: set[tuple[str, str, str]] = set()
    for entity_key in entity_keys or set():
        parts = str(entity_key or "").split("|")
        if len(parts) < 3:
            continue
        city = parts[0].strip()
        target_date = parts[1].strip()
        metric = parts[2].strip()
        if city and target_date and metric in {"high", "low"}:
            out.add((city, target_date, metric))
    return out


def run_edli_continuous_redecision_screen_cycle(*, screen_lock) -> None:
    """P2 cheap-screen job (continuous re-decision resurrection 2026-06-12).

    Reads cached beliefs (world, RO) × freshest executable prices (trade, RO), runs the cheap edge
    screen, and ENQUEUES EDLI_REDECISION_PENDING events for families whose edge fired — so the
    reactor re-decides on PRICE movement between forecast cycles (the ~5-6h cadence gap the operator
    flagged). ALSO screens OPEN maker rests (§4.5): a rest whose belief decayed on new evidence, or
    whose book moved/went stale, is pulled (re-decide at fresh price) — the fix for "submitted then
    abandoned" (Busan/Beijing). NO new HTTP: reads only what the warm/fast lanes already persisted;
    the actual cancel reuses the shared venue-cancel-journal path. Fail-soft: never crashes
    the scheduler.

    Wave-1 2026-06-12: the redecision_screen_enabled gate is DELETED. The screen is the
    fill-rate ORGAN, not an optional feature — it now runs whenever the reactor is LIVE and
    event writing is enabled (the same arm conditions that license the reactor itself). Data
    + cancel only; no new submit authority of its own.

    R4-b4 (2026-07-08 main.py slimming): thin scheduler hook extracted. ``screen_lock``
    is the ``threading.Lock`` main.py calls ``_edli_redecision_screen_lock``, injected
    from src.main: it is a cross-job scheduling-coordination primitive (settlement
    attribution and the day0-hourly-refresh cluster also read its ``.locked()`` state),
    so main.py -- the dispatcher -- retains ownership of the Lock object itself. This
    cycle owns the acquire/release lifecycle around its own run, exactly as it did before
    the extraction. ``_edli_redecision_acted_state`` is reach-back-imported rather than
    injected: it is a plain mutable dict with no acquire/release lifecycle, and the
    out-of-scope command-recovery cluster still mutates it directly in main.py, so the
    reach-back import just binds the same live object reference -- no duplication.
    """
    import logging as _logging
    from src.config import get_mode
    from src.main import (
        _defer_for_held_position_monitor,
        _edli_acquire_mutex,
        _edli_emit_lock_timeout_seconds,
        _edli_is_sqlite_lock_error,
        _edli_next_redecision_source,
        _edli_pending_entity_keys,
        _edli_redecision_acted_state,
        _redecision_event_with_origin,
        _settings_section,
    )

    _log = _logging.getLogger("zeus.events.reactor")
    edli_cfg = _settings_section("edli", {})
    if not edli_cfg.get("enabled") or not edli_cfg.get("event_writer_enabled"):
        return
    # Live-armed condition (replaces the deleted redecision_screen_enabled flag): the reactor
    # must be in live mode. When submit is disabled the screen organ stays dark.
    if str(edli_cfg.get("reactor_mode", "live")) != "live":
        return
    if _defer_for_held_position_monitor("edli_redecision_screen"):
        return
    if not screen_lock.acquire(blocking=False):
        _log.info("edli_redecision_screen skipped: previous screen still running")
        return
    try:
        from datetime import datetime, timezone
        from src.events.continuous_redecision import (
            _all_latest_beliefs,
            entry_substrate_refresh_scope,
            filter_redecisions_with_spine_members,
            screen_entry_redecisions,
            screened_family_keys,
            screen_resting_orders,
            REDECISION_EVENT_TYPE,
        )
        from src.state.db import (
            get_world_connection_read_only,
            get_trade_connection_read_only,
            get_world_connection,
            get_forecasts_connection_read_only,
        )

        now = datetime.now(timezone.utc)
        received_at = now.isoformat()
        min_edge = float(edli_cfg.get("redecision_screen_min_edge", 0.01))
        # Wave-1 2026-06-12: redecision_max_per_cycle cap DELETED. The screen re-emit uses the
        # fixed fair-cursor batch (wraps modulo family count → full coverage, no tail drop).
        rd_cap = _EDLI_REDECISION_FAIR_BATCH

        # 1) ENTRY screen + rest screen on RO connections (pure read, no HTTP).
        world_ro = get_world_connection_read_only()
        trade_ro = get_trade_connection_read_only()
        try:
            all_beliefs = _all_latest_beliefs(
                world_ro,
                decision_time=received_at,
                forecast_only_admissible=True,
            )
            beliefs, screened_belief_keys, total_beliefs = _edli_redecision_screen_belief_batch(
                all_beliefs,
                max_families=rd_cap,
            )
            if total_beliefs and len(beliefs) < total_beliefs:
                _log.info(
                    "edli_redecision_screen: entry belief fair batch size=%d total=%d cursor=%d",
                    len(beliefs),
                    total_beliefs,
                    _edli_redecision_screen_belief_cursor,
                )
            probe_acted_state = dict(_edli_redecision_acted_state)
            redecisions = screen_entry_redecisions(
                world_ro,
                trade_ro,
                decision_time=received_at,
                min_edge=min_edge,
                acted_state=probe_acted_state,
                beliefs=beliefs,
            )
            try:
                forecasts_filter_ro = get_forecasts_connection_read_only()
                try:
                    entry_redecisions = filter_redecisions_with_spine_members(
                        forecasts_filter_ro,
                        redecisions,
                        beliefs=beliefs,
                        decision_time=received_at,
                    )
                finally:
                    forecasts_filter_ro.close()
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "edli_redecision_screen: spine availability read failed; "
                    "entry redecisions not admitted this tick: %r",
                    exc,
                )
                entry_redecisions = []
            raw_entry_family_keys = screened_family_keys(world_ro, entry_redecisions, beliefs=beliefs)
            # Open maker rests are already-live order-management obligations.
            # They must be screened every cycle even when their family is outside
            # the entry fair-batch cursor; the fair batch limits new entry scans,
            # not management of submitted GTC rests that hold the submit mutex.
            open_rests = _edli_open_maker_rests_for_screen(
                trade_ro,
                world_ro,
                beliefs=all_beliefs,
            )
            entry_refresh_condition_scope = entry_substrate_refresh_scope(
                trade_ro,
                beliefs=beliefs,
                decision_time=received_at,
                max_families=rd_cap,
                min_edge=min_edge,
            )
            rest_pulls = screen_resting_orders(
                world_ro,
                trade_ro,
                open_rests=open_rests,
                decision_time=received_at,
            )
            entry_condition_scope = _edli_redecision_condition_scope(entry_redecisions, beliefs)
            open_rest_condition_scope = _edli_open_rest_condition_scope(open_rests, all_beliefs)
            rest_condition_scope = _edli_rest_pull_condition_scope(rest_pulls, beliefs)
        finally:
            try:
                world_ro.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                trade_ro.close()
            except Exception:  # noqa: BLE001
                pass

        # A rest-pull family must also re-decide (cancel + re-decide at fresh price). Add its
        # family key to the re-emit restriction so the reactor re-certifies it; the cancel itself
        # runs through the shared venue-cancel-journal path below.
        rest_pull_families: set = set()
        if rest_pulls:
            by_family = {
                b.family_id: (b.city, b.target_date, b.metric) for b in all_beliefs
            }
            for rest, _decision in rest_pulls:
                key = _edli_family_key_from_rest(rest) or by_family.get(rest.family_id)
                if key is not None and all(key):
                    rest_pull_families.add(key)
        held_families = _edli_current_held_position_family_keys()
        family_keys = _edli_entry_redecision_family_keys(
            raw_entry_family_keys,
            held_families,
            decision_time=now,
        )
        entry_refresh_families = set(entry_refresh_condition_scope)
        held_reemit_families = _edli_reemittable_held_position_family_keys(
            held_families,
            decision_time=now,
        )
        held_condition_scope = _edli_current_held_position_family_condition_scope(
            held_reemit_families
        )
        all_families = set(family_keys) | rest_pull_families | held_reemit_families
        confirmed_entry_scope = set(family_keys) | entry_refresh_families
        confirmed_rest_scope = set(rest_pull_families)
        confirmed_held_scope = set(held_reemit_families)
        held_refresh_families = set(held_condition_scope)
        confirm_families = set(all_families) | held_refresh_families | entry_refresh_families
        fresh_entry_scope = _edli_families_with_fresh_scoped_executable_substrate(
            _edli_merge_condition_scopes(
                entry_condition_scope,
                entry_refresh_condition_scope,
            ),
            now_utc=now,
        )
        fresh_rest_scope = _edli_families_with_fresh_scoped_executable_substrate(
            rest_condition_scope,
            now_utc=now,
        )
        fresh_held_scope = _edli_families_with_fresh_scoped_executable_substrate(
            held_condition_scope,
            now_utc=now,
        )
        fresh_confirmed_families = (
            fresh_entry_scope | fresh_rest_scope | fresh_held_scope
        )
        requested_confirm_families = set(confirm_families)
        missing_confirm_families = (
            requested_confirm_families - fresh_confirmed_families
        )
        confirm_refresh_summary: dict = {}
        if missing_confirm_families:
            def _missing_scope(scope):
                return {
                    family: condition_ids
                    for family, condition_ids in scope.items()
                    if family in missing_confirm_families
                }

            priority_condition_ids = _edli_confirm_priority_condition_ids(
                rest_condition_scope=_missing_scope(rest_condition_scope),
                held_condition_scope=_missing_scope(held_condition_scope),
                entry_condition_scope=_missing_scope(entry_condition_scope),
                entry_refresh_condition_scope=_missing_scope(
                    entry_refresh_condition_scope
                ),
                open_rest_condition_scope=_missing_scope(open_rest_condition_scope),
                full_family_refresh_families=(
                    held_reemit_families & missing_confirm_families
                ),
            )
            confirm_refresh_summary = _edli_refresh_continuous_money_path_families(
                missing_confirm_families,
                priority_condition_ids=priority_condition_ids,
            )
        elif requested_confirm_families:
            confirm_refresh_summary = {
                "status": "already_fresh",
                "families_requested": 0,
                "executable_substrate_coverage_status": "FULL",
            }
        if requested_confirm_families:
            confirmed_entry_scope &= fresh_entry_scope
            confirmed_rest_scope &= fresh_rest_scope
            confirmed_held_scope &= fresh_held_scope
            confirm_families &= fresh_confirmed_families
            scoped_filter_reason = (
                "async_confirmation_requested"
                if missing_confirm_families
                else "current_substrate_verified"
            )
            _log.info(
                "edli_redecision_screen: %s admitted fresh scoped families=%d/%d "
                "entry_scope=%d rest_scope=%d held_scope=%d entry_conditions=%d "
                "rest_conditions=%d held_conditions=%d summary=%r",
                scoped_filter_reason,
                len(fresh_confirmed_families),
                len(requested_confirm_families),
                len(confirmed_entry_scope),
                len(confirmed_rest_scope),
                len(confirmed_held_scope),
                sum(len(v) for v in entry_condition_scope.values())
                + sum(len(v) for v in entry_refresh_condition_scope.values()),
                sum(len(v) for v in rest_condition_scope.values()),
                sum(len(v) for v in held_condition_scope.values()),
                confirm_refresh_summary,
            )
            if not confirmed_entry_scope and not confirmed_rest_scope and not confirmed_held_scope:
                from src.state.db import world_write_mutex as _world_write_mutex

                expiry_ro = get_world_connection_read_only()
                try:
                    expiry_plan = _edli_plan_unadmitted_redecision_expiry(
                        expiry_ro,
                        set(),
                        decision_time=received_at,
                    )
                finally:
                    expiry_ro.close()
                emit_mutex = _world_write_mutex()
                emit_lock_timeout_s = _edli_emit_lock_timeout_seconds(edli_cfg)
                emit_acquired = False
                world = None
                expired_unadmitted = 0
                try:
                    emit_acquired = _edli_acquire_mutex(emit_mutex, timeout=emit_lock_timeout_s)
                    if emit_acquired:
                        world = get_world_connection()
                        expired_unadmitted = _edli_apply_unadmitted_redecision_expiry(
                            world,
                            expiry_plan,
                            decision_time=received_at,
                        )
                        world.commit()
                    else:
                        _log.warning(
                            "edli_redecision_screen: no-fresh stale-pending expiry "
                            "skipped because world write mutex was unavailable after %.3fs",
                            emit_lock_timeout_s,
                        )
                finally:
                    if world is not None:
                        try:
                            world.close()
                        except Exception:  # noqa: BLE001
                            pass
                    if emit_acquired:
                        emit_mutex.release()
                _log.info(
                    "edli_redecision_screen: confirmation refresh produced no fresh "
                    "screened money-path substrate; skipping emit this tick rather "
                    "than queueing stale redecision families=%d expired_unadmitted=%d",
                    len(set(all_families) | held_refresh_families),
                    expired_unadmitted,
                )
                return

            # Re-run the screen against the freshly refreshed money-path
            # substrate. The initial pass only chooses the confirmation scope;
            # this second pass is the value authority for emitted redecision rows.
            world_ro = get_world_connection_read_only()
            trade_ro = get_trade_connection_read_only()
            try:
                all_beliefs = _all_latest_beliefs(
                    world_ro,
                    decision_time=received_at,
                    forecast_only_admissible=True,
                )
                beliefs = _edli_filter_beliefs_to_family_keys(
                    all_beliefs,
                    screened_belief_keys,
                )
                redecisions = screen_entry_redecisions(
                    world_ro,
                    trade_ro,
                    decision_time=received_at,
                    min_edge=min_edge,
                    acted_state=_edli_redecision_acted_state,
                    beliefs=beliefs,
                )
                try:
                    forecasts_filter_ro = get_forecasts_connection_read_only()
                    try:
                        entry_redecisions = filter_redecisions_with_spine_members(
                            forecasts_filter_ro,
                            redecisions,
                            beliefs=beliefs,
                            decision_time=received_at,
                        )
                    finally:
                        forecasts_filter_ro.close()
                except Exception as exc:  # noqa: BLE001
                    _log.warning(
                        "edli_redecision_screen: post-confirm spine availability read failed; "
                        "entry redecisions not admitted this tick: %r",
                        exc,
                    )
                    entry_redecisions = []
                raw_entry_family_keys = screened_family_keys(world_ro, entry_redecisions, beliefs=beliefs)
                open_rests = _edli_open_maker_rests_for_screen(
                    trade_ro,
                    world_ro,
                    beliefs=all_beliefs,
                )
                rest_pulls = screen_resting_orders(
                    world_ro,
                    trade_ro,
                    open_rests=open_rests,
                    decision_time=received_at,
                )
                entry_condition_scope = _edli_redecision_condition_scope(entry_redecisions, beliefs)
                rest_condition_scope = _edli_rest_pull_condition_scope(rest_pulls, beliefs)
            finally:
                try:
                    world_ro.close()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    trade_ro.close()
                except Exception:  # noqa: BLE001
                    pass

            rest_pull_families = set()
            if rest_pulls:
                by_family = {
                    b.family_id: (b.city, b.target_date, b.metric) for b in all_beliefs
                }
                for rest, _decision in rest_pulls:
                    key = _edli_family_key_from_rest(rest) or by_family.get(rest.family_id)
                    if key is not None and all(key):
                        rest_pull_families.add(key)
            rest_pull_families &= confirmed_rest_scope
            if rest_pull_families:
                rest_pull_families &= _edli_families_with_fresh_scoped_executable_substrate(
                    rest_condition_scope,
                    now_utc=now,
                )
            held_families = _edli_current_held_position_family_keys()
            family_keys = _edli_entry_redecision_family_keys(
                raw_entry_family_keys,
                held_families,
                decision_time=now,
            )
            family_keys &= confirmed_entry_scope
            if family_keys:
                family_keys &= _edli_families_with_fresh_scoped_executable_substrate(
                    entry_condition_scope,
                    now_utc=now,
                )
            held_reemit_families = _edli_reemittable_held_position_family_keys(
                held_families,
                decision_time=now,
            )
            held_reemit_families &= confirmed_held_scope
            if held_reemit_families:
                held_reemit_families &= _edli_families_with_fresh_scoped_executable_substrate(
                    _edli_current_held_position_family_condition_scope(held_reemit_families),
                    now_utc=now,
                )
            all_families = set(family_keys) | rest_pull_families | held_reemit_families
        expired_unadmitted = 0
        expired_stale_pending = 0
        expired_rest_pull_blockers = 0
        if not all_families:
            from src.state.db import world_write_mutex as _world_write_mutex

            expiry_ro = get_world_connection_read_only()
            try:
                expiry_plan = _edli_plan_unadmitted_redecision_expiry(
                    expiry_ro,
                    set(),
                    decision_time=received_at,
                )
            finally:
                expiry_ro.close()
            emit_mutex = _world_write_mutex()
            emit_lock_timeout_s = _edli_emit_lock_timeout_seconds(edli_cfg)
            emit_acquired = False
            world = None
            try:
                emit_acquired = _edli_acquire_mutex(emit_mutex, timeout=emit_lock_timeout_s)
                if emit_acquired:
                    world = get_world_connection()
                    expired_unadmitted = _edli_apply_unadmitted_redecision_expiry(
                        world,
                        expiry_plan,
                        decision_time=received_at,
                    )
                    world.commit()
                else:
                    _log.warning(
                        "edli_redecision_screen: stale-pending expiry skipped because "
                        "world write mutex was unavailable after %.3fs",
                        emit_lock_timeout_s,
                    )
            finally:
                if world is not None:
                    try:
                        world.close()
                    except Exception:  # noqa: BLE001
                        pass
                if emit_acquired:
                    emit_mutex.release()
            _log.info(
                "edli_redecision_screen: entry_candidates=%d entry_spine_confirmed=%d "
                "entry_families=0 rest_pulls=%d "
                "held_monitor_families=%d held_reemit_families=0 families_reemitted=0 "
                "events_emitted=0 rests_cancelled=0 expired_unadmitted=%d reason=no_screened_families",
                len(redecisions),
                len(entry_redecisions),
                len(rest_pulls),
                len(held_families),
                expired_unadmitted,
            )
            return

        # 2) EMIT EDLI_REDECISION_PENDING for the screened families (world write, under the mutex,
        #    no HTTP) — routed through the EXISTING FSR re-emit machinery (restrict_to_families).
        from src.events.event_writer import EventWriter
        from src.events.triggers.forecast_snapshot_ready import (
            ForecastSnapshotReadyTrigger,
            executable_forecast_live_eligible_reader,
        )
        from src.state.db import world_write_mutex as _world_write_mutex

        forecasts_ro = get_forecasts_connection_read_only()
        world_scan_ro = None
        try:
            expiry_ro = get_world_connection_read_only()
            try:
                stale_plan = _edli_plan_unadmitted_redecision_expiry(
                    expiry_ro,
                    set(all_families),
                    decision_time=received_at,
                    supersede_stale_admitted=True,
                )
            finally:
                expiry_ro.close()
            prune_mutex = _world_write_mutex()
            prune_lock_timeout_s = _edli_emit_lock_timeout_seconds(edli_cfg)
            prune_acquired = _edli_acquire_mutex(prune_mutex, timeout=prune_lock_timeout_s)
            if not prune_acquired:
                _log.warning(
                    "edli_redecision_screen skipped: world write mutex unavailable "
                    "for stale-pending prune after %.3fs; no venue side effect attempted.",
                    prune_lock_timeout_s,
                )
                return
            world_prune = None
            try:
                world_prune = get_world_connection()
                expired_stale_pending = _edli_apply_unadmitted_redecision_expiry(
                    world_prune,
                    stale_plan,
                    decision_time=received_at,
                )
                expired_rest_pull_blockers = (
                    _edli_supersede_pending_redecisions_for_rest_pull_families(
                        world_prune,
                        rest_pull_families,
                        decision_time=received_at,
                    )
                )
                world_prune.commit()
            finally:
                if world_prune is not None:
                    try:
                        world_prune.close()
                    except Exception:  # noqa: BLE001
                        pass
                prune_mutex.release()
            world_scan_ro = get_world_connection_read_only()
            pending = _edli_pending_entity_keys(world_scan_ro, event_types=(REDECISION_EVENT_TYPE,))
            pending_families = _edli_redecision_family_keys_from_entity_keys(pending)
            emit_families = set(all_families) - pending_families
            if emit_families:
                trig = ForecastSnapshotReadyTrigger(
                    EventWriter(world_scan_ro),
                    live_eligibility_reader=executable_forecast_live_eligible_reader(forecasts_ro),
                )
                events_to_emit = trig.build_committed_snapshot_events(
                    forecasts_conn=forecasts_ro,
                    decision_time=now,
                    received_at=received_at,
                    limit=rd_cap,
                    source=_edli_next_redecision_source(),
                    already_pending_keys=pending,
                    event_type=REDECISION_EVENT_TYPE,
                    restrict_to_families=emit_families,
                    phase_filter_exempt_families=set(),
                )
            else:
                events_to_emit = []
        finally:
            try:
                forecasts_ro.close()
            except Exception:  # noqa: BLE001
                pass
            if world_scan_ro is not None:
                try:
                    world_scan_ro.close()
                except Exception:  # noqa: BLE001
                    pass

        emit_mutex = _world_write_mutex()
        emit_lock_timeout_s = _edli_emit_lock_timeout_seconds(edli_cfg)
        emit_acquired = False
        world = None
        expiry_ro = get_world_connection_read_only()
        try:
            expiry_plan = _edli_plan_unadmitted_redecision_expiry(
                expiry_ro,
                set(all_families),
                decision_time=received_at,
            )
        finally:
            expiry_ro.close()
        try:
            emit_acquired = _edli_acquire_mutex(emit_mutex, timeout=emit_lock_timeout_s)
            if not emit_acquired:
                _log.warning(
                    "edli_redecision_screen skipped: world write mutex unavailable "
                    "for redecision emit after %.3fs; no venue side effect attempted.",
                    emit_lock_timeout_s,
                )
                return
            world = get_world_connection()
            expired_unadmitted = _edli_apply_unadmitted_redecision_expiry(
                world,
                expiry_plan,
                decision_time=received_at,
            )
            expired_rest_pull_blockers += (
                _edli_supersede_pending_redecisions_for_rest_pull_families(
                    world,
                    rest_pull_families,
                    decision_time=received_at,
                )
            )
            fresh_events = []
            for event in events_to_emit:
                if event.entity_key in pending:
                    continue
                try:
                    payload = json.loads(str(event.payload_json or "{}"))
                    event_family = (
                        str(payload.get("city") or "").strip(),
                        str(payload.get("target_date") or "").strip(),
                        str(payload.get("metric") or "").strip(),
                    )
                except Exception:  # noqa: BLE001
                    event_family = ("", "", "")
                if event_family in rest_pull_families:
                    fresh_events.append(_redecision_event_with_origin(event, "rest_pull"))
                elif event_family in held_reemit_families:
                    fresh_events.append(_redecision_event_with_origin(event, "held_position"))
                elif event_family in family_keys:
                    fresh_events.append(_redecision_event_with_origin(event, "entry_screen"))
            emitted = EventWriter(world).write_many(fresh_events)
            world.commit()
        finally:
            if world is not None:
                try:
                    world.close()
                except Exception:  # noqa: BLE001
                    pass
            if emit_acquired:
                emit_mutex.release()

        # 3) CANCEL the pulled rests via the EXISTING shared venue-cancel-journal path (no new
        #    venue call site). The next reactor cycle re-decides the re-emitted family at fresh price.
        cancelled = 0
        if rest_pulls and get_mode() == "live":
            from src.data.polymarket_client import PolymarketClient
            from src.execution.venue_cancel_journal import run_persisted_cancels_for_expired_rests
            from src.state.db import get_trade_connection

            to_cancel = [
                {"command_id": rest.command_id, "venue_order_id": rest.venue_order_id,
                 "created_at": rest.created_at, "fact_state": rest.fact_state,
                 "matched_size": rest.matched_size, "cancel_reason": decision.reason,
                 "cancel_action": decision.action, "cancel_detail": decision.detail}
                for rest, decision in rest_pulls
            ]
            cstats = run_persisted_cancels_for_expired_rests(
                to_cancel,
                PolymarketClient(),
                conn_factory=lambda: get_trade_connection(write_class="live"),
            )
            cancelled = cstats.get("cancelled", 0)

        _log.info(
            "edli_redecision_screen: entry_candidates=%d entry_spine_confirmed=%d "
            "entry_families=%d rest_pulls=%d "
            "held_monitor_families=%d held_reemit_families=%d families_reemitted=%d "
            "pending_redecision_families=%d suppressed_existing_pending=%d "
            "events_emitted=%d rests_cancelled=%d expired_unadmitted=%d "
            "expired_stale_pending=%d expired_rest_pull_blockers=%d",
            len(redecisions), len(entry_redecisions), len(family_keys), len(rest_pulls), len(held_families),
            len(held_reemit_families),
            len(all_families),
            len(pending_families),
            len(set(all_families) & pending_families),
            len(emitted), cancelled, expired_unadmitted, expired_stale_pending,
            expired_rest_pull_blockers,
        )
        if confirm_refresh_summary:
            _log.info(
                "edli_redecision_screen: confirmation_refresh_summary=%r",
                confirm_refresh_summary,
            )
    except sqlite3.OperationalError as exc:
        if not _edli_is_sqlite_lock_error(exc):
            raise
        _log.warning(
            "edli_redecision_screen skipped: database locked during read/write "
            "coordination; no venue side effect attempted and next tick will retry: %s",
            exc,
        )
    finally:
        screen_lock.release()


# ---------------------------------------------------------------------------
# R4-b4 (2026-07-08 main.py slimming): pre-submit-JIT warm-CLOB-client cluster,
# extracted from src/main.py's book/warmup timeout helpers, the
# _PRE_SUBMIT_JIT_CLOB_CLIENT singleton (construct/reset), the prewarm helper,
# and the keepalive-pinger scheduler job. main.py's scheduler hook is now a
# thin delegating call. R4-b3 already moved this cluster's consumer --
# _edli_pre_submit_jit_book_quote_provider below -- and at the time kept the
# singleton in main.py because it was shared with this pinger; now that the
# pinger has moved too, every consumer of the singleton lives in this module,
# so the former reach-back import in that function is deleted below.
# _edli_pre_submit_clob_timeout_seconds is still reach-back-imported from
# main.py: it is genuinely used broadly outside this cluster (three other
# reach-back sites already in this module).
# ---------------------------------------------------------------------------
def _edli_pre_submit_jit_book_timeout():
    """STRICT connect/read timeout for the submit-time JIT ``/book`` fetch (GATE #84).

    Runs inside the pre-submit guard's worker thread, so it must fail-closed BEFORE
    the outer daemon guard (the 2026-06-19 invariant). httpcore applies the connect
    timeout to ``connect_tcp`` AND ``start_tls`` separately, so the worst-case connect
    cost is ``2*connect``; bound ``2*connect + read + write + pool`` strictly under the
    outer guard. With outer=6.0 this yields connect≈2.25 (worst case 2*2.25+0.85+0.25+
    0.10 = 5.70 < 6.0). This connect budget is a FAIL-CLOSED bound, not the normal
    path — the boot pre-warm + keepalive pinger keep the socket warm so the submit-time
    fetch reuses an established connection (~0.66s, measured forward 2026-06-22) and
    does not pay a cold handshake here. Cold handshakes (~2.2-2.7s) are absorbed by the
    generous warmup timeout OUTSIDE the worker.
    """

    import httpx
    from src.main import _edli_pre_submit_clob_timeout_seconds

    outer = _edli_pre_submit_clob_timeout_seconds()
    # A 0.55s read cap was below observed live /book tail latency and caused the
    # armed submit path to fall back to stale DB feasibility rows. Keep the full
    # worst-case httpcore budget inside the outer guard, but give the warm read
    # enough room to complete on real CLOB tails.
    read, write, pool = 1.75, 0.20, 0.08
    connect = max(0.25, min(1.80, (outer - read - write - pool - 0.25) / 2.0))
    return httpx.Timeout(connect=connect, read=read, write=write, pool=pool)




def _edli_pre_submit_jit_warmup_timeout():
    """GENEROUS connect timeout for the JIT client's boot pre-warm + keepalive ping.

    Used ONLY by ``_edli_prewarm_pre_submit_jit_client`` / the pinger tick, which run
    OUTSIDE the submit worker, so a connect budget large enough to absorb a cold TLS
    handshake (~2.2-2.7s, with margin) does not threaten the pre-submit outer guard.
    The read budget stays tight (the ``/time`` health probe is tiny).
    """

    import httpx

    return httpx.Timeout(connect=4.5, read=0.75, write=0.25, pool=0.10)


_PRE_SUBMIT_JIT_CLOB_CLIENT = None
_PRE_SUBMIT_JIT_CLOB_CLIENT_LOCK = threading.Lock()


def _edli_reset_pre_submit_jit_clob_client():
    """Drop and close the warm JIT CLOB client (clean shutdown + test isolation)."""

    global _PRE_SUBMIT_JIT_CLOB_CLIENT
    with _PRE_SUBMIT_JIT_CLOB_CLIENT_LOCK:
        client = _PRE_SUBMIT_JIT_CLOB_CLIENT
        _PRE_SUBMIT_JIT_CLOB_CLIENT = None
    if client is not None:
        try:
            client.close()
        except Exception:  # noqa: BLE001 - best-effort close on shutdown
            pass


def _edli_pre_submit_jit_clob_client():
    """Return a WARM, reused CLOB client for the submit-time JIT ``/book`` fetch.

    Reusing one client keeps its TLS connection warm (httpx keepalive) across
    submit candidates, so each fetch skips the ~2.2-2.7s cold handshake that timed
    out 118/120 submits (warm reuse drops the fetch to ~0.66s, measured forward
    2026-06-22). Thread-safe: httpx.Client is safe to share across the pre-submit
    guard's worker threads; construction is lock-guarded (double-checked).
    """

    global _PRE_SUBMIT_JIT_CLOB_CLIENT
    client = _PRE_SUBMIT_JIT_CLOB_CLIENT
    if client is None:
        with _PRE_SUBMIT_JIT_CLOB_CLIENT_LOCK:
            client = _PRE_SUBMIT_JIT_CLOB_CLIENT
            if client is None:
                from src.data.polymarket_client import (
                    PRESUBMIT_JIT_CLOB_HTTP_LIMITS,
                    PolymarketClient,
                )

                client = PolymarketClient(
                    public_http_timeout=_edli_pre_submit_jit_book_timeout(),
                    public_http_limits=PRESUBMIT_JIT_CLOB_HTTP_LIMITS,
                )
                _PRE_SUBMIT_JIT_CLOB_CLIENT = client
    return client


def _edli_prewarm_pre_submit_jit_client() -> bool:
    """Construct the warm JIT CLOB client and complete a cold TLS handshake OUTSIDE
    the submit worker (boot + keepalive pinger). Uses the generous warmup timeout so
    a slow cold handshake is absorbed here, never on the money path. Fail-soft."""

    try:
        client = _edli_pre_submit_jit_clob_client()
        ok = client.warm_public_connection(timeout=_edli_pre_submit_jit_warmup_timeout())
        return bool(ok)
    except Exception:  # noqa: BLE001 - pre-warm is best-effort; never block boot
        return False


def run_edli_presubmit_jit_keepalive_cycle() -> None:
    """Keepalive pinger: keep the submit-time JIT CLOB connection warm across reactor
    cycles (keepalive_expiry=90s > 60s cycle) so an edge-positive submit candidate
    never pays a cold TLS handshake at the pre-submit gate. Read-only /time probe;
    touches NO trading state; logs success/failure only (GATE #84, 2026-06-22)."""
    import logging as _logging

    _log = _logging.getLogger("zeus.events.reactor")

    warmed = _edli_prewarm_pre_submit_jit_client()
    if warmed:
        _log.debug("pre-submit JIT keepalive: connection warm")
    else:
        _log.warning("pre-submit JIT keepalive: warm-up probe failed (will retry next tick)")
