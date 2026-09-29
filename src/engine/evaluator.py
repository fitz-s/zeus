# Created: prior
# Last reused/audited: 2026-09-29
# Authority basis: the per-candidate evaluate_candidate lane (and its Day0Router
#   q) had no caller once the event reactor's global auction became the only entry
#   authority; it was deleted 2026-09-29 so Day0 q has one law
#   (src/calibration/day0_diurnal_residual.py, Day0DiurnalMixture.apply).
"""Evaluator decision types and the shared entry-sizing helpers they carry.

``MarketCandidate`` and ``EdgeDecision`` are the entry decision records; the helpers
here (price-boundary sizing, economic floor, Day0 truth classification, observation
field readers) are consumed by the cycle runtime, the monitor and the reactor.
"""

import json
import logging
import hashlib
import math
import sqlite3
import uuid
from dataclasses import (
    dataclass,
    field,
)
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    from src.data.observation_client import Day0ObservationContext
    from src.strategy.market_phase import MarketPhase
    from src.strategy.market_phase_evidence import MarketPhaseEvidence

from src.config import (
    CONFIG_DIR,
    City,
)
from src.contracts import (
    EdgeContext,
    SettlementSemantics,
)
from src.contracts.day0_payoff_truth import (
    Day0PayoffTruth,
    classify_day0_payoff_truth,
)
from src.state.portfolio import (
    PortfolioState,
    cluster_exposure_for_bankroll,
)
from src.strategy.kelly import kelly_size
from src.contracts.decision_evidence import DecisionEvidence
from src.contracts.no_trade_reason import NoTradeReason
from src.contracts.effective_kelly_context import EffectiveKellyContext, MissingEffectiveContextError
from src.contracts.execution_price import ExecutionPrice
from src.strategy.strategy_profile import try_get as _try_get_strategy_profile
from src.types import BinEdge

logger = logging.getLogger(__name__)
DAY0_EXECUTABLE_OBSERVATION_SOURCES_BY_SETTLEMENT_TYPE = {
    # Same physical settlement station as WU (ICAO identity enforced by
    # fast_obs_source_for_city + faithfulness gate in day0_fast_obs.py). This is
    # not a cross-source fallback: the AWC METAR channel is admitted only as a
    # faster distribution tail for the same station when WU's live distribution
    # is absent/stale/coverage-incomplete.
    "wu_icao": frozenset({
        "wu_api",
        "same_station_fast_tail",
        "wu_api+same_station_fast_tail",
        "wu_icao_history",
    }),
    # Hong Kong has no valid WU/VHHH route; the live Day0 monitor source is the
    # HKO native accumulator only.
    "hko": frozenset({"hko_hourly_accumulator"}),
    # NOAA-settled cities consume the exact configured ICAO station. Direct
    # AviationWeather publications are the low-latency current-state channel;
    # Ogimet remains the canonical hourly/history mirror of the same station,
    # and noaa_wrh_<icao> is the settlement page itself. The per-station
    # channels are DERIVED from the city's own station by
    # ``_day0_station_scoped_observation_sources`` below, never enumerated:
    # this set was written when Istanbul/Moscow/Tel-Aviv were the only NOAA
    # cities, so after the migration it authorized 3 stations and rejected the
    # other 45 for the identical source shape.
    "noaa": frozenset({"aviationweather_metar"}),
}
NATIVE_BUY_NO_QUOTE_AVAILABLE_VALIDATION = "buy_no_native_quote_available"


class FeeRateUnavailableError(RuntimeError):
    """Raised when token-specific execution fee cannot be established."""


