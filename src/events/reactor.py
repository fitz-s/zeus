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
import os
import sqlite3
import time
from dataclasses import dataclass, field, replace as dataclass_replace
from datetime import datetime, timezone
from typing import Any, Callable

from src.decision_kernel import claims
from src.events.event_store import EventStore
from src.events.opportunity_event import OpportunityEvent, assert_available_for_decision
from src.state.db import world_write_mutex
from src.strategy.live_inference.live_admission import (
    live_buy_no_conservative_evidence_rejection_reason,
)

UTC = timezone.utc

DEFAULT_REACTOR_CYCLE_BUDGET_SECONDS = 30.0


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


DEFAULT_REACTOR_DRAIN_BUDGET_SECONDS = 10.0


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

# VENUE-CLOSE HORIZON eligibility (freshness-throughput starvation fix 2026-06-14,
# #92 / docs/evidence/deadloop_2026-06-14/binding_wall.md). The geometric
# venue-close terminal (horizon b) applies to EVERY family-keyed event that binds a
# (city, target_date) weather market — NOT only the forecast-decision lane.
# DAY0_EXTREME_UPDATED is family-keyed (carries city + target_date + metric) but is
# NOT a forecast-decision type, so the prior horizon scope (forecast-decision types
# only) NEVER terminalized a past-close DAY0 event: EventStore._is_timely returns
# True for non-forecast-decision types (event_store.py L803) AND the venue-close
# horizon skipped them (reactor L959), so a past-close DAY0 event whose market
# settled at the F1 12:00-UTC close requeued FOREVER on EXECUTABLE_SNAPSHOT_BLOCKED.
# Live 2026-06-14 19:36Z (daemon pid 8058): 4903 of 5180 pending events were
# past-close DAY0_EXTREME_UPDATED (target_date 06-12/06-13), monopolizing the
# reactor working set so the ~277 live 06-15 families were starved (processed≈0).
# The venue-close predicate is purely geometric (city tz + target_date + F1 anchor)
# and returns None for any live future-close family, so widening the scope can NEVER
# terminalize a genuinely-live family — it only sweeps the dead past-close clog out
# of the working set. The forecast-decision set keeps its other (non-horizon)
# semantics (source-truth treatment, _is_timely floor) unchanged.
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
    strategy_key: str | None = None
    # Shadow-only Opportunity Book selector evidence. Omitted from receipt_json
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
    # Twin-authority reconciliation #7 (2026-06-11): the family settlement-backward
    # coverage VERDICT status ("LICENSED"/"UNLICENSED"/"INSUFFICIENT_DATA"; None on
    # canonical/legacy receipts). Mirrors same_bin_yes_posterior's travel exactly:
    # the ADAPTER admission gate evaluated buy-NO conservative evidence WITH this
    # verdict, so the receipt-level re-enforcement (_receipt_money_path_blocker)
    # MUST see the SAME value — a starved receipt-level twin would re-reject every
    # coverage-licensed buy_no it had just admitted (the 21a4c14ee2 lesson).
    # Omitted-when-None from receipt_json so existing hashes stay byte-stable.
    settlement_coverage_status: str | None = None
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
    #   "NO_SUBMIT_ADAPTER" — the no-submit adapter ran (the degrade lane). On a
    #                          full-pass its reason names the degrade cause that drove
    #                          the selector off the live lane (NO_SUBMIT_ADAPTER_LANE:
    #                          <cause>), NEVER the default literal.
    # This is DECISION provenance (which lane decided), not transport metadata, so it
    # IS serialized into receipt_json. None on legacy / pre-stamp receipts; omit-when-
    # None in receipt_json keeps existing receipt_hash byte-stable, and readers MUST
    # tolerate its absence (only NEW writes carry/enforce it).
    submit_lane: str | None = None
    # C2 SELECTION SHRINKAGE SHADOW (task #60, 2026-06-13). The trading-path
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
    # When the replacement flag is OFF these are SHADOW-only (computed + stamped,
    # selection unchanged). DECISION provenance, so serialized into receipt_json;
    # None on legacy / gate-reject receipts; omit-when-None keeps existing
    # receipt_hash byte-stable, mirroring submit_lane / envelope_json travel.
    lfsr: float | None = None
    edge_shrunk: float | None = None
    edge_shrunk_posterior_sd: float | None = None
    selection_authority: str | None = None

    def __post_init__(self) -> None:
        if self.proof_accepted is None:
            object.__setattr__(self, "proof_accepted", bool(self.submitted))


