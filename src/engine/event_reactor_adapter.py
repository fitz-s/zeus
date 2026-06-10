# Last reused or audited: 2026-06-10 (S6 PRICE_MOVED maker/taker fix: the submit-
#   recapture gate now threads order_rests_at_admitted_price into RecaptureInputs.
#   A resting MAKER (governor GTC/GTD) order pays its admitted limit and never chases
#   the recaptured ask, so it skips the PRICE_MOVED ceiling; TAKER (FOK/FAK) keeps a
#   BOUNDED slippage ceiling (one tick / 5% / 1¢ cap). _order_will_rest_at_admitted_price
#   mirrors _governor_mode_for_snapshot's fail-direction; the taker no-chase bound stays
#   at intent build (TOUCH_EXCEEDS_RESERVATION). Tolerated/rested moves are recorded on
#   the receipt (price_moved_within_tolerance + admitted/recaptured/tolerance). Fixes the
#   live sub-3¢ false-abort churn 2026-06-10.
# Last reused or audited: 2026-06-08 (S7: deleted the last opportunity-book
#   selector on/off gate artifacts — the dead `selector_enabled`/`selector_shadow`
#   cache keys, the `_env_flag_enabled` helper + its `import os`, and every literal
#   toggle-name string. The marginal-utility ranker is the unconditional single
#   selection path — "bin selection.md" §14 item 8 + operator directive 2026-06-08.
#   S4: marginal-utility ranker is the SOLE size authority via
#   RobustCandidateScore.optimal_stake_usd on robust q_lcb + exposure; removed
#   scalar market-disagreement / pre-selection scalar-Kelly gates —
#   "bin selection.md" §3/§5.2/§5.3/§6/§14.7 + operator directive 2026-06-08)
# Authority basis: Operator GOAL 2026-06-04 — full-family q/FDR + executable-mask for illiquid bins; never trade an assumed/renormalized subset;
#   P1 ZERO-SUBMIT FIX B (2026-06-05, iron-rule-1, co-cause) — per-cycle in-flight reservation
#   made rollback-aware (PortfolioReservationLedger): provisional reserve on Kelly+RiskGuard pass,
#   reactor commits on emit / rolls back on downstream reject. Exposed via _submit.reservation_ledger.
#   S1 (2026-06-08, "bin selection.md" §5.3/§5.4/§4/§9 Hidden #6/#16/§13/§14.3 +
#   operator directive 2026-06-08): _execution_price_from_snapshot now prices each
#   native side with its OWN side-tagged ExecutableCostCurve (depth-walked convex
#   curve) built from the same snapshot row's native ask ladder — replacing the
#   scalar VWMP cost-kernel pricing. One pricing object, no flag, no shadow branch.
#   S1-fix (2026-06-08, "bin selection.md" §5.3/§5.4/§13 + verifier REJECT):
#   the proof path now sizes the candidate by exact SHARE count via
#   ExecutableCostCurve.avg_cost_for_shares(min_order_size), NOT by converting
#   min_order_size shares to a USD stake at the top price (which UNDERFILLED a
#   thin top level and FALSE-no-traded buy_yes/buy_no the legacy kernel priced).
#   Dead helper _min_order_notional_usd removed. Byte-identical to the legacy
#   share-parameterized VWMP all-in for all books (zero-fee/single-level); the
#   per-level fee on multi-level walks is the curve being more correct.
#   S2 (2026-06-08, "bin selection.md" §4/§5.6/§9 Hidden #2/#3/#4/§12.B/§14.4 +
#   operator directive 2026-06-08): the q-construction builders now derive q_lcb
#   from the per-bin YES *probability* samples (market_analysis.bin_yes_probability_
#   samples) via ProbabilityUncertainty — REPLACING the "q_lcb = edge_ci_lower + cost"
#   restore (Hidden #2) in _canonical_probability_and_fdr_proof. The native-NO leg
#   carries its OWN robust q_lcb_no = 1 - q_ucb_yes (canonical via the per-sample
#   complement no_side_samples; replacement_0_1 via the bundle q_ucb map in
#   _replacement_no_lcb_for_bin), NOT 1 - q_lcb_yes (Hidden #3). _generate_candidate_
#   proofs now gives buy_no a real q_no = 1 - q_yes point + native q_lcb_no, retiring
#   the ADMISSION_BUY_NO_INDEPENDENT_NO_POSTERIOR_MISSING hardcode (Hidden #4) and the
#   dead "q_lcb > q_value" boundary clamp (the invariant is now structural in the seam).
#   One seam helper (_side_q_lcb_from_yes_samples), no flag, no shadow branch.
#   S3 (2026-06-08, "bin selection.md" §14.2/§6 pseudocode/§4/§9 Hidden #1+#4/
#   §11 Phase 1/§12.A native-side economics + operator directive 2026-06-08): each
#   priced _CandidateProof now MATERIALIZES as the unified bin-selection
#   NativeSideCandidate (YES/NO unified candidate shape) on the live selection
#   path. _candidate_evaluation_from_proof builds the canonical NativeSideCandidate
#   FIRST (_native_side_candidate_from_proof: direction->side DIRECTION-LAW map,
#   q_point=q_posterior, q_lcb=q_lcb_5pct robust lower bound, ProbabilityUncertainty
#   from the proof's S2 authority, ExecutableCostCurve rebuilt from the proof's
#   snapshot row via the S1 builder), then DERIVES the legacy CandidateEvaluation
#   receipt FROM that candidate — one materialization path, one candidate object.
#   A missing native token/quote downgrades to a NATIVE_TOKEN_MISSING /
#   NATIVE_QUOTE_MISSING no-trade candidate (no complement pricing). No flag, no
#   shadow branch. (The CandidateEvaluation scalar-Kelly ranker / opportunity_book
#   selector remains the ranking surface until S4 replaces it with the
#   marginal-utility ranker.)
#   S3-fix (2026-06-08, "bin selection.md" §6/§7/§13/§14.7/§14.8 + verifier REJECT
#   for single_path + operator directive 2026-06-08): the materialized
#   NativeSideCandidate is no longer DISCARDED. _selected_candidate_proof now makes
#   the SINGLE live decision via _select_proof_by_robust_marginal_utility, which
#   ranks the materialized candidates by robust marginal expected LOG utility (ΔU)
#   using the §7 utility_ranker (FamilyPayoffMatrix over bins+OUTSIDE Hidden #5,
#   robust_probabilities from per-bin YES q_lcb, rank_candidates) and applies the
#   §13 "robust marginal expected log utility <= 0" no-trade gate ON THE LIVE PATH.
#   The legacy scalar-Kelly surfaces (build_family_opportunity_book ->
#   select_best_family_candidate, and the max(executable, key=(trade_score,q_lcb))
#   fallback) are RETIRED as the decision; build_family_opportunity_book now RECORDS
#   the ΔU decision (decided_candidate_id) and uses select_best_family_candidate only
#   for display ranks/loser reasons. The off-able family-selector on/off env+settings
#   toggle is REMOVED (the ranker is unconditional). q_lcb > q_point is a
#   §13/Hidden #2 Q_LCB_INVALID no-trade. One ranking surface, one decision, one
#   truth. No flag, no shadow branch.
#   S5 (2026-06-08, "bin selection.md" §5.1-§5.3/§5.2/§14.7/§14.10/§9 Hidden #6+#15/
#   §12.C.2/.3/.4 + operator directive 2026-06-08): the live decision body now sizes
#   from RobustCandidateScore.optimal_stake_usd AND reprices the Kelly cost-of-entry
#   at the CHOSEN stake — execution_price = ExecutableCostCurve.avg_cost(optimal_stake)
#   on the selected leg's OWN native curve (typed, fee-deducted, probability_units;
#   passes assert_kelly_safe), REPLACING S1's cheap min-order/top-ask scalar as the
#   boundary the intent + receipt carry. Scalar Kelly on a single top-ask over-bets
#   into thin levels (Hidden #6); the cost-curve optimizer already maximized ΔU over
#   the feasible depth-bounded stake interval, so size and price now come from ONE
#   scored candidate + ONE curve and cannot drift. The fractional/CI/lead/portfolio-
#   heat haircut remains the multiplier bounding the ΔU stake (max_stake_usd), NOT a
#   second scalar Kelly. New seam: _robust_marginal_utility_stake_and_price (returns
#   stake + chosen-stake price) + _chosen_stake_execution_price; the float-only
#   _robust_marginal_utility_optimal_stake_usd wraps the kernel. No flag, no shadow.
#   S6 (2026-06-08, "bin selection.md" §5 submit pseudocode (recompute-not-validate)/
#   §7 re-decision+reversal state machine/§9 Hidden #7/#14/#17/§13/§14.9/§14.10/§12.E +
#   operator directive 2026-06-08): the live decision body now routes the submit
#   recapture boundary through the ONE fail-closed RedecisionEngine.evaluate_submit_
#   recapture state machine (src/strategy/redecision.py). _evaluate_submit_recapture_
#   for_selected RECOMPUTES (not validates) on the FRESH books: the selected leg's own
#   ExecutableCostCurve, the chosen fractional-Kelly stake+price (S5 kernel), the
#   robust q_lcb, and the family rank (_family_rank_reversed_at_recapture re-runs the
#   single ΔU selection on fresh curves), then routes them through ONE
#   evaluate_submit_recapture. The intent is built ONLY when may_submit is True; the
#   three abort branches are first-class lifecycle states mapped to no-submit receipt
#   reasons (SUBMIT_ABORTED_PRICE_MOVED / _EDGE_REVERSED / _FAMILY_REVERSED) — REPLACING
#   the former scattered inline `not kelly.passed` size re-gate (the implicit submit
#   eligibility check). Stale/failed recapture (no fresh curve) and a zero-ΔU stake
#   fail closed (price-moved / edge-reversed). A family-rank reversal aborts and defers
#   to a full re-rank (the engine never switches inline; a WATCH fallback cannot
#   auto-submit, Hidden #7). One state machine, one abort taxonomy. No flag, no shadow.
"""Engine adapter for EDLI opportunity reactor construction.

The adapter connects EDLI events to the event-bound no-submit proof kernel. It
does not call the broad cycle runner and it does not cross the executor or venue
side-effect boundary.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, replace as dataclass_replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from collections.abc import Mapping
from typing import Any, Callable

import numpy as np

from src.contracts.execution_intent import ExecutableCostBasis
from src.contracts.execution_price import ExecutionPrice, ExecutionPriceContractError
from src.contracts.executable_cost_curve import (
    BookLevel,
    ExecutableCostCurve,
    FeeModel,
)
from src.contracts.native_side_candidate import (
    CandidateNoTradeReason,
    NativeSideCandidate,
)
from src.decision_kernel import claims
from src.decision_kernel.canonicalization import stable_hash
from src.decision_kernel.certificate import DecisionCertificate, build_certificate
from src.decision_kernel.certificates.action import build_actionable_trade_certificate
from src.decision_kernel.certificates.execution import (
    build_execution_command_certificate_from_final_intent,
    build_execution_receipt_certificate,
    build_executor_expressibility_certificate,
    build_final_intent_certificate_from_actionable,
    build_live_cap_transition_certificate,
    build_pre_submit_revalidation_certificate,
)
from src.decision_kernel.compiler import (
    DecisionCompiler,
    FORECAST_LIVE_ELIGIBLE_STATUS,
    AuthorityEvidence,
    EvidenceClock,
    NoSubmitProofBundle,
    normalize_forecast_reader_status,
)
from src.engine.event_bound_final_intent import (
    EventBoundExecutorSubmitResult,
    EventBoundFinalIntent,
    build_event_bound_final_intent_receipt,
    serialize_event_bound_final_intent_receipt,
    validate_final_intent_cert_for_existing_executor,
)
from src.engine.replacement_forecast_reactor_hook import ReplacementForecastReactorHookResult
from src.data.replacement_forecast_refit_gate import ReplacementForecastRefitDecision
from src.data.replacement_forecast_runtime_policy import ReplacementForecastPromotionEvidence
from src.data.replacement_forecast_runtime_policy import ReplacementForecastCapitalObjectiveEvidence
from src.state.snapshot_repo import executable_snapshot_from_row, get_snapshot
from src.events.candidate_binding import MarketTopologyCandidate
from src.events.candidate_evaluation import CandidateEvaluation
from src.events.decision_engine import EventBoundDecisionEngine, EventBoundDecisionRequest
from src.events.event_store import EventStore
from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.events.money_path_adapters import evaluate_fdr_full_family, evaluate_kelly, evaluate_riskguard
from src.events.opportunity_book import OpportunityBook, build_family_opportunity_book
from src.events.opportunity_event import OpportunityEvent
from src.events.reactor import EventSubmissionReceipt, OpportunityEventReactor, ReactorConfig
from src.riskguard.risk_level import RiskLevel
from src.sizing.sizing_context import SizingContext
from src.sizing.portfolio_reservation import PortfolioReservationLedger
from src.signal.ensemble_signal import p_raw_vector_from_maxes
from src.config import runtime_cities_by_name, edge_n_bootstrap, settings
from src.contracts.settlement_semantics import SettlementSemantics
from src.strategy.market_fusion import MODEL_ONLY_POSTERIOR_MODE
from src.strategy.market_phase import (
    MarketPhase,
    FORECAST_ONLY_ADMIT_PHASES as _FORECAST_ONLY_ADMIT_PHASES,
    market_phase_admits,
)
from src.strategy.live_inference.live_admission import (
    live_buy_no_conservative_evidence_rejection_reason,
    live_capital_efficiency_rejection_reason,
)
from src.strategy import market_phase_evidence as _market_phase_evidence
# The §7 robust marginal-expected-log-utility ranker IS the single live decision
# surface (operator directive 2026-06-08; spec §6/§14.7/§14.8). _selected_candidate_proof
# ranks the materialized NativeSideCandidates by ΔU and applies the §13
# "robust marginal expected log utility <= 0" no-trade gate on the LIVE path.
from src.strategy import utility_ranker
from src.strategy.redecision import (
    CandidateLifecycleState,
    RecaptureInputs,
    RedecisionEngine,
    ReversalReason,
    SubmitRecaptureDecision,
    SUBMIT_ABORT_STATES,
)
from src.types.market import Bin
# QLCB_HONESTY.md FIX-C — the EXISTING settlement σ-floor (state/settlement_sigma_floor.json,
# 232 cells, median 3.18C realized residual) + its WMO-aware settlement-preimage bin
# integrator. Module-level so the live replacement q_lcb floor reuses the SAME antibody
# that already protects the canonical/EMOS path (one builder; no parallel sigma mechanism).
from src.calibration.emos import (
    settlement_sigma_floor,
    bin_probability_settlement as _bin_probability_settlement,
)


UTC = timezone.utc


@dataclass(frozen=True)
class _CandidateProof:
    candidate: MarketTopologyCandidate
    token_id: str
    direction: str
    row: dict[str, Any] | None
    executable_snapshot_id: str | None
    execution_price: ExecutionPrice | None
    q_posterior: float
    q_lcb_5pct: float
    c_cost_95pct: float | None
    p_fill_lcb: float
    trade_score: float
    p_value: float
    passed_prefilter: bool
    native_quote_available: bool
    p_cal_vector_hash: str
    p_live_vector_hash: str
    missing_reason: str | None = None
    # Mainstream-agreement gate verdict (Task #135). None = gate not evaluated
    # (flag OFF or evaluation failed). REFERENCE-ONLY (operator directive
    # 2026-06-03): recorded on the receipt to inform the ARM decision; it does
    # NOT gate production selection (see _selected_candidate_proof). `.passed` is
    # an arm-decision reference signal, never a selection filter.
    mainstream_agreement: dict | None = None
    # #120 (2026-06-04): the calibrator that produced q_posterior for this
    # candidate's family ("emos" | "bias_platt" | "platt"). Read from
    # payload["_edli_q_source"] (set by the ONE-CALIBRATOR SEAM, era.py:3735-3818)
    # at proof construction — instance-safe (same payload threaded by the #149
    # fix). Carried to the receipt so 06-05+ settlement can attribute EMOS-cells
    # vs maze-cells per city (the PROMOTE evidence).
    q_source: str | None = None
    q_lcb_calibration_source: str | None = None
    same_bin_yes_posterior: float | None = None
    # H2_E2E (REAUDIT_0_1.md §2/§4): carry the bundle posterior_id +
    # probability_authority from the evidence dict
    # (_replacement_authority_probability_and_fdr_proof :5752-5754) through to the
    # receipt so the fill->posterior link is reconstructable in SQL. None on the
    # canonical path. Observability only — never gates selection.
    posterior_id: int | None = None
    probability_authority: str | None = None


@dataclass(frozen=True)
class PreSubmitAuthorityWitness:
    quote_seen_at: str
    book_hash: str
    current_best_bid: float
    current_best_ask: float
    tick_size: float
    min_order_size: float
    neg_risk: bool
    heartbeat_status: str
    user_ws_status: str
    venue_connectivity_status: str
    balance_allowance_status: str
    book_authority_id: str
    book_captured_at: str
    heartbeat_authority_id: str
    heartbeat_checked_at: str
    user_ws_authority_id: str
    user_ws_checked_at: str
    venue_connectivity_authority_id: str
    venue_connectivity_checked_at: str
    balance_allowance_authority_id: str
    balance_allowance_checked_at: str
    checked_at: str | None = None
    max_quote_age_ms: int = 1000


class _LiveOpportunityAlreadyLocked(RuntimeError):
    """Raised when continuous redecision rediscovers an already-locked opportunity."""


_DURABLE_LIVE_CAP_UNKNOWN_CITY = "__unknown_live_cap_city__"


def _adapter_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


_DURABLE_LIVE_CAP_TERMINAL_COMMAND_STATES = frozenset(
    {"CANCELLED", "CANCELED", "EXPIRED", "FILLED", "REJECTED", "SUBMIT_REJECTED"}
)
_DURABLE_LIVE_CAP_MATERIALIZED_POSITION_PHASES = frozenset(
    {"active", "day0_window", "pending_exit"}
)


def _durable_live_cap_final_intent_token(final_intent_id: str) -> str:
    token = str(final_intent_id or "").rsplit(":", 1)[-1].strip()
    return token if token and token != str(final_intent_id or "") else ""


def _durable_live_cap_usage_is_represented_in_trade_truth(
    trade_conn: sqlite3.Connection | None,
    *,
    execution_command_id: str,
    final_intent_id: str,
) -> bool:
    if trade_conn is None:
        return False
    try:
        if execution_command_id and _adapter_table_exists(trade_conn, "venue_commands"):
            row = trade_conn.execute(
                """
                SELECT state
                  FROM venue_commands
                 WHERE decision_id = ?
                   AND intent_kind = 'ENTRY'
                 ORDER BY updated_at DESC, created_at DESC
                 LIMIT 1
                """,
                (execution_command_id,),
            ).fetchone()
            if row is not None:
                state = str(row[0] if not isinstance(row, sqlite3.Row) else row["state"]).strip().upper()
                if state in _DURABLE_LIVE_CAP_TERMINAL_COMMAND_STATES:
                    return True

        token = _durable_live_cap_final_intent_token(final_intent_id)
        if token and _adapter_table_exists(trade_conn, "position_current"):
            row = trade_conn.execute(
                """
                SELECT 1
                  FROM position_current
                 WHERE phase IN (?, ?, ?)
                   AND (
                        token_id = ?
                     OR no_token_id = ?
                   )
                   AND (
                        COALESCE(cost_basis_usd, 0) > 0
                     OR COALESCE(chain_cost_basis_usd, 0) > 0
                     OR COALESCE(shares, 0) > 0
                   )
                 LIMIT 1
                """,
                (*sorted(_DURABLE_LIVE_CAP_MATERIALIZED_POSITION_PHASES), token, token),
            ).fetchone()
            if row is not None:
                return True
    except Exception as exc:  # noqa: BLE001 - sizing must fail closed on exposure ambiguity.
        raise RuntimeError(
            f"DURABLE_LIVE_CAP_TRADE_TRUTH_UNAVAILABLE:{type(exc).__name__}:{exc}"
        ) from exc
    return False


def _durable_unmaterialized_live_cap_reservations(
    conn: sqlite3.Connection | None,
    *,
    trade_conn: sqlite3.Connection | None = None,
) -> tuple[tuple[str, str, float], ...]:
    """Return durable live-cap exposure not yet represented by position truth.

    ``position_current`` is the canonical open-position truth after a
    ``UserTradeObserved`` bridge. Between venue submit and user-channel/bridge
    materialization, however, the submitted notional is real in-flight capital.
    Seed that cross-cycle exposure into the same Kelly reservation ledger so a
    later reactor cycle cannot over-size while waiting for fills to arrive.
    """
    if conn is None:
        return ()
    try:
        if not (
            _adapter_table_exists(conn, "edli_live_cap_usage")
            and _adapter_table_exists(conn, "edli_live_order_events")
        ):
            return ()
        rows = conn.execute(
            """
            WITH live_cap AS (
                SELECT
                    usage_id,
                    event_id,
                    final_intent_id,
                    execution_command_id,
                    reserved_notional_usd,
                    event_id || ':' || COALESCE(final_intent_id, '') AS aggregate_id
                FROM edli_live_cap_usage
                WHERE reservation_status IN ('RESERVED', 'CONSUMED')
                  AND reserved_notional_usd > 0
            ),
            observed AS (
                SELECT DISTINCT aggregate_id
                FROM edli_live_order_events
                WHERE event_type = 'UserTradeObserved'
                  AND json_extract(payload_json, '$.fill_authority_state') = 'FILL_CONFIRMED'
            ),
            absence_reconciled AS (
                SELECT DISTINCT aggregate_id
                FROM edli_live_order_events
                WHERE event_type = 'Reconciled'
                  AND (
                    json_extract(payload_json, '$.cap_transition_recommendation') = 'RELEASED'
                    OR json_type(payload_json, '$.authenticated_absence_proof') IS NOT NULL
                  )
            ),
            pre_submit AS (
                SELECT aggregate_id, payload_json
                FROM edli_live_order_events
                WHERE event_type = 'PreSubmitRevalidated'
            ),
            decision_audit AS (
                SELECT aggregate_id, payload_json
                FROM edli_live_order_events
                WHERE event_type = 'DecisionProofAccepted'
            )
            SELECT
                live_cap.usage_id,
                live_cap.final_intent_id,
                live_cap.execution_command_id,
                COALESCE(
                    NULLIF(json_extract(pre_submit.payload_json, '$.city'), ''),
                    NULLIF(json_extract(decision_audit.payload_json, '$.decision_audit.city'), ''),
                    ?
                ) AS city,
                live_cap.reserved_notional_usd
            FROM live_cap
            LEFT JOIN observed ON observed.aggregate_id = live_cap.aggregate_id
            LEFT JOIN absence_reconciled ON absence_reconciled.aggregate_id = live_cap.aggregate_id
            LEFT JOIN pre_submit ON pre_submit.aggregate_id = live_cap.aggregate_id
            LEFT JOIN decision_audit ON decision_audit.aggregate_id = live_cap.aggregate_id
            WHERE observed.aggregate_id IS NULL
              AND absence_reconciled.aggregate_id IS NULL
            ORDER BY live_cap.usage_id
            """,
            (_DURABLE_LIVE_CAP_UNKNOWN_CITY,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - sizing must fail closed on exposure ambiguity.
        import logging

        logging.getLogger("zeus.edli_portfolio").warning(
            "durable live-cap exposure seed unavailable: %s: %s",
            type(exc).__name__,
            exc,
        )
        raise RuntimeError(
            f"DURABLE_LIVE_CAP_EXPOSURE_SEED_UNAVAILABLE:{type(exc).__name__}:{exc}"
        ) from exc
    out: list[tuple[str, str, float]] = []
    for row in rows:
        usage_id = str(row[0] if not isinstance(row, sqlite3.Row) else row["usage_id"])
        final_intent_id = str(row[1] if not isinstance(row, sqlite3.Row) else row["final_intent_id"] or "")
        execution_command_id = str(row[2] if not isinstance(row, sqlite3.Row) else row["execution_command_id"] or "")
        city = str(row[3] if not isinstance(row, sqlite3.Row) else row["city"])
        reserved = float(row[4] if not isinstance(row, sqlite3.Row) else row["reserved_notional_usd"])
        if _durable_live_cap_usage_is_represented_in_trade_truth(
            trade_conn,
            execution_command_id=execution_command_id,
            final_intent_id=final_intent_id,
        ):
            continue
        if usage_id and reserved > 0.0:
            out.append((f"durable_live_cap:{usage_id}", city, reserved))
    return tuple(out)


def _seed_portfolio_reservations_from_durable_live_cap(
    ledger: PortfolioReservationLedger,
    conn: sqlite3.Connection | None,
    *,
    trade_conn: sqlite3.Connection | None = None,
) -> int:
    seeded = 0
    for reservation_id, city, reserved_usd in _durable_unmaterialized_live_cap_reservations(
        conn,
        trade_conn=trade_conn,
    ):
        ledger.seed_committed(reservation_id, city, reserved_usd)
        seeded += 1
    return seeded


def _event_bound_strategy_key(
    *,
    event_type: str,
    direction: str | None,
    metric: str | None,
    require_metric_live: bool = False,
) -> str:
    """Classify the EDLI event-bound entry strategy without defaulting to settlement_capture."""

    normalized_direction = str(direction or "").strip().lower()
    normalized_metric = str(metric or "").strip().lower()
    if event_type == "DAY0_EXTREME_UPDATED":
        strategy = "settlement_capture"
    elif event_type == "FORECAST_SNAPSHOT_READY":
        strategy = "opening_inertia" if normalized_direction == "buy_no" else "center_buy"
    else:
        raise ValueError(f"EDLI_STRATEGY_UNSUPPORTED_EVENT_TYPE:{event_type}")

    from src.strategy.strategy_profile import try_get

    profile = try_get(strategy)
    if profile is None or not profile.is_runtime_live():
        raise ValueError(f"EDLI_STRATEGY_NOT_RUNTIME_LIVE:{strategy}")
    if normalized_direction and not profile.is_direction_allowed(normalized_direction):
        raise ValueError(
            f"EDLI_STRATEGY_DIRECTION_BLOCKED:{strategy}:direction={normalized_direction}"
        )
    if require_metric_live and normalized_metric and not profile.metric_is_live(normalized_metric):
        raise ValueError(f"EDLI_STRATEGY_METRIC_BLOCKED:{strategy}:metric={normalized_metric}")
    return strategy


def _assert_event_bound_strategy_live_admitted(
    *,
    strategy_key: str | None,
    direction: str | None,
    metric: str | None,
) -> None:
    """Fail closed before the executor boundary if registry semantics reject the receipt."""

    from src.strategy.strategy_profile import try_get

    normalized_strategy = str(strategy_key or "").strip()
    normalized_direction = str(direction or "").strip().lower()
    normalized_metric = str(metric or "").strip().lower()
    profile = try_get(normalized_strategy)
    if profile is None or not profile.is_runtime_live():
        raise ValueError(f"EDLI_STRATEGY_NOT_RUNTIME_LIVE:{normalized_strategy or 'missing'}")
    if normalized_direction and not profile.is_direction_allowed(normalized_direction):
        raise ValueError(
            f"EDLI_STRATEGY_DIRECTION_BLOCKED:{normalized_strategy}:direction={normalized_direction}"
        )
    if normalized_metric and not profile.metric_is_live(normalized_metric):
        raise ValueError(f"EDLI_STRATEGY_METRIC_BLOCKED:{normalized_strategy}:metric={normalized_metric}")


def _assert_event_bound_calibration_live_admitted(calibration: DecisionCertificate) -> None:
    """Identity fallback is evidence-only; it must never authorize real live commands."""

    payload = calibration.payload
    authority = str(payload.get("authority") or "").strip().upper()
    n_samples_raw = payload.get("n_samples")
    try:
        n_samples = int(n_samples_raw) if n_samples_raw is not None else None
    except (TypeError, ValueError):
        n_samples = None
    if authority == "IDENTITY_FALLBACK_NO_PLATT_BUCKET":
        raise ValueError("EDLI_LIVE_CALIBRATION_AUTHORITY_BLOCKED:IDENTITY_FALLBACK_NO_PLATT_BUCKET")
    if n_samples is not None and n_samples <= 0:
        raise ValueError(f"EDLI_LIVE_CALIBRATION_EMPTY_SAMPLE_BLOCKED:authority={authority or 'missing'}")


def _assert_event_bound_receipt_live_authority(receipt: EventSubmissionReceipt) -> None:
    """Real submit needs the family-selection provenance shown in receipts."""

    if str(receipt.q_source or "").strip() == "":
        raise ValueError("EDLI_LIVE_Q_SOURCE_MISSING")
    book = receipt.opportunity_book
    if not isinstance(book, dict):
        raise ValueError("EDLI_LIVE_OPPORTUNITY_BOOK_MISSING")
    selected = str(book.get("selected_candidate_id") or "").strip()
    actual = str(book.get("actual_receipt_selected_candidate_id") or "").strip()
    if not selected:
        raise ValueError("EDLI_LIVE_OPPORTUNITY_BOOK_SELECTED_MISSING")
    if actual and actual != selected:
        raise ValueError(
            "EDLI_LIVE_OPPORTUNITY_BOOK_SELECTION_MISMATCH:"
            f"selected={selected}:actual={actual}"
        )
    receipt_candidate_id = _opportunity_book_candidate_id_for_receipt(book, receipt)
    if not receipt_candidate_id:
        raise ValueError(
            "EDLI_LIVE_OPPORTUNITY_BOOK_RECEIPT_CANDIDATE_MISSING:"
            f"condition_id={receipt.condition_id}:token_id={receipt.token_id}:direction={receipt.direction}"
        )
    if receipt_candidate_id != selected:
        raise ValueError(
            "EDLI_LIVE_OPPORTUNITY_BOOK_RECEIPT_NOT_SELECTED:"
            f"selected={selected}:receipt_candidate={receipt_candidate_id}:"
            f"condition_id={receipt.condition_id}:token_id={receipt.token_id}:direction={receipt.direction}"
        )


def _opportunity_book_candidate_id_for_receipt(
    book: Mapping[str, object],
    receipt: EventSubmissionReceipt,
) -> str | None:
    candidates = book.get("candidates")
    if not isinstance(candidates, list):
        return None
    condition_id = str(receipt.condition_id or "").strip()
    token_id = str(receipt.token_id or "").strip()
    direction = str(receipt.direction or "").strip()
    if not condition_id or not token_id or not direction:
        return None
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        if str(candidate.get("condition_id") or "").strip() != condition_id:
            continue
        if str(candidate.get("token_id") or "").strip() != token_id:
            continue
        if str(candidate.get("direction") or "").strip() != direction:
            continue
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        return candidate_id or None
    return None


def build_event_reactor(
    store: EventStore,
    *,
    source_truth_gate,
    executable_snapshot_gate,
    riskguard_gate,
    final_intent_submit,
    reject,
    config: ReactorConfig | None = None,
    regret_ledger=None,
) -> OpportunityEventReactor:
    return OpportunityEventReactor(
        store,
        source_truth_gate=source_truth_gate,
        executable_snapshot_gate=executable_snapshot_gate,
        riskguard_gate=riskguard_gate,
        final_intent_submit=final_intent_submit,
        reject=reject,
        config=config,
        regret_ledger=regret_ledger,
    )


def edli_source_truth_gate(event: OpportunityEvent) -> bool:
    """Fail closed unless an EDLI event is source-eligible for a live cycle."""

    payload = _payload(event)
    if event.event_type == "FORECAST_SNAPSHOT_READY":
        return (
            bool(event.causal_snapshot_id)
            and payload.get("completeness_status") == "COMPLETE"
            and payload.get("required_fields_present") is True
            and payload.get("required_steps_present") is True
        )
    if event.event_type == "DAY0_EXTREME_UPDATED":
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
    return False


def executable_snapshot_gate_from_trade_conn(
    trade_conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    topology_conn: sqlite3.Connection | None = None,
) -> Callable[[OpportunityEvent, datetime], bool]:
    """Return a gate requiring a fresh executable snapshot bound to the event."""

    fallback_checked_at = now.astimezone(UTC) if now is not None else None

    def _gate(event: OpportunityEvent, decision_time: datetime) -> bool:
        if topology_conn is None:
            return False
        checked_at = (
            decision_time.astimezone(UTC)
            if decision_time.tzinfo is not None and decision_time.utcoffset() is not None
            else fallback_checked_at
        )
        if checked_at is None:
            return False
        if not _table_exists(trade_conn, "executable_market_snapshots"):
            return False
        columns = _table_columns(trade_conn, "executable_market_snapshots")
        required = {"freshness_deadline", "yes_token_id", "no_token_id"}
        if not required <= columns:
            return False
        payload = _payload(event)
        family_topology_rows = _event_family_market_topology_rows(topology_conn, payload)
        if not family_topology_rows:
            return False
        condition_ids = tuple(str(row.get("condition_id") or "") for row in family_topology_rows)
        rows = _latest_snapshot_rows_for_event_family(
            trade_conn,
            event,
            condition_ids=condition_ids,
            fresh_at=checked_at,
            require_fresh=False,  # entry gate proves market identity; price-freshness is enforced at submission
        )
        if not rows:
            return False
        # Entry gate: at least one sibling snapshot present AND the event's own selected
        # bin has a snapshot. The full-family q/FDR proof is enforced inside
        # build_event_bound_no_submit_receipt; here we only need to know the market
        # family is partially live and the selected bin is executable. Illiquid tail
        # bins absent from executable_market_snapshots are non-tradeable but still part
        # of the full MECE topology — exact-set-equality is the wrong predicate here.
        return _selected_snapshot_row_for_event(rows, payload) is not None

    return _gate


def riskguard_allows_new_entries(*, get_current_level: Callable[[], RiskLevel]) -> Callable[[OpportunityEvent], bool]:
    """Return a reactor gate that preserves RiskGuard's entry-blocking law."""

    def _gate(_event: OpportunityEvent) -> bool:
        return get_current_level() == RiskLevel.GREEN

    return _gate


def edli_trade_score_gate(event: OpportunityEvent) -> bool:
    """TradeScore is generated inside the event-bound no-submit adapter.

    Forecast and Day0 events are causal facts; they must not carry q/c/FDR/Kelly
    proof fields as event-authoritative payload data.
    """

    return event.event_type in {"FORECAST_SNAPSHOT_READY", "DAY0_EXTREME_UPDATED"}


def _resolve_replacement_forecast_adapter_hook(
    *,
    replacement_forecast_hook: Callable[["_CandidateProof", OpportunityEvent, datetime], ReplacementForecastReactorHookResult | None] | None,
    replacement_forecast_runtime_flags: Mapping[str, object] | None,
    replacement_forecast_baseline_bundle_provider: Callable[["_CandidateProof", OpportunityEvent, datetime], object | None] | None,
    replacement_forecast_world_tables: tuple[str, ...],
    replacement_forecast_source_fact_status: str,
    replacement_forecast_data_fact_status: str,
    replacement_forecast_refit_decision: ReplacementForecastRefitDecision | None,
    replacement_forecast_promotion_evidence: ReplacementForecastPromotionEvidence | None,
    replacement_forecast_capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None,
    forecast_conn: sqlite3.Connection | None,
    trade_conn: sqlite3.Connection,
) -> Callable[["_CandidateProof", OpportunityEvent, datetime], ReplacementForecastReactorHookResult | None] | None:
    if replacement_forecast_hook is not None or replacement_forecast_runtime_flags is None:
        return replacement_forecast_hook
    if forecast_conn is None:
        def _missing_forecast_conn_hook(
            proof: _CandidateProof,
            _event: OpportunityEvent,
            _decision_time: datetime,
        ) -> ReplacementForecastReactorHookResult:
            return ReplacementForecastReactorHookResult(
                status="BLOCKED",
                reason_codes=("REPLACEMENT_FORECAST_HOOK_FORECAST_CONNECTION_MISSING",),
                effective_direction=proof.direction,
                effective_q_posterior=proof.q_posterior,
                effective_q_lcb=proof.q_lcb_5pct,
                effective_kelly_fraction=0.0,
            )

        return _missing_forecast_conn_hook
    from src.engine.replacement_forecast_hook_factory import (
        ReplacementForecastHookFactoryInput,
        build_replacement_forecast_event_hook,
    )

    return build_replacement_forecast_event_hook(
        ReplacementForecastHookFactoryInput(
            forecast_conn=forecast_conn,
            trade_conn=trade_conn,
            runtime_flags=replacement_forecast_runtime_flags,
            baseline_bundle_provider=replacement_forecast_baseline_bundle_provider,
            refit_decision=replacement_forecast_refit_decision,
            promotion_evidence=replacement_forecast_promotion_evidence,
            capital_objective_evidence=replacement_forecast_capital_objective_evidence,
            world_tables=replacement_forecast_world_tables,
            source_fact_status=replacement_forecast_source_fact_status,
            data_fact_status=replacement_forecast_data_fact_status,
        )
    )


def replacement_forecast_baseline_bundle_provider_from_forecast_conn(
    forecast_conn: sqlite3.Connection,
) -> Callable[["_CandidateProof", OpportunityEvent, datetime], object | None]:
    """Build the B0 executable forecast provider used by replacement shadow/veto."""

    def _provider(
        proof: _CandidateProof,
        event: OpportunityEvent,
        decision_time: datetime,
    ) -> object | None:
        from src.data.executable_forecast_reader import SOURCE_TRANSPORT, read_executable_forecast

        payload = _payload(event)
        candidate = proof.candidate
        city = str(payload.get("city") or candidate.city)
        target_date_text = str(payload.get("target_date") or candidate.target_date)
        metric = str(payload.get("metric") or candidate.metric)
        source_run_id = str(payload.get("source_run_id") or "")
        source_id = str(payload.get("source_id") or "")
        track = str(payload.get("track") or "")
        if not city or not target_date_text or metric not in {"high", "low"} or not source_run_id:
            return None
        table_ref = _authority_table_ref(forecast_conn, "source_run_coverage")
        if table_ref is None:
            return None
        columns = _table_ref_columns(forecast_conn, table_ref)
        required = {
            "source_run_id",
            "city",
            "target_local_date",
            "temperature_metric",
            "data_version",
            "source_id",
            "track",
            "computed_at",
        }
        if not required.issubset(columns):
            return None
        predicates = [
            "source_run_id = ?",
            "city = ?",
            "target_local_date = ?",
            "temperature_metric = ?",
        ]
        params: list[object] = [source_run_id, city, target_date_text, metric]
        if source_id:
            predicates.append("source_id = ?")
            params.append(source_id)
        if track:
            predicates.append("track = ?")
            params.append(track)
        row = forecast_conn.execute(
            f"""
            SELECT *
            FROM {table_ref}
            WHERE {' AND '.join(predicates)}
            ORDER BY computed_at DESC, recorded_at DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        if row is None:
            return None
        coverage = dict(row)
        city_id = str(coverage.get("city_id") or city)
        city_timezone = str(coverage.get("city_timezone") or getattr(runtime_cities_by_name().get(city), "timezone", "UTC"))
        final_source_id = str(coverage.get("source_id") or source_id)
        source_transport = str(coverage.get("source_transport") or SOURCE_TRANSPORT)
        data_version = str(coverage.get("data_version") or "")
        final_track = str(coverage.get("track") or track)
        if not final_source_id or not data_version or not final_track:
            return None
        result = read_executable_forecast(
            forecast_conn,
            city_id=city_id,
            city_name=str(coverage.get("city") or city),
            city_timezone=city_timezone,
            target_local_date=date.fromisoformat(target_date_text),
            temperature_metric=metric,
            source_id=final_source_id,
            source_transport=source_transport,
            data_version=data_version,
            track=final_track,
            strategy_key="entry_forecast",
            market_family=str(candidate.condition_id or ""),
            condition_id=str(candidate.condition_id or ""),
            decision_time=decision_time,
            require_entry_readiness=False,
        )
        return result.bundle if result.ok and result.bundle is not None else None

    return _provider


def event_bound_no_submit_adapter_from_trade_conn(
    trade_conn: sqlite3.Connection,
    *,
    get_current_level: Callable[[], RiskLevel],
    forecast_conn: sqlite3.Connection | None = None,
    topology_conn: sqlite3.Connection | None = None,
    calibration_conn: sqlite3.Connection | None = None,
    live_cap_conn: sqlite3.Connection | None = None,
    bankroll_usd_provider: Callable[[], float | None] | None = None,
    portfolio_state_provider: "Callable[[], Any] | None" = None,
    replacement_forecast_hook: Callable[["_CandidateProof", OpportunityEvent, datetime], ReplacementForecastReactorHookResult | None] | None = None,
    replacement_forecast_runtime_flags: Mapping[str, object] | None = None,
    replacement_forecast_baseline_bundle_provider: Callable[["_CandidateProof", OpportunityEvent, datetime], object | None] | None = None,
    replacement_forecast_world_tables: tuple[str, ...] = (),
    replacement_forecast_source_fact_status: str = "STALE_FOR_LIVE",
    replacement_forecast_data_fact_status: str = "STALE_FOR_LIVE",
    replacement_forecast_refit_decision: ReplacementForecastRefitDecision | None = None,
    replacement_forecast_promotion_evidence: ReplacementForecastPromotionEvidence | None = None,
    replacement_forecast_capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None = None,
) -> Callable[[OpportunityEvent, datetime], EventSubmissionReceipt]:
    """Build a proof-only final-intent receipt adapter for EDLI events.

    Task #107 (portfolio/multi Kelly): ``portfolio_state_provider`` (mirrors
    ``bankroll_usd_provider``) lets Kelly size against the bankroll NET of
    correlation-weighted committed capital. The per-cycle in-flight reservation
    accumulator (INV-K7) is CLOSURE-held here — NOT module-global — so parallel
    cycles / tests stay isolated. One adapter instance == one reactor cycle, so
    the accumulator is fresh per cycle by construction."""

    # INV-K7 reservation ledger: closure-held (test-isolation safe), fresh per
    # adapter instance (== per reactor cycle). FIX B (2026-06-05): rollback-aware
    # ledger (not a bare list) so a candidate rejected downstream of Kelly is
    # rolled back by the reactor before the next sequential event reads it.
    portfolio_reservation = PortfolioReservationLedger()
    _seed_portfolio_reservations_from_durable_live_cap(
        portfolio_reservation,
        live_cap_conn,
        trade_conn=trade_conn,
    )
    resolved_replacement_forecast_hook = _resolve_replacement_forecast_adapter_hook(
        replacement_forecast_hook=replacement_forecast_hook,
        replacement_forecast_runtime_flags=replacement_forecast_runtime_flags,
        replacement_forecast_baseline_bundle_provider=replacement_forecast_baseline_bundle_provider,
        replacement_forecast_world_tables=replacement_forecast_world_tables,
        replacement_forecast_source_fact_status=replacement_forecast_source_fact_status,
        replacement_forecast_data_fact_status=replacement_forecast_data_fact_status,
        replacement_forecast_refit_decision=replacement_forecast_refit_decision,
        replacement_forecast_promotion_evidence=replacement_forecast_promotion_evidence,
        replacement_forecast_capital_objective_evidence=replacement_forecast_capital_objective_evidence,
        forecast_conn=forecast_conn,
        trade_conn=trade_conn,
    )

    def _submit(event: OpportunityEvent, decision_time: datetime) -> EventSubmissionReceipt:
        # CATEGORY ANTIBODY (2026-06-08, "database is locked" HOLDER-side kill):
        # same trade-DB lock-hold disease as the live adapter — the reactor's single
        # per-cycle trade_conn is read/written here via build_event_bound_no_submit_
        # receipt and committed NOWHERE in process_pending (only closed at cycle end),
        # so the implicit transaction pins the trade-DB WAL lock / read-mark across
        # the whole multi-event cycle and starves concurrent trade-DB writers
        # (substrate warm, log_trade_exit, CollateralLedger heartbeat). Commit
        # trade_conn per event in a finally to release the lock and end the WAL-floor-
        # pinning read txn each event (mirrors the live adapter + reactor world-DB
        # per-event windows). In-memory reservation ledger is unaffected; no gate or
        # decision semantics change — this only bounds the lock-hold.
        try:
            return build_event_bound_no_submit_receipt(
                event,
                trade_conn=trade_conn,
                decision_time=decision_time,
                forecast_conn=forecast_conn,
                topology_conn=topology_conn,
                calibration_conn=calibration_conn,
                get_current_level=get_current_level,
                bankroll_usd_provider=bankroll_usd_provider,
                portfolio_state_provider=portfolio_state_provider,
                portfolio_reservation=portfolio_reservation,
                locked_opportunity_conn=live_cap_conn or trade_conn,
                replacement_forecast_hook=resolved_replacement_forecast_hook,
                replacement_forecast_promotion_evidence=replacement_forecast_promotion_evidence,
                replacement_forecast_capital_objective_evidence=replacement_forecast_capital_objective_evidence,
            )
        finally:
            try:
                trade_conn.commit()
            except Exception:  # noqa: BLE001 - commit is a lock-release boundary; never mask the real result/raise
                pass

    # Expose the per-cycle ledger so the reactor can commit/rollback provisional
    # reservations in its post-submit phase (FIX B). The reactor reads this
    # attribute off the injected submit callable; absent it falls back to the
    # legacy append-only behavior (no commit/rollback).
    _submit.reservation_ledger = portfolio_reservation  # type: ignore[attr-defined]
    return _submit


def event_bound_live_adapter_from_trade_conn(
    trade_conn: sqlite3.Connection,
    *,
    get_current_level: Callable[[], RiskLevel],
    forecast_conn: sqlite3.Connection | None = None,
    topology_conn: sqlite3.Connection | None = None,
    calibration_conn: sqlite3.Connection | None = None,
    live_cap_conn: sqlite3.Connection | None = None,
    bankroll_usd_provider: Callable[[], float | None] | None = None,
    portfolio_state_provider: "Callable[[], Any] | None" = None,
    replacement_forecast_hook: Callable[["_CandidateProof", OpportunityEvent, datetime], ReplacementForecastReactorHookResult | None] | None = None,
    replacement_forecast_runtime_flags: Mapping[str, object] | None = None,
    replacement_forecast_baseline_bundle_provider: Callable[["_CandidateProof", OpportunityEvent, datetime], object | None] | None = None,
    replacement_forecast_world_tables: tuple[str, ...] = (),
    replacement_forecast_source_fact_status: str = "STALE_FOR_LIVE",
    replacement_forecast_data_fact_status: str = "STALE_FOR_LIVE",
    replacement_forecast_refit_decision: ReplacementForecastRefitDecision | None = None,
    replacement_forecast_promotion_evidence: ReplacementForecastPromotionEvidence | None = None,
    replacement_forecast_capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None = None,
    real_order_submit_enabled: bool = False,
    live_canary_enabled: bool = False,
    executor_submit: Callable[[DecisionCertificate, DecisionCertificate], EventBoundExecutorSubmitResult] | None = None,
    pre_submit_authority_provider: Callable[[DecisionCertificate, DecisionCertificate, datetime], PreSubmitAuthorityWitness] | None = None,
    durable_submit_outbox_enabled: bool = False,
    canary_force_taker_provider: Callable[[], bool] | None = None,
    taker_fok_fak_live_enabled: bool = False,
    operator_arm: "OperatorArm | None" = None,
    edli_live_scope: str = "forecast_only",
) -> Callable[[OpportunityEvent, datetime], EventSubmissionReceipt]:
    """Build the event-bound live certificate chain up to the executor boundary.

    This first full-live increment deliberately stops before executor submit
    when real submit is disabled. It creates the durable proof shape that a
    later live-canary authorization can submit through the existing executor seam.

    Task #107 (portfolio/multi Kelly): ``portfolio_state_provider`` (mirrors
    ``bankroll_usd_provider``) lets Kelly size against the bankroll NET of
    correlation-weighted committed capital. The INV-K7 per-cycle in-flight
    reservation accumulator is CLOSURE-held (test-isolation safe), fresh per
    adapter instance (== per reactor cycle).
    """

    # FIX-3 (P1): day0_shadow scope → structural no-submit at the FINAL ADAPTER
    # BOUNDARY for day0-lane events. edli_live_scope="day0_shadow" ADMITS day0
    # events (the mask and shadow certs run) but the word "shadow" must not lie:
    # no real submit can ever reach the venue for a day0 event under this scope.
    # The guard lives here (not at admission) so future admission changes cannot
    # bypass it. Fail-closed: unknown event_type is treated as day0 (rejected).
    #
    # forecast_plus_day0 (operator directive 2026-06-09 '全部打开'): day0-lane
    # events PASS this boundary (real submit allowed, subject to all OTHER
    # proofs/gates/arm downstream). The unknown-event-type fail-closed posture is
    # preserved under both shadow-style scopes — an event type that is neither
    # the known forecast lane nor the known day0 lane is rejected.
    _DAY0_LANE_EVENT_TYPES: frozenset[str] = frozenset({"DAY0_EXTREME_UPDATED"})

    # FIX-4 (P2): per-cycle live submit call counter. Incremented ONLY when
    # executor_submit() is actually called (i.e., real_order_submit_enabled and
    # all gates pass). Exposed on the adapter callable so main.py can read it
    # after process_pending and populate live_submit_attempts accurately.
    # No-submit / degraded cycles always read 0.
    _live_submit_count: list[int] = [0]

    # FIX-4 venue_acks: per-cycle counter of actual venue ACKs (successful
    # place_limit_order responses).  Incremented when venue_ack_received is
    # True on the submit result.  Exposed on the adapter callable for main.py.
    _live_ack_count: list[int] = [0]

    # INV-K7 reservation ledger: closure-held, fresh per reactor cycle. FIX B
    # (2026-06-05): rollback-aware so a candidate rejected downstream of Kelly is
    # rolled back by the reactor before the next sequential event reads it.
    portfolio_reservation = PortfolioReservationLedger()
    _seed_portfolio_reservations_from_durable_live_cap(
        portfolio_reservation,
        live_cap_conn,
        trade_conn=trade_conn,
    )
    resolved_replacement_forecast_hook = _resolve_replacement_forecast_adapter_hook(
        replacement_forecast_hook=replacement_forecast_hook,
        replacement_forecast_runtime_flags=replacement_forecast_runtime_flags,
        replacement_forecast_baseline_bundle_provider=replacement_forecast_baseline_bundle_provider,
        replacement_forecast_world_tables=replacement_forecast_world_tables,
        replacement_forecast_source_fact_status=replacement_forecast_source_fact_status,
        replacement_forecast_data_fact_status=replacement_forecast_data_fact_status,
        replacement_forecast_refit_decision=replacement_forecast_refit_decision,
        replacement_forecast_promotion_evidence=replacement_forecast_promotion_evidence,
        replacement_forecast_capital_objective_evidence=replacement_forecast_capital_objective_evidence,
        forecast_conn=forecast_conn,
        trade_conn=trade_conn,
    )

    def _submit_inner(event: OpportunityEvent, decision_time: datetime) -> EventSubmissionReceipt:
        # FIX-3 (P1) DAY0_SCOPE_SHADOW_ONLY boundary gate.
        # When edli_live_scope is "day0_shadow", any event whose type belongs to
        # the day0 lane gets a deterministic no-submit rejection here at the
        # FINAL ADAPTER boundary — BEFORE the no-submit proof chain runs and
        # BEFORE any venue interaction. Fail-closed: an event_type that is not
        # in the known forecast lane while scope is day0_shadow is treated as
        # day0 (rejected), with a loud log to surface the anomaly.
        if edli_live_scope in ("day0_shadow", "forecast_plus_day0"):
            event_type = getattr(event, "event_type", None)
            _FORECAST_LANE_EVENT_TYPES: frozenset[str] = frozenset({"FORECAST_SNAPSHOT_READY"})
            is_forecast_lane = event_type in _FORECAST_LANE_EVENT_TYPES
            is_day0_lane = event_type in _DAY0_LANE_EVENT_TYPES
            # forecast_plus_day0 admits day0-lane events through this boundary;
            # day0_shadow rejects them. In BOTH scopes, an event type that is
            # neither known lane is fail-closed (treated as day0, rejected).
            day0_lane_blocked_here = edli_live_scope == "day0_shadow"
            if not is_forecast_lane and not is_day0_lane:
                import logging as _logging
                _logging.getLogger(__name__).error(
                    "DAY0_SCOPE_SHADOW_ONLY: unknown event_type=%r treated as day0 (fail-closed) "
                    "while edli_live_scope=%r",
                    event_type,
                    edli_live_scope,
                )
            reject_day0_lane = is_day0_lane and day0_lane_blocked_here
            reject_unknown = not is_forecast_lane and not is_day0_lane
            if reject_day0_lane or reject_unknown:
                return EventSubmissionReceipt(
                    False,
                    event.event_id,
                    event.causal_snapshot_id,
                    reason="DAY0_SCOPE_SHADOW_ONLY",
                    proof_accepted=False,
                )
        no_submit_receipt = build_event_bound_no_submit_receipt(
            event,
            trade_conn=trade_conn,
            decision_time=decision_time,
            forecast_conn=forecast_conn,
            topology_conn=topology_conn,
            calibration_conn=calibration_conn,
            get_current_level=get_current_level,
            bankroll_usd_provider=bankroll_usd_provider,
            portfolio_state_provider=portfolio_state_provider,
            portfolio_reservation=portfolio_reservation,
            locked_opportunity_conn=live_cap_conn or trade_conn,
            replacement_forecast_hook=resolved_replacement_forecast_hook,
            replacement_forecast_promotion_evidence=replacement_forecast_promotion_evidence,
            replacement_forecast_capital_objective_evidence=replacement_forecast_capital_objective_evidence,
        )
        if no_submit_receipt.proof_accepted is not True or no_submit_receipt.decision_proof_bundle is None:
            return no_submit_receipt
        if real_order_submit_enabled and not live_canary_enabled:
            return EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason="LIVE_CANARY_DISABLED",
                proof_accepted=False,
            )
        if real_order_submit_enabled and not durable_submit_outbox_enabled:
            return EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason="EDLI_DURABLE_SUBMIT_OUTBOX_REQUIRED",
                proof_accepted=False,
            )
        if real_order_submit_enabled and executor_submit is None:
            return EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason="EXECUTOR_BOUNDARY_MISSING",
                proof_accepted=False,
            )
        # FIX-2b (PR_SPEC.md §2) OPERATOR ARM GATE: every real submit on the EDLI
        # boundary requires the operator-arm capability token, regardless of mode
        # (canary included). The token is constructible ONLY in main.py via
        # ``require_operator_arm`` after asserting ``edli_live_operator_authorized is
        # True``. Absent the token this fails closed BEFORE the live-order build /
        # executor seam. This is an UPSTREAM guard on the EDLI adapter only; the
        # mainline convergence node never constructs this adapter, so the 293-order
        # mainline is unaffected.
        if real_order_submit_enabled and operator_arm is None:
            return EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason="OPERATOR_ARM_REQUIRED",
                proof_accepted=False,
            )
        # OPERATOR LAW (2026-06-04, Rule-4 antibody): mainstream is OBSERVATIONAL /
        # DISPLAY-ONLY — it is NEVER a decision input. The former submit-time mainstream
        # enforce branch (which rejected an armed submit on a missing/failed mainstream
        # verdict) is DELETED so mainstream has NO code path to gate / reject / skip /
        # alter direction, q, q_lcb, trade_score, selection, or submit. The verdict is
        # computed + annotated on the receipt for the operator's ARM review only; it can
        # never change a decision. This makes "mainstream changes a decision"
        # UNCONSTRUCTABLE (not merely flag-OFF). The dead config key is left inert.
        # Canary knob (§7): force the taker branch (bypassing the governor's
        # maker/taker CHOICE, never its NO_TRADE/risk gates) while the canary is
        # active and below its min fill count. main.py owns the count gate via
        # ``canary_force_taker_provider``; absent a provider, the canary stage
        # flag itself drives the force (the count gate lives upstream in the
        # stage-readiness check).
        if canary_force_taker_provider is not None:
            try:
                canary_force_taker = bool(canary_force_taker_provider())
            except Exception:
                canary_force_taker = bool(live_canary_enabled)
        else:
            canary_force_taker = bool(live_canary_enabled)
        try:
            if real_order_submit_enabled:
                build_conn = live_cap_conn or trade_conn
                command_certificates = _run_live_order_build_savepoint(
                    build_conn,
                    lambda: _build_live_execution_command_certificates(
                        event=event,
                        receipt=no_submit_receipt,
                        decision_time=decision_time.astimezone(UTC),
                        live_cap_conn=build_conn,
                        trade_conn=trade_conn,
                        pre_submit_authority_provider=pre_submit_authority_provider,
                        canary_force_taker=canary_force_taker,
                        taker_fok_fak_live_enabled=taker_fok_fak_live_enabled,
                    ),
                )
                final_intent = _required_cert(command_certificates, claims.FINAL_INTENT)
                command = _required_cert(command_certificates, claims.EXECUTION_COMMAND)
                assert executor_submit is not None
                _append_venue_submit_attempted_aggregate_event(
                    live_cap_conn or trade_conn,
                    command,
                    decision_time=decision_time.astimezone(UTC),
                )
                _live_submit_count[0] += 1  # FIX-4: count actual venue submit calls
                submit_result = executor_submit(final_intent, command)
                if submit_result.venue_ack_received:
                    _live_ack_count[0] += 1  # FIX-4 venue_acks: count actual ACKs
                receipt_cert = build_execution_receipt_certificate(
                    execution_command_cert=command,
                    decision_time=decision_time.astimezone(UTC),
                    status=submit_result.status,
                    reason_code=submit_result.reason_code,
                    submit_started_at=submit_result.submit_started_at,
                    submit_finished_at=submit_result.submit_finished_at,
                    venue_order_id=submit_result.venue_order_id,
                    raw_response=submit_result.raw_response,
                    raw_response_hash=submit_result.raw_response_hash,
                    reconciliation_followup_required=submit_result.reconciliation_followup_required,
                    venue_call_started=submit_result.venue_call_started,
                    venue_ack_received=submit_result.venue_ack_received,
                    side_effect_known=submit_result.side_effect_known,
                )
                _append_submit_terminal_aggregate_event(
                    live_cap_conn or trade_conn,
                    command,
                    receipt_cert,
                    submit_result=submit_result,
                    decision_time=decision_time.astimezone(UTC),
                )
                transition_cert = _transition_live_cap_after_submit(
                    command_certificates,
                    live_cap_conn or trade_conn,
                    command,
                    receipt_cert,
                    submit_result,
                    decision_time=decision_time.astimezone(UTC),
                )
                certificates = command_certificates + (receipt_cert, transition_cert)
                side_effect_status = submit_result.status
                submitted = submit_result.status in {"SUBMITTED"}
                reason = submit_result.reason_code
            else:
                build_conn = live_cap_conn or trade_conn
                certificates = _run_live_order_build_savepoint(
                    build_conn,
                    lambda: _build_submit_disabled_live_certificates(
                        event=event,
                        receipt=no_submit_receipt,
                        decision_time=decision_time.astimezone(UTC),
                        live_cap_conn=build_conn,
                        trade_conn=trade_conn,
                        pre_submit_authority_provider=pre_submit_authority_provider,
                        canary_force_taker=canary_force_taker,
                        taker_fok_fak_live_enabled=taker_fok_fak_live_enabled,
                    ),
                )
                side_effect_status = "SUBMIT_DISABLED"
                submitted = False
                reason = "real_order_submit_disabled"
        except _LiveOpportunityAlreadyLocked as exc:
            return dataclass_replace(
                no_submit_receipt,
                side_effect_status="NO_SUBMIT",
                reason=str(exc),
                proof_accepted=True,
            )
        except Exception as exc:
            return EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason=f"EDLI_LIVE_CERTIFICATE_BUILD_FAILED:{exc}",
                proof_accepted=False,
            )
        return EventSubmissionReceipt(
            submitted=submitted,
            event_id=no_submit_receipt.event_id,
            causal_snapshot_id=no_submit_receipt.causal_snapshot_id,
            city=no_submit_receipt.city,
            target_date=no_submit_receipt.target_date,
            metric=no_submit_receipt.metric,
            condition_id=no_submit_receipt.condition_id,
            token_id=no_submit_receipt.token_id,
            outcome_label=no_submit_receipt.outcome_label,
            candidate_id=no_submit_receipt.candidate_id,
            executable_snapshot_id=no_submit_receipt.executable_snapshot_id,
            family_id=no_submit_receipt.family_id,
            bin_label=no_submit_receipt.bin_label,
            direction=no_submit_receipt.direction,
            q_live=no_submit_receipt.q_live,
            q_lcb_5pct=no_submit_receipt.q_lcb_5pct,
            c_fee_adjusted=no_submit_receipt.c_fee_adjusted,
            c_cost_95pct=no_submit_receipt.c_cost_95pct,
            p_fill_lcb=no_submit_receipt.p_fill_lcb,
            trade_score=no_submit_receipt.trade_score,
            native_quote_available=no_submit_receipt.native_quote_available,
            source_status=no_submit_receipt.source_status,
            family_complete=no_submit_receipt.family_complete,
            trade_score_positive=no_submit_receipt.trade_score_positive,
            fdr_pass=no_submit_receipt.fdr_pass,
            fdr_family_id=no_submit_receipt.fdr_family_id,
            fdr_hypothesis_count=no_submit_receipt.fdr_hypothesis_count,
            kelly_pass=no_submit_receipt.kelly_pass,
            kelly_execution_price_type=no_submit_receipt.kelly_execution_price_type,
            kelly_price_fee_deducted=no_submit_receipt.kelly_price_fee_deducted,
            kelly_size_usd=no_submit_receipt.kelly_size_usd,
            kelly_cost_basis_id=no_submit_receipt.kelly_cost_basis_id,
            kelly_decision_id=no_submit_receipt.kelly_decision_id,
            risk_decision_id=no_submit_receipt.risk_decision_id,
            final_intent_id=no_submit_receipt.final_intent_id,
            neg_risk=no_submit_receipt.neg_risk,
            side_effect_status=side_effect_status,
            reason=reason,
            proof_accepted=True,
            decision_proof_bundle=certificates,
            mainstream_agreement_pass=no_submit_receipt.mainstream_agreement_pass,
            mainstream_agreement_fail_reason=no_submit_receipt.mainstream_agreement_fail_reason,
            mainstream_point=no_submit_receipt.mainstream_point,
            mainstream_delta=no_submit_receipt.mainstream_delta,
            mainstream_bin_label=no_submit_receipt.mainstream_bin_label,
            mainstream_source=no_submit_receipt.mainstream_source,
            mainstream_fetched_at_utc=no_submit_receipt.mainstream_fetched_at_utc,
            alpha_gap=no_submit_receipt.alpha_gap,
            q_source=no_submit_receipt.q_source,
            strategy_key=no_submit_receipt.strategy_key,
            opportunity_book=no_submit_receipt.opportunity_book,
            replacement_forecast=no_submit_receipt.replacement_forecast,
            unit=no_submit_receipt.unit,
        )

    def _submit(event: OpportunityEvent, decision_time: datetime) -> EventSubmissionReceipt:
        # CATEGORY ANTIBODY (2026-06-08, "database is locked" HOLDER-side kill):
        # the reactor opens ONE trade connection per cycle (main.py:5231) and hands
        # it here; sqlite3 isolation_level="" makes the first read/write inside
        # _submit_inner open an implicit transaction that takes the trade-DB WAL
        # write lock (on the live-order build INSERTs) / pins the WAL read-mark, and
        # NOTHING in process_pending commits trade_conn until cycle-end close().
        # Across a multi-event cycle (each event doing a venue HTTP POST inside the
        # executor) that lock is held continuously, so the substrate-warm cycle,
        # log_trade_exit, and the CollateralLedger heartbeat all block out their
        # busy_timeout and record "database is locked" (live 2026-06-08 09:43-09:52).
        #
        # Fix (mirrors the reactor's WORLD-DB per-event commit windows in
        # events/reactor.py and the harvester per-event commit in
        # ingest/harvester_truth_writer): commit trade_conn at the END of EVERY
        # _submit — accept, gate-reject, or raise — in a finally. This releases the
        # trade-DB write lock AND ends the WAL-floor-pinning read transaction per
        # event, so concurrent trade-DB writers get a write window each event and
        # the WAL can checkpoint. The executor already commits its own venue-command
        # write units durably; this commits the remaining adapter-level trade_conn
        # writes (durable, intended) + closes the read txn. The provisional
        # PortfolioReservationLedger is IN-MEMORY (sizing/portfolio_reservation.py),
        # so the reactor's post-submit commit/rollback of reservations is unaffected
        # by a trade_conn.commit() here. No gate is weakened: the commit only
        # bounds the lock-hold; it changes no decision, gate, or submit semantics.
        try:
            return _submit_inner(event, decision_time)
        finally:
            try:
                trade_conn.commit()
            except Exception:  # noqa: BLE001 - commit is a lock-release boundary; never mask the real result/raise
                pass

    # FIX B: expose the per-cycle ledger so the reactor commits/rolls back
    # provisional reservations in its post-submit phase.
    _submit.reservation_ledger = portfolio_reservation  # type: ignore[attr-defined]
    # FIX-4: expose the live submit call counter so main.py can read it after
    # process_pending to populate live_submit_attempts in the status pulse.
    _submit._live_submit_count = _live_submit_count  # type: ignore[attr-defined]
    # FIX-4 venue_acks: expose the ACK counter alongside submit counter.
    _submit._live_ack_count = _live_ack_count  # type: ignore[attr-defined]
    return _submit


def _run_live_order_build_savepoint(
    conn: sqlite3.Connection,
    build: Callable[[], tuple[DecisionCertificate, ...]],
) -> tuple[DecisionCertificate, ...]:
    conn.execute("SAVEPOINT edli_live_order_build")
    try:
        result = build()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT edli_live_order_build")
        conn.execute("RELEASE SAVEPOINT edli_live_order_build")
        raise
    conn.execute("RELEASE SAVEPOINT edli_live_order_build")
    return result


# --------------------------------------------------------------------------- #
# forecast_only market-phase admission gate (#98).
#
# forecast_only is BLIND to observation: the instant a market's target LOCAL
# DAY begins, its daily extremum starts realizing and a forecast-only decision
# can land on the already-observed (losing) side — the Paris 2026-06-01 buy_no
# on observed low=14°C incident. Category-killing rule (STRONGER than
# DAY0_OBSERVATION_WRONGSIDE_ROOT §4.1; see DESIGN_CRITIC_2026-06-01 MAJOR-4):
# admit ONLY MarketPhase.PRE_SETTLEMENT_DAY (the whole target local day still
# in the future). SETTLEMENT_DAY / POST_TRADING / RESOLVED / unknown all reject
# fail-closed. Same-day edge belongs to the disjoint day0 observation-aware
# scope, never forecast_only. Authority: src/strategy/market_phase.py.
#
# WAVE-1 W1-T1: the admit-set and the predicate are now the SHARED canonical
# objects from src.strategy.market_phase (FORECAST_ONLY_ADMIT_PHASES /
# market_phase_admits), imported at module top. The reactor keeps this
# evidence-building backstop (it needs the typed evidence for the
# EVENT_BOUND_MARKET_PHASE_CLOSED rejection reason); the admit verdict cannot
# diverge from the intake filter because both consult the same frozenset.
# --------------------------------------------------------------------------- #


def _edli_forecast_only_phase_evidence(
    *,
    city: str,
    target_date: str,
    decision_time: datetime,
    selected_market_row: Mapping[str, Any] | None,
    uma_resolved_source: str | None = None,
) -> "_market_phase_evidence.MarketPhaseEvidence":
    """Phase evidence for a forecast_only family at decision_time.

    Fail-closed: when the city has no resolvable timezone the phase is
    undeterminable and the returned evidence carries phase=None (the caller then
    rejects). The selected snapshot row supplies an explicit endDate when
    present; otherwise the F1 12:00-UTC fallback applies (per market_phase.py).
    """
    city_config = runtime_cities_by_name().get(city)
    tz = getattr(city_config, "timezone", None) if city_config is not None else None
    if not tz:
        return _market_phase_evidence.MarketPhaseEvidence(
            phase=None,
            phase_source="unknown",
            market_start_at=None,
            market_end_at=None,
            settlement_day_entry_utc=None,
            failure_reason=f"city_timezone_missing:{city}",
        )
    market = dict(selected_market_row) if selected_market_row else {}
    return _market_phase_evidence.from_market_dict(
        market=market,
        city_timezone=tz,
        target_date_str=str(target_date),
        decision_time_utc=decision_time.astimezone(UTC),
        uma_resolved_source=uma_resolved_source,
    )


def _forecast_only_phase_admits(evidence: "_market_phase_evidence.MarketPhaseEvidence") -> bool:
    """True iff the family may be admitted in forecast_only scope: ONLY when the
    whole target local day is still future (PRE_SETTLEMENT_DAY). Fail-closed."""
    return evidence.phase in _FORECAST_ONLY_ADMIT_PHASES


def build_event_bound_no_submit_receipt(
    event: OpportunityEvent,
    *,
    trade_conn: sqlite3.Connection,
    decision_time: datetime,
    get_current_level: Callable[[], RiskLevel],
    forecast_conn: sqlite3.Connection | None = None,
    topology_conn: sqlite3.Connection | None = None,
    calibration_conn: sqlite3.Connection | None = None,
    bankroll_usd_provider: Callable[[], float | None] | None = None,
    portfolio_state_provider: "Callable[[], Any] | None" = None,
    portfolio_reservation: "PortfolioReservationLedger | list[tuple[str, float]] | None" = None,
    locked_opportunity_conn: sqlite3.Connection | None = None,
    replacement_forecast_hook: Callable[["_CandidateProof", OpportunityEvent, datetime], ReplacementForecastReactorHookResult | None] | None = None,
    replacement_forecast_promotion_evidence: ReplacementForecastPromotionEvidence | None = None,
    replacement_forecast_capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None = None,
) -> EventSubmissionReceipt:
    """Produce a typed no-submit EDLI proof without running the cycle runner.

    Task #107 (portfolio/multi Kelly): ``portfolio_state_provider`` (mirrors
    ``bankroll_usd_provider``) supplies the current PortfolioState snapshot so
    Kelly sizes against the bankroll NET of correlation-weighted committed
    capital. ``portfolio_reservation`` is the per-cycle in-flight ledger
    (``PortfolioReservationLedger``, iterable as ``(city, usd)``); the builder
    reads it as ``extra_reserved`` for INV-K7 and PROVISIONALLY reserves this
    event's stake (``reserve``) so the next same-cycle event nets it. FIX B
    (2026-06-05): the reactor commits/rolls back that provisional reserve in its
    post-submit phase, so a candidate rejected downstream of Kelly never inflates
    later candidates. A plain ``list`` is still accepted (legacy append-only
    behavior). When either provider/ledger is None the sizing reduces EXACTLY to
    pre-#107 single-Kelly (no regression for unwired callers/tests)."""

    decision_time = decision_time.astimezone(UTC)
    payload = _payload(event)
    if forecast_conn is None:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="FORECAST_AUTHORITY_CONNECTION_MISSING")
    if topology_conn is None:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="TOPOLOGY_AUTHORITY_CONNECTION_MISSING")
    if calibration_conn is None:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="CALIBRATION_AUTHORITY_CONNECTION_MISSING")
    source_conn = forecast_conn
    topology_authority_conn = topology_conn
    family_topology_rows = _event_family_market_topology_rows(topology_authority_conn, payload)
    if not family_topology_rows:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="EVENT_BOUND_MARKET_TOPOLOGY_MISSING")
    family_condition_ids = tuple(str(row.get("condition_id") or "") for row in family_topology_rows)
    family_rows = _latest_snapshot_rows_for_event_family(
        trade_conn,
        event,
        condition_ids=family_condition_ids,
        fresh_at=decision_time,
        require_fresh=False,  # FDR proves family identity/completeness; price-freshness is enforced at submission
    )
    if not family_rows:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="EVENT_BOUND_EXECUTABLE_SNAPSHOT_MISSING")
    snapshot_token_maps = _snapshot_token_maps_by_condition(family_rows)
    # Full-family topology: the family domain is ALWAYS the complete market_events
    # partition (MECE, all bins). Bins WITH a snapshot are tradeable; bins WITHOUT a
    # snapshot are non-tradeable (executable_mask=False) but still carry their bin so
    # q/FDR are computed over the full MECE family. Removing any bin would renormalize
    # q over the subset (~1.2× inflation at 3/11 missing) and shrink fdr_hypothesis_count
    # from 22 to 16 — both unsafe. The selected-bin gate below handles the only fatal
    # case: the event's own target bin has no snapshot.
    #
    # For non-tradeable bins (no snapshot in executable_market_snapshots) we use the
    # market_events.token_id (YES token only) as identity; no_token_id is None. The
    # executable_mask in _generate_candidate_proofs already falls back to
    # executable_mask=False when native_costs has no price for a condition (line ~3877).
    try:
        topology = tuple(
            _topology_candidate_from_market_event(
                row,
                snapshot_token_maps.get(str(row.get("condition_id") or "")),
                payload,
            )
            for row in family_topology_rows
        )
    except ValueError as exc:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=f"EVENT_BOUND_MARKET_TOPOLOGY_INVALID:{exc}",
        )
    row = _selected_snapshot_row_for_event(family_rows, payload)
    if row is None:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="EVENT_BOUND_SELECTED_SNAPSHOT_MISSING")
    selected_stale_reason = _snapshot_price_stale_reason(row, decision_time=decision_time)
    if selected_stale_reason is not None:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=selected_stale_reason,
        )
    # Market-age gate for opening_inertia (EDLI path): restore legacy
    # MODE_PARAMS[OPENING_HUNT]["max_hours_since_open"] = 24 semantics.
    # Kelly's phase-aware multiplier sizes by opening-tick age; a mislabeled
    # 30-day-old market receives wrong sizing.  Conservative: missing age →
    # pass (do NOT reject without evidence).
    # Placed before the decision engine so it fires even when forecast fields
    # are absent (the decision engine would return NO_TRADE in those cases, but
    # the age reason is more informative and fires cheaply first).
    if event.event_type == "FORECAST_SNAPSHOT_READY" and str(payload.get("direction") or "").strip().lower() == "buy_no":
        _oi_age_hours = _opening_inertia_market_age_hours(
            snapshot_row=row,
            topology_rows=family_topology_rows,
            family_rows=family_rows,
            decision_time=decision_time,
        )
        if _oi_age_hours is not None and _oi_age_hours >= 24.0:
            return EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason=f"OPENING_INERTIA_MARKET_TOO_OLD:{_oi_age_hours:.1f}h",
                city=str(payload.get("city") or ""),
                target_date=str(payload.get("target_date") or ""),
                metric=str(payload.get("metric") or payload.get("temperature_metric") or ""),
                source_status="MATCH",
                family_complete=True,
            )
    decision = EventBoundDecisionEngine().evaluate(
        EventBoundDecisionRequest(
            event=event,
            market_topology=topology,
            decision_time=decision_time,
            market_topology_source="executable_market_snapshots",
        )
    )
    if decision.status != "CANDIDATE_FAMILY_READY" or decision.candidate_family is None:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=decision.rejection_reason or "EVENT_BOUND_CANDIDATE_BINDING_FAILED",
        )
    family = decision.candidate_family
    # forecast_only market-phase admission gate (#98): reject families whose
    # target local day has begun or whose market has closed — forecast_only is
    # blind to the already-realizing/observed extremum (wrong-side risk, Paris
    # 2026-06-01). Scoped to FORECAST_SNAPSHOT_READY; the day0 observation-aware
    # scope owns same-day. Placed before scoring so closed families never reach
    # q/FDR/Kelly and never re-fire through continuous re-decision.
    if event.event_type == "FORECAST_SNAPSHOT_READY":
        _phase_evidence = _edli_forecast_only_phase_evidence(
            city=family.city,
            target_date=family.target_date,
            decision_time=decision_time,
            selected_market_row=row,
        )
        if not _forecast_only_phase_admits(_phase_evidence):
            return EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason=(
                    "EVENT_BOUND_MARKET_PHASE_CLOSED:"
                    f"{(_phase_evidence.phase.value if _phase_evidence.phase else 'unknown')}:"
                    f"{_phase_evidence.phase_source}"
                ),
                city=family.city,
                target_date=family.target_date,
                metric=family.metric,
                family_id=family.family_id,
                source_status="MATCH",
                family_complete=True,
            )
    try:
        proofs = _generate_candidate_proofs(
            event=event,
            payload=payload,
            family=family,
            snapshot_rows=family_rows,
            trade_conn=trade_conn,
            forecast_conn=source_conn,
            calibration_conn=calibration_conn,
            decision_time=decision_time,
            promotion_evidence=replacement_forecast_promotion_evidence,
            capital_objective_evidence=replacement_forecast_capital_objective_evidence,
        )
    except ValueError as exc:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=f"LIVE_INFERENCE_INPUTS_MISSING:{exc}",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            family_id=family.family_id,
            source_status="MATCH",
            family_complete=True,
        )
    # S4 (operator directive 2026-06-08): the pre-selection scalar-Kelly sizing
    # (_candidate_selection_kelly_size_usd_by_id) is RETIRED. Exposure-aware sizing
    # now comes from RobustCandidateScore.optimal_stake_usd inside the marginal-
    # utility ranker (the size authority, computed post-selection on the WINNING
    # leg with the family payoff matrix + existing exposure). The opportunity_book
    # per-candidate kelly_size_usd is a display field only; it is no longer fed by a
    # parallel scalar-Kelly pass (one sizing surface, no shadow).
    proof = _selected_candidate_proof(
        payload,
        proofs,
        locked_opportunity_conn=locked_opportunity_conn,
    )
    opportunity_book = _opportunity_book_from_proofs(
        event_id=event.event_id,
        family_id=family.family_id,
        proofs=proofs,
        selected_proof=proof,
        locked_opportunity_conn=locked_opportunity_conn,
    )
    if proof is None:
        # MAJOR2 fix (#135): when ALL candidates fail the mainstream-agreement gate,
        # persist the best-scoring family's mainstream verdict on the MISSING receipt so
        # demotions are auditable (not an invisible hole). Pull from payload verdicts
        # dict; attach the verdict for the highest trade_score proof that was evaluated.
        _missing_mav_fields: dict[str, object] = {}
        _all_verdicts: dict[tuple[str, str], dict] = payload.get("_mainstream_agreement_verdicts", {})  # type: ignore[assignment]
        if _all_verdicts:
            best_proof = max(proofs, key=lambda p: p.trade_score, default=None)
            if best_proof is not None:
                _best_v = _all_verdicts.get(
                    (str(best_proof.candidate.condition_id or ""), best_proof.direction)
                )
                if _best_v is not None:
                    _missing_mav_fields = {
                        "mainstream_agreement_pass": _best_v.get("mainstream_agreement_pass"),
                        "mainstream_agreement_fail_reason": _best_v.get("mainstream_agreement_fail_reason"),
                        "mainstream_point": _optional_float(_best_v.get("mainstream_point")),
                        "mainstream_delta": _optional_float(_best_v.get("forecast_delta")),
                        "mainstream_bin_label": _best_v.get("mainstream_bin_label"),
                        "mainstream_source": _best_v.get("mainstream_source"),
                        "mainstream_fetched_at_utc": _best_v.get("mainstream_fetched_at_utc"),
                    }
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason="EVENT_BOUND_SELECTED_CANDIDATE_MISSING",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            family_id=family.family_id,
            source_status="MATCH",
            family_complete=True,
            **_missing_mav_fields,  # type: ignore[arg-type]
        )
    candidate = proof.candidate
    selected_token_id = proof.token_id
    direction = proof.direction
    execution_price = proof.execution_price
    if execution_price is None:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=f"EXECUTABLE_NATIVE_ASK_MISSING:{proof.missing_reason or 'native executable quote unavailable'}",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            condition_id=str(candidate.condition_id or ""),
            token_id=selected_token_id,
            executable_snapshot_id=proof.executable_snapshot_id,
            family_id=family.family_id,
            bin_label=candidate.bin.label,
            direction=direction,
            q_live=proof.q_posterior,
            q_lcb_5pct=proof.q_lcb_5pct,
            c_fee_adjusted=None,
            c_cost_95pct=proof.c_cost_95pct,
            p_fill_lcb=proof.p_fill_lcb,
            trade_score=proof.trade_score,
            native_quote_available=False,
            source_status="MATCH",
            family_complete=True,
        )
    untradeable_limit_reason = _candidate_limit_price_untradeable_reason(proof)
    if untradeable_limit_reason is not None:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=untradeable_limit_reason,
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            condition_id=str(candidate.condition_id or ""),
            token_id=selected_token_id,
            executable_snapshot_id=proof.executable_snapshot_id,
            family_id=family.family_id,
            bin_label=candidate.bin.label,
            direction=direction,
            q_live=proof.q_posterior,
            q_lcb_5pct=proof.q_lcb_5pct,
            c_fee_adjusted=execution_price.value,
            c_cost_95pct=proof.c_cost_95pct,
            p_fill_lcb=proof.p_fill_lcb,
            trade_score=proof.trade_score,
            native_quote_available=True,
            source_status="MATCH",
            family_complete=True,
        )
    replacement_forecast_receipt_tag: dict[str, Any] | None = None
    if replacement_forecast_hook is not None and not _replacement_primary_authority_already_applied(proof):
        replacement_hook_result = replacement_forecast_hook(proof, event, decision_time)
        if replacement_hook_result is not None:
            if replacement_hook_result.status == "BLOCKED":
                replacement_forecast_receipt_tag = replacement_hook_result.as_receipt_tag()
                return EventSubmissionReceipt(
                    False,
                    event.event_id,
                    event.causal_snapshot_id,
                    reason="REPLACEMENT_FORECAST_HOOK_BLOCKED:" + ",".join(replacement_hook_result.reason_codes),
                    city=family.city,
                    target_date=family.target_date,
                    metric=family.metric,
                    condition_id=str(candidate.condition_id or ""),
                    token_id=selected_token_id,
                    executable_snapshot_id=proof.executable_snapshot_id,
                    family_id=family.family_id,
                    bin_label=candidate.bin.label,
                    direction=direction,
                    q_live=proof.q_posterior,
                    q_lcb_5pct=proof.q_lcb_5pct,
                    c_fee_adjusted=execution_price.value,
                    c_cost_95pct=proof.c_cost_95pct,
                    p_fill_lcb=proof.p_fill_lcb,
                    trade_score=proof.trade_score,
                    native_quote_available=True,
                    source_status="MATCH",
                    family_complete=True,
                    replacement_forecast=replacement_forecast_receipt_tag,
                )
            if replacement_hook_result.status == "SHADOW_VETO_ONLY":
                replacement_forecast_receipt_tag = replacement_hook_result.as_receipt_tag()
                if replacement_hook_result.effective_direction != direction:
                    return EventSubmissionReceipt(
                        False,
                        event.event_id,
                        event.causal_snapshot_id,
                        reason="REPLACEMENT_FORECAST_HOOK_DIRECTION_FLIP",
                        city=family.city,
                        target_date=family.target_date,
                        metric=family.metric,
                        condition_id=str(candidate.condition_id or ""),
                        token_id=selected_token_id,
                        executable_snapshot_id=proof.executable_snapshot_id,
                        family_id=family.family_id,
                        bin_label=candidate.bin.label,
                        direction=direction,
                        q_live=proof.q_posterior,
                        q_lcb_5pct=proof.q_lcb_5pct,
                        c_fee_adjusted=execution_price.value,
                        c_cost_95pct=proof.c_cost_95pct,
                        p_fill_lcb=proof.p_fill_lcb,
                        trade_score=proof.trade_score,
                        native_quote_available=True,
                        source_status="MATCH",
                        family_complete=True,
                        replacement_forecast=replacement_forecast_receipt_tag,
                    )
                effective_q_lcb = min(proof.q_lcb_5pct, replacement_hook_result.effective_q_lcb)
                effective_trade_score = _robust_trade_score_from_generated_inputs(
                    q_posterior=proof.q_posterior,
                    q_lcb_5pct=effective_q_lcb,
                    execution_price=execution_price,
                    c_cost_95pct=proof.c_cost_95pct,
                    p_fill_lcb=proof.p_fill_lcb,
                )
                proof = dataclass_replace(
                    proof,
                    q_lcb_5pct=effective_q_lcb,
                    trade_score=min(proof.trade_score, effective_trade_score),
                )
            elif replacement_hook_result.status == "LIVE_AUTHORITY":
                replacement_forecast_receipt_tag = replacement_hook_result.as_receipt_tag()
                effective_proof = _replacement_live_authority_proof_for_direction(
                    proofs=proofs,
                    baseline_proof=proof,
                    effective_direction=replacement_hook_result.effective_direction,
                )
                if effective_proof is None:
                    return EventSubmissionReceipt(
                        False,
                        event.event_id,
                        event.causal_snapshot_id,
                        reason="REPLACEMENT_FORECAST_LIVE_DIRECTION_PROOF_MISSING",
                        city=family.city,
                        target_date=family.target_date,
                        metric=family.metric,
                        condition_id=str(candidate.condition_id or ""),
                        token_id=selected_token_id,
                        executable_snapshot_id=proof.executable_snapshot_id,
                        family_id=family.family_id,
                        bin_label=candidate.bin.label,
                        direction=direction,
                        q_live=proof.q_posterior,
                        q_lcb_5pct=proof.q_lcb_5pct,
                        c_fee_adjusted=execution_price.value,
                        c_cost_95pct=proof.c_cost_95pct,
                        p_fill_lcb=proof.p_fill_lcb,
                        trade_score=proof.trade_score,
                        native_quote_available=True,
                        source_status="MATCH",
                        family_complete=True,
                        replacement_forecast=replacement_forecast_receipt_tag,
                    )
                proof = effective_proof
                candidate = proof.candidate
                selected_token_id = proof.token_id
                direction = proof.direction
                execution_price = proof.execution_price
                row = proof.row
                opportunity_book = _opportunity_book_from_proofs(
                    event_id=event.event_id,
                    family_id=family.family_id,
                    proofs=proofs,
                    selected_proof=proof,
                    locked_opportunity_conn=locked_opportunity_conn,
                )
                if execution_price is None or row is None:
                    return EventSubmissionReceipt(
                        False,
                        event.event_id,
                        event.causal_snapshot_id,
                        reason="REPLACEMENT_FORECAST_LIVE_EXECUTABLE_PROOF_MISSING",
                        city=family.city,
                        target_date=family.target_date,
                        metric=family.metric,
                        condition_id=str(candidate.condition_id or ""),
                        token_id=selected_token_id,
                        executable_snapshot_id=proof.executable_snapshot_id,
                        family_id=family.family_id,
                        bin_label=candidate.bin.label,
                        direction=direction,
                        q_live=proof.q_posterior,
                        q_lcb_5pct=proof.q_lcb_5pct,
                        c_fee_adjusted=None,
                        c_cost_95pct=proof.c_cost_95pct,
                        p_fill_lcb=proof.p_fill_lcb,
                        trade_score=proof.trade_score,
                        native_quote_available=False,
                        source_status="MATCH",
                        family_complete=True,
                        replacement_forecast=replacement_forecast_receipt_tag,
                    )
                effective_q_posterior = replacement_hook_result.effective_q_posterior
                effective_q_lcb = replacement_hook_result.effective_q_lcb
                effective_trade_score = _robust_trade_score_from_generated_inputs(
                    q_posterior=effective_q_posterior,
                    q_lcb_5pct=effective_q_lcb,
                    execution_price=execution_price,
                    c_cost_95pct=proof.c_cost_95pct,
                    p_fill_lcb=proof.p_fill_lcb,
                )
                proof = dataclass_replace(
                    proof,
                    q_posterior=effective_q_posterior,
                    q_lcb_5pct=effective_q_lcb,
                    trade_score=effective_trade_score,
                )
            elif replacement_hook_result.status == "SHADOW_ONLY":
                replacement_forecast_receipt_tag = replacement_hook_result.as_receipt_tag()
            elif replacement_hook_result.status != "DISABLED":
                replacement_forecast_receipt_tag = replacement_hook_result.as_receipt_tag()
                return EventSubmissionReceipt(
                    False,
                    event.event_id,
                    event.causal_snapshot_id,
                    reason=f"REPLACEMENT_FORECAST_HOOK_UNSUPPORTED:{replacement_hook_result.status}",
                    city=family.city,
                    target_date=family.target_date,
                    metric=family.metric,
                    condition_id=str(candidate.condition_id or ""),
                    token_id=selected_token_id,
                    executable_snapshot_id=proof.executable_snapshot_id,
                    family_id=family.family_id,
                    bin_label=candidate.bin.label,
                    direction=direction,
                    q_live=proof.q_posterior,
                    q_lcb_5pct=proof.q_lcb_5pct,
                    c_fee_adjusted=execution_price.value,
                    c_cost_95pct=proof.c_cost_95pct,
                    p_fill_lcb=proof.p_fill_lcb,
                    trade_score=proof.trade_score,
                    native_quote_available=True,
                    source_status="MATCH",
                    family_complete=True,
                    replacement_forecast=replacement_forecast_receipt_tag,
                )
    trade_score = proof.trade_score
    if trade_score <= 0.0:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason="TRADE_SCORE_NON_POSITIVE",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            condition_id=str(candidate.condition_id or ""),
            token_id=selected_token_id,
            executable_snapshot_id=proof.executable_snapshot_id,
            family_id=family.family_id,
            bin_label=candidate.bin.label,
            direction=direction,
            q_live=proof.q_posterior,
            q_lcb_5pct=proof.q_lcb_5pct,
            c_fee_adjusted=execution_price.value,
            c_cost_95pct=proof.c_cost_95pct,
            p_fill_lcb=proof.p_fill_lcb,
            trade_score=trade_score,
            native_quote_available=True,
            source_status="MATCH",
            family_complete=True,
        )
    hypothesis_id = f"{family.family_id}:{selected_token_id}"
    try:
        fdr = evaluate_fdr_full_family(
            family_id=family.family_id,
            all_hypothesis_ids=tuple(
                f"{family.family_id}:{token}" for token in family.yes_token_ids + family.no_token_ids
            ),
            selected_hypothesis_ids=(hypothesis_id,),
            hypothesis_p_values={f"{family.family_id}:{candidate.token_id}": candidate.p_value for candidate in proofs},
            passed_prefilter={
                f"{family.family_id}:{candidate.token_id}": candidate.passed_prefilter for candidate in proofs
            },
        )
    except (TypeError, ValueError) as exc:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=f"FDR_FULL_FAMILY_PROOF_MISSING:{exc}",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            condition_id=str(candidate.condition_id or ""),
            token_id=selected_token_id,
            executable_snapshot_id=proof.executable_snapshot_id,
            family_id=family.family_id,
            bin_label=candidate.bin.label,
            direction=direction,
            q_live=proof.q_posterior,
            q_lcb_5pct=proof.q_lcb_5pct,
            c_fee_adjusted=execution_price.value,
            c_cost_95pct=proof.c_cost_95pct,
            p_fill_lcb=proof.p_fill_lcb,
            trade_score=trade_score,
            native_quote_available=True,
            source_status="MATCH",
            family_complete=False,
        )
    if not fdr.passed:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason="FDR_REJECTED",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            condition_id=str(candidate.condition_id or ""),
            token_id=selected_token_id,
            executable_snapshot_id=proof.executable_snapshot_id,
            family_id=family.family_id,
            bin_label=candidate.bin.label,
            direction=direction,
            q_live=proof.q_posterior,
            q_lcb_5pct=proof.q_lcb_5pct,
            c_fee_adjusted=execution_price.value,
            c_cost_95pct=proof.c_cost_95pct,
            p_fill_lcb=proof.p_fill_lcb,
            trade_score=trade_score,
            native_quote_available=True,
            source_status="MATCH",
            family_complete=True,
            fdr_pass=False,
            fdr_family_id=fdr.fdr_family_id,
            fdr_hypothesis_count=fdr.attempted_hypotheses,
        )
    kelly_cost_basis_id = f"edli_cost:{event.event_id}:{selected_token_id}"
    try:
        bankroll_usd = (
            _bankroll_usd_from_provider(bankroll_usd_provider)
            if bankroll_usd_provider is not None
            else _runtime_bankroll_usd(cached_only=True)
        )
        kelly_multiplier = _runtime_kelly_multiplier()
        (
            kelly_multiplier,
            _bias_decay_applied,
            _bias_decay_native,
            _bias_decay_reason,
        ) = _maybe_bias_decay_kelly_haircut(
            kelly_multiplier,
            family=family,
            q_source=proof.q_source,
        )
        # S3 (variance-required Kelly, task #103/#111): carry the candidate's
        # posterior CI width and forecast lead into Kelly so a wider-CI edge
        # sizes STRICTLY smaller. The config/bias-decay multiplier above is the
        # BASE; SizingContext adds the CI/lead variance haircut on top. lead_days
        # derivation can raise ValueError (CALIBRATION_AUTHORITY_MISSING) — that
        # routes through the except envelope below to KELLY_PROOF_MISSING
        # fail-closed, never silent.
        _lead_days = _snapshot_lead_days(snapshot=row, family=family, payload=payload)
        # Task #107 (portfolio/multi Kelly): when a PortfolioState provider is
        # wired, carry correlation-weighted and raw committed capital into the
        # Kelly context as SOFT marginal pressure inputs. They shrink the next
        # multiplier but do not subtract from a global budget and hard-zero a
        # positive-edge candidate. The reservation accumulator (closure-held in
        # the adapter factory) carries this cycle's already-emitted-but-unfilled
        # stakes (INV-K7). When no provider is wired (back-compat / tests), fall
        # back to the #103 3-arg context.
        if portfolio_state_provider is not None:
            from src.state.portfolio import correlated_committed_usd, total_exposure_usd

            _portfolio_state = portfolio_state_provider()
            _corr_committed_usd = correlated_committed_usd(
                _portfolio_state,
                new_city=family.city,
                extra_reserved=(
                    list(portfolio_reservation)
                    if portfolio_reservation is not None
                    else None
                ),
            )
            # Raw exposure pressure: total cash deployed across all open
            # positions (no corr weighting) + same-cycle reservation usd. This
            # is not a hard portfolio cap; evaluate_kelly turns it into a
            # continuous marginal Kelly haircut.
            _raw_committed_usd = total_exposure_usd(_portfolio_state) + sum(
                float(usd)
                for _, usd in (portfolio_reservation or [])
            )
            sizing_context = SizingContext.from_candidate_proof_with_portfolio(
                q_posterior=proof.q_posterior,
                q_lcb_5pct=proof.q_lcb_5pct,
                lead_days=_lead_days,
                bankroll_usd=bankroll_usd,
                corr_committed_usd=_corr_committed_usd,
                raw_committed_usd=_raw_committed_usd,
            )
        else:
            sizing_context = SizingContext.from_candidate_proof(
                q_posterior=proof.q_posterior,
                q_lcb_5pct=proof.q_lcb_5pct,
                lead_days=_lead_days,
            )
        kelly = evaluate_kelly(
            kelly_decision_id=f"edli_kelly:{event.event_id}:{selected_token_id}",
            p_posterior=proof.q_posterior,
            execution_price=execution_price,
            bankroll_usd=bankroll_usd,
            sizing_context=sizing_context,
            kelly_multiplier=kelly_multiplier,
        )
        # ROBUST-LOWER-BOUND SIZING + CHOSEN-STAKE PRICE (S4+S5; spec §3/§5.2/§5.3/
        # §14.7/§14.10, money-path iron law).
        # ``evaluate_kelly`` is NO LONGER the size authority: it sizes on
        # ``p_posterior`` (q_point) via the scalar binary-Kelly formula. The
        # bin-selection size authority is the marginal-utility ranker's
        # ``RobustCandidateScore.optimal_stake_usd`` — the log-optimal stake on the
        # candidate's OWN robust q_lcb-based π against the family payoff matrix AND
        # the EXISTING per-outcome exposure (Hidden #10), scaled by the FRACTIONAL-
        # Kelly haircut ``evaluate_kelly`` already derived (CI-width / lead /
        # portfolio-heat — variance is NOT dropped). We keep ``evaluate_kelly`` for
        # the typed-price assert, the receipt fields, and that haircut multiplier;
        # we OVERRIDE the size with the q_lcb-grounded ΔU stake.
        #
        # S5 (operator directive 2026-06-08): we ALSO override ``execution_price``
        # with the CHOSEN-STAKE boundary — ``ExecutableCostCurve.avg_cost(optimal_
        # stake)`` on the selected leg's OWN native curve (typed, fee-deducted,
        # probability_units; passes ``assert_kelly_safe``). Scalar Kelly on the
        # cheap min-order top-ask over-bets into thin levels (Hidden #6); the cost-
        # curve optimizer in ``score_candidate`` already maximized ΔU over the
        # feasible depth-bounded stake interval, so the Kelly cost-of-entry the
        # intent + receipt carry MUST be that same curve at that same stake — the
        # depth-walked cost the order actually pays, not S1's top-of-book scalar.
        _fractional_kelly_mult = float(
            kelly.effective_multiplier
            if kelly.effective_multiplier is not None
            else kelly_multiplier
        )
        # S6 (operator directive 2026-06-08; spec §5 submit pseudocode / §7 / §14.9 /
        # §14.10). THE single fail-closed submit-recapture gate — recompute, not
        # validate, at the no-submit receipt boundary. This call RECOMPUTES the
        # selected leg's fresh ExecutableCostCurve, the chosen fractional-Kelly stake
        # + chosen-stake price on that fresh curve (the S5 kernel, internally), the
        # robust q_lcb, and the family rank, then routes ALL of it through ONE
        # RedecisionEngine.evaluate_submit_recapture. It REPLACES the former scattered
        # inline re-gate (the ``not kelly.passed`` size check that implicitly decided
        # submit eligibility): the three abort branches (price-through-max, edge<=0 /
        # forecast-stale, family-rank reversed) are now first-class lifecycle states.
        # The intent is built ONLY when ``_recapture.may_submit`` is True — no parallel
        # branch, no flag, no shadow.
        _recapture_exposure = _family_existing_exposure_by_bin_id(
            proofs=proofs,
            selected_proof=proof,
            portfolio_state_provider=portfolio_state_provider,
            portfolio_reservation=portfolio_reservation,
            family=family,
        )
        # Stake-floor provenance collector (2026-06-09 min-order fix). The recapture
        # kernel writes stake_floor="VENUE_MIN_ORDER" here when it bumps a positive-edge
        # candidate's haircut stake up to the venue min order, so the submit receipt
        # records WHY the stake equals the venue floor (the fractional-Kelly risk intent
        # was preserved — min order is << the bankroll cap). Empty on the normal path.
        _stake_floor_provenance: dict[str, object] = {}
        # Maker/taker fill semantics for the S6 PRICE_MOVED ceiling (2026-06-10).
        # A resting maker order rests at the admitted limit and never chases the
        # recaptured ask, so the price-moved ceiling must NOT abort it (it was
        # producing the live sub-3¢ false-abort churn). A taker order crosses and
        # pays the recaptured cost, so the bounded tolerance ceiling still governs.
        # Mirrors the downstream order-mode authority; the taker no-chase bound lives
        # at intent build (TOUCH_EXCEEDS_RESERVATION), so this never relaxes a real cross.
        _order_rests_at_admitted_price = _order_will_rest_at_admitted_price(payload)
        _recapture, _robust_stake_usd, _chosen_stake_price = (
            _evaluate_submit_recapture_for_selected(
                family_key=str(family.family_id or ""),
                selected_proof=proof,
                all_proofs=proofs,
                extra_exposure_by_bin_id=_recapture_exposure,
                bankroll_usd=float(bankroll_usd),
                kelly_multiplier=_fractional_kelly_mult,
                order_rests_at_admitted_price=_order_rests_at_admitted_price,
                # On this synchronous path the forecast snapshot was validated by the
                # decision engine and (if present) the replacement-forecast hook, both
                # of which already returned a no-submit receipt on any flip/block
                # above — so the proof carried here is forecast-current at recapture.
                forecast_still_current=True,
                # S6 scope-set invariant: the recapture family re-rank must scope the
                # candidate set EXACTLY as selection did (`_selected_candidate_proof` was
                # called with this same conn at selection) — else a locked / below-min-tick
                # leg scoped OUT of selection falsely reverses the chosen leg.
                locked_opportunity_conn=locked_opportunity_conn,
                stake_floor_out=_stake_floor_provenance,
            )
        )
        kelly = dataclass_replace(
            kelly,
            size_usd=float(_robust_stake_usd),
            passed=bool(_recapture.may_submit),
        )
        # Rebind the Kelly cost-of-entry to the chosen-stake boundary when the
        # recapture cleared (so the intent's ``execution_price`` / the receipt's
        # ``c_fee_adjusted`` / the executor limit reflect the depth walk). On any abort
        # the stake is 0.0 / price is None and we leave ``execution_price`` as the S1
        # boundary — the abort receipt below reports it for audit and builds no intent.
        if _recapture.may_submit and _chosen_stake_price is not None:
            execution_price = _chosen_stake_price
    except (TypeError, ValueError) as exc:
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=f"KELLY_PROOF_MISSING:{exc}",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            condition_id=str(candidate.condition_id or ""),
            token_id=selected_token_id,
            executable_snapshot_id=proof.executable_snapshot_id,
            family_id=family.family_id,
            bin_label=candidate.bin.label,
            direction=direction,
            q_live=proof.q_posterior,
            q_lcb_5pct=proof.q_lcb_5pct,
            c_fee_adjusted=execution_price.value,
            c_cost_95pct=proof.c_cost_95pct,
            p_fill_lcb=proof.p_fill_lcb,
            trade_score=trade_score,
            native_quote_available=True,
            source_status="MATCH",
            family_complete=True,
        )
    if not _recapture.may_submit:
        # S6: the submit recapture aborted. The receipt reason is DERIVED from the
        # engine's terminal lifecycle state (SUBMIT_ABORTED_PRICE_MOVED /
        # _EDGE_REVERSED / _FAMILY_REVERSED) — one taxonomy, never set independently of
        # the state machine. ``detail`` carries the human-readable trigger; the abort
        # state is in SUBMIT_ABORT_STATES (assertable). No intent is built (fail-closed,
        # spec §5/§7/§13: price-through-max, edge<=0/forecast-stale, or rank reversed
        # without a full re-rank).
        assert _recapture.state in SUBMIT_ABORT_STATES, (
            f"recapture aborted but state {_recapture.state} is not a submit-abort "
            f"state — the gate must land in a first-class abort state (§7)"
        )
        _abort_reason = _SUBMIT_ABORT_RECEIPT_REASON[_recapture.state]
        _abort_detail = _recapture.detail or _abort_reason
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=f"{_abort_reason}:{_abort_detail}",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            condition_id=str(candidate.condition_id or ""),
            token_id=selected_token_id,
            executable_snapshot_id=proof.executable_snapshot_id,
            family_id=family.family_id,
            bin_label=candidate.bin.label,
            direction=direction,
            q_live=proof.q_posterior,
            q_lcb_5pct=proof.q_lcb_5pct,
            c_fee_adjusted=execution_price.value,
            c_cost_95pct=proof.c_cost_95pct,
            p_fill_lcb=proof.p_fill_lcb,
            trade_score=trade_score,
            native_quote_available=True,
            source_status="MATCH",
            family_complete=True,
            fdr_pass=True,
            fdr_family_id=fdr.fdr_family_id,
            fdr_hypothesis_count=fdr.attempted_hypotheses,
            kelly_pass=False,
            kelly_execution_price_type=execution_price.__class__.__name__,
            kelly_price_fee_deducted=execution_price.fee_deducted,
            kelly_size_usd=kelly.size_usd,
            kelly_cost_basis_id=kelly_cost_basis_id,
        )
    risk = evaluate_riskguard(
        risk_decision_id=f"edli_risk:{event.event_id}:{selected_token_id}",
        level=get_current_level(),
    )
    if not risk.passed:
        return EventSubmissionReceipt(False, event.event_id, event.causal_snapshot_id, reason="RISK_GUARD_BLOCKED")
    # Task #107 INV-K7 (same-cycle in-flight reservation): this bet has now
    # passed Kelly + RiskGuard. Reserve its stake in the per-cycle ledger so the
    # NEXT event in this reactor cycle nets it — a just-emitted EDLI entry is
    # PENDING_TRACKED without fill authority, so its effective_cost_basis_usd is
    # 0.0 and it is invisible to the portfolio snapshot until reconciled. Without
    # this, two same-cycle bets would both size against the full (un-netted)
    # budget and breach it intra-cycle.
    #
    # FIX B (P1 zero-submit co-cause, 2026-06-05): the reservation is now
    # PROVISIONAL. Passing Kelly + RiskGuard is NOT emission — the receipt can
    # still be REJECTED downstream at DECISION_CERTIFICATE / EXECUTOR_
    # EXPRESSIBILITY in the reactor's post-submit phase. The reactor finalizes
    # this provisional reserve (commit on emit / rollback on reject) BEFORE the
    # next sequential event reads the ledger, so a rejected candidate never
    # inflates corr_committed_usd / raw_committed_usd for later same-cycle
    # candidates. (Plain lists supplied by legacy/test callers keep the old
    # append-only behavior via the shim below.)
    if portfolio_reservation is not None:
        _reserve = getattr(portfolio_reservation, "reserve", None)
        if callable(_reserve):
            _reserve(event.event_id, family.city, float(kelly.size_usd))
        else:
            portfolio_reservation.append((family.city, float(kelly.size_usd)))
    intent = EventBoundFinalIntent(
        final_intent_id=f"edli_intent:{event.event_id}:{selected_token_id}",
        event_id=event.event_id,
        family_id=family.family_id,
        candidate_id=f"{family.family_id}:{candidate.condition_id}",
        condition_id=str(candidate.condition_id or ""),
        token_id=selected_token_id,
        direction=direction,
        executable_snapshot_id=str(proof.executable_snapshot_id or ""),
        execution_price=execution_price,
    )
    typed_receipt = build_event_bound_final_intent_receipt(
        intent=intent,
        causal_snapshot_id=str(event.causal_snapshot_id or ""),
        trade_score_id=f"edli_trade_score:{event.event_id}:{selected_token_id}",
        fdr_family_id=fdr.fdr_family_id,
        kelly_decision_id=kelly.kelly_decision_id,
        risk_decision_id=risk.risk_decision_id,
        live_submit_enabled=False,
    )
    raw_receipt = serialize_event_bound_final_intent_receipt(
        typed_receipt,
        trade_score_positive=True,
        fdr_pass=fdr.passed,
        fdr_hypothesis_count=fdr.attempted_hypotheses,
        kelly_pass=kelly.passed,
        kelly_size_usd=kelly.size_usd,
        kelly_cost_basis_id=kelly_cost_basis_id,
    )
    raw_receipt.update(
        {
            "city": family.city,
            "target_date": family.target_date,
            "metric": family.metric,
            "strategy_key": _event_bound_strategy_key(
                event_type=event.event_type,
                direction=direction,
                metric=family.metric,
            ),
            "bin_label": candidate.bin.label,
            "unit": getattr(candidate.bin, "unit", None),
            "outcome_label": "NO" if selected_token_id == candidate.no_token_id else "YES",
            "q_live": proof.q_posterior,
            "q_lcb_5pct": proof.q_lcb_5pct,
            "q_lcb_calibration_source": proof.q_lcb_calibration_source,
            "q_source": proof.q_source,  # #120 calibrator provenance
            # H2_E2E: typed posterior link carried to the receipt (None on canonical).
            "posterior_id": proof.posterior_id,
            "probability_authority": proof.probability_authority,
            "c_fee_adjusted": execution_price.value,
            "c_cost_95pct": proof.c_cost_95pct,
            "p_fill_lcb": proof.p_fill_lcb,
            "trade_score": trade_score,
            "bias_decay_applied": bool(_bias_decay_applied),
            "bias_decay_bias_native": _bias_decay_native,
            "bias_decay_reason": _bias_decay_reason,
            "bias_decay_kelly_factor": float(settings["edli_v1"].get("bias_decay_kelly_factor", 0.5)) if _bias_decay_applied else 1.0,
            "neg_risk": bool(row.get("neg_risk") or False),
            "native_quote_available": True,
            "source_status": FORECAST_LIVE_ELIGIBLE_STATUS,
            "family_complete": True,
        }
    )
    # Stake-floor provenance (2026-06-09 min-order fix): record when the chosen stake
    # was bumped up to the venue min order because the fractional-Kelly haircut shrank
    # it below the floor while the robust edge at min order was strictly positive. Absent
    # on the normal (un-bumped) path. Keeps the receipt honest about why size==min order.
    if _stake_floor_provenance.get("stake_floor"):
        raw_receipt["stake_floor"] = _stake_floor_provenance["stake_floor"]
        if "stake_floor_min_order_usd" in _stake_floor_provenance:
            raw_receipt["stake_floor_min_order_usd"] = _stake_floor_provenance[
                "stake_floor_min_order_usd"
            ]
        if "stake_floor_delta_u_at_min_order" in _stake_floor_provenance:
            raw_receipt["stake_floor_delta_u_at_min_order"] = _stake_floor_provenance[
                "stake_floor_delta_u_at_min_order"
            ]
    # Price-move tolerance provenance (2026-06-10). When the recapture priced
    # STRICTLY WORSE than the admitted ceiling yet the entry proceeded — the maker
    # order rested at the admitted price, or a taker filled within the bounded
    # tolerance — record admitted vs recaptured so settlement attribution can measure
    # whether tolerated/rested entries underperform vs assumption (fill-rate, slippage).
    # Absent on the clean no-move path (receipt stays clean).
    if _recapture.price_moved_within_tolerance:
        raw_receipt["price_moved_within_tolerance"] = True
        raw_receipt["price_move_admitted_price"] = _recapture.admitted_price
        raw_receipt["price_move_recaptured_cost"] = _recapture.recaptured_all_in_cost
        raw_receipt["price_move_tolerance"] = _recapture.price_move_tolerance
        raw_receipt["price_move_order_rests_at_admitted"] = bool(
            _order_rests_at_admitted_price
        )
    if opportunity_book is not None:
        raw_receipt["opportunity_book"] = opportunity_book.to_receipt_dict()
    if replacement_forecast_receipt_tag is not None:
        raw_receipt["replacement_forecast"] = replacement_forecast_receipt_tag
    # Mainstream-agreement gate fields (#135). Added when the verdict is available on the
    # selected proof; absent otherwise (gate OFF or evaluation error — receipt stays clean).
    if proof.mainstream_agreement is not None:
        _mav = proof.mainstream_agreement
        raw_receipt.update(
            {
                "mainstream_agreement_pass": _mav.get("mainstream_agreement_pass"),
                "mainstream_agreement_fail_reason": _mav.get("mainstream_agreement_fail_reason"),
                "mainstream_point": _mav.get("mainstream_point"),
                "mainstream_delta": _mav.get("forecast_delta"),
                "mainstream_bin_label": _mav.get("mainstream_bin_label"),
                "mainstream_source": _mav.get("mainstream_source"),
                "mainstream_fetched_at_utc": _mav.get("mainstream_fetched_at_utc"),
            }
        )
    try:
        proof_bundle = _build_no_submit_proof_bundle_from_adapter_evidence(
            event=event,
            payload=payload,
            decision_time=decision_time,
            family=family,
            family_topology_rows=family_topology_rows,
            family_snapshot_rows=family_rows,
            selected_snapshot_row=row,
            trade_conn=trade_conn,
            forecast_conn=source_conn,
            calibration_conn=calibration_conn,
            proof=proof,
            raw_receipt=raw_receipt,
            fdr=fdr,
            kelly=kelly,
            risk=risk,
            bankroll_usd=bankroll_usd,
            kelly_multiplier=kelly_multiplier,
        )
    except ValueError as exc:
        missing_reason = str(exc)
        if not missing_reason.startswith("CALIBRATION_AUTHORITY_EVIDENCE_MISSING:"):
            raise
        missing_reason = missing_reason.replace(
            "CALIBRATION_AUTHORITY_EVIDENCE_MISSING:",
            "CALIBRATION_AUTHORITY_MISSING:",
            1,
        )
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=f"LIVE_INFERENCE_INPUTS_MISSING:{missing_reason}",
            city=family.city,
            target_date=family.target_date,
            metric=family.metric,
            family_id=family.family_id,
            source_status="MATCH",
            family_complete=True,
        )
    return _event_submission_receipt_from_typed_receipt_payload(
        raw_receipt,
        event,
        decision_proof_bundle=proof_bundle,
    )


def _event_submission_receipt_from_typed_receipt_payload(
    raw_receipt: dict[str, Any],
    event: OpportunityEvent,
    *,
    decision_proof_bundle: NoSubmitProofBundle | None = None,
) -> EventSubmissionReceipt:
    schema = str(raw_receipt.get("schema") or "")
    if schema != "edli_event_bound_no_submit_v1":
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason="EDLI_EVENT_BOUND_RECEIPT_SCHEMA_INVALID",
        )
    if str(raw_receipt.get("side_effect_status") or "") != "NO_SUBMIT":
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason="EDLI_EVENT_BOUND_RECEIPT_NOT_NO_SUBMIT",
        )
    return EventSubmissionReceipt(
        submitted=bool(raw_receipt.get("submitted")),
        event_id=str(raw_receipt.get("event_id") or ""),
        causal_snapshot_id=raw_receipt.get("causal_snapshot_id"),
        city=raw_receipt.get("city"),
        target_date=raw_receipt.get("target_date"),
        metric=raw_receipt.get("metric"),
        condition_id=raw_receipt.get("condition_id"),
        token_id=raw_receipt.get("token_id"),
        outcome_label=raw_receipt.get("outcome_label"),
        candidate_id=raw_receipt.get("candidate_id"),
        executable_snapshot_id=raw_receipt.get("executable_snapshot_id"),
        family_id=raw_receipt.get("family_id"),
        bin_label=raw_receipt.get("bin_label"),
        direction=raw_receipt.get("direction"),
        q_live=_optional_float(raw_receipt.get("q_live")),
        q_lcb_5pct=_optional_float(raw_receipt.get("q_lcb_5pct")),
        c_fee_adjusted=_optional_float(raw_receipt.get("c_fee_adjusted")),
        c_cost_95pct=_optional_float(raw_receipt.get("c_cost_95pct")),
        p_fill_lcb=_optional_float(raw_receipt.get("p_fill_lcb")),
        trade_score=_optional_float(raw_receipt.get("trade_score")),
        native_quote_available=_optional_bool(raw_receipt.get("native_quote_available")),
        source_status=raw_receipt.get("source_status"),
        family_complete=_optional_bool(raw_receipt.get("family_complete")),
        trade_score_positive=bool(raw_receipt.get("trade_score_positive")),
        fdr_pass=bool(raw_receipt.get("fdr_pass")),
        fdr_family_id=raw_receipt.get("fdr_family_id"),
        fdr_hypothesis_count=int(raw_receipt.get("fdr_hypothesis_count") or 0),
        kelly_pass=bool(raw_receipt.get("kelly_pass")),
        kelly_execution_price_type=raw_receipt.get("kelly_execution_price_type"),
        kelly_price_fee_deducted=bool(raw_receipt.get("kelly_price_fee_deducted")),
        kelly_size_usd=float(raw_receipt.get("kelly_size_usd") or 0.0),
        kelly_cost_basis_id=raw_receipt.get("kelly_cost_basis_id"),
        kelly_decision_id=raw_receipt.get("kelly_decision_id"),
        risk_decision_id=raw_receipt.get("risk_decision_id"),
        final_intent_id=raw_receipt.get("final_intent_id"),
        neg_risk=bool(raw_receipt.get("neg_risk") or False),
        side_effect_status="NO_SUBMIT",
        reason=str(raw_receipt.get("reason") or "event_bound_final_intent_no_submit"),
        proof_accepted=bool(raw_receipt.get("proof_accepted")),
        decision_proof_bundle=decision_proof_bundle,
        mainstream_agreement_pass=_optional_bool(raw_receipt.get("mainstream_agreement_pass")),
        mainstream_agreement_fail_reason=raw_receipt.get("mainstream_agreement_fail_reason"),
        mainstream_point=_optional_float(raw_receipt.get("mainstream_point")),
        mainstream_delta=_optional_float(raw_receipt.get("mainstream_delta")),
        mainstream_bin_label=raw_receipt.get("mainstream_bin_label"),
        mainstream_source=raw_receipt.get("mainstream_source"),
        mainstream_fetched_at_utc=raw_receipt.get("mainstream_fetched_at_utc"),
        q_source=raw_receipt.get("q_source"),  # #120 calibrator provenance
        q_lcb_calibration_source=raw_receipt.get("q_lcb_calibration_source"),
        posterior_id=_optional_int(raw_receipt.get("posterior_id")),  # H2_E2E
        probability_authority=raw_receipt.get("probability_authority"),  # H2_E2E
        strategy_key=raw_receipt.get("strategy_key"),
        opportunity_book=raw_receipt.get("opportunity_book"),
        replacement_forecast=raw_receipt.get("replacement_forecast"),
        unit=raw_receipt.get("unit"),
    )


def _build_submit_disabled_live_certificates(
    *,
    event: OpportunityEvent,
    receipt: EventSubmissionReceipt,
    decision_time: datetime,
    live_cap_conn: sqlite3.Connection | None = None,
    trade_conn: sqlite3.Connection | None = None,
    pre_submit_authority_provider: Callable[[DecisionCertificate, DecisionCertificate, datetime], PreSubmitAuthorityWitness] | None = None,
    canary_force_taker: bool = False,
    taker_fok_fak_live_enabled: bool = False,
) -> tuple[DecisionCertificate, ...]:
    command_certificates = _build_live_execution_command_certificates(
        event=event,
        receipt=receipt,
        decision_time=decision_time,
        live_cap_conn=live_cap_conn,
        trade_conn=trade_conn,
        pre_submit_authority_provider=pre_submit_authority_provider,
        canary_force_taker=canary_force_taker,
        taker_fok_fak_live_enabled=taker_fok_fak_live_enabled,
    )
    command = _required_cert(command_certificates, claims.EXECUTION_COMMAND)
    receipt_cert = build_execution_receipt_certificate(
        execution_command_cert=command,
        decision_time=decision_time,
        status="SUBMIT_DISABLED",
        reason_code="REAL_ORDER_SUBMIT_DISABLED",
    )
    transition_cert = _release_live_cap_for_submit_disabled(
        command_certificates,
        receipt_cert,
        live_cap_conn,
        decision_time=decision_time,
    )
    return command_certificates + (receipt_cert, transition_cert)


def _build_live_execution_command_certificates(
    *,
    event: OpportunityEvent,
    receipt: EventSubmissionReceipt,
    decision_time: datetime,
    live_cap_conn: sqlite3.Connection | None = None,
    trade_conn: sqlite3.Connection | None = None,
    pre_submit_authority_provider: Callable[[DecisionCertificate, DecisionCertificate, datetime], PreSubmitAuthorityWitness] | None = None,
    canary_force_taker: bool = False,
    taker_fok_fak_live_enabled: bool = False,
) -> tuple[DecisionCertificate, ...]:
    _assert_event_bound_strategy_live_admitted(
        strategy_key=receipt.strategy_key,
        direction=receipt.direction,
        metric=receipt.metric,
    )
    _assert_event_bound_receipt_live_authority(receipt)
    proof_bundle = receipt.decision_proof_bundle
    compile_result = DecisionCompiler().compile_no_submit(
        event,
        decision_time=decision_time,
        mode="NO_SUBMIT",
        proof_bundle=proof_bundle,
    )
    if compile_result.status != "VERIFIED":
        # KILLER 2 (2026-05-31): surface the UNDERLYING failing assertion, not just the
        # generic stage reason_code. compiler._rejected() captures the specific failure
        # message in CompileFailure.reason_detail (e.g. the exact field/parent that failed
        # _validate_no_submit_parent_consistency), but only reason_code was propagated —
        # so 147/308 positive-edge candidates died as opaque NO_SUBMIT_CERTIFICATE_REJECTED
        # with no diagnosable sub-reason in no_trade_regret_events. Append reason_detail so
        # the regret row records WHY the no-submit certificate was rejected.
        if compile_result.failures:
            failure = compile_result.failures[0]
            reason = failure.reason_code
            detail = getattr(failure, "reason_detail", None)
            if detail:
                reason = f"{reason}:{detail}"
        else:
            reason = "NO_SUBMIT_CERTIFICATE_REJECTED"
        raise ValueError(reason)
    base_certs = tuple(
        cert
        for cert in compile_result.certificates
        if cert.certificate_type not in {claims.NO_SUBMIT_DECISION, claims.NO_SUBMIT_MODE}
    )
    _assert_event_bound_calibration_live_admitted(_required_cert(base_certs, claims.CALIBRATION))
    executable_snapshot = _required_cert(base_certs, claims.EXECUTABLE_SNAPSHOT)
    live_cap = _build_live_cap_certificate_from_ledger(
        event=event,
        receipt=receipt,
        decision_time=decision_time,
        live_cap_conn=live_cap_conn,
        persist=False,
    )
    try:
        actionable = build_actionable_trade_certificate(
            payload=_actionable_payload_from_receipt(receipt, live_cap, event=event),
            parent_certificates=base_certs + (live_cap,),
            decision_time=decision_time,
        )
        forecast_authority = _required_cert(base_certs, claims.FORECAST_AUTHORITY)
        quote_feasibility = _required_cert(base_certs, claims.QUOTE_FEASIBILITY)
        cost_model = _required_cert(base_certs, claims.COST_MODEL)
        quote_payload = quote_feasibility.payload
        from types import SimpleNamespace

        side = "BUY" if str(actionable.payload.get("direction")) in {"buy_yes", "buy_no"} else "SELL"
        provisional_final_intent = SimpleNamespace(
            payload={
                "token_id": actionable.payload["token_id"],
                "side": side,
                "tick_size": float(_float_or_default(executable_snapshot.payload.get("min_tick_size"), 0.01)),
                "min_order_size": float(_float_or_default(executable_snapshot.payload.get("min_order_size"), 1.0)),
                "neg_risk": bool(actionable.payload.get("neg_risk", False)),
            }
        )
        authority_witness = _require_pre_submit_authority_witness(
            pre_submit_authority_provider,
            provisional_final_intent,
            executable_snapshot,
            decision_time,
        )
        fresh_best_bid = float(authority_witness.current_best_bid)
        fresh_best_ask = float(authority_witness.current_best_ask)
        best_bid = _optional_float(quote_payload.get("best_bid"))
        best_ask = _optional_float(quote_payload.get("best_ask"))
        order_mode = _select_edli_order_mode(
            actionable_payload=actionable.payload,
            quote_payload=quote_payload,
            best_bid=best_bid,
            best_ask=best_ask,
            executable_snapshot=executable_snapshot,
            canary_force_taker=canary_force_taker,
        )
        # WALL #1 (GATE #85 follow-on, 2026-06-01): the passive-maker context is a
        # MAKER-ONLY structural input. ``FinalExecutionIntent`` only requires it when
        # ``order_policy == "post_only_passive_limit"`` (execution_intent.py:1735); a
        # taker FOK/FAK crosses the JIT book at submit and never rests, so its
        # economics do not depend on the snapshot's top-of-book maker context.
        #
        # The pre-#85 path built ``_passive_maker_context_from_authorities``
        # UNCONDITIONALLY, which raises QUOTE_FEASIBILITY_BID_ASK_REQUIRED whenever the
        # elected snapshot has no captured book — killing every taker candidate whose
        # snapshot happened to be book-less (the DOMINANT live wall: 713/2h). Conditioning
        # the construction on order_mode makes that rejection CATEGORY impossible for
        # taker orders (Fitz #1: make the category impossible, not the instance). MAKER
        # still requires the maker context (and still raises if bid/ask are absent — the
        # correct fail-closed behavior, since a resting maker order genuinely needs a book).
        passive_maker_context = (
            _passive_maker_context_from_authorities(
                actionable=actionable,
                quote_feasibility_cert=quote_feasibility,
                executable_snapshot_cert=executable_snapshot,
                decision_time=decision_time,
            )
            if str(order_mode).strip().upper() == "MAKER"
            else None
        )
        # SIZE-TO-DEPTH + SWEEP-VWAP (Wall B / Wall C, 2026-06-01):
        # For TAKER FOK orders, compute the crossable depth and sweep VWAP from
        # the elected snapshot's live book BEFORE building the cert.  This ensures:
        #   (a) size is capped at available depth (FOK semantics preserved on the
        #       sized amount → no DEPTH_INSUFFICIENT at executor validation).
        #   (b) expected_fill_price_before_fee = sweep VWAP, not limit_price, so
        #       the executor sweep-average check (executor.py:1778) passes on
        #       multi-level books.
        # If no trade_conn is available, or order is MAKER, skip (legacy behaviour).
        available_crossable_shares: float | None = None
        sweep_expected_fill_price: float | None = None
        # MUST be initialized before the TAKER block: the final_intent build below
        # (Bug A tick_size source) references `_snap_for_depth` for ALL order modes,
        # but it is only assigned inside the TAKER+trade_conn block. Without this
        # initialization a MAKER order (or taker without trade_conn) raises
        # UnboundLocalError at cert build. None → tick_size falls back to the
        # executable_snapshot payload default, which is correct for the MAKER path.
        _snap_for_depth = None
        if str(order_mode).strip().upper() == "TAKER" and trade_conn is not None:
            from src.contracts.execution_intent import (
                quantize_submit_shares_for_venue_at_most,
                simulate_clob_sweep,
            )
            _snap_id_for_depth = str(
                executable_snapshot.payload.get("identity")
                or executable_snapshot.payload.get("selected_snapshot_id")
                or ""
            )
            try:
                _snap_for_depth = get_snapshot(trade_conn, _snap_id_for_depth) if _snap_id_for_depth else None
            except Exception:
                _snap_for_depth = None
            if _snap_for_depth is not None:
                _action_payload = actionable.payload
                _min_order_size_d = Decimal(str(
                    executable_snapshot.payload.get("min_order_size") or "1.0"
                ))
                _tick_size_d = Decimal(str(
                    executable_snapshot.payload.get("min_tick_size") or "0.01"
                ))
                _reservation = Decimal(str(_action_payload.get("c_fee_adjusted") or "0"))
                _direction_for_depth = str(_action_payload.get("direction") or "buy_no")
                if _direction_for_depth.startswith("buy_"):
                    _fresh_touch = Decimal(str(fresh_best_ask))
                    # A BUY taker must cross the fresh ask.  If the ask is now above
                    # the reservation, this candidate is no longer executable and
                    # must be rejected so the reactor can continue to the next market.
                    if _fresh_touch > _reservation:
                        raise ValueError(
                            "TAKER_BUY_TOUCH_EXCEEDS_RESERVATION:"
                            f"best_ask={_fresh_touch}:reservation={_reservation}"
                        )
                    _limit_price_d = _fresh_touch
                    _rounding_mode = "up"
                else:
                    _fresh_touch = Decimal(str(fresh_best_bid))
                    if _fresh_touch < _reservation:
                        raise ValueError(
                            "TAKER_SELL_TOUCH_BELOW_RESERVATION:"
                            f"best_bid={_fresh_touch}:reservation={_reservation}"
                        )
                    _limit_price_d = _fresh_touch
                    _rounding_mode = "down"
                # Tick-align the marketable touch using the canonical tick_size:
                # BUY rounds up to keep crossing; SELL rounds down to keep crossing.
                import math as _math
                if _tick_size_d > 0:
                    _ratio = float(_limit_price_d) / float(_tick_size_d)
                    _round_fn = _math.ceil if _rounding_mode == "up" else _math.floor
                    _epsilon = -1e-9 if _rounding_mode == "up" else 1e-9
                    _limit_price_d = Decimal(str(
                        round(_round_fn(_ratio + _epsilon) * float(_tick_size_d), 10)
                    ))
                    if _direction_for_depth.startswith("buy_") and _limit_price_d > _reservation:
                        raise ValueError(
                            "TAKER_BUY_TOUCH_EXCEEDS_RESERVATION:"
                            f"marketable_limit={_limit_price_d}:reservation={_reservation}"
                        )
                    if (
                        not _direction_for_depth.startswith("buy_")
                        and _limit_price_d < _reservation
                    ):
                        raise ValueError(
                            "TAKER_SELL_TOUCH_BELOW_RESERVATION:"
                            f"marketable_limit={_limit_price_d}:reservation={_reservation}"
                        )
                if _limit_price_d <= 0:
                    raise ValueError(
                        "EXECUTION_PRICE_BELOW_MIN_TICK:"
                        f"limit_price={_limit_price_d}:min_tick_size={_tick_size_d}"
                    )
                _reserved_notional = Decimal(str(
                    _action_payload.get("live_cap_reserved_notional_usd")
                    or _action_payload.get("kelly_size_usd")
                    or "0"
                ))
                # Bug B fix (2026-06-01): compute desired_shares using float arithmetic
                # so the value matches exactly what the cert builder will compute for
                # `size = max(float(min_order_size), reserved_notional / limit_price)`.
                # Using Decimal division here produced a different number of shares than
                # the cert builder's float division (e.g. 8.333...333 vs 8.333333333333334),
                # causing the guard's re-sweep to get a different VWAP → parity rejection.
                _min_order_size_f = float(_min_order_size_d)
                _reserved_notional_f = float(_reserved_notional)
                _limit_price_f = float(_limit_price_d)
                _desired_shares_f = (
                    max(_min_order_size_f, _reserved_notional_f / _limit_price_f)
                    if _limit_price_f > 0 else _min_order_size_f
                )
                _desired_shares = Decimal(str(_desired_shares_f))
                _depth_sweep = simulate_clob_sweep(
                    snapshot=_snap_for_depth,
                    direction=_direction_for_depth,
                    requested_size_kind="shares",
                    requested_size_value=_desired_shares,
                    limit_price=_limit_price_d,
                )
                if _depth_sweep.filled_shares > 0:
                    # SEV-1.2 fix (2026-06-06): the submitted share amount itself
                    # must already obey the venue's immediate-BUY amount grids before
                    # the final intent certificate is built.  The earlier Wall-B fix
                    # mirrored the cert builder's size cap, but left many-decimal BUY
                    # sizes such as 36.304447843137254 in SubmitPlanBuilt; the
                    # executor correctly rejected them before contacting the SDK.
                    #
                    # Mirror the final builder's exact size law here, then sweep on
                    # the largest venue-legal size that does not exceed the target or
                    # available depth. If no venue-legal size can fully fill, fail
                    # closed rather than submitting a deterministic reject.
                    _raw_capped_shares = min(_desired_shares, _depth_sweep.filled_shares)
                    _venue_quantized_shares = quantize_submit_shares_for_venue_at_most(
                        _direction_for_depth,
                        _raw_capped_shares,
                        final_limit_price=_limit_price_d,
                        order_type="FOK",
                    )
                    _venue_quantized_sweep = simulate_clob_sweep(
                        snapshot=_snap_for_depth,
                        direction=_direction_for_depth,
                        requested_size_kind="shares",
                        requested_size_value=_venue_quantized_shares,
                        limit_price=_limit_price_d,
                    )
                    if not _venue_quantized_sweep.fully_filled:
                        raise ValueError(
                            "DEPTH_BELOW_VENUE_QUANTIZED_SIZE:"
                            f"filled_shares={_venue_quantized_sweep.filled_shares}:"
                            f"venue_quantized_shares={_venue_quantized_shares}:"
                            f"depth_status={_venue_quantized_sweep.depth_status}"
                        )
                    available_crossable_shares = float(_venue_quantized_shares)
                    # Single-source principle: the final intent size, the stored
                    # expected fill price, and the executor guard re-sweep are all
                    # driven by the same snapshot, limit, and venue-legal size.
                    sweep_expected_fill_price = (
                        str(_venue_quantized_sweep.average_price)
                        if _venue_quantized_sweep.average_price is not None else None
                    )
        executable_market_context = _executable_market_context_from_snapshot(_snap_for_depth)
        final_intent = build_final_intent_certificate_from_actionable(
            actionable_cert=actionable,
            executable_snapshot_cert=executable_snapshot,
            quote_feasibility_cert=quote_feasibility,
            cost_model_cert=cost_model,
            forecast_authority_cert=forecast_authority,
            decision_source_context=forecast_authority.payload,
            passive_maker_context=passive_maker_context,
            decision_time=decision_time,
            order_mode=order_mode,
            # BUG #92 structural fix (2026-06-02): the intent's tick_size MUST be the
            # min_tick_size of the SAME snapshot the executor re-hydrates at submit
            # time (intent.snapshot_id == proof.executable_snapshot_id ==
            # executable_snapshot.payload['identity']).  Both branches below are bound
            # to that one snapshot:
            #   - TAKER: `_snap_for_depth` = get_snapshot(proof.executable_snapshot_id).
            #   - else : executable_snapshot.payload['min_tick_size'], populated at
            #            reactor:2448 from `_hydrated_snapshot =
            #            get_snapshot(proof.executable_snapshot_id)`.
            # The pre-fix `_float_or_default(..., 0.01)` silent default was an UNBOUND
            # tick source: when the canonical tick disagreed with a hardcoded 0.01 the
            # intent diverged from the executor's snapshot (live 2026-06-01: intent
            # tick=0.001 vs bound snapshot tick=0.01 → 28 EXECUTOR_PRE_VENUE_REJECTED).
            # Fail closed instead of defaulting — a missing canonical tick is a
            # provenance fault, not a 0.01 market.
            tick_size=str(_snap_for_depth.min_tick_size) if _snap_for_depth is not None else _required_bound_tick_size(_snap_for_depth, executable_snapshot.payload),
            min_order_size=_float_or_default(executable_snapshot.payload.get("min_order_size"), 1.0),
            best_bid=fresh_best_bid if str(order_mode).strip().upper() == "TAKER" else best_bid,
            best_ask=fresh_best_ask if str(order_mode).strip().upper() == "TAKER" else best_ask,
            taker_fok_fak_live_enabled=taker_fok_fak_live_enabled,
            available_crossable_shares=available_crossable_shares,
            sweep_expected_fill_price=sweep_expected_fill_price,
            executable_market_context=executable_market_context,
        )
        if (
            taker_fok_fak_live_enabled
            and final_intent.payload.get("post_only") is True
            and _ev_boundary_favors_cross(
                actionable_payload=actionable.payload,
                quote_payload=quote_payload,
                best_bid=fresh_best_bid,
                best_ask=fresh_best_ask,
                reservation=_optional_float(actionable.payload.get("c_fee_adjusted")),
                side=str(actionable.payload.get("side") or "BUY"),
            )
        ):
            final_intent = build_final_intent_certificate_from_actionable(
                actionable_cert=actionable,
                executable_snapshot_cert=executable_snapshot,
                quote_feasibility_cert=quote_feasibility,
                cost_model_cert=cost_model,
                forecast_authority_cert=forecast_authority,
                decision_source_context=forecast_authority.payload,
                passive_maker_context=None,
                decision_time=decision_time,
                order_mode="TAKER",
                tick_size=str(_snap_for_depth.min_tick_size) if _snap_for_depth is not None else _required_bound_tick_size(_snap_for_depth, executable_snapshot.payload),
                min_order_size=_float_or_default(executable_snapshot.payload.get("min_order_size"), 1.0),
                best_bid=fresh_best_bid,
                best_ask=fresh_best_ask,
                    taker_fok_fak_live_enabled=taker_fok_fak_live_enabled,
                    available_crossable_shares=available_crossable_shares,
                    sweep_expected_fill_price=sweep_expected_fill_price,
                    executable_market_context=executable_market_context,
                )
        already_locked_reason = _locked_live_opportunity_no_price_improvement_reason(
            live_cap_conn,
            condition_id=str(final_intent.payload["condition_id"]),
            token_id=str(final_intent.payload["token_id"]),
            direction=str(final_intent.payload["direction"]),
            side=str(final_intent.payload.get("side") or "BUY"),
            limit_price=_optional_float(final_intent.payload.get("limit_price")),
        )
        if already_locked_reason is not None:
            raise _LiveOpportunityAlreadyLocked(already_locked_reason)
        executor_native_intent_hash = validate_final_intent_cert_for_existing_executor(final_intent)
        aggregate_ledger = LiveOrderAggregateLedger(live_cap_conn)
        aggregate_id = _live_order_aggregate_id(event.event_id, str(final_intent.payload["final_intent_id"]))
        decision_event = aggregate_ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="DecisionProofAccepted",
            payload={
                "event_id": event.event_id,
                "final_intent_id": final_intent.payload["final_intent_id"],
                "no_submit_certificate_count": len(base_certs),
                "no_submit_receipt_event_id": receipt.event_id,
                "decision_audit": _live_decision_audit_payload(
                    receipt=receipt,
                    base_certs=base_certs,
                    actionable=actionable,
                    final_intent=final_intent,
                ),
            },
            occurred_at=decision_time,
            source_authority="decision_kernel",
        )
        submit_plan_event = aggregate_ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="SubmitPlanBuilt",
            payload={
                "event_id": event.event_id,
                "final_intent_id": final_intent.payload["final_intent_id"],
                "condition_id": final_intent.payload["condition_id"],
                "token_id": final_intent.payload["token_id"],
                "direction": final_intent.payload["direction"],
                "order_type": final_intent.payload["order_type"],
                "time_in_force": final_intent.payload["time_in_force"],
                "post_only": final_intent.payload["post_only"],
                "limit_price": final_intent.payload["limit_price"],
                "size": final_intent.payload["size"],
            },
            occurred_at=decision_time,
            source_authority="engine_adapter",
            expected_parent_event_hash=decision_event.event_hash,
        )
        pre_submit_event = aggregate_ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_revalidation_payload_from_final_intent(
                final_intent=final_intent,
                executable_snapshot=executable_snapshot,
                decision_time=decision_time,
                authority_witness=authority_witness,
            ),
            occurred_at=decision_time,
            source_authority="engine_adapter",
            expected_parent_event_hash=submit_plan_event.event_hash,
        )
        live_cap_event = aggregate_ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="LiveCapReserved",
            payload={
                "event_id": event.event_id,
                "final_intent_id": final_intent.payload["final_intent_id"],
                "usage_id": live_cap.payload["usage_id"],
                "reserved_notional_usd": live_cap.payload["reserved_notional_usd"],
                "reservation_status": live_cap.payload["reservation_status"],
            },
            occurred_at=decision_time,
            source_authority="live_cap_ledger",
            expected_parent_event_hash=pre_submit_event.event_hash,
        )
        execution_command_id = _execution_command_id_from_final_intent(actionable, final_intent)
        expressibility = build_executor_expressibility_certificate(
            final_intent_cert=final_intent,
            executable_snapshot_cert=executable_snapshot,
            live_cap_cert=live_cap,
            decision_time=decision_time,
            executor_native_intent_hash=executor_native_intent_hash,
        )
        command_event = aggregate_ledger.append_event(
            aggregate_id=aggregate_id,
            event_type="ExecutionCommandCreated",
            payload={
                "event_id": event.event_id,
                "final_intent_id": final_intent.payload["final_intent_id"],
                "execution_command_id": execution_command_id,
                "pre_submit_event_hash": pre_submit_event.event_hash,
                "live_cap_reserved_event_hash": live_cap_event.event_hash,
                "usage_id": live_cap.payload["usage_id"],
            },
            occurred_at=decision_time,
            source_authority="engine_adapter",
            expected_parent_event_hash=live_cap_event.event_hash,
        )
        pre_submit = build_pre_submit_revalidation_certificate(
            pre_submit_event=pre_submit_event,
            final_intent_cert=final_intent,
            live_cap_cert=live_cap,
            decision_time=decision_time,
            execution_command_event_hash=command_event.event_hash,
        )
        command = build_execution_command_certificate_from_final_intent(
            actionable_cert=actionable,
            final_intent_cert=final_intent,
            executor_expressibility_cert=expressibility,
            live_cap_cert=live_cap,
            pre_submit_revalidation_cert=pre_submit,
            decision_time=decision_time,
        )
        from src.events.live_cap import LiveCapLedger

        # Durable re-reservation of the Kelly notional already computed by
        # _build_live_cap_certificate_from_ledger above (which built the cert with
        # persist=False). This is the EXACTLY-ONCE + DRIFT-DETECT write: it dedupes
        # by (event_id, cap_scope) and raises if the reserved notional drifted.
        # 2026-06-08: no notional/order-count cap args — the reservation caps
        # nothing; it only records and dedupes.
        reserve_result = LiveCapLedger(live_cap_conn).reserve(
            event_id=event.event_id,
            decision_time=decision_time,
            cap_scope="tiny_live_canary",
            requested_notional_usd=float(live_cap.payload["reserved_notional_usd"]),
            final_intent_id=str(final_intent.payload["final_intent_id"]),
            execution_command_id=execution_command_id,
        )
        if reserve_result.usage_id != str(live_cap.payload["usage_id"]):
            raise ValueError("live cap reservation drift for provisional certificate")
        if float(reserve_result.reserved_notional_usd) != float(live_cap.payload["reserved_notional_usd"]):
            raise ValueError("LIVE_CAP_RESERVED_NOTIONAL_DRIFT")
    except Exception:
        raise
    return base_certs + (live_cap, actionable, final_intent, expressibility, pre_submit, command)


def _actionable_payload_from_receipt(
    receipt: EventSubmissionReceipt,
    live_cap_cert: DecisionCertificate,
    *,
    event: OpportunityEvent | None = None,
) -> dict[str, object]:
    reserved_notional = float(live_cap_cert.payload["reserved_notional_usd"])
    city = receipt.city or _event_identity_value(event, "city")
    target_date = receipt.target_date or _event_identity_value(event, "target_date")
    metric = receipt.metric or _event_identity_value(event, "metric") or _event_identity_value(event, "temperature_metric")
    return {
        "event_id": receipt.event_id,
        "event_type": event.event_type if event is not None else None,
        "causal_snapshot_id": receipt.causal_snapshot_id,
        "strategy_key": receipt.strategy_key,
        "family_id": receipt.family_id,
        "candidate_id": receipt.candidate_id,
        "condition_id": receipt.condition_id,
        "token_id": receipt.token_id,
        "direction": receipt.direction,
        "executable_snapshot_id": receipt.executable_snapshot_id,
        "q_source": receipt.q_source,
        "opportunity_book": receipt.opportunity_book,
        "q_live": receipt.q_live,
        "q_lcb_5pct": receipt.q_lcb_5pct,
        "c_fee_adjusted": receipt.c_fee_adjusted,
        "c_cost_95pct": receipt.c_cost_95pct,
        "p_fill_lcb": receipt.p_fill_lcb,
        "trade_score": receipt.trade_score,
        "action_score": receipt.trade_score,
        "fdr_family_id": receipt.fdr_family_id,
        "kelly_decision_id": receipt.kelly_decision_id,
        "kelly_size_usd": receipt.kelly_size_usd,
        "risk_decision_id": receipt.risk_decision_id,
        "live_cap_usage_id": live_cap_cert.payload["usage_id"],
        "live_cap_reserved_notional_usd": reserved_notional,
        "final_intent_id": receipt.final_intent_id,
        "neg_risk": receipt.neg_risk,
        "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
        "native_quote_available": receipt.native_quote_available,
        "city": city,
        "target_date": target_date,
        "metric": metric,
        "temperature_metric": metric,
        "bin_label": receipt.bin_label,
        "outcome_label": receipt.outcome_label,
        "unit": receipt.unit,
        "submitted": False,
    }


def _live_decision_audit_payload(
    *,
    receipt: EventSubmissionReceipt,
    base_certs: tuple[DecisionCertificate, ...],
    actionable: DecisionCertificate,
    final_intent: DecisionCertificate,
) -> dict[str, object]:
    """Compact durable audit payload for real-submit aggregate root events.

    ``edli_no_submit_receipts`` intentionally rejects real-submit receipts. The
    live-order aggregate is therefore the durable source for reconstructing why a
    submitted order existed. Keep this payload small and derived from the same
    receipt/certificates already authorized by the money path.
    """

    book = receipt.opportunity_book if isinstance(receipt.opportunity_book, dict) else {}
    selected_candidate_id = str(book.get("selected_candidate_id") or "").strip() or None
    actual_candidate_id = str(book.get("actual_receipt_selected_candidate_id") or "").strip() or None
    selected_candidate: Mapping[str, object] | None = None
    candidates = book.get("candidates")
    if isinstance(candidates, list) and selected_candidate_id is not None:
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            if str(candidate.get("candidate_id") or "").strip() == selected_candidate_id:
                selected_candidate = candidate
                break

    return {
        "schema": "edli_live_decision_audit_v1",
        "event_id": receipt.event_id,
        "final_intent_id": receipt.final_intent_id,
        "family_id": receipt.family_id,
        "candidate_id": receipt.candidate_id,
        "condition_id": receipt.condition_id,
        "token_id": receipt.token_id,
        "direction": receipt.direction,
        "city": receipt.city,
        "target_date": receipt.target_date,
        "metric": receipt.metric,
        "bin_label": receipt.bin_label,
        "outcome_label": receipt.outcome_label,
        "unit": receipt.unit,
        "strategy_key": receipt.strategy_key,
        "q_source": receipt.q_source,
        # H2_E2E: make the live-order aggregate self-contained — the fill->posterior
        # link is reconstructable from the aggregate payload without JSON_EXTRACT on
        # the receipt blob. None on canonical orders.
        "posterior_id": receipt.posterior_id,
        "probability_authority": receipt.probability_authority,
        "q_lcb_calibration_source": receipt.q_lcb_calibration_source,
        "q_live": receipt.q_live,
        "q_lcb_5pct": receipt.q_lcb_5pct,
        "c_fee_adjusted": receipt.c_fee_adjusted,
        "c_cost_95pct": receipt.c_cost_95pct,
        "p_fill_lcb": receipt.p_fill_lcb,
        "trade_score": receipt.trade_score,
        "kelly_size_usd": receipt.kelly_size_usd,
        "kelly_decision_id": receipt.kelly_decision_id,
        "risk_decision_id": receipt.risk_decision_id,
        "opportunity_book": receipt.opportunity_book,
        "selected_candidate_id": selected_candidate_id,
        "actual_receipt_selected_candidate_id": actual_candidate_id,
        "selected_condition_id": (
            str(selected_candidate.get("condition_id") or "").strip()
            if selected_candidate is not None
            else None
        ),
        "selected_token_id": (
            str(selected_candidate.get("token_id") or "").strip()
            if selected_candidate is not None
            else None
        ),
        "selected_direction": (
            str(selected_candidate.get("direction") or "").strip()
            if selected_candidate is not None
            else None
        ),
        "selected_bin_label": (
            selected_candidate.get("bin_label")
            if selected_candidate is not None
            else None
        ),
        "actual_condition_id": receipt.condition_id,
        "actual_token_id": receipt.token_id,
        "actual_direction": receipt.direction,
        "actual_bin_label": receipt.bin_label,
        "actionable_certificate_hash": actionable.certificate_hash,
        "final_intent_certificate_hash": final_intent.certificate_hash,
        "parent_certificates": [
            {
                "certificate_type": cert.certificate_type,
                "certificate_id": cert.certificate_id,
                "certificate_hash": cert.certificate_hash,
            }
            for cert in base_certs
        ],
    }


def _event_identity_value(event: OpportunityEvent | None, key: str) -> object | None:
    if event is None:
        return None
    payload = getattr(event, "payload", None)
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _live_order_aggregate_id(event_id: str, final_intent_id: str) -> str:
    return f"{event_id}:{final_intent_id}"


def _execution_command_id_from_final_intent(
    actionable: DecisionCertificate,
    final_intent: DecisionCertificate,
) -> str:
    action = actionable.payload
    intent = final_intent.payload
    return (
        f"edli_exec_cmd:{action['event_id']}:{intent['final_intent_id']}:"
        f"{intent['token_id']}:{intent['direction']}"
    )


def _locked_live_opportunity_no_price_improvement_reason(
    live_cap_conn: sqlite3.Connection | None,
    *,
    condition_id: str,
    token_id: str,
    direction: str,
    side: str,
    limit_price: float | None,
    improve_delta: float = 0.02,
) -> str | None:
    """Return a suppression reason when a locked opportunity has not repriced better.

    Continuous redecision may keep scanning fresh forecast events, but once the
    money path has locked a specific condition/token/direction into an execution
    command, identical later cycles must not emit another will-trade chain.  A
    later cycle is allowed only when the final limit price materially improves.
    For BUY directions that means a lower limit; for SELL directions, a higher
    limit.
    """

    if live_cap_conn is None or not condition_id or not token_id or not direction:
        return None
    LiveOrderAggregateLedger(live_cap_conn)
    rows = live_cap_conn.execute(
        """
        SELECT
            json_extract(plan.payload_json, '$.limit_price') AS prior_limit_price,
            plan.aggregate_id,
            plan.occurred_at
        FROM edli_live_order_events AS plan
        WHERE plan.event_type = 'SubmitPlanBuilt'
          AND json_extract(plan.payload_json, '$.condition_id') = ?
          AND json_extract(plan.payload_json, '$.token_id') = ?
          AND json_extract(plan.payload_json, '$.direction') = ?
          AND EXISTS (
              SELECT 1
              FROM edli_live_order_events AS command
              WHERE command.aggregate_id = plan.aggregate_id
                AND command.event_type = 'ExecutionCommandCreated'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM edli_live_order_events AS rejected
              WHERE rejected.aggregate_id = plan.aggregate_id
                AND rejected.event_type = 'SubmitRejected'
          )
        ORDER BY plan.occurred_at DESC
        LIMIT 64
        """,
        (condition_id, token_id, direction),
    ).fetchall()
    prior_prices = [
        price
        for price in (_optional_float(row[0]) for row in rows)
        if price is not None
    ]
    if not prior_prices:
        return None
    side_upper = str(side or "").strip().upper()
    if limit_price is None:
        return (
            "EDLI_LOCKED_OPPORTUNITY_NO_PRICE_IMPROVEMENT:"
            f"condition_id={condition_id}:token_id={token_id}:direction={direction}"
        )
    if side_upper == "SELL":
        prior_best = max(prior_prices)
        if limit_price >= prior_best + improve_delta - 1e-9:
            return None
        comparison = f"prior_best_limit={prior_best:.6g}:current_limit={limit_price:.6g}:required_delta={improve_delta:.6g}"
    else:
        prior_best = min(prior_prices)
        if limit_price <= prior_best - improve_delta + 1e-9:
            return None
        comparison = f"prior_best_limit={prior_best:.6g}:current_limit={limit_price:.6g}:required_delta={improve_delta:.6g}"
    return (
        "EDLI_LOCKED_OPPORTUNITY_NO_PRICE_IMPROVEMENT:"
        f"condition_id={condition_id}:token_id={token_id}:direction={direction}:"
        f"{comparison}"
    )


def _pre_submit_revalidation_payload_from_final_intent(
    *,
    final_intent: DecisionCertificate,
    executable_snapshot: DecisionCertificate,
    decision_time: datetime,
    authority_witness: PreSubmitAuthorityWitness,
) -> dict[str, object]:
    payload = final_intent.payload
    limit_price = _float_or_default(payload.get("limit_price"), 0.01)
    quote_seen_at = _parse_utc(authority_witness.quote_seen_at)
    if quote_seen_at is None:
        raise ValueError("PRE_SUBMIT_QUOTE_SEEN_AT_REQUIRED")
    quote_age_ms = int(max(0.0, (decision_time.astimezone(UTC) - quote_seen_at).total_seconds() * 1000.0))
    current_best_bid = float(authority_witness.current_best_bid)
    current_best_ask = float(authority_witness.current_best_ask)
    tick_size = float(authority_witness.tick_size)
    min_order_size = float(authority_witness.min_order_size)
    side = str(payload["side"])
    would_cross = _would_cross_post_only_book(
        side=side,
        limit_price=limit_price,
        current_best_bid=current_best_bid,
        current_best_ask=current_best_ask,
    )
    return {
        "event_id": payload["event_id"],
        "event_type": payload.get("event_type"),
        "final_intent_id": payload["final_intent_id"],
        "strategy_key": payload.get("strategy_key"),
        "condition_id": payload["condition_id"],
        "token_id": payload["token_id"],
        "side": payload["side"],
        "direction": payload["direction"],
        "city": payload.get("city"),
        "target_date": payload.get("target_date"),
        "metric": payload.get("metric") or payload.get("temperature_metric"),
        "temperature_metric": payload.get("temperature_metric") or payload.get("metric"),
        "bin_label": payload.get("bin_label"),
        "outcome_label": payload.get("outcome_label"),
        "unit": payload.get("unit"),
        "order_type": payload["order_type"],
        "time_in_force": payload["time_in_force"],
        "post_only": payload["post_only"],
        "checked_at": authority_witness.checked_at or decision_time.isoformat(),
        "quote_seen_at": authority_witness.quote_seen_at,
        "quote_age_ms": quote_age_ms,
        "max_quote_age_ms": int(authority_witness.max_quote_age_ms),
        "book_hash": authority_witness.book_hash,
        "current_best_bid": current_best_bid,
        "current_best_ask": current_best_ask,
        "limit_price": limit_price,
        "would_cross_book": would_cross,
        "tick_size": tick_size,
        "tick_aligned": _is_price_tick_aligned(limit_price, tick_size),
        "min_order_size": min_order_size,
        "size_ok": _float_or_default(payload.get("size"), 0.0) >= min_order_size,
        "neg_risk": authority_witness.neg_risk,
        "heartbeat_status": authority_witness.heartbeat_status,
        "user_ws_status": authority_witness.user_ws_status,
        "venue_connectivity_status": authority_witness.venue_connectivity_status,
        "balance_allowance_status": authority_witness.balance_allowance_status,
        "book_authority_id": authority_witness.book_authority_id,
        "book_captured_at": authority_witness.book_captured_at,
        "heartbeat_authority_id": authority_witness.heartbeat_authority_id,
        "heartbeat_checked_at": authority_witness.heartbeat_checked_at,
        "user_ws_authority_id": authority_witness.user_ws_authority_id,
        "user_ws_checked_at": authority_witness.user_ws_checked_at,
        "venue_connectivity_authority_id": authority_witness.venue_connectivity_authority_id,
        "venue_connectivity_checked_at": authority_witness.venue_connectivity_checked_at,
        "balance_allowance_authority_id": authority_witness.balance_allowance_authority_id,
        "balance_allowance_checked_at": authority_witness.balance_allowance_checked_at,
        "expected_edge_source_certificate_hash": payload.get("actionable_certificate_hash"),
        "cost_basis_source_certificate_hash": payload.get("cost_basis_hash"),
        "final_intent_certificate_hash": final_intent.certificate_hash,
    }


def _require_pre_submit_authority_witness(
    provider: Callable[[DecisionCertificate, DecisionCertificate, datetime], PreSubmitAuthorityWitness] | None,
    final_intent: DecisionCertificate,
    executable_snapshot: DecisionCertificate,
    decision_time: datetime,
) -> PreSubmitAuthorityWitness:
    if provider is None:
        raise ValueError("PRE_SUBMIT_AUTHORITY_WITNESS_REQUIRED")
    witness = provider(final_intent, executable_snapshot, decision_time)
    if not isinstance(witness, PreSubmitAuthorityWitness):
        raise ValueError("PRE_SUBMIT_AUTHORITY_WITNESS_REQUIRED")
    required_text_fields = {
        "book_hash": witness.book_hash,
        "book_authority_id": witness.book_authority_id,
        "book_captured_at": witness.book_captured_at,
        "heartbeat_authority_id": witness.heartbeat_authority_id,
        "heartbeat_checked_at": witness.heartbeat_checked_at,
        "user_ws_authority_id": witness.user_ws_authority_id,
        "user_ws_checked_at": witness.user_ws_checked_at,
        "venue_connectivity_authority_id": witness.venue_connectivity_authority_id,
        "venue_connectivity_checked_at": witness.venue_connectivity_checked_at,
        "balance_allowance_authority_id": witness.balance_allowance_authority_id,
        "balance_allowance_checked_at": witness.balance_allowance_checked_at,
    }
    missing = [field for field, value in required_text_fields.items() if not str(value or "").strip()]
    if missing:
        raise ValueError("PRE_SUBMIT_AUTHORITY_PROVENANCE_REQUIRED:" + ",".join(missing))
    return witness


def _would_cross_post_only_book(
    *,
    side: str,
    limit_price: float,
    current_best_bid: float,
    current_best_ask: float,
) -> bool:
    if side == "BUY":
        return limit_price >= current_best_ask
    if side == "SELL":
        return limit_price <= current_best_bid
    raise ValueError(f"unsupported pre-submit side: {side!r}")


def _is_price_tick_aligned(price: float, tick_size: float) -> bool:
    if tick_size <= 0:
        return False
    units = round(price / tick_size)
    return abs(price - units * tick_size) < 1e-9


def _build_live_cap_certificate_from_ledger(
    *,
    event: OpportunityEvent,
    receipt: EventSubmissionReceipt,
    decision_time: datetime,
    live_cap_conn: sqlite3.Connection | None,
    persist: bool = True,
) -> DecisionCertificate:
    if live_cap_conn is None:
        raise ValueError("LIVE_CAP_LEDGER_CONNECTION_REQUIRED")
    from src.events.live_cap import LiveCapLedger

    # 2026-06-08 operator directive: the tiny_live $5 notional + per-day/window
    # order-count caps are DELETED. Order size is governed SOLELY by the
    # structural fractional-Kelly sizing upstream (money_path_adapters.evaluate_kelly).
    # The reservation records the (uncapped) Kelly notional; it clamps NOTHING.
    # A one-tick floor still guards against a sub-tick request.
    price = _float_or_default(receipt.c_fee_adjusted, 0.01)
    kelly_usd = float(receipt.kelly_size_usd or 0.0)
    min_order_notional = max(price, 0.01)
    requested_notional = max(kelly_usd, min_order_notional)
    usage_id = LiveCapLedger._usage_id(event.event_id, "tiny_live_canary")
    if persist:
        reservation = LiveCapLedger(live_cap_conn).reserve(
            event_id=event.event_id,
            decision_time=decision_time,
            cap_scope="tiny_live_canary",
            requested_notional_usd=float(requested_notional),
            final_intent_id=receipt.final_intent_id,
        )
    else:
        from src.events.live_cap import LiveCapReservation

        reservation = LiveCapReservation(
            usage_id=usage_id,
            event_id=event.event_id,
            decision_time=decision_time,
            cap_scope="tiny_live_canary",
            reserved_notional_usd=float(requested_notional),
            reservation_status="RESERVED",
            final_intent_id=receipt.final_intent_id,
        )
    payload = reservation.certificate_payload()
    return build_certificate(
        certificate_type=claims.LIVE_CAP,
        semantic_key=f"live_cap:{reservation.usage_id}",
        claim_type=claims.LIVE_CAP,
        mode="LIVE",
        decision_time=decision_time,
        source_available_at=decision_time,
        agent_received_at=decision_time,
        persisted_at=decision_time,
        payload=payload,
        parent_edges=(),
        parent_certificates=(),
        authority_id="edli.live_cap",
        authority_version="v1",
        algorithm_id="edli.submit_disabled_live_cap",
        algorithm_version="v1",
    )


def _release_live_cap_for_submit_disabled(
    certificates: tuple[DecisionCertificate, ...],
    receipt_cert: DecisionCertificate,
    live_cap_conn: sqlite3.Connection | None,
    *,
    decision_time: datetime,
) -> DecisionCertificate:
    live_cap = _required_cert(certificates, claims.LIVE_CAP)
    command = _required_cert(certificates, claims.EXECUTION_COMMAND)
    _release_live_cap_certificate(live_cap, live_cap_conn, reason="SUBMIT_DISABLED")
    cap_event_hash = _append_cap_transition_aggregate_event(
        live_cap_conn,
        command,
        receipt_cert,
        to_status="RELEASED",
        projection_status="RELEASED",
        reason_code="SUBMIT_DISABLED",
        decision_time=decision_time,
    )
    return build_live_cap_transition_certificate(
        live_cap_cert=live_cap,
        execution_receipt_cert=receipt_cert,
        decision_time=decision_time,
        to_status="RELEASED",
        reason_code="SUBMIT_DISABLED",
        aggregate_event_hash=cap_event_hash,
    )


def _transition_live_cap_after_submit(
    certificates: tuple[DecisionCertificate, ...],
    live_cap_conn: sqlite3.Connection,
    command: DecisionCertificate,
    receipt_cert: DecisionCertificate,
    submit_result: EventBoundExecutorSubmitResult,
    *,
    decision_time: datetime,
) -> DecisionCertificate:
    live_cap = _required_cert(certificates, claims.LIVE_CAP)
    usage_id = str(live_cap.payload["usage_id"])
    from src.events.live_cap import LiveCapLedger

    ledger = LiveCapLedger(live_cap_conn)
    if submit_result.status == "SUBMITTED":
        ledger.consume(
            usage_id,
            final_intent_id=str(command.payload["final_intent_id"]),
            execution_command_id=str(command.payload["execution_command_id"]),
        )
        return build_live_cap_transition_certificate(
            live_cap_cert=live_cap,
            execution_receipt_cert=receipt_cert,
            decision_time=decision_time,
            to_status="CONSUMED",
            reason_code=submit_result.reason_code,
            aggregate_event_hash=_append_cap_transition_aggregate_event(
                live_cap_conn,
                command,
                receipt_cert,
                to_status="CONSUMED",
                projection_status="CONSUMED",
                reason_code=submit_result.reason_code,
                decision_time=decision_time,
            ),
        )
    elif submit_result.status in {"REJECTED", "PRE_SUBMIT_ERROR"}:
        ledger.release(usage_id, submit_result.reason_code)
        return build_live_cap_transition_certificate(
            live_cap_cert=live_cap,
            execution_receipt_cert=receipt_cert,
            decision_time=decision_time,
            to_status="RELEASED",
            reason_code=submit_result.reason_code,
            aggregate_event_hash=_append_cap_transition_aggregate_event(
                live_cap_conn,
                command,
                receipt_cert,
                to_status="RELEASED",
                projection_status="RELEASED",
                reason_code=submit_result.reason_code,
                decision_time=decision_time,
            ),
        )
    elif submit_result.status in {"TIMEOUT_UNKNOWN", "POST_SUBMIT_UNKNOWN"}:
        _append_submit_unknown_aggregate_event(
            live_cap_conn,
            command,
            receipt_cert,
            submit_result=submit_result,
            decision_time=decision_time,
        )
        return build_live_cap_transition_certificate(
            live_cap_cert=live_cap,
            execution_receipt_cert=receipt_cert,
            decision_time=decision_time,
            to_status="PENDING_RECONCILE",
            projection_status="RESERVED",
            reason_code=submit_result.reason_code,
            aggregate_event_hash=_append_cap_transition_aggregate_event(
                live_cap_conn,
                command,
                receipt_cert,
                to_status="PENDING_RECONCILE",
                projection_status="RESERVED",
                reason_code=submit_result.reason_code,
                decision_time=decision_time,
            ),
        )
    raise ValueError(f"unsupported submit result status for live cap transition: {submit_result.status!r}")


def _append_venue_submit_attempted_aggregate_event(
    conn: sqlite3.Connection | None,
    command: DecisionCertificate,
    *,
    decision_time: datetime,
) -> str | None:
    if conn is None:
        return None
    aggregate_id = str(command.payload.get("aggregate_id") or "")
    if not aggregate_id:
        return None
    event = LiveOrderAggregateLedger(conn).append_event(
        aggregate_id=aggregate_id,
        event_type="VenueSubmitAttempted",
        payload={
            "event_id": command.payload["event_id"],
            "final_intent_id": command.payload["final_intent_id"],
            "execution_command_id": command.payload["execution_command_id"],
            "idempotency_key": command.payload.get("idempotency_key"),
        },
        occurred_at=decision_time,
        source_authority="existing_executor",
    )
    return event.event_hash


def _append_submit_terminal_aggregate_event(
    conn: sqlite3.Connection | None,
    command: DecisionCertificate,
    receipt_cert: DecisionCertificate,
    *,
    submit_result: EventBoundExecutorSubmitResult,
    decision_time: datetime,
) -> str | None:
    if conn is None:
        return None
    aggregate_id = str(command.payload.get("aggregate_id") or "")
    if not aggregate_id:
        return None
    if submit_result.status == "SUBMITTED":
        event = LiveOrderAggregateLedger(conn).append_event(
            aggregate_id=aggregate_id,
            event_type="VenueSubmitAcknowledged",
            payload={
                "event_id": command.payload["event_id"],
                "final_intent_id": command.payload["final_intent_id"],
                "execution_command_id": command.payload["execution_command_id"],
                "execution_receipt_hash": receipt_cert.certificate_hash,
                "venue_order_id": submit_result.venue_order_id,
                "venue_ack_received": submit_result.venue_ack_received,
                "raw_response_hash": submit_result.raw_response_hash,
            },
            occurred_at=decision_time,
            source_authority="existing_executor",
        )
        return event.event_hash
    if submit_result.status in {"REJECTED", "PRE_SUBMIT_ERROR"}:
        event = LiveOrderAggregateLedger(conn).append_event(
            aggregate_id=aggregate_id,
            event_type="SubmitRejected",
            payload={
                "event_id": command.payload["event_id"],
                "final_intent_id": command.payload["final_intent_id"],
                "execution_command_id": command.payload["execution_command_id"],
                "execution_receipt_hash": receipt_cert.certificate_hash,
                "reason_code": submit_result.reason_code,
                "venue_order_id": submit_result.venue_order_id,
                "raw_response_hash": submit_result.raw_response_hash,
            },
            occurred_at=decision_time,
            source_authority="existing_executor",
        )
        return event.event_hash
    return None


def _append_cap_transition_aggregate_event(
    conn: sqlite3.Connection | None,
    command: DecisionCertificate,
    receipt_cert: DecisionCertificate,
    *,
    to_status: str,
    projection_status: str,
    reason_code: str,
    decision_time: datetime,
) -> str | None:
    if conn is None:
        return None
    aggregate_id = str(command.payload.get("aggregate_id") or "")
    if not aggregate_id:
        return None
    event = LiveOrderAggregateLedger(conn).append_event(
        aggregate_id=aggregate_id,
        event_type="CapTransitioned",
        payload={
            "event_id": command.payload["event_id"],
            "final_intent_id": command.payload["final_intent_id"],
            "execution_command_id": command.payload["execution_command_id"],
            "execution_receipt_hash": receipt_cert.certificate_hash,
            "to_status": to_status,
            "projection_status": projection_status,
            "transition_reason": reason_code,
        },
        occurred_at=decision_time,
        source_authority="live_cap_ledger",
    )
    return event.event_hash


def _append_submit_unknown_aggregate_event(
    conn: sqlite3.Connection | None,
    command: DecisionCertificate,
    receipt_cert: DecisionCertificate,
    *,
    submit_result: EventBoundExecutorSubmitResult,
    decision_time: datetime,
) -> str | None:
    if conn is None:
        return None
    aggregate_id = str(command.payload.get("aggregate_id") or "")
    if not aggregate_id:
        return None
    event = LiveOrderAggregateLedger(conn).append_event(
        aggregate_id=aggregate_id,
        event_type="SubmitUnknown",
        payload={
            "event_id": command.payload["event_id"],
            "final_intent_id": command.payload["final_intent_id"],
            "execution_command_id": command.payload["execution_command_id"],
            "execution_receipt_hash": receipt_cert.certificate_hash,
            "submit_status": submit_result.status,
            "reason_code": submit_result.reason_code,
            "venue_call_started": submit_result.venue_call_started,
            "side_effect_known": submit_result.side_effect_known,
            "reconciliation_followup_required": submit_result.reconciliation_followup_required,
        },
        occurred_at=decision_time,
        source_authority="existing_executor",
    )
    return event.event_hash


def _passive_maker_context_from_authorities(
    *,
    actionable: DecisionCertificate,
    quote_feasibility_cert: DecisionCertificate,
    executable_snapshot_cert: DecisionCertificate,
    decision_time: datetime,
) -> dict[str, object]:
    quote_payload = quote_feasibility_cert.payload
    best_bid = quote_payload.get("best_bid")
    best_ask = quote_payload.get("best_ask")
    if best_bid in (None, "") or best_ask in (None, ""):
        raise ValueError("QUOTE_FEASIBILITY_BID_ASK_REQUIRED")
    quote_available_at = quote_feasibility_cert.header.source_available_at
    snapshot_available_at = executable_snapshot_cert.header.source_available_at
    if quote_available_at is None:
        raise ValueError("QUOTE_FEASIBILITY_SOURCE_AVAILABLE_AT_REQUIRED")
    if snapshot_available_at is None:
        raise ValueError("EXECUTABLE_SNAPSHOT_SOURCE_AVAILABLE_AT_REQUIRED")
    spread_usd = max(0.0, float(best_ask) - float(best_bid))
    p_fill_lcb = float(actionable.payload.get("p_fill_lcb") or 0.0)
    # Adverse-selection proxy (§4 Dim 4.2): A ~= recent belief volatility x spread.
    # Belief volatility is sourced from the actionable's prior-cycle posterior when
    # available (|q_posterior - q_posterior_prev|); absent a trustworthy prior we
    # fall back to A = 0, which biases the §2 boundary toward maker (the
    # conservative, documented default — never fabricate adverse cost we can't
    # source). queue_depth_ahead uses the quote's visible depth when present.
    adverse_selection_score = _adverse_selection_proxy(
        actionable_payload=actionable.payload,
        spread_usd=spread_usd,
    )
    queue_depth_ahead = _queue_depth_ahead_from_quote(quote_payload)
    return {
        "spread_usd": spread_usd,
        "quote_age_ms": int(max(0.0, (decision_time - quote_available_at).total_seconds() * 1000.0)),
        "expected_fill_probability": str(max(min(p_fill_lcb, 1.0), 0.0001)),
        "queue_depth_ahead": queue_depth_ahead,
        "adverse_selection_score": adverse_selection_score,
        "orderbook_hash_age_ms": int(max(0.0, (decision_time - snapshot_available_at).total_seconds() * 1000.0)),
        "best_bid": float(best_bid),
        "best_ask": float(best_ask),
    }


def _adverse_selection_proxy(*, actionable_payload: Mapping[str, object], spread_usd: float) -> str | None:
    """A ~= |q_posterior - q_posterior_prev| * spread (Dim 4.2 cheap proxy).

    Returns None (the conservative default that biases toward maker) when no
    trustworthy prior-cycle belief is available — Fitz #4: do not fabricate an
    adverse-selection cost from data we do not have.
    """
    q_now = actionable_payload.get("q_live")
    q_prev = actionable_payload.get("q_live_prev_cycle")
    if q_now in (None, "") or q_prev in (None, ""):
        return None
    try:
        belief_move = abs(float(q_now) - float(q_prev))
    except (TypeError, ValueError):
        return None
    return str(max(0.0, belief_move * float(spread_usd)))


def _queue_depth_ahead_from_quote(quote_payload: Mapping[str, object]) -> str | None:
    """Best-effort queue-ahead size from the quote's visible depth, else None."""
    for key in ("queue_depth_ahead", "bid_queue_size", "visible_depth"):
        raw = quote_payload.get(key)
        if raw in (None, ""):
            continue
        try:
            return str(max(0.0, float(raw)))
        except (TypeError, ValueError):
            continue
    return None


def _executable_market_context_from_snapshot(snapshot) -> dict[str, object] | None:
    if snapshot is None:
        return None
    context: dict[str, object] = {}
    for field in ("event_id", "event_slug", "market_end_at", "market_close_at"):
        value = getattr(snapshot, field, None)
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        if value not in (None, ""):
            context[field] = value
    return context or None


def _select_edli_order_mode(
    *,
    actionable_payload: Mapping[str, object],
    quote_payload: Mapping[str, object],
    best_bid: float | None,
    best_ask: float | None,
    executable_snapshot: DecisionCertificate,
    canary_force_taker: bool = False,
    canary_edge_floor: float | None = None,
) -> str:
    """Select MAKER/TAKER for the entry per design §1-§2 (governor + EV override).

    Authority order (Fitz #4 provenance):
      1. Canary knob (§7): when ``canary_force_taker`` and the post-cross edge
         clears the 5c floor, FORCE taker. This bypasses the governor's
         maker/taker CHOICE but never its NO_TRADE/risk gates (those gate the
         candidate upstream before this point and remain in force).
      2. Governor (§1): consult ``maker_or_taker`` when a global governor is
         configured. NO_TRADE is impossible here (the candidate already cleared
         the gates) but is mapped to MAKER (the conservative resting default).
      3. EV override (§2): even when the governor says MAKER, cross if the
         economic boundary ``e*(1-P_fill) >= s/2*(1+P_fill) + f - A`` holds.

    Defaults to MAKER (the pre-change passive law) whenever inputs are missing —
    a partial/uncertain signal must never silently produce a taker cross.
    """
    side = "BUY" if str(actionable_payload.get("direction")) in {"buy_yes", "buy_no"} else "SELL"
    reservation = _optional_float(actionable_payload.get("c_fee_adjusted"))

    # --- 1. Canary force-taker (with 5c post-cross edge floor) ---
    if canary_force_taker:
        floor = 0.05 if canary_edge_floor is None else float(canary_edge_floor)
        post_cross_edge = _post_cross_edge(
            actionable_payload=actionable_payload, best_bid=best_bid, best_ask=best_ask, side=side
        )
        if post_cross_edge is not None and post_cross_edge >= floor:
            return "TAKER"
        # Floor not met: fall through to governor/EV (do NOT force a sub-floor cross).

    # --- 2. Governor maker_or_taker ---
    governor_mode = _governor_mode_for_snapshot(executable_snapshot)
    if governor_mode == "TAKER":
        return "TAKER"

    # --- 3. Economic EV override (§2 boundary) ---
    if _ev_boundary_favors_cross(
        actionable_payload=actionable_payload,
        quote_payload=quote_payload,
        best_bid=best_bid,
        best_ask=best_ask,
        reservation=reservation,
        side=side,
    ):
        return "TAKER"
    return "MAKER"


def _governor_mode_for_snapshot(executable_snapshot: DecisionCertificate) -> str:
    """Return the global governor's maker/taker mode, or MAKER if unavailable.

    The candidate has already passed the upstream NO_TRADE/risk gates, so a
    NO_TRADE here (or an unconfigured governor) maps to the conservative MAKER
    resting default rather than blocking — the design routes order-TYPE only.
    """
    try:
        from src.risk_allocator import select_global_order_type

        order_type = select_global_order_type(executable_snapshot.payload)
    except Exception:
        return "MAKER"
    return "TAKER" if str(order_type).strip().upper() in {"FOK", "FAK"} else "MAKER"


def _order_will_rest_at_admitted_price(snapshot_payload: Any) -> bool:
    """True iff this entry will rest as a MAKER order (GTC/GTD), not cross as a taker.

    Governs whether the S6 submit-recapture gate applies the PRICE_MOVED ceiling
    (2026-06-10). A resting maker order pays its OWN limit (downstream
    ``compute_native_limit_price`` = min(held_prob, ask) - offset, at the admitted
    boundary) and rests when the ask moves away — it never crosses, never chases,
    never pays the recaptured ask, so the price-moved ceiling must NOT abort it.
    The PRICE_MOVED ceiling is a TAKER-only protection: it bounds what an immediate
    crossing fill pays.

    Fail-direction: this MIRRORS the downstream order-mode authority
    ``_governor_mode_for_snapshot`` (which maps governor unconfigured / NO_TRADE /
    error -> the conservative resting MAKER default, and only routes TAKER when the
    governor EXPLICITLY forces FOK/FAK on degraded heartbeat / shallow depth / near
    close). The live default IS resting GTC (venue_order_facts: order_type GTC), so
    aligning the gate to "rest unless the governor forces taker" matches both the
    live maker reality and the maker design — and removes the observed sub-3¢
    false-abort churn.

    Skipping the S6 ceiling on the maker path NEVER removes the taker chase bound:
    when the order actually crosses (governor TAKER, or a later ``_select_order_mode``
    EV-override), the intent build enforces ``TAKER_BUY_TOUCH_EXCEEDS_RESERVATION`` /
    ``TAKER_SELL_TOUCH_BELOW_RESERVATION`` against the admitted reservation on the
    fresh crossing price — the authoritative no-chase enforcement for taker fills.
    """
    try:
        from src.risk_allocator import select_global_order_type

        order_type = str(select_global_order_type(snapshot_payload) or "").strip().upper()
    except Exception:
        # Governor unconfigured / NO_TRADE -> conservative resting MAKER default,
        # mirroring _governor_mode_for_snapshot. The live path rests GTC here.
        return True
    # Explicit taker (degraded / shallow / near close) keeps the strict bounded
    # ceiling; any resting / unknown-but-non-taker type rests at admitted price.
    return order_type not in {"FOK", "FAK"}


def _post_cross_edge(
    *,
    actionable_payload: Mapping[str, object],
    best_bid: float | None,
    best_ask: float | None,
    side: str,
) -> float | None:
    """q_posterior - far_touch - fee  (the §7 canary edge floor numerator)."""
    q = _optional_float(actionable_payload.get("q_live"))
    fee = _optional_float(actionable_payload.get("fee_rate")) or 0.0
    if q is None:
        return None
    if side == "BUY":
        if best_ask is None:
            return None
        return q - best_ask - fee
    if best_bid is None:
        return None
    return best_bid - q - fee


def _ev_boundary_favors_cross(
    *,
    actionable_payload: Mapping[str, object],
    quote_payload: Mapping[str, object],
    best_bid: float | None,
    best_ask: float | None,
    reservation: float | None,
    side: str,
) -> bool:
    """§2 boundary: cross iff e*(1-P_fill) >= s/2*(1+P_fill) + f - A.

    Conservative: returns False (rest as maker) on any missing input.
    """
    e = _optional_float(actionable_payload.get("trade_score"))
    if e is None:
        e = _optional_float(actionable_payload.get("q_live"))
        c = _optional_float(actionable_payload.get("c_fee_adjusted"))
        if e is not None and c is not None:
            e = (e - c) if side == "BUY" else (c - e)
    if e is None or best_bid is None or best_ask is None:
        return False
    spread = max(0.0, best_ask - best_bid)
    p_fill = _optional_float(actionable_payload.get("p_fill_lcb"))
    if p_fill is None:
        return False
    p_fill = max(0.0, min(1.0, p_fill))
    fee = _optional_float(actionable_payload.get("fee_rate")) or 0.0
    adverse = _adverse_selection_proxy(actionable_payload=actionable_payload, spread_usd=spread)
    a = float(adverse) if adverse is not None else 0.0
    lhs = e - (e * p_fill)
    rhs = (spread / 2.0) * (1.0 + p_fill) + fee - a
    return lhs >= rhs


def _release_live_cap_certificate(
    live_cap: DecisionCertificate,
    live_cap_conn: sqlite3.Connection | None,
    *,
    reason: str,
) -> None:
    if live_cap_conn is None:
        return
    from src.events.live_cap import LiveCapError, LiveCapLedger

    try:
        LiveCapLedger(live_cap_conn).release(str(live_cap.payload["usage_id"]), reason)
    except LiveCapError:
        return


def _required_cert(certs: tuple[DecisionCertificate, ...], certificate_type: str) -> DecisionCertificate:
    for cert in certs:
        if cert.certificate_type == certificate_type:
            return cert
    raise ValueError(f"missing required certificate: {certificate_type}")


def _require_snapshot_hash(snapshot: object) -> str:
    """Return executable_snapshot_hash from a hydrated snapshot; raise if absent."""
    if snapshot is None:
        raise ValueError("EXECUTABLE_SNAPSHOT_HASH_UNAVAILABLE: snapshot not found in trade DB")
    h = snapshot.executable_snapshot_hash  # type: ignore[union-attr]
    if not h:
        raise ValueError("EXECUTABLE_SNAPSHOT_HASH_UNAVAILABLE: hash is empty")
    return h


def _require_cost_basis(
    snapshot: object,
    *,
    direction: str,
    size_usd: float,
    execution_price: "ExecutionPrice",
) -> "ExecutableCostBasis":
    """Build canonical ExecutableCostBasis from a hydrated snapshot.

    Uses the fee-adjusted execution_price.value as final_limit_price /
    expected_fill_price_before_fee — for the no-submit passive path the limit
    price IS the pre-fee ask (fee is added on top inside from_snapshot).
    We pass fee_adjusted_execution_price to let from_snapshot verify consistency.

    Raises a clear COST_BASIS_HASH_UNAVAILABLE error on any failure so the cert
    pipeline fails closed rather than emitting a blank hash.
    """
    if snapshot is None:
        raise ValueError("COST_BASIS_HASH_UNAVAILABLE: snapshot not found in trade DB")
    try:
        # For the no-submit adapter path the limit is a passive post-only order.
        # execution_price.value is fee-adjusted; snapshot.orderbook_top_ask (for
        # buy) is the canonical pre-fee price used as limit/expected_fill.
        # Fall back to execution_price.value if top_ask/bid unavailable.
        snap = snapshot  # type: ignore[union-attr]
        # Strip selected_outcome_token_id / outcome_label so from_snapshot does not
        # raise a direction-mismatch when the snapshot row was fetched for the other
        # side of the same condition (the adapter reuses one row for both buy_yes and
        # buy_no proofs of the same condition).
        if snap.selected_outcome_token_id or snap.outcome_label:
            snap = dataclass_replace(snap, selected_outcome_token_id=None, outcome_label=None)  # type: ignore[arg-type]
        if direction.startswith("buy_"):
            pre_fee_limit = (
                snap.orderbook_top_ask
                if snap.orderbook_top_ask is not None
                else Decimal(str(execution_price.value))
            )
        else:
            pre_fee_limit = (
                snap.orderbook_top_bid
                if snap.orderbook_top_bid is not None
                else Decimal(str(execution_price.value))
            )
        pre_fee_limit = Decimal(str(pre_fee_limit))
        requested_size = Decimal(str(max(size_usd, 0.01)))
        return ExecutableCostBasis.from_snapshot(
            snapshot=snap,
            direction=direction,
            order_policy="post_only_passive_limit",
            requested_size_kind="notional_usd",
            requested_size_value=requested_size,
            final_limit_price=pre_fee_limit,
            expected_fill_price_before_fee=pre_fee_limit,
            depth_status="NOT_MARKETABLE_PASSIVE_LIMIT",
        )
    except Exception as exc:
        raise ValueError(f"COST_BASIS_HASH_UNAVAILABLE: {exc}") from exc


def _build_no_submit_proof_bundle_from_adapter_evidence(
    *,
    event: OpportunityEvent,
    payload: dict[str, object],
    decision_time: datetime,
    family,
    family_topology_rows: list[dict[str, Any]],
    family_snapshot_rows: list[dict[str, Any]],
    selected_snapshot_row: dict[str, Any],
    trade_conn: sqlite3.Connection,
    forecast_conn: sqlite3.Connection,
    calibration_conn: sqlite3.Connection,
    proof: _CandidateProof,
    raw_receipt: dict[str, Any],
    fdr,
    kelly,
    risk,
    bankroll_usd: float,
    kelly_multiplier: float,
) -> NoSubmitProofBundle:
    event_clock = EvidenceClock(
        source_available_at=_parse_utc(event.available_at) or decision_time,
        agent_received_at=_parse_utc(event.received_at) or decision_time,
        persisted_at=_parse_utc(event.created_at) or decision_time,
    )
    decision_clock = EvidenceClock(decision_time, decision_time, decision_time)
    quote_clock = _evidence_clock_from_row(selected_snapshot_row, fallback=decision_time)
    forecast_payload, forecast_clock = _forecast_authority_payload_and_clock(
        forecast_conn,
        event=event,
        family=family,
        payload=payload,
        decision_time=decision_time,
    )
    calibration_payload, calibration_clock = _calibration_authority_payload_and_clock(
        calibration_conn,
        event=event,
        family=family,
        payload=payload,
        forecast_payload=forecast_payload,
        decision_time=decision_time,
    )
    projection = {
        "event_id": raw_receipt.get("event_id"),
        "final_intent_id": raw_receipt.get("final_intent_id"),
        "side_effect_status": raw_receipt.get("side_effect_status"),
        "proof_accepted": raw_receipt.get("proof_accepted"),
        "submitted": raw_receipt.get("submitted"),
        "executable_snapshot_id": raw_receipt.get("executable_snapshot_id"),
    }
    projection["projection_hash"] = stable_hash(projection)
    condition_ids = tuple(str(row.get("condition_id") or "") for row in family_topology_rows)
    executable_snapshot_ids = tuple(sorted(str(row.get("snapshot_id") or "") for row in family_snapshot_rows))
    hypothesis_id = f"{family.family_id}:{proof.token_id}"
    execution_price = proof.execution_price
    topology_clock = _evidence_clock_from_rows(family_topology_rows)
    bin_labels_hash = stable_hash(tuple(str(candidate.bin.label) for candidate in family.candidates))
    bin_units = tuple(sorted({str(candidate.bin.unit) for candidate in family.candidates if candidate.bin.unit}))
    forecast_payload = {**forecast_payload, "bin_labels_hash": bin_labels_hash}
    market_analysis_config_hash = stable_hash(
        {
            "posterior_mode": MODEL_ONLY_POSTERIOR_MODE,
            "edge_bootstrap_n": edge_n_bootstrap(),
            "family_id": family.family_id,
        }
    )
    _hydrated_snapshot = get_snapshot(trade_conn, str(proof.executable_snapshot_id or ""))
    _canonical_cost_basis = _require_cost_basis(
        _hydrated_snapshot,
        direction=proof.direction,
        size_usd=kelly.size_usd,
        execution_price=execution_price,
    )
    # Align kelly_cost_basis_id in raw_receipt to the canonical cost_basis:{hash[:16]} form.
    # DecisionCompiler validates kelly.cost_basis_id == cost_model.cost_basis_id; both
    # must use the canonical form.
    raw_receipt["kelly_cost_basis_id"] = _canonical_cost_basis.cost_basis_id
    return NoSubmitProofBundle(
        final_intent_id=str(raw_receipt.get("final_intent_id") or ""),
        source_truth=AuthorityEvidence(
            claims.SOURCE_TRUTH,
            "source_truth",
            "source_truth",
            {
                "identity": event.source,
                "event_source": event.source,
                "event_type": event.event_type,
                "source_status": forecast_payload.get("reader_status"),
                "source_authority_id": "read_executable_forecast",
                "source_reason_code": forecast_payload.get("reader_reason_code"),
                "derived_from_certificate_type": claims.FORECAST_AUTHORITY,
                "derived_from_snapshot_id": forecast_payload.get("snapshot_id"),
                # WAVE-1 W1-T3: the reader-ELECTED executable source_run (may
                # differ from the causal event run, e.g. 00Z causal → 12Z
                # elected). Stamped unconditionally so the payload is
                # self-describing; the cert's dual-chain binding only consults it
                # when edli_v1.edli_source_run_dual_chain_enabled is ON (default
                # OFF → legacy single-chain equality, so the merge is inert).
                "derived_from_source_run_id": forecast_payload.get("source_run_id"),
                "derived_from_reader_status": forecast_payload.get("reader_status"),
                "completeness_status": payload.get("completeness_status"),
                "required_fields_present": payload.get("required_fields_present"),
                "required_steps_present": payload.get("required_steps_present"),
                "source_id": payload.get("source_id"),
                "source_run_id": payload.get("source_run_id"),
                "snapshot_id": payload.get("snapshot_id") or event.causal_snapshot_id,
                "payload_hash": event.payload_hash,
                "causal_snapshot_id": event.causal_snapshot_id,
                "available_at": event.available_at,
                "received_at": event.received_at,
                "event_id": event.event_id,
            },
            event_clock,
            "zeus.events.source_truth_gate",
            algorithm_id="decision_kernel.source_truth.event_bound_adapter",
        ),
        market_topology=AuthorityEvidence(
            claims.MARKET_TOPOLOGY,
            "market_topology",
            "market_topology",
            {
                "identity": family.family_id,
                "family_id": family.family_id,
                "condition_ids": condition_ids,
                "candidate_count": len(tuple(family.candidates)),
                "source_table": "market_events",
                "event_id": event.event_id,
            },
            topology_clock,
            "zeus.forecasts.market_events",
            algorithm_id="decision_kernel.topology.event_bound_adapter",
        ),
        family_closure=AuthorityEvidence(
            claims.FAMILY_CLOSURE,
            "family_closure",
            "family_closure",
            {
                "identity": family.family_id,
                "family_id": family.family_id,
                "condition_ids": condition_ids,
                "yes_token_ids": tuple(family.yes_token_ids),
                "no_token_ids": tuple(family.no_token_ids),
                "sibling_hypothesis_count": len(tuple(family.yes_token_ids)) + len(tuple(family.no_token_ids)),
                "family_complete": True,
                "bin_labels_hash": bin_labels_hash,
                "bin_units": bin_units,
                "metric": family.metric,
                "target_date": family.target_date,
                "event_id": event.event_id,
            },
            topology_clock,
            "zeus.events.candidate_binding",
            algorithm_id="decision_kernel.family_closure.event_bound_adapter",
        ),
        forecast_authority=AuthorityEvidence(
            claims.FORECAST_AUTHORITY,
            "forecast_authority",
            "forecast_authority",
            forecast_payload,
            forecast_clock,
            "zeus.data.executable_forecast_reader",
            algorithm_id="decision_kernel.forecast_authority.event_bound_adapter",
        ),
        calibration=AuthorityEvidence(
            claims.CALIBRATION,
            "calibration",
            "calibration",
            calibration_payload,
            calibration_clock,
            "zeus.calibration.manager",
            algorithm_id="decision_kernel.calibration.event_bound_adapter",
        ),
        model_config=AuthorityEvidence(
            claims.MODEL_CONFIG,
            "model_config",
            "model_config",
            {
                "identity": "event_bound_no_submit_v1",
                "posterior_mode": MODEL_ONLY_POSTERIOR_MODE,
                "edge_bootstrap_n": edge_n_bootstrap(),
                "kelly_multiplier": kelly_multiplier,
                "market_analysis_config_hash": market_analysis_config_hash,
                "calibration_input_space": calibration_payload.get("input_space"),
                "calibrator_model_key": calibration_payload.get("calibrator_model_key"),
                "calibrator_model_hash": calibration_payload.get("model_hash"),
            },
            decision_clock,
            "zeus.config.settings",
            algorithm_id="decision_kernel.model_config.event_bound_adapter",
        ),
        belief=AuthorityEvidence(
            claims.BELIEF,
            "belief",
            "belief",
            {
                "identity": hypothesis_id,
                "q_live": proof.q_posterior,
                "q_lcb_5pct": proof.q_lcb_5pct,
                "p_value": proof.p_value,
                "passed_prefilter": proof.passed_prefilter,
                "forecast_snapshot_id": forecast_payload.get("snapshot_id"),
                "calibrator_model_key": calibration_payload.get("calibrator_model_key"),
                "calibrator_model_hash": calibration_payload.get("model_hash"),
                "p_cal_vector_hash": proof.p_cal_vector_hash,
                "p_live_vector_hash": proof.p_live_vector_hash,
                "p_cal_hash": proof.p_cal_vector_hash,
                "p_live_hash": proof.p_live_vector_hash,
                "bin_labels_hash": bin_labels_hash,
                "members_json_hash": forecast_payload.get("members_json_hash"),
                "market_analysis_config_hash": market_analysis_config_hash,
                "bootstrap_n": edge_n_bootstrap(),
                "unit": forecast_payload.get("unit"),
                "unit_authority_source": forecast_payload.get("unit_authority_source"),
            },
            forecast_clock,
            "zeus.strategy.market_analysis_family_scan",
            algorithm_id="decision_kernel.belief.event_bound_adapter",
        ),
        executable_snapshot=AuthorityEvidence(
            claims.EXECUTABLE_SNAPSHOT,
            "executable_snapshot",
            "executable_snapshot",
            {
                "identity": proof.executable_snapshot_id,
                "selected_snapshot_id": proof.executable_snapshot_id,
                "family_snapshot_ids": executable_snapshot_ids,
                "condition_id": raw_receipt.get("condition_id"),
                "token_id": raw_receipt.get("token_id"),
                "cost_basis_id": raw_receipt.get("kelly_cost_basis_id"),
                "orderbook_hash": _hash_jsonish(selected_snapshot_row.get("orderbook_depth_json") or selected_snapshot_row.get("orderbook_depth_jsonb")),
                "fee_details_hash": _hash_jsonish(selected_snapshot_row.get("fee_details_json") or selected_snapshot_row.get("fee_details")),
                "min_tick_size": str(_hydrated_snapshot.min_tick_size),
                "min_order_size": str(_hydrated_snapshot.min_order_size),
                "neg_risk": bool(_hydrated_snapshot.neg_risk),
                "captured_at": selected_snapshot_row.get("captured_at"),
                "freshness_deadline": selected_snapshot_row.get("freshness_deadline"),
                "active": selected_snapshot_row.get("active"),
                "closed": selected_snapshot_row.get("closed"),
                "executable_snapshot_hash": _require_snapshot_hash(_hydrated_snapshot),
            },
            quote_clock,
            "zeus.trades.executable_market_snapshots",
            algorithm_id="decision_kernel.executable_snapshot.event_bound_adapter",
        ),
        quote_feasibility=AuthorityEvidence(
            claims.QUOTE_FEASIBILITY,
            "quote_feasibility",
            "quote_feasibility",
            {
                "identity": hypothesis_id,
                "condition_id": raw_receipt.get("condition_id"),
                "token_id": raw_receipt.get("token_id"),
                "direction": proof.direction,
                "native_side": _native_side_for_direction(proof.direction),
                "cost_source": _native_cost_source_for_direction(proof.direction),
                "quote_source_kind": "executable_market_snapshot_native_book",
                "forbidden_cost_source": False,
                "selected_token_id": proof.token_id,
                # Top-of-book is the SAME causally-bound, freshness-gated selected_snapshot_row
                # that already passed entry gates and from which quote_clock
                # (source_available_at) is derived. The passive-maker consumer
                # (_passive_maker_context_from_authorities) requires best_bid/best_ask on this
                # cert; the production payload previously omitted them, so the live cert build
                # failed QUOTE_FEASIBILITY_BID_ASK_REQUIRED for every candidate. No quote
                # newer than decision_time and no relaxed staleness bound is introduced here.
                "best_bid": _optional_float(selected_snapshot_row.get("orderbook_top_bid")),
                "best_ask": _optional_float(selected_snapshot_row.get("orderbook_top_ask")),
                "quote_depth_hash": _hash_jsonish(selected_snapshot_row.get("orderbook_depth_json") or selected_snapshot_row.get("orderbook_depth_jsonb")),
                "p_fill_lcb_policy_id": "edli_v1.no_submit_visible_depth_fill_lcb",
                "native_quote_available": proof.native_quote_available,
                "execution_price_type": execution_price.__class__.__name__ if execution_price is not None else None,
                "execution_price_value": execution_price.value if execution_price is not None else None,
                "fee_deducted": execution_price.fee_deducted if execution_price is not None else None,
                "p_fill_lcb": proof.p_fill_lcb,
            },
            quote_clock,
            "zeus.strategy.live_inference.executable_cost",
            algorithm_id="decision_kernel.quote_feasibility.event_bound_adapter",
        ),
        cost_model=AuthorityEvidence(
            claims.COST_MODEL,
            "cost_model",
            "cost_model",
            {
                "identity": _canonical_cost_basis.cost_basis_id,
                "cost_basis_id": _canonical_cost_basis.cost_basis_id,
                "cost_basis_hash": _canonical_cost_basis.cost_basis_hash,
                "condition_id": raw_receipt.get("condition_id"),
                "token_id": raw_receipt.get("token_id"),
                "cost_source": _native_cost_source_for_direction(proof.direction),
                "quote_source_kind": "executable_market_snapshot_native_book",
                "forbidden_cost_source": False,
                "execution_price_type": execution_price.__class__.__name__ if execution_price is not None else None,
                "price_fee_deducted": execution_price.fee_deducted if execution_price is not None else None,
                "c_fee_adjusted": raw_receipt.get("c_fee_adjusted"),
                "c_cost_95pct": proof.c_cost_95pct,
            },
            quote_clock,
            "zeus.contracts.execution_price",
            algorithm_id="decision_kernel.cost_model.event_bound_adapter",
        ),
        pre_trade_evidence=AuthorityEvidence(
            claims.PRE_TRADE_EVIDENCE,
            "pre_trade_evidence",
            "pre_trade_evidence",
            {
                "identity": hypothesis_id,
                "quote_edge_bound": proof.trade_score,
                "conditional_edge_given_fill": None,
                "no_submit_trade_score_evidence": proof.trade_score,
                "actionable_trade_score": 0.0,
            },
            decision_clock,
            "zeus.strategy.market_analysis_family_scan",
            algorithm_id="decision_kernel.pre_trade_evidence.event_bound_adapter",
        ),
        candidate_evidence=AuthorityEvidence(
            claims.CANDIDATE_EVIDENCE,
            "candidate_evidence",
            "candidate_evidence",
            {
                "identity": hypothesis_id,
                "candidate_id": raw_receipt.get("candidate_id"),
                "family_id": family.family_id,
                "condition_id": raw_receipt.get("condition_id"),
                "bin_label": raw_receipt.get("bin_label"),
                "selected_token_id": proof.token_id,
                "direction": proof.direction,
                "hypothesis_id": hypothesis_id,
            },
            decision_clock,
            "zeus.events.decision_engine",
            algorithm_id="decision_kernel.candidate_evidence.event_bound_adapter",
        ),
        testing_protocol=AuthorityEvidence(
            claims.TESTING_PROTOCOL,
            "testing_protocol",
            "testing_protocol",
            {
                "identity": family.family_id,
                "testing_protocol_id": f"edli_testing:{family.family_id}",
                "family_id": family.family_id,
                "mode": "FIXED_WINDOW_BH",
                "optional_stopping_valid": True,
                "sibling_hypothesis_count": fdr.attempted_hypotheses,
            },
            decision_clock,
            "zeus.strategy.fdr_filter",
            algorithm_id="decision_kernel.testing_protocol.event_bound_adapter",
        ),
        fdr=AuthorityEvidence(
            claims.FDR,
            "fdr",
            "fdr",
            {
                "identity": fdr.fdr_family_id,
                "fdr_family_id": fdr.fdr_family_id,
                "selected_hypotheses": tuple(fdr.selected_hypotheses),
                "selected_post_fdr": tuple(fdr.selected_post_fdr),
                "fdr_hypothesis_count": fdr.attempted_hypotheses,
                "edge_bootstrap_n": edge_n_bootstrap(),
                "passed": fdr.passed,
            },
            decision_clock,
            "zeus.strategy.fdr_filter",
            algorithm_id="decision_kernel.fdr.event_bound_adapter",
        ),
        kelly_dry_run=AuthorityEvidence(
            claims.KELLY_DRY_RUN,
            "kelly_dry_run",
            "kelly_dry_run",
            {
                "identity": kelly.kelly_decision_id,
                "kelly_decision_id": kelly.kelly_decision_id,
                "kelly_size_usd": kelly.size_usd,
                "bankroll_usd": bankroll_usd,
                "kelly_multiplier": kelly_multiplier,
                "cost_basis_id": raw_receipt.get("kelly_cost_basis_id"),
                "execution_price_type": execution_price.__class__.__name__ if execution_price is not None else None,
                "price_fee_deducted": execution_price.fee_deducted if execution_price is not None else None,
                "passed": kelly.passed,
            },
            decision_clock,
            "zeus.strategy.kelly",
            algorithm_id="decision_kernel.kelly.event_bound_adapter",
        ),
        risk_level=AuthorityEvidence(
            claims.RISK_LEVEL,
            "risk_level",
            "risk_level",
            {
                "identity": risk.risk_decision_id,
                "risk_decision_id": risk.risk_decision_id,
                "risk_level": risk.level.name,
                "passed": risk.passed,
                "final_intent_id": raw_receipt.get("final_intent_id"),
            },
            decision_clock,
            "zeus.riskguard.risk_level",
            algorithm_id="decision_kernel.risk.event_bound_adapter",
        ),
        no_submit_projection=projection,
    )


def _forecast_authority_payload_and_clock(
    conn: sqlite3.Connection,
    *,
    event: OpportunityEvent,
    family,
    payload: dict[str, object],
    decision_time: datetime,
) -> tuple[dict[str, Any], EvidenceClock]:
    allow_latest = event.event_type == "DAY0_EXTREME_UPDATED"
    snapshot = _forecast_snapshot_row_for_event(
        conn,
        event=event,
        family=family,
        allow_latest=allow_latest,
        decision_time=decision_time,
    )
    if snapshot is None:
        raise ValueError("FORECAST_AUTHORITY_EVIDENCE_MISSING:snapshot")
    source_run_id = _nonnull(snapshot.get("source_run_id") or payload.get("source_run_id"))
    source_run_table = _authority_table_ref(conn, "source_run")
    coverage_table = _authority_table_ref(conn, "source_run_coverage")
    if not source_run_id or source_run_table is None or coverage_table is None:
        raise ValueError("FORECAST_AUTHORITY_EVIDENCE_MISSING:scope")
    source_run = _row_by_id(conn, source_run_table, "source_run_id", source_run_id)
    if source_run is None:
        raise ValueError("FORECAST_AUTHORITY_EVIDENCE_MISSING:source_run")
    coverage = _coverage_row_for_snapshot(
        conn,
        coverage_table,
        source_run_id=source_run_id,
        family=family,
        snapshot=snapshot,
    )
    if coverage is None:
        raise ValueError("FORECAST_AUTHORITY_EVIDENCE_MISSING:coverage")
    result = _read_executable_forecast_bundle_result(
        conn,
        snapshot=snapshot,
        source_run=source_run,
        coverage=coverage,
        event=event,
        family=family,
        decision_time=decision_time,
    )
    if not result.ok or result.bundle is None:
        raise ValueError(f"FORECAST_AUTHORITY_EVIDENCE_MISSING:{result.reason_code}")
    evidence = result.bundle.evidence
    if not tuple(evidence.applied_validations):
        raise ValueError("FORECAST_AUTHORITY_VALIDATIONS_MISSING")
    unit = _snapshot_unit(snapshot, payload)
    city_config = runtime_cities_by_name().get(family.city)
    if city_config is None:
        raise ValueError(f"FORECAST_AUTHORITY_EVIDENCE_MISSING:city:{family.city}")
    members_json_hash = _snapshot_members_json_hash(snapshot)
    # horizon_profile is NOT a column on ensemble_snapshots and is not populated upstream
    # (forecast_calibration_domain.derive_phase2_keys_from_ens_result docstring). The calibrator
    # lookup DERIVES the horizon stratum from the forecast cycle (00/12 -> 'full', else 'short').
    # The forecast authority must carry that SAME derived value so the no-submit cert can enforce a
    # real calibration.horizon_profile == forecast.horizon_profile equality instead of silently
    # comparing a derived 'full' against a structural None (the live FORECAST horizon mismatch leak).
    from src.calibration.forecast_calibration_domain import derive_phase2_keys_from_ens_result

    _, _, derived_horizon_profile = derive_phase2_keys_from_ens_result(
        {
            "issue_time": _nonnull(
                evidence.source_issue_time
                or evidence.source_cycle_time
                or snapshot.get("source_issue_time")
                or snapshot.get("source_cycle_time")
                or payload.get("issue_time")
                or payload.get("source_cycle_time")
            ),
            "source_id": _nonnull(evidence.forecast_source_id or snapshot.get("source_id") or payload.get("source_id")),
            "horizon_profile": snapshot.get("horizon_profile"),
        }
    )
    ens_result = result.bundle.to_ens_result()
    payload_out = {
        "identity": str(result.bundle.snapshot.snapshot_id),
        "snapshot_id": str(result.bundle.snapshot.snapshot_id),
        "reader_authority": "read_executable_forecast",
        "reader_status": normalize_forecast_reader_status(result.status, result.reason_code) or result.status,
        "reader_reason_code": None if result.reason_code in {None, "", "OK", "LIVE_ELIGIBLE", "EXECUTABLE_FORECAST_READY"} else result.reason_code,
        "city": family.city,
        "target_date": family.target_date,
        "metric": family.metric,
        "temperature_metric": family.metric,
        "members_extrema_metric_identity": snapshot.get("temperature_metric"),
        "members_extrema_transform": _members_extrema_transform(family.metric),
        "members_json_source": "ensemble_snapshots.daily_extrema",
        "members_json_hash": members_json_hash,
        "target_local_date": family.target_date,
        "city_timezone": city_config.timezone,
        "settlement_unit": snapshot.get("settlement_unit"),
        "members_unit": snapshot.get("members_unit"),
        "unit": unit,
        "unit_authority_source": _snapshot_unit_authority_source(snapshot),
        "local_date_window_hash": stable_hash(
            {
                "city": snapshot.get("city"),
                "target_date": snapshot.get("target_date"),
                "temperature_metric": snapshot.get("temperature_metric"),
                "members_json_hash": members_json_hash,
                "local_day_start_utc": snapshot.get("local_day_start_utc"),
                "forecast_window_start_utc": snapshot.get("forecast_window_start_utc"),
                "forecast_window_end_utc": snapshot.get("forecast_window_end_utc"),
            }
        ),
        "forecast_source_id": evidence.forecast_source_id,
        "model": ens_result.get("model"),
        "model_family": ens_result.get("model"),
        "forecast_issue_time": ens_result.get("issue_time"),
        "forecast_valid_time": ens_result.get("valid_time"),
        "forecast_fetch_time": ens_result.get("fetch_time"),
        "forecast_available_at": ens_result.get("available_at"),
        "degradation_level": ens_result.get("degradation_level"),
        "forecast_source_role": ens_result.get("forecast_source_role"),
        "authority_tier": ens_result.get("authority_tier"),
        "decision_time": decision_time.astimezone(UTC).isoformat(),
        "decision_time_status": "OK",
        "first_member_observed_time": ens_result.get("first_member_observed_time"),
        "run_complete_time": ens_result.get("run_complete_time"),
        "forecast_data_version": evidence.forecast_data_version,
        "source_transport": evidence.source_transport,
        "source_cycle_time": evidence.source_cycle_time,
        "source_issue_time": evidence.source_issue_time,
        "horizon_profile": _nonnull(snapshot.get("horizon_profile")) or derived_horizon_profile,
        "source_run_id": evidence.source_run_id,
        "coverage_id": evidence.coverage_id,
        "producer_readiness_id": evidence.producer_readiness_id,
        "entry_readiness_id": evidence.entry_readiness_id,
        "input_snapshot_ids": tuple(str(item) for item in evidence.input_snapshot_ids),
        "raw_payload_hash": ens_result.get("raw_payload_hash"),
        "manifest_hash": evidence.manifest_hash,
        "required_steps": tuple(evidence.required_steps),
        "observed_steps": tuple(evidence.observed_steps),
        "expected_members": evidence.expected_members,
        "observed_members": evidence.observed_members,
        "source_run_status": evidence.source_run_status,
        "source_run_completeness_status": evidence.source_run_completeness_status,
        "coverage_completeness_status": evidence.coverage_completeness_status,
        "coverage_readiness_status": evidence.coverage_readiness_status,
        "applied_validations": tuple(evidence.applied_validations),
        "source_available_at": evidence.source_available_at,
        "fetch_started_at": evidence.fetch_started_at,
        "fetch_finished_at": evidence.fetch_finished_at,
        "captured_at": evidence.captured_at,
    }
    source_time = _parse_utc(evidence.source_available_at)
    agent_time = _parse_utc(evidence.fetch_finished_at) or _parse_utc(evidence.captured_at)
    persisted_time = (
        _parse_utc(source_run.get("imported_at"))
        or _parse_utc(coverage.get("computed_at"))
        or _parse_utc(evidence.captured_at)
    )
    if source_time is None or agent_time is None or persisted_time is None:
        raise ValueError("FORECAST_AUTHORITY_EVIDENCE_MISSING:clock")
    return payload_out, EvidenceClock(source_time, agent_time, persisted_time)


def _calibration_authority_payload_and_clock(
    calibration_conn: sqlite3.Connection,
    *,
    event: OpportunityEvent,
    family,
    payload: dict[str, object],
    forecast_payload: dict[str, Any],
    decision_time: datetime,
) -> tuple[dict[str, Any], EvidenceClock]:
    city = runtime_cities_by_name().get(family.city)
    if city is None:
        raise ValueError("CALIBRATION_AUTHORITY_EVIDENCE_MISSING:city")
    source_id = _nonnull(payload.get("source_id") or forecast_payload.get("forecast_source_id"))
    issue_time = _nonnull(
        payload.get("issue_time")
        or payload.get("source_cycle_time")
        or payload.get("cycle")
        or forecast_payload.get("source_issue_time")
        or forecast_payload.get("source_cycle_time")
    )
    if not source_id or not issue_time:
        raise ValueError("CALIBRATION_AUTHORITY_EVIDENCE_MISSING:forecast_provenance")
    from src.calibration.forecast_calibration_domain import derive_phase2_keys_from_ens_result
    from src.data.forecast_source_registry import calibration_source_id_for_lookup

    cycle, raw_source_id, horizon_profile = derive_phase2_keys_from_ens_result(
        {
            "issue_time": issue_time,
            "source_id": source_id,
            "horizon_profile": payload.get("horizon_profile") or forecast_payload.get("horizon_profile"),
        }
    )
    calibration_source_id = calibration_source_id_for_lookup(raw_source_id)
    if calibration_source_id is None:
        raise ValueError("CALIBRATION_AUTHORITY_EVIDENCE_MISSING:source_id")
    if not _table_exists(calibration_conn, "platt_models"):
        raise ValueError("CALIBRATION_AUTHORITY_EVIDENCE_MISSING:store")
    row, level, _model_data = _persisted_calibration_model_row_for_receipt(
        calibration_conn,
        city=city,
        target_date=str(family.target_date),
        temperature_metric=family.metric,
        cycle=cycle,
        source_id=calibration_source_id,
        horizon_profile=horizon_profile,
    )
    if row is None:
        model_key = (
            "identity_fallback_no_platt_bucket_v1:"
            f"{family.metric}:{city.cluster}:{str(family.target_date)}:"
            f"{cycle}:{calibration_source_id}:{horizon_profile}"
        )
        training_cutoff_raw = decision_time.astimezone(UTC).isoformat()
        payload_out = {
            "identity": model_key,
            "calibrator_model_key": model_key,
            "calibrator_version": model_key,
            "calibration_source_id": calibration_source_id,
            "raw_source_id": raw_source_id,
            "source_cycle": cycle,
            "horizon_profile": horizon_profile,
            "training_cutoff": training_cutoff_raw,
            "model_available_at": training_cutoff_raw,
            "model_materialized_at": training_cutoff_raw,
            "model_hash": _hash_jsonish(
                {
                    "model_key": model_key,
                    "calibration_method": "identity_missing_platt_bucket_v1",
                    "cluster": city.cluster,
                    "temperature_metric": family.metric,
                    "source_id": calibration_source_id,
                    "cycle": cycle,
                    "horizon_profile": horizon_profile,
                }
            ),
            "maturity_level": 4,
            "n_samples": 0,
            "input_space": "width_normalized_density",
            "authority": "IDENTITY_FALLBACK_NO_PLATT_BUCKET",
        }
        return payload_out, EvidenceClock(
            decision_time,
            decision_time,
            decision_time,
        )
    model_key = row.get("model_key")
    training_cutoff_raw = row.get("training_cutoff") or _date_cutoff_from_calibration_row(row)
    training_cutoff_time = _parse_utc(training_cutoff_raw)
    if training_cutoff_time is None:
        raise ValueError("CALIBRATION_AUTHORITY_EVIDENCE_MISSING:clock")
    materialized_at = row.get("recorded_at") or row.get("fitted_at")
    payload_out = {
        "identity": str(model_key or ""),
        "calibrator_model_key": model_key,
        "calibrator_version": row.get("model_key"),
        "calibration_source_id": calibration_source_id,
        "raw_source_id": raw_source_id,
        "source_cycle": cycle,
        "horizon_profile": horizon_profile,
        "training_cutoff": training_cutoff_raw,
        "model_available_at": training_cutoff_raw,
        "model_materialized_at": materialized_at,
        "model_hash": _hash_jsonish({
            "model_key": row.get("model_key"),
            "param_A": row.get("param_A"),
            "param_B": row.get("param_B"),
            "param_C": row.get("param_C"),
            "bootstrap_params_json": row.get("bootstrap_params_json"),
        }),
        "maturity_level": level,
        "n_samples": row.get("n_samples"),
        "input_space": row.get("input_space"),
        "authority": row.get("authority"),
    }
    return payload_out, EvidenceClock(
        training_cutoff_time,
        training_cutoff_time,
        training_cutoff_time,
    )


def _persisted_calibration_model_row_for_receipt(
    conn: sqlite3.Connection,
    *,
    city,
    target_date: str,
    temperature_metric: str,
    cycle: str | None,
    source_id: str | None,
    horizon_profile: str | None,
) -> tuple[dict[str, Any] | None, int, dict[str, Any] | None]:
    """Load calibration authority without fitting inside the live reactor.

    ``get_calibrator()`` may train a Platt model from historical pair rows on a
    cache miss. Receipt compilation is an authority read seam: it may use an
    already-persisted model or fail closed, but it must not perform runtime
    training while the scheduler is trying to emit liveness receipts.
    """

    if not _table_exists(conn, "platt_models"):
        return None, 4, None
    from src.calibration.manager import (
        _candidate_data_versions_for_metric_source,
        _low_live_min_decision_groups,
        _resolve_pin_for_bucket,
        maturity_level,
        season_from_date,
    )
    from src.calibration.store import load_platt_model

    season = season_from_date(target_date, lat=city.lat)
    cluster = city.cluster
    candidate_data_versions = _candidate_data_versions_for_metric_source(
        temperature_metric, source_id
    )
    primary_frozen, primary_model_key = _resolve_pin_for_bucket(
        temperature_metric, cluster, season, cycle
    )
    opendata_to_tigge_bridge = (
        len(candidate_data_versions) > 1
        and candidate_data_versions[0].startswith("ecmwf_opendata_")
    )
    selected_model_data: dict[str, Any] | None = None
    for data_version in candidate_data_versions:
        if opendata_to_tigge_bridge and data_version.startswith("tigge_"):
            lookup_cycle, lookup_source_id, lookup_horizon = "00", "tigge_mars", "full"
        else:
            lookup_cycle, lookup_source_id, lookup_horizon = cycle, source_id, horizon_profile
        model_data = load_platt_model(
            conn,
            temperature_metric=temperature_metric,
            cluster=cluster,
            season=season,
            data_version=data_version,
            frozen_as_of=primary_frozen,
            model_key=primary_model_key,
            cycle=lookup_cycle,
            source_id=lookup_source_id,
            horizon_profile=lookup_horizon,
        )
        if model_data is not None:
            selected_model_data = model_data
            break
    if selected_model_data is None:
        return None, 4, None
    model_key = selected_model_data.get("model_key")
    row = _calibration_model_row(conn, model_key=model_key)
    if row is None:
        return None, 4, None
    if row.get("calibration_method") == "identity_full_transport_v1":
        return row, 1, selected_model_data
    n_samples = int(row.get("n_samples") or 0)
    if temperature_metric == "low" and n_samples < _low_live_min_decision_groups():
        return None, 4, None
    return row, maturity_level(n_samples), selected_model_data


def _date_cutoff_from_calibration_row(row: dict[str, Any]) -> str | None:
    """Return the date-level training cutoff for legacy platt_models rows.

    Older live/cache schemas do not persist ``training_cutoff`` even though the
    certificate payload requires that semantic. ``fitted_at``/``recorded_at`` are
    model materialization times and can be created by read-time cache writes during
    the reactor cycle; using the full timestamp as ``training_cutoff`` makes the
    proof non-causal by construction. The established calibration producer
    convention writes date-only cutoffs, so legacy rows degrade to the UTC date of
    the materialization timestamp.
    """

    for key in ("fitted_at", "recorded_at"):
        parsed = _parse_utc(row.get(key))
        if parsed is not None:
            return datetime.combine(parsed.date(), time.min, tzinfo=UTC).isoformat()
    return None


_MIN_ROBUST_CAPITAL_EFFICIENCY_ROI = 0.0


# REMOVED 2026-06-08 (S7; operator directive; "bin selection.md" §14 item 8): the
# `_env_flag_enabled` helper was used ONLY to read the opportunity-book shadow
# toggle env var into the cache_summary. With the shadow / off-able selector
# artifacts deleted (single-primary-live, no flag, no shadow), it has no caller
# and is removed — there is no env-driven branch on the selection path.


# REMOVED 2026-06-08 (operator directive; "bin selection.md" §14 item 8 single-
# primary-live): the family-selector on/off gate (its env var + its
# edli_v1 settings key) is GONE. The bin-selection robust marginal-log-utility
# ranker is the UNCONDITIONAL single live decision surface — there is no disable
# path to silently flip. A scattered off-able gate is the regression disease the
# directive abolishes; correctness is enforced by types + relationship tests +
# the ff-branch review, never a runtime flag. (S7 also deleted the last literal
# toggle-name strings so the symbol cannot be re-grepped into a gate; antibody
# tests/engine/test_s7_selector_gate_removed.py.)
#
# REMOVED 2026-06-08 (S4; "bin selection.md" §6/§9 Hidden #3/#10/§13): the scalar
# market-disagreement buy_no demotion (_market_disagreement_demotes_buy_no + its
# _MARKET_DISAGREE_* thresholds) is GONE. The marginal-utility ranker subsumes it:
# a cheap NO scored with its OWN honest robust NO q_lcb (1 - q_ucb_yes) against the
# cheap NO all-in cost yields a negative robust edge -> ΔU <= 0 -> §13 no-trade,
# whenever the market is confident YES. One ranking surface, no scalar side-gate.
# Antibody: tests/engine/test_s4_subsumed_gates.py.


def _capital_efficiency_untradeable_reason(
    *,
    execution_price: ExecutionPrice | None,
    q_lcb_5pct: float,
    trade_score: float,
) -> str | None:
    if execution_price is None:
        return "ADMISSION_CAPITAL_EFFICIENCY:price=missing"
    return live_capital_efficiency_rejection_reason(
        q_lcb=q_lcb_5pct,
        execution_price=execution_price.value,
        trade_score=trade_score,
    )


def _candidate_robust_roi(proof: _CandidateProof) -> float:
    execution_price = getattr(proof, "execution_price", None)
    if execution_price is None:
        return 0.0
    price = _optional_float(execution_price.value)
    if price is None or price <= 0.0:
        return 0.0
    return float(getattr(proof, "trade_score", 0.0) or 0.0) / price


def _candidate_evaluation_id(proof: _CandidateProof) -> str:
    condition_id = str(getattr(proof.candidate, "condition_id", "") or "")
    return stable_hash(
        {
            "condition_id": condition_id,
            "token_id": proof.token_id,
            "direction": proof.direction,
        }
    )


def _candidate_low_volume_usd(row: Mapping[str, object]) -> float | None:
    for key in ("volume_usd", "volume", "total_volume"):
        if key in row and row.get(key) not in (None, ""):
            return _optional_float(row.get(key))
    return None


def _candidate_max_executable_shares(proof: _CandidateProof) -> float | None:
    row = proof.row
    if row is None or proof.execution_price is None:
        return None
    try:
        book = _native_quote_book_from_snapshot_row(row)
    except Exception:
        return None
    levels = {
        "buy_yes": book.yes_asks,
        "buy_no": book.no_asks,
        "sell_yes": book.yes_bids,
        "sell_no": book.no_bids,
    }.get(str(proof.direction or ""))
    if not levels:
        return 0.0
    try:
        return float(sum((level.size for level in levels), Decimal("0")))
    except Exception:
        return None


def _candidate_bin_id(proof: _CandidateProof) -> str:
    """Stable per-bin id for a proof's NativeSideCandidate (spec §14.2 bin_id).

    The bin id keys a bin WITHIN a family; the YES and NO sides of one bin share
    it (so the two native side candidates are recognisably the same bin), while
    distinct bins differ. Derived from the bin's settlement-defining geometry
    (low/high/unit/label) plus the condition id, never the side or token — those
    are what distinguish the two SIDES of the same bin.
    """
    candidate = proof.candidate
    bin_obj = getattr(candidate, "bin", None)
    return stable_hash(
        {
            "condition_id": str(getattr(candidate, "condition_id", "") or ""),
            "bin_low": getattr(bin_obj, "low", None),
            "bin_high": getattr(bin_obj, "high", None),
            "bin_unit": getattr(bin_obj, "unit", None),
            "bin_label": getattr(bin_obj, "label", None),
        }
    )


def _proof_probability_uncertainty(
    *, q_point: float, q_lcb: float
) -> "ProbabilityUncertainty":
    """Wrap the proof's scalar S2 q authority as a ProbabilityUncertainty (§14.4).

    The proof carries the per-side robust authority as SCALARS already computed by
    S2 (``q_posterior`` -> q_point, ``q_lcb_5pct`` -> q_lcb). For the YES side
    ``q_lcb_5pct`` is ``q_lcb_yes`` (lower tail of the YES probability samples);
    for the NO side it is ``q_lcb_no = 1 - q_ucb_yes`` (lower tail of the per-sample
    complement, Hidden #3) — NOT ``1 - q_lcb_yes``. This function only TRANSPORTS
    that authority; it never recomputes or complements it, so the §4 belief-space
    separation the S2 seam established is preserved verbatim.

    The probability bootstrap sample VECTOR is not threaded to this materialization
    layer (the proof carries only the scalars), so q_ucb is set to q_point here:
    the candidate object uses q_lcb as its sizing authority, and the
    ProbabilityUncertainty invariant only requires ``q_lcb <= q_point <= q_ucb``.
    A future phase that threads the sample vector can populate the real q_ucb /
    q_samples_hash without changing this contract.
    """
    from src.strategy.probability_uncertainty import ProbabilityUncertainty as _PU

    qp = float(min(max(q_point, 0.0), 1.0))
    ql = float(min(max(q_lcb, 0.0), 1.0))
    # Structural guard: q_lcb must not exceed q_point (edge_ci_lower-as-q_lcb is
    # Hidden #2). S2 already enforces this at the proof boundary; clamp defensively
    # so a degenerate scalar can never raise inside the constructor.
    ql = min(ql, qp)
    return _PU(
        q_point=qp,
        q_samples_hash="",
        q_lcb=ql,
        q_ucb=qp,
    )


def _native_side_cost_curve_from_execution_price(
    *,
    proof: _CandidateProof,
    side: str,
    token_id: str,
    market_snapshot_id: str,
) -> "ExecutableCostCurve | None":
    """Single-level fallback cost curve from the proof's OWN-side all-in price.

    Used ONLY when the snapshot row's native ask ladder cannot be rebuilt into a
    full ExecutableCostCurve (no depth json) but the proof IS genuinely priced.
    The proof's ``execution_price`` is its OWN native side's depth-walked all-in
    cost (S1, fee-deducted, probability_units). A single-level curve at that price
    reproduces the same scalar cost-of-entry the proof was priced at, so the ΔU
    ranker (the single live decision surface) can rank it instead of spuriously
    no-trading a priced candidate.

    §4 separation is preserved: the level price is the proof's OWN-side all-in
    cost and the curve is tagged with the proof's OWN ``side`` — it is NEVER a
    ``1 - p_exec(other side)`` complement. ``fee_rate=0`` so the curve's
    ``all_in_price`` reproduces the already-fee-deducted scalar verbatim (no
    double fee). ``min_tick`` is chosen to land the price on grid; depth is the
    proof's executable share count when known, else a unit of depth at min order.

    Returns ``None`` when the proof carries no usable own-side executable price
    (the candidate then stays a NATIVE_QUOTE_MISSING no-trade — fail closed).
    """
    if side not in ("YES", "NO"):
        return None
    execution_price = getattr(proof, "execution_price", None)
    price_value = _optional_float(getattr(execution_price, "value", None))
    if price_value is None or not (0.0 < price_value < 1.0):
        return None
    if not bool(getattr(proof, "native_quote_available", False)):
        return None

    # Land the all-in price on a tick grid fine enough to represent it exactly.
    price = Decimal(str(price_value)).quantize(Decimal("0.0001"))
    if not (Decimal("0") < price < Decimal("1")):
        return None
    min_tick = Decimal("0.0001")

    row = proof.row or {}
    min_order = _optional_float(row.get("min_order_size"))
    min_order_size = Decimal(str(min_order)) if (min_order and min_order > 0) else Decimal("1")

    shares = _candidate_max_executable_shares(proof)
    depth_shares = (
        Decimal(str(shares)) if (shares is not None and shares > 0) else None
    )
    # Depth must cover at least one min order; default to a deep single level so
    # the ΔU stake sweep is not artificially depth-capped when share count is
    # unknown (the scalar all-in price is the only executable fact available).
    if depth_shares is None or depth_shares < min_order_size:
        depth_shares = max(min_order_size, Decimal("1000000"))

    try:
        return ExecutableCostCurve(
            token_id=str(token_id),
            side=side,  # type: ignore[arg-type]
            snapshot_id=str(market_snapshot_id or ""),
            book_hash=str(row.get("book_hash") or row.get("snapshot_hash") or ""),
            levels=(BookLevel(price=price, size=depth_shares),),
            fee_model=FeeModel(fee_rate=Decimal("0")),
            min_tick=min_tick,
            min_order_size=min_order_size,
            quote_ttl=timedelta(seconds=1),
        )
    except (ValueError, KeyError, TypeError):
        return None


def _native_side_candidate_from_proof(
    *,
    family_key: str,
    proof: _CandidateProof,
) -> NativeSideCandidate:
    """Materialize a priced ``_CandidateProof`` as a unified NativeSideCandidate.

    S3 (spec §14.2 / §6 pseudocode / §11 Phase 1). This is the ONE materialization
    path: every priced YES/NO proof on the live selection path becomes a single
    ``NativeSideCandidate`` object — the unified candidate shape the ranker/selector
    consumes. There is no flag, no shadow branch, and no alternate candidate shape.

    DIRECTION LAW (money-path iron law, spec §4/§6): ``buy_yes`` -> ``side="YES"``
    (the proof's own bin is the WIN outcome); ``buy_no`` -> ``side="NO"`` (the own
    bin is the LOSE outcome). The mapping is ``_native_curve_side_for_direction``
    and is NEVER inverted here. A direction that is neither buy_yes nor buy_no has
    no native BUY side to price -> NATIVE_QUOTE_MISSING no-trade.

    ROBUST LOWER BOUND (spec §5.6 / Hidden #2): the candidate's ``q_lcb`` is the
    proof's ``q_lcb_5pct`` (the S2 robust probability lower bound) — never
    ``q_posterior`` and never ``edge_ci_lower``. ``q_point`` is ``q_posterior``.
    The constructor enforces ``q_lcb <= q_point``.

    NATIVE EXECUTABLE SEPARATION (spec §4 / Hidden #1): the executable cost curve
    is rebuilt from the proof's OWN snapshot-row native ask ladder, side-tagged
    with the proof's side (the S1 ``_native_side_cost_curve_from_snapshot_row``
    builder). A NO candidate therefore walks the NO ask book; the contract raises
    if a side-mismatched curve is ever fed in (``p_exec(NO) != 1 - p_exec(YES)``).

    MISSING TOKEN/QUOTE -> NO-TRADE (spec §13 / Hidden #4): a missing native token
    id, an unpriced side (``execution_price is None`` /
    ``native_quote_available`` False), or a side with no executable ask ladder
    downgrades to a recorded ``NATIVE_TOKEN_MISSING`` / ``NATIVE_QUOTE_MISSING``
    no-trade candidate. The no-trade candidate carries NO executable curve and NO
    probability authority — there is nothing to complement-substitute from. It is
    RECORDED (not omitted) so the family hypothesis set / FDR denominator and the
    learning layer see the tested-and-untradeable side (Hidden #1).
    """
    candidate = proof.candidate
    condition_id = str(getattr(candidate, "condition_id", "") or "")
    token_id = str(proof.token_id or "")
    bin_id = _candidate_bin_id(proof)
    forecast_snapshot_id = str(getattr(proof, "executable_snapshot_id", "") or "")
    row = proof.row or {}
    market_snapshot_id = str(row.get("snapshot_id") or forecast_snapshot_id or "")
    hypothesis_id = _candidate_evaluation_id(proof)

    side = _native_curve_side_for_direction(str(proof.direction or ""))

    # Missing native token id -> NATIVE_TOKEN_MISSING (no native identity to trade).
    if not token_id:
        return NativeSideCandidate.no_trade(
            family_key=family_key,
            bin_id=bin_id,
            side=side or "YES",
            token_id=token_id,
            condition_id=condition_id,
            forecast_snapshot_id=forecast_snapshot_id,
            market_snapshot_id=market_snapshot_id,
            reason=CandidateNoTradeReason.NATIVE_TOKEN_MISSING,
            hypothesis_id=hypothesis_id,
        )

    # No native BUY side for this direction, or the proof was not priced
    # (execution_price missing / native quote unavailable) -> NATIVE_QUOTE_MISSING.
    # No complement substitution: a side with no executable ask is recorded as a
    # no-trade diagnostic, never a YES-derived price.
    if (
        side is None
        or proof.execution_price is None
        or not bool(proof.native_quote_available)
    ):
        return NativeSideCandidate.no_trade(
            family_key=family_key,
            bin_id=bin_id,
            side=side or ("NO" if str(proof.direction or "") == "buy_no" else "YES"),
            token_id=token_id,
            condition_id=condition_id,
            forecast_snapshot_id=forecast_snapshot_id,
            market_snapshot_id=market_snapshot_id,
            reason=CandidateNoTradeReason.NATIVE_QUOTE_MISSING,
            hypothesis_id=hypothesis_id,
        )

    # Rebuild this side's OWN executable cost curve from its native ask ladder
    # (S1 builder, side-tagged). A book that cannot build a curve (empty/off-grid)
    # falls back to the proof's OWN-side scalar all-in cost (execution_price): a
    # single-level curve at the price the proof was ALREADY priced at (S1). This
    # keeps a genuinely-priced proof rankable by the ΔU ranker (the single decision
    # surface) instead of spuriously no-trading it, WITHOUT fabricating a complement
    # — the fallback curve uses the proof's own native side and its own all-in
    # price (§4: never 1 - p_exec(other side)). Only a proof with no own-side
    # executable price at all stays a NATIVE_QUOTE_MISSING no-trade.
    try:
        curve = _native_side_cost_curve_from_snapshot_row(
            row, side=side, token_id=token_id
        )
    except (ValueError, KeyError, TypeError):
        curve = _native_side_cost_curve_from_execution_price(
            proof=proof,
            side=side,
            token_id=token_id,
            market_snapshot_id=market_snapshot_id,
        )
        if curve is None:
            return NativeSideCandidate.no_trade(
                family_key=family_key,
                bin_id=bin_id,
                side=side,
                token_id=token_id,
                condition_id=condition_id,
                forecast_snapshot_id=forecast_snapshot_id,
                market_snapshot_id=market_snapshot_id,
                reason=CandidateNoTradeReason.NATIVE_QUOTE_MISSING,
                hypothesis_id=hypothesis_id,
            )

    q_point = float(proof.q_posterior)
    q_lcb = float(proof.q_lcb_5pct)

    # §13 / Hidden #2 live no-trade gate: q_lcb is INVALID when it is out of
    # [0, 1] or EXCEEDS q_point (a lower-confidence bound above the point estimate
    # is the "edge_ci_lower masquerading as q_lcb" corruption). A corrupt q_lcb
    # must NOT size a trade — record a Q_LCB_INVALID no-trade candidate rather than
    # clamping it into a tradeable one (clamping would hide the corruption and let
    # a low-win-rate candidate trade on a fabricated lower bound). On the real path
    # S2 guarantees q_lcb <= q_point per side, so this only fires on corrupt input.
    if not (0.0 <= q_lcb <= 1.0) or not (0.0 <= q_point <= 1.0) or q_lcb > q_point:
        return NativeSideCandidate.no_trade(
            family_key=family_key,
            bin_id=bin_id,
            side=side,
            token_id=token_id,
            condition_id=condition_id,
            forecast_snapshot_id=forecast_snapshot_id,
            market_snapshot_id=market_snapshot_id,
            reason=CandidateNoTradeReason.Q_LCB_INVALID,
            hypothesis_id=hypothesis_id,
        )

    probability_uncertainty = _proof_probability_uncertainty(
        q_point=q_point, q_lcb=q_lcb
    )

    return NativeSideCandidate.tradeable(
        family_key=family_key,
        bin_id=bin_id,
        side=side,
        token_id=token_id,
        condition_id=condition_id,
        q_point=q_point,
        q_lcb=q_lcb,
        probability_uncertainty=probability_uncertainty,
        executable_cost_curve=curve,
        forecast_snapshot_id=forecast_snapshot_id,
        market_snapshot_id=market_snapshot_id,
        hypothesis_id=hypothesis_id,
    )


def _candidate_evaluation_from_proof(
    *,
    family_id: str,
    proof: _CandidateProof,
    kelly_size_usd: float = 0.0,
) -> CandidateEvaluation:
    """Derive the legacy CandidateEvaluation RECEIPT from the materialized candidate.

    S3 (operator directive 2026-06-08): the priced proof is materialized as the
    canonical ``NativeSideCandidate`` FIRST (``_native_side_candidate_from_proof``
    — the one candidate object), then this function derives the legacy
    ``CandidateEvaluation`` receipt shape FROM that candidate so the existing
    opportunity_book ranking/serialization surface keeps working unchanged. There
    is ONE materialization path producing ONE candidate object; the receipt is a
    projection of it, not a parallel re-derivation off the proof.

    The receipt-only / ranking fields that the NativeSideCandidate does not model
    (trade_score, p_value, p_fill_lcb, c_cost_95pct, kelly_size_usd,
    max_executable_shares, low_volume_usd, calibration source, same-bin posterior,
    and the legacy ranker's native_quote_available / missing_reason admission
    inputs) are read from the proof — they are scalar-Kelly ranking inputs that S4
    retires when the marginal-utility ranker replaces this surface. The IDENTITY
    fields (candidate_id, condition_id, token_id) are sourced from the materialized
    NativeSideCandidate so the receipt and the candidate cannot drift on identity.
    """
    native_candidate = _native_side_candidate_from_proof(
        family_key=family_id, proof=proof
    )
    execution_price = _optional_float(getattr(getattr(proof, "execution_price", None), "value", None))
    row = proof.row or {}
    candidate = proof.candidate
    bin_obj = getattr(candidate, "bin", None)
    return CandidateEvaluation(
        # Identity sourced from the materialized NativeSideCandidate (single
        # candidate object) so the receipt and the candidate cannot drift.
        candidate_id=native_candidate.hypothesis_id,
        family_id=family_id,
        condition_id=native_candidate.condition_id,
        token_id=native_candidate.token_id,
        direction=str(proof.direction or ""),
        bin_label=getattr(bin_obj, "label", None),
        execution_price=execution_price,
        # Diagnostic q fields record what the proof TESTED on this side (the S2
        # robust authority). A no-trade NativeSideCandidate carries no q authority
        # (q_point/q_lcb None) precisely because there is nothing to size — the
        # receipt still reports the tested scalars so the family/FDR/learning layer
        # sees the side was tested-and-untradeable (Hidden #1).
        q_posterior=float(proof.q_posterior),
        q_lcb_5pct=float(proof.q_lcb_5pct),
        q_lcb_calibration_source=proof.q_lcb_calibration_source,
        same_bin_yes_posterior=proof.same_bin_yes_posterior,
        c_cost_95pct=_optional_float(proof.c_cost_95pct),
        p_fill_lcb=float(proof.p_fill_lcb),
        trade_score=float(proof.trade_score),
        p_value=float(proof.p_value),
        passed_prefilter=bool(proof.passed_prefilter),
        # The receipt's native_quote_available / missing_reason are the LEGACY
        # selector's ranking-admission inputs; they remain sourced from the proof
        # so S3 does not change which proofs the legacy ranker admits (that is S4's
        # job when the marginal-utility ranker replaces this surface). On the real
        # path a priced proof's row always carries the native ask ladder it was
        # priced from, so the materialized NativeSideCandidate is tradeable
        # whenever the proof is — they agree; this preserves byte-identical legacy
        # ranking while the candidate object becomes the canonical materialization.
        native_quote_available=bool(proof.native_quote_available),
        missing_reason=proof.missing_reason,
        kelly_size_usd=max(0.0, float(kelly_size_usd)),
        max_executable_shares=_candidate_max_executable_shares(proof),
        book_hash=_nonnull(row.get("book_hash") or row.get("executable_book_hash") or row.get("snapshot_hash")),
        low_volume_usd=_candidate_low_volume_usd(row),
    )


# REMOVED 2026-06-08 (S4; operator directive; "bin selection.md" §3/§5.2/§5.3):
# _candidate_selection_kelly_size_usd_by_id — the pre-selection scalar binary-Kelly
# sizing pass over every sibling candidate (sized on q_point via evaluate_kelly).
# Exposure-aware sizing is now the marginal-utility ranker's
# RobustCandidateScore.optimal_stake_usd, computed POST-selection on the WINNING leg
# (_robust_marginal_utility_optimal_stake_usd) against the family payoff matrix +
# existing per-outcome exposure (Hidden #10). One sizing surface, sized on the robust
# q_lcb (not q_point), no parallel pre-selection scalar Kelly.


def _selection_scoped_proofs(
    *,
    proofs: tuple[_CandidateProof, ...],
    locked_opportunity_conn: sqlite3.Connection | None = None,
) -> tuple[_CandidateProof, ...]:
    executable = [proof for proof in proofs if proof.execution_price is not None]
    tradeable_limit = [
        proof
        for proof in executable
        if _candidate_limit_price_untradeable_reason(proof) is None
    ]
    scoped = executable
    if tradeable_limit:
        scoped = tradeable_limit
    if locked_opportunity_conn is not None:
        unlocked = [
            proof
            for proof in scoped
            if _locked_candidate_no_price_improvement_reason(
                locked_opportunity_conn,
                proof,
            )
            is None
        ]
        if unlocked:
            scoped = unlocked
        elif scoped:
            return ()
    return tuple(scoped)


def _opportunity_book_proofs_with_selection_rejections(
    *,
    proofs: tuple[_CandidateProof, ...],
    locked_opportunity_conn: sqlite3.Connection | None = None,
) -> tuple[_CandidateProof, ...]:
    excluded_by_id: dict[str, str] = {}
    selected_ids = {
        _candidate_evaluation_id(proof)
        for proof in _selection_scoped_proofs(
            proofs=proofs,
            locked_opportunity_conn=locked_opportunity_conn,
        )
    }
    for proof in proofs:
        proof_id = _candidate_evaluation_id(proof)
        if selected_ids and proof_id in selected_ids:
            continue
        if proof.execution_price is None:
            continue
        reason = _candidate_limit_price_untradeable_reason(proof)
        if reason is None and locked_opportunity_conn is not None:
            reason = _locked_candidate_no_price_improvement_reason(
                locked_opportunity_conn,
                proof,
            )
        if reason is not None:
            excluded_by_id[proof_id] = reason
    if not excluded_by_id:
        return proofs
    annotated: list[_CandidateProof] = []
    for proof in proofs:
        reason = excluded_by_id.get(_candidate_evaluation_id(proof))
        if reason is None:
            annotated.append(proof)
            continue
        annotated.append(
            dataclass_replace(
                proof,
                missing_reason=reason,
                passed_prefilter=False,
                trade_score=0.0,
            )
        )
    return tuple(annotated)


def _opportunity_book_from_proofs(
    *,
    event_id: str,
    family_id: str,
    proofs: tuple[_CandidateProof, ...],
    selected_proof: _CandidateProof | None = None,
    locked_opportunity_conn: sqlite3.Connection | None = None,
) -> OpportunityBook:
    # The per-candidate kelly_size_usd is a DISPLAY field only (S4): the pre-
    # selection scalar-Kelly pass that used to populate it is retired; the live
    # stake is the marginal-utility ranker's optimal_stake_usd on the winning leg.
    # The receipt's display kelly_size_usd defaults to 0.0 for non-winning siblings.
    evaluations = tuple(
        _candidate_evaluation_from_proof(
            family_id=family_id,
            proof=proof,
        )
        for proof in _opportunity_book_proofs_with_selection_rejections(
            proofs=proofs,
            locked_opportunity_conn=locked_opportunity_conn,
        )
    )
    # The live decision is the ΔU ranker's pick (selected_proof). The book RECORDS
    # it as the single selected_candidate_id (operator directive 2026-06-08;
    # spec §14 item 7/8) rather than re-deciding via legacy scalar-Kelly. The
    # bin-selection ranker is the unconditional single path: there is no off-able
    # gate, so the receipt's selected_candidate_id is always the ΔU decision (S7
    # deleted the last `selector_enabled` / shadow toggle artifacts from the
    # cache_summary; the receipt serializer records the decision unconditionally).
    #
    # A NON-executable selected_proof (execution_price None) is the best-belief
    # fallback surfaced for the EXECUTABLE_NATIVE_ASK_MISSING receipt, NOT a real
    # ΔU trade decision — it must NOT be recorded as the book's selected candidate
    # (else the receipt would claim a non-tradeable leg was selected). Record a
    # selection only for a genuinely-priced ΔU winner.
    decided_candidate_id = (
        _candidate_evaluation_id(selected_proof)
        if selected_proof is not None
        and getattr(selected_proof, "execution_price", None) is not None
        else None
    )
    return build_family_opportunity_book(
        family_id=family_id,
        evaluations=evaluations,
        event_id=event_id,
        decided_candidate_id=decided_candidate_id,
        cache_summary={
            "belief_cache": "source_run_bound",
            "price_cache": "snapshot_rows_refreshed_for_family",
            "actual_receipt_selected_candidate_id": decided_candidate_id,
        },
    )


def _generate_candidate_proofs(
    *,
    event: OpportunityEvent,
    payload: dict[str, object],
    family,
    snapshot_rows: list[dict[str, Any]],
    trade_conn: sqlite3.Connection,
    forecast_conn: sqlite3.Connection,
    calibration_conn: sqlite3.Connection,
    decision_time: datetime,
    promotion_evidence: ReplacementForecastPromotionEvidence | None = None,
    capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None = None,
) -> tuple[_CandidateProof, ...]:
    native_costs = _native_costs_by_candidate_direction(family=family, snapshot_rows=snapshot_rows)
    (
        q_by_condition,
        q_lcb_by_direction,
        generated_p_values,
        generated_prefilter,
        probability_evidence,
    ) = _live_yes_probabilities(
        event=event,
        payload=payload,
        family=family,
        conn=forecast_conn,
        calibration_conn=calibration_conn,
        native_costs=native_costs,
        decision_time=decision_time,
        promotion_evidence=promotion_evidence,
        capital_objective_evidence=capital_objective_evidence,
    )
    proofs: list[_CandidateProof] = []
    rows_by_direction = _snapshot_rows_by_condition_and_direction(snapshot_rows)
    for candidate in family.candidates:
        condition_id = str(candidate.condition_id or "")
        yes_q = q_by_condition.get(condition_id)
        yes_lcb_entry = q_lcb_by_direction.get((condition_id, "buy_yes"))
        no_lcb_entry = q_lcb_by_direction.get((condition_id, "buy_no"))
        if yes_q is None or yes_lcb_entry is None or no_lcb_entry is None:
            raise ValueError(f"missing q_live for condition {condition_id}")
        # K3: q_lcb carrier entries are QlcbProvenance (or bare float on the day0
        # generated path); _qlcb_raw_float reads the RAW (pre-clamp) value so the
        # selection-ranking key (trade_score, q_lcb_5pct) stays byte-identical to
        # legacy when deep-OTM bins carry a legitimately negative bootstrap q_lcb.
        # Clamped vs raw only differs when clamped=True; for in-range bins they are
        # identical. Using raw here restores legacy loser-selection ordering on
        # no-submit receipts (the measurement/telemetry substrate). _qlcb_float
        # (the clamped door) is still used everywhere a value must be in [0,1].
        from src.calibration.qlcb_provenance import _qlcb_raw_float
        yes_lcb = _qlcb_raw_float(yes_lcb_entry)
        no_lcb = _qlcb_raw_float(no_lcb_entry)
        # bin-selection S2 (Hidden #3/#4, §4): the buy_no leg now carries a REAL native-NO
        # belief authority, not the disabled placeholder. The NO point is the per-sample
        # YES complement 1 - yes_q (valid in belief space, §4); the NO q_lcb is no_lcb —
        # the native-NO robust lower bound 1 - q_ucb_yes from the q-construction seam
        # (canonical: _side_q_lcb_from_yes_samples; replacement: _replacement_no_lcb_for_bin).
        # The old ADMISSION_BUY_NO_INDEPENDENT_NO_POSTERIOR_MISSING hardcode (which forced
        # q_no=0 so buy_no never scored) is retired: the native NO authority IS the posterior.
        no_q = float(min(max(1.0 - float(yes_q), 0.0), 1.0))
        for token_id, direction, q_value, q_lcb, independent_no_missing_reason in (
            (str(candidate.yes_token_id or ""), "buy_yes", yes_q, yes_lcb, None),
            (str(candidate.no_token_id or ""), "buy_no", no_q, no_lcb, None),
        ):
            row = rows_by_direction.get((condition_id, direction))
            # bin-selection S2: q_lcb <= q_point is now an UPSTREAM structural guarantee
            # (ProbabilityUncertainty for YES, the 1 - q_ucb_yes complement clamped under
            # 1 - q_yes for NO — both in the q-construction seam). The old here-at-boundary
            # "q_lcb > q_value -> clamp" patch existed only because the edge_ci_lower +
            # cost restore (Hidden #2) could manufacture a lower bound above the point;
            # that restore is gone, so the patch is dead and removed (no redundant gate).
            execution_price: ExecutionPrice | None = None
            c_cost_95pct: float | None = None
            p_fill_lcb = 0.0
            missing_reason: str | None = None
            if independent_no_missing_reason is not None:
                missing_reason = independent_no_missing_reason
            elif not token_id:
                missing_reason = "missing token id"
            elif row is None:
                missing_reason = "missing executable snapshot row"
            else:
                # Price TTL must not shrink the family selector. The selected
                # executable is re-authorized against a JIT book at submit time.
                try:
                    execution_price, p_fill_lcb, c_cost_95pct = _execution_price_from_snapshot(
                        row,
                        selected_token_id=token_id,
                        direction=direction,
                    )
                except ValueError as exc:
                    missing_reason = str(exc)
            score = _robust_trade_score_from_generated_inputs(
                q_posterior=q_value,
                q_lcb_5pct=q_lcb,
                execution_price=execution_price,
                c_cost_95pct=c_cost_95pct,
                p_fill_lcb=p_fill_lcb,
            )
            # REMOVED 2026-06-08 (S4; "bin selection.md" §6/§9 Hidden #3/#10/§13 +
            # operator directive): the scalar market-disagreement buy_no demotion
            # (cheap-NO-overconfidence -> score=0) is GONE. It is SUBSUMED by the
            # marginal-utility ranker: a cheap NO means a low NO all-in cost, and
            # the candidate is scored with its OWN honest robust NO q_lcb
            # (1 - q_ucb_yes). When the market is confident YES (cheap NO) that
            # honest q_lcb_no is low, so q_lcb_no < cost -> negative robust edge ->
            # ΔU <= 0 -> the §13 no-trade gate fires inside the ranker. The cheap-NO
            # loser is UNCONSTRUCTABLE without a separate scalar gate, and where the
            # two differed the ranker is the settlement-correct one (it trades a
            # cheap NO whose HONEST robust q_lcb genuinely covers the cost, instead
            # of hard-blocking everything below a 0.95 cutoff). Antibody:
            # tests/engine/test_s4_subsumed_gates.py::
            # test_cheap_no_overconfidence_loser_is_delta_u_no_trade. Scattered
            # on/off gates ARE the regression disease the directive abolishes.
            capital_efficiency_reason = _capital_efficiency_untradeable_reason(
                execution_price=execution_price,
                q_lcb_5pct=q_lcb,
                trade_score=score,
            )
            if capital_efficiency_reason is not None:
                score = 0.0
                if missing_reason is None:
                    missing_reason = capital_efficiency_reason
            def _lcb_source(value: object) -> str | None:
                source = getattr(value, "calibration_source", None)
                return str(source) if source else None

            q_lcb_source = _lcb_source(no_lcb_entry if direction == "buy_no" else yes_lcb_entry)
            buy_no_conservative_evidence_reason = live_buy_no_conservative_evidence_rejection_reason(
                direction=direction,
                q_direction=q_value,
                q_lcb=q_lcb,
                execution_price=execution_price.value if execution_price is not None else None,
                q_lcb_calibration_source=q_lcb_source,
                same_bin_yes_posterior=yes_q,
            )
            if buy_no_conservative_evidence_reason is not None:
                score = 0.0
                if missing_reason is None:
                    missing_reason = buy_no_conservative_evidence_reason
            p_value = generated_p_values[(condition_id, direction)]
            passed_prefilter = bool(generated_prefilter.get((condition_id, direction), execution_price is not None and score > 0.0))
            # A structurally non-tradeable candidate must not enter the FDR family
            # as a "passed" hypothesis. Force prefilter False so it can never be
            # selected. (The S4-removed market-disagreement scalar demotion is no
            # longer one of these triggers — the marginal-utility ranker's §13
            # ΔU<=0 gate subsumes the cheap-NO-overconfidence demotion.)
            if (
                capital_efficiency_reason is not None
                or buy_no_conservative_evidence_reason is not None
            ):
                passed_prefilter = False
            proofs.append(
                _CandidateProof(
                    candidate=candidate,
                    token_id=token_id,
                    direction=direction,
                    row=row,
                    executable_snapshot_id=str(row.get("snapshot_id") or "") if row is not None else None,
                    execution_price=execution_price,
                    q_posterior=q_value,
                    q_lcb_5pct=q_lcb,
                    q_lcb_calibration_source=q_lcb_source,
                    c_cost_95pct=c_cost_95pct,
                    p_fill_lcb=p_fill_lcb,
                    trade_score=score,
                    p_value=p_value,
                    passed_prefilter=passed_prefilter,
                    native_quote_available=execution_price is not None,
                    p_cal_vector_hash=str(probability_evidence["p_cal_vector_hash"]),
                    p_live_vector_hash=str(probability_evidence["p_live_vector_hash"]),
                    missing_reason=missing_reason,
                    mainstream_agreement=payload.get(
                        "_mainstream_agreement_verdicts", {}
                    ).get((condition_id, direction)),
                    # #120: calibrator provenance — per-family, set by the
                    # ONE-CALIBRATOR SEAM (era.py:3772 emos / 3774 maze). Same
                    # payload instance (#149 fix), so this is the actual q_source.
                    q_source=payload.get("_edli_q_source"),
                    same_bin_yes_posterior=yes_q,
                    # H2_E2E: carry posterior_id + probability_authority from the
                    # probability evidence dict. Present only on the replacement_0_1
                    # path; None (absent key) on canonical. posterior_id is emitted
                    # as a string by the authority builder — coerce to int for the
                    # typed column / FK to forecast_posteriors(posterior_id).
                    posterior_id=_optional_int(probability_evidence.get("posterior_id")),
                    probability_authority=probability_evidence.get("probability_authority"),
                )
            )
    return tuple(proofs)


def _per_bin_yes_q_lcb(
    proofs: tuple[_CandidateProof, ...],
) -> dict[str, float]:
    """Robust YES q_lcb for each bin in the family (spec §14.7 / §3 π_y^rob).

    The :func:`utility_ranker.robust_probabilities` outcome vector ``π_y^rob`` is
    built from each bin's ROBUST YES lower bound ``q_lcb_yes_i`` (NOT q_point —
    Hidden #2). A ``buy_yes`` proof carries that value directly in its
    ``q_lcb_5pct`` (YES probability space); a ``buy_no`` proof's ``q_lcb_5pct`` is
    NO-space (``1 - q_ucb_yes``) and must NOT be read as a YES q_lcb — so the map
    is sourced ONLY from YES proofs. A bin with no YES proof is absent from the
    map; ``robust_probabilities`` then assigns it ``q_lcb=0`` win-mass (the
    outcome still exists, so NO candidates still win on it — Hidden #5), which is
    the conservative treatment.

    Keyed by the same ``_candidate_bin_id`` the materialized NativeSideCandidate
    uses, so the per-bin q_lcb and the candidates share one bin index.
    """
    by_bin: dict[str, float] = {}
    for proof in proofs:
        if str(getattr(proof, "direction", None) or "") != "buy_yes":
            continue
        bin_id = _candidate_bin_id(proof)
        q_lcb = float(min(max(float(proof.q_lcb_5pct), 0.0), 1.0))
        # If multiple YES proofs map to the same bin (should not happen on the
        # real path), keep the most conservative (lowest) robust lower bound.
        prior = by_bin.get(bin_id)
        by_bin[bin_id] = q_lcb if prior is None else min(prior, q_lcb)
    return by_bin


def _robust_marginal_utility_baseline_usd() -> Decimal:
    """Flat per-outcome wealth baseline ``A_y`` for the ΔU optimizer (spec §3).

    The marginal log utility ``ΔU = Σ_y π_y [log(A_y + R_y) − log(A_y)]`` needs a
    positive wealth-by-outcome baseline. The baseline is the runtime bankroll when
    it is cheaply available; otherwise a positive constant (the optimum stake
    FRACTION is invariant to the baseline scale under a FLAT baseline, so this only
    sets the ΔU units, never the ranking). Existing per-outcome exposure is layered
    ON TOP of this baseline by :func:`_robust_marginal_utility_exposure` (S4 / §11
    Phase 4), which is what makes existing exposure shrink ΔU (Hidden #10).
    """
    try:
        bankroll = _runtime_bankroll_usd(cached_only=True)
    except (TypeError, ValueError):
        bankroll = 0.0
    if bankroll and bankroll > 0.0:
        return Decimal(str(bankroll))
    return Decimal("1000")


def _robust_marginal_utility_exposure(
    matrix: "utility_ranker.FamilyPayoffMatrix",
    *,
    baseline_usd: Decimal,
    extra_exposure_by_bin_id: Mapping[str, float] | None = None,
) -> "utility_ranker.PortfolioExposureVector":
    """Per-outcome wealth ``A_y`` = flat baseline + EXISTING family exposure (S4, §11 Phase 4).

    The ΔU objective measures the marginal log utility of a NEW leg against the
    wealth the book WOULD hold if Y settled each outcome, given everything ALREADY
    on/pending in this family. ``extra_exposure_by_bin_id[bin_id]`` is the extra
    wealth realized if Y settles that bin (e.g. an existing/pending long on that
    bin's YES). Because log is concave, a new candidate that wins where ``A_y`` is
    already large has LOWER marginal value — so existing exposure shrinks the
    optimal stake or forces a no-trade (spec §6 "too correlated with existing
    exposure"; §11 Phase 4 acceptance; Hidden #10). With NO existing exposure this
    is exactly :meth:`PortfolioExposureVector.flat` (the conservative single-primary
    baseline), so an empty mapping reproduces the prior behavior byte-for-byte.

    Exposure is keyed by the SAME ``_candidate_bin_id`` the candidates use, so an
    existing-position bin and a new candidate on that bin share one outcome index.
    An exposure key that is not a family outcome is ignored (the outcome set is the
    family the ranker is scoring; foreign-family exposure does not enter THIS
    family's payoff geometry).
    """
    extra = {
        bin_id: Decimal(str(usd))
        for bin_id, usd in (extra_exposure_by_bin_id or {}).items()
        if bin_id in matrix.outcomes and float(usd) > 0.0
    }
    if not extra:
        return utility_ranker.PortfolioExposureVector.flat(
            matrix, baseline=baseline_usd
        )
    return utility_ranker.PortfolioExposureVector.from_outcome_wealth(
        matrix, baseline=baseline_usd, extra_by_outcome=extra
    )


def _score_family_candidates_by_robust_marginal_utility(
    *,
    executable: list[_CandidateProof],
    family_key: str,
    per_bin_yes_q_lcb: Mapping[str, float],
    extra_exposure_by_bin_id: Mapping[str, float] | None = None,
    max_stake_usd: Decimal | None = None,
    baseline_usd: Decimal | None = None,
) -> tuple[
    list["utility_ranker.RobustCandidateScore"],
    dict[str, _CandidateProof],
]:
    """Materialize + ΔU-score the family's executable proofs (spec §6 / §14.7).

    The ONE scoring kernel both the live selection (:func:`_select_proof_by_robust_
    marginal_utility`) and the exposure-aware sizing (:func:`_robust_marginal_
    utility_optimal_stake_usd`) share, so the rank and the size are computed on the
    SAME FamilyPayoffMatrix (bins + OUTSIDE, Hidden #5), the SAME robust YES-q_lcb
    π, and the SAME exposure vector — they cannot drift. Returns the ΔU-descending
    scores and a hypothesis_id -> proof index for mapping a winning candidate back
    to its proof.
    """
    candidate_by_proof: list[tuple[NativeSideCandidate, _CandidateProof]] = [
        (
            _native_side_candidate_from_proof(family_key=family_key, proof=proof),
            proof,
        )
        for proof in executable
    ]
    proof_by_hypothesis: dict[str, _CandidateProof] = {
        cand.hypothesis_id: proof for cand, proof in candidate_by_proof
    }
    tradeable = [cand for cand, _ in candidate_by_proof if cand.is_tradeable]
    if not tradeable:
        return [], proof_by_hypothesis

    bin_ids = list(dict.fromkeys(cand.bin_id for cand in tradeable))
    matrix = utility_ranker.FamilyPayoffMatrix.over_bins(bin_ids)
    pi = utility_ranker.robust_probabilities(
        matrix,
        per_bin_q_lcb={
            bin_id: float(per_bin_yes_q_lcb.get(bin_id, 0.0)) for bin_id in bin_ids
        },
    )
    exposure = _robust_marginal_utility_exposure(
        matrix,
        baseline_usd=(
            baseline_usd
            if baseline_usd is not None
            else _robust_marginal_utility_baseline_usd()
        ),
        extra_exposure_by_bin_id=extra_exposure_by_bin_id,
    )
    scored = utility_ranker.rank_candidates(
        tradeable, matrix, pi, exposure, max_stake_usd=max_stake_usd
    )
    return scored, proof_by_hypothesis


def _select_proof_by_robust_marginal_utility(
    *,
    executable: list[_CandidateProof],
    family_key: str,
    per_bin_yes_q_lcb: Mapping[str, float],
    extra_exposure_by_bin_id: Mapping[str, float] | None = None,
) -> _CandidateProof | None:
    """THE single live selection decision (spec §6 / §14.7 / §13).

    Materialize each executable proof as its unified ``NativeSideCandidate`` (the
    ONE materialization path, :func:`_native_side_candidate_from_proof`), build
    the family payoff matrix over every bin PLUS the OUTSIDE outcome (Hidden #5),
    and pick the candidate that maximizes robust marginal expected LOG utility
    ``ΔU`` (:func:`utility_ranker.rank_candidates`). The §13 no-trade gate
    ("robust marginal expected log utility <= 0") fires HERE, on the live path:
    if no candidate has positive ΔU the family no-trades (returns ``None``).

    This REPLACES the legacy scalar-Kelly ranking surfaces
    (``build_family_opportunity_book`` -> ``select_best_family_candidate`` and the
    ``max(executable, key=(trade_score, q_lcb_5pct))`` fallback). There is exactly
    ONE ranking surface now — the bin-selection §7 ranker — so the materialized
    candidate is no longer discarded; it IS the decision (operator directive
    2026-06-08; spec §14.8 single-primary-live).

    Native-NO conservatism (Hidden #3) and the OUTSIDE outcome (Hidden #5) are
    enforced inside the ranker: a NO candidate is scored with its OWN robust NO
    ``q_lcb = 1 - q_ucb_yes`` (its ``candidate.q_lcb``), never the looser
    ``1 - q_lcb_yes`` implied by the shared YES π.

    EXISTING EXPOSURE (S4 / §11 Phase 4 / Hidden #10): ``extra_exposure_by_bin_id``
    layers current/pending family exposure onto the per-outcome wealth baseline, so
    a candidate that wins where the book is already heavily committed scores LOWER
    ΔU (concavity of log) — can flip to no-trade. With no existing exposure (empty
    mapping) this is the conservative flat-baseline single-primary objective.
    """
    scored, proof_by_hypothesis = _score_family_candidates_by_robust_marginal_utility(
        executable=executable,
        family_key=family_key,
        per_bin_yes_q_lcb=per_bin_yes_q_lcb,
        extra_exposure_by_bin_id=extra_exposure_by_bin_id,
    )
    for score in scored:
        # §13 live no-trade gate: skip any non-positive-ΔU candidate. rank_candidates
        # sorts ΔU-descending, so the first positive-ΔU score is the family primary.
        if score.is_no_trade:
            continue
        return proof_by_hypothesis.get(score.candidate.hypothesis_id)
    # Every candidate's robust marginal expected log utility was <= 0 -> no-trade.
    return None


def _chosen_stake_execution_price(
    curve: "ExecutableCostCurve", stake_usd: Decimal | float
) -> ExecutionPrice:
    """Typed Kelly cost-of-entry at the CHOSEN stake (spec §5.3 / §14.10 / Hidden #6).

    S5 (operator directive 2026-06-08). THE boundary recomputation: given the
    selected candidate's OWN native :class:`ExecutableCostCurve` and the ΔU
    optimizer's chosen stake, return the DEPTH-WALKED average all-in cost AT THAT
    STAKE as a typed :class:`ExecutionPrice`. This is the price the live order is
    actually sized against — NOT the cheap min-order / top-of-book scalar the proof
    was first priced at (S1's ``avg_cost_for_shares(min_order_size)``).

    WHY (Hidden #6 — "scalar VWMP hides the convex cost curve"). Scalar Kelly on a
    single top-ask price over-bets into thin levels: the order's true fill cost is
    the convex depth walk, which on a thin book is strictly worse than the top ask.
    The §5.3 optimizer already chose the stake against that convex curve; the Kelly
    boundary the intent carries MUST be the same curve evaluated at the same stake,
    or the realized cost-of-entry (and therefore the executor's limit price and the
    receipt's ``c_fee_adjusted``) would understate cost and the size+price would be
    internally inconsistent.

    The returned ``ExecutionPrice`` is ``fee_adjusted`` / ``fee_deducted=True`` in
    ``probability_units`` (``ExecutableCostCurve.avg_cost`` guarantees this), so it
    passes :meth:`ExecutionPrice.assert_kelly_safe` (R1/R2 identity preserved); we
    assert it here so a corrupt boundary fails closed at this seam rather than
    laundering an unsafe price into the intent.

    Raises ``ValueError`` (fail closed, spec §13) when the stake is below min order
    or above executable depth — but the ΔU optimizer's feasible interval is exactly
    ``[min_order_notional, depth]`` and the fractional haircut only shrinks the
    stake, so on the live path a positive chosen stake is always fillable.
    """
    price = curve.avg_cost(Decimal(str(stake_usd)))
    price.assert_kelly_safe()
    return price


class _StakeBelowMinOrder(RuntimeError):
    """The fractional-Kelly chosen stake fell below the venue min order and could NOT
    be bumped to min order within the bankroll cap.

    This is a SIZING/venue-floor abort, NOT an edge reversal. Raised by the sizing
    kernel (:func:`_robust_marginal_utility_stake_and_price`) and caught in the submit
    decision body, where it becomes a distinct ``SUBMIT_ABORTED_BELOW_MIN_ORDER``
    decision. Never conflated with the zero-stake EDGE_REVERSED gate (antibody for the
    2026-06-09 false-EDGE_REVERSED regression: a positive-edge candidate whose
    post-haircut stake was below min order was being mislabeled "no edge").
    """


# Operator guard on the auto-bump-to-min-order action (2026-06-09 fix). When the
# fractional-Kelly haircut shrinks the chosen stake below the venue min order but the
# ROBUST (q_lcb-based) ΔU at the min-order notional is strictly positive, the sizing
# path bumps the stake UP to min order — the fractional-Kelly risk intent is preserved
# because a $0.05–$0.80 min order on a ~$900 wallet is well under this ceiling. The bump
# is ALLOWED only when min_order_usd <= this fraction of the SIZING bankroll; above it,
# the candidate aborts as SUBMIT_ABORTED_BELOW_MIN_ORDER rather than over-committing.
_MIN_ORDER_BUMP_MAX_BANKROLL_PCT = 0.02


def _robust_marginal_utility_stake_and_price(
    *,
    family_key: str,
    selected_proof: _CandidateProof,
    all_proofs: tuple[_CandidateProof, ...] | list[_CandidateProof],
    extra_exposure_by_bin_id: Mapping[str, float] | None,
    bankroll_usd: float,
    kelly_multiplier: float,
    stake_floor_out: dict[str, object] | None = None,
) -> tuple[float, ExecutionPrice | None]:
    """Chosen FRACTIONAL-Kelly stake AND its typed chosen-stake price (spec §5.3 / §14.10).

    S5 (operator directive 2026-06-08). THE single sizing+pricing kernel for the
    live intent: the ΔU optimizer (:meth:`utility_ranker.score_candidate`) returns
    ``optimal_stake_usd`` — the LOG-OPTIMAL (full-Kelly) stake on the candidate's
    OWN robust q_lcb-based π, scored against the family payoff matrix and the
    EXISTING per-outcome exposure (Hidden #10) — which is then scaled by the
    FRACTIONAL-Kelly multiplier ``kelly_multiplier`` (the CI-width / lead /
    portfolio-heat haircut, spec §5.2 ``x_final = x_raw · f_kelly · h_*``) so a
    wider-CI edge sizes strictly smaller (variance is never silently dropped).

    Then — the S5 boundary — it RE-PRICES the selected leg at that CHOSEN stake on
    the SAME scored candidate's native :class:`ExecutableCostCurve`
    (:func:`_chosen_stake_execution_price`). Size and price come from ONE scored
    candidate and ONE curve, so the Kelly boundary the intent carries is the
    depth-walked cost the order actually pays (Hidden #6), not S1's cheap min-order
    scalar. There is no second scalar Kelly and no shadow branch.

    ROBUST-LOWER-BOUND SIZING (money-path iron law): the stake derives from
    ``q_lcb`` (via the q_lcb-based π inside ``score_candidate``), NEVER from
    ``q_point``. Two proofs with equal q_lcb but different q_posterior size
    identically — the legacy ``evaluate_kelly`` (sized on ``p_posterior =
    q_posterior``) is no longer the size authority.

    Returns ``(stake_usd, chosen_stake_execution_price)``. ``(0.0, None)`` when no
    positive-ΔU stake clears min order / depth (a no-trade) or the boundary cannot
    be priced. The selected proof is scored within the WHOLE family so the π /
    exposure / OUTSIDE geometry matches the ranking decision exactly.

    MIN-ORDER FLOOR (2026-06-09 false-EDGE_REVERSED fix). When the fractional-Kelly
    haircut shrinks the chosen stake below the venue min-order notional but the ROBUST
    ΔU at min order is strictly positive AND min order is within the bankroll-cap guard
    (``_MIN_ORDER_BUMP_MAX_BANKROLL_PCT``), the stake is BUMPED to min order and the
    floor is recorded in ``stake_floor_out`` (if provided) as
    ``stake_floor="VENUE_MIN_ORDER"``. When the bump is not admissible (bankroll cap
    fails) it raises :class:`_StakeBelowMinOrder` — a DISTINCT sizing abort the decision
    body maps to ``SUBMIT_ABORTED_BELOW_MIN_ORDER``, never EDGE_REVERSED. A genuinely
    reversed candidate (no positive-ΔU stake at ANY admissible size incl. min order)
    still returns ``(0.0, None)`` -> EDGE_REVERSED.

    ``stake_floor_out`` (optional): a caller-owned dict the kernel writes the stake-floor
    provenance into on a bump. Left unset by callers that don't need provenance (the
    behavior is otherwise identical), so existing 2-tuple call sites are unaffected.
    """
    if bankroll_usd <= 0.0 or selected_proof.execution_price is None:
        return 0.0, None
    mult = float(kelly_multiplier)
    if not (mult > 0.0):
        return 0.0, None

    per_bin_yes_q_lcb = _per_bin_yes_q_lcb(tuple(all_proofs))
    selected_hypothesis_id = _candidate_evaluation_id(selected_proof)

    # Score the WHOLE family (so π / exposure / OUTSIDE match the ranking), then
    # read the selected leg's full-Kelly optimum. The feasible stake ceiling is the
    # SMALLER of two pure UPPER BOUNDS on the chosen stake (the ONE clamp, applied at
    # the single ``min(...)`` choke point below — NOT a second, parallel cap):
    #
    #   1. the FRACTIONAL-Kelly budget ``mult × bankroll`` — the ΔU optimizer never
    #      bets above the fractional-Kelly cap, which is how the variance haircut
    #      bounds the size; AND
    #   2. the SINGLE-POSITION CONCENTRATION CEILING ``max_single_position_pct ×
    #      bankroll`` (operator concentration law, restored to the LIVE bin-selection
    #      sizing path). Without this the S5 ``optimal_stake_usd`` path bypassed the
    #      concentration ceiling that ``money_path_adapters.evaluate_kelly`` restored:
    #      the live decision body OVERRIDES ``evaluate_kelly.size_usd`` with this
    #      function's q_lcb-grounded ΔU stake (event_reactor_adapter ~L2095), so the
    #      ceiling living only inside ``evaluate_kelly`` protected nothing live — a
    #      strong-edge candidate sized at ~10% of a $1k wallet at the live
    #      kelly_multiplier=0.125 (and 83% at full Kelly). The ceiling is reconciled
    #      HERE, at the single live sizing choke point.
    #
    # Both are #107-safe UPPER bounds (``min`` on an already-positive ΔU stake can
    # never zero a positive edge — both bounds are pct·bankroll > 0 whenever the
    # wallet has cash): they clamp only the strong-edge TAIL; weak/modest edges sit
    # below both and keep their full ΔU-proportional stake. The base is the SIZING
    # bankroll (``bankroll_usd`` = free spendable cash; see _runtime_bankroll_usd's
    # spendable_cash), which scales the ceiling with wealth ($50 at $1k, $500 at $10k)
    # — a structural concentration limit, not a fixed-dollar clamp. It bounds the
    # STAKE MAGNITUDE only: the ΔU RANK (which side/bin is primary) is decided by
    # _select_proof_by_robust_marginal_utility BEFORE this sizing call, so clamping
    # the winner's stake cannot change which candidate is the winner (ranking
    # invariance). ``max_single_position_pct == 0`` disables the ceiling (only the
    # fractional-Kelly cap binds), matching the no-concentration-cap directive surface.
    from src.config import sizing_defaults

    _single_pos_pct = float(sizing_defaults()["max_single_position_pct"])
    _fractional_cap = Decimal(str(mult)) * Decimal(str(bankroll_usd))
    if _single_pos_pct > 0.0:
        _concentration_ceiling = Decimal(str(_single_pos_pct)) * Decimal(
            str(bankroll_usd)
        )
        max_stake = min(_fractional_cap, _concentration_ceiling)
    else:
        max_stake = _fractional_cap
    scored, proof_by_hypothesis = _score_family_candidates_by_robust_marginal_utility(
        executable=list(all_proofs),
        family_key=family_key,
        per_bin_yes_q_lcb=per_bin_yes_q_lcb,
        extra_exposure_by_bin_id=extra_exposure_by_bin_id,
        baseline_usd=Decimal(str(bankroll_usd)),
    )
    for score in scored:
        if _candidate_evaluation_id(
            proof_by_hypothesis.get(score.candidate.hypothesis_id, selected_proof)
        ) != selected_hypothesis_id:
            continue
        if score.is_no_trade:
            return 0.0, None
        # full-Kelly log-optimal stake on robust q_lcb-based π, scaled to fractional
        # Kelly by the haircut multiplier (spec §5.2). Bounded by ``max_stake`` = the
        # tighter of the fractional-Kelly budget and the single-position concentration
        # ceiling (the ONE clamp computed above). Both are pure #107-safe upper bounds.
        full_kelly_stake = float(score.optimal_stake_usd)
        fractional = full_kelly_stake * mult
        chosen = Decimal(str(min(Decimal(str(fractional)), max_stake)))
        if chosen <= Decimal("0"):
            return 0.0, None

        # MIN-ORDER-AWARE STAKE FLOOR (2026-06-09 false-EDGE_REVERSED fix). The ΔU
        # optimizer sizes ``optimal_stake_usd`` on the full bankroll; the fractional-
        # Kelly haircut (×mult, e.g. 0.125) then shrinks the chosen stake. For cheap
        # low-probability bins the haircut stake can fall BELOW the venue min-order
        # notional even though the ROBUST (q_lcb-based) edge at min order is strictly
        # positive. Previously ``_chosen_stake_execution_price`` raised "below min
        # order" and the generic except mapped it to (0.0, None) -> a FALSE
        # EDGE_REVERSED. Now: if the haircut stake is below min order BUT ΔU at the
        # min-order notional is strictly positive AND min order is within the operator
        # bankroll-cap guard, BUMP the stake up to min order (the fractional-Kelly risk
        # intent is preserved — a sub-$1 min order on a ~$900 wallet is << the cap) and
        # record the floor in provenance. Otherwise raise ``_StakeBelowMinOrder`` so the
        # decision body emits a DISTINCT SUBMIT_ABORTED_BELOW_MIN_ORDER (never
        # EDGE_REVERSED). ΔU(min_order) is the SAME robust q_lcb-based π/exposure the
        # ranker used (utility_ranker records it on the score), so the min-order edge is
        # robust-consistent, never a looser point estimate.
        min_order_usd = Decimal(str(score.min_order_notional_usd))
        if min_order_usd > Decimal("0") and chosen < min_order_usd:
            delta_u_at_min = float(score.delta_u_at_min_order)
            bankroll_cap = Decimal(str(_MIN_ORDER_BUMP_MAX_BANKROLL_PCT)) * Decimal(
                str(bankroll_usd)
            )
            if delta_u_at_min > 0.0 and min_order_usd <= bankroll_cap:
                # Edge is genuinely positive at min order and the floor is within the
                # bankroll cap -> trade at the venue minimum.
                chosen = min_order_usd
                if stake_floor_out is not None:
                    stake_floor_out["stake_floor"] = "VENUE_MIN_ORDER"
                    stake_floor_out["stake_floor_min_order_usd"] = float(min_order_usd)
                    stake_floor_out["stake_floor_delta_u_at_min_order"] = delta_u_at_min
            else:
                # Either ΔU(min_order) <= 0 (no admissible positive-edge stake — but the
                # ranker already excluded that via is_no_trade above, so this arm is the
                # bankroll-cap guard) or min order exceeds the bankroll cap. Distinct
                # BELOW_MIN_ORDER abort — NOT an edge reversal.
                raise _StakeBelowMinOrder(
                    f"chosen stake {float(chosen):.6f} USD below venue min order "
                    f"{float(min_order_usd):.6f} USD; "
                    f"delta_u_at_min_order={delta_u_at_min:.6g}, "
                    f"min_order_usd/bankroll_cap={float(min_order_usd):.6f}/"
                    f"{float(bankroll_cap):.6f} "
                    f"(positive edge at min order but stake floor not admissible; "
                    f"NOT an edge reversal)"
                )

        # S5 boundary: reprice the SELECTED leg at the CHOSEN stake on its OWN curve
        # (the depth-walked avg cost — Hidden #6), as the typed Kelly cost-of-entry.
        curve = score.candidate.executable_cost_curve
        if curve is None:
            return float(chosen), None
        try:
            price = _chosen_stake_execution_price(curve, chosen)
        except ValueError as exc:
            # NARROWED except (2026-06-09): a "below min order" ValueError must NOT be
            # conflated with arithmetic/contract faults. After the bump above the chosen
            # stake is >= min order on the live path, so a below-min ValueError here is a
            # genuine venue-floor block -> route to the distinct BELOW_MIN_ORDER abort.
            # Any OTHER ValueError (depth exhaustion / off-grid) keeps the original
            # fail-closed behavior (0.0 -> EDGE_REVERSED), unchanged.
            if "min_order_size" in str(exc) or "below" in str(exc):
                raise _StakeBelowMinOrder(
                    f"chosen-stake pricing rejected below min order: {exc}"
                ) from exc
            return 0.0, None
        except (ArithmeticError, ExecutionPriceContractError):
            # Arithmetic/contract fault on the boundary -> no priced stake (fail closed,
            # §13). Genuinely unexpected; keep the conservative no-trade behavior.
            return 0.0, None
        return float(chosen), price
    return 0.0, None


# The ONE submit-recapture state machine. Module-level singleton (the engine is a
# pure, stateless, frozen dataclass — no DB, no clock, no mutable state), so the
# recapture boundary always routes through the SAME RedecisionEngine. Default
# HysteresisPolicy: the inline submit gate aborts-and-defers on a family-rank
# reversal (it never switches inline, §5 AbortOrSwitchOnlyAfterFullRerank), so the
# η_switch / T_no_churn anti-churn policy is exercised only by the WATCH re-rank
# path, not by this gate.
_SUBMIT_RECAPTURE_ENGINE = RedecisionEngine()


# Map the engine's submit-abort lifecycle state to the no-submit receipt reason the
# decision body emits. The single source of truth for the abort-reason taxonomy —
# the receipt reason is DERIVED from the state machine's terminal state, never set
# independently (so the receipt and the lifecycle state can never disagree, §7/§14.9).
_SUBMIT_ABORT_RECEIPT_REASON: dict[CandidateLifecycleState, str] = {
    CandidateLifecycleState.SUBMIT_ABORTED_PRICE_MOVED: "SUBMIT_ABORTED_PRICE_MOVED",
    CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED: "SUBMIT_ABORTED_EDGE_REVERSED",
    CandidateLifecycleState.SUBMIT_ABORTED_FAMILY_REVERSED: "SUBMIT_ABORTED_FAMILY_REVERSED",
    CandidateLifecycleState.SUBMIT_ABORTED_BELOW_MIN_ORDER: "SUBMIT_ABORTED_BELOW_MIN_ORDER",
}


def _family_rank_reversed_at_recapture(
    *,
    family_key: str,
    selected_proof: _CandidateProof,
    all_proofs: tuple[_CandidateProof, ...] | list[_CandidateProof],
    locked_opportunity_conn: sqlite3.Connection | None = None,
) -> bool:
    """True iff the FRESH-curve re-rank no longer makes ``selected_proof`` primary.

    S6 (spec §5 submit pseudocode ``family_rank_reversed`` / §7 family-rank row /
    Hidden #7). The submit recapture must RECOMPUTE — not just validate — the family
    rank on the recaptured books: re-run the SAME single ΔU selection
    (:func:`_select_proof_by_robust_marginal_utility`, the one ranking surface) over
    the fresh-curve candidates and ask whether a DIFFERENT candidate is now the family
    primary. If a sibling now out-ranks the selected proof (price/forecast moved the
    order), the rank reversed and the inline submit must ABORT and defer to a full
    re-rank (the engine never switches inline; a WATCH fallback cannot auto-submit,
    Hidden #7).

    ``True`` ONLY when a DIFFERENT proof is the fresh ΔU primary. ``False`` when the
    selected proof is still the winner OR when the WHOLE family now no-trades (the
    fresh primary is None): a family-wide no-trade is NOT a rank *reversal* — for the
    selected leg it is its own edge reversal (utility <= 0), which the EDGE gate owns.
    Conflating the two would mislabel a plain edge collapse as a family switch.
    """
    # S6 SCOPE-SET INVARIANT (2026-06-09): the fresh-curve re-rank MUST be computed over
    # the SAME scoped candidate set SELECTION ranked over — `_selection_scoped_proofs`
    # (limit-tradeable AND unlocked-with-price-improvement only), with `per_bin_yes_q_lcb`
    # over the full set, EXACTLY as `_selected_candidate_proof` (era:6219/6233/6254). Ranking
    # the raw full set let a SCOPED-OUT leg (locked-no-improvement / below-min-tick) — which
    # still materializes is_tradeable — become the ΔU argmax and FALSELY reverse the
    # genuinely-best selected leg (SUBMIT_ABORTED_FAMILY_REVERSED). Mirroring selection's
    # scope is STRICTER, never looser: a scoped-out leg can never be the family primary.
    per_bin_yes_q_lcb = _per_bin_yes_q_lcb(tuple(all_proofs))
    scoped = _selection_scoped_proofs(
        proofs=tuple(all_proofs),
        locked_opportunity_conn=locked_opportunity_conn,
    )
    if not scoped:
        # Nothing survives selection scoping on the fresh set (all locked / untradeable):
        # selection itself would no-trade — handled by the selected leg's own EDGE gate,
        # not a family-rank *reversal*.
        return False
    fresh_primary = _select_proof_by_robust_marginal_utility(
        executable=list(scoped),
        family_key=family_key,
        per_bin_yes_q_lcb=per_bin_yes_q_lcb,
    )
    if fresh_primary is None:
        # Whole family no-trades on the fresh curves -> not a rank reversal; the
        # selected leg's own EDGE gate handles its nonpositive utility.
        return False
    return _candidate_evaluation_id(fresh_primary) != _candidate_evaluation_id(
        selected_proof
    )


def _evaluate_submit_recapture_for_selected(
    *,
    family_key: str,
    selected_proof: _CandidateProof,
    all_proofs: tuple[_CandidateProof, ...] | list[_CandidateProof],
    extra_exposure_by_bin_id: Mapping[str, float] | None,
    bankroll_usd: float,
    kelly_multiplier: float,
    forecast_still_current: bool,
    locked_opportunity_conn: sqlite3.Connection | None = None,
    stake_floor_out: dict[str, object] | None = None,
    order_rests_at_admitted_price: bool = False,
) -> tuple[SubmitRecaptureDecision, float, ExecutionPrice | None]:
    """THE single fail-closed submit-recapture gate (spec §5 / §7 / §14.9 / §14.10).

    S6 (operator directive 2026-06-08). RECOMPUTE-NOT-VALIDATE at the no-submit
    receipt boundary. This REPLACES the scattered inline submit-time re-gates (the
    ``trade_score <= 0`` / Kelly-not-passed checks that implicitly decided whether the
    recaptured leg could submit) with ONE pass through
    :meth:`RedecisionEngine.evaluate_submit_recapture`. There is no parallel branch
    and no flag: the receipt is built ONLY when ``decision.may_submit`` is True.

    What it recomputes (not validates) on the FRESH books:

      * the selected leg's OWN fresh ``ExecutableCostCurve`` — materialized via the
        ONE candidate path (:func:`_native_side_candidate_from_proof`); the engine
        walks it for the depth-walked all-in cost at the chosen stake (Hidden #6);
      * the chosen FRACTIONAL-Kelly stake + chosen-stake price on that curve (the S5
        kernel :func:`_robust_marginal_utility_stake_and_price`);
      * the family rank (:func:`_family_rank_reversed_at_recapture`) — does the
        selected proof remain the ΔU primary on the fresh curves?
      * the robust q_lcb (the candidate's OWN ``q_lcb`` — NO-side already in NO-space
        as ``1 - q_ucb_yes``; never the YES complement).

    Abort taxonomy (each a first-class fail-closed state, §7), in precedence order:

      1. no fresh curve (stale / failed recapture) -> the engine's missing-recapture
         branch, SUBMIT_ABORTED_PRICE_MOVED (no executable price re-established, §13);
      2. family rank reversed (selected leg no longer the ΔU primary on fresh curves)
         -> FAMILY_REVERSED (abort + defer to full re-rank; a non-primary leg never
         price/edge-checks — Hidden #7 / §5 AbortOrSwitchOnlyAfterFullRerank);
      3. stake <= 0 (no positive-utility stake at ANY admissible size INCLUDING min
         order on the fresh curve) -> EDGE_REVERSED (utility nonpositive, §5 ``if
         utility <= 0: Abort``);
      3b. positive edge at min order but the fractional-Kelly haircut stake is below
         the venue min order and cannot be bumped within the bankroll cap ->
         BELOW_MIN_ORDER (a DISTINCT sizing abort, NOT an edge reversal — 2026-06-09
         antibody; the regret ledger records the true cause);
      4. recaptured all-in cost > ``max_acceptable_price`` -> PRICE_MOVED;
      5. ``edge_lcb = q_lcb - all_in_cost <= 0`` or forecast not current -> EDGE_REVERSED.

    Gates 4-5 are resolved by the engine (price -> edge -> forecast). Family rank is
    checked here at top precedence so a no-longer-primary leg reports the governing
    reason, not an incidental own-leg edge/price softening.

    ``max_acceptable_price`` is the leg's DECISION-TIME admitted price (the proof's S1
    ``execution_price.value``): a fresh recapture that prices STRICTLY WORSE than the
    price the candidate was admitted at is a price move through the band (§7 price row).

    Returns ``(decision, chosen_stake_usd, chosen_stake_price)``. On a clean recapture
    the stake/price are the S5 chosen-stake size+boundary the intent must carry; on any
    abort the stake is 0.0 / price is None and the decision's ``state`` is in
    :data:`SUBMIT_ABORT_STATES` with the triggering ``reversal_reason``.
    """
    engine = _SUBMIT_RECAPTURE_ENGINE
    candidate = _native_side_candidate_from_proof(
        family_key=family_key, proof=selected_proof
    )

    # GATE 1 (stale / failed recapture, §13). A no-trade materialization (missing
    # native token / quote / invalid q_lcb) means there is NO fresh executable curve
    # to recapture -> route to the engine's missing-recapture branch by passing
    # recaptured_cost_curve=None (SUBMIT_ABORTED_PRICE_MOVED: no executable price could
    # be re-established; "Snapshot stale and recapture fails"). Never submit from the
    # decision-time snapshot. This is checked FIRST: a missing curve cannot price, rank,
    # or edge-check.
    if not candidate.is_tradeable:
        decision = engine.evaluate_submit_recapture(
            candidate,
            RecaptureInputs(
                recaptured_cost_curve=None,
                stake_usd=Decimal("0"),
                max_acceptable_price=Decimal("0"),
                recaptured_q_lcb=0.0,
                forecast_still_current=bool(forecast_still_current),
                family_rank_reversed=False,
            ),
        )
        return decision, 0.0, None
    fresh_curve = candidate.executable_cost_curve

    # GATE 2 (family rank reversed, §5 AbortOrSwitchOnlyAfterFullRerank / §7 family-rank
    # row / Hidden #7). HIGHEST-PRECEDENCE present-curve abort: if the selected proof is
    # no longer the family ΔU primary on the FRESH curves, it has lost submit authority
    # — it must NOT even price/edge-check, and a sibling that now out-ranks it is a
    # WATCH-only fallback that can only submit via a FULL re-rank (never inline). Checked
    # BEFORE the zero-stake/edge gates so a no-longer-primary leg reports the governing
    # reason (family reversed), not an incidental own-leg edge softening.
    if _family_rank_reversed_at_recapture(
        family_key=family_key,
        selected_proof=selected_proof,
        all_proofs=all_proofs,
        locked_opportunity_conn=locked_opportunity_conn,
    ):
        return (
            SubmitRecaptureDecision(
                state=CandidateLifecycleState.SUBMIT_ABORTED_FAMILY_REVERSED,
                may_submit=False,
                reversal_reason=ReversalReason.FAMILY_RANK,
                detail=(
                    "family rank reversed at recapture: selected leg is no longer the "
                    "ΔU primary on the fresh curves; abort and defer to a full re-rank "
                    "(no inline switch; a WATCH fallback cannot auto-submit, Hidden #7)"
                ),
            ),
            0.0,
            None,
        )

    # Recompute the chosen fractional-Kelly stake + chosen-stake price on the FRESH
    # curve (the S5 kernel — same scored candidate, same curve, no drift). The kernel
    # raises _StakeBelowMinOrder (a DISTINCT sizing abort) when the haircut stake is
    # below the venue min order AND cannot be bumped within the bankroll cap — that is
    # NOT an edge reversal and must NOT be folded into the zero-stake EDGE gate below.
    try:
        chosen_stake_usd, chosen_price = _robust_marginal_utility_stake_and_price(
            family_key=family_key,
            selected_proof=selected_proof,
            all_proofs=all_proofs,
            extra_exposure_by_bin_id=extra_exposure_by_bin_id,
            bankroll_usd=bankroll_usd,
            kelly_multiplier=kelly_multiplier,
            stake_floor_out=stake_floor_out,
        )
    except _StakeBelowMinOrder as exc:
        # GATE 3b (BELOW_MIN_ORDER, 2026-06-09 antibody). Edge is positive at min order
        # but the sized stake could not clear the venue floor within the bankroll cap.
        # A DISTINCT first-class abort so the regret ledger records the true cause —
        # EDGE_REVERSED keeps meaning "no edge at ANY admissible stake".
        return (
            SubmitRecaptureDecision(
                state=CandidateLifecycleState.SUBMIT_ABORTED_BELOW_MIN_ORDER,
                may_submit=False,
                reversal_reason=ReversalReason.MIN_ORDER,
                detail=(
                    f"recaptured edge positive at min order but fractional-Kelly stake "
                    f"below venue min order and not bump-admissible within the bankroll "
                    f"cap: {exc}"
                ),
            ),
            0.0,
            None,
        )

    # GATE 3 (edge reversed, §5 'utility <= 0: Abort' / §7 edge row). A zero chosen
    # stake on a PRESENT fresh curve is an edge reversal: the recompute found no
    # positive-utility stake at ANY admissible size including min order (q_lcb fell,
    # cost rose, or exposure concavity killed it). NOTE: a below-min-order-but-positive
    # edge case is handled by GATE 3b above (distinct BELOW_MIN_ORDER), so reaching here
    # with stake 0 genuinely means no positive-ΔU stake exists. Surface as EDGE_REVERSED
    # directly — feeding stake_usd=0 to the engine would make avg_cost(0) raise (below
    # min order) and mis-map to PRICE_MOVED.
    if chosen_stake_usd <= 0.0:
        return (
            SubmitRecaptureDecision(
                state=CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED,
                may_submit=False,
                reversal_reason=ReversalReason.EDGE,
                detail=(
                    "recaptured robust marginal utility nonpositive: no positive-ΔU "
                    "stake clears min order / depth on the fresh curve (§5 'utility "
                    "<= 0: Abort'; §7 edge row)"
                ),
            ),
            0.0,
            None,
        )

    # GATE 4 + 5 (price moved / edge<=0 / forecast stale). The decision-time admitted
    # price is the band ceiling: a fresh recapture pricing strictly worse than what the
    # candidate was admitted at is a price move (§7 price row). family_rank_reversed is
    # False here (gate 2 already passed), so the engine resolves the remaining price /
    # edge / forecast-currency gates.
    decision_time_price = selected_proof.execution_price
    max_acceptable_price = (
        Decimal(str(decision_time_price.value))
        if decision_time_price is not None
        else Decimal("0")
    )
    decision = engine.evaluate_submit_recapture(
        candidate,
        RecaptureInputs(
            recaptured_cost_curve=fresh_curve,
            stake_usd=Decimal(str(chosen_stake_usd)),
            max_acceptable_price=max_acceptable_price,
            recaptured_q_lcb=float(candidate.q_lcb),
            forecast_still_current=bool(forecast_still_current),
            family_rank_reversed=False,
            # MAKER (resting GTC/GTD limit at the admitted price) skips the PRICE_MOVED
            # ceiling: the order rests, never chases the recaptured ask. TAKER (FOK/FAK)
            # crosses and pays the recaptured cost, so the bounded slippage tolerance
            # governs the ceiling. Fail-closed False (taker ceiling) when the mode is
            # not supplied.
            order_rests_at_admitted_price=bool(order_rests_at_admitted_price),
        ),
    )
    if decision.may_submit:
        return decision, float(chosen_stake_usd), chosen_price
    return decision, 0.0, None


def _robust_marginal_utility_optimal_stake_usd(
    *,
    family_key: str,
    selected_proof: _CandidateProof,
    all_proofs: tuple[_CandidateProof, ...] | list[_CandidateProof],
    extra_exposure_by_bin_id: Mapping[str, float] | None,
    bankroll_usd: float,
    kelly_multiplier: float,
) -> float:
    """Exposure-aware FRACTIONAL-Kelly stake for the selected leg (spec §3 / §5.2 / §5.3).

    Thin wrapper over :func:`_robust_marginal_utility_stake_and_price` that returns
    only the stake (USD). The size authority for the live intent (S4): the ΔU
    optimizer's ``optimal_stake_usd`` on the candidate's OWN robust q_lcb-based π,
    scaled by the fractional-Kelly haircut. ``0.0`` when no positive-ΔU stake clears
    min order / depth (a no-trade). See the kernel for the full derivation; the live
    decision body uses the kernel directly so it also gets the chosen-stake price.
    """
    stake_usd, _price = _robust_marginal_utility_stake_and_price(
        family_key=family_key,
        selected_proof=selected_proof,
        all_proofs=all_proofs,
        extra_exposure_by_bin_id=extra_exposure_by_bin_id,
        bankroll_usd=bankroll_usd,
        kelly_multiplier=kelly_multiplier,
    )
    return stake_usd


def _family_existing_exposure_by_bin_id(
    *,
    proofs: tuple[_CandidateProof, ...] | list[_CandidateProof],
    selected_proof: _CandidateProof,
    portfolio_state_provider: "Callable[[], Any] | None",
    portfolio_reservation: "PortfolioReservationLedger | list[tuple[str, float]] | None",
    family,
) -> dict[str, float]:
    """Existing/pending per-outcome family exposure for the ΔU sizing vector (§11 Phase 4).

    Maps the family's ALREADY-committed and same-cycle-RESERVED capital onto the
    win OUTCOME it backs, so the marginal-utility optimizer measures the new leg's
    stake against a wealth-by-outcome baseline that already reflects that exposure
    (Hidden #10: a new leg correlated with existing exposure sizes smaller / can
    no-trade). Keyed by ``_candidate_bin_id`` so it lines up with the candidates'
    bins.

    Provenance scope (what we can attribute at THIS phase): the portfolio state
    tracks committed capital by CITY, and the same-cycle reservation ledger by
    city, not yet by bin/outcome. The selected leg's own bin is the only outcome
    whose direction we know here, so SAME-CITY committed + same-cycle reserved
    capital is attributed to the selected proof's bin (the conservative, no-double-
    count choice — it shrinks the marginal stake on the bin the book is already
    leaning into). Returns ``{}`` (flat baseline, no exposure) when no provider /
    reservation is wired or no same-city exposure exists, reproducing the prior
    behavior exactly.
    """
    selected_bin_id = _candidate_bin_id(selected_proof)
    city = str(getattr(family, "city", "") or "")
    exposure_usd = 0.0

    # Same-cycle reservations for this family's city (already-emitted-but-unfilled
    # stakes this cycle). PortfolioReservationLedger and a plain list both iterate
    # as (city, usd) pairs.
    if portfolio_reservation is not None:
        try:
            for res_city, usd in portfolio_reservation:
                if str(res_city) == city:
                    exposure_usd += float(usd)
        except (TypeError, ValueError):
            pass

    # Committed capital already on the book for this city (open positions).
    if portfolio_state_provider is not None:
        try:
            from src.state.portfolio import correlated_committed_usd

            state = portfolio_state_provider()
            if state is not None:
                exposure_usd += float(
                    correlated_committed_usd(state, new_city=city, extra_reserved=None)
                )
        except (TypeError, ValueError, ImportError):
            pass

    if exposure_usd <= 0.0:
        return {}
    return {selected_bin_id: exposure_usd}


def _selected_candidate_proof(
    payload: dict[str, object],
    proofs: tuple[_CandidateProof, ...],
    *,
    locked_opportunity_conn: sqlite3.Connection | None = None,
) -> _CandidateProof | None:
    """Pick the single live primary leg via the bin-selection ΔU ranker (§14.7).

    ONE decision path (operator directive 2026-06-08; spec §14 item 8): every
    priced proof is materialized as a ``NativeSideCandidate`` and the family
    primary is the robust-marginal-expected-log-utility winner
    (:func:`_select_proof_by_robust_marginal_utility`). The legacy scalar-Kelly
    surfaces (``select_best_family_candidate`` / the ``(trade_score, q_lcb_5pct)``
    tuple) and the off-able family-selector runtime gate (its env var + settings
    key) are GONE — the ranker is unconditional, so the materialized candidate is
    the decision, never discarded.

    Sizing is computed POST-selection on the winning leg by
    :func:`_robust_marginal_utility_optimal_stake_usd` (the ΔU optimizer's
    ``optimal_stake_usd`` on the candidate's robust q_lcb — spec §3/§5.3); there is
    no pre-selection scalar-Kelly size threaded here.
    """
    family_key = str(payload.get("family_id") or payload.get("event_id") or "family")
    per_bin_yes_q_lcb = _per_bin_yes_q_lcb(proofs)

    # A ``token_id`` in the payload is a continuous-redecision REFRESH SCOPE (it
    # tells the upstream proof-generation which leg to re-capture), NOT a forced
    # selection: the family selector still ranks the WHOLE family and picks the
    # best sibling by ΔU. (Contract: test_token_redecision_refresh_scope_does_not_
    # force_requested_token / test_opportunity_book_selector_is_default_on_for_
    # requested_token.) So there is no requested-token branch here — one ranking
    # surface for every decision.

    # REFERENCE-ONLY GATE (operator directive 2026-06-03). The mainstream-agreement
    # verdict (#135 + #135-B) is recorded on the receipt to inform the ARM decision;
    # it takes NO part in production selection — we trade on the FORECAST. The gate
    # can never exclude a candidate from the ΔU ranking below.
    executable = list(
        _selection_scoped_proofs(
            proofs=proofs,
            locked_opportunity_conn=locked_opportunity_conn,
        )
    )
    if not executable:
        # Nothing executable survived scoping. Surface the best-belief NON-executable
        # proof (execution_price None) so the EXECUTABLE_NATIVE_ASK_MISSING receipt
        # (era :1562) can explain the no-native-ask no-trade rather than vanishing
        # silently. A proof that IS executable but was scoped OUT (e.g. locked with
        # no price improvement) must NOT be re-surfaced as the decision — that would
        # trade a locked leg. So the fallback is restricted to non-executable proofs;
        # if there is none, the family no-trades (returns None).
        non_executable = [
            proof for proof in proofs if proof.execution_price is None
        ]
        if not non_executable:
            return None
        return max(non_executable, key=lambda proof: proof.q_lcb_5pct)

    return _select_proof_by_robust_marginal_utility(
        executable=executable,
        family_key=family_key,
        per_bin_yes_q_lcb=per_bin_yes_q_lcb,
    )


def _native_direction(value: object) -> str:
    return str(value or "").split(":", 1)[0]


def _replacement_live_authority_proof_for_direction(
    *,
    proofs: tuple[_CandidateProof, ...],
    baseline_proof: _CandidateProof,
    effective_direction: str,
) -> _CandidateProof | None:
    target_direction = _native_direction(effective_direction)
    if target_direction == baseline_proof.direction:
        return baseline_proof
    condition_id = str(baseline_proof.candidate.condition_id or "")
    for proof in proofs:
        if str(proof.candidate.condition_id or "") != condition_id:
            continue
        if proof.direction != target_direction:
            continue
        if proof.execution_price is None:
            continue
        if _candidate_limit_price_untradeable_reason(proof) is not None:
            continue
        return proof
    return None


def _replacement_primary_authority_already_applied(proof: _CandidateProof) -> bool:
    return str(getattr(proof, "q_source", "") or "") == "replacement_0_1"


def _locked_candidate_no_price_improvement_reason(
    live_cap_conn: sqlite3.Connection | None,
    proof: _CandidateProof,
) -> str | None:
    execution_price = getattr(proof, "execution_price", None)
    limit_price = _optional_float(getattr(execution_price, "value", None))
    return _locked_live_opportunity_no_price_improvement_reason(
        live_cap_conn,
        condition_id=str(getattr(getattr(proof, "candidate", None), "condition_id", "") or ""),
        token_id=str(getattr(proof, "token_id", "") or ""),
        direction=str(getattr(proof, "direction", "") or ""),
        side="SELL" if str(getattr(proof, "direction", "") or "").startswith("sell_") else "BUY",
        limit_price=limit_price,
    )


def _candidate_limit_price_untradeable_reason(proof: _CandidateProof) -> str | None:
    execution_price = getattr(proof, "execution_price", None)
    limit_price = _optional_float(getattr(execution_price, "value", None))
    if limit_price is None:
        return "EXECUTION_PRICE_MISSING"
    min_tick = _candidate_min_tick_size(proof)
    if min_tick is None:
        min_tick = 0.01
    if min_tick <= 0.0:
        return f"EXECUTION_PRICE_MIN_TICK_INVALID:{min_tick!r}"
    if limit_price < min_tick - 1e-12:
        return (
            "EXECUTION_PRICE_BELOW_MIN_TICK:"
            f"limit_price={limit_price:.12g}:min_tick_size={min_tick:.12g}"
        )
    return None


def _candidate_min_tick_size(proof: _CandidateProof) -> float | None:
    row = getattr(proof, "row", None)
    if row is None:
        return None
    getter = getattr(row, "get", None)
    for key in ("min_tick_size", "tick_size"):
        try:
            raw = getter(key) if callable(getter) else row[key]
        except Exception:
            raw = None
        value = _optional_float(raw)
        if value is not None:
            return value
    return None


def _live_yes_probabilities(
    *,
    event: OpportunityEvent,
    payload: dict[str, object],
    family,
    conn: sqlite3.Connection,
    calibration_conn: sqlite3.Connection,
    native_costs: dict[tuple[str, str], tuple[dict[str, Any] | None, ExecutionPrice | None, float, float | None, str | None]],
    decision_time: datetime,
    promotion_evidence: ReplacementForecastPromotionEvidence | None = None,
    capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None = None,
) -> tuple[
    dict[str, float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], bool],
    dict[str, str],
]:
    # 2026-05-30: canonical kernel reconstructed (snapshot fetch + MarketAnalysis assembly +
    # hypothesis-family scan + evaluate_live_bins). Gated by the acceptance suite in
    # tests/engine/test_event_reactor_no_bypass.py; SHADOW until #24 bias. See task Break-4.
    if event.event_type == "FORECAST_SNAPSHOT_READY":
        # FIX-1 Insertion A: thread the settlement-evidence objects (loaded once in
        # main.py, carried through the adapter closure) into the live 0.1 authority
        # builder so the shared gate runs on the path that is actually live.
        replacement = _replacement_authority_probability_and_fdr_proof(
            event=event,
            payload=payload,
            family=family,
            conn=conn,
            native_costs=native_costs,
            decision_time=decision_time,
            promotion_evidence=promotion_evidence,
            capital_objective_evidence=capital_objective_evidence,
        )
        if replacement is not None:
            return replacement
        return _canonical_probability_and_fdr_proof(
            event=event,
            payload=payload,
            family=family,
            conn=conn,
            calibration_conn=calibration_conn,
            native_costs=native_costs,
            decision_time=decision_time,
        )
    if event.event_type == "DAY0_EXTREME_UPDATED":
        # ORACLE ANOMALY PAUSE (day0 review 2026-06-10 item E, Paris-CDG class):
        # when the WU-vs-METAR divergence detector has flagged this family's
        # (city, target_date), no day0 q may be built — the running extreme's
        # truth source is suspect (sensor tampering / feed fault). Raising here
        # converts to a deterministic no-submit receipt at the
        # _generate_candidate_proofs ValueError boundary
        # (LIVE_INFERENCE_INPUTS_MISSING:DAY0_ORACLE_ANOMALY_PAUSED:...).
        from src.data.day0_oracle_anomaly import is_day0_family_paused

        if is_day0_family_paused(str(family.city), str(family.target_date)):
            raise ValueError(
                f"DAY0_ORACLE_ANOMALY_PAUSED:{family.city}:{family.target_date}"
            )
        generated = _canonical_probability_and_fdr_proof(
            event=event,
            payload=payload,
            family=family,
            conn=conn,
            calibration_conn=calibration_conn,
            native_costs=native_costs,
            allow_latest_snapshot=True,
            decision_time=decision_time,
        )
        q_by_condition, lcb_by_condition, p_values, prefilter, evidence = generated
        masked_q, masked_lcb = _apply_day0_mask_to_generated_probabilities(
            payload=payload,
            family=family,
            q_by_condition=q_by_condition,
            lcb_by_condition=lcb_by_condition,
            decision_time=decision_time,
        )
        return masked_q, masked_lcb, p_values, prefilter, {
            **evidence,
            "p_live_vector_hash": _probability_vector_hash(
                masked_q[str(candidate.condition_id or "")]
                for candidate in family.candidates
            ),
        }
    raise ValueError(f"unsupported EDLI event type for inference: {event.event_type}")


# FIX 1 (2026-06-09) — replacement q-mode live eligibility gate. The materializer derives an
# explicit `replacement_q_mode` into provenance_json (FUSED_NORMAL_FULL/PARTIAL,
# SOFT_ANCHOR_FALLBACK, U0R_CAPTURE_MISSING, FUSED_Q_BUILD_FAILED). Real submit is allowed ONLY
# for the two fused-Normal modes (the constructed Normal shape the release evidence assumes).
# This kills the silent-degradation category: a row that fell back to the legacy member-vote
# soft-anchor q (or had no fusion at all) must NOT size live Kelly under the wrong probability
# regime just because all flags were on.
_REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL = "FUSED_NORMAL_FULL"
_REPLACEMENT_Q_MODE_FUSED_NORMAL_PARTIAL = "FUSED_NORMAL_PARTIAL"
_REPLACEMENT_Q_MODE_LIVE_ELIGIBLE = frozenset(
    {_REPLACEMENT_Q_MODE_FUSED_NORMAL_FULL, _REPLACEMENT_Q_MODE_FUSED_NORMAL_PARTIAL}
)


def _replacement_q_mode_live_eligibility(replacement_bundle: object) -> tuple[bool, str]:
    """Return (live_eligible, mode) for a replacement posterior bundle. Fail-closed.

    Reads provenance_json.replacement_q_mode. Eligible ONLY for the two fused-Normal modes.

    GRANDFATHERING: pre-change live rows (the 67 already in the DB) have NO replacement_q_mode key
    but were built with q_shape=="fused_normal_direct" (the constructed fused-Normal shape) — those
    are treated as a fused-Normal mode so this change does not brick them; the next materialization
    adds the explicit key. A row with NO mode key AND q_shape != "fused_normal_direct" is the legacy
    member-vote soft-anchor shape and is NOT live-eligible (fail-closed).
    """
    provenance = getattr(replacement_bundle, "provenance_json", None)
    if not isinstance(provenance, Mapping):
        provenance = {}
    mode = provenance.get("replacement_q_mode")
    if isinstance(mode, str) and mode:
        return (mode in _REPLACEMENT_Q_MODE_LIVE_ELIGIBLE, mode)
    # No explicit mode key: grandfather only the constructed fused-Normal shape.
    q_shape = str(provenance.get("q_shape") or "")
    if q_shape == "fused_normal_direct":
        return (True, "FUSED_NORMAL_GRANDFATHERED")
    return (False, "NO_Q_MODE_KEY")


def _replacement_authority_enabled() -> bool:
    try:
        flags = settings["feature_flags"]
    except Exception:
        return False
    return bool(flags.get("openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled", False))


def _replacement_qlcb_settlement_sigma_floor_enabled() -> bool:
    """QLCB_HONESTY.md FIX-C flag (default FALSE). When OFF the replacement q_lcb is
    byte-identical to the raw Wilson/bundle value — the settlement σ-floor is NEVER
    consulted, no DB/table read on this path. When ON the per-bin q_lcb is floored at
    the realized-settlement residual (settlement_sigma_floor) so a tight member cluster
    cannot manufacture an overconfident lower bound (iron rule #6)."""
    try:
        return bool(settings["edli_v1"].get("replacement_qlcb_settlement_sigma_floor_enabled", False))
    except Exception:
        return False


def _replacement_settlement_grounded_lcb(
    *,
    mu_c: float,
    sigma_floor_c: float,
    sigma_model_c: float | None,
    lower_c: float | None,
    upper_c: float | None,
) -> float:
    """The settlement-grounded YES-bin q_lcb under ``N(mu_c, max(sigma_model_c, sigma_floor_c))``.

    QLCB_HONESTY.md §2 Construction B root cause: the live replacement q_lcb is sized
    from the ~0.67C member spread (Wilson over 51 AIFS votes), ignoring the ~3.2x
    settlement underdispersion. This integrates the WMO settlement preimage of the bin
    under a Gaussian whose σ is FLOORED at the per-(city,season,metric) realized residual
    (settlement_sigma_floor, median 3.18C). The floor only WIDENS σ → LOWERS the q_lcb
    (never tightens), so caller's ``min(raw_wilson, this)`` is ONLY-LOWERS by construction.

    ``sigma_model_c`` (the AIFS member spread, when carried) participates via ``max`` so a
    legitimately-wider model σ is preserved; ``None`` (the usual live case — provenance
    carries vote frequencies, not a member std) means the floor is the effective σ. Uses
    the SAME ``bin_probability_settlement`` (WMO round-half-up preimage) the canonical/EMOS
    path uses, so the grounded mass matches the settlement grading semantics.
    """
    floor = float(sigma_floor_c)
    if not (floor > 0.0):
        # No usable floor — degrade to a non-binding ceiling (caller's min() keeps raw).
        return 1.0
    sigma_eff = floor if sigma_model_c is None else max(float(sigma_model_c), floor)
    if not (sigma_eff > 0.0):
        return 1.0
    grounded = _bin_probability_settlement(float(mu_c), sigma_eff, lower_c, upper_c)
    return float(min(max(grounded, 0.0), 1.0))


# QLCB_HONESTY.md FIX-C honest reason code: a missing floor input on the LIVE replacement
# q_lcb path. Surfaced as a ValueError so it propagates through _live_yes_probabilities ->
# _generate_candidate_proofs, where the existing `except ValueError` (era.py:1388) converts
# it into a LIVE_INFERENCE_INPUTS_MISSING no-submit receipt — i.e. the candidate is BLOCKED,
# no order is placed. Module-level constant so the reason code is the single source of truth
# for the production raiser AND its antibody tests.
REPLACEMENT_QLCB_FLOOR_MISSING_LIVE_BLOCK = "REPLACEMENT_0_1_LIVE_AUTHORITY_QLCB_FLOOR_MISSING"


def _resolve_replacement_settlement_floor_lcb(
    *,
    live_authority: bool,
    city: str,
    condition_id: str,
    bin_id: str,
    anchor_mu_c: float | None,
    sigma_floor_c: float | None,
    bounds: tuple[float | None, float | None] | None,
) -> float | None:
    """The per-bin settlement-grounded q_lcb ceiling, with the BLOCKER 7 mode split.

    QLCB_HONESTY.md FIX-C exists because the raw Wilson q_lcb over the 51 AIFS votes ignores
    the ~3.2x settlement underdispersion — it is an OVERCONFIDENT lower bound. The floor caps
    it at the realized-settlement residual. When the floor is ENABLED (the caller only invokes
    this helper in that case) but a floor input is MISSING — no anchor μ, no σ-floor cell, or
    no bin topology — there is no settlement-grounded ceiling to compute. The mode then decides
    the SEMANTICS of that miss (PR#400 the_path audit BLOCKER 7):

      - ``live_authority=True`` (LIVE / authority / capital): degrading to the raw Wilson value
        re-emits the exact overconfident bound the floor exists to fix, and that bound would
        size real capital. That is UNSAFE. Raise ``ValueError`` so the candidate is BLOCKED
        (the caller's ValueError handler turns it into a no-submit receipt). NEVER pass raw.

      - ``live_authority=False`` (SHADOW / observation only, no capital at risk): keep the
        current fail-soft behavior — emit a queryable raw-fallback log record and return
        ``None`` so the caller keeps the raw bound for measurement.

    Returns the grounded ceiling (a YES-bin probability in [0,1]) when all inputs are present,
    in BOTH modes — the floor still floors; only the MISSING case is mode-dependent. ``None``
    is returned ONLY in shadow mode on a missing input (live mode raises instead).
    """
    import logging as _logging  # module uses lazy per-fn logging imports
    if anchor_mu_c is None or sigma_floor_c is None or bounds is None:
        missing = (
            "anchor_mu" if anchor_mu_c is None
            else "sigma_floor_cell" if sigma_floor_c is None
            else "bin_topology"
        )
        if live_authority:
            # Iron rule #6 + BLOCKER 7: a missing floor on the live path BLOCKS the candidate;
            # it must never leak the raw overconfident Wilson bound to capital sizing.
            raise ValueError(
                f"{REPLACEMENT_QLCB_FLOOR_MISSING_LIVE_BLOCK}:{condition_id}:bin={bin_id}:missing={missing}"
            )
        _logging.getLogger("zeus.replacement_qlcb_shadow").warning(
            "replacement q_lcb floor missing (shadow: raw fallback kept) city=%s cond=%s "
            "bin=%s missing=%s",
            city, condition_id, bin_id, missing,
        )
        return None
    return _replacement_settlement_grounded_lcb(
        mu_c=float(anchor_mu_c),
        sigma_floor_c=float(sigma_floor_c),
        # AIFS member std is not carried in provenance (vote frequencies only); None → the
        # floor IS the effective σ (not the tight, underdispersed member spread).
        sigma_model_c=None,
        lower_c=bounds[0],
        upper_c=bounds[1],
    )


def _wilson_lower_bound(successes: float, trials: float, *, z: float = 1.645) -> float:
    if trials <= 0.0:
        return 0.0
    successes = min(max(float(successes), 0.0), float(trials))
    p_hat = successes / float(trials)
    z2 = z * z
    denom = 1.0 + z2 / float(trials)
    center = p_hat + z2 / (2.0 * float(trials))
    margin = z * float(np.sqrt((p_hat - (p_hat * p_hat) + z2 / (4.0 * float(trials))) / float(trials)))
    return max(0.0, min(1.0, (center - margin) / denom))


def _candidate_replacement_bin_id(candidate: object, replacement_bundle: object) -> str | None:
    provenance = getattr(replacement_bundle, "provenance_json", None) or {}
    if not isinstance(provenance, Mapping):
        return None
    topology = provenance.get("bin_topology")
    if not isinstance(topology, list) or not topology:
        return None
    bin_obj = getattr(candidate, "bin", None)
    unit = str(getattr(bin_obj, "unit", "") or "")
    if not unit:
        return None
    lower_c = _replacement_bound_to_c(getattr(bin_obj, "low", None), unit=unit)
    upper_c = _replacement_bound_to_c(getattr(bin_obj, "high", None), unit=unit)
    matches: list[str] = []
    for item in topology:
        if not isinstance(item, Mapping):
            continue
        bin_id = str(item.get("bin_id") or "").strip()
        if not bin_id:
            continue
        item_lower = item.get("lower_c")
        item_upper = item.get("upper_c")
        lower_ok = (item_lower is None and lower_c is None) or (
            item_lower is not None and lower_c is not None and math.isclose(float(item_lower), float(lower_c), rel_tol=0.0, abs_tol=1e-9)
        )
        upper_ok = (item_upper is None and upper_c is None) or (
            item_upper is not None and upper_c is not None and math.isclose(float(item_upper), float(upper_c), rel_tol=0.0, abs_tol=1e-9)
        )
        if lower_ok and upper_ok:
            matches.append(bin_id)
    if len(matches) != 1:
        return None
    return matches[0]


def _replacement_bound_to_c(value: object, *, unit: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if unit == "C":
        return number
    if unit == "F":
        return (number - 32.0) * 5.0 / 9.0
    raise ValueError("replacement candidate bin unit must be C or F")


def _replacement_bin_bounds_c(replacement_bundle: object, bin_id: str) -> tuple[float | None, float | None] | None:
    """The (lower_c, upper_c) of ``bin_id`` from the bundle's bin_topology (°C).

    ``None`` shoulders are open ends (e.g. an "X or below" floor bin has lower_c None);
    the settlement-preimage integrator handles them. Returns ``None`` when the topology
    is absent/malformed so the caller skips the floor (degrade, never crash the hot path).
    """
    provenance = getattr(replacement_bundle, "provenance_json", None) or {}
    if not isinstance(provenance, Mapping):
        return None
    topology = provenance.get("bin_topology")
    if not isinstance(topology, list):
        return None
    for item in topology:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("bin_id") or "").strip() != bin_id:
            continue
        lower = item.get("lower_c")
        upper = item.get("upper_c")
        return (
            None if lower is None else float(lower),
            None if upper is None else float(upper),
        )
    return None


def _replacement_anchor_mu_c(replacement_bundle: object) -> float | None:
    """The soft-anchor point estimate μ (°C) the bundle was built around.

    Read from ``provenance_json.anchor_value_c`` (the deterministic IFS9 anchor the AIFS
    prior is fused with; confirmed present on all live posteriors). ``None`` when absent
    so the floor is skipped (degrade, never crash)."""
    provenance = getattr(replacement_bundle, "provenance_json", None) or {}
    if not isinstance(provenance, Mapping):
        return None
    value = provenance.get("anchor_value_c")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _replacement_yes_lcb_for_bin(
    replacement_bundle: object,
    *,
    bin_id: str,
    q_yes: float,
    settlement_floor_lcb: float | None = None,
) -> float:
    """Raw replacement YES q_lcb (bundle q_lcb map, else Wilson over AIFS votes).

    QLCB_HONESTY.md FIX-C: ``settlement_floor_lcb`` is the settlement-grounded ceiling
    (``_replacement_settlement_grounded_lcb``); when supplied (flag ON) the returned
    bound is ``min(raw, settlement_floor_lcb)`` so it ONLY-LOWERS — the floor can never
    raise the q_lcb. ``None`` (the default, flag OFF) keeps the result byte-identical to
    the pre-fix Wilson/bundle value.
    """
    def _apply_floor(raw: float) -> float:
        if settlement_floor_lcb is None:
            return raw
        return min(raw, float(settlement_floor_lcb))

    q_lcb = getattr(replacement_bundle, "q_lcb", None) or {}
    if isinstance(q_lcb, Mapping) and bin_id in q_lcb:
        return _apply_floor(min(max(float(q_lcb[bin_id]), 0.0), max(0.0, min(float(q_yes), 1.0))))
    provenance = getattr(replacement_bundle, "provenance_json", None) or {}
    aifs_probabilities = provenance.get("aifs_probabilities") if isinstance(provenance, Mapping) else None
    if isinstance(aifs_probabilities, Mapping) and bin_id in aifs_probabilities:
        try:
            member_count = float(provenance.get("aifs_member_count") or 51.0)
            successes = float(aifs_probabilities[bin_id]) * member_count
            return _apply_floor(min(max(float(q_yes), 0.0), _wilson_lower_bound(successes, member_count)))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _replacement_no_lcb_for_bin(
    replacement_bundle: object,
    *,
    bin_id: str,
    q_yes: float,
) -> float:
    """Native-NO robust q_lcb for ``bin_id`` on the replacement_0_1 path (Hidden #3).

    bin-selection §4 (lines 154-166) / §9 Hidden #3: the NO robust lower bound is the
    lower tail of the per-sample complement ``1 - q_yes``, which equals ``1 - q_ucb_yes``
    — NOT ``1 - q_lcb_yes``. The replacement bundle exposes a per-bin ``q_ucb`` map, and
    the complement identity is exact, so ``q_lcb_no = 1 - q_ucb_yes`` directly. Clamp into
    ``[0, 1 - q_yes]`` so the NO lower bound never exceeds the NO point ``1 - q_yes``
    (the ProbabilityUncertainty / NativeSideCandidate invariant, Hidden #2 on the NO leg).

    Absent a bundle ``q_ucb`` there is no native NO authority — return ``0.0``
    (fail-closed). It is NEVER derived from the YES q_lcb (the point-complement the spec
    forbids).
    """
    q_ucb = getattr(replacement_bundle, "q_ucb", None) or {}
    q_point_no = float(min(max(1.0 - float(q_yes), 0.0), 1.0))
    if isinstance(q_ucb, Mapping) and bin_id in q_ucb:
        try:
            q_ucb_yes = float(min(max(float(q_ucb[bin_id]), 0.0), 1.0))
        except (TypeError, ValueError):
            return 0.0
        q_lcb_no = 1.0 - q_ucb_yes
        return float(min(max(q_lcb_no, 0.0), q_point_no))
    return 0.0


def _replacement_authority_probability_and_fdr_proof(
    *,
    event: OpportunityEvent,
    payload: dict[str, object],
    family,
    conn: sqlite3.Connection,
    native_costs: dict[tuple[str, str], tuple[dict[str, Any] | None, ExecutionPrice | None, float, float | None, str | None]],
    decision_time: datetime,
    promotion_evidence: ReplacementForecastPromotionEvidence | None = None,
    capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None = None,
) -> tuple[
    dict[str, float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], bool],
    dict[str, str],
] | None:
    if not _replacement_authority_enabled():
        return None
    # OPERATOR DIRECTIVE 2026-06-08 — INSERTION A promotion/capital-objective EVIDENCE
    # GATE REMOVED (paired with the runtime_policy.py Insertion-B removal). It was the
    # second copy of the circular "prove after-cost before trading" bureaucracy and was
    # the actual binding blocker on the live FORECAST_SNAPSHOT_READY path: with the live
    # evidence file it returned permitted=False (INSUFFICIENT_OFFICIAL_DAYS/ROWS,
    # Q_LCB_COVERAGE_TOO_LOW, NESTED_WALK_FORWARD_NOT_PASSED) → return None → U0R silently
    # degraded to the legacy canonical kernel and never traded live. Live authority is
    # now granted by the trade_authority flag (_replacement_authority_enabled, above).
    # The REAL forward risk controls remain enforced BELOW and are untouched: readiness
    # freshness (READINESS_MISSING raise), the bundle gate (BUNDLE_BLOCKED), the q_lcb
    # settlement-sigma floor (QLCB_FLOOR_MISSING — blocks, never degrades to raw Wilson),
    # the direction law (re-derived from argmax(U0R.q) per bin), fractional Kelly, the
    # after-cost cost floor, and RiskGuard. Overconfidence is bounded FORWARD by q_lcb +
    # fractional Kelly and judged by settlement, not by a pre-trade evidence checklist.
    import logging as _logging
    from src.calibration.qlcb_provenance import QlcbByDirection, _set_qlcb_provenance
    from src.data.replacement_forecast_bundle_reader import read_replacement_forecast_bundle
    from src.engine.replacement_forecast_hook_factory import _latest_replacement_readiness

    readiness = _latest_replacement_readiness(
        conn,
        city=str(family.city),
        target_date=str(family.target_date),
        temperature_metric=str(family.metric),
    )
    if readiness is None:
        raise ValueError("REPLACEMENT_0_1_LIVE_AUTHORITY_READINESS_MISSING")
    bundle_result = read_replacement_forecast_bundle(
        conn,
        baseline_bundle=None,
        readiness=readiness,
        city=str(family.city),
        target_date=str(family.target_date),
        temperature_metric=str(family.metric),
        decision_time=decision_time,
        require_baseline_bundle=False,
    )
    if not bundle_result.ok or bundle_result.bundle is None:
        raise ValueError(f"REPLACEMENT_0_1_LIVE_AUTHORITY_BUNDLE_BLOCKED:{bundle_result.reason_code}")
    replacement_bundle = bundle_result.bundle
    # FIX 1 (2026-06-09) — q-mode live eligibility gate. Real submit is allowed ONLY when the
    # replacement posterior's q was built as a fused-Normal (FUSED_NORMAL_FULL/PARTIAL, or a
    # grandfathered q_shape=="fused_normal_direct" row). Every other mode (soft-anchor fallback,
    # capture missing, fused-q build failed, or a legacy non-fused row without the key) is a
    # deterministic no-submit: the row sizes Kelly under a DIFFERENT probability regime than the
    # release evidence assumes. Raising here becomes a LIVE_INFERENCE_INPUTS_MISSING no-submit
    # receipt at the caller (data-class check; fail-closed).
    _q_mode_eligible, _q_mode = _replacement_q_mode_live_eligibility(replacement_bundle)
    if not _q_mode_eligible:
        raise ValueError(f"REPLACEMENT_Q_MODE_NOT_LIVE_ELIGIBLE#{_q_mode}")
    q_by_condition: dict[str, float] = {}
    lcb_by_direction: QlcbByDirection = QlcbByDirection()
    p_values: dict[tuple[str, str], float] = {}
    prefilter: dict[tuple[str, str], bool] = {}
    q_map = replacement_bundle.q
    # QLCB_HONESTY.md FIX-C — settlement σ-floor inputs, resolved ONCE per family (flag
    # ON only). μ is the soft-anchor point (°C); σ-floor is the per-(city,season,metric)
    # realized-residual cell.
    #
    # PR#400 the_path audit BLOCKER 7 — this function is the LIVE replacement_0_1 authority
    # builder: it is reached after `_replacement_authority_enabled()` (TRADE_AUTHORITY flag)
    # ALONE. NOTE (operator directive 2026-06-08): the settlement-evidence promotion gate
    # `replacement_live_authority_evidence_gate` was REMOVED and NO LONGER gates this path
    # (zero call-sites; flag-only LIVE_AUTHORITY — see replacement_forecast_runtime_policy).
    # The q_lcb it returns is stamped probability_authority="replacement_0_1" and sizes REAL
    # capital. So the missing-floor mode here is unconditionally live/authority/capital.
    # A missing floor input must therefore BLOCK (never degrade to the raw, overconfident
    # Wilson bound) — both at family-setup time and per-bin. The block raises a ValueError
    # that the caller (_generate_candidate_proofs :1388) converts to a no-submit receipt.
    floor_enabled = _replacement_qlcb_settlement_sigma_floor_enabled()
    live_authority = True  # structural: see BLOCKER 7 note above (this is the live path).
    anchor_mu_c: float | None = None
    sigma_floor_c: float | None = None
    if floor_enabled:
        try:
            anchor_mu_c = _replacement_anchor_mu_c(replacement_bundle)
            from src.contracts.season import season_from_date

            city_obj = runtime_cities_by_name().get(family.city)
            lat = getattr(city_obj, "lat", 90.0) if city_obj else 90.0
            season = season_from_date(str(getattr(family, "target_date", "")), lat=lat)
            sigma_floor_c = settlement_sigma_floor(
                str(family.city), season, str(family.metric).lower()
            )
        except Exception as _floor_exc:  # noqa: BLE001
            # BLOCKER 7: setup failure on the LIVE path is NOT fail-soft-to-raw — keeping the
            # raw bound for every bin would size capital on the overconfident value the floor
            # exists to fix. Block the whole family (no submit) via the floor-missing code.
            _logging.getLogger("zeus.replacement_qlcb_shadow").warning(
                "replacement q_lcb floor setup failed on LIVE path (blocking, not raw): %s",
                _floor_exc,
            )
            raise ValueError(
                f"{REPLACEMENT_QLCB_FLOOR_MISSING_LIVE_BLOCK}:setup:{family.city}:{_floor_exc}"
            ) from _floor_exc
    for candidate in family.candidates:
        condition_id = str(candidate.condition_id or "")
        bin_id = _candidate_replacement_bin_id(candidate, replacement_bundle)
        if not bin_id or bin_id not in q_map:
            raise ValueError(f"REPLACEMENT_0_1_LIVE_AUTHORITY_BIN_BINDING_MISSING:{condition_id}")
        q_yes = min(max(float(q_map[bin_id]), 0.0), 1.0)
        # FIX-C + BLOCKER 7: the settlement-grounded ceiling for THIS bin (flag ON only). The
        # mode-aware resolver RAISES on a missing floor input when live_authority=True (here,
        # always — block the candidate, never raw); it would log+return None only in shadow.
        settlement_floor_lcb: float | None = None
        if floor_enabled:
            settlement_floor_lcb = _resolve_replacement_settlement_floor_lcb(
                live_authority=live_authority,
                city=str(family.city),
                condition_id=condition_id,
                bin_id=bin_id,
                anchor_mu_c=anchor_mu_c,
                sigma_floor_c=sigma_floor_c,
                bounds=_replacement_bin_bounds_c(replacement_bundle, bin_id),
            )
        claimed_yes_lcb = _replacement_yes_lcb_for_bin(
            replacement_bundle, bin_id=bin_id, q_yes=q_yes, settlement_floor_lcb=None
        )
        yes_lcb = _replacement_yes_lcb_for_bin(
            replacement_bundle,
            bin_id=bin_id,
            q_yes=q_yes,
            settlement_floor_lcb=settlement_floor_lcb,
        )
        # ITEM 3 — shadow-log claimed -> floored on EVERY replacement q_lcb decision so
        # live before/after validation data accrues from the next daemon run (the
        # coverage-shrunk value, when licensed, is logged separately by the K3 helper).
        _logging.getLogger("zeus.replacement_qlcb_shadow").info(
            "replacement q_lcb floor city=%s cond=%s bin=%s claimed=%.6f floored=%.6f "
            "floor_enabled=%s sigma_floor_c=%s anchor_mu_c=%s",
            family.city, condition_id, bin_id, claimed_yes_lcb, yes_lcb,
            floor_enabled,
            "None" if sigma_floor_c is None else f"{sigma_floor_c:.4f}",
            "None" if anchor_mu_c is None else f"{anchor_mu_c:.4f}",
        )
        q_by_condition[condition_id] = q_yes
        _set_qlcb_provenance(
            lcb_by_direction,
            (condition_id, "buy_yes"),
            yes_lcb,
            source="FORECAST_BOOTSTRAP",
        )
        # bin-selection S2 native-NO authority (Hidden #3, §4 lines 154-166): the NO
        # robust lower bound is the lower tail of the per-sample complement 1 - q_yes,
        # which equals 1 - q_ucb_yes. The replacement_0_1 bundle does not expose the YES
        # sample array (it reads a precomputed posterior), but it DOES carry the per-bin
        # q_ucb map — and the complement identity is exact, so q_lcb_no = 1 - q_ucb_yes
        # directly, NEVER 1 - q_lcb_yes (the point-complement the spec forbids). Clamp
        # under the NO point (1 - q_yes) so q_lcb_no <= q_point_no. Absent a bundle q_ucb
        # there is no native NO authority -> 0.0 (fail-closed; never a YES-complement).
        no_lcb = _replacement_no_lcb_for_bin(
            replacement_bundle, bin_id=bin_id, q_yes=q_yes
        )
        _set_qlcb_provenance(
            lcb_by_direction,
            (condition_id, "buy_no"),
            no_lcb,
            source="FORECAST_BOOTSTRAP",
        )
        yes_price = native_costs.get((condition_id, "buy_yes"), (None, None, 0.0, None, None))[1]
        yes_cost = float(yes_price.value) if yes_price is not None else 1.0
        yes_edge_lcb_positive = yes_price is not None and yes_lcb > yes_cost
        p_values[(condition_id, "buy_yes")] = 0.0 if yes_edge_lcb_positive else 1.0
        prefilter[(condition_id, "buy_yes")] = bool(yes_edge_lcb_positive)
        p_values[(condition_id, "buy_no")] = 1.0
        prefilter[(condition_id, "buy_no")] = False
    # ITEM 2 (FIX-B) — wire the EXISTING K3 settlement-backward-coverage shrink into the
    # LIVE replacement path (its sole prior call site was the canonical/EMOS path). SAME
    # helper, SAME flag (edli_v1.q_lcb_settlement_coverage_gate_enabled, default FALSE):
    # flag OFF → immediate no-op (byte-identical); flag ON → only ever LOWERS the q_lcb,
    # fails open. No-op (INSUFFICIENT_DATA) until ≥min_n replacement markets settle —
    # wired now so the protection is live the moment June fills resolve (one builder; no
    # duplicate helper).
    _maybe_apply_settlement_coverage_to_lcb(
        family=family,
        forecast_conn=conn,
        lcb_by_direction=lcb_by_direction,
    )
    payload["_edli_q_source"] = "replacement_0_1"
    return q_by_condition, lcb_by_direction, p_values, prefilter, {
        "probability_authority": "replacement_0_1",
        "posterior_id": str(replacement_bundle.posterior_id),
        "replacement_product_id": replacement_bundle.product_id,
        "p_cal_vector_hash": _probability_vector_hash(
            q_by_condition[str(candidate.condition_id or "")]
            for candidate in family.candidates
        ),
        "p_live_vector_hash": _probability_vector_hash(
            q_by_condition[str(candidate.condition_id or "")]
            for candidate in family.candidates
        ),
    }


def _forecast_snapshot_probability_and_fdr_proof(
    *,
    event: OpportunityEvent,
    family,
    conn: sqlite3.Connection,
    calibration_conn: sqlite3.Connection,
    native_costs: dict[tuple[str, str], tuple[dict[str, Any] | None, ExecutionPrice | None, float, float | None, str | None]],
    decision_time: datetime,
    allow_latest_snapshot: bool = False,
) -> tuple[
    dict[str, float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], bool],
    dict[str, str],
]:
    """
    FAIL-CLOSED STUB — codex never authored the EDLI probability + FDR inference kernel.

    The full implementation requires authoring EDLI's live-money probability
    semantics (Platt p_cal lookup, hypothesis bootstrap, FDR proof construction)
    which is out-of-scope for rebase-resolution. Until codex provides the
    canonical implementation, this stub returns empty mappings so:

      1. Module imports succeed (event reactor tests pass)
      2. Any production path reaching this function admits NO candidates
         (q_by_condition empty → no executable proofs → no_submit decision)
      3. Evidence dict explicitly documents the gap for downstream audit

    Returns an empty inference result. Do not "fill in" the empty dicts with
    placeholder probabilities — that would silently mis-trade.
    """
    q_by_condition: dict[str, float] = {}
    q_lcb_by_direction: dict[tuple[str, str], float] = {}
    generated_p_values: dict[tuple[str, str], float] = {}
    generated_prefilter: dict[tuple[str, str], bool] = {}
    probability_evidence: dict[str, str] = {
        "status": "no_submit_fail_closed",
        "reason": "edli_probability_kernel_unauthored",
        "TODO": "codex must implement _forecast_snapshot_probability_and_fdr_proof per EDLI v1 spec",
        "event_type": event.event_type,
        "allow_latest_snapshot": str(allow_latest_snapshot),
        "decision_time": decision_time.isoformat(),
    }
    return q_by_condition, q_lcb_by_direction, generated_p_values, generated_prefilter, probability_evidence


# ── bin-selection S2 q_lcb seam (Created 2026-06-08; Authority: "bin selection.md"
#    §4 belief/executable/portfolio spaces + §5.6 recommended q_lcb formula +
#    §9 Hidden #2/#3/#4 + §14.4 split-probability-from-edge + operator directive
#    2026-06-08 single-primary-live) ──────────────────────────────────────────────
# THE single computation that turns a bin's YES probability samples into the robust
# per-side q_lcb authority. It REPLACES the "q_lcb = edge_ci_lower + cost" restore
# (Hidden #2) and the "q_lcb_no = 1 - q_lcb_yes" point-complement (Hidden #3). Both
# q-construction builders (canonical bootstrap path AND replacement_0_1 bundle path)
# route their q_lcb through this ONE seam so there is one truth, not two parallel ones.
def _side_q_lcb_from_yes_samples(
    yes_samples,  # noqa: ANN001 - np.ndarray | sequence of YES probability samples
    *,
    q_yes_point: float,
) -> tuple[float, float]:
    """Return ``(q_lcb_yes, q_lcb_no)`` from ONE bin's YES probability samples.

    bin-selection §5.6 / §14.4::

        q_lcb_yes = lower_quantile(q_yes_samples)              # probability-only, Hidden #2
        q_lcb_no  = lower_quantile(1 - q_yes_samples)          # = 1 - q_ucb_yes, Hidden #3
                  = 1 - upper_quantile(q_yes_samples)

    ``q_lcb_no`` is the lower tail of the per-sample complement ``1 - q_yes`` — the
    native-NO robust lower bound (the bin's LOSE-outcome probability for a NO holder).
    It is emphatically NOT ``1 - q_lcb_yes`` (the point-complement intuition the spec
    forbids). The complement is taken via the blessed :func:`no_side_samples` so no
    independent NO forecast is ever rebuilt.

    Both bounds are clamped at the proof boundary so ``q_lcb_side <= q_point_side``
    (the NativeSideCandidate / ProbabilityUncertainty invariant): ``q_lcb_yes`` is
    floored under ``q_yes_point`` (the live inference point authority), and
    ``q_lcb_no`` under the point complement ``1 - q_yes_point``. This makes a
    probability lower bound that exceeds its own point — the edge_ci_lower-as-q_lcb
    signature (Hidden #2) — unconstructable on BOTH sides.
    """
    from src.strategy.probability_uncertainty import (
        lower_quantile,
        no_side_samples,
        probability_uncertainty_from_samples,
    )

    # YES authority: q_lcb is a pure function of the probability samples (never cost).
    pu_yes = probability_uncertainty_from_samples(yes_samples)
    q_point_yes = float(min(max(q_yes_point, 0.0), 1.0))
    q_lcb_yes = float(min(pu_yes.q_lcb, q_point_yes))

    # NO authority (Hidden #3): lower tail of (1 - q_yes_samples) == 1 - q_ucb_yes.
    q_lcb_no_raw = lower_quantile(no_side_samples(yes_samples))
    q_point_no = float(min(max(1.0 - q_yes_point, 0.0), 1.0))
    q_lcb_no = float(min(max(q_lcb_no_raw, 0.0), q_point_no))
    return q_lcb_yes, q_lcb_no


def _canonical_probability_and_fdr_proof(
    *,
    event: OpportunityEvent,
    payload: dict[str, object],
    family,
    conn: sqlite3.Connection,
    calibration_conn: sqlite3.Connection,
    native_costs: dict[tuple[str, str], tuple[dict[str, Any] | None, ExecutionPrice | None, float, float | None, str | None]],
    decision_time: datetime,
    allow_latest_snapshot: bool = False,
) -> tuple[
    dict[str, float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], float],
    dict[tuple[str, str], bool],
    dict[str, str],
]:
    snapshot = _forecast_snapshot_row_for_event(
        conn,
        event=event,
        family=family,
        allow_latest=allow_latest_snapshot,
        decision_time=decision_time,
    )
    if snapshot is None:
        if allow_latest_snapshot:
            raise ValueError("Day0 base forecast snapshot missing for event-bound inference")
        raise ValueError("causal forecast snapshot missing for event-bound inference")
    analysis = _market_analysis_from_event_snapshot(
        calibration_conn=calibration_conn,
        snapshot=snapshot,
        family=family,
        native_costs=native_costs,
        # #120/#149: the seam mutates payload['_edli_q_source'] (+ bias/grid flags);
        # must be the THREADED payload, not a fresh _payload(event) throwaway, or
        # the q_source provenance is set on a discarded dict and the proof reads None.
        payload=payload,
        decision_time=decision_time,
    )
    from src.strategy.market_analysis_family_scan import scan_full_hypothesis_family
    from src.config import edge_n_bootstrap

    hypotheses = scan_full_hypothesis_family(analysis, n_bootstrap=edge_n_bootstrap())
    hypothesis_by_label_direction = {
        (hypothesis.range_label, hypothesis.direction): hypothesis
        for hypothesis in hypotheses
    }
    q_by_condition: dict[str, float] = {}
    # K3 (Phase-2): the live q_lcb carrier is the typed QlcbByDirection — a bare
    # float is unconstructable at this boundary (it raises TypeError at __setitem__).
    # Every entry travels WITH its calibration provenance (FORECAST_BOOTSTRAP here,
    # EMOS_ANALYTIC on the licensed override, SETTLEMENT_ISOTONIC on the coverage
    # shrink). Consumers read the float back via _qlcb_float(...).q_lcb.
    from src.calibration.qlcb_provenance import QlcbByDirection, _set_qlcb_provenance
    lcb_by_direction: QlcbByDirection = QlcbByDirection()
    p_values: dict[tuple[str, str], float] = {}
    prefilter: dict[tuple[str, str], bool] = {}
    # Live FDR truth comes from the family hypothesis scan above (the same
    # scan_full_hypothesis_family / FullFamilyHypothesis the legacy evaluator uses),
    # keyed by (range_label, direction). Each hypothesis carries p_posterior (calibrated
    # forecast probability), bootstrap p_value, ci_lower, and prefilter. We read those
    # directly — no DB selection-fact round-trip — fail-closed if any bin/direction is absent.
    # bin-selection S2: the executable cost vectors that the OLD "q_lcb = ci_lower +
    # cost" restore added back are no longer read here — q_lcb is now a probability-only
    # bound (the cost lives only in the FDR edge engine and the trade-score). Removed
    # p_market_yes_vec / p_market_no_vec with the restore (no dead code).
    p_posterior_vec = np.asarray(analysis.p_posterior, dtype=float)
    for index, candidate in enumerate(family.candidates):
        condition_id = str(candidate.condition_id or "")
        range_label = candidate.bin.label
        # q-posterior is defined for EVERY bin from the calibrated forecast (market-independent),
        # so the full MECE family prior is always complete even for bins with no executable quote.
        yes_posterior = float(p_posterior_vec[index])
        q_by_condition[condition_id] = yes_posterior
        # bin-selection S2 (§5.6 / §14.4 / Hidden #2/#3): q_lcb is the lower quantile of
        # the per-bin YES *probability* samples q_yes^(b) ALONE — the SAME samples the
        # FDR edge CI draws (analysis.bin_yes_probability_samples), BEFORE the executable
        # cost is subtracted. This REPLACES the old "q_lcb = edge_ci_lower + cost" restore
        # (which was edge_ci_lower masquerading as a probability bound — Hidden #2). The
        # native-NO authority (Hidden #3) is q_lcb_no = lower_quantile(1 - q_yes^(b)) =
        # 1 - q_ucb_yes, NOT 1 - q_lcb_yes — computed in the ONE seam helper below.
        # FDR p_value / prefilter stay edge-space (read from hyp), unchanged: the q_lcb
        # split (§14.4) leaves the FDR inference gate on the proven MC edge engine.
        yes_hyp = hypothesis_by_label_direction.get((range_label, "buy_yes"))
        no_hyp = hypothesis_by_label_direction.get((range_label, "buy_no"))
        yes_executable = (
            yes_hyp is not None
            and yes_hyp.p_value is not None
            and yes_hyp.ci_lower is not None
        )
        if yes_executable:
            # ONE sample-producing path (market_analysis owns it); both q_lcb sides and
            # the FDR edge CI consume the same q_yes^(b) array. NO is the per-sample
            # complement of YES — a native NO authority, never an independent forecast.
            yes_samples = analysis.bin_yes_probability_samples(index, edge_n_bootstrap())
            q_lcb_yes, q_lcb_no = _side_q_lcb_from_yes_samples(
                yes_samples, q_yes_point=yes_posterior
            )
            p_values[(condition_id, "buy_yes")] = float(yes_hyp.p_value)
            _set_qlcb_provenance(
                lcb_by_direction,
                (condition_id, "buy_yes"),
                q_lcb_yes,
                source="FORECAST_BOOTSTRAP",
            )
            prefilter[(condition_id, "buy_yes")] = bool(yes_hyp.passed_prefilter)
            # NO direction: q_lcb_no is the native-NO robust lower bound (1 - q_ucb_yes).
            # FDR for NO follows the NO hypothesis when the NO side is executable; absent
            # a NO hypothesis the NO direction is recorded non-actionable (rejected
            # downstream by the missing native NO ask, never a complement price).
            _set_qlcb_provenance(
                lcb_by_direction,
                (condition_id, "buy_no"),
                q_lcb_no,
                source="FORECAST_BOOTSTRAP",
            )
            if no_hyp is not None and no_hyp.p_value is not None:
                p_values[(condition_id, "buy_no")] = float(no_hyp.p_value)
                prefilter[(condition_id, "buy_no")] = bool(no_hyp.passed_prefilter)
            else:
                p_values[(condition_id, "buy_no")] = 1.0
                prefilter[(condition_id, "buy_no")] = False
        else:
            # scan_full_hypothesis_family skips a bin entirely when its YES side has no
            # executable market. Emit neutral, non-actionable values for BOTH directions:
            # the directions are then rejected downstream by the missing native execution
            # price (EXECUTABLE_NATIVE_ASK_MISSING), not by a family-level fail-closed
            # raise. q_lcb_no is 0.0 here (no samples => no native NO authority), never a
            # YES-complement (Hidden #4: native NO needs native evidence).
            for direction in ("buy_yes", "buy_no"):
                q_point = yes_posterior if direction == "buy_yes" else 0.0
                p_values[(condition_id, direction)] = 1.0
                _set_qlcb_provenance(
                    lcb_by_direction,
                    (condition_id, direction),
                    q_point,
                    source="FORECAST_BOOTSTRAP",
                )
                prefilter[(condition_id, direction)] = False

    # EMOS-CI LIVE OVERRIDE (Option B, 2026-06-02, /tmp/design_emos_ci.md §6).
    # Replace the MC q_5pct (lcb_by_direction) with the coverage-honest EMOS analytic CI
    # for LICENSED HIGH-metric cities only. DEFAULT OFF — no live decision change unless
    # the operator flips edli_v1.edli_emos_ci_live_enabled AND adds the city to
    # state/emos_ci_license.json. Touches ONLY the q_5pct term the robust trade-score
    # consumes; hyp.p_value / prefilter (the FDR edge-space gate) stay on the proven MC
    # engine (lowest blast radius). FAIL-CLOSED: any missing EMOS / exception keeps the
    # MC lcb (never crash, never substitute a wrong value).
    _maybe_override_lcb_with_emos_ci(
        family=family,
        snapshot=snapshot,
        analysis=analysis,
        native_costs=native_costs,
        payload=_payload(event),
        lcb_by_direction=lcb_by_direction,
    )

    # K3 (Phase-2) SETTLEMENT-BACKWARD COVERAGE — license each q_lcb against the
    # REALIZED settlement win-rate in its band, shrinking an UNLICENSED band to the
    # realized rate minus 1pp (source SETTLEMENT_ISOTONIC). SHADOW FLAG, DEFAULT OFF
    # (edli_v1.q_lcb_settlement_coverage_gate_enabled): with the flag OFF this is a
    # pure no-op and the q_lcb is byte-identical to the EMOS/MC value above. The
    # coverage table is built ONLY through the spine grade_receipt. FAIL-OPEN: any
    # error keeps the upstream lcb (never crash, never widen optimistically).
    _maybe_apply_settlement_coverage_to_lcb(
        family=family,
        forecast_conn=conn,
        lcb_by_direction=lcb_by_direction,
    )

    from src.strategy.live_inference.inference_engine import InferenceInputs, evaluate_live_bins

    prior = tuple(max(q_by_condition[str(candidate.condition_id or "")], 1e-9) for candidate in family.candidates)
    live_state = evaluate_live_bins(
        InferenceInputs(
            prior=prior,
            forecast_complete=True,
            orderbook_event=False,
        )
    )
    for index, candidate in enumerate(family.candidates):
        condition_id = str(candidate.condition_id or "")
        q_value = float(live_state.probabilities[str(index)])
        q_by_condition[condition_id] = q_value
        # bin-selection S2 / #176 structural re-grounding: q_lcb above was computed from
        # the bootstrap samples and clamped under the analysis p_posterior. evaluate_live_bins
        # renormalises the point (prior -> live), and that renormalisation is the OTHER
        # source of a q_lcb > q_point inversion (the two modules normalise differently —
        # the documented #176 root). Re-ground each side's q_lcb under the FINAL live point
        # it will be RECORDED against, so q_lcb_side <= q_point_side holds at the proof
        # boundary BY CONSTRUCTION — not by a downstream per-proof clamp (which is removed).
        # Only ever LOWERS the q_lcb; never raises it.
        yes_lcb_entry = lcb_by_direction.get((condition_id, "buy_yes"))
        no_lcb_entry = lcb_by_direction.get((condition_id, "buy_no"))
        if yes_lcb_entry is not None and float(yes_lcb_entry.q_lcb) > q_value:
            _set_qlcb_provenance(
                lcb_by_direction, (condition_id, "buy_yes"), q_value, source="FORECAST_BOOTSTRAP"
            )
        no_point = max(0.0, min(1.0, 1.0 - q_value))
        if no_lcb_entry is not None and float(no_lcb_entry.q_lcb) > no_point:
            _set_qlcb_provenance(
                lcb_by_direction, (condition_id, "buy_no"), no_point, source="FORECAST_BOOTSTRAP"
            )
    probability_evidence = {
        "p_cal_vector_hash": _probability_vector_hash(float(value) for value in analysis.p_cal),
        "p_live_vector_hash": _probability_vector_hash(
            q_by_condition[str(candidate.condition_id or "")]
            for candidate in family.candidates
        ),
    }
    # P1 (continuous re-decision): cache this family's belief (q-posterior per bin) so the periodic
    # re-decision scan can cheap-screen it against fresh prices WITHOUT re-running this kernel between
    # forecast cycles. Best-effort + double-guarded — a cache hiccup must never break the decision.
    # DISABLED 2026-05-31: persist_belief_live opened a SECOND world connection and
    # INSERT+committed probability_trace_fact WHILE this kernel runs inside the reactor's
    # OWN world write-transaction (process_pending's per-event SAVEPOINT) → SQLite
    # self-deadlock that HUNG every event in process_pending (faulthandler-pinned:
    # continuous_redecision.cache_belief:124). The surrounding try/except could not catch
    # it because it HANGS, not raises. The belief cache is currently write-only — no live
    # reader (enqueue_live_redecisions/screen_exit are unwired dead code per the 2026-05-31
    # audit) — so skipping the write is safe and is the unlock for the first receipt.
    # Re-enable under plan A2 by writing the belief through the reactor's EXISTING
    # connection (same transaction), never a fresh get_world_connection().

    # EMOS shadow ledger (PIECE 2, 2026-06-02): parallel EMOS-calibrated probabilities.
    # Flag-gated (edli_v1.edli_emos_shadow_ledger_enabled, default OFF).
    # FAIL-OPEN/SILENT: any error must not affect the live q_by_condition decision.
    try:
        if bool(settings["edli_v1"].get("edli_emos_shadow_ledger_enabled", False)):
            _write_emos_shadow_ledger(
                event=event,
                family=family,
                snapshot=snapshot,
                analysis=analysis,
                q_by_condition=q_by_condition,
                decision_time=decision_time,
                lcb_by_direction=lcb_by_direction,
                native_costs=native_costs,
            )
    except Exception as _emos_exc:
        try:
            logging.getLogger("zeus.emos_ledger").warning(
                "EMOS shadow ledger write failed (non-fatal): %s", _emos_exc
            )
        except Exception:
            pass

    # MAINSTREAM AGREEMENT ANNOTATION (#135 / operator directive 2026-06-04 #2):
    # ALWAYS compute + annotate the per-candidate mainstream/bias agreement value, DECOUPLED
    # from the reference flag (mainstream_agreement_reference_enabled). The operator wants the
    # number SHOWN ("数值显示") on every receipt without flipping the foreign config — so the
    # evaluation is UNCONDITIONAL here. It is purely OBSERVATIONAL: verdicts are stored in the
    # payload for receipt annotation only; they do NOT filter / exclude candidates in
    # _selected_candidate_proof, and (post-2026-06-04) there is NO submit-time enforce branch.
    # WARM-CACHE-ONLY: the eval helper below reads read_mainstream_point_cached
    # (the STEP-7 off-mutex path, never the synchronous fetch under the world mutex). Cache cold
    # -> verdict carries mainstream_available=False (pass annotated as unknown); the candidate
    # still forms (mainstream is display-only and can never block). FAIL-OPEN/SILENT: any
    # evaluation error must not affect the live q_by_condition decision.
    # Key: (condition_id, direction) → verdict dict.
    try:
        _evaluate_and_store_mainstream_agreement(
            event=event,
            family=family,
            analysis=analysis,
            payload=payload,
        )
    except Exception as _gate_exc:
        try:
            logging.getLogger("zeus.mainstream_gate").warning(
                "mainstream-agreement annotation failed (non-fatal): %s", _gate_exc
            )
        except Exception:
            pass

    return q_by_condition, lcb_by_direction, p_values, prefilter, probability_evidence


def _forecast_snapshot_row_for_event(
    conn: sqlite3.Connection,
    *,
    event: OpportunityEvent,
    family,
    allow_latest: bool,
    decision_time: datetime,
) -> dict[str, Any] | None:
    """Fetch the causal (or, for Day0, latest-available) ensemble_snapshots row for a family.

    ``allow_latest`` selects the latest available snapshot (Day0 base) rather than the exact
    causal snapshot bound by the event. Returns the row as a dict, or None if the authority
    table/columns are absent. Raises (fail-closed) if the forecast reader block-reason fires.
    """
    table_ref = _authority_table_ref(conn, "ensemble_snapshots")
    if table_ref is None:
        raise ValueError("ensemble_snapshots authority table missing for event-bound inference")
    columns = _table_ref_columns(conn, table_ref)
    required = {"city", "target_date", "temperature_metric", "snapshot_id"}
    if not required.issubset(columns):
        return None
    predicates = ["city = ?", "target_date = ?", "temperature_metric = ?"]
    params: list[object] = [family.city, family.target_date, family.metric]
    if not allow_latest:
        predicates.append("CAST(snapshot_id AS TEXT) = ?")
        params.append(str(event.causal_snapshot_id or ""))
    if "available_at" in columns:
        predicates.append("available_at <= ?")
        params.append(decision_time.astimezone(UTC).isoformat())
    if "authority" in columns:
        predicates.append("COALESCE(authority, 'VERIFIED') = 'VERIFIED'")
    if "causality_status" in columns:
        predicates.append("COALESCE(causality_status, 'OK') = 'OK'")
    if "boundary_ambiguous" in columns:
        predicates.append("COALESCE(boundary_ambiguous, 0) = 0")
    order_field = "available_at" if "available_at" in columns else "snapshot_id"
    cur = conn.execute(
        f"""
        SELECT *
        FROM {table_ref}
        WHERE {' AND '.join(predicates)}
        ORDER BY {order_field} DESC
        """,
        tuple(params),
    )
    row = cur.fetchone()
    if row is None:
        return None
    names = [description[0] for description in cur.description]
    snapshot = {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))
    reason, elected_snapshot_id = _forecast_snapshot_reader_block_reason(
        conn,
        snapshot=snapshot,
        event=event,
        family=family,
        allow_latest=allow_latest,
        decision_time=decision_time,
    )
    if reason is not None:
        raise ValueError(reason)
    # Compute inference on the reader-ELECTED executable snapshot (the single forecast
    # authority), not the causal-pinned seed. The causal snapshot triggers the event but its
    # source_run may still be re-ingesting members (captured_at advances past the decision
    # moment), so the reader's causality gate legitimately drops it and elects the freshest
    # fully-captured FULL_CONTRIBUTOR (often an earlier cycle). Returning that row — instead of
    # asserting reader==causal — dissolves the permanent FORECAST_READER_SNAPSHOT_MISMATCH leak.
    # causal_snapshot_id stays as event provenance.
    if elected_snapshot_id is not None and _nonnull(snapshot.get("snapshot_id")) != _nonnull(elected_snapshot_id):
        cur = conn.execute(
            f"SELECT * FROM {table_ref} WHERE CAST(snapshot_id AS TEXT) = ?",
            (str(elected_snapshot_id),),
        )
        elected_row = cur.fetchone()
        if elected_row is not None:
            names = [description[0] for description in cur.description]
            return (
                {name: elected_row[name] for name in names}
                if isinstance(elected_row, sqlite3.Row)
                else dict(zip(names, elected_row))
            )
    return snapshot


def _assert_settlement_unit_identity(*, snapshot: dict[str, Any], payload: dict[str, object], city, bins) -> str:
    """Fail-closed 3-way unit-identity gate at the q seam (#101 / U1).

    The snapshot's unit, the city's settlement unit, and EVERY bin's unit MUST
    agree — otherwise q is computed in one unit and the market resolves in
    another (wrong-bin / wrong-SIDE on a KNOWN market, Paris-class). Returns the
    single agreed unit. Raises ``FORECAST_SETTLEMENT_UNIT_DIVERGENCE`` on any
    mismatch, empty bins, or mixed bin units (all fail-closed).
    """
    snapshot_unit = _snapshot_unit(snapshot, payload)
    city_unit = getattr(city, "settlement_unit", None)
    bin_units = {b.unit for b in bins}
    if len(bin_units) != 1:
        raise ValueError(
            "FORECAST_SETTLEMENT_UNIT_DIVERGENCE: family bins carry "
            f"{'no' if not bin_units else 'mixed'} units {sorted(bin_units)} "
            f"(city={getattr(city, 'name', '?')})"
        )
    (bin_unit,) = tuple(bin_units)
    if not (snapshot_unit == city_unit == bin_unit):
        raise ValueError(
            "FORECAST_SETTLEMENT_UNIT_DIVERGENCE: "
            f"snapshot_unit={snapshot_unit} city_unit={city_unit} bin_unit={bin_unit} "
            f"(city={getattr(city, 'name', '?')})"
        )
    return snapshot_unit


def _make_emos_bootstrap_sampler(mu_native: float, sigma_native: float):
    """Bootstrap sampler that draws the q_lcb from the EMOS predictive N(mu, sigma).

    The ONE-calibrator lcb (#110): replaces member-resampling so the q_lcb reflects ONLY the
    EMOS predictive sigma (no ensemble-spread double-count); the point p_cal is the analytic
    EMOS q_vec. One (mu, sigma) feeds both. Uses the MarketAnalysis rng/settle/bin so the
    sampled distribution matches the live settlement rounding convention exactly.
    """
    def _sampler(analysis, n_members):
        draws = analysis._rng.normal(float(mu_native), float(sigma_native), int(n_members))
        measured = analysis._settle(draws)
        vec = np.array(
            [analysis._bin_probability(measured, bb) for bb in analysis.bins], dtype=float
        )
        # Guard: NaN/inf or zero-sum => fall back to p_cal so the caller always
        # receives a valid finite normalized distribution (avoids fail-close in
        # _finite_probability_distribution when the flag is ON).
        if not np.all(np.isfinite(vec)):
            return np.asarray(analysis.p_cal, dtype=float)
        s = float(vec.sum())
        if s <= 0.0:
            return np.asarray(analysis.p_cal, dtype=float)
        return vec / s
    return _sampler


def _make_day0_bootstrap_sampler(
    *,
    members_native,
    payload: dict[str, object],
    family,
    unit: str,
    decision_time: "datetime | None",
):
    """Obs-floor-conditional bootstrap sampler for the DAY0 q_lcb (review item D).

    Per draw: resample member daily extremes (with replacement) + instrument
    noise widened by obs staleness (margin/2 treated as ~1 extra sigma of
    unobserved boundary motion), clamp to the absorbing physical law
    (HIGH: max(draw, running max); LOW: min(draw, running min)), settle-round
    via the analysis's own convention, bin, apply the absorbing mask,
    renormalize. Returns None (caller falls back to the legacy static sampler,
    loudly) when inputs cannot support a bootstrap.
    """
    try:
        members = np.asarray(members_native, dtype=float).ravel()
        members = members[np.isfinite(members)]
        if members.size == 0:
            raise ValueError("no finite members for day0 bootstrap")
        rounded = _optional_float(payload.get("rounded_value"))
        metric = str(payload.get("metric") or payload.get("temperature_metric") or "")
        if metric not in {"high", "low"}:
            raise ValueError(f"unsupported day0 metric for bootstrap: {metric!r}")
        mask = _day0_absorbing_mask(payload=payload, family=family)
        from src.signal.forecast_uncertainty import sigma_instrument
        from src.signal.day0_obs_latency import (
            stale_extreme_uncertainty_margin,
            staleness_budget_minutes,
        )

        base_sigma = float(sigma_instrument(unit).value)
        obs_age_min = _day0_observation_age_minutes(payload, decision_time)
        budget_min = staleness_budget_minutes(str(getattr(family, "city", "") or ""))
        margin = stale_extreme_uncertainty_margin(
            unit=unit, obs_age_minutes=obs_age_min, budget_minutes=budget_min
        )
        sigma = float(np.sqrt(base_sigma ** 2 + (margin / 2.0) ** 2))
        if not (sigma > 0.0 and np.isfinite(sigma)):
            raise ValueError(f"day0 bootstrap sigma invalid: {sigma}")
    except Exception as exc:  # noqa: BLE001 — degrade LOUDLY to the static sampler
        import logging as _logging

        _logging.getLogger("zeus.day0_bootstrap_lcb").warning(
            "DAY0_BOOTSTRAP_LCB_UNAVAILABLE city=%s exc=%s: %s — static q_lcb fallback (q_lcb==q)",
            getattr(family, "city", "?"), type(exc).__name__, exc,
        )
        return None

    members_arr = members
    mask_arr = np.asarray(mask, dtype=float)

    def _sampler(analysis, n_members):
        n = max(1, int(n_members))
        idx = analysis._rng.integers(0, members_arr.size, n)
        draws = members_arr[idx] + analysis._rng.normal(0.0, sigma, n)
        if rounded is not None:
            if metric == "high":
                draws = np.maximum(draws, float(rounded))
            else:
                draws = np.minimum(draws, float(rounded))
        measured = analysis._settle(draws)
        vec = np.array(
            [analysis._bin_probability(measured, bb) for bb in analysis.bins], dtype=float
        )
        if vec.shape == mask_arr.shape:
            vec = vec * mask_arr
        if not np.all(np.isfinite(vec)):
            return np.asarray(analysis.p_cal, dtype=float)
        s = float(vec.sum())
        if s <= 0.0:
            return np.asarray(analysis.p_cal, dtype=float)
        return vec / s

    return _sampler


def _market_analysis_from_event_snapshot(
    *,
    calibration_conn: sqlite3.Connection,
    snapshot: dict[str, Any],
    family,
    native_costs: dict[tuple[str, str], tuple[dict[str, Any] | None, ExecutionPrice | None, float, float | None, str | None]],
    payload: dict[str, object],
    decision_time: datetime | None,
) -> MarketAnalysis:
    from src.strategy.market_analysis import MarketAnalysis
    from src.config import settings

    bins = list(family.bins)
    raw_members = _snapshot_members(snapshot)
    # §4.1 (CI_HONESTY_AND_SCORE_GATE_RULING_2026-06-01): hoist bias correction so
    # both the p_raw path AND the bootstrap (member_maxes) consume the SAME corrected
    # surface.  Pre-fix: correction was applied inside _snapshot_p_raw (local rebind)
    # and never escaped — MarketAnalysis received uncorrected cold array, placing
    # q_lcb_5pct ~|eff_bias_c|° below the warm point posterior.
    city = runtime_cities_by_name().get(family.city)
    if city is None:
        raise ValueError(f"city config missing for event-bound forecast inference: {family.city}")
    # Settlement-unit identity gate (#101 / SETTLEMENT_CORRECTNESS_AUDIT U1): q is
    # meaningless unless the snapshot members, the city's settlement unit, and the
    # bin units all agree. Converts the previously-DISCARDED _snapshot_unit() call
    # into a load-bearing fail-closed assertion at the q seam, so a future ingest
    # unit-swap (Kelvin leak / source swap / new city) cannot silently invert q
    # into the wrong bins (wrong-SIDE on a KNOWN market — Paris-class).
    unit = _assert_settlement_unit_identity(snapshot=snapshot, payload=payload, city=city, bins=bins)
    # === ONE-CALIBRATOR SEAM (#110 / ELEVATION S2) ===========================================
    # When EMOS serves this (city, season) cell and the flag is ON, the traded distribution IS
    # the EMOS predictive N(mu, sigma): point p_cal = analytic q_vec; the q_lcb bootstrap draws
    # from the SAME N(mu, sigma) (one sigma, no ensemble-spread double-count). This collapses
    # the bias/grid/identity-Platt mean-correction maze into a single calibrator. served=raw /
    # missing / flag-OFF / day0 -> the existing path below runs unchanged (byte-identical).
    _emos_q = None
    _emos_sampler = None
    # ONE-CALIBRATOR REGIME (#110 universal, operator 2026-06-05): when ON (non-day0), the cell
    # is served by EXACTLY one of {EMOS predictive, do-no-harm-VALIDATED honest raw N(xbar,S^2)};
    # the bias/grid/Platt maze is NEVER reached. served=raw / EMOS-miss / serve-fail therefore
    # routes to honest raw (members UN-shifted, identity p_cal), not _maybe_apply_edli_bias_correction.
    _emos_regime = (
        family.event_type != "DAY0_EXTREME_UPDATED"
        and bool(settings["edli_v1"].get("edli_emos_sole_calibrator_enabled", False))
    )
    if _emos_regime:
        # M1 (critic 2026-06-04): the EMOS branch must degrade to the honest path on ANY failure,
        # mirroring the flag-OFF try/except around _snapshot_lead_days — otherwise a lead-missing
        # snapshot fail-closes the whole family (coverage regression) when the flag is ON.
        # NH month-season, MATCHING emos_calibration.json keying (fit_emos_calibration.season()).
        # MUST NOT be hemisphere-aware: the fit groups e.g. Sao Paulo|June under "JJA" (NH label);
        # a lat-flipped season would serve the OPPOSITE-season cell (critic C1). Month-only keys
        # the cell fit on the SAME calendar months as the target. Computed BEFORE the try so it is
        # always available for the failure log (city|season|metric cell identity).
        _emos_m = (family.target_date.month if hasattr(family.target_date, "month")
                   else int(str(family.target_date)[5:7]))
        _emos_season = ("DJF" if _emos_m in (12, 1, 2) else "MAM" if _emos_m in (3, 4, 5)
                        else "JJA" if _emos_m in (6, 7, 8) else "SON")
        # EMPIRICAL settlement σ-floor (loop-breaker, investigation 2026-06-05; iron rule 5:
        # overconfidence = ruin). The q-builders are pure (no settings import); the SEAM reads the
        # flag and passes an explicit bool. Default OFF ⇒ byte-identical to today. When ON, the
        # builder floors σ at k·σ_settled (DETRENDED settlement std) — max() only WIDENS σ → lower
        # q_lcb → fewer overconfident bets; can NEVER tighten or create a wrong-side trade.
        _apply_settlement_floor = bool(
            settings["edli_v1"].get("edli_settlement_sigma_floor_enabled", False)
        )
        _require_settlement_floor = bool(
            settings["edli_v1"].get("edli_settlement_sigma_floor_required", True)
        )
        try:
            from src.calibration.emos_q_builder import build_emos_q as _build_emos_q
            _emos_q = _build_emos_q(
                city=city.name, season=_emos_season, metric=family.metric,
                lead_days=_snapshot_lead_days(snapshot=snapshot, family=family, payload=payload),
                members_native=raw_members, unit=unit, bins=bins,
                apply_settlement_floor=_apply_settlement_floor,
                require_settlement_floor=_require_settlement_floor,
            )
        except Exception as _emos_exc:
            # DE-SILENCED ANTIBODY (#149 / live-diagnosis 2026-06-04): a bare
            # `except Exception: _emos_q = None` swallowed EVERY EMOS failure with NO log, so a
            # flag-ON-but-always-failing calibrator was INDISTINGUISHABLE from flag-OFF (q_source
            # absent; legacy ran invisibly — the exact fail-open-inert class). EMOS stays
            # best-effort (degrades to the honest legacy path, never fail-closes a family), BUT
            # the degrade is now LOUD: a distinct EMOS_SERVE_FAILED line carrying the exception
            # type+message and the served cell identity (city|season|metric|unit) so monitoring
            # catches a served cell that has silently stopped serving. A silent fall-back on a
            # served cell is forbidden; this makes that category UNCONSTRUCTABLE in CI
            # (tests/engine/test_emos_seam_serve_loud.py). `_emos_q` left None -> legacy path.
            _emos_q = None
            payload["_edli_emos_serve_failed"] = True
            import logging  # module uses lazy per-fn logging imports
            logging.getLogger("zeus.emos_serve").warning(
                "EMOS_SERVE_FAILED cell=%s|%s|%s unit=%s exc=%s: %s",
                getattr(city, "name", family.city), _emos_season, family.metric, unit,
                type(_emos_exc).__name__, _emos_exc,
            )
            from src.calibration.emos import SettlementSigmaFloorError
            if isinstance(_emos_exc, SettlementSigmaFloorError):
                raise
    if _emos_q is not None:
        _q_vec, _emos_mu_native, _emos_sigma_native = _emos_q
        p_raw = np.asarray(_q_vec, dtype=float)
        p_cal = np.asarray(_q_vec, dtype=float)  # EMOS IS the calibrated point distribution
        members = raw_members
        _bias_corrected = False
        representativeness_sigma = 0.0  # the EMOS sampler carries the predictive sigma
        payload["_edli_q_source"] = "emos"
        _emos_sampler = _make_emos_bootstrap_sampler(_emos_mu_native, _emos_sigma_native)
    elif _emos_regime:
        # HONEST RAW (universal, #110 / operator 2026-06-05) with a CALIBRATED σ-FLOOR (residual
        # under-dispersion fix, counterfactual 2026-06-05). EMOS did not serve this non-day0 cell —
        # a do-no-harm served=raw cell, an EMOS-table miss, or a loud serve-fail. The contract is
        # "EMOS, or the do-no-harm raw MEAN with a CALIBRATED dispersion, NEVER the bias maze". The
        # do-no-harm gate kept the raw MEAN (its EMOS mean did not generalize) — but the raw ensemble
        # σ (~0.6°C for Singapore-class cells) is too tight: that under-dispersion pinned q_no≈1.0 and
        # drove the expensive-NO-on-the-winner loss. So build_honest_raw_q keeps x̄ but FLOORS σ at the
        # cell's EMOS lead-aware σ (max(raw_σ, emos_σ)); the point q AND the q_lcb bootstrap both draw
        # from N(x̄, floored σ). Conservative: only widens → lower q_lcb. When no EMOS σ-model exists
        # for the cell (truly absent), degrade to the pure raw analytic (members_already_corrected=True
        # tells _snapshot_p_raw NOT to re-apply bias — never the maze).
        members = raw_members
        _bias_corrected = False
        representativeness_sigma = 0.0
        payload["_edli_q_source"] = "raw_honest"
        _hr = None
        try:
            from src.calibration.emos_q_builder import build_honest_raw_q as _build_hr
            # _apply_settlement_floor is defined in the `if _emos_regime:` block above (this elif is
            # only reached when _emos_regime is True, so the block ran). Pass the same flag so the
            # honest-raw path composes the EMPIRICAL settlement floor on top of the emos_σ_model floor.
            _hr = _build_hr(
                city=city.name, season=_emos_season, metric=family.metric,
                lead_days=_snapshot_lead_days(snapshot=snapshot, family=family, payload=payload),
                members_native=raw_members, unit=unit, bins=bins,
                apply_settlement_floor=_apply_settlement_floor,
                require_settlement_floor=_require_settlement_floor,
            )
        except Exception as _hr_exc:  # noqa: BLE001 — best-effort floor; degrade to raw analytic, LOUD
            _hr = None
            import logging
            logging.getLogger("zeus.emos_serve").warning(
                "HONEST_RAW_FLOOR_FAILED cell=%s|%s|%s unit=%s exc=%s: %s",
                getattr(city, "name", family.city), _emos_season, family.metric, unit,
                type(_hr_exc).__name__, _hr_exc,
            )
            from src.calibration.emos import SettlementSigmaFloorError
            if isinstance(_hr_exc, SettlementSigmaFloorError):
                raise
        if _hr is not None:
            _hrq, _hr_mu, _hr_sigma = _hr
            p_raw = np.asarray(_hrq, dtype=float)
            p_cal = np.asarray(_hrq, dtype=float)
            _emos_sampler = _make_emos_bootstrap_sampler(_hr_mu, _hr_sigma)
        else:
            p_raw = _snapshot_p_raw(
                snapshot, family=family, bins=bins, members=members, payload=payload,
                members_already_corrected=True,
            )
            p_cal = np.asarray(p_raw, dtype=float)
    else:
        # === DAY0 REMAINING-DAY MODE (review 2026-06-10 item B, flag-gated OFF) ===
        # When ON and fresh high-res hourly vectors are persisted for this family
        # (day0_hourly_vectors lane), the member array becomes the pooled per-model
        # REMAINING-day extremes (hours >= now, city-local), clamped to the absorbing
        # physical law (HIGH: max(model_remaining, running max)). This prices
        # P(remaining excursion | now) instead of the full-day distribution —
        # below-floor remaining members land IN the floor bin (the post-peak
        # repricing) rather than being renormalized away. Platt is SKIPPED in this
        # mode (identity p_cal): the fitted Platt's domain is full-day member
        # distributions, not remaining-day pools. The absorbing mask below still
        # applies, and the day0 bootstrap sampler draws from the SAME members.
        _day0_rd_members = None
        if family.event_type == "DAY0_EXTREME_UPDATED" and _day0_remaining_day_q_enabled():
            _day0_rd_members = _day0_remaining_day_members(
                payload=payload, family=family, unit=unit, decision_time=decision_time
            )
        if _day0_rd_members is not None:
            members = _day0_rd_members
            _bias_corrected = False
            payload["_edli_q_source"] = "day0_remaining_day"
            payload["_edli_day0_q_mode"] = "remaining_day"
        else:
            members, _bias_corrected = _maybe_apply_edli_bias_correction(
                raw_members, snapshot=snapshot, family=family, city=city, payload=payload
            )
        if _bias_corrected:
            payload["_edli_bias_corrected"] = True
        # #120: maze-fallback calibrator provenance (EMOS did not serve this cell).
        # "bias_platt" = bias-corrected members fed to Platt; "platt" = plain.
        if _day0_rd_members is None:
            payload["_edli_q_source"] = "bias_platt" if _bias_corrected else "platt"
        # Grid→point representativeness correction (lead-invariant, OOS-validated).
        # Flag-gated (edli_v1.edli_grid_representativeness_correction_enabled, default OFF).
        # Applied on the (potentially bias-corrected) member array so both corrections compose.
        members, _grid_corrected = _maybe_apply_grid_representativeness_correction(
            members, snapshot=snapshot, family=family, city=city, payload=payload
        )
        # payload['_edli_grid_corrected'] set inside the hook when applied.
        # DOUBLE-COUNT STRUCTURAL ANTIBODY (2026-06-03): bias and grid both subtract a per-city
        # MEAN temperature residual. If BOTH apply to the same members the warm-shift is applied
        # ~twice (F = E[r_bias] + E[r_grid], over-correction). Today bias=ON / grid=OFF so this is
        # inert, but the guard makes the wrong composition UNCONSTRUCTABLE — fail CLOSED rather
        # than silently double-subtract. Make the error category impossible, not the instance.
        _assert_single_temperature_mean_correction(
            bias_applied=_bias_corrected, grid_applied=_grid_corrected,
            city=getattr(city, "name", family.city), target_date=str(family.target_date),
        )
        # REPRESENTATIVENESS VARIANCE (iron rule 6, 2026-06-03): when (and only when) the EDLI
        # bias correction was applied, the member MEAN was shifted but the spread was NOT widened.
        # Fold the per-city forecast-vs-settlement residual σ (native unit) into the MC bootstrap
        # noise so q_lcb widens honestly. σ_repr=0.0 when no correction => MarketAnalysis behaviour
        # is byte-identical. Does NOT touch the POINT q (p_raw / p_posterior below) — only the CI.
        representativeness_sigma = (
            _edli_representativeness_sigma_native(snapshot=snapshot, family=family, city=city)
            if _bias_corrected
            else 0.0
        )
        p_raw = _snapshot_p_raw(
            snapshot, family=family, bins=bins, members=members, payload=payload,
            members_already_corrected=True,
        )
        if _day0_rd_members is not None:
            # remaining-day mode: identity calibration (see block comment above)
            p_cal = np.asarray(p_raw, dtype=float)
        else:
            p_cal = _snapshot_p_cal(
                calibration_conn,
                snapshot=snapshot,
                family=family,
                bins=bins,
                p_raw=p_raw,
                payload=payload,
                decision_time=decision_time,
            )
        if family.event_type == "DAY0_EXTREME_UPDATED":
            p_raw = _apply_day0_mask_to_probability_vector(payload=payload, family=family, vector=p_raw)
            p_cal = _apply_day0_mask_to_probability_vector(payload=payload, family=family, vector=p_cal)
    p_market_yes: list[float] = []
    p_market_no: list[float] = []
    buy_no_available: list[bool] = []
    executable_mask: list[bool] = []
    for candidate in family.candidates:
        condition_id = str(candidate.condition_id or "")
        yes_cost = native_costs.get((condition_id, "buy_yes"))
        no_cost = native_costs.get((condition_id, "buy_no"))
        yes_price = yes_cost[1].value if yes_cost is not None and yes_cost[1] is not None else None
        no_price = no_cost[1].value if no_cost is not None and no_cost[1] is not None else None
        p_market_yes.append(float(yes_price) if yes_price is not None else 0.999999)
        p_market_no.append(float(no_price) if no_price is not None else 0.999999)
        buy_no_available.append(no_price is not None)
        executable_mask.append(yes_price is not None or no_price is not None)
    sampler = _emos_sampler  # one-calibrator (#110): EMOS N(mu,sigma) lcb bootstrap, else None
    is_day0 = family.event_type == "DAY0_EXTREME_UPDATED"
    if is_day0:
        # DAY0 q_lcb FIX (first-principles review 2026-06-10 item D): the prior
        # static sampler returned p_cal verbatim every draw, so the bootstrap
        # percentile collapsed to the point estimate (q_lcb == q — ZERO
        # uncertainty quantification on the day0 lane). Replace with a real
        # obs-floor-conditional member bootstrap: each draw resamples the
        # member extremes, adds instrument noise WIDENED by the measured
        # per-city obs staleness (config/wu_obs_latency.json), clamps to the
        # absorbing physical law (final >= running max / final <= running min),
        # settles, bins, and applies the absorbing mask. q_lcb < q again, and
        # it widens honestly when the running extreme is stale. Any
        # construction failure degrades LOUDLY to the legacy static sampler
        # (no regression vs the pre-fix behavior).
        _day0_sampler = _make_day0_bootstrap_sampler(
            members_native=members,
            payload=payload,
            family=family,
            unit=unit,
            decision_time=decision_time,
        )
        if _day0_sampler is not None:
            sampler = _day0_sampler
        else:
            static_p_cal = np.asarray(p_cal, dtype=float)

            def _static_sampler(_analysis, _n_members):
                return static_p_cal

            sampler = _static_sampler
    # K1 — ForecastSharpnessEvidence (Phase-2, REQUIRED ctor param). Day0/imminent
    # paths are exempt (the realized observation replaces the forecast, so forecast
    # sharpness is moot). Otherwise load the settlement MAE for (city, unit, lead)
    # from forecast_skill. The BEHAVIOR (edge suppression) is flag-gated OFF
    # (edli_v1.forecast_sharpness_gate_enabled) so this evidence is inert on live emit
    # today; only the TYPE is load-bearing now. Any load failure -> fail-closed
    # `missing` evidence (also inert while the flag is OFF).
    forecast_sharpness = _edli_forecast_sharpness_evidence(
        snapshot=snapshot, family=family, payload=payload, unit=unit, bins=bins,
        day0_exempt=is_day0,
    )
    return MarketAnalysis(
        p_raw=np.asarray(p_raw, dtype=float),
        p_cal=np.asarray(p_cal, dtype=float),
        p_market=np.asarray(p_market_yes, dtype=float),
        p_market_no=np.asarray(p_market_no, dtype=float),
        buy_no_quote_available=np.asarray(buy_no_available, dtype=bool),
        executable_mask=np.asarray(executable_mask, dtype=bool),
        alpha=float(settings["edge"]["base_alpha"]["level1"]),
        bins=bins,
        member_maxes=members,  # §4.1: corrected array (hoisted above)
        unit=unit,  # #101: the unit-identity-asserted agreed unit (snapshot==city==bins)
        precision=float(snapshot.get("members_precision") or 1.0),
        round_fn=None,
        city_name=family.city,
        season="",
        forecast_source=str(snapshot.get("source_id") or payload.get("source_id") or ""),
        bias_corrected=_bias_corrected,  # §4.1: propagate correction flag
        market_complete=True,
        posterior_mode=MODEL_ONLY_POSTERIOR_MODE,
        bootstrap_probability_sampler=sampler,
        bootstrap_signal_type="edli_event_bound_day0" if is_day0 else "edli_event_bound_forecast",
        representativeness_sigma=representativeness_sigma,  # iron rule 6: honest q_lcb widening on corrected domain
        forecast_sharpness=forecast_sharpness,  # K1: required sharpness contract
    )


def _bin_width_native(bins) -> float:
    """Return the integer settlement bin width in the native unit (1 °C / 2 °F).

    Reads the first finite-width (non-shoulder) bin so the K1 sharpness threshold
    is keyed to the actual market grid, not an assumed value.
    """
    for b in bins:
        w = getattr(b, "width", None)
        if w:
            return float(w)
    # No finite-width bin in the family (all shoulders) — fall back to unit default.
    unit = getattr(bins[0], "unit", "F") if bins else "F"
    return 2.0 if unit == "F" else 1.0


def _edli_forecast_sharpness_evidence(
    *, snapshot, family, payload, unit, bins, day0_exempt: bool
):
    """Build the K1 ForecastSharpnessEvidence for the event-bound q path.

    Day0 -> exempt. Otherwise aggregate settlement MAE from forecast_skill keyed by
    (city, unit, int(min(lead_days, 7))). Fail-closed `missing` on any error — inert
    while the gate flag is OFF, conservative when ON.
    """
    from src.contracts.forecast_sharpness import ForecastSharpnessEvidence

    if day0_exempt:
        return ForecastSharpnessEvidence.exempt(unit=unit)
    bin_width = _bin_width_native(bins)
    try:
        lead_days = _snapshot_lead_days(snapshot=snapshot, family=family, payload=payload)
    except Exception:
        return ForecastSharpnessEvidence.missing(unit=unit, bin_width=bin_width, lead_days=7)
    # WAL checkpoint-starvation fix (2026-06-04, part 1a): this is the EDLI
    # reactor HOT PATH — called per-event (line ~3889). The prior code opened a
    # zeus-world.db read connection and NEVER closed it: each call leaked a
    # connection holding a WAL read snapshot (read-mark) that pins the WAL floor
    # until non-deterministic GC. Under load these accumulate and starve
    # wal_checkpoint(TRUNCATE) → -wal grows to GBs → lock-starvation. Close the
    # connection in a finally so its snapshot is released the moment the read
    # (which load_for fully materializes before returning) completes.
    conn = None
    try:
        from src.state.db import get_world_connection_read_only

        conn = get_world_connection_read_only()
        return ForecastSharpnessEvidence.load_for(
            conn, city=family.city, unit=unit, lead_days=lead_days, bin_width=bin_width
        )
    except Exception:
        return ForecastSharpnessEvidence.missing(
            unit=unit, bin_width=bin_width, lead_days=int(min(max(lead_days, 0.0), 7.0))
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — close is best-effort; never mask the result
                pass


def _evaluate_and_store_mainstream_agreement(
    *,
    event: OpportunityEvent,
    family,
    analysis,  # MarketAnalysis — carries member_maxes (corrected) + bins + unit + precision
    payload: dict,
) -> None:
    """Evaluate the 4-check mainstream-agreement gate per candidate and store verdicts.

    Verdicts are stored as payload["_mainstream_agreement_verdicts"] dict keyed by
    (condition_id, direction) → MainstreamAgreementVerdict.to_dict(). The payload is
    event-scoped so verdicts survive only for this event's receipt build.

    Fail-closed is enforced by the gate module itself (mainstream_point=None → FAIL_CLOSED).
    This function never raises — if something goes wrong the payload key is absent and
    the receipt simply omits mainstream_agreement_* fields.
    """
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement
    from src.data.mainstream_forecast_source import read_mainstream_point_cached

    members = list(float(m) for m in analysis.member_maxes) if analysis.member_maxes is not None else None
    our_point = float(analysis.member_maxes.mean()) if members else None
    # Provenance: read the raw_member_maxes accessor if available. In the EDLI
    # event-bound path, bias/grid corrections are applied upstream (before
    # MarketAnalysis is constructed), so raw_member_maxes already carries those
    # corrections — it is NOT a genuinely pre-correction array. The resulting
    # raw_our_point / agrees_on_raw / agreement_correction_dependent fields are
    # therefore informational annotations only; no demotion or gate action is taken.
    # Best-effort: older MarketAnalysis without raw accessor leaves this None
    # (provenance fields will be absent in those cases — backward-safe).
    _raw = getattr(analysis, "raw_member_maxes", None)
    raw_our_point = float(_raw.mean()) if (_raw is not None and len(_raw)) else None
    bins = list(analysis.bins)
    unit = str(analysis.unit or "C")
    precision = float(getattr(analysis, "precision", 1.0) or 1.0)

    # STEP 7 (E2): cache-ONLY read — never fetch on the mutex-held decision path.
    # A miss yields mainstream_snap=None → mainstream_point=None → the gate's
    # existing FAIL_CLOSED path (the warm job _edli_mainstream_warm_cycle keeps
    # this cache populated off the decision path).
    mainstream_snap = read_mainstream_point_cached(
        family.city,
        family.target_date,
        metric=family.metric,  # METRIC-MATCHED (#metric-crossing fix): LOW->daily min, HIGH->daily max.
    )
    mainstream_pt = float(mainstream_snap["point"]) if mainstream_snap is not None else None

    verdicts: dict[tuple[str, str], dict] = {}
    for candidate in family.candidates:
        condition_id = str(candidate.condition_id or "")
        for direction in ("buy_yes", "buy_no"):
            try:
                verdict = evaluate_mainstream_agreement(
                    city=family.city,
                    target_date=family.target_date,
                    unit=unit,
                    our_point=our_point if our_point is not None else 0.0,
                    bins=bins,
                    traded_bin=candidate.bin,
                    direction=direction,
                    members=members,
                    mainstream_point=mainstream_pt,
                    raw_our_point=raw_our_point,
                    precision=precision,
                )
                _vd = verdict.to_dict()
                # DISPLAY-ONLY (operator directive 2026-06-04 #2): a COLD cache
                # (mainstream_pt is None) is UNKNOWN, not a failure. Annotate
                # mainstream_agreement_pass=None (unknown) rather than False so the
                # operator's review set distinguishes "no mainstream point yet" from a
                # genuine bias mismatch. The candidate STILL forms (mainstream never
                # blocks); the order-able ∩ bias-pass review query treats None as
                # excluded (unknown != pass), the same as a fail.
                if mainstream_pt is None:
                    _vd["mainstream_agreement_pass"] = None
                verdicts[(condition_id, direction)] = _vd
                verdicts[(condition_id, direction)]["mainstream_authority_tier"] = (
                    mainstream_snap.get("authority_tier") if mainstream_snap else None
                )
                verdicts[(condition_id, direction)]["mainstream_source"] = (
                    mainstream_snap.get("source") if mainstream_snap else None
                )
                verdicts[(condition_id, direction)]["mainstream_fetched_at_utc"] = (
                    mainstream_snap.get("fetched_at_utc") if mainstream_snap else None
                )
            except Exception as _v_exc:
                logging.getLogger("zeus.mainstream_gate").debug(
                    "verdict evaluation failed for %s %s %s: %s",
                    family.city, condition_id, direction, _v_exc,
                )

    if verdicts:
        payload["_mainstream_agreement_verdicts"] = verdicts


def _snapshot_members(snapshot: dict[str, Any]) -> np.ndarray:
    members = _json_list(snapshot.get("members_json"))
    values = np.asarray([float(item) for item in members if item is not None], dtype=float)
    if values.ndim != 1 or values.size == 0 or not np.isfinite(values).all():
        raise ValueError("causal forecast snapshot members_json invalid")
    return values


def _snapshot_members_json_hash(snapshot: dict[str, Any]) -> str:
    return _probability_vector_hash(_snapshot_members(snapshot))


_EDLI_BIAS_FAMILY = "edli_per_city_v1"


def _maybe_bias_decay_kelly_haircut(
    kelly_multiplier: float,
    *,
    family,
    q_source: str | None = None,
) -> tuple[float, bool, float | None, str]:
    """INTERIM (data-insufficient phase) pre-submit Kelly haircut on high-bias cities.

    Operator directive 2026-05-31: if the per-city forecast bias magnitude exceeds the
    unit-aware threshold (edli_v1.bias_decay_threshold_c for C-settled cities,
    bias_decay_threshold_f for F-settled SF/Seattle), multiply the Kelly multiplier by
    bias_decay_kelly_factor (0.5 = halve). Sizes DOWN cities whose forecast we cannot yet
    trust enough to fully correct (corrected-#24 showed a full p_raw correction worsens
    the live gate -> edge-reversal risk). Does NOT shift p_raw.

    Bias source: model_bias_ens.effective_bias_c (edli_per_city_v1, VERIFIED). The stored
    bias is degC; for F-settled cities compare |eff_c * 1.8| to the F threshold.
    FAIL-SAFE: no VERIFIED bias row (data absent = the data-insufficient trigger) -> apply
    the haircut + WARN. FAIL-OPEN on UNEXPECTED ERROR only: any exception -> NO haircut +
    WARN (never crash or zero a live size). Flag-gated: edli_v1.bias_decay_kelly_haircut_enabled.
    """
    try:
        if str(q_source or "").strip().lower() in {"emos", "raw_honest"}:
            return kelly_multiplier, False, None, "one_calibrator_regime"
        ev = settings["edli_v1"]
        if not bool(ev.get("bias_decay_kelly_haircut_enabled", False)):
            return kelly_multiplier, False, None, "disabled"
        import contextlib
        import logging
        from src.calibration.manager import season_from_date
        from src.calibration.ens_bias_repo import read_bias_model
        from src.state.db import get_world_connection

        city = runtime_cities_by_name().get(family.city)
        if city is None:
            return kelly_multiplier, False, None, "no_city"
        # Phase-2 K2+N1+#122 (task #167): corrected XOR haircut. When v2 is ON, consult the
        # single typed BiasTreatment. If this (city,bucket) is on the CORRECT path the bias
        # was already consumed by the p_raw shift — the haircut MUST NOT also fire on the
        # same row (the N1 double penalty). The XOR invariant lives in BiasTreatment.
        # kelly_factor(): a CORRECT treatment returns factor 1.0 (residual-after-correction
        # is 0). Flag OFF -> this block is skipped -> legacy haircut byte-identical.
        if bool(ev.get("bias_treatment_v2_enabled", False)):
            _treatment = _edli_bias_treatment_for_bucket(family=family, city=city)
            if _treatment is not None:
                _unit = getattr(city, "settlement_unit", "C")
                _thr = float(ev.get("bias_decay_threshold_f", 3.0)) if _unit == "F" else float(
                    ev.get("bias_decay_threshold_c", 2.0)
                )
                _factor = float(ev.get("bias_decay_kelly_factor", 0.5))
                _kf = _treatment.kelly_factor(threshold_native=_thr, haircut_factor=_factor)
                if _kf < 1.0:
                    logging.getLogger("zeus.edli_bias").info(
                        "bias-decay haircut (v2 BiasTreatment) APPLIED city=%s residual=%.2f "
                        "thr=%.2f factor=%.2f", family.city, _treatment.residual_native, _thr, _kf,
                    )
                    return kelly_multiplier * _kf, True, _treatment.residual_native, "bias_exceeds_v2"
                # CORRECT path or within-threshold: NO haircut (XOR honoured).
                return kelly_multiplier, False, _treatment.residual_native, "treated_v2_no_haircut"
            # treatment is None: either no VERIFIED row, or correction flag OFF with no row.
            # Fall through to the legacy fail-safe (data-absent -> conservative haircut),
            # preserving the operator's data-insufficient-phase intent for uncovered buckets.
        unit = getattr(city, "settlement_unit", "C")
        metric = family.metric
        ldv = (
            "ecmwf_opendata_mx2t3_local_calendar_day_max"
            if metric == "high"
            else "ecmwf_opendata_mn2t3_local_calendar_day_min"
        )
        season = season_from_date(str(family.target_date), lat=city.lat)
        month = int(str(family.target_date)[5:7])
        eff_c = None
        with contextlib.closing(get_world_connection()) as conn:
            conn.row_factory = sqlite3.Row
            row = read_bias_model(
                conn,
                city=city.name,
                season=season,
                metric=metric,
                live_data_version=ldv,
                month=month,
                target_month=month,
                authority="VERIFIED",
                error_model_family=_EDLI_BIAS_FAMILY,
            )
        if row is not None:
            try:
                eff_c = float(row["effective_bias_c"])
            except Exception:
                eff_c = None
        factor = float(ev.get("bias_decay_kelly_factor", 0.5))
        if eff_c is None:
            logging.getLogger("zeus.edli_bias").warning(
                "bias-decay haircut APPLIED (fail-safe: no VERIFIED bias row) city=%s metric=%s factor=%.2f",
                family.city, metric, factor,
            )
            return kelly_multiplier * factor, True, None, "no_bias_row_conservative"
        if unit == "F":
            bias_native = eff_c * 1.8
            thr = float(ev.get("bias_decay_threshold_f", 3.0))
        else:
            bias_native = eff_c
            thr = float(ev.get("bias_decay_threshold_c", 2.0))
        if abs(bias_native) > thr:
            logging.getLogger("zeus.edli_bias").info(
                "bias-decay haircut APPLIED city=%s unit=%s bias_native=%.2f thr=%.2f factor=%.2f",
                family.city, unit, bias_native, thr, factor,
            )
            return kelly_multiplier * factor, True, bias_native, "bias_exceeds"
        return kelly_multiplier, False, bias_native, "within_threshold"
    except Exception as exc:  # fail-OPEN on unexpected error: never crash/zero a live size
        try:
            import logging
            logging.getLogger("zeus.edli_bias").warning(
                "bias-decay haircut SKIPPED (fail-open on error, no haircut): %s", exc
            )
        except Exception:
            pass
        return kelly_multiplier, False, None, "error_fail_open"


class DoubleTemperatureCorrectionError(RuntimeError):
    """A candidate would be BOTH bias-corrected AND grid-corrected (double mean subtraction).

    Both corrections subtract a per-city MEAN temperature residual from the member array; if
    both apply, the warm-shift is applied roughly twice (over-correction that inverts q). The
    adapter fails CLOSED on this rather than silently double-subtracting.
    """


def _assert_single_temperature_mean_correction(
    *,
    bias_applied: bool,
    grid_applied: bool,
    city: str | None = None,
    target_date: str | None = None,
) -> None:
    """Fail CLOSED if BOTH temperature-domain mean corrections are applied to one candidate.

    Structural antibody (Fitz: make the wrong code unconstructable). The EDLI bias correction
    (_maybe_apply_edli_bias_correction) and the grid-representativeness correction
    (_maybe_apply_grid_representativeness_correction) each subtract a per-city MEAN residual.
    Composing both subtracts E[r_bias] + E[r_grid] — the de-biasing shift is applied ~twice.
    At most ONE may apply. Today bias=ON / grid=OFF so this never fires, but if a future flag
    flip ever turns both ON this raises instead of producing a silently over-corrected q.
    """
    if bias_applied and grid_applied:
        raise DoubleTemperatureCorrectionError(
            "double / mutually-exclusive temperature mean correction: both EDLI bias "
            "correction AND grid-representativeness correction applied to the same member "
            f"array (city={city!r} target_date={target_date!r}). Both subtract a per-city "
            "mean residual; composing them double-subtracts the warm-shift. Exactly one of "
            "edli_v1.edli_bias_correction_enabled / "
            "edli_v1.edli_grid_representativeness_correction_enabled may be active. "
            "Failing closed rather than over-correcting q."
        )


def _edli_bias_treatment_for_bucket(
    *,
    family,
    city,
    snapshot: dict[str, Any] | None = None,
):
    """Build the single typed ``BiasTreatment`` decision for a (city,bucket).

    Phase-2 K2+N1+#122 (task #167). This is the ONE place the per-(city,bucket) bias is
    turned into a decision; both the p_raw correction and the Kelly haircut consult it so a
    bias is corrected XOR haircut, never both (kills the N1 double penalty). The fail-closed
    BiasTreatment factory refuses NULL/non-VERIFIED authority (#122) and a training_cutoff
    outside the target season (stale-fit gate). Returns ``None`` when:
      * ``edli_v1.bias_treatment_v2_enabled`` is OFF (legacy paths own the decision), OR
      * no VERIFIED row / weight_live<=0 / effective_bias missing, OR
      * the row fails the provenance or staleness gate (fail-closed).

    The returned mode is CORRECT whenever the correction would be live for this bucket
    (``edli_v1.edli_bias_correction_enabled`` ON), else HAIRCUT — so the two consumers are
    mutually exclusive by construction. Native unit: degC for C-cities, degF (x1.8) for
    F-settled cities (matches the legacy member-array unit).
    """
    try:
        ev = settings["edli_v1"]
        if not bool(ev.get("bias_treatment_v2_enabled", False)):
            return None
        import contextlib
        from src.calibration.manager import season_from_date
        from src.calibration.ens_bias_repo import read_bias_model
        from src.state.db import get_world_connection
        from src.contracts.bias_treatment import (
            BiasProvenanceError,
            BiasStaleError,
            BiasTreatment,
            BiasTreatmentMode,
        )

        metric = family.metric
        ldv = (
            "ecmwf_opendata_mx2t3_local_calendar_day_max"
            if metric == "high"
            else "ecmwf_opendata_mn2t3_local_calendar_day_min"
        )
        season = season_from_date(str(family.target_date), lat=city.lat)
        month = int(str(family.target_date)[5:7])
        with contextlib.closing(get_world_connection()) as conn:
            try:
                conn.row_factory = sqlite3.Row
            except Exception:
                pass
            row = read_bias_model(
                conn,
                city=city.name,
                season=season,
                metric=metric,
                live_data_version=ldv,
                month=month,
                target_month=month,
                authority="VERIFIED",
                error_model_family=_EDLI_BIAS_FAMILY,
            )
        if row is None:
            return None
        keys = set(row.keys())
        eff = row["effective_bias_c"] if "effective_bias_c" in keys else None
        wl = row["weight_live"] if "weight_live" in keys else 0.0
        if eff is None or float(wl or 0.0) <= 0.0:
            return None

        unit = getattr(city, "settlement_unit", "C")
        scale = 1.8 if unit == "F" else 1.0
        eff_native = float(eff) * scale
        resid_c = row["residual_sd_c"] if "residual_sd_c" in keys else None
        resid_native = abs(float(resid_c)) * scale if resid_c is not None else 0.0
        n_live = int(row["n_live"]) if ("n_live" in keys and row["n_live"] is not None) else 0
        cs = row["correction_strength"] if "correction_strength" in keys else None
        cs = float(cs) if cs is not None else 1.0
        authority = row["authority"] if "authority" in keys else None
        training_cutoff = row["training_cutoff"] if "training_cutoff" in keys else None

        thr = float(ev.get("bias_decay_threshold_f", 3.0)) if unit == "F" else float(
            ev.get("bias_decay_threshold_c", 2.0)
        )
        correction_on = bool(ev.get("edli_bias_correction_enabled", False))
        mode = BiasTreatmentMode.CORRECT if correction_on else BiasTreatmentMode.HAIRCUT
        try:
            return BiasTreatment.from_row(
                effective_bias_native=eff_native,
                residual_sd_native=resid_native,
                n_live=n_live,
                correction_strength=cs,
                authority=authority,
                training_cutoff=training_cutoff,
                target_date=str(family.target_date),
                lat=float(city.lat),
                threshold_native=thr,
                mode=mode,
            )
        except (BiasProvenanceError, BiasStaleError) as exc:
            # Fail closed: a NULL-authority or stale row never enters live q.
            import logging
            logging.getLogger("zeus.edli_bias").warning(
                "BiasTreatment refused (fail-closed) city=%s metric=%s: %s",
                getattr(city, "name", family.city), metric, exc,
            )
            return None
    except Exception as exc:  # never break the live decision path
        try:
            import logging
            logging.getLogger("zeus.edli_bias").warning(
                "BiasTreatment build skipped (fail-closed): %s", exc
            )
        except Exception:
            pass
        return None


def _maybe_apply_edli_bias_correction(
    members: np.ndarray,
    *,
    snapshot: dict[str, Any],
    family,
    city,
    payload: dict[str, object],
) -> tuple[np.ndarray, bool]:
    """A4 per-city promoted bias correction for the LIVE EDLI p_raw path.

    Subtracts the promoted ``model_bias_ens.effective_bias_c`` (per city x season x
    metric x live_data_version, authority='VERIFIED', error_model_family='edli_per_city_v1',
    weight_live>0) from the member maxes BEFORE p_raw is computed. The bias sign
    convention is ``effective_bias_c = mean(forecast - observed)`` so subtracting it
    de-biases toward observed truth (cold forecast => negative bias_c => members warmed).

    Flag-gated by ``edli_v1.edli_bias_correction_enabled`` (default OFF: prepared, not
    active). FAIL-CLOSED: any missing flag/row/field or error returns the raw members
    with applied=False, so the live path never breaks and never applies an unverified
    correction. When applied, the caller marks payload['_edli_bias_corrected']=True so
    the calibration step uses identity Platt for the corrected p_raw domain (train/serve
    lockstep — calibration_pairs were fit on uncorrected p_raw).
    """
    try:
        if not bool(settings["edli_v1"].get("edli_bias_correction_enabled", False)):
            return members, False
        # Phase-2 K2+N1+#122 (task #167): when bias_treatment_v2_enabled is ON, the typed
        # BiasTreatment gate is the single fail-closed decision. A NULL-authority (#122) or
        # stale-cutoff row yields treatment=None -> NO correction (raw members), so an
        # unverified/out-of-season bias never enters live q. The shift it applies is
        # IDENTICAL to the legacy subtraction (eff_native = eff * (1.8 if F else 1)); only
        # the fail-closed GATE is added. Flag OFF -> this block is skipped -> byte-identical.
        if bool(settings["edli_v1"].get("bias_treatment_v2_enabled", False)):
            _treatment = _edli_bias_treatment_for_bucket(
                family=family, city=city, snapshot=snapshot
            )
            if _treatment is None or not _treatment.is_correcting:
                return members, False
            corrected = np.asarray(members, dtype=float) - float(_treatment.shift_native)
            import logging
            logging.getLogger("zeus.edli_bias").info(
                "EDLI bias correction (v2 BiasTreatment) city=%s metric=%s shift_native=%.3f "
                "n_live=%d authority=%s", city.name, family.metric,
                float(_treatment.shift_native), int(_treatment.n_live), _treatment.authority,
            )
            return corrected, True
        import contextlib
        from src.calibration.manager import season_from_date
        from src.calibration.ens_bias_repo import read_bias_model
        from src.state.db import get_world_connection

        ldv = _nonnull(
            snapshot.get("dataset_id")
            or snapshot.get("data_version")
            or payload.get("dataset_id")
        )
        if not ldv:
            return members, False
        season = season_from_date(str(family.target_date), lat=city.lat)
        _tmonth = int(str(family.target_date)[5:7])
        with contextlib.closing(get_world_connection()) as conn:
            row = read_bias_model(
                conn,
                city=city.name,
                season=season,
                metric=family.metric,
                live_data_version=str(ldv),
                month=_tmonth,
                target_month=_tmonth,
                authority="VERIFIED",
                error_model_family=_EDLI_BIAS_FAMILY,
            )
        if row is None:
            return members, False
        keys = set(row.keys())
        eff = row["effective_bias_c"] if "effective_bias_c" in keys else None
        wl = row["weight_live"] if "weight_live" in keys else 0.0
        if eff is None or float(wl or 0.0) <= 0.0:
            return members, False
        # UNIT FIX (2026-05-31): effective_bias_c is degC; members carry the city's
        # SETTLEMENT unit. SF/Seattle settle degF, so a degC bias must be converted to
        # degF (x1.8) before subtracting — else F-cities are under-corrected 1.8x.
        # Validated by settled-truth backtest (SF bin_bias<=1 8%->65% with unit-correct form).
        _unit = getattr(city, "settlement_unit", "C")
        eff_native = float(eff) * 1.8 if _unit == "F" else float(eff)
        corrected = np.asarray(members, dtype=float) - eff_native
        import logging
        logging.getLogger("zeus.edli_bias").info(
            "EDLI bias correction applied city=%s season=%s metric=%s unit=%s eff_bias_c=%.3f eff_native=%.3f",
            city.name, season, family.metric, _unit, float(eff), eff_native,
        )
        return corrected, True
    except Exception as exc:  # fail-closed: never break the live decision path
        try:
            import logging
            logging.getLogger("zeus.edli_bias").warning(
                "EDLI bias correction skipped (fail-closed): %s", exc
            )
        except Exception:
            pass
        return members, False


def _edli_representativeness_sigma_native(
    *,
    snapshot: dict[str, Any],
    family,
    city,
) -> float:
    """Per-city representativeness σ (forecast-vs-settlement residual std), NATIVE unit.

    Iron-rule-6 pre-arm antibody (2026-06-03). The EDLI bias correction shifts the member
    MEAN but does NOT widen the spread, so the bootstrap CI (q_lcb) is over-confident on
    corrected cities. This returns the irreducible representativeness uncertainty — the std
    of the forecast-vs-settlement residual the correction is trained on — so the caller can
    fold it into the MC resampling noise in QUADRATURE and widen q_lcb honestly.

    Primary source: model_bias_ens.total_residual_sd_c (edli_per_city_v1, VERIFIED, same row
    keyed identically to _maybe_apply_edli_bias_correction). This is the FULL FORWARD PREDICTIVE
    σ — the in-sample daily residual std inflated by the mean-estimation drift (σ_resid·sqrt(1+
    1/n)) — NOT the in-sample-only residual_sd_c. #89 honest-q_lcb fix (2026-06-03): reading the
    in-sample-only std under-stated the predictive uncertainty and produced the over-confident
    deep-NO tail (claimed 0.93, realized 0.645). total_residual_sd_c is degC; for F-settled
    cities the member array is degF so the σ is scaled ×1.8 (degC delta → degF delta).

    Legacy/backward-compat: rows written before #89 carry total_residual_sd_c == residual_sd_c
    (or NULL); the reader falls back to residual_sd_c so pre-fix rows keep today's behaviour
    exactly (the widening only grows once the producer re-stamps the heterogeneity-inflated total).

    Fallback: if the row carries no usable σ, compute the per-city residual std from the
    trailing-window settled residuals (mean over the last settled days of
    raw_ens_mean − settlement, in settlement unit). Robust either way.

    FAIL-SAFE: returns 0.0 only when no σ can be sourced (then q_lcb stays at today's
    behaviour — never tighter). Never raises: a thrown exception here must not break the
    live decision path, but a 0.0 here is the LEAST conservative outcome, so the primary
    and fallback are both attempted before giving up.
    """
    _unit = getattr(city, "settlement_unit", "C")
    _scale = 1.8 if _unit == "F" else 1.0

    # ---- Primary: the FULL PREDICTIVE σ stamped on the VERIFIED edli bias row ----
    try:
        import contextlib
        from src.calibration.manager import season_from_date
        from src.calibration.ens_bias_repo import read_bias_model
        from src.state.db import get_world_connection

        ldv = _nonnull(
            snapshot.get("dataset_id")
            or snapshot.get("data_version")
            or None
        )
        if ldv:
            season = season_from_date(str(family.target_date), lat=city.lat)
            _tmonth = int(str(family.target_date)[5:7])
            with contextlib.closing(get_world_connection()) as conn:
                conn.row_factory = sqlite3.Row
                row = read_bias_model(
                    conn,
                    city=city.name,
                    season=season,
                    metric=family.metric,
                    live_data_version=str(ldv),
                    month=_tmonth,
                    target_month=_tmonth,
                    authority="VERIFIED",
                    error_model_family=_EDLI_BIAS_FAMILY,
                )
            if row is not None:
                keys = set(row.keys())
                # #89 honest q_lcb (2026-06-03): prefer the FULL FORWARD PREDICTIVE σ
                # (total_residual_sd_c = σ_resid·sqrt(1+1/n)), which captures the mean-
                # estimation drift the in-sample-only residual_sd_c drops. Fall back to
                # residual_sd_c for legacy rows that predate the heterogeneity stamp, so
                # pre-fix behaviour is preserved exactly. Both are degC; ×_scale → native.
                total_c = row["total_residual_sd_c"] if "total_residual_sd_c" in keys else None
                resid_c = row["residual_sd_c"] if "residual_sd_c" in keys else None
                chosen = None
                if total_c is not None and float(total_c) > 0.0 and np.isfinite(float(total_c)):
                    chosen = float(total_c)
                elif resid_c is not None and float(resid_c) > 0.0 and np.isfinite(float(resid_c)):
                    chosen = float(resid_c)
                # Defensive: total must never be < in-sample residual (a predictive σ that is
                # narrower than the in-sample scatter is not honest). Floor to residual_sd_c.
                if chosen is not None:
                    if resid_c is not None and float(resid_c) > 0.0 and np.isfinite(float(resid_c)):
                        chosen = max(chosen, float(resid_c))
                    sigma_native = chosen * _scale
                    # Phase-2 K2 D4 (task #167): when bias_treatment_v2_enabled is ON and the
                    # fit is low-n (n_live<20), fold the bias-MEAN standard error
                    # (shift_se = residual_sd/sqrt(n)) into the representativeness σ IN
                    # QUADRATURE so a low-n correction WIDENS q_lcb rather than applying a
                    # hard point shift (iron rule 6).
                    #
                    # K2 D4 DOUBLE-COUNT FIX (2026-06-03, adversarial-verify finding #2):
                    # the quadrature BASE must be the IN-SAMPLE residual_sd_c (= σ_resid),
                    # NOT total_residual_sd_c. total_residual_sd_c = σ_resid·sqrt(1 + 1/n)
                    # ALREADY contains the 1/n mean-estimation-drift term, so folding
                    # shift_se² = σ_resid²/n onto total² gave σ_resid²·(1 + 2/n) — the 1/n is
                    # counted TWICE (~6% over-wide q_lcb at n=7). Basing the fold on
                    # residual_sd_c reconstructs exactly the intended predictive σ:
                    #   sqrt(σ_resid² + σ_resid²/n) = σ_resid·sqrt(1 + 1/n) = total_residual_sd_c.
                    # The fold therefore widens to the honest predictive σ once, never twice.
                    # Flag OFF -> sigma_native returned unchanged (byte-identical legacy).
                    try:
                        if bool(settings["edli_v1"].get("bias_treatment_v2_enabled", False)):
                            import math as _math
                            _n = (
                                int(row["n_live"])
                                if ("n_live" in keys and row["n_live"] is not None)
                                else 0
                            )
                            if 0 < _n < 20 and resid_c is not None and float(resid_c) > 0.0:
                                # IN-SAMPLE σ is the quadrature base (no 1/n term in it).
                                in_sample_native = float(resid_c) * _scale
                                shift_se_native = (float(resid_c) / _math.sqrt(_n)) * _scale
                                folded = float(
                                    _math.sqrt(in_sample_native ** 2 + shift_se_native ** 2)
                                )
                                # Defensive: never let the D4 fold TIGHTEN below the σ already
                                # chosen (total_residual_sd_c floor). With honest producer
                                # stamps folded == sigma_native; the max only guards a row
                                # whose total < σ_resid·sqrt(1+1/n) (stale/legacy stamp).
                                sigma_native = max(sigma_native, folded)
                    except Exception:
                        pass
                    return sigma_native
    except Exception as exc:
        try:
            import logging
            logging.getLogger("zeus.edli_bias").warning(
                "representativeness σ primary read failed (trying fallback): %s", exc
            )
        except Exception:
            pass

    # ---- Fallback: trailing-window settled residual std (raw_ens_mean − settlement) ----
    try:
        sigma_native = _trailing_residual_std_native(family=family, city=city, scale=_scale)
        if sigma_native is not None and sigma_native > 0.0 and np.isfinite(sigma_native):
            return float(sigma_native)
    except Exception as exc:
        try:
            import logging
            logging.getLogger("zeus.edli_bias").warning(
                "representativeness σ fallback failed (σ_repr=0.0): %s", exc
            )
        except Exception:
            pass
    return 0.0


# Trailing window (days) for the fallback per-city residual-std computation.
_REPRESENTATIVENESS_FALLBACK_WINDOW_DAYS = 7
_REPRESENTATIVENESS_FALLBACK_MIN_N = 3


def _trailing_residual_std_native(*, family, city, scale: float) -> float | None:
    """Compute per-city residual std (forecast raw_ens_mean − settlement) over the trailing
    window of settled days, returned in the members' NATIVE unit.

    Joins settlement_outcomes (settlement_value) to ensemble_snapshots (members_json →
    raw ensemble mean) for the same city/metric. The residual is computed in the SETTLEMENT
    unit (settlement_value and the snapshot members are both in the settlement unit at this
    seam), so no per-source conversion is needed; ``scale`` only carries the degC→native
    factor for callers whose σ source is degC (the primary path). Here the residual is
    ALREADY native, so scale is NOT re-applied — the std is returned directly.

    Returns None when fewer than _REPRESENTATIVENESS_FALLBACK_MIN_N settled residuals exist
    (too thin to trust a scale), so the caller falls back to 0.0 (today's behaviour).
    """
    import contextlib
    import json
    import statistics
    from src.state.db import get_forecasts_connection

    _ = scale  # residual is already in native settlement unit; scale intentionally unused
    metric = family.metric
    target_date = str(family.target_date)
    with contextlib.closing(get_forecasts_connection()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT s.target_date AS td, s.settlement_value AS sv, e.members_json AS mj
            FROM settlement_outcomes s
            JOIN ensemble_snapshots e
              ON e.city = s.city
             AND e.target_date = s.target_date
             AND e.temperature_metric = s.temperature_metric
            WHERE s.city = ?
              AND s.temperature_metric = ?
              AND s.authority = 'VERIFIED'
              AND s.settlement_value IS NOT NULL
              AND s.target_date < ?
            ORDER BY s.target_date DESC
            LIMIT ?
            """,
            (city.name, metric, target_date, _REPRESENTATIVENESS_FALLBACK_WINDOW_DAYS * 4),
        ).fetchall()
    residuals: list[float] = []
    seen_dates: set[str] = set()
    for r in rows:
        td = str(r["td"])
        if td in seen_dates:
            continue
        try:
            members = json.loads(r["mj"]) if r["mj"] else None
            if not members:
                continue
            ens_mean = float(np.mean(np.asarray(members, dtype=float)))
            settlement = float(r["sv"])
        except Exception:
            continue
        residuals.append(ens_mean - settlement)
        seen_dates.add(td)
        if len(seen_dates) >= _REPRESENTATIVENESS_FALLBACK_WINDOW_DAYS:
            break
    if len(residuals) < _REPRESENTATIVENESS_FALLBACK_MIN_N:
        return None
    return float(statistics.stdev(residuals))


def _maybe_apply_grid_representativeness_correction(
    members: np.ndarray,
    *,
    snapshot: dict[str, Any],
    family,
    city,
    payload: dict[str, object],
) -> tuple[np.ndarray, bool]:
    """Per-(city,season) grid→point representativeness offset for the LIVE EDLI p_raw path.

    Subtracts the OOS-validated, shrunk per-(city,season) offset from member maxes BEFORE
    p_raw is computed. The offset sign convention is ``offset_c = mean(ENS_member_mean −
    obs_daily_max)`` so subtracting it warms cold-biased members toward the settlement
    station point (offset_c is negative for cold cities → subtracting warms).

    Flag-gated by ``edli_v1.edli_grid_representativeness_correction_enabled`` (default OFF).
    FAIL-CLOSED: any missing flag/table/entry/activated=False/error → return raw members
    with applied=False, so live behavior is byte-identical to today when the flag is absent.

    When applied, sets payload['_edli_grid_corrected']=True so the calibration step uses
    identity Platt for the corrected p_raw domain (mirrors _edli_bias_corrected semantics).

    Unit convention: offset_c is in °C. For F-settled cities (settlement_unit='F'), the
    member array is in °F, so offset_native = offset_c × 1.8.
    """
    try:
        if not bool(settings["edli_v1"].get("edli_grid_representativeness_correction_enabled", False)):
            return members, False
        from src.calibration.grid_representativeness import get_offset
        from src.calibration.manager import season_from_date

        season = season_from_date(str(family.target_date), lat=city.lat)
        # METRIC GATE (codex P1, 2026-06-02): the grid offset table is fit for
        # metric='high' ONLY (grid_representativeness.py / fit_grid_representativeness_offset.py).
        # A LOW family must NOT receive a HIGH-derived offset — that would mix the
        # high/low tracks and shift LOW-market p_raw by the wrong physical quantity.
        # Pass family.metric and fail closed (get_offset returns None) for any
        # non-high metric until separate LOW offsets are fit.
        entry = get_offset(city.name, season, metric=str(getattr(family, "metric", "high")))
        if entry is None:
            return members, False
        offset_c = float(entry["offset_c"])
        _unit = getattr(city, "settlement_unit", "C")
        offset_native = offset_c * 1.8 if _unit == "F" else offset_c
        corrected = np.asarray(members, dtype=float) - offset_native
        payload["_edli_grid_corrected"] = True
        import logging
        logging.getLogger("zeus.grid_repr").info(
            "EDLI grid-repr correction applied city=%s season=%s unit=%s offset_c=%.3f offset_native=%.3f",
            city.name, season, _unit, offset_c, offset_native,
        )
        return corrected, True
    except Exception as exc:
        try:
            import logging
            logging.getLogger("zeus.grid_repr").warning(
                "EDLI grid-repr correction skipped (fail-closed): %s", exc
            )
        except Exception:
            pass
        return members, False


def _write_emos_shadow_ledger(
    *,
    event: "OpportunityEvent",
    family,
    snapshot: dict[str, Any],
    analysis: Any,
    q_by_condition: dict[str, float],
    decision_time: datetime,
    lcb_by_direction: dict | None = None,
    native_costs: dict | None = None,
) -> None:
    """Write per-bin EMOS shadow ledger rows (PIECE 2 + CI extension 2026-06-02).

    Called from _canonical_probability_and_fdr_proof ONLY when
    edli_v1.edli_emos_shadow_ledger_enabled is True.  FAIL-OPEN: caller
    wraps in try/except so any raise here is silently absorbed.

    lcb_by_direction: the live q_5pct in probability space, built at adapter:3107.
        Keys: (condition_id, "buy_yes") / (condition_id, "buy_no").
        Defaults to None → cost/lcb/score fields recorded as null.
    native_costs: the executable-ask cost dict from _canonical_probability_and_fdr_proof.
        native_costs[(cond, dir)][1] is the ExecutionPrice; .value = the live ask.
        Defaults to None → same fallback.
    """
    import logging as _logging
    from datetime import timezone as _tz
    from src.calibration.emos import emos_predictive, bin_probability_settlement as bin_probability, season_for
    from src.calibration.emos_ledger import append_ledger
    from src.calibration.emos_ci_shadow import compute_robust_edge
    from src.contracts.season import season_from_date

    city_obj = runtime_cities_by_name().get(family.city)
    lat = getattr(city_obj, "lat", 90.0) if city_obj else 90.0
    season = season_from_date(str(family.target_date), lat=lat)
    lead_days = _snapshot_lead_days(snapshot=snapshot, family=family, payload=_payload(event))

    # members_c: read raw members from snapshot["members_json"] — the EXACT source used
    # in fit_emos_calibration.py (scripts/fit_emos_calibration.py:68-70).  This matches the
    # EMOS fit (raw 51 members, no bias/grid offset), so forward emos_q is consistent with
    # the backward coverage license.  The bug: getattr(analysis,"member_maxes",...) returned
    # an EMPTY array because the attribute is analysis._member_maxes (private, and
    # uncertainty-adjusted — different from raw fit source anyway).
    # Option (a) preferred: snapshot already available here; _snapshot_members() is the
    # same parse path the adapter uses for p_raw computation.
    try:
        members_native = _snapshot_members(snapshot).astype(float)
    except Exception:
        members_native = np.array([], dtype=float)
    unit = getattr(analysis, "_unit", "C")
    if unit == "F":
        members_c = (members_native - 32.0) * 5.0 / 9.0
    else:
        members_c = members_native

    raw_mu_c = float(np.mean(members_c)) if members_c.size > 0 else float("nan")
    raw_sigma_c = float(np.std(members_c, ddof=1)) if members_c.size > 1 else float("nan")

    # EMOS calibration table is HIGH-metric only (fit_emos_calibration.py:68 WHERE
    # temperature_metric='high').  Applying HIGH params to LOW members produces garbage
    # emos_q.  Gate the entire EMOS computation on metric == "high".
    family_metric = str(getattr(family, "metric", "") or "").lower()
    is_high_metric = (family_metric == "high")

    emos_mu_c: float | None = None
    emos_sigma_c: float | None = None
    served_status = "missing"
    if is_high_metric:
        emos_result = emos_predictive(
            family.city, season, lead_days, members_c,
            metric=str(getattr(family, "metric", "high") or "high").lower(),
        )
        if emos_result is not None:
            emos_mu_c, emos_sigma_c = emos_result
            served_status = "emos"
        else:
            # Distinguish raw vs missing by checking table directly
            from src.calibration.emos import load_emos_table
            tbl = load_emos_table()
            cell = tbl.get("cells", {}).get(f"{family.city}|{season}|high")  # 3-key (HIGH path)
            if cell is not None:
                served_status = str(cell.get("served", "missing"))
            else:
                served_status = "missing"
    else:
        # LOW or unknown metric: EMOS not applicable; served_status remains "missing"
        # raw fields (raw_mu_c, raw_sigma_c) still recorded for completeness.
        served_status = "not_high_metric"

    # p_raw is the raw ensemble vector (before Platt); p_cal is after Platt.
    # raw_q (stored below) records p_cal[index] — the Platt-calibrated probability.
    # q_live = q_by_condition[cond] = the post-evaluate_live_bins q (the live trade score q).
    # The robust score MUST use q_live, not raw_q (q-domain parity, spec §2b/#91/#105).
    p_posterior_vec = np.asarray(getattr(analysis, "p_cal", np.array([])), dtype=float)

    ts = decision_time.astimezone(_tz.utc).isoformat()

    for index, candidate in enumerate(family.candidates):
        condition_id = str(candidate.condition_id or "")
        raw_q = float(p_posterior_vec[index]) if index < len(p_posterior_vec) else float("nan")
        b = candidate.bin
        bin_low = b.low
        bin_high = b.high
        bin_unit = getattr(b, "unit", unit)

        # q_live: the live q after evaluate_live_bins (the value the trade score uses).
        q_live: float | None = q_by_condition.get(condition_id)

        # EMOS q and q_lcb — computed in the bin's native unit (unit-correct per spec §5).
        emos_q: float | None = None
        emos_q_lcb: float | None = None
        mu_native: float | None = None
        sigma_native: float | None = None
        if emos_mu_c is not None and emos_sigma_c is not None:
            try:
                if bin_unit == "F":
                    mu_native = emos_mu_c * 9.0 / 5.0 + 32.0
                    sigma_native = emos_sigma_c * 9.0 / 5.0
                else:
                    mu_native = emos_mu_c
                    sigma_native = emos_sigma_c
                emos_q = bin_probability(mu_native, sigma_native, bin_low, bin_high)
                # k_cov=1.0 in shadow: emos_q_lcb = min(emos_q, q(mu, 1.0*sigma)) = emos_q
                # (harness re-derives k_cov post-hoc from realized coverage)
                emos_q_lcb = emos_q  # k_cov=1.0 → min(emos_q, emos_q) = emos_q
            except Exception:
                emos_q = None
                emos_q_lcb = None

        # LCB values from lcb_by_direction (live MC q_5pct in probability space).
        raw_q_lcb_buy_yes: float | None = None
        raw_q_lcb_buy_no: float | None = None
        if lcb_by_direction is not None:
            from src.calibration.qlcb_provenance import _qlcb_float
            _lcb_yes = lcb_by_direction.get((condition_id, "buy_yes"))
            _lcb_no = lcb_by_direction.get((condition_id, "buy_no"))
            raw_q_lcb_buy_yes = _qlcb_float(_lcb_yes) if _lcb_yes is not None else None
            raw_q_lcb_buy_no = _qlcb_float(_lcb_no) if _lcb_no is not None else None

        # Costs from native_costs (the executable ask the trade score uses).
        # native_costs[(cond, dir)] is a 5-tuple; index [1] is ExecutionPrice | None.
        cost_buy_yes: float | None = None
        cost_buy_no: float | None = None
        if native_costs is not None:
            _ep_yes = (native_costs.get((condition_id, "buy_yes")) or (None, None))[1]
            _ep_no = (native_costs.get((condition_id, "buy_no")) or (None, None))[1]
            cost_buy_yes = float(_ep_yes.value) if _ep_yes is not None else None
            cost_buy_no = float(_ep_no.value) if _ep_no is not None else None

        # Robust edge scores (replicate trade_score.py:48-52 using q_live, NOT raw_q).
        # buy_yes: q_posterior = q_live, q_5pct = raw_q_lcb_buy_yes, cost = cost_buy_yes
        # buy_no:  q_posterior = native NO posterior, q_5pct = raw_q_lcb_buy_no, cost = cost_buy_no
        #          (INV/#106: buy_no lcb is independent, NOT negation of buy_yes lcb)
        _PENALTY = 0.01  # mirror adapter:4525-4526

        robust_score_raw_buy_yes: float | None = None
        robust_score_raw_buy_no: float | None = None
        if q_live is not None and raw_q_lcb_buy_yes is not None and cost_buy_yes is not None:
            robust_score_raw_buy_yes = compute_robust_edge(
                q_posterior=q_live,
                q_5pct=raw_q_lcb_buy_yes,
                cost=cost_buy_yes,
                penalty=_PENALTY,
            )
        if q_live is not None and raw_q_lcb_buy_no is not None and cost_buy_no is not None:
            robust_score_raw_buy_no = None

        robust_score_emos_buy_yes: float | None = None
        robust_score_emos_buy_no: float | None = None
        if emos_q is not None and emos_q_lcb is not None and cost_buy_yes is not None:
            # k_cov=1 → both emos_q_lcb and emos_q equal, so min(...) = emos_q
            robust_score_emos_buy_yes = compute_robust_edge(
                q_posterior=emos_q,
                q_5pct=emos_q_lcb,
                cost=cost_buy_yes,
                penalty=_PENALTY,
            )
        if emos_q is not None and emos_q_lcb is not None and cost_buy_no is not None:
            robust_score_emos_buy_no = None

        # Clearing booleans
        would_clear_emos_buy_yes = (
            bool(robust_score_emos_buy_yes > 0) if robust_score_emos_buy_yes is not None else None
        )
        would_clear_emos_buy_no = (
            bool(robust_score_emos_buy_no > 0) if robust_score_emos_buy_no is not None else None
        )
        cleared_raw_buy_yes = (
            bool(robust_score_raw_buy_yes > 0) if robust_score_raw_buy_yes is not None else None
        )
        cleared_raw_buy_no = (
            bool(robust_score_raw_buy_no > 0) if robust_score_raw_buy_no is not None else None
        )

        # FIX B — write-boundary staleness reject.
        # Reject rows whose decision_time is far from wall-clock now.
        # Catches replay/fixture-contamination leaks at the write boundary:
        # live daemon rows are written within seconds of the event; a row with
        # decision_time 2+ days old is from a replay or a test fixture that
        # slipped through the path seam.
        # FAIL-OPEN: any exception in this check is silently swallowed; the
        # row is rejected on stale detection but never raises into the hot path.
        _STALE_BOUNDARY_DAYS = 2
        try:
            from datetime import timezone as _tz_b
            _now_utc = datetime.now(_tz_b.utc)
            _age_seconds = abs((_now_utc - decision_time.astimezone(_tz_b.utc)).total_seconds())
            if _age_seconds > _STALE_BOUNDARY_DAYS * 86400:
                import logging as _lg_b
                _lg_b.getLogger(__name__).debug(
                    "emos_ledger: skipping stale row (age=%.0fs > %dd) for %s/%s",
                    _age_seconds, _STALE_BOUNDARY_DAYS, family.city, str(family.target_date),
                )
                continue
        except Exception:
            pass  # fail-open: if we can't check age, proceed with write

        row = {
            "ts": ts,
            "city": family.city,
            "target_date": str(family.target_date),
            "season": season,
            "lead_days": lead_days,
            "metric": family_metric,  # "high" / "low" / "" — EMOS only valid for "high"
            "bin_label": b.label,
            "bin_low": bin_low,
            "bin_high": bin_high,
            "bin_unit": bin_unit,
            "raw_q": raw_q,           # p_cal[index] (Platt-calibrated, pre-evaluate_live_bins)
            "q_live": q_live,          # q_by_condition[cond] (post-evaluate_live_bins, trade-score q)
            "emos_q": emos_q,
            "emos_q_lcb": emos_q_lcb,  # k_cov=1 shadow: == emos_q; harness re-derives k_cov>1
            "raw_mu_c": raw_mu_c,
            "raw_sigma_c": raw_sigma_c,
            "emos_mu_c": emos_mu_c,
            "emos_sigma_c": emos_sigma_c,
            "served": served_status,
            "candidate_id": f"{family.family_id}:{condition_id}" if getattr(family, "family_id", None) else condition_id,
            # CI extension fields (spec §2b)
            "raw_q_lcb_buy_yes": raw_q_lcb_buy_yes,
            "raw_q_lcb_buy_no": raw_q_lcb_buy_no,
            "cost_buy_yes": cost_buy_yes,
            "cost_buy_no": cost_buy_no,
            "robust_score_raw_buy_yes": robust_score_raw_buy_yes,
            "robust_score_raw_buy_no": robust_score_raw_buy_no,
            "robust_score_emos_buy_yes": robust_score_emos_buy_yes,
            "robust_score_emos_buy_no": robust_score_emos_buy_no,
            "would_clear_emos_buy_yes": would_clear_emos_buy_yes,
            "would_clear_emos_buy_no": would_clear_emos_buy_no,
            "cleared_raw_buy_yes": cleared_raw_buy_yes,
            "cleared_raw_buy_no": cleared_raw_buy_no,
            "penalty_used": _PENALTY,
            "kcov_applied": 1.0,
        }
        append_ledger(row)


def _maybe_override_lcb_with_emos_ci(
    *,
    family,
    snapshot: dict[str, Any],
    analysis: Any,
    native_costs: dict | None,
    payload: dict[str, object],
    lcb_by_direction: dict[tuple[str, str], float],
) -> None:
    """EMOS-CI LIVE OVERRIDE (Option B, /tmp/design_emos_ci.md §6) — in-place mutate lcb_by_direction.

    For LICENSED HIGH-metric cities only, replace the MC q_5pct
    (lcb_by_direction[(cond,dir)]) with the coverage-honest EMOS analytic CI:

        emos_q          = bin_probability(mu_native, sigma_native, low, high)
        q_inflated      = bin_probability(mu_native, k_cov * sigma_native, low, high)
        buy_yes lcb     = min(emos_q, q_inflated)            # never optimistic (widening σ lowers a peaked bin)
        buy_no  lcb     = native NO lower bound only; absent native NO evidence means no live NO authority.

    Native unit: °F cities convert (mu_c, sigma_c) to °F exactly as
    _write_emos_shadow_ledger does (mirror EXACTLY). k_cov comes from the per-city
    license cell (clamped >= 1.0; sigma is never tightened).

    Gating (all must hold or the override is a no-op, MC lcb stands):
      - settings["edli_v1"].edli_emos_ci_live_enabled is True (default False)
      - family.metric == "high"
      - family.city in the EMOS-CI license (state/emos_ci_license.json)
      - emos_predictive(city, season, lead_days, members_c) is not None (served == emos)

    FAIL-CLOSED: any missing EMOS / per-bin error / any exception leaves the MC lcb
    for that key untouched. The function NEVER raises into the hot path and NEVER
    substitutes a wrong value (a per-bin failure keeps that bin's MC lcb).

    Touches ONLY lcb_by_direction. q_by_condition, p_values, prefilter, and
    hyp.p_value are unchanged (the FDR edge-space gate stays on the MC engine).
    """
    import logging as _logging

    try:
        if not bool(settings["edli_v1"].get("edli_emos_ci_live_enabled", False)):
            return
    except Exception:
        return

    # Metric gate: EMOS calibration is HIGH-metric only (HIGH params on LOW members = garbage).
    family_metric = str(getattr(family, "metric", "") or "").lower()
    if family_metric != "high":
        return

    # Per-city license gate (operator-armed). Absent city → no-op (fail-closed).
    try:
        from src.calibration.emos_ci_license import emos_ci_k_cov
        k_cov = emos_ci_k_cov(family.city)
    except Exception:
        return
    if k_cov is None:
        return

    log = _logging.getLogger("zeus.emos_ci_live")
    try:
        from src.calibration.emos import emos_predictive, bin_probability_settlement as bin_probability
        from src.contracts.season import season_from_date

        # Season + lead_days + members_c — EXACT mirror of _write_emos_shadow_ledger
        # (raw 51 members from snapshot["members_json"], °F→°C convert, season hemisphere-aware).
        city_obj = runtime_cities_by_name().get(family.city)
        lat = getattr(city_obj, "lat", 90.0) if city_obj else 90.0
        from src.calibration.emos import emos_season as _emos_season
        season = _emos_season(family.target_date)  # NH-month canonical (no SH season-crossing)
        lead_days = _snapshot_lead_days(snapshot=snapshot, family=family, payload=payload)
        try:
            members_native = _snapshot_members(snapshot).astype(float)
        except Exception:
            members_native = np.array([], dtype=float)
        unit = getattr(analysis, "_unit", "C")
        if unit == "F":
            members_c = (members_native - 32.0) * 5.0 / 9.0
        else:
            members_c = members_native

        emos_result = emos_predictive(
            family.city, season, lead_days, members_c,
            metric=str(getattr(family, "metric", "high") or "high").lower(),
        )
        if emos_result is None:
            # served != emos (raw/missing cell) or insufficient members → fail-closed, MC stands.
            return
        emos_mu_c, emos_sigma_c = emos_result
    except Exception as exc:
        log.warning("EMOS-CI live override setup failed (non-fatal, MC lcb kept): %s", exc)
        return

    for candidate in family.candidates:
        condition_id = str(candidate.condition_id or "")
        b = candidate.bin
        bin_unit = getattr(b, "unit", unit)
        try:
            # Native-unit conversion — EXACT mirror of _write_emos_shadow_ledger (3783-3788).
            if bin_unit == "F":
                mu_native = emos_mu_c * 9.0 / 5.0 + 32.0
                sigma_native = emos_sigma_c * 9.0 / 5.0
            else:
                mu_native = emos_mu_c
                sigma_native = emos_sigma_c
            emos_q = bin_probability(mu_native, sigma_native, b.low, b.high)
            q_inflated = bin_probability(mu_native, k_cov * sigma_native, b.low, b.high)
            # buy_yes: never-optimistic lower bound on the YES (in-bin) mass.
            emos_q_lcb_yes = min(emos_q, q_inflated)
            # Buy-NO requires an explicit NO-side posterior/LCB, not a YES complement.
            emos_q_lcb_no = 0.0
        except Exception as exc:
            log.warning(
                "EMOS-CI live override skipped bin %s/%s (non-fatal, MC lcb kept): %s",
                family.city, getattr(b, "label", "?"), exc,
            )
            continue

        for direction, emos_lcb in (("buy_yes", emos_q_lcb_yes), ("buy_no", emos_q_lcb_no)):
            key = (condition_id, direction)
            if key not in lcb_by_direction:
                # No MC entry for this direction (non-executable side) → nothing to override.
                continue
            from src.calibration.qlcb_provenance import _qlcb_float, _set_qlcb_provenance
            mc_lcb = _qlcb_float(lcb_by_direction[key])
            # K3: the EMOS analytic CI is its own calibration source. On the live
            # typed carrier this writes a QlcbProvenance(source=EMOS_ANALYTIC); on a
            # plain test dict it writes a bare float (legacy EMOS tests unchanged).
            _set_qlcb_provenance(
                lcb_by_direction, key, float(emos_lcb), source="EMOS_ANALYTIC"
            )
            try:
                log.info(
                    "EMOS-CI override city=%s cond=%s dir=%s k_cov=%.3f mc_lcb=%.6f->emos_lcb=%.6f",
                    family.city, condition_id, direction, k_cov, float(mc_lcb), float(emos_lcb),
                )
            except Exception:
                pass


def _settlement_coverage_observations(
    *,
    forecast_conn: sqlite3.Connection,
    city: str,
    metric: str,
    bin: Bin,
    direction: str,
    claimed_q_lcb: float,
):
    """Build the (claimed_q_lcb, won) coverage stream for ONE (bin, direction).

    Backward coverage: for every SETTLED outcome of this (city, metric), grade
    "had I traded THIS bin in THIS direction, would the settled value have won?"
    via the spine grade_receipt — the Direction Law + BinKind + unit antibodies are
    inherited, not re-rolled. Returns a list[CoverageObservation]. FAIL-OPEN: any
    error / unit mismatch yields an empty stream (→ INSUFFICIENT_DATA → no shrink).
    """
    from types import SimpleNamespace

    from src.calibration.settlement_backward_coverage import CoverageObservation
    from src.contracts.graded_receipt import grade_receipt
    from src.types.temperature import UnitMismatchError

    obs: list = []
    try:
        rows = forecast_conn.execute(
            "SELECT settlement_value, settlement_unit FROM settlement_outcomes "
            "WHERE city = ? AND temperature_metric = ? "
            "AND settlement_value IS NOT NULL AND settlement_unit IS NOT NULL",
            (str(city), str(metric).lower()),
        ).fetchall()
    except Exception:
        return obs
    for row in rows:
        try:
            settled_value = float(row[0])
            settled_unit = str(row[1])
        except (TypeError, ValueError):
            continue
        # settlement_outcomes.settlement_value is WMO-rounded at write time, so no
        # semantics object is needed (grade_receipt grades the stored value as-is).
        settlement = SimpleNamespace(
            settlement_value=settled_value, settlement_unit=settled_unit
        )
        try:
            graded = grade_receipt(bin, direction, settlement)
        except UnitMismatchError:
            # Cross-unit settlement for this bin — not a valid backward observation.
            continue
        except Exception:
            continue
        obs.append(CoverageObservation(q_lcb=float(claimed_q_lcb), won=bool(graded.won)))
    return obs


def _maybe_apply_settlement_coverage_to_lcb(
    *,
    family,
    forecast_conn: sqlite3.Connection,
    lcb_by_direction,
) -> None:
    """K3 (Phase-2): shrink an UNLICENSED q_lcb to its realized settlement rate.

    SHADOW FLAG (edli_v1.q_lcb_settlement_coverage_gate_enabled, default FALSE):
    flag OFF → IMMEDIATE no-op, the q_lcb is byte-identical to the EMOS/MC value.
    Flag ON → for each (cond, direction) build the backward-coverage stream through
    grade_receipt, run settlement_backward_coverage_check, and apply the shrink via
    apply_settlement_coverage (only UNLICENSED moves the number; the shrink only ever
    LOWERS the LCB). The new entry's calibration_source becomes SETTLEMENT_ISOTONIC.

    FAIL-OPEN: any error keeps the upstream lcb (never crash the hot path, never
    widen optimistically). Touches ONLY lcb_by_direction; q/p_values/prefilter stay.
    """
    import logging as _logging

    try:
        if not bool(settings["edli_v1"].get("q_lcb_settlement_coverage_gate_enabled", False)):
            return
    except Exception:
        return

    log = _logging.getLogger("zeus.qlcb_settlement_coverage")
    try:
        from src.calibration.qlcb_provenance import _qlcb_float, _set_qlcb_provenance
        from src.calibration.settlement_backward_coverage import (
            apply_settlement_coverage,
            settlement_backward_coverage_check,
        )
        from src.contracts.season import season_from_date

        metric = str(getattr(family, "metric", "") or "").lower()
        city_obj = runtime_cities_by_name().get(family.city)
        lat = getattr(city_obj, "lat", 90.0) if city_obj else 90.0
        season = season_from_date(str(getattr(family, "target_date", "")), lat=lat)
    except Exception as exc:
        log.warning("K3 coverage setup failed (non-fatal, lcb kept): %s", exc)
        return

    for candidate in family.candidates:
        condition_id = str(candidate.condition_id or "")
        bin_obj = getattr(candidate, "bin", None)
        if bin_obj is None:
            continue
        for direction in ("buy_yes", "buy_no"):
            key = (condition_id, direction)
            if key not in lcb_by_direction:
                continue
            try:
                claimed = _qlcb_float(lcb_by_direction[key])
                obs = _settlement_coverage_observations(
                    forecast_conn=forecast_conn,
                    city=family.city,
                    metric=metric,
                    bin=bin_obj,
                    direction=direction,
                    claimed_q_lcb=claimed,
                )
                verdict = settlement_backward_coverage_check(
                    city=family.city, metric=metric, season=season,
                    q_lcb=claimed, observations=obs, min_n=30,
                )
                new_q = apply_settlement_coverage(q_lcb=claimed, verdict=verdict, enabled=True)
                if new_q != claimed:
                    _set_qlcb_provenance(
                        lcb_by_direction, key, new_q,
                        source="SETTLEMENT_ISOTONIC",
                        n_settlement_observations=verdict.n_settlement_observations,
                        coverage_ratio=verdict.coverage_ratio,
                    )
                    log.info(
                        "K3 coverage shrink city=%s cond=%s dir=%s %.6f->%.6f (status=%s n=%d)",
                        family.city, condition_id, direction, claimed, new_q,
                        verdict.status, verdict.n_settlement_observations,
                    )
            except Exception as exc:
                log.warning(
                    "K3 coverage skipped bin %s/%s (non-fatal, lcb kept): %s",
                    family.city, getattr(bin_obj, "label", "?"), exc,
                )
                continue


def _snapshot_p_raw(
    snapshot: dict[str, Any],
    *,
    family,
    bins: list[Bin],
    members: np.ndarray,
    payload: dict[str, object],
    members_already_corrected: bool = False,
) -> np.ndarray:
    city = runtime_cities_by_name().get(family.city)
    if city is None:
        raise ValueError(f"city config missing for event-bound forecast inference: {family.city}")
    _snapshot_unit(snapshot, payload)
    _validate_snapshot_members_metric_identity(snapshot=snapshot, family=family, payload=payload)
    semantics = SettlementSemantics.for_city(city)
    # A4 (2026-05-31): per-city promoted bias correction on member maxes BEFORE p_raw.
    # Flag-gated (edli_v1.edli_bias_correction_enabled, default OFF) + FAIL-CLOSED.
    # §4.1 guard: skip if caller already hoisted correction (members_already_corrected=True)
    # to prevent double-application when _snapshot_p_raw is called from
    # _market_analysis_from_event_snapshot (which now owns the single correction site).
    if not members_already_corrected:
        members, _bias_corrected = _maybe_apply_edli_bias_correction(
            members, snapshot=snapshot, family=family, city=city, payload=payload
        )
        if _bias_corrected:
            payload["_edli_bias_corrected"] = True
    arr = p_raw_vector_from_maxes(members, city, semantics, bins)
    if arr.shape != (len(bins),) or not np.isfinite(arr).all() or np.any(arr < 0.0):
        raise ValueError("event-bound p_raw vector invalid")
    total = float(arr.sum())
    if total <= 0.0:
        raise ValueError("event-bound p_raw vector has zero mass")
    arr = arr / total
    return arr


def _snapshot_p_cal(
    calibration_conn: sqlite3.Connection,
    *,
    snapshot: dict[str, Any],
    family,
    bins: list[Bin],
    p_raw: np.ndarray,
    payload: dict[str, object],
    decision_time: datetime | None,
) -> np.ndarray:
    city = runtime_cities_by_name().get(family.city)
    if city is None:
        raise ValueError(f"CALIBRATION_AUTHORITY_MISSING:city config missing for {family.city}")

    # A4 lockstep: when the member maxes were bias-corrected OR grid-representativeness
    # corrected pre-p_raw, the existing Platt models were fit on the UNCORRECTED
    # (unshifted) p_raw domain and would mis-calibrate the shifted domain. Use identity
    # Platt (p_cal = normalized p_raw) for the corrected domain until a Platt is refit on
    # the corrected p_raw_domain. Enforces train/serve match.
    #   - _edli_bias_corrected: city-specific bias shift (_maybe_apply_edli_bias_correction)
    #   - _edli_grid_corrected: grid→point representativeness shift
    #     (codex P1, 2026-06-02): this flag was set but NEVER consumed here, so a
    #     grid-shifted p_raw was still fed through Platt fits on the unshifted domain.
    if bool(payload.get("_edli_bias_corrected")) or bool(payload.get("_edli_grid_corrected")):
        arr = np.asarray(p_raw, dtype=float)
        total = float(arr.sum())
        if not _valid_probability_vector(arr, len(bins)) or total <= 0.0:
            raise ValueError("CALIBRATION_AUTHORITY_MISSING:corrected p_raw invalid")
        return arr / total

    source_id = _nonnull(snapshot.get("source_id") or payload.get("source_id"))
    issue_time = _nonnull(snapshot.get("issue_time") or snapshot.get("source_cycle_time") or payload.get("cycle"))
    lead_days = _snapshot_lead_days(snapshot=snapshot, family=family, payload=payload)
    if not source_id or not issue_time:
        raise ValueError("CALIBRATION_AUTHORITY_MISSING:forecast provenance missing")

    from src.calibration.forecast_calibration_domain import derive_phase2_keys_from_ens_result
    from src.calibration.manager import _model_data_to_calibrator
    from src.calibration.platt import calibrate_and_normalize
    from src.data.forecast_source_registry import calibration_source_id_for_lookup

    cycle, raw_source_id, horizon_profile = derive_phase2_keys_from_ens_result(
        {
            "issue_time": issue_time,
            "source_id": source_id,
            "horizon_profile": snapshot.get("horizon_profile") or payload.get("horizon_profile"),
        }
    )
    calibration_source_id = calibration_source_id_for_lookup(raw_source_id)
    if calibration_source_id is None:
        raise ValueError("CALIBRATION_AUTHORITY_MISSING:unsupported forecast source")
    try:
        _row, _level, model_data = _persisted_calibration_model_row_for_receipt(
            calibration_conn,
            city=city,
            target_date=str(family.target_date),
            temperature_metric=family.metric,
            cycle=cycle,
            source_id=calibration_source_id,
            horizon_profile=horizon_profile,
        )
    except (sqlite3.Error, ValueError) as exc:
        raise ValueError("CALIBRATION_AUTHORITY_MISSING:calibration store unavailable") from exc
    if model_data is None:
        # Identity-Platt fallback: no fitted Platt for this (city, season, metric) bucket.
        # Use normalized p_raw as p_cal (identity passthrough). This is the designed
        # fail-closed default per platt_oos_resolver.py §P0: identity is the live default;
        # a fitted Platt is a CANDIDATE that requires OOS proof. Prevents whole-city
        # blackout when a season boundary is crossed before new Platt rows are fitted.
        # Tagged for log aggregation: calibration_identity_fallback_no_platt_bucket.
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "calibration_identity_fallback_no_platt_bucket city=%s "
            "metric=%s target_date=%s cycle=%s source_id=%s "
            "horizon_profile=%s — no fitted Platt for this bucket; using "
            "identity (p_cal = normalized p_raw). Fit a Platt to promote.",
            family.city,
            family.metric,
            family.target_date,
            cycle,
            calibration_source_id,
            horizon_profile,
        )
        arr = np.asarray(p_raw, dtype=float)
        total = float(arr.sum())
        if not _valid_probability_vector(arr, len(bins)) or total <= 0.0:
            raise ValueError("CALIBRATION_AUTHORITY_MISSING:identity fallback p_raw invalid")
        return arr / total
    cal = _model_data_to_calibrator(model_data)
    p_cal = calibrate_and_normalize(
        np.asarray(p_raw, dtype=float),
        cal,
        lead_days,
        bin_widths=[candidate.width for candidate in bins],
    )
    if not _valid_probability_vector(p_cal, len(bins)):
        raise ValueError("CALIBRATION_AUTHORITY_MISSING:p_cal invalid")
    return p_cal


def _snapshot_lead_days(*, snapshot: dict[str, Any], family, payload: dict[str, object]) -> float:
    lead_hours = _optional_float(snapshot.get("lead_hours") or payload.get("lead_hours"))
    if lead_hours is not None and lead_hours >= 0.0:
        return lead_hours / 24.0
    issue = _parse_utc(
        snapshot.get("issue_time")
        or snapshot.get("source_cycle_time")
        or snapshot.get("source_available_at")
        or snapshot.get("available_at")
        or payload.get("cycle")
        or payload.get("source_cycle_time")
        or payload.get("source_available_at")
        or payload.get("available_at")
        or payload.get("observation_time")
        or payload.get("observation_available_at")
    )
    try:
        target_day = date.fromisoformat(str(family.target_date))
    except ValueError as exc:
        raise ValueError("CALIBRATION_AUTHORITY_MISSING:target date invalid") from exc
    if issue is None:
        raise ValueError("CALIBRATION_AUTHORITY_MISSING:lead_days missing")
    target_start = _parse_utc(snapshot.get("local_day_start_utc") or payload.get("local_day_start_utc"))
    if target_start is None:
        target_start = datetime.combine(target_day, time.min, tzinfo=UTC)
    return max(0.0, (target_start - issue).total_seconds() / 86400.0)


def _valid_probability_vector(value: np.ndarray, expected_len: int) -> bool:
    arr = np.asarray(value, dtype=float)
    return (
        arr.shape == (expected_len,)
        and bool(np.isfinite(arr).all())
        and bool(np.all(arr >= 0.0))
        and float(arr.sum()) > 0.0
    )


def _probability_vector_hash(values) -> str:
    return stable_hash(tuple(round(float(value), 12) for value in values))


def _snapshot_unit(snapshot: dict[str, Any], payload: dict[str, object]) -> str:
    unit = _nonnull(snapshot.get("settlement_unit") or snapshot.get("unit"))
    if unit in {"F", "C"}:
        return unit
    members_unit = _nonnull(snapshot.get("members_unit"))
    if members_unit == "degC":
        return "C"
    if members_unit == "degF":
        return "F"
    raise ValueError("FORECAST_UNIT_AUTHORITY_MISSING")


def _snapshot_unit_authority_source(snapshot: dict[str, Any]) -> str:
    if _nonnull(snapshot.get("settlement_unit") or snapshot.get("unit")):
        return "ensemble_snapshots.settlement_unit"
    if _nonnull(snapshot.get("members_unit")):
        return "ensemble_snapshots.members_unit"
    raise ValueError("FORECAST_UNIT_AUTHORITY_MISSING")


def _validate_snapshot_members_metric_identity(*, snapshot: dict[str, Any], family, payload: dict[str, object]) -> None:
    snapshot_metric = _nonnull(snapshot.get("temperature_metric") or snapshot.get("members_extrema_metric_identity"))
    family_metric = _nonnull(getattr(family, "metric", None) or payload.get("metric") or payload.get("temperature_metric"))
    if not snapshot_metric or not family_metric:
        raise ValueError("FORECAST_MEMBERS_METRIC_IDENTITY_MISSING")
    if snapshot_metric != family_metric:
        raise ValueError("FORECAST_MEMBERS_METRIC_IDENTITY_MISMATCH")


def _members_extrema_transform(metric: object) -> str:
    if metric == "high":
        return "daily_max"
    if metric == "low":
        return "daily_min"
    raise ValueError("FORECAST_MEMBERS_METRIC_IDENTITY_MISSING")


def _day0_absorbing_mask(*, payload: dict[str, object], family) -> "np.ndarray":
    """Absorbing-boundary mask over family bins for a Day0 observed extreme.

    A bin is zeroed when the observed rounded extreme already rules it out:
    for ``high`` the observed max exceeds the bin's upper edge; for ``low`` the observed
    min falls below the bin's lower edge. Shoulder bins (open-ended edge) are retained.
    """
    rounded = _optional_float(payload.get("rounded_value"))
    if rounded is None:
        raise ValueError("Day0 event missing rounded_value")
    metric = _nonnull(payload.get("metric") or payload.get("temperature_metric"))
    mask = np.ones(len(family.candidates), dtype=float)
    for index, candidate in enumerate(family.candidates):
        bin_value = candidate.bin
        if metric == "high":
            if bin_value.high is not None and rounded > float(bin_value.high):
                mask[index] = 0.0
        elif metric == "low":
            if bin_value.low is not None and rounded < float(bin_value.low):
                mask[index] = 0.0
        else:
            raise ValueError(f"unsupported Day0 metric: {metric}")
    return mask


def _apply_day0_mask_to_probability_vector(*, payload: dict[str, object], family, vector) -> "np.ndarray":
    """Apply the Day0 absorbing-boundary mask to a probability vector and renormalize.

    Used pre-inference on p_raw / p_cal so the calibrated forecast respects the observed
    extreme before posterior + hypothesis construction. If the mask eliminates all support
    (degenerate observation) the unmasked vector is returned unchanged rather than dividing
    by zero — the downstream gates then reject on absent edge.
    """
    arr = np.asarray(vector, dtype=float)
    mask = _day0_absorbing_mask(payload=payload, family=family)
    masked = arr * mask
    total = float(masked.sum())
    if total <= 0.0:
        return arr
    return masked / total


def _day0_observation_age_minutes(
    payload: dict[str, object], decision_time: "datetime | None"
) -> float | None:
    """Age of the day0 running extreme at decision time, in minutes.

    Measured from the OBSERVATION VALID TIME (payload.observation_time — the
    station report timestamp), not from imported_at/observation_available_at:
    the absorbing boundary's truth-age is how old the station report itself is.
    Returns None when unparseable (callers must treat None as MAXIMALLY STALE —
    fail-closed; see day0 first-principles review 2026-06-10, charge #1).
    """
    if decision_time is None:
        return None
    raw = payload.get("observation_time") or payload.get("observation_available_at")
    if not raw:
        return None
    try:
        observed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return None
        age = (decision_time.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() / 60.0
    except (TypeError, ValueError, OSError):
        return None
    if not (age == age):  # NaN
        return None
    return max(0.0, age)


def _day0_stale_obs_boundary_guard_enabled() -> bool:
    """STALE-OBS BOUNDARY GUARD flag (day0 first-principles review 2026-06-10).

    Default TRUE (correctness, not tuning): WU station obs are published on a
    city-specific cadence (30/60-min METAR grid + publication delay; measured
    in config/wu_obs_latency.json). A running extreme older than the city's
    staleness budget is a stale LOWER bound — bins just above it may already
    be dead. The guard only ZEROES buy_yes q_lcb for boundary-adjacent bins
    (suppresses submits); it can never enable a trade. Fail-open returns True.
    """
    try:
        return bool(settings["edli_v1"].get("day0_stale_obs_boundary_guard_enabled", True))
    except Exception:
        return True


def _day0_remaining_day_q_enabled() -> bool:
    """REMAINING-DAY q mode flag (review 2026-06-10 item B). Default FALSE.

    Unlike the conservative-only guards (boundary suppression, anomaly pause,
    bootstrap LCB), this CHANGES the day0 point q in both directions (it can
    RAISE q for the bin containing the running extreme post-peak). It must be
    operator-flipped only after shadow receipts comparing
    _edli_day0_q_mode=remaining_day vs the legacy full-day-masked q look sane.
    """
    try:
        return bool(settings["edli_v1"].get("day0_remaining_day_q_enabled", False))
    except Exception:
        return False


def _day0_remaining_day_members(
    *,
    payload: dict[str, object],
    family,
    unit: str,
    decision_time: "datetime | None",
) -> "np.ndarray | None":
    """Pooled per-model remaining-day extremes in the NATIVE unit, clamped to
    the absorbing physical law. None (-> legacy full-day path) when no fresh
    persisted high-res vectors exist for this family.

    Source: day0_hourly_vectors lane (degC storage; converted here at the
    consumption seam). Clamp: HIGH max(value, running max); LOW min(value,
    running min) — below-floor remaining mass lands IN the floor bin.
    """
    if decision_time is None:
        return None
    try:
        from src.data.day0_hourly_vectors import (
            read_freshest_day0_hourly_vectors,
            remaining_day_extremes_c,
        )

        metric = str(payload.get("metric") or payload.get("temperature_metric") or "")
        if metric not in {"high", "low"}:
            return None
        vectors = read_freshest_day0_hourly_vectors(
            city=str(family.city), target_date=str(family.target_date), now=decision_time
        )
        if not vectors:
            return None
        extremes_c = remaining_day_extremes_c(
            vectors, target_date=str(family.target_date), now=decision_time, metric=metric
        )
        if not extremes_c:
            return None
        values = np.asarray(extremes_c, dtype=float)
        if str(unit).upper() == "F":
            values = values * 9.0 / 5.0 + 32.0
        rounded = _optional_float(payload.get("rounded_value"))
        if rounded is not None:
            values = (
                np.maximum(values, float(rounded))
                if metric == "high"
                else np.minimum(values, float(rounded))
            )
        payload["_edli_day0_remaining_models"] = int(values.size)
        return values
    except Exception as exc:  # noqa: BLE001 — degrade LOUDLY to the full-day path
        import logging as _logging

        _logging.getLogger("zeus.day0_remaining_day").warning(
            "DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE city=%s date=%s exc=%s: %s — full-day fallback",
            getattr(family, "city", "?"), getattr(family, "target_date", "?"),
            type(exc).__name__, exc,
        )
        return None


def _apply_day0_mask_to_generated_probabilities(
    *,
    payload: dict[str, object],
    family,
    q_by_condition: dict[str, float],
    lcb_by_condition: dict[tuple[str, str], float],
    decision_time: "datetime | None" = None,
) -> tuple[dict[str, float], dict[tuple[str, str], float]]:
    rounded = _optional_float(payload.get("rounded_value"))
    if rounded is None:
        raise ValueError("Day0 event missing rounded_value")
    metric = _nonnull(payload.get("metric") or payload.get("temperature_metric"))
    mask: list[float] = []
    for candidate in family.candidates:
        bin_value = candidate.bin
        if metric == "high":
            if bin_value.high is not None and rounded > float(bin_value.high):
                mask.append(0.0)
            elif bin_value.high is None and bin_value.low is not None and rounded >= float(bin_value.low):
                mask.append(1.0)
            else:
                mask.append(1.0)
        elif metric == "low":
            if bin_value.low is not None and rounded < float(bin_value.low):
                mask.append(0.0)
            elif bin_value.low is None and bin_value.high is not None and rounded <= float(bin_value.high):
                mask.append(1.0)
            else:
                mask.append(1.0)
        else:
            raise ValueError(f"unsupported Day0 metric: {metric}")
    # STALE-OBS BOUNDARY GUARD (day0 first-principles review 2026-06-10, charge #1).
    # The running extreme is a monotone bound: KILLING bins with a stale extreme is
    # always safe (the true extreme can only be further along). But a bin the stale
    # extreme says is ALIVE may already be dead — the true extreme may have moved
    # past its edge during the unobserved window. For bins whose survival edge lies
    # within (plausible-move-rate x excess staleness) of the stale extreme, the
    # dead/alive state is UNKNOWN: their buy_yes q_lcb is forced to 0.0 (no live
    # submit). q itself is NOT changed (the masked posterior remains the honest
    # point estimate); only the submit-licensing LCB is suppressed. Fail-closed:
    # unparseable obs time or unknown city => maximum margin / conservative budget.
    staleness_uncertain: list[bool] = [False] * len(list(family.candidates))
    if _day0_stale_obs_boundary_guard_enabled():
        from src.signal.day0_obs_latency import (
            stale_extreme_uncertainty_margin,
            staleness_budget_minutes,
        )

        _bins = [candidate.bin for candidate in family.candidates]
        _unit = ""
        for _b in _bins:
            _unit = str(getattr(_b, "unit", "") or "")
            if _unit:
                break
        if not _unit:
            _unit = str(payload.get("settlement_unit") or "F")
        _obs_age_min = _day0_observation_age_minutes(payload, decision_time)
        _budget_min = staleness_budget_minutes(str(getattr(family, "city", "") or payload.get("city") or ""))
        _margin = stale_extreme_uncertainty_margin(
            unit=_unit, obs_age_minutes=_obs_age_min, budget_minutes=_budget_min
        )
        if _margin > 0.0:
            for _index, _bin in enumerate(_bins):
                if mask[_index] <= 0.0:
                    continue  # already dead — kill direction is staleness-safe
                if metric == "high":
                    # Alive bin whose UPPER edge could already have been crossed by
                    # the unseen true running max. Open-high shoulder cannot die.
                    if _bin.high is not None and float(_bin.high) <= rounded + _margin:
                        staleness_uncertain[_index] = True
                else:  # metric == "low" (validated above)
                    if _bin.low is not None and float(_bin.low) >= rounded - _margin:
                        staleness_uncertain[_index] = True
            if any(staleness_uncertain):
                import logging as _logging

                _logging.getLogger("zeus.day0_stale_obs_guard").info(
                    "DAY0_STALE_OBS_BOUNDARY_GUARD city=%s metric=%s rounded=%s obs_age_min=%s "
                    "budget_min=%.1f margin=%.2f%s suppressed_bins=%d/%d",
                    getattr(family, "city", "?"), metric, rounded,
                    "None" if _obs_age_min is None else f"{_obs_age_min:.1f}",
                    _budget_min, _margin, _unit, sum(staleness_uncertain), len(_bins),
                )
    from src.strategy.live_inference.inference_engine import InferenceInputs, evaluate_live_bins

    prior = tuple(max(q_by_condition[str(candidate.condition_id or "")], 1e-9) for candidate in family.candidates)
    live_state = evaluate_live_bins(
        InferenceInputs(
            prior=prior,
            day0_mask=tuple(mask),
            forecast_complete=True,
            orderbook_event=False,
        )
    )
    # K3: keep the typed carrier end-to-end so the masked output a day0 family hands
    # to the candidate consumer (at 3092) is also a provenance-carrying QlcbByDirection.
    from src.calibration.qlcb_provenance import (
        QlcbByDirection,
        _qlcb_float,
        _set_qlcb_provenance,
    )
    masked_q_by_condition: dict[str, float] = {}
    masked_lcb_by_direction: QlcbByDirection = QlcbByDirection()
    for index, candidate in enumerate(family.candidates):
        condition_id = str(candidate.condition_id or "")
        q_value = float(live_state.probabilities[str(index)])
        masked_q_by_condition[condition_id] = q_value
        # BLOCKER #3 fix (day0 critic 2026-05-31): direct dict lookup raised KeyError
        # for any bin-direction with no executable market quote (common in day0 where
        # some bins are illiquid/delisted), propagating as LIVE_INFERENCE_INPUTS_MISSING
        # and killing the ENTIRE family (zero candidates) instead of skipping just the
        # non-executable direction. .get(...,0.0) → that direction gets no fill confidence
        # (min(0.0,·)=0.0 → not acceptable) while bins WITH quotes still proceed.
        yes_lcb = _qlcb_float(lcb_by_condition.get((condition_id, "buy_yes"), 0.0))
        no_lcb = _qlcb_float(lcb_by_condition.get((condition_id, "buy_no"), 0.0))
        # The masked LCB inherits the upstream calibration source; the day0 mask is a
        # downstream transform of the forecast-bootstrap LCB, not a new calibration.
        _set_qlcb_provenance(
            masked_lcb_by_direction,
            (condition_id, "buy_yes"),
            # STALE-OBS BOUNDARY GUARD: a bin whose dead/alive state is unknowable
            # under the current obs staleness gets NO buy_yes submit license.
            0.0 if (mask[index] <= 0.0 or staleness_uncertain[index]) else min(yes_lcb, q_value),
            source="FORECAST_BOOTSTRAP",
        )
        _set_qlcb_provenance(
            masked_lcb_by_direction,
            (condition_id, "buy_no"),
            0.0,
            source="FORECAST_BOOTSTRAP",
        )
    return masked_q_by_condition, masked_lcb_by_direction


def _table_ref_columns(conn: sqlite3.Connection, table_ref: str) -> set[str]:
    if "." in table_ref:
        schema, table = table_ref.split(".", 1)
        return {row[1] for row in conn.execute(f"PRAGMA {schema}.table_info({table})").fetchall()}
    return _table_columns(conn, table_ref)


def _authority_table_ref(conn: sqlite3.Connection, table_name: str) -> str | None:
    try:
        attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
        if "forecasts" in attached:
            exists = conn.execute(
                "SELECT 1 FROM forecasts.sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            if exists is not None:
                return f"forecasts.{table_name}"
        if "world" in attached:
            exists = conn.execute(
                "SELECT 1 FROM world.sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            if exists is not None:
                return f"world.{table_name}"
    except Exception:
        pass
    if _table_exists(conn, table_name):
        return table_name
    return None


def _snapshot_rows_by_condition(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        condition_id = _nonnull(row.get("condition_id"))
        if condition_id and condition_id not in out:
            out[condition_id] = row
    return out


def _snapshot_rows_by_condition_and_direction(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        condition_id = _nonnull(row.get("condition_id"))
        if not condition_id:
            continue
        yes_token_id = _nonnull(row.get("yes_token_id"))
        no_token_id = _nonnull(row.get("no_token_id"))
        selected_token_id = _nonnull(row.get("selected_outcome_token_id"))
        selected_label = _nonnull(row.get("outcome_label")).upper()
        for token_id, label, direction in (
            (yes_token_id, "YES", "buy_yes"),
            (no_token_id, "NO", "buy_no"),
        ):
            if not token_id:
                continue
            if selected_token_id and selected_token_id != token_id:
                continue
            if selected_label and selected_label != label:
                continue
            out.setdefault((condition_id, direction), row)
    return out


def _snapshot_price_stale_reason(row: dict[str, Any], *, decision_time: datetime) -> str | None:
    deadline_raw = row.get("freshness_deadline")
    if deadline_raw in {None, ""}:
        return "EXECUTABLE_SNAPSHOT_STALE:freshness_deadline_missing"
    try:
        deadline = _parse_utc(str(deadline_raw))
    except Exception:
        return "EXECUTABLE_SNAPSHOT_STALE:freshness_deadline_invalid"
    checked_at = decision_time.astimezone(UTC)
    if deadline < checked_at:
        return (
            "EXECUTABLE_SNAPSHOT_STALE:"
            f"freshness_deadline={deadline.isoformat()}:decision_time={checked_at.isoformat()}"
        )
    return None


def _latest_snapshot_rows_for_event_family(
    trade_conn: sqlite3.Connection,
    event: OpportunityEvent,
    *,
    condition_ids: tuple[str, ...],
    fresh_at: datetime | None = None,
    require_fresh: bool = True,
) -> list[dict[str, Any]]:
    """Latest executable snapshot row per family condition_id.

    ``require_fresh`` controls whether the 30s PRICE-freshness window
    (``freshness_deadline``) is applied. The entry/FDR family-completeness gate proves
    MARKET IDENTITY (a snapshot row exists for every MECE sibling), which does not decay
    with price age — once a market is captured it does not "disappear". A full family is
    captured bin-by-bin and can span >30s, so applying the price window here would drop
    early-captured siblings and make large-family decisions structurally impossible. Callers
    proving identity pass ``require_fresh=False``; PRICE-freshness for the actually-traded
    selected bin is enforced at submission (``assert_snapshot_executable``). Operator design
    law 2026-05-30: "freshness 针对价格不针对市场; 市场捕捉了不会突然消失."
    """
    if not _table_exists(trade_conn, "executable_market_snapshots"):
        return []
    columns = _table_columns(trade_conn, "executable_market_snapshots")
    clean_condition_ids = tuple(condition_id for condition_id in condition_ids if condition_id)
    if not clean_condition_ids or "condition_id" not in columns:
        return []
    predicates: list[str] = []
    params: list[object] = []
    if require_fresh:
        predicates.append("freshness_deadline >= ?")
        params.append((fresh_at or datetime.now(UTC)).isoformat())
    if fresh_at is not None and "captured_at" in columns:
        checked_at = fresh_at.astimezone(UTC) if fresh_at.tzinfo is not None and fresh_at.utcoffset() is not None else fresh_at
        predicates.append("captured_at <= ?")
        params.append(checked_at.isoformat())
    placeholders = ",".join("?" for _ in clean_condition_ids)
    predicates.append(f"condition_id IN ({placeholders})")
    params.extend(clean_condition_ids)
    if "active" in columns:
        predicates.append("COALESCE(active, 0) = 1")
    if "closed" in columns:
        predicates.append("COALESCE(closed, 0) = 0")
    cur = trade_conn.execute(
        f"""
        SELECT *
        FROM executable_market_snapshots
        WHERE {' AND '.join(predicates)}
        ORDER BY captured_at DESC, snapshot_id DESC
        """,
        tuple(params),
    )
    names = [description[0] for description in cur.description]
    rows: list[dict[str, Any]] = []
    seen_side: set[tuple[str, str]] = set()
    for row in cur.fetchall():
        item = {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))
        condition_id = str(item.get("condition_id") or "")
        selected_token = str(item.get("selected_outcome_token_id") or "")
        side_key = (condition_id, selected_token)
        if not condition_id or side_key in seen_side:
            continue
        seen_side.add(side_key)
        rows.append(item)
    return rows


def _selected_snapshot_row_for_event(
    rows: list[dict[str, Any]],
    payload: dict[str, object],
) -> dict[str, Any] | None:
    snapshot_id = _nonnull(payload.get("executable_snapshot_id"))
    condition_id = _nonnull(payload.get("condition_id"))
    token_id = _nonnull(payload.get("token_id"))
    for row in rows:
        if snapshot_id and str(row.get("snapshot_id") or "") != snapshot_id:
            continue
        if condition_id and str(row.get("condition_id") or "") != condition_id:
            continue
        if not token_id:
            return row
        if token_id not in {str(row.get("yes_token_id") or ""), str(row.get("no_token_id") or "")}:
            continue
        if _nonnull(row.get("selected_outcome_token_id")) == token_id and not _snapshot_outcome_matches_selected_token(row, token_id):
            continue
        return row
    return None


def _snapshot_token_maps_by_condition(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    token_maps: dict[str, dict[str, str]] = {}
    for row in rows:
        condition_id = _nonnull(row.get("condition_id"))
        yes_token_id = _nonnull(row.get("yes_token_id"))
        no_token_id = _nonnull(row.get("no_token_id"))
        if condition_id and yes_token_id and no_token_id:
            token_maps.setdefault(condition_id, {"yes_token_id": yes_token_id, "no_token_id": no_token_id})
    return token_maps


def _topology_candidate_from_market_event(
    row: dict[str, Any],
    snapshot_token_map: dict[str, str] | None,
    payload: dict[str, object],
) -> MarketTopologyCandidate:
    """Build a topology candidate for one market_events row.

    ``snapshot_token_map`` is None for bins that have no entry in
    ``executable_market_snapshots`` (illiquid tail bins).  Those bins are still
    included in the full-family topology so q/FDR run over the complete MECE
    partition; they are non-tradeable (yes_token_id from market_events, no_token_id=None)
    and the executable_mask downstream will mark them as False.
    """
    city = _nonnull(payload.get("city"))
    target_date = _nonnull(payload.get("target_date"))
    metric = _nonnull(payload.get("metric") or payload.get("temperature_metric"))
    if not (city and target_date and metric):
        raise ValueError("EDLI event payload missing city/target_date/metric")
    if snapshot_token_map is not None:
        # Tradeable bin: use the executable snapshot's token ids
        yes_token_id: str | None = snapshot_token_map["yes_token_id"]
        no_token_id: str | None = snapshot_token_map["no_token_id"]
    else:
        # Non-tradeable bin: use market_events.token_id (YES side only); no executable
        # snapshot exists so no_token_id is absent
        yes_token_id = _nonnull(row.get("token_id")) or None
        no_token_id = None
    return MarketTopologyCandidate(
        city=city,
        target_date=target_date,
        metric=metric,
        condition_id=_nonnull(row.get("condition_id")),
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        bin=_bin_from_market_event(row, payload),
        market_slug=_nonnull(row.get("market_slug") or row.get("event_slug")) or None,
    )


def _settlement_unit_for_payload_city(payload: dict[str, object]) -> str:
    """Authoritative settlement unit for the payload's city.

    The unit is CARRIED from the city settlement contract (``SettlementSemantics`` — the same
    authority p_raw uses), never inferred from the market label or blindly defaulted to 'F'.
    market_events has no unit column, so defaulting a missing payload unit to 'F' silently
    mislabelled every Celsius-city bin and failed closed on EVENT_BOUND_MARKET_TOPOLOGY_INVALID
    ('… is Celsius but unit=F'). Falls back to an explicit payload unit only when the city is
    unknown, then 'F'. The Bin label cross-check remains the fail-closed guard if config and
    market label ever disagree. Data-provenance law (Fitz #4): authority over default.
    """
    city_name = _nonnull(payload.get("city"))
    if city_name:
        try:
            from src.config import runtime_cities_by_name
            from src.contracts.settlement_semantics import SettlementSemantics

            city_obj = runtime_cities_by_name().get(city_name)
            if city_obj is not None:
                return SettlementSemantics.for_city(city_obj).measurement_unit
        except Exception:
            pass
    return _nonnull(payload.get("unit") or payload.get("temperature_unit") or "F")


def _bin_from_market_event(row: dict[str, Any], payload: dict[str, object]) -> Bin:
    label = _nonnull(row.get("range_label") or row.get("outcome") or payload.get("bin_label") or payload.get("outcome_label"))
    low = row.get("range_low")
    high = row.get("range_high")
    unit = _settlement_unit_for_payload_city(payload)
    if isinstance(low, (int, float)) or isinstance(high, (int, float)):
        return Bin(
            low=float(low) if isinstance(low, (int, float)) else None,
            high=float(high) if isinstance(high, (int, float)) else None,
            unit=unit,
            label=label,
        )
    raise ValueError("market topology bin range missing")


def _bin_from_payload(payload: dict[str, object]) -> Bin:
    label = _nonnull(payload.get("bin_label") or payload.get("outcome_label"))
    low = payload.get("bin_low")
    high = payload.get("bin_high")
    unit = _nonnull(payload.get("unit") or payload.get("temperature_unit") or "F")
    if isinstance(low, (int, float)) or isinstance(high, (int, float)):
        return Bin(
            low=float(low) if isinstance(low, (int, float)) else None,
            high=float(high) if isinstance(high, (int, float)) else None,
            unit=unit,
            label=label,
        )
    return Bin(low=0, high=1, unit="F", label=label or "0-1°F")


def _snapshot_outcome_matches_selected_token(row: dict[str, Any], selected_token_id: str) -> bool:
    selected_label = "YES" if selected_token_id == str(row.get("yes_token_id") or "") else "NO"
    outcome_label = _nonnull(row.get("outcome_label")).upper()
    return not outcome_label or outcome_label == selected_label


def _execution_price_from_snapshot(
    row: dict[str, Any],
    *,
    selected_token_id: str,
    direction: str,
) -> tuple[ExecutionPrice, float, float]:
    # ZEUS-NOBYPASS-1 fail-closed guard (re-added; orig 4f7d963606). Strictly
    # more restrictive: only block when tradeability_status_json.executable_allowed
    # is EXPLICITLY False. Absent/None/True -> byte-identical to pre-guard behavior
    # (do not block snapshots that lack the field). A non-executable substrate row
    # can never actually fill (submit-time assert_snapshot_executable already
    # fail-closes); this removes only the phantom tradeable candidate from the
    # proof/receipt/opportunity-book layer. Raising ValueError routes to the
    # caller's EXECUTABLE_NATIVE_ASK_MISSING path (execution_price=None,
    # native_quote_available=False) carrying the substrate reason.
    tradeability_status = _json_object(row.get("tradeability_status_json") or row.get("tradeability_status") or {})
    if tradeability_status.get("executable_allowed") is False:
        reason = _nonnull(tradeability_status.get("reason") or "not_executable")
        raise ValueError(f"EDLI executable snapshot marked non-executable: {reason}")
    if selected_token_id not in {str(row.get("yes_token_id") or ""), str(row.get("no_token_id") or "")}:
        raise ValueError("EDLI executable snapshot selected token mismatch")
    if _nonnull(row.get("selected_outcome_token_id")) == selected_token_id and not _snapshot_outcome_matches_selected_token(row, selected_token_id):
        raise ValueError("EDLI executable snapshot outcome label mismatch")

    # S1 (bin selection.md §5.3 cost-curve Kelly, §5.4 fees/slippage/depth,
    # §9 Hidden #6 "scalar VWMP hides the convex cost curve", §4 executable-space
    # separation, operator directive 2026-06-08): the native side is priced by
    # its OWN ExecutableCostCurve — the depth-walked, fee-adjusted convex curve —
    # NOT a single scalar VWMP at min_order_size shares. The curve is built from
    # the SAME executable snapshot row's native ask ladder (yes_asks for buy_yes,
    # no_asks for buy_no), side-tagged so a YES curve can never price a NO side
    # (the contract raises on a curve_side mismatch). avg_cost(stake) emits the
    # typed ExecutionPrice cost-of-entry at the chosen stake — the single live
    # pricing object on this path. The scalar executable_cost VWMP kernel is no
    # longer the pricing authority here.
    side = _native_curve_side_for_direction(direction)
    if side is None:
        raise ValueError(f"unsupported direction for native cost curve: {direction}")

    # The depth-coverage fill-LCB still walks the native quote book; we build it
    # once and reuse its ladder for the curve so both read the SAME row depth.
    book = _native_quote_book_from_snapshot_row(row)
    shares = book.min_order_size
    curve = _native_side_cost_curve_from_snapshot_row(
        row, side=side, token_id=selected_token_id, book=book
    )

    # The cost-of-entry on the convex curve at the venue min-order QUANTITY (the
    # smallest executable taker order, in SHARES — §13). We price by exact share
    # count, NOT by converting min_order_size shares to a USD stake at the top
    # price: that conversion underfills whenever the top ask level's depth is
    # below min_order_size shares (the USD budget computed at the cheap top price
    # buys fewer than min_order_size shares once the walk crosses into costlier
    # deeper levels), which would FALSE-no-trade a side the depth-walk in fact
    # fills (and which the legacy share-parameterized VWMP kernel priced fine).
    # avg_cost_for_shares walks SHARES directly (spec §5.3/§5.4), so the share/USD
    # round-trip — and its loss — never happens; this is byte-identical to the
    # legacy kernel's all-in result for ALL books, not only single-level ones.
    # It raises (depth-exhausted / off-grid / empty / below-min-order) exactly
    # where the §13 no-trade gates require fail-closed — the caller routes a
    # ValueError to the EXECUTABLE_NATIVE_ASK_MISSING / NATIVE_QUOTE_MISSING
    # no-trade path. avg_cost(stake_usd) remains for the future §5.3 USD-stake ELG
    # optimizer; this path asks the share-parameterized question.
    execution_price = curve.avg_cost_for_shares(shares)

    p_fill_lcb = _p_fill_lcb_for_direction(book, direction=direction, shares=shares)
    c_cost_95pct = min(0.999999, execution_price.value + float(book.min_tick_size))
    return execution_price, p_fill_lcb, c_cost_95pct


def _native_curve_side_for_direction(direction: str) -> str | None:
    """Map a BUY direction to the native executable side the curve prices.

    buy_yes -> "YES" (walks yes_asks); buy_no -> "NO" (walks no_asks). Sell
    directions and anything else return None: the cost curve prices a BUY only
    (spec §5.4 "walk asks for BUY"), and the candidate proof path only ever
    prices buy_yes / buy_no.
    """
    if direction == "buy_yes":
        return "YES"
    if direction == "buy_no":
        return "NO"
    return None


def _native_side_cost_curve_from_snapshot_row(
    row: dict[str, Any],
    *,
    side: str,
    token_id: str,
    book: Any | None = None,
) -> "ExecutableCostCurve":
    """Build the native side's ExecutableCostCurve from a snapshot row (S1).

    Spec §14.3 + §5.3/§5.4 + §4 + Hidden #6/#16. The curve is constructed from
    the SAME executable snapshot row's native ask ladder as the rest of the proof
    path: ``yes_asks`` for ``side=="YES"``, ``no_asks`` for ``side=="NO"``. The
    curve carries ``side`` so the bin-selection contract makes pricing a NO side
    from the YES book UNCONSTRUCTABLE (it raises on a curve_side mismatch).

    Fail-closed (§13): an empty native ask ladder, an off-min-tick-grid price, or
    an invalid tick/min-order raises ValueError, which the proof path routes to
    NATIVE_QUOTE_MISSING (execution_price=None, native_quote_available=False) —
    never a fabricated price.

    ``book`` may be passed to reuse an already-built NativeQuoteBook (the proof
    path builds it once for the fill-LCB); otherwise it is built here.
    """
    if side not in ("YES", "NO"):
        raise ValueError(f"native cost curve side must be 'YES' or 'NO', got {side!r}")
    if book is None:
        book = _native_quote_book_from_snapshot_row(row)

    asks = book.yes_asks if side == "YES" else book.no_asks
    if not asks:
        # §13 no-trade gate: a BUY side with no native ask depth is not tradeable.
        # Surface the missing-quote condition rather than fabricate a price.
        raise ValueError(
            f"native {side} ask ladder is empty on token {token_id!r}; "
            "fail closed (NATIVE_QUOTE_MISSING) rather than fabricate a price"
        )

    # ExecutableCostCurve owns its own grid/range validation (each BookLevel must
    # be in (0, 1); every level must lie on min_tick; Hidden #16). We map the
    # native QuoteLevel ladder onto BookLevel and let the contract enforce.
    levels = tuple(BookLevel(price=lvl.price, size=lvl.size) for lvl in asks)
    fee_model = FeeModel(fee_rate=Decimal(str(book.fee_rate)))
    return ExecutableCostCurve(
        token_id=str(token_id),
        side=side,  # type: ignore[arg-type]
        snapshot_id=_nonnull(row.get("snapshot_id")),
        book_hash=_nonnull(
            row.get("book_hash")
            or row.get("executable_book_hash")
            or row.get("snapshot_hash")
            or row.get("raw_orderbook_hash")
        ),
        levels=levels,
        fee_model=fee_model,
        min_tick=book.min_tick_size,
        min_order_size=book.min_order_size,
        quote_ttl=_native_quote_ttl_from_row(row),
    )


def _native_quote_ttl_from_row(row: dict[str, Any]) -> "timedelta":
    """Resolve a positive quote TTL for the curve from the snapshot row.

    ExecutableCostCurve requires a strictly-positive ``quote_ttl`` (it carries the
    freshness budget for the cache / submit-recapture layer; it does NOT itself
    enforce expiry — that is the runtime's job at recapture). The proof-layer
    pricing on this path does not gate on TTL (price TTL must not shrink the
    family selector; the selected leg is re-authorized against a JIT book at
    submit). We derive a positive budget from freshness_deadline - captured_at
    when both are present, else fall back to a small positive default.
    """
    from datetime import timedelta as _td

    captured = _parse_iso_optional(row.get("captured_at"))
    deadline = _parse_iso_optional(row.get("freshness_deadline"))
    if captured is not None and deadline is not None:
        delta = deadline - captured
        if delta > _td(0):
            return delta
    return _td(seconds=1)


def _parse_iso_optional(value: object) -> "datetime | None":
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _native_quote_book_from_snapshot_row(row: dict[str, Any]):
    from src.contracts.executable_market_snapshot import fee_rate_fraction_from_details
    from src.strategy.live_inference.executable_cost import NativeQuoteBook, QuoteLevel

    min_tick_size = Decimal(str(row.get("min_tick_size") or row.get("tick_size") or "0.01"))
    min_order_size = Decimal(str(row.get("min_order_size") or "1"))
    fee_details = _json_object(row.get("fee_details_json") or row.get("fee_details") or {})
    fee_rate = fee_rate_fraction_from_details(fee_details)
    neg_risk = bool(_optional_bool(row.get("neg_risk")) or False)
    depth = _json_object(row.get("orderbook_depth_json") or row.get("orderbook_depth_jsonb") or {})
    yes_token_id = str(row.get("yes_token_id") or "")
    no_token_id = str(row.get("no_token_id") or "")
    yes_depth = _depth_for_token_or_label(depth, token_id=yes_token_id, label="YES")
    no_depth = _depth_for_token_or_label(depth, token_id=no_token_id, label="NO")
    if yes_depth is None:
        yes_depth = _explicit_depth_for_selected_token(row, token_id=yes_token_id, min_order_size=min_order_size)
    if no_depth is None:
        no_depth = _explicit_depth_for_selected_token(row, token_id=no_token_id, min_order_size=min_order_size)
    yes_depth = yes_depth or {}
    no_depth = no_depth or {}
    return NativeQuoteBook(
        yes_asks=_parse_quote_levels(yes_depth.get("asks", ())),
        no_asks=_parse_quote_levels(no_depth.get("asks", ())),
        yes_bids=_parse_quote_levels(yes_depth.get("bids", ())),
        no_bids=_parse_quote_levels(no_depth.get("bids", ())),
        min_tick_size=min_tick_size,
        min_order_size=min_order_size,
        fee_rate=fee_rate,
        neg_risk=neg_risk,
    )


def _parse_quote_levels(raw_levels: object):
    from src.strategy.live_inference.executable_cost import QuoteLevel

    levels = []
    if not isinstance(raw_levels, (list, tuple)):
        return tuple()
    for raw in raw_levels:
        if isinstance(raw, dict):
            price = raw.get("price")
            size = raw.get("size")
        else:
            try:
                price, size = raw
            except (TypeError, ValueError):
                continue
        if price in {None, ""} or size in {None, ""}:
            continue
        levels.append(QuoteLevel(Decimal(str(price)), Decimal(str(size))))
    return tuple(levels)


def _depth_for_token_or_label(depth: object, *, token_id: str, label: str) -> dict[str, object] | None:
    if not isinstance(depth, dict):
        return None
    for key in (token_id, label, label.lower()):
        value = depth.get(key)
        if isinstance(value, dict):
            return value
    # SINGLE-TOKEN CLOB FORMAT (2026-06-09 depth-JSON fix). The materializer stores a
    # SINGLE token's raw CLOB /book response directly as orderbook_depth_json:
    # ``{"asks": [...], "asset_id": "<token>", "bids": [...], ...}`` — the asks/bids are
    # at the TOP LEVEL (not nested under tokens/outcomes/books, and the token is the
    # ``asset_id`` field, not a dict key). Pre-fix this format matched NOTHING, so the
    # curve degraded to the 1-level _explicit_depth_for_selected_token fallback (top_ask
    # + depth_at_best_ask only) instead of the 30–80 level book actually present. Now we
    # recognize it and return the full book — CANONICALLY SORTED (asks ascending /
    # cheapest first, bids descending / highest first) so the depth-walk consumes the
    # BEST level first. We sort by price rather than trusting array position because the
    # codebase's own CLOB consumers (_top_book_level_decimal) take min/max over all rows
    # rather than rely on order — CLOB-native arrays are best-price-LAST, so a raw pass
    # would walk the WORST level first and corrupt the cost curve. Sorting makes the
    # ordering-bug category impossible regardless of source order.
    if str(depth.get("asset_id") or depth.get("token_id") or "") == token_id and (
        isinstance(depth.get("asks"), list) or isinstance(depth.get("bids"), list)
    ):
        return _canonicalize_single_token_book(depth)
    for key in ("tokens", "outcomes", "books"):
        value = depth.get(key)
        if isinstance(value, dict):
            nested = _depth_for_token_or_label(value, token_id=token_id, label=label)
            if nested is not None:
                return nested
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                if str(item.get("asset_id") or item.get("token_id") or "") == token_id:
                    return item
                if str(item.get("outcome") or item.get("outcome_label") or "").upper() == label:
                    return item
    return None


def _canonicalize_single_token_book(book: dict) -> dict[str, object]:
    """Normalize a raw single-token CLOB book to best-price-FIRST asks/bids.

    The depth-walk (_book_walk_average / ExecutableCostCurve) consumes levels in order
    and assumes ``levels[0]`` is the best (cheapest ask / highest bid). Raw CLOB arrays
    are best-price-LAST, so we sort: asks ASCENDING (cheapest first), bids DESCENDING
    (highest first) — identical to the projector's normalization (project_rest_snapshot).
    Rows that cannot be parsed to a numeric price are dropped (fail-safe). Returns the
    ``{"asks": [...], "bids": [...]}`` shape the NativeQuoteBook builder expects.
    """
    def _price(row: object) -> Decimal | None:
        if isinstance(row, dict):
            raw = row.get("price")
        else:
            try:
                raw, _ = row  # type: ignore[misc]
            except (TypeError, ValueError):
                return None
        if raw in (None, ""):
            return None
        try:
            return Decimal(str(raw))
        except (ArithmeticError, ValueError, TypeError):
            return None

    def _sorted(side: object, *, ascending: bool) -> list[object]:
        if not isinstance(side, list):
            return []
        keyed = [(p, row) for row in side if (p := _price(row)) is not None]
        keyed.sort(key=lambda pr: pr[0], reverse=not ascending)
        return [row for _, row in keyed]

    return {
        "asks": _sorted(book.get("asks"), ascending=True),
        "bids": _sorted(book.get("bids"), ascending=False),
    }


def _explicit_depth_for_selected_token(
    row: dict[str, Any],
    *,
    token_id: str,
    min_order_size: Decimal,
) -> dict[str, object] | None:
    if _nonnull(row.get("selected_outcome_token_id")) != token_id:
        return None
    ask_price = row.get("orderbook_top_ask")
    bid_price = row.get("orderbook_top_bid")
    ask_size = _decimal_from_optional(
        row.get("depth_at_best_ask")
        or row.get("orderbook_top_ask_size")
        or row.get("best_ask_size")
    )
    bid_size = _decimal_from_optional(
        row.get("depth_at_best_bid")
        or row.get("orderbook_top_bid_size")
        or row.get("best_bid_size")
    )
    asks = _explicit_level(ask_price, ask_size, min_order_size=min_order_size)
    bids = _explicit_level(bid_price, bid_size, min_order_size=min_order_size)
    if not asks and not bids:
        return None
    return {"asks": asks, "bids": bids}


def _explicit_level(price: object, size: Decimal | None, *, min_order_size: Decimal) -> list[dict[str, str]]:
    if price in {None, "", "ABSENT"} or size is None or size < min_order_size:
        return []
    return [{"price": str(price), "size": str(size)}]


def _decimal_from_optional(value: object) -> Decimal | None:
    if value in {None, "", "ABSENT"}:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _p_fill_lcb_for_direction(book, *, direction: str, shares: Decimal) -> float:
    """Lower-confidence bound on the fill probability of a sized-to-depth taker order.

    The order we actually submit is sized to ``shares`` (= book.min_order_size at
    the call site, then capped to crossable depth by the executor). Its fill
    probability against the *visible* book is governed by whether the crossing
    levels hold at least ``shares`` units, NOT by a blanket floor. The previous
    implementation hard-returned the config floor (0.05) for every candidate with
    *any* qualifying depth, which pessimized a min-size order fully covered by a
    100-deep best level down to p_fill=0.05 and crushed every 1-20% robust-positive
    edge at trade_score = p_fill x edge - penalty (TRADE_SCORE_NON_POSITIVE).

    The correct quantity is the depth-coverage of the sized order. We walk the
    crossing levels to the depth the order can actually consume, form the coverage
    cushion (available_depth / sized_size), and return a conservative Wilson-style
    lower bound on that cushion. Properties (all honest, none inflated):

      * available_depth >= sized_size (book fully covers the order)  -> LCB -> ~1.0
        and rises toward 1 as the depth cushion grows (more "evidence").
      * available_depth <  sized_size (genuinely thin book)          -> LCB stays
        LOW and the candidate is correctly penalized.
      * available_depth == 0 / unknown (no crossable book)           -> 0.0
        (fail-closed; the candidate has no executable quote at all).

    The configured ``no_submit_visible_depth_fill_lcb`` value remains the FLOOR
    when depth is present-but-only-exactly-covering, so a candidate is never
    scored *below* the historical conservative floor purely because of the new
    bound. It is never used as the default for a candidate with a real, deep
    crossable book.
    """
    levels = {
        "buy_yes": book.yes_asks,
        "buy_no": book.no_asks,
        "sell_yes": book.yes_bids,
        "sell_no": book.no_bids,
    }[direction]
    sized = float(shares)
    if sized <= 0.0:
        return 0.0
    available = float(sum((level.size for level in levels), Decimal("0")))
    if available <= 0.0:
        # No crossable visible book on this side -> no executable quote.
        return 0.0
    if available < sized:
        # Genuinely thin: the visible book cannot cover the sized order.
        # Honest, low (sub-floor) fill probability — correctly penalized.
        coverage = available / sized
        return max(0.0, min(1.0, _wilson_depth_fill_lcb(coverage=coverage, depth_cushion=coverage)))
    # Book fully covers the sized order. The fill-probability lower bound is the
    # Wilson LCB on a fully-covered crossing (p_hat = 1.0) with the depth cushion
    # (available / sized) as the evidence count: deeper books -> tighter LCB -> ~1.0.
    depth_cushion = available / sized
    floor = max(0.0, min(1.0, float(settings["edli_v1"].get("no_submit_visible_depth_fill_lcb", 0.05))))
    return max(floor, min(1.0, _wilson_depth_fill_lcb(coverage=1.0, depth_cushion=depth_cushion)))


def _wilson_depth_fill_lcb(*, coverage: float, depth_cushion: float) -> float:
    """Conservative Wilson lower-confidence bound on a depth-coverage proportion.

    ``coverage`` is the observed fill proportion of the sized order against the
    visible crossing depth (= 1.0 when the book holds >= the sized size). The
    ``depth_cushion`` (available_depth / sized_size, >= 0) is treated as the
    binomial evidence count ``n``: a thicker book is stronger evidence that a
    min-size taker order fills, so the lower bound tightens toward ``coverage``.

    A Wilson interval is used (not Wald) because it is well-behaved at the
    p_hat -> 1 boundary, returning a value strictly < 1.0 for any finite cushion
    (honest: the visible book is feasibility evidence, never a fill guarantee) and
    degrading smoothly as the cushion shrinks.
    """
    p_hat = max(0.0, min(1.0, coverage))
    n = max(0.0, depth_cushion)
    if n <= 0.0:
        return 0.0
    z = float(settings["edli_v1"].get("no_submit_visible_depth_fill_z", 1.645))
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p_hat + z2 / (2.0 * n)
    margin = z * float(np.sqrt(((p_hat - (p_hat * p_hat)) / n) + (z2 / (4.0 * n * n))))
    lower = (center - margin) / denom
    return max(0.0, min(1.0, lower))


def _robust_trade_score_from_generated_inputs(
    *,
    q_posterior: float,
    q_lcb_5pct: float,
    execution_price: ExecutionPrice | None,
    c_cost_95pct: float | None,
    p_fill_lcb: float,
) -> float:
    if execution_price is None or c_cost_95pct is None:
        return 0.0
    from src.strategy.live_inference.trade_score import robust_trade_score

    receipt = robust_trade_score(
        trade_score_id="edli_generated_trade_score",
        q_posterior=q_posterior,
        q_5pct=q_lcb_5pct,
        c_95pct=ExecutionPrice(c_cost_95pct, "ask", fee_deducted=True, currency="probability_units"),
        c_stress=ExecutionPrice(c_cost_95pct, "ask", fee_deducted=True, currency="probability_units"),
        p_fill_lcb=p_fill_lcb,
        penalty=0.01,
        stress_penalty=0.01,
    )
    return float(receipt.score)


def _bankroll_usd_from_provider(provider: Callable[[], float | None]) -> float:
    value = provider()
    if value is None:
        raise ValueError("bankroll_provider_unavailable")
    bankroll_usd = float(value)
    if bankroll_usd <= 0:
        raise ValueError("bankroll_provider_nonpositive")
    return bankroll_usd


def _runtime_bankroll_usd(*, cached_only: bool = False) -> float:
    from src.runtime import bankroll_provider

    bankroll = (
        bankroll_provider.cached()
        if cached_only and hasattr(bankroll_provider, "cached")
        else bankroll_provider.current()
    )
    if bankroll is None:
        # No-submit/cached path must NEVER live-fetch the wallet (contract:
        # tests/engine/test_event_reactor_no_bypass.py::
        # test_no_submit_default_bankroll_path_does_not_live_fetch_wallet). A cold cache fails
        # CLOSED → KELLY_PROOF_MISSING. Reliability is the cycle-warm's responsibility:
        # _edli_event_reactor_cycle calls bankroll_provider.current() once per reactor cycle to
        # populate cached(); the prior self-heal that called current() here re-introduced a
        # per-decision live wallet fetch and is removed (#45).
        raise ValueError("bankroll_provider_unavailable")
    if bankroll.authority != "canonical" or bankroll.source != "polymarket_wallet":
        raise ValueError("bankroll_provider_not_canonical")
    # NEW-ENTRY sizing base (dual-bankroll, 2026-06-09 P1). Preference order:
    #   1. spendable_cash_usd — free BUY collateral (architect memo §2: live
    #      entry sizing uses spendable cash so open positions never inflate the
    #      next order's cap). This is already phantom-free (free cash only).
    #   2. equity_for_new_entry_sizing_usd — conservative equity that EXCLUDES
    #      the blip_held phantom. Used only when spendable_cash is unavailable.
    #   3. value_usd — LAST resort. Under blip_held this HOLDS a phantom position
    #      value (defends the loss threshold) and MUST NOT seed Kelly; it is the
    #      final fallback only for legacy/degraded records that carry neither
    #      conservative field.
    spendable_cash = getattr(bankroll, "spendable_cash_usd", None)
    sizing_equity = getattr(bankroll, "equity_for_new_entry_sizing_usd", None)
    if spendable_cash is not None:
        bankroll_usd = float(spendable_cash)
    elif sizing_equity is not None:
        bankroll_usd = float(sizing_equity)
    else:
        bankroll_usd = float(bankroll.value_usd)
    if bankroll_usd <= 0:
        raise ValueError("bankroll_provider_nonpositive")
    return bankroll_usd


def _runtime_kelly_multiplier() -> float:
    from src.config import settings

    value = float(settings["sizing"]["kelly_multiplier"])
    if value <= 0:
        raise ValueError("kelly_multiplier_nonpositive")
    return value


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_or_default(value: object, default: float) -> float:
    parsed = _optional_float(value)
    return default if parsed is None else parsed


def _optional_int(value: object) -> int | None:
    # H2_E2E: coerce posterior_id (emitted as a string by the authority builder)
    # to int for the typed column / FK. None on absent/blank/unparseable so the
    # canonical path leaves the column NULL (observability only — never gates).
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _required_bound_tick_size(snap_for_depth, executable_snapshot_payload) -> str:
    """Resolve the intent tick_size bound to the executor's hydration target.

    BUG #92 antibody: the tick MUST be the min_tick_size of the snapshot the
    executor re-hydrates (proof.executable_snapshot_id).  Both candidate sources
    below are bound to that single snapshot:

      1. ``snap_for_depth`` is ``get_snapshot(proof.executable_snapshot_id)``
         (TAKER + trade_conn path); its ``min_tick_size`` is the canonical Decimal.
      2. ``executable_snapshot_payload['min_tick_size']`` is populated at the
         evidence-build site from ``_hydrated_snapshot =
         get_snapshot(proof.executable_snapshot_id)`` — the same id.

    There is NO hardcoded default: a tick that is not the bound snapshot's tick
    is exactly the two-snapshot divergence that produced the live pre-arm wall.
    If neither source yields a tick, fail closed (provenance fault) rather than
    silently substituting a fixed 0.01 that the executor's snapshot will reject.
    """
    if snap_for_depth is not None and getattr(snap_for_depth, "min_tick_size", None) is not None:
        return str(snap_for_depth.min_tick_size)
    payload_tick = executable_snapshot_payload.get("min_tick_size")
    if payload_tick is None or str(payload_tick).strip() == "":
        raise ValueError(
            "BUG#92_TICK_UNBOUND: executable_snapshot evidence carries no "
            "min_tick_size and no depth snapshot was hydrated; cannot bind "
            "intent tick_size to the executor's snapshot without a silent "
            "default — fail closed."
        )
    # Already a canonical Decimal string from the evidence builder; normalise.
    return str(Decimal(str(payload_tick)))


def _opening_inertia_market_age_hours(
    *,
    snapshot_row: dict[str, Any],
    topology_rows: list[dict[str, Any]],
    family_rows: list[dict[str, Any]],
    decision_time: datetime,
) -> float | None:
    """Return market age in hours, or None if age cannot be determined.

    Priority: snapshot market_start_at → topology created_at (Gamma createdAt) →
    earliest family captured_at (proxy: first time we saw the market).
    Returns None (conservative pass) when no usable timestamp found.
    """
    # 1) market_start_at from the selected snapshot row
    raw = snapshot_row.get("market_start_at")
    market_open = _parse_utc(raw) if isinstance(raw, str) else None
    # 2) created_at from the first topology row (Gamma createdAt, most authoritative)
    if market_open is None and topology_rows:
        for trow in topology_rows:
            market_open = _parse_utc(trow.get("created_at"))
            if market_open is not None:
                break
    # 3) earliest captured_at across all family rows (proxy lower-bound on open time)
    if market_open is None and family_rows:
        earliest = None
        for frow in family_rows:
            ts = _parse_utc(frow.get("captured_at"))
            if ts is not None and (earliest is None or ts < earliest):
                earliest = ts
        market_open = earliest
    if market_open is None:
        return None  # conservative: cannot determine age → allow through
    decision_utc = decision_time.astimezone(UTC)
    delta_seconds = (decision_utc - market_open).total_seconds()
    return delta_seconds / 3600.0


def _optional_bool(value: object) -> bool | None:
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


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _hash_jsonish(value: object) -> str | None:
    if value is None or value == "":
        return None
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = value
    return stable_hash(parsed)


def _native_costs_by_candidate_direction(
    family: Any,
    snapshot_rows: list[dict[str, Any]],
) -> dict[tuple[str, str], tuple[dict[str, Any] | None, "ExecutionPrice | None", float, float | None, str | None]]:
    """Return cost tuple per (condition_id, direction) for all candidates × buy directions.

    Value tuple: (quote_book_dict, execution_price, max_size_at_price, slippage_bps, source_kind)
    Only index [1] (ExecutionPrice) is consumed by downstream callers.
    """
    rows_by_direction = _snapshot_rows_by_condition_and_direction(snapshot_rows)
    result: dict[tuple[str, str], tuple[dict[str, Any] | None, Any, float, float | None, str | None]] = {}
    for candidate in family.candidates:
        condition_id = str(candidate.condition_id or "")
        if not condition_id:
            continue
        for token_id, direction in (
            (str(candidate.yes_token_id or ""), "buy_yes"),
            (str(candidate.no_token_id or ""), "buy_no"),
        ):
            row = rows_by_direction.get((condition_id, direction))
            source_kind = _native_cost_source_for_direction(direction)
            if row is None or not token_id:
                result[(condition_id, direction)] = (None, None, 0.0, None, source_kind)
                continue
            try:
                execution_price, _p_fill, _c95 = _execution_price_from_snapshot(
                    row, selected_token_id=token_id, direction=direction
                )
                book = _native_quote_book_from_snapshot_row(row)
                max_size = float(book.min_order_size)
            except Exception:
                result[(condition_id, direction)] = (None, None, 0.0, None, source_kind)
                continue
            result[(condition_id, direction)] = (None, execution_price, max_size, None, source_kind)
    return result


def _native_side_for_direction(direction: str | None) -> str | None:
    if direction == "buy_yes":
        return "YES_ASK"
    if direction == "buy_no":
        return "NO_ASK"
    if direction == "sell_yes":
        return "YES_BID"
    if direction == "sell_no":
        return "NO_BID"
    return None


def _native_cost_source_for_direction(direction: str | None) -> str | None:
    if direction in {"buy_yes", "buy_no"}:
        return "native_orderbook_ask"
    if direction in {"sell_yes", "sell_no"}:
        return "native_orderbook_bid"
    return None


def _calibration_model_row(conn: sqlite3.Connection, *, model_key: object) -> dict[str, Any] | None:
    if not model_key or not _table_exists(conn, "platt_models"):
        return None
    cur = conn.execute("SELECT * FROM platt_models WHERE model_key = ? LIMIT 1", (str(model_key),))
    row = cur.fetchone()
    if row is None:
        return None
    names = [description[0] for description in cur.description]
    return {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))


def _evidence_clock_from_row(row: dict[str, Any], *, fallback: datetime) -> EvidenceClock:
    source_time = (
        _parse_utc(row.get("book_timestamp"))
        or _parse_utc(row.get("captured_at"))
        or _parse_utc(row.get("source_available_at"))
        or fallback
    )
    agent_time = (
        _parse_utc(row.get("received_at"))
        or _parse_utc(row.get("agent_received_at"))
        or _parse_utc(row.get("captured_at"))
        or source_time
    )
    persisted_time = (
        _parse_utc(row.get("persisted_at"))
        or _parse_utc(row.get("created_at"))
        or _parse_utc(row.get("inserted_at"))
        or agent_time
    )
    return EvidenceClock(source_time, agent_time, persisted_time)


def _evidence_clock_from_rows(rows: list[dict[str, Any]]) -> EvidenceClock:
    if not rows:
        raise ValueError("TOPOLOGY_CLOCK_MISSING")
    clocks = [_evidence_clock_from_topology_row(row) for row in rows]
    return EvidenceClock(
        source_available_at=max(clock.source_available_at for clock in clocks),
        agent_received_at=max(clock.agent_received_at for clock in clocks),
        persisted_at=max(clock.persisted_at for clock in clocks),
    )


def _evidence_clock_from_topology_row(row: dict[str, Any]) -> EvidenceClock:
    source_time = _first_parseable_utc(
        row,
        ("discovered_at", "captured_at", "available_at", "gamma_updated_at", "created_at"),
    )
    agent_time = _first_parseable_utc(
        row,
        ("received_at", "scanned_at", "captured_at", "created_at"),
    )
    persisted_time = _first_parseable_utc(
        row,
        ("persisted_at", "updated_at", "created_at"),
    )
    if source_time is None or agent_time is None or persisted_time is None:
        raise ValueError("TOPOLOGY_CLOCK_MISSING")
    return EvidenceClock(source_time, agent_time, persisted_time)


def _first_parseable_utc(row: dict[str, Any], keys: tuple[str, ...]) -> datetime | None:
    for key in keys:
        if row.get(key) in (None, ""):
            continue
        parsed = _parse_utc(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _read_executable_forecast_bundle_result(
    conn: sqlite3.Connection,
    *,
    snapshot: dict[str, Any],
    source_run: dict[str, Any],
    coverage: dict[str, Any],
    event: OpportunityEvent,
    family,
    decision_time: datetime,
):
    from src.data.executable_forecast_reader import SOURCE_TRANSPORT, read_executable_forecast

    target_date = date.fromisoformat(str(coverage.get("target_local_date") or family.target_date))
    source_id = _nonnull(coverage.get("source_id") or source_run.get("source_id") or snapshot.get("source_id"))
    source_transport = _nonnull(coverage.get("source_transport") or snapshot.get("source_transport") or SOURCE_TRANSPORT)
    data_version = _nonnull(coverage.get("data_version") or snapshot.get("data_version"))
    source_run_id = _nonnull(source_run.get("source_run_id") or snapshot.get("source_run_id"))
    track = _nonnull(coverage.get("track") or source_run.get("track") or snapshot.get("track") or _payload(event).get("track"))
    condition_id = _nonnull(_payload(event).get("condition_id") or (family.condition_ids[0] if family.condition_ids else ""))
    if (
        not source_id
        or not source_transport
        or not data_version
        or not source_run_id
        or not track
        or not condition_id
    ):
        raise ValueError("FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:scope_incomplete")
    return read_executable_forecast(
        conn,
        city_id=str(coverage.get("city_id") or family.city),
        city_name=str(coverage.get("city") or family.city),
        city_timezone=str(coverage.get("city_timezone") or "UTC"),
        target_local_date=target_date,
        temperature_metric=family.metric,
        source_id=source_id,
        source_transport=source_transport,
        data_version=data_version,
        track=track,
        strategy_key="entry_forecast",
        market_family=family.family_id,
        condition_id=condition_id,
        decision_time=decision_time,
        require_entry_readiness=False,
    )


def _forecast_snapshot_reader_block_reason(
    conn: sqlite3.Connection,
    *,
    snapshot: dict[str, Any],
    event: OpportunityEvent,
    family,
    allow_latest: bool,
    decision_time: datetime,
) -> tuple[str | None, str | None]:
    """Return ``(reason, elected_snapshot_id)`` — see _executable_forecast_reader_authority_block_reason."""
    if event.event_type not in {"FORECAST_SNAPSHOT_READY", "DAY0_EXTREME_UPDATED"}:
        return None, None
    source_run_id = _nonnull(snapshot.get("source_run_id") or _payload(event).get("source_run_id"))
    if not source_run_id:
        return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:source_run_id_missing", None
    source_run_table = _authority_table_ref(conn, "source_run")
    coverage_table = _authority_table_ref(conn, "source_run_coverage")
    if source_run_table is None or coverage_table is None:
        return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:source_run_authority_missing", None
    source_run = _row_by_id(conn, source_run_table, "source_run_id", source_run_id)
    if source_run is None:
        return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:source_run_missing", None
    coverage = _coverage_row_for_snapshot(
        conn,
        coverage_table,
        source_run_id=source_run_id,
        family=family,
        snapshot=snapshot,
    )
    if coverage is None:
        return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:coverage_missing", None
    return _executable_forecast_reader_authority_block_reason(
        conn,
        snapshot=snapshot,
        source_run=source_run,
        coverage=coverage,
        event=event,
        family=family,
        allow_latest=allow_latest,
        decision_time=decision_time,
    )


def _executable_forecast_reader_authority_block_reason(
    conn: sqlite3.Connection,
    *,
    snapshot: dict[str, Any],
    source_run: dict[str, Any],
    coverage: dict[str, Any],
    event: OpportunityEvent,
    family,
    allow_latest: bool,
    decision_time: datetime,
) -> tuple[str | None, str | None]:
    """Revalidate forecast eligibility through the canonical executable reader.

    Returns ``(reason, elected_snapshot_id)``. On success ``reason`` is None and
    ``elected_snapshot_id`` is the snapshot the canonical reader ELECTS as the
    executable forecast for this scope — which may differ from the event's causal
    (trigger) snapshot when the causal cycle's source_run is still re-ingesting
    members (captured_at advances past the decision moment) and the reader's
    causality gate drops it in favour of the freshest fully-captured
    FULL_CONTRIBUTOR. The caller computes inference on the elected row;
    causal_snapshot_id remains provenance only. On block, ``reason`` is the
    BLOCKED reason code and elected id is None.
    """

    try:
        from src.data.executable_forecast_reader import SOURCE_TRANSPORT, read_executable_forecast

        target_date = date.fromisoformat(str(coverage.get("target_local_date") or family.target_date))
        source_id = _nonnull(coverage.get("source_id") or source_run.get("source_id") or snapshot.get("source_id"))
        source_transport = _nonnull(coverage.get("source_transport") or snapshot.get("source_transport") or SOURCE_TRANSPORT)
        data_version = _nonnull(coverage.get("data_version") or snapshot.get("data_version"))
        source_run_id = _nonnull(source_run.get("source_run_id") or snapshot.get("source_run_id"))
        track = _nonnull(coverage.get("track") or source_run.get("track") or snapshot.get("track") or _payload(event).get("track"))
        condition_id = _nonnull(_payload(event).get("condition_id") or (family.condition_ids[0] if family.condition_ids else ""))
        if (
            not source_id
            or not source_transport
            or not data_version
            or not source_run_id
            or not track
            or not condition_id
        ):
            return "FORECAST_READER_SCOPE_CONSTRUCTION_MISSING:scope_incomplete", None
        result = read_executable_forecast(
            conn,
            city_id=str(coverage.get("city_id") or family.city),
            city_name=str(coverage.get("city") or family.city),
            city_timezone=str(coverage.get("city_timezone") or "UTC"),
            target_local_date=target_date,
            temperature_metric=family.metric,
            source_id=source_id,
            source_transport=source_transport,
            data_version=data_version,
            track=track,
            strategy_key="entry_forecast",
            market_family=family.family_id,
            condition_id=condition_id,
            decision_time=decision_time,
            require_entry_readiness=False,
        )
    except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        return f"FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:{exc}", None
    if not result.ok or result.bundle is None:
        return f"FORECAST_READER_LIVE_ELIGIBILITY_BLOCKED:{result.reason_code}", None
    # SINGLE SNAPSHOT AUTHORITY: honour the reader's elected executable snapshot rather than
    # asserting it equals the reactor's causal-pinned selection. The causal snapshot triggers
    # the event but its source_run may still be re-ingesting members (captured_at advances past
    # the decision moment), so the causality gate legitimately drops it and the reader elects
    # the freshest fully-captured FULL_CONTRIBUTOR (often an earlier cycle). The prior
    # assertion produced a permanent FORECAST_READER_SNAPSHOT_MISMATCH leak (decision_events=0)
    # whenever the causal cycle was still ingesting. Elected snapshot = executable authority;
    # causal_snapshot_id stays provenance only.
    return None, _nonnull(result.bundle.snapshot.snapshot_id)


def _row_by_id(conn: sqlite3.Connection, table_ref: str, id_col: str, value: str) -> dict[str, Any] | None:
    cur = conn.execute(f"SELECT * FROM {table_ref} WHERE {id_col} = ? LIMIT 1", (value,))
    row = cur.fetchone()
    if row is None:
        return None
    names = [description[0] for description in cur.description]
    return {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))


def _coverage_row_for_snapshot(
    conn: sqlite3.Connection,
    table_ref: str,
    *,
    source_run_id: str,
    family,
    snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    columns = _table_ref_columns(conn, table_ref)
    predicates = ["source_run_id = ?"]
    params: list[object] = [source_run_id]
    for column, value in (
        ("city", family.city),
        ("target_local_date", family.target_date),
        ("temperature_metric", family.metric),
        ("source_id", snapshot.get("source_id")),
        ("source_transport", snapshot.get("source_transport")),
        ("data_version", snapshot.get("data_version")),
    ):
        if column in columns and value not in {None, ""}:
            predicates.append(f"{column} = ?")
            params.append(value)
    cur = conn.execute(
        f"""
        SELECT *
        FROM {table_ref}
        WHERE {' AND '.join(predicates)}
        ORDER BY computed_at DESC, recorded_at DESC
        LIMIT 1
        """,
        tuple(params),
    )
    row = cur.fetchone()
    if row is None:
        return None
    names = [description[0] for description in cur.description]
    return {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))


def _payload(event: OpportunityEvent) -> dict[str, object]:
    try:
        parsed = json.loads(event.payload_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _nonnull(value: object) -> str:
    return str(value or "").strip()


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


class FamilyKeyingError(ValueError):
    """A market_events sibling of the bound family carries no resolved condition_id.

    Fitz Constraint #4 antibody (silent keying loss). The family universe for a
    (city, target_date, metric) is the COMPLETE MECE partition in market_events;
    q/FDR are computed over that full partition. A sibling row whose condition_id
    is NULL/empty cannot be keyed to an executable identity. Silently filtering it
    out (the legacy ``COALESCE(condition_id,'') != ''`` behavior) shrinks the
    family with NO diagnosable signal — which either kills every sibling later as
    ``FDR_FAMILY_TOPOLOGY_INCOMPLETE`` or renormalizes q over a subset (~1.2x
    inflation at 3/11 missing). Both are catastrophic and invisible.

    Raising here converts that silent loss into a LOUD, named failure that points
    at the exact family. It is byte-identical to legacy behavior when condition_id
    is clean (the live invariant: 0/21018 market_events rows NULL today), so it
    fabricates no trade and changes no current decision — it only makes a FUTURE
    keying regression impossible to swallow silently at the producer->consumer seam.
    """


def _event_family_market_topology_rows(
    conn: sqlite3.Connection,
    payload: dict[str, object],
) -> list[dict[str, Any]]:
    """Return canonical market topology rows for the event city/date/metric.

    Forecast and Day0 events are family facts, not child-token facts. They may
    legitimately lack condition/token ids, but they still must bind through the
    forecast-owned market topology table before executable snapshots can satisfy
    the quote gate. The family universe comes from market_events, not from the
    subset of fresh executable snapshots, so a missing sibling cannot shrink the
    FDR denominator.

    Fail-loud keying antibody (Fitz #4): if ANY market_events row matching the
    family (city, target_date, metric) has a NULL/empty condition_id, the family
    is keying-broken and ``FamilyKeyingError`` is raised rather than the broken
    sibling being silently dropped from the MECE partition. See
    ``FamilyKeyingError`` for why a silent drop is catastrophic and invisible.
    """

    city = str(payload.get("city") or "").strip()
    target_date = str(payload.get("target_date") or "").strip()
    metric = str(payload.get("metric") or payload.get("temperature_metric") or "").strip()
    if not (city and target_date and metric):
        return []
    table_ref = _market_events_table_ref(conn)
    if table_ref is None:
        return []
    columns = _market_events_columns(conn, table_ref)
    required = {"city", "target_date", "temperature_metric", "condition_id"}
    if not required.issubset(columns):
        return []
    # ANTIBODY (Fitz #4): detect a keying-broken sibling BEFORE building the
    # family. A separate count of NULL/empty-condition_id rows scoped to THIS
    # family (never the whole table) — additive, so when condition_id is clean
    # the count is 0 and the family construction below is byte-identical to
    # legacy. A non-zero count means a sibling lost its executable identity at
    # the producer (market ingest) → fail loud, naming the family, instead of
    # silently shrinking the MECE partition q/FDR are computed over.
    broken_siblings = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {table_ref}
        WHERE city = ?
          AND target_date = ?
          AND temperature_metric = ?
          AND COALESCE(condition_id, '') = ''
        """,
        (city, target_date, metric),
    ).fetchone()
    n_broken = int(broken_siblings[0]) if broken_siblings else 0
    if n_broken:
        raise FamilyKeyingError(
            f"market_events family city={city!r} target_date={target_date!r} "
            f"metric={metric!r} has {n_broken} sibling row(s) with a NULL/empty "
            f"condition_id — the bin lost its executable identity at the ingest "
            f"producer. Refusing to bind a silently-shrunk MECE family (Fitz #4 "
            f"keying antibody). Fix the market_events writer keying, do NOT drop "
            f"the sibling."
        )
    select_fields = [
        "condition_id",
        _optional_column_expr(columns, "market_slug"),
        _optional_column_expr(columns, "range_label"),
        _optional_column_expr(columns, "range_low"),
        _optional_column_expr(columns, "range_high"),
        _optional_column_expr(columns, "outcome"),
        _optional_column_expr(columns, "token_id"),
        _optional_column_expr(columns, "discovered_at"),
        _optional_column_expr(columns, "captured_at"),
        _optional_column_expr(columns, "available_at"),
        _optional_column_expr(columns, "gamma_updated_at"),
        _optional_column_expr(columns, "created_at"),
        _optional_column_expr(columns, "received_at"),
        _optional_column_expr(columns, "scanned_at"),
        _optional_column_expr(columns, "persisted_at"),
        _optional_column_expr(columns, "updated_at"),
    ]
    label_order = "COALESCE(range_label, outcome, '')" if {"range_label", "outcome"}.issubset(columns) else (
        "COALESCE(range_label, '')" if "range_label" in columns else ("COALESCE(outcome, '')" if "outcome" in columns else "''")
    )
    token_order = "COALESCE(token_id, '')" if "token_id" in columns else "''"
    cur = conn.execute(
        f"""
        SELECT {', '.join(select_fields)}
        FROM {table_ref}
        WHERE city = ?
          AND target_date = ?
          AND temperature_metric = ?
          AND COALESCE(condition_id, '') != ''
        ORDER BY condition_id, {label_order}, {token_order}
        """,
        (city, target_date, metric),
    )
    names = [description[0] for description in cur.description]
    rows: list[dict[str, Any]] = []
    seen_conditions: set[str] = set()
    for row in cur.fetchall():
        item = {name: row[name] for name in names} if isinstance(row, sqlite3.Row) else dict(zip(names, row))
        condition_id = str(item.get("condition_id") or "")
        if not condition_id or condition_id in seen_conditions:
            continue
        seen_conditions.add(condition_id)
        rows.append(item)
    return rows


def _market_events_table_ref(conn: sqlite3.Connection) -> str | None:
    try:
        attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
        if "forecasts" in attached:
            exists = conn.execute(
                "SELECT 1 FROM forecasts.sqlite_master WHERE type='table' AND name='market_events'"
            ).fetchone()
            if exists is not None:
                return "forecasts.market_events"
    except Exception:
        pass
    if _table_exists(conn, "market_events"):
        return "market_events"
    return None


def _market_events_columns(conn: sqlite3.Connection, table_ref: str) -> set[str]:
    if "." in table_ref:
        schema, table = table_ref.split(".", 1)
        return {row[1] for row in conn.execute(f"PRAGMA {schema}.table_info({table})").fetchall()}
    return _table_columns(conn, table_ref)


def _optional_column_expr(columns: set[str], column: str) -> str:
    if column in columns:
        return column
    return f"NULL AS {column}"


def _qualified_optional_expr(columns: set[str], column: str, alias: str) -> str:
    if column in columns:
        return f"{alias}.{column} AS {column}"
    return f"NULL AS {column}"