@dataclass
class MarketCandidate:
    """A market discovered by the scanner, ready for evaluation."""

    city: City
    target_date: str
    outcomes: list[dict]
    hours_since_open: float
    hours_to_resolution: Optional[float] = None
    temperature_metric: str = "high"
    event_id: str = ""
    slug: str = ""
    observation: Optional["Day0ObservationContext"] = None
    discovery_mode: str = ""
    # P2 (PLAN_v3 §6.P2 stage 2): MarketPhase axis A tag.
    # Computed from (target_local_date, city.timezone, decision_time_utc,
    # polymarket_start/end) at candidate construction time using the
    # cycle's frozen decision_time. None when constructed by legacy
    # callers (test fixtures, off-cycle paths) — production discovery
    # tags every candidate via market_phase_from_market_dict in
    # cycle_runtime. Storing the enum (not the str) keeps downstream
    # dispatch type-safe; cycle_runtime serializes to .value for SQL.
    market_phase: Optional["MarketPhase"] = None
    # PR #56 review (P1, 2026-05-04): full provenance
    # of how ``market_phase`` was determined. Pre-fix the evaluator
    # hardcoded ``_phase_source="verified_gamma"`` whenever
    # ``market_phase`` was non-None, silently dropping the actual
    # provenance from MarketPhaseEvidence and skipping the 0.7×
    # ``fallback_f1`` haircut in the A6 phase-aware Kelly resolver.
    # Now cycle_runtime stamps the evidence's ``phase_source`` here so
    # the evaluator passes the real value through. Valid values match
    # MarketPhaseEvidence.phase_source: ``verified_gamma`` |
    # ``fallback_f1`` | ``onchain_resolved`` | ``unknown`` | None
    # (legacy fixture / pre-evidence path).
    market_phase_source: Optional[str] = None
    phase_evidence: Optional["MarketPhaseEvidence"] = None
    # OBS-AUTHORITY-FOUNDATION (2026-05-23): id of the
    # settlement_day_observation_authority row written by cycle_runtime right
    # after the day0/settlement observation was fetched (or failed). Stamped on
    # every EdgeDecision this candidate produces so opportunity_fact can join
    # back to the runtime observation object. None for non-settlement-day
    # candidates and legacy/test callers.
    observation_authority_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.phase_evidence is not None:
            if self.market_phase is None:
                self.market_phase = self.phase_evidence.phase
            if self.market_phase_source is None:
                self.market_phase_source = self.phase_evidence.phase_source


@dataclass
class EdgeDecision:
    """Result of evaluating a candidate. Either trade or no-trade."""

    should_trade: bool
    edge: Optional[BinEdge] = None
    tokens: Optional[dict] = None
    size_usd: float = 0.0
    decision_id: str = ""
    rejection_stage: str = ""
    rejection_reasons: list[str] = field(default_factory=list)
    selected_method: str = ""
    applied_validations: list[str] = field(default_factory=list)
    decision_snapshot_id: str = ""
    edge_source: str = ""
    strategy_key: str = ""
    availability_status: str = ""
    # Signal data for decision chain recording
    p_raw: Optional[np.ndarray] = None
    p_cal: Optional[np.ndarray] = None
    p_market: Optional[np.ndarray] = None
    alpha: float = 0.0
    agreement: str = "AGREE"
    spread: float = 0.0
    n_edges_found: int = 0
    n_edges_after_fdr: int = 0
    fdr_family_scan_unavailable: bool = False
    fdr_family_size: int = 0
    sizing_bankroll: float = 0.0
    kelly_multiplier_used: float = 0.0
    execution_fee_rate: float = 0.0
    # P2 (PLAN_v3 §6.P2 stage 2): MarketPhase axis A tag forwarded from
    # the candidate. Stored as the enum's ``.value`` string so SQL/JSON
    # serialization is uniform; downstream callers that need the enum
    # re-resolve via ``MarketPhase(value)``. None when the upstream
    # candidate was untagged (legacy / test fixtures).
    market_phase: Optional[str] = None

    # Heavy Bound Domain Objects (Phase 2 encapsulation)
    edge_context: Optional[EdgeContext] = None
    settlement_semantics_json: Optional[str] = None
    epistemic_context_json: Optional[str] = None
    edge_context_json: Optional[str] = None

    # T4.1b 2026-04-23 (D4 Option E persistence wiring): entry-path
    # `DecisionEvidence` captured at the accept site flows here so the
    # canonical ENTRY_ORDER_POSTED event payload can carry a
    # `decision_evidence_envelope` sidecar. None on rejection paths and
    # test fixtures — the sidecar key is omitted in that case.
    decision_evidence: Optional[DecisionEvidence] = None

    # T2 Phase 2: no_trade instrumentation fields.
    # rejection_reason_enum: NoTradeReason CATEGORY — set by _make_rejection_decision.
    # rejection_reason_detail: free-form telemetry — original f-string / str(exc) content.
    # Both None on trade paths (should_trade=True) and pre-T2 callsites not yet migrated.
    # Persisted to no_trade_events by cycle_runtime after evaluate_candidate returns.
    rejection_reason_enum: Optional["NoTradeReason"] = None
    rejection_reason_detail: Optional[str] = None
    family_ranked_candidate_rank: int = 0
    family_ranked_candidate_count: int = 0
    family_portfolio_selected_leg_count: int = 0
    family_portfolio_leg_role: str = ""

    # OBS-AUTHORITY-FOUNDATION (2026-05-23): FK to the
    # settlement_day_observation_authority row captured at decision time for
    # day0/settlement candidates. None for non-settlement-day candidates and
    # legacy callsites. Persisted to opportunity_fact.observation_authority_id
    # so an operator can join an edge back to the runtime observation object.
    observation_authority_id: Optional[str] = None

    # LIVE-PROB-P0 §E (2026-05-23): per-edge-bin sanity telemetry columns.
    # Set by probability_edge_bin_sanity at the per-edge gate site (~5077).
    # All populated when the gate is evaluated; None for day0 / pre-gate paths.
    # ALL columns must be populated in production (no dead columns per critic INV).
    probability_sanity_mode: Optional[str] = None
    probability_sanity_reason: Optional[str] = None
    edge_bin_idx: Optional[int] = None
    edge_bin_label: Optional[str] = None
    edge_bin_p_raw: Optional[float] = None
    edge_bin_p_cal: Optional[float] = None
    edge_bin_p_market: Optional[float] = None
    edge_bin_member_support: Optional[float] = None
    edge_bin_odds_ratio: Optional[float] = None
    near_tail_p_cal: Optional[float] = None
    near_tail_p_market: Optional[float] = None

    def __post_init__(self) -> None:
        if self.decision_snapshot_id is None:
            raise ValueError(
                "EdgeDecision.decision_snapshot_id must not be None"
            )