Submit = Callable[[OpportunityEvent, datetime], bool | None | EventSubmissionReceipt]


class LiveLaneDarkInvariantError(RuntimeError):
    """Raised at the no-submit persist boundary when a full-pass receipt would be
    booked as accepted while the live lane was nominally armed and stamped LIVE.

    The combination (nominally-armed live daemon + proof_accepted=True +
    side_effect_status=NO_SUBMIT + submit_lane="LIVE") is IMPOSSIBLE for a genuine
    full-pass: the live lane either SUBMITs, returns a SUBMIT_DISABLED build, or
    carries a TYPED abort reason (never a default no-submit). If this combination
    reaches persistence the live lane silently ate a tradeable full-pass entry —
    the 2026-06-12 11:51-12:12Z silent-kill incident — so we RAISE instead of
    persisting a kill indistinguishable from normal no-submit accounting.

    A no-submit receipt produced by the legitimate degrade lane carries
    submit_lane="NO_SUBMIT_ADAPTER" + a named degrade cause and is NOT impacted by
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
        held_family_provider: "Callable[[], frozenset[tuple[str, str, str]]] | None" = None,
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
        # ORDERING (operator correction 2026-06-12): refresh fan-out is NOT liquidity-ordered —
        # opportunity is uncorrelated with liquidity (small markets can carry denser sophisticated
        # competition; liquidity's only role stays in sizing/fill). The ONLY ordering bias is
        # HELD-POSITION-FIRST: a family with money at risk RIGHT NOW (the exit monitor reads its
        # belief) is refreshed before new-money families. ``held_family_provider`` returns the
        # current held (city, target_date, metric) set, read-only and fail-soft (absent / raising =>
        # no held bias, pure fair rotation). The reactor owns zeus-world only; this provider is
        # injected (it reads zeus_trades.position_current) so the reactor never opens a trades conn.
        self._held_family_provider = held_family_provider
        # Per-family debounce: family-key -> last successful refresh-attempt monotonic time. The
        # window is DERIVED from the snapshot freshness window (half of it), never a magic number.
        self._family_refresh_last_at: dict[str, float] = {}
        self._family_cycle_advance_last_at: dict[str, float] = {}
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

    def process_pending(self, *, decision_time: datetime, limit: int | None = 100) -> ReactorResult:
        result = ReactorResult()
        # ALWAYS-DECIDABLE invariant (2026-06-12): families blocked on a refreshable substrate
        # THIS cycle, accumulated during processing and drained AFTER all per-event units of work
        # close (no network inside any open world/trade txn). Per-cycle scope so a family that
        # un-blocks stops being refreshed.
        self._pending_snapshot_refreshes: list[tuple[str, str, str]] = []
        self._pending_cycle_advances: list[tuple[str, str, str]] = []
        # E1 (STEP 8): per-cycle wall-clock budget. A cycle must not run unbounded;
        # once the budget is exceeded, stop after the current event and leave the
        # rest PENDING (not consumed, not dropped) for the next cycle. This caps a
        # cycle so the scheduler never hits "max running instances reached" and
        # fresh candidates (freshest-target-first, STEP 3) are reached promptly.
        # Default 45s; override via ZEUS_REACTOR_CYCLE_BUDGET_SECONDS.
        budget = _cycle_budget_seconds()
        cycle_start = time.monotonic()
        batch_limit = 250 if limit is None else max(1, int(limit))
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
            events = self._store.fetch_pending(
                decision_time=decision_time.astimezone(UTC).isoformat(),
                limit=request_limit,
                day0_is_tradeable=self._config.day0_is_tradeable,
            )
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

    def _process_event_unit(
        self,
        event: OpportunityEvent,
        *,
        decision_time: datetime,
        result: ReactorResult,
    ) -> None:
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
        mutex = world_write_mutex()
        mutex.acquire()
        pre_disposition: str | None
        should_submit = False
        try:
            try:
                # CLAIM-STORM FIX (2026-06-11 17:51Z): acquire the WAL write lock
                # DETERMINISTICALLY with BEGIN IMMEDIATE (full busy_timeout engaged
                # at BEGIN) instead of letting claim()'s UPDATE upgrade lazily.
                # A lazily-upgraded txn whose snapshot predates another writer's
                # commit fails SQLITE_BUSY_SNAPSHOT IMMEDIATELY — the busy handler
                # never engages for snapshot-upgrade conflicts — which is how one
                # bounced claim poisoned every later claim in the cycle. Mirrors
                # Window B's BEGIN IMMEDIATE discipline (line ~497).
                if not self._store.conn.in_transaction:
                    self._store.conn.execute("BEGIN IMMEDIATE")
                claimed = self._store.claim(event.event_id, claimed_at=decision_time.astimezone(UTC).isoformat())
            except Exception as exc:
                if _is_sqlite_lock_error(exc):
                    # CLAIM-STORM FIX (storm amplifier): ALWAYS roll back. The old
                    # path returned with the implicit txn left OPEN on the store
                    # conn; the next fetch_pending then read INSIDE that dangling
                    # txn, pinning a stale snapshot, and every subsequent claim
                    # failed BUSY_SNAPSHOT instantly => the whole-cycle 0/250
                    # bounce storm. Rollback resets the conn so the next event
                    # starts a fresh txn under the full busy handler.
                    _was_in_txn = bool(getattr(self._store.conn, "in_transaction", False))
                    with contextlib.suppress(Exception):
                        self._store.conn.rollback()
                    result.claim_lock_bounces += 1
                    result.retried += 1
                    import logging as _logging

                    _logging.getLogger("zeus.events.reactor").warning(
                        "reactor claim lock-bounce event_id=%s txn_open_at_bounce=%s exc=%s "
                        "(rolled back; event stays pending; counted in claim_lock_bounces)",
                        event.event_id,
                        _was_in_txn,
                        exc,
                    )
                    return
                raise
            if not claimed:
                # Claim lost (another worker / lease not yet stale): release any
                # open txn and the mutex; nothing to process this cycle.
                self._commit_event_unit()
                return
            try:
                self._store.conn.execute("SAVEPOINT edli_reactor_event")
                pre_disposition, should_submit = self._process_one_pre_submit(
                    event, decision_time=decision_time, result=result
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
                self._dead_letter_unknown(event, exc, decision_time=decision_time, result=result)
                return
        finally:
            mutex.release()

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
                    event, post_disposition, decision_time=decision_time, result=result
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
                    # If the lock failure happened before the savepoint opened
                    # (for example BEGIN IMMEDIATE in Window B), we cannot safely
                    # write requeue/dead-letter surfaces because the same writer
                    # lock is unavailable. Leave the event in processing; the
                    # store's stale-lease fetch path will retry it next cycle.
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
          (b) MARKET_VENUE_CLOSED — the venue market has entered POST_TRADING
              (RESOLVED). For a Polymarket weather family this is the F1 12:00-UTC
              close of target_date (authority: src/strategy/market_phase). Once the
              venue market is closed the family can produce no fresh executable book
              (capture freezes at the last pre-close snapshot) and no receipt, so a
              transient EXECUTABLE_SNAPSHOT_STALE block on it can NEVER clear — it
              must terminalize at the venue close, not requeue.
          (a) TIMELINESS_FLOOR_PAST — the event is no longer timely. Delegates to
              the SINGLE existing timeliness authority (EventStore._is_timely):
              a forecast-decision event whose target LOCAL day is strictly past
              has crossed its market horizon. This is the SAME predicate
              fetch_pending applies on its read floor — no second clock.

        WHY (b) EXISTS — the local-day floor (a) is NOT the market-closed signal.
        The prior design assumed "(a) subsumes market-closed (b): the
        settlement-day-end floor IS the market-closed authority." That assumption
        was FALSE: the venue closes at 12:00 UTC of target_date (POST_TRADING),
        which is EARLIER than the target-LOCAL-day end for every city whose local
        day extends past 12:00 UTC (UTC+, and UTC- before noon-local). In the window
        [venue_close, local_day_end) the book is gone but (a) still reports the
        event timely → an EXECUTABLE_SNAPSHOT_STALE block requeued FOREVER (measured
        live 2026-06-13 15:48Z: 679 events / 51 families pinned at processed=0;
        docs/evidence/no_order_root_2026-06-13/diagnosis.md). (b) closes that gap by
        asking the venue-close authority directly. It invents NO new clock and runs
        NO venue probe — it reuses the SAME market_phase POST_TRADING anchor the
        reactor's EVENT_BOUND_MARKET_PHASE_CLOSED gate uses, applied at the horizon
        locus. It is a SEMANTIC horizon (venue close), never an attempt cap.

        Non-family-keyed events (no city+target_date) have no timeliness floor of
        their own — for them only the operator disarm horizon applies; absent that
        they requeue until consumed by another terminal path. They cannot leak the
        queue: the cross-city round-robin in fetch_pending interleaves fresh events
        fairly (see _note_transient_requeue docstring).

        FAMILY-KEYED COVERAGE (2026-06-14, #92): the venue-close horizon (b) now
        covers DAY0_EXTREME_UPDATED in addition to the forecast-decision lane (see
        _VENUE_CLOSE_HORIZON_EVENT_TYPES). The timeliness floor (a) still applies only
        to the forecast-decision lane (EventStore._is_timely L803), so for a past-close
        DAY0 family the venue-close horizon (b) — which fires EARLIER and is geometric
        — is the terminal that sweeps it out of the working set.
        """
        # (c) Operator disarm — highest precedence kill-switch.
        if _operator_disarm_active():
            return ("OPERATOR_DISARM", f"{_TRANSIENT_DISARM_ENV} set")

        # (b) Venue-close floor — the market has entered POST_TRADING/RESOLVED. A
        # closed market yields no fresh book and no receipt, so a transient block on
        # it cannot clear; terminalize at the venue close (which precedes the
        # local-day floor (a) for most cities). Fail-soft: an unresolvable
        # tz/date returns None (NOT closed) so the event keeps requeueing — never
        # burned on a missing predicate.
        venue_closed = self._venue_market_closed_horizon(event, decision_time=decision_time)
        if venue_closed is not None:
            return venue_closed

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
        """Horizon (b): the venue market is in POST_TRADING/RESOLVED at decision_time.

        For a family-keyed event (city + target_date), consult the canonical
        market_phase authority with the F1 12:00-UTC fallback close anchor — the
        SAME authority the reactor's EVENT_BOUND_MARKET_PHASE_CLOSED gate uses, so
        the two sites cannot disagree on the venue-close instant. No venue probe, no
        snapshot read: the phase is derived purely from city timezone + target_date
        + decision_time + the F1 anchor.

        SCOPE (freshness-throughput starvation fix 2026-06-14, #92): applies to every
        ``_VENUE_CLOSE_HORIZON_EVENT_TYPES`` member — the forecast-decision lane AND
        ``DAY0_EXTREME_UPDATED`` (also family-keyed). A past-close DAY0 event has a
        real venue close (its market settled at the F1 12:00-UTC anchor) and must
        terminalize at that horizon; before this fix it was scoped out and requeued
        forever, clogging the working set (see ``_VENUE_CLOSE_HORIZON_EVENT_TYPES``).

        Returns ``("MARKET_VENUE_CLOSED", detail)`` iff the phase is POST_TRADING or
        RESOLVED; otherwise None (the family is still live, or the inputs are
        unresolvable → fail-soft requeue, never a premature terminal).
        """
        if event.event_type not in _VENUE_CLOSE_HORIZON_EVENT_TYPES:
            return None
        payload = _payload_dict(event)
        city = str(payload.get("city") or "").strip()
        target_date = str(payload.get("target_date") or "").strip()
        if not city or not target_date:
            return None
        try:
            from datetime import date as _date_cls

            from src.config import runtime_cities_by_name
            from src.strategy.market_phase import (
                MarketPhase,
                _f1_fallback_end_utc,
                market_phase_for_decision,
            )

            city_config = runtime_cities_by_name().get(city)
            tz = getattr(city_config, "timezone", None) if city_config is not None else None
            if not tz:
                return None
            target_local_date = _date_cls.fromisoformat(target_date)
            phase = market_phase_for_decision(
                target_local_date=target_local_date,
                city_timezone=tz,
                decision_time_utc=decision_time.astimezone(UTC),
                polymarket_start_utc=None,
                polymarket_end_utc=_f1_fallback_end_utc(target_local_date),
            )
        except Exception:
            # Fail-soft: an unresolvable city/tz/date must NOT terminalize a family
            # that might still be live. Requeue (None); the local-day floor (a) is
            # the backstop terminal once the whole local day ends.
            return None
        if phase in (MarketPhase.POST_TRADING, MarketPhase.RESOLVED):
            return ("MARKET_VENUE_CLOSED", f"venue market phase {phase.value} (F1 12:00-UTC close)")
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

        ``kind`` is ``"snapshot"`` (executable-snapshot block -> family_snapshot_refresher) or
        ``"posterior"`` (stale/absent replacement posterior -> single-family cycle-advance reseed).
        De-duplicated per family per cycle (a family blocked by many bins refreshes ONCE). The
        intents are drained AFTER the event's unit-of-work closes (no network in any open txn).
        """
        family = self._family_identity(event)
        if family is None:
            return
        bucket = self._pending_snapshot_refreshes if kind == "snapshot" else self._pending_cycle_advances
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
        # HELD-POSITION set, computed ONCE per cycle (fail-soft): families with money at risk now.
        held = self._held_families_failsoft()
        if held:
            import logging as _logging

            _logging.getLogger("zeus.events.reactor").debug(
                "always-decidable drain ordering: held-position-first (%d held), then fair "
                "rotation; basis=position_current", len(held),
            )
        # SHARED per-cycle drain deadline (monotonic). None => budget disabled (legacy unbounded
        # drain). The snapshot bucket (held-first within it) drains BEFORE the cycle-advance bucket,
        # both against the SAME deadline, so the budget never starves a held family's snapshot.
        drain_budget = _drain_budget_seconds()
        drain_deadline = (time.monotonic() + drain_budget) if drain_budget is not None else None
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
                self._note_transient_requeue(event)
                self._store.requeue_pending(event.event_id)
                result.retried += 1
            return
        # disposition is None: a pre-submit gate rejected the event (its reject
        # ledgers were written in _process_one_pre_submit). The legacy single-pass
        # flow marked such drained-rejection events processed and counted them as
        # ``processed`` (the event is consumed, not retried). Preserve that exactly.
        self._transient_requeue_reasons.pop(event.event_id, None)
        self._transient_requeue_counts.pop(event.event_id, None)
        self._store.mark_processed(event.event_id, processed_at=decision_time.astimezone(UTC).isoformat())
        result.processed += 1

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
        self, event: OpportunityEvent, *, decision_time: datetime, result: ReactorResult
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
        if not self._executable_snapshot_gate(event, decision_time.astimezone(UTC)):
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
            # with the shared bounded disposition instead: NOTHING submits
            # while blocked (the gate is not weakened), and exhaustion after
            # MAX retries terminates with the honest RISK_GUARD_BLOCKED cause —
            # a sustained genuine halt still ends in a terminal label.
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
            # live lane never produces a full-pass NO_SUBMIT with proof_accepted — it
            # SUBMITs, returns a SUBMIT_DISABLED build, or carries a typed abort reason.
            # submit_lane="LIVE" here means the live lane silently ate a tradeable entry
            # (the 11:51-12:12Z incident). Raise rather than persist the kill. Receipts
            # from the honest degrade lane (submit_lane="NO_SUBMIT_ADAPTER" + named
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
                self._reject_event(
                    event,
                    "EXECUTION_RECEIPT",
                    receipt.reason or receipt.side_effect_status,
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
            # Transient: the forecast source was re-ingested after this cycle's
            # decision moment, or the selected executable price expired between
            # the pre-submit family identity gate and the adapter's JIT scoring.
            # Requeue for the next cycle instead of terminally consuming the
            # opportunity (horizon-bounded — no attempt cap; see
            # _transient_horizon_terminal).
            self._transient_requeue_reasons[event.event_id] = str(reason)
            return _EXECUTABLE_SNAPSHOT_RETRY
        self._reject_event(event, stage, reason, result, receipt=receipt, decision_time=decision_time)
        return None

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
        family_level_all_rejected = str(reason or "").startswith(
            "EVENT_BOUND_ALL_CANDIDATES_REJECTED:"
        )
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
        if family_level_all_rejected:
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
    for field in ("city", "target_date", "metric", "condition_id", "token_id"):
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
        )
        if buy_no_conservative_reason is not None:
            return "TRADE_SCORE", buy_no_conservative_reason
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
    # Executable family snapshot not captured yet / went stale this cycle.
    "EXECUTABLE_SNAPSHOT_STALE",
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
    # QKERNEL_DAY0_NOT_WIRED, SPINE_WIRING_FAULT). EVERY such no-trade is TERMINAL
    # for THIS event, exactly like the legacy honest-no-edge declines (FDR_REJECTED,
    # TRADE_SCORE_NON_POSITIVE): the spine re-prices the whole family from a FRESH
    # book on the NEXT forecast snapshot, which arrives as a NEW event — so the
    # recovery path is a fresh event, NOT a requeue of this one. Requeueing instead
    # would double-churn the same event every cycle against an unchanged decision
    # substrate (the live QKERNEL_DAY0_NOT_WIRED requeue storm, monitor b9w56vec6).
    # Genuine intra-cycle execution races (PRICE_MOVED / MODE_FLIPPED) are classified
    # later at the SUBMIT stage under their own transient bases and are unaffected.
    "QKERNEL_SPINE_NO_TRADE",
    # Structural event-type contract violations are terminal for the event. They
    # cannot become valuable by re-running the same payload and must not clog
    # continuous redecision.
    "unsupported live candidate event type",
    "unsupported EDLI event type for inference",
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
        or "database is locked" in suffix_lower
        or "database table is locked" in suffix_lower
        or "database is busy" in suffix_lower
    )


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
# (event_reactor_adapter.py ~9609-9622). A reason CHAIN that nests one of these ANYWHERE
# (e.g. wrapped in a stage prefix) still counts — the belief substrate is the root cause.
_POSTERIOR_STALENESS_REASON_BASES = frozenset(
    {
        "REPLACEMENT_0_1_LIVE_READINESS_MISSING",
        "REPLACEMENT_0_1_LIVE_BUNDLE_BLOCKED",
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
        and payload.get("live_authority_status") == "live"
    )


def _regret_bucket_for(reason: str) -> str:
    if reason in {"FDR_REJECTED"}:
        return "FDR_REJECTED"
    if reason in {"KELLY_TOO_SMALL"}:
        return "KELLY_TOO_SMALL"
    if "RISK" in reason:
        return "RISK_CAP"
    if "QUOTE" in reason or "SNAPSHOT" in reason:
        return "QUOTE_UNAVAILABLE"
    if "SOURCE" in reason or "DAY0_HARD_FACT" in reason:
        return "SOURCE_WRONG"
    if "FAMILY" in reason:
        return "FAMILY_INCOMPLETE"
    if "LEAK" in reason or "AVAILABLE_AT" in reason:
        return "LEAKAGE_BLOCKED"
    return "UNKNOWN_REVIEW_REQUIRED"
