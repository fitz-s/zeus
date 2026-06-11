"""EDLI opportunity event reactor.

This module intentionally has no venue-adapter import. Execution side effects
must flow through injected final-intent/executor seams owned by `src.engine` and
`src.execution`.
"""

# Last reused/audited: 2026-06-11
# Authority basis (2026-06-11 operator follow-up): (a) SOURCE_TRUTH intake gate
#   defers to the serving authority — coverage PARTIAL/BLOCKED passes through,
#   dead-letter only for junk run identity (twin-authority #8, 16:33:51Z six-city
#   incident); (b) price-race aborts (SUBMIT_ABORTED_PRICE_MOVED, would_cross_book
#   certificate failure) classify TRANSIENT → bounded requeue (Miami/NYC 16:22Z);
#   (c) pre-event cycle-budget check caps overrun to one in-flight event.
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
    live_capital_efficiency_rejection_reason,
    live_lcb_consistency_rejection_reason,
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


DRY_EXECUTION_RECEIPT_TERMINAL_STATUSES = frozenset({"SUBMIT_DISABLED", "NOT_SUBMITTED_DRY_RUN"})
LIVE_EXECUTION_RECEIPT_TERMINAL_STATUSES = frozenset({
    "SUBMITTED",
    "REJECTED",
    "TIMEOUT_UNKNOWN",
    "PRE_SUBMIT_ERROR",
    "POST_SUBMIT_UNKNOWN",
})
EXECUTION_RECEIPT_TERMINAL_STATUSES = DRY_EXECUTION_RECEIPT_TERMINAL_STATUSES | LIVE_EXECUTION_RECEIPT_TERMINAL_STATUSES
EDLI_PROCESSING_REACTOR_MODES = frozenset({"live", "live_no_submit", "submit_disabled_live_bridge"})

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

    def __post_init__(self) -> None:
        if self.proof_accepted is None:
            object.__setattr__(self, "proof_accepted", bool(self.submitted))


Submit = Callable[[OpportunityEvent, datetime], bool | None | EventSubmissionReceipt]


@dataclass
class ReactorConfig:
    reactor_mode: str = "live_no_submit"
    real_order_submit_enabled: bool = False
    taker_fok_fak_live_enabled: bool = False
    # Task #102 (BEST-ORDER SELECTION): book-wide edge-zone admission gate, the
    # LAST step in the money-path. DEFAULT FALSE => byte-identical to today (the
    # gate is computed only when this flag is True). When True, a candidate is
    # admitted ONLY if its honest (q_lcb-based) after-cost EV-per-dollar clears
    # ``edge_zone_min_ev_per_dollar`` -- a TIGHTENING that demotes the confident
    # tails (price>0.8 / price<0.5) where after-cost edge is absent and keeps the
    # market-uncertain mid-range where settlement-grounded edge is real. Pure +
    # order-independent: it can never admit a negative-EV order ahead of a
    # positive one. See src/contracts/edge_zone_admission.py.
    edge_zone_admission_enabled: bool = False
    edge_zone_min_ev_per_dollar: float = 0.0
    # Scope-aware claim tier (2026-06-11 anti-starvation). True (default) =
    # historical behaviour: DAY0_EXTREME_UPDATED ranks at the top claim tier
    # (realized obs = freshest tradeable alpha). False = day0 is shadow-only
    # (edli_live_scope='day0_shadow'); the reactor demotes DAY0_EXTREME_UPDATED
    # below tradeable FORECAST_SNAPSHOT_READY so the shadow flood cannot starve
    # tradeable forecast families out of the per-cycle proof budget. Derived from
    # the scope via src.events.event_priority.day0_is_tradeable_for_scope.
    day0_is_tradeable: bool = True


# An executable market snapshot for the family may simply not be captured yet on the cycle
# the reactor reaches the event (the targeted refresh and the reactor share a cycle). That is
# a TRANSIENT condition, not a terminal rejection: the event is requeued and retried on a later
# cycle (after capture) rather than being consumed. After this many attempts without a snapshot
# the event is dead-lettered as genuinely uncapturable.
_EXECUTABLE_SNAPSHOT_RETRY = "RETRY_EXECUTABLE_SNAPSHOT_PENDING"