@dataclass(frozen=True)
class ShoulderClusterContext:
    cluster_id: str
    side: str
    regime: str


def _shoulder_cluster_context_for_edge(
    *,
    city_name: str,
    target_date: str,
    edge: BinEdge,
) -> ShoulderClusterContext | None:
    if not getattr(edge.bin, "is_shoulder", False):
        return None
    try:
        from src.contracts.weather_regime_tag import WeatherRegimeTag
        from src.strategy.correlation_cluster import tail_correlation_cluster_for

        regime = getattr(edge, "tail_regime_tag", WeatherRegimeTag.UNKNOWN)
        if isinstance(regime, str):
            try:
                regime = WeatherRegimeTag(regime)
            except ValueError:
                regime = WeatherRegimeTag.UNKNOWN
        target = date.fromisoformat(str(target_date))
        cluster_id = tail_correlation_cluster_for(city_name, regime, target)
        if not cluster_id:
            return None
        side = "sell" if edge.direction == "buy_no" else "buy"
        return ShoulderClusterContext(
            cluster_id=cluster_id,
            side=side,
            regime=str(getattr(regime, "value", regime)),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "shoulder cluster context unavailable for city=%s target_date=%s: %s",
            city_name,
            target_date,
            exc,
        )
        return None


def _record_accepted_shoulder_exposure(
    *,
    conn: sqlite3.Connection | None,
    city_name: str,
    target_date: str,
    edge: BinEdge,
    notional_usd: float,
    decision_event_id: str,
    observed_at: datetime,
    source: str,
) -> str | None:
    from src.strategy.shoulder_cluster_cap import record_accepted_shoulder_exposure

    return record_accepted_shoulder_exposure(
        conn=conn,
        city_name=city_name,
        target_date=target_date,
        edge=edge,
        notional_usd=notional_usd,
        decision_event_id=decision_event_id,
        observed_at=observed_at,
        source=source,
    )


def _current_cluster_exposure_for_sizing(
    *,
    portfolio: PortfolioState,
    cluster_key: str,
    sizing_bankroll: float,
    projected_cluster_exposure_usd: dict[str, float],
) -> float:
    projected_cluster_heat = (
        projected_cluster_exposure_usd[cluster_key] / sizing_bankroll
        if sizing_bankroll > 0
        else 0.0
    )
    return (
        cluster_exposure_for_bankroll(portfolio, cluster_key, sizing_bankroll)
        + projected_cluster_heat
    )


# F25 Strategy R: sentinel for pre-snapshot rejection paths (all 31 early-rejection
# sites in evaluate_candidate fire before snapshot_id is resolved at line ~2378+).
# Non-NULL, non-empty — passes truthy checks and SQL LIKE queries.
def _day0_observation_field(
    observation: "Day0ObservationContext",
    field: str,
    default=None,
):
    if isinstance(observation, dict):
        return observation.get(field, default)
    return getattr(observation, field, default)


def _parse_day0_observation_time_utc(value) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            raw = str(value).strip()
            if not raw:
                return None
            if raw.isdigit():
                parsed = datetime.fromtimestamp(float(raw), tz=timezone.utc)
            else:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (OSError, OverflowError, TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite_day0_observation_float(
    observation: "Day0ObservationContext",
    field: str,
) -> float | None:
    raw = _day0_observation_field(observation, field)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _decision_id() -> str:
    return str(uuid.uuid4())[:12]


def _strategy_live_quality_policy(strategy_key: str) -> SimpleNamespace:
    profile = _try_get_strategy_profile(strategy_key)
    return SimpleNamespace(
        min_entry_price=float(getattr(profile, "min_entry_price", 0.05) if profile is not None else 0.05),
        min_strategy_notional_usd=float(
            getattr(profile, "min_strategy_notional_usd", 1.0) if profile is not None else 1.0
        ),
        min_expected_profit_usd=float(
            getattr(profile, "min_expected_profit_usd", 0.05) if profile is not None else 0.05
        ),
        allow_ultra_low_tail=bool(
            getattr(profile, "allow_ultra_low_tail", False) if profile is not None else False
        ),
        partial_source_run_allowed=bool(
            getattr(profile, "partial_source_run_allowed", True) if profile is not None else True
        ),
        complete_required_for_tail_orders=bool(
            getattr(profile, "complete_required_for_tail_orders", True) if profile is not None else True
        ),
        partial_run_kelly_haircut=float(
            getattr(profile, "partial_run_kelly_haircut", 0.5) if profile is not None else 0.5
        ),
    )


def _live_entry_economic_floor_rejection(
    *,
    strategy_key: str,
    edge: BinEdge,
    submitted_notional_usd: float,
    expected_profit_usd: float,
    final_limit_price: float,
    passive_order: bool,
    passive_fill_probability: float | None = None,
    passive_adverse_selection_score: float | None = None,
) -> str | None:
    """Return a live-quality rejection reason, separate from venue min order."""

    policy = _strategy_live_quality_policy(strategy_key)
    if submitted_notional_usd < policy.min_strategy_notional_usd:
        return (
            "STRATEGY_NOTIONAL_BELOW_LIVE_FLOOR("
            f"{submitted_notional_usd:.4f}<${policy.min_strategy_notional_usd:.2f}; "
            f"strategy={strategy_key})"
        )
    effective_expected_profit_usd = float(expected_profit_usd)
    if passive_order:
        if passive_fill_probability is None:
            return (
                "PASSIVE_FILL_PROBABILITY_UNMODELED("
                f"price={final_limit_price:.4f}; strategy={strategy_key})"
            )
        try:
            fill_probability = float(passive_fill_probability)
        except (TypeError, ValueError):
            fill_probability = -1.0
        if fill_probability <= 0.0 or fill_probability > 1.0:
            return (
                "PASSIVE_FILL_PROBABILITY_INVALID("
                f"{fill_probability:.4f}; strategy={strategy_key})"
            )
        adverse_selection_cost_usd = 0.0
        if passive_adverse_selection_score is not None:
            try:
                adverse_selection = float(passive_adverse_selection_score)
            except (TypeError, ValueError):
                adverse_selection = 1.0
            adverse_selection = max(0.0, min(1.0, adverse_selection))
            adverse_selection_cost_usd = adverse_selection * float(submitted_notional_usd)
        effective_expected_profit_usd = (
            effective_expected_profit_usd * fill_probability
            - adverse_selection_cost_usd
        )
    if not policy.allow_ultra_low_tail and final_limit_price <= policy.min_entry_price:
        return (
            "ULTRA_LOW_PRICE_NOT_AUTHORIZED("
            f"{final_limit_price:.4f}<={policy.min_entry_price:.2f}; strategy={strategy_key})"
        )
    if effective_expected_profit_usd < policy.min_expected_profit_usd:
        display_profit = effective_expected_profit_usd if passive_order else expected_profit_usd
        detail = (
            "; fill_adjusted=true"
            if passive_order and passive_adverse_selection_score is None
            else (
                "; fill_adjusted=true; adverse_selection=true"
                if passive_order
                else ""
            )
        )
        return (
            "EXPECTED_PROFIT_BELOW_LIVE_FLOOR("
            f"{display_profit:.4f}<${policy.min_expected_profit_usd:.2f}; "
            f"strategy={strategy_key}{detail})"
        )
    return None


def _source_quality_context(ens_result: dict | None) -> SimpleNamespace:
    result = ens_result if isinstance(ens_result, dict) else {}
    evidence = result.get("executable_forecast_evidence")
    get_attr = getattr
    source_run_status = str(
        result.get("source_run_status")
        or get_attr(evidence, "source_run_status", "")
        or ""
    ).upper()
    source_run_completeness = str(
        result.get("source_run_completeness_status")
        or get_attr(evidence, "source_run_completeness_status", "")
        or ""
    ).upper()
    coverage_completeness = str(
        result.get("coverage_completeness_status")
        or get_attr(evidence, "coverage_completeness_status", "")
        or ""
    ).upper()
    try:
        expected_members = int(result.get("expected_members") or get_attr(evidence, "expected_members", 0) or 0)
    except (TypeError, ValueError):
        expected_members = 0
    try:
        observed_members = int(result.get("observed_members") or get_attr(evidence, "observed_members", 0) or 0)
    except (TypeError, ValueError):
        observed_members = 0
    members_complete = expected_members > 0 and observed_members >= expected_members
    per_scope_complete = coverage_completeness in {"COMPLETE", "FULL", "OK"} and members_complete
    partial = not per_scope_complete and (
        source_run_status == "PARTIAL"
        or source_run_completeness == "PARTIAL"
        or coverage_completeness == "PARTIAL"
        or (expected_members > 0 and observed_members < expected_members)
    )
    return SimpleNamespace(
        partial=partial,
        source_run_status=source_run_status,
        source_run_completeness_status=source_run_completeness,
        coverage_completeness_status=coverage_completeness,
        expected_members=expected_members,
        observed_members=observed_members,
    )


def _source_quality_kelly_haircut(strategy_key: str, ens_result: dict | None) -> float:
    if not _source_quality_context(ens_result).partial:
        return 1.0
    policy = _strategy_live_quality_policy(strategy_key)
    if not policy.partial_source_run_allowed:
        return 0.0
    return max(0.0, min(1.0, float(policy.partial_run_kelly_haircut)))


def _size_at_execution_price_boundary(
    *,
    p_posterior: float,
    entry_price: float,
    fee_rate: float,
    sizing_bankroll: float,
    kelly_multiplier: float,
    effective_context: EffectiveKellyContext | None = None,
    allow_missing_context: bool = False,
    max_executable_shares: float | None = None,
) -> float:
    """Size a trade at the evaluator→Kelly boundary using typed entry cost.

    P10E: rollback path removed — fee-adjusted typed price is the
    only path. No feature flag; assert_kelly_safe() runs unconditionally.

    Per-trade safety-cap authority was removed 2026-05-04; per-cycle exposure
    discipline now lives in posture / RiskGuard / max-exposure gates only.

    PR 7 — INV-kelly-effective: effective_context provides a 5th multiplicative
    haircut factor applied AFTER the existing 4-multiplier chain.  When
    effective_context is None on a live path, raises MissingEffectiveContextError
    (fail-closed) UNLESS allow_missing_context=True is explicitly passed.  The
    sole authorised allow_missing_context=True site is the evaluate_candidate
    inner loop (evaluator.py:~3707) — a pre-snapshot provisional sizing path
    where context is not yet in scope; final haircut is applied downstream at
    cycle_runtime W2/W3/W4.  Non-live paths (replay/backtest) pass None with
    graceful degrade (no haircut, WARNING logged).
    """
    effective_kelly_multiplier = kelly_multiplier
    if effective_context is not None:
        effective_kelly_multiplier = kelly_multiplier * effective_context.haircut()
    elif effective_context is None:
        from src.config import get_mode as _get_mode
        if _get_mode() == "live" and not allow_missing_context:
            # Fail-closed on live: raise so any caller that forgets context
            # surfaces immediately rather than silently sizing without haircut.
            raise MissingEffectiveContextError(
                "INV-kelly-effective: _size_at_execution_price_boundary called without "
                "effective_context on live path; failing closed."
            )
        # Only warn when context is genuinely missing (not an explicit bypass).
        # allow_missing_context=True is an authorised path (e.g. replay W5,
        # evaluate_candidate pre-snapshot loop) — suppress the warning there
        # to avoid log noise on every tick in the hot inner loop.
        if not allow_missing_context:
            _mode = _get_mode()
            logger.warning(
                "INV-kelly-effective: _size_at_execution_price_boundary called without "
                "effective_context on %s path; degrading gracefully (no haircut)",
                _mode,
            )

    # Wave 2 (INV-39, 2026-05-27): DO NOT fabricate
    # ExecutionPrice(price_type="implied_probability") at this boundary.
    # The upstream BinEdge.entry_price (constructed at edge-scan in
    # MarketAnalysis.find_edges) is already a typed ExecutionPrice carrying
    # real provenance (e.g. "vwmp" from _buy_entry_price_from_clob). Fabricating
    # implied_probability here, then immediately laundering it into
    # "fee_adjusted" via .with_taker_fee(), defeats the D3/INV-12 contract that
    # ExecutionPrice was created to enforce — assert_kelly_safe() would only
    # pass because of the laundering, not because real provenance reached the
    # boundary. See architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §D5.
    if isinstance(entry_price, ExecutionPrice):
        ep = entry_price
    else:
        # Legacy float caller (test fixture / non-BinEdge path). Coerce with
        # explicit implied_probability tag so the legacy semantic is preserved
        # but visible; INV-38 fix in BinEdge.__post_init__ catches the BinEdge
        # path before it reaches here.
        ep = ExecutionPrice(
            value=float(entry_price),
            price_type="implied_probability",
            fee_deducted=False,
            currency="probability_units",
        )
    ep_fee_adjusted = ep if ep.fee_deducted else ep.with_taker_fee(fee_rate)
    ep_fee_adjusted.assert_kelly_safe()

    # DT#5 P9B (INV-21): pass the full ExecutionPrice object, not `.value`.
    # kelly_size now accepts ExecutionPrice and calls assert_kelly_safe()
    # internally — structural enforcement at the Kelly boundary.
    fee_adjusted_size = kelly_size(
        p_posterior,
        ep_fee_adjusted,
        sizing_bankroll,
        effective_kelly_multiplier,
    )

    # K3 fix (PR #348 operator review, P0-4, 2026-05-27): cap the sized order
    # to the depth-walked executable authority. The σ_market that fed edge_LCB
    # was computed for ``EntryQuoteEvidence.depth_at_target_size`` shares; an
    # order larger than that walks deeper into the book than the cost estimate
    # accounted for, so its real slippage exceeds the σ_market in the CI.
    # Capping USD size to ``max_executable_shares * price`` ensures live
    # execution never exceeds the depth the cost evidence actually priced.
    # None (default / legacy / no-EQE path) leaves size unchanged.
    if max_executable_shares is not None and max_executable_shares > 0:
        price_value = ep_fee_adjusted.value
        if price_value > 0:
            cap_usd = float(max_executable_shares) * price_value
            if fee_adjusted_size > cap_usd:
                logger.info(
                    "[DEPTH_CAP] sized=%.4f > depth_authority=%.4f USD "
                    "(%.1f shares @ %.4f) — capping to depth-walked authority",
                    fee_adjusted_size, cap_usd, float(max_executable_shares), price_value,
                )
                fee_adjusted_size = cap_usd

    # P10E strict: rollback path removed. R10 requires fee-adjusted typed price.
    return fee_adjusted_size


def _default_weather_fee_rate() -> float:
    try:
        from src.contracts.reality_contract import load_contracts_from_yaml

        contracts = load_contracts_from_yaml(CONFIG_DIR / "reality_contracts" / "economic.yaml")
        fee_contract = next(
            (contract for contract in contracts if contract.contract_id == "FEE_RATE_WEATHER"),
            None,
        )
        if fee_contract is not None:
            return float(fee_contract.current_value)
    except Exception as exc:
        from src.contracts.exceptions import FeeRateUnavailableError
        logger.warning("FEE_RATE_WEATHER contract unavailable; failing evaluation: %s", exc)
        raise FeeRateUnavailableError(f"FEE_RATE_WEATHER contract unavailable: {exc}") from exc
    from src.contracts.exceptions import FeeRateUnavailableError
    raise FeeRateUnavailableError("FEE_RATE_WEATHER contract not found in economic.yaml")


def _edli_payload(context: dict) -> dict:
    payload = context.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


# Tolerance for "carries mass": a surviving bin holding strictly more than this
# after renorm is treated as live. Renorm produces exact 0.0 on masked bins, so
# any non-trivial residual on an impossible bin signals an inverted/incorrect mask.


def _day0_truth_classification_for_edge(
    candidate: MarketCandidate,
    edge: BinEdge,
) -> str | None:
    """Classify selected-side Day0 payoff truth after settlement rounding."""

    metric = str(candidate.temperature_metric).lower()
    if metric not in {"high", "low"}:
        return None
    observation_field = "high_so_far" if metric == "high" else "low_so_far"
    observed_raw = _finite_day0_observation_float(
        candidate.observation,
        observation_field,
    )
    if observed_raw is None:
        return "observation_unknown"
    try:
        sem = SettlementSemantics.for_city(candidate.city)
        observed = sem.round_single(observed_raw)
    except Exception as exc:
        logger.warning(
            "settlement_semantics_rounding_failed city=%s: %s — candidate rejected",
            candidate.city, exc,
        )
        return "settlement_semantics_unavailable"
    edge_bin = edge.bin
    truth = classify_day0_payoff_truth(
        metric=metric,
        direction=edge.direction,
        observed_extreme=observed,
        bin_low=edge_bin.low,
        bin_high=edge_bin.high,
    )
    if truth is Day0PayoffTruth.LOCKED:
        return "observation_locked"
    if truth is Day0PayoffTruth.REFUTED:
        return "observation_refutes_selected_side"
    if truth is Day0PayoffTruth.UNKNOWN:
        return "observation_unknown"
    if (
        metric == "high"
        and edge.direction == "buy_yes"
        and edge_bin.low is not None
        and observed < float(edge_bin.low)
    ):
        return "observation_floor_plus_forecast_upside"
    return "observation_partial_unresolved"


def _day0_high_truth_classification_for_edge(
    candidate: MarketCandidate,
    edge: BinEdge,
) -> str | None:
    """Compatibility alias for the former HIGH-only classifier."""

    return _day0_truth_classification_for_edge(candidate, edge)


def day0_high_truth_classification_for_edge(
    candidate: MarketCandidate,
    edge: BinEdge,
) -> str | None:
    """Public wrapper for _day0_high_truth_classification_for_edge.

    OBS-AUTHORITY-FOUNDATION FIX-2 (2026-05-23). Lets the durable opportunity_
    fact writer (src/state/db.py) compute the same observation-lock truth string
    the evaluator uses for edge_source/strategy_key, so it can be persisted per
    opportunity row without duplicating the classification logic.
    """
    return _day0_truth_classification_for_edge(candidate, edge)


def _valid_probability_vector(values: np.ndarray, expected_len: int) -> bool:
    try:
        arr = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError):
        return False
    if arr.shape != (expected_len,):
        return False
    if not np.all(np.isfinite(arr)):
        return False
    if np.any(arr < 0.0):
        return False
    if np.any(arr > 1.0):
        return False
    total = float(np.sum(arr))
    return bool(np.isfinite(total) and np.isclose(total, 1.0, rtol=1e-6, atol=1e-6))


def _parse_forecast_timestamp(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _attached_table_exists(conn, schema: str, table: str) -> bool:
    try:
        row = conn.execute(
            f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _snapshot_identity_matches(
    row,
    *,
    city,
    target_date: str,
    temperature_metric: str,
    data_version: str,
    model_version: str,
    issue_time: str | None,
    valid_time: str | None,
    available_at: str,
    fetch_time: str,
) -> bool:
    return (
        row is not None
        and row["city"] == city.name
        and row["target_date"] == target_date
        and row["temperature_metric"] == temperature_metric
        and row["dataset_id"] == data_version
        and row["model_version"] == model_version
        and row["issue_time"] == issue_time
        and row["valid_time"] == valid_time
        and row["available_at"] == available_at
        and row["fetch_time"] == fetch_time
    )