# K2.1: once-per-process-per-base warning dedup for unregistered rejection-reason
# bases (see _write_regret). Module-level so every reactor instance shares it.
_UNREGISTERED_REJECTION_BASES_WARNED: set[str] = set()
MAX_EXECUTABLE_SNAPSHOT_RETRIES = 8
# Sentinel returned by _process_one when a FORECAST_SNAPSHOT_READY event has been dead-lettered
# due to non-live-eligible window authority. The dead-letter + reject writes are done inside
# _process_one; process_pending must NOT double-count or attempt mark_processed on this path.
_FSR_PARTIAL_DEAD_LETTER = "FSR_PARTIAL_DEAD_LETTER"


@dataclass
class ReactorResult:
    processed: int = 0
    rejected: int = 0
    proof_accepted: int = 0
    dead_lettered: int = 0
    retried: int = 0
    rejection_reasons: list[str] = field(default_factory=list)

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
    ) -> None:
        self._store = store
        self._source_truth_gate = source_truth_gate
        self._executable_snapshot_gate = executable_snapshot_gate
        self._riskguard_gate = riskguard_gate
        self._submit = final_intent_submit
        self._reject = reject
        self._config = config or ReactorConfig()
        self._regret_ledger = regret_ledger
        # DecisionProvenanceEnvelope (operator law 2026-06-11): an OPTIONAL fail-soft accessor
        # returning (bundle, forecast_conn) for the event being rejected, so the regret envelope
        # can carry the full forecast data-combination + per-input ages. None => the envelope is
        # still built from the world-DB snapshot + receipt economics + the FULL rejection reason
        # (never less than that). Observability only; never gates. See
        # docs/evidence/settlement_guard/2026-06-11_decision_provenance_plan.md.
        self._decision_provenance_hook = decision_provenance_hook
        self._family_logged: set[str] = set()
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
        while remaining is None or remaining > 0:
            # fetch_pending is a READ — WAL permits concurrent readers, so it is NOT
            # taken under the world-DB write mutex.  limit=None means drain the current
            # admissible queue in batches; the batch size is pagination, not a total cap.
            request_limit = batch_limit if remaining is None else min(batch_limit, remaining)
            events = self._store.fetch_pending(
                decision_time=decision_time.astimezone(UTC).isoformat(),
                limit=request_limit,
                day0_is_tradeable=self._config.day0_is_tradeable,
            )
            if not events:
                break
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
                claimed = self._store.claim(event.event_id, claimed_at=decision_time.astimezone(UTC).isoformat())
            except Exception as exc:
                if _is_sqlite_lock_error(exc):
                    result.retried += 1
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
            attempts = self._store.attempt_count(event.event_id)
            if attempts >= MAX_EXECUTABLE_SNAPSHOT_RETRIES:
                # Genuinely uncapturable after repeated cycles → terminal.
                self._reject_event(event, "EXECUTABLE_QUOTE", "EXECUTABLE_SNAPSHOT_BLOCKED", result, decision_time=decision_time)
                self._store.mark_dead_letter(
                    event,
                    failure_stage="EXECUTABLE_SNAPSHOT_BLOCKED",
                    error_message=f"executable snapshot not captured after {attempts} attempts",
                    created_at=decision_time.astimezone(UTC).isoformat(),
                )
                result.dead_lettered += 1
            else:
                # Transient block: requeue for retry next cycle (after capture completes).
                # Do NOT consume the event the way mark_processed would.
                self._store.requeue_pending(event.event_id)
                result.retried += 1
            return
        # disposition is None: a pre-submit gate rejected the event (its reject
        # ledgers were written in _process_one_pre_submit). The legacy single-pass
        # flow marked such drained-rejection events processed and counted them as
        # ``processed`` (the event is consumed, not retried). Preserve that exactly.
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
        if event.event_type in {"BOOK_SNAPSHOT", "BEST_BID_ASK_CHANGED", "NEW_MARKET_DISCOVERED"}:
            self._reject_event(event, "EXECUTABLE_QUOTE", "MARKET_CHANNEL_EVENT_NO_DIRECT_STALE_TRADE", result, decision_time=decision_time)
            return None, False
        if event.event_type == "FORECAST_SNAPSHOT_READY":
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
            # with the full reason chain (REPLACEMENT_0_1_LIVE_AUTHORITY_BUNDLE_
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
            return _EXECUTABLE_SNAPSHOT_RETRY, False
        self._log_family_once(event)
        if not self._riskguard_gate(event):
            self._reject_event(event, "RISK_GUARD", "RISK_GUARD_BLOCKED", result, decision_time=decision_time)
            return None, False
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
        result.proof_accepted += 1

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
        if _is_transient_money_path_reason(reason):
            # Transient: the forecast source was re-ingested after this cycle's
            # decision moment, or the selected executable price expired between
            # the pre-submit family identity gate and the adapter's JIT scoring.
            # Requeue for the next cycle instead of terminally consuming the
            # opportunity (bounded by MAX_EXECUTABLE_SNAPSHOT_RETRIES).
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
        self._regret_ledger.insert_idempotent(
            NoTradeRegretEvent(
                event_id=event.event_id,
                rejection_stage=stage,  # type: ignore[arg-type]
                rejection_reason=reason,
                regret_bucket=_regret_bucket_for(reason),  # type: ignore[arg-type]
                envelope_json=envelope_json,
                market_slug=payload.get("market_slug"),
                condition_id=_receipt_or_payload(receipt, payload, "condition_id"),
                token_id=_receipt_or_payload(receipt, payload, "token_id"),
                outcome_label=_receipt_or_payload(receipt, payload, "outcome_label"),
                decision_time=decision_time.astimezone(UTC).isoformat() if decision_time is not None else None,
                city=_receipt_or_payload(receipt, payload, "city"),
                target_date=_receipt_or_payload(receipt, payload, "target_date"),
                metric=_receipt_or_payload(receipt, payload, "metric"),
                observation_time=payload.get("observation_time"),
                decision_seq=_optional_int(payload.get("decision_seq")),
                family_id=_receipt_or_payload(receipt, payload, "family_id"),
                bin_label=_receipt_or_payload(receipt, payload, "bin_label"),
                direction=_receipt_or_payload(receipt, payload, "direction"),
                q_live=_optional_float(_receipt_or_payload(receipt, payload, "q_live")),
                q_lcb_5pct=_optional_float(_receipt_or_payload(receipt, payload, "q_lcb_5pct")),
                c_fee_adjusted=_optional_float(_receipt_or_payload(receipt, payload, "c_fee_adjusted")),
                c_cost_95pct=_optional_float(_receipt_or_payload(receipt, payload, "c_cost_95pct")),
                p_fill_lcb=_optional_float(_receipt_or_payload(receipt, payload, "p_fill_lcb")),
                trade_score=_optional_float(_receipt_or_payload(receipt, payload, "trade_score")),
                native_quote_available=_optional_bool(_receipt_or_payload(receipt, payload, "native_quote_available")),
                source_status=_receipt_or_payload(receipt, payload, "source_status"),
                family_complete=_optional_bool(_receipt_or_payload(receipt, payload, "family_complete")),
                hypothetical_order_type=payload.get("hypothetical_order_type"),
                hypothetical_fill_status=payload.get("hypothetical_fill_status"),
                hypothetical_fill_price=_optional_float(payload.get("hypothetical_fill_price")),
                causal_snapshot_id=event.causal_snapshot_id,
                executable_snapshot_id=_receipt_or_payload(receipt, payload, "executable_snapshot_id"),
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
                    saved = self._store.conn.row_factory
                    self._store.conn.row_factory = sqlite3.Row
                    try:
                        snapshot_row = self._store.conn.execute(
                            "SELECT snapshot_id, captured_at, orderbook_top_bid, orderbook_top_ask, "
                            "market_end_at, condition_id FROM executable_market_snapshots "
                            "WHERE snapshot_id = ?",
                            (str(snapshot_id),),
                        ).fetchone()
                    finally:
                        self._store.conn.row_factory = saved
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
        lcb_consistency_reason = live_lcb_consistency_rejection_reason(
            q_direction=receipt.q_live,
            q_lcb=receipt.q_lcb_5pct,
        )
        if lcb_consistency_reason is not None:
            return "TRADE_SCORE", lcb_consistency_reason
        capital_efficiency_reason = live_capital_efficiency_rejection_reason(
            q_lcb=receipt.q_lcb_5pct,
            execution_price=receipt.c_fee_adjusted,
            trade_score=receipt.trade_score,
        )
        if capital_efficiency_reason is not None:
            return "TRADE_SCORE", capital_efficiency_reason
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
    # admission checks above own capital efficiency and conservative Buy-NO
    # evidence. This flag remains a separate extra tightening over q_lcb-vs-cost.
    if config is not None and getattr(config, "edge_zone_admission_enabled", False):
        from src.contracts.edge_zone_admission import edge_zone_admits

        verdict = edge_zone_admits(
            q_lcb=receipt.q_lcb_5pct,
            cost=receipt.c_fee_adjusted,
            min_ev_per_dollar=float(getattr(config, "edge_zone_min_ev_per_dollar", 0.0)),
        )
        if not verdict.admits:
            return "TRADE_SCORE", verdict.reason or "EDGE_ZONE_BLOCKED"
    return None, ""


def _is_transient_money_path_reason(reason: str | None) -> bool:
    """Stale-decision-vs-fresh-book races and DB-lock blips are TRANSIENT: requeue
    (bounded by MAX_EXECUTABLE_SNAPSHOT_RETRIES → dead-letter) so the next cycle
    RE-DECIDES with a fresh book. The retry re-runs the full gate chain and
    re-prices from scratch — it NEVER resubmits the same envelope, so the
    no-verbatim-retry venue rule is untouched (and in every reason below the
    venue order was never placed: PRICE_MOVED aborts pre-POST, would_cross_book
    fails the pre-submit revalidation certificate).

    TRANSIENT (2026-06-11 live additions):
      * SUBMIT_ABORTED_PRICE_MOVED — taker flavor: JIT recapture found the all-in
        cost above max_acceptable_price + bounded tolerance. Live: Miami 16:22:35Z
        cleared EVERY gate, aborted on a moved book, and was terminally consumed
        even though EV at the NEW price was still strongly positive (q_lcb 0.6776
        vs cost 0.5136). Same shape as EXECUTABLE_SNAPSHOT_STALE, which already
        requeues.
      * EDLI_LIVE_CERTIFICATE_BUILD_FAILED + would_cross_book — maker flavor of
        the SAME race: a post-only limit crossed because the book moved between
        decision and submit (NYC 16:22:33Z). Narrowed to the would_cross
        sub-reason ONLY; every other certificate build failure stays terminal
        (except the pre-existing db-lock transients).
      * SUBMIT_ABORTED_MODE_FLIPPED — third flavor of the SAME race: the proof
        priced a MAKER rest into an empty own-ask, and by final submit the book
        had a live ask (fresh_mode=TAKER), so the P0-1 mode-intent authority
        correctly refused the stale-mode plan. Live: 17:23:33Z four cities
        (Chongqing/Wellington/Shanghai/Milan 06-13 buy_no, Kelly $9-35) were
        terminally consumed while the FRESH ask carried +6..+19% conservative
        EV. The requeue re-decides on the fresh book and prices TAKER from the
        start — the abort itself proves the venue order was never placed.
    """
    if not reason:
        return False
    reason_lower = reason.lower()
    return (
        "SOURCE_CAPTURED_AFTER_DECISION_TIME" in reason
        or "EXECUTABLE_SNAPSHOT_STALE" in reason
        or "SUBMIT_ABORTED_PRICE_MOVED" in reason
        or "SUBMIT_ABORTED_MODE_FLIPPED" in reason
        or (
            "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:" in reason
            and (
                "database is locked" in reason_lower
                or "database table is locked" in reason_lower
                or "database is busy" in reason_lower
                or "would_cross_book" in reason_lower
            )
        )
    )


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
        and payload.get("live_authority_status") == "LIVE_AUTHORITY"
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
