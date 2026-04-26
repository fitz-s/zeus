"""Evaluator: takes a market candidate, returns an EdgeDecision or NoTradeCase.

Contains ALL business logic for edge detection. Doesn't know about scheduling,
portfolio state, or execution. Pure function: candidate -> decision.
"""

import json
import logging
import hashlib
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    from src.data.observation_client import Day0ObservationContext

from src.calibration.manager import get_calibrator
from src.calibration.manager import season_from_date
from src.calibration.platt import calibrate_and_normalize
from src.config import CONFIG_DIR, City, edge_n_bootstrap, ensemble_crosscheck_member_count, settings
from src.contracts import (
    EntryMethod,
    Direction,
    EdgeContext,
    EpistemicContext,
    SettlementSemantics,
)
from src.data.ensemble_client import fetch_ensemble, validate_ensemble
from src.data.polymarket_client import PolymarketClient
from src.engine.discovery_mode import DiscoveryMode
from src.engine.time_context import lead_days_to_date_start, lead_hours_to_date_start
from src.signal.day0_router import Day0Router, Day0SignalInputs
from src.signal.day0_window import remaining_member_extrema_for_day0
from src.signal.ensemble_signal import EnsembleSignal, select_hours_for_target_date
from src.control.control_plane import get_edge_threshold_multiplier
from src.riskguard.policy import StrategyPolicy, resolve_strategy_policy
from src.signal.model_agreement import model_agreement
from src.state.portfolio import (
    PortfolioState,
    city_exposure_for_bankroll,
    cluster_exposure_for_bankroll,
    has_same_city_range_open,
    is_reentry_blocked,
    is_token_on_cooldown,
    portfolio_heat_for_bankroll,
)
from src.strategy.fdr_filter import fdr_filter, DEFAULT_FDR_ALPHA
from src.strategy.kelly import dynamic_kelly_mult, kelly_size
from src.strategy.oracle_penalty import get_oracle_info, OracleStatus
from src.strategy.market_analysis_family_scan import FullFamilyHypothesis, scan_full_hypothesis_family
from src.strategy.selection_family import (
    apply_familywise_fdr,
    make_hypothesis_family_id,
    make_edge_family_id,
)
from src.types.metric_identity import MetricIdentity
from src.state.db import log_selection_family_fact, log_selection_hypothesis_fact
from src.contracts.boundary_policy import boundary_ambiguous_refuses_signal
from src.contracts.decision_evidence import DecisionEvidence
from src.contracts.execution_price import ExecutionPrice, polymarket_fee
from src.contracts.alpha_decision import AlphaTargetMismatchError
from src.strategy.market_analysis import MarketAnalysis
from src.strategy.market_fusion import AuthorityViolation, compute_alpha, vwmp
from src.strategy.risk_limits import RiskLimits, check_position_allowed
from src.types import Bin, BinEdge
from src.types.market import BinTopologyError, validate_bin_topology
from src.types.temperature import TemperatureDelta

logger = logging.getLogger(__name__)
CENTER_BUY_ULTRA_LOW_PRICE_MAX_ENTRY = 0.02


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
    fdr_fallback_fired: bool = False
    fdr_family_size: int = 0

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



def _read_v2_snapshot_metadata(
    conn, city_name: str, target_date: str, temperature_metric: str,
    snapshot_id: str | None = None,
) -> dict:
    """Phase 9C A4 (DT#7 wire) + P10D S1 (M3 causality wire):
    read boundary_ambiguous, causality_status, and snapshot_id metadata
    for one (city, target_date, metric) row from ensemble_snapshots_v2.

    Pre-Golden-Window-lift: v2 is empty → query returns no rows → returns
    empty dict → boundary_ambiguous_refuses_signal() returns False → no
    refusal (dormant gate). Post-data-lift: v2 populated by
    extract_tigge_mn2t6_localday_min.py per §DT#7 boundary-leakage law →
    gate fires on boundary_ambiguous=1 rows.

    M3 ordering: if snapshot_id is provided (candidate edge origin), lookup
    by snapshot_id first for exact match; fallback to fetch_time DESC LIMIT 1
    (most-recent row) when snapshot_id is absent or unmatched. The fallback
    may read a later-filed correction row; this is acceptable pre-data-lift
    but noted as a caveat for post-lift audits.

    Returns:
        dict with `boundary_ambiguous`, `causality_status`, and `snapshot_id`
        keys when row exists; empty dict when row is absent OR v2 table is
        not present (backward compat for legacy-only databases).
    """
    # Resolve schema prefix (world.ensemble_snapshots_v2 when world DB
    # attached; bare ensemble_snapshots_v2 in monolithic test DBs).
    import sqlite3
    for sp in ("world.", ""):
        try:
            if snapshot_id:
                # M3: prefer exact snapshot_id match to avoid reading
                # later-filed correction rows for a different fetch cycle.
                row = conn.execute(
                    f"""
                    SELECT boundary_ambiguous, causality_status, snapshot_id
                    FROM {sp}ensemble_snapshots_v2
                    WHERE city = ?
                      AND target_date = ?
                      AND temperature_metric = ?
                      AND snapshot_id = ?
                    LIMIT 1
                    """,
                    (city_name, target_date, temperature_metric, snapshot_id),
                ).fetchone()
                if row is None:
                    # snapshot_id present but unmatched — fall through to
                    # fetch_time-ordered fallback below.
                    row = conn.execute(
                        f"""
                        SELECT boundary_ambiguous, causality_status, snapshot_id
                        FROM {sp}ensemble_snapshots_v2
                        WHERE city = ?
                          AND target_date = ?
                          AND temperature_metric = ?
                        ORDER BY fetch_time DESC
                        LIMIT 1
                        """,
                        (city_name, target_date, temperature_metric),
                    ).fetchone()
            else:
                # M3 fallback: no snapshot_id — use most-recent fetch.
                row = conn.execute(
                    f"""
                    SELECT boundary_ambiguous, causality_status, snapshot_id
                    FROM {sp}ensemble_snapshots_v2
                    WHERE city = ?
                      AND target_date = ?
                      AND temperature_metric = ?
                    ORDER BY fetch_time DESC
                    LIMIT 1
                    """,
                    (city_name, target_date, temperature_metric),
                ).fetchone()
        except sqlite3.OperationalError:
            continue
        except Exception:
            return {}
        if row is None:
            return {}
        return {
            "boundary_ambiguous": bool(row["boundary_ambiguous"]),
            "causality_status": str(row["causality_status"]),
            "snapshot_id": row["snapshot_id"],
        }
    return {}


def _decision_id() -> str:
    return str(uuid.uuid4())[:12]


def _default_strategy_policy(strategy_key: str) -> StrategyPolicy:
    threshold_multiplier = max(1.0, float(get_edge_threshold_multiplier()))
    sources: list[str] = []
    if threshold_multiplier > 1.0:
        sources.append(f"hard_safety:tighten_risk:{threshold_multiplier:g}")
    return StrategyPolicy(
        strategy_key=strategy_key,
        gated=False,
        allocation_multiplier=1.0,
        threshold_multiplier=threshold_multiplier,
        exit_only=False,
        sources=sources,
    )


def _center_buy_ultra_low_price_block_reason(strategy_key: str, edge: BinEdge) -> str | None:
    if strategy_key != "center_buy":
        return None
    if edge.direction != "buy_yes":
        return None
    try:
        entry_price = float(edge.entry_price)
    except (TypeError, ValueError):
        return None
    if entry_price <= CENTER_BUY_ULTRA_LOW_PRICE_MAX_ENTRY:
        return f"CENTER_BUY_ULTRA_LOW_PRICE({entry_price:.4f}<={CENTER_BUY_ULTRA_LOW_PRICE_MAX_ENTRY:.2f})"
    return None


def _size_at_execution_price_boundary(
    *,
    p_posterior: float,
    entry_price: float,
    fee_rate: float,
    sizing_bankroll: float,
    kelly_multiplier: float,
    safety_cap_usd: float | None,
) -> float:
    """Size a trade at the evaluator→Kelly boundary using typed entry cost.

    P10E: shadow-off rollback path removed — fee-adjusted typed price is the
    only path. No feature flag; assert_kelly_safe() runs unconditionally.
    """
    raw_entry_price = float(entry_price)
    ep = ExecutionPrice(
        value=raw_entry_price,
        price_type="implied_probability",
        fee_deducted=False,
        currency="probability_units",
    )
    ep_fee_adjusted = ep.with_taker_fee(fee_rate)
    ep_fee_adjusted.assert_kelly_safe()

    # DT#5 P9B (INV-21): pass the full ExecutionPrice object, not `.value`.
    # kelly_size now accepts ExecutionPrice and calls assert_kelly_safe()
    # internally — structural enforcement at the Kelly boundary.
    fee_adjusted_size = kelly_size(
        p_posterior,
        ep_fee_adjusted,
        sizing_bankroll,
        kelly_multiplier,
        safety_cap_usd=safety_cap_usd,
    )

    # P10E strict: shadow-off path removed. R10 requires fee-adjusted typed price.
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


def _fee_rate_for_token(clob: PolymarketClient, token_id: str) -> float:
    getter = getattr(clob, "get_fee_rate", None)
    if callable(getter):
        try:
            return float(getter(token_id))
        except Exception as exc:
            raise FeeRateUnavailableError(f"fee-rate lookup failed for {token_id}: {exc}") from exc
    return _default_weather_fee_rate()


def _to_jsonable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {f.name: _to_jsonable(getattr(value, f.name)) for f in fields(value)}
    return value


def _serialize_json(value) -> str:
    return json.dumps(_to_jsonable(value), default=str, ensure_ascii=False)


def _forecast_source_key(model_name: str | None) -> str:
    text = str(model_name or "ecmwf_ifs025").strip().lower()
    if text.startswith("ecmwf"):
        return "ecmwf"
    if text.startswith("gfs"):
        return "gfs"
    if text.startswith("icon"):
        return "icon"
    if text.startswith("openmeteo"):
        return "openmeteo"
    return text


def _normalize_temperature_metric(value: str | None) -> MetricIdentity:
    """The single legal str→MetricIdentity conversion point in the codebase.

    Wraps the raw string from MarketCandidate.temperature_metric into a typed
    MetricIdentity. All downstream signal code receives the MetricIdentity object.

    Slice A3 (PR #19 finding 7, 2026-04-26): pre-A3 this function silently
    defaulted None / empty / unrecognized inputs to HIGH (`raw = "low" if
    text == "low" else "high"`), making it impossible for callers to know
    when their identity was lost. Now raises ValueError on invalid input
    so the silent fallback to HIGH cannot mask a missing or corrupt metric
    upstream. MarketCandidate(temperature_metric: str = "high") still
    protects callers that intentionally rely on the default; only callers
    that pass None / empty / garbage are surfaced.
    """
    text = str(value or "").strip().lower()
    if text not in ("high", "low"):
        raise ValueError(
            f"temperature_metric must be 'high' or 'low'; got {value!r}"
        )
    return MetricIdentity.from_raw(text)


def _load_model_bias_reference(conn, *, city_name: str, season: str, forecast_source: str) -> dict:
    if conn is None:
        return {}
    try:
        row = conn.execute(
            """
            SELECT bias, mae, n_samples, discount_factor
            FROM model_bias
            WHERE city = ? AND season = ? AND source = ?
            """,
            (city_name, season, forecast_source),
        ).fetchone()
    except Exception:
        return {}
    if row is None:
        return {}
    return {
        "source": forecast_source,
        "bias": float(row["bias"]),
        "mae": float(row["mae"]),
        "n_samples": int(row["n_samples"]),
        "discount_factor": float(row["discount_factor"]),
    }


def _edge_source_for(candidate: MarketCandidate, edge: BinEdge) -> str:
    if candidate.discovery_mode == DiscoveryMode.DAY0_CAPTURE.value:
        return "settlement_capture"
    if candidate.discovery_mode == DiscoveryMode.OPENING_HUNT.value:
        return "opening_inertia"
    if edge.bin.is_shoulder:
        return "shoulder_sell"
    if edge.direction == "buy_yes":
        return "center_buy"
    return "opening_inertia"


def _strategy_key_for(candidate: MarketCandidate, edge: BinEdge) -> str:
    if candidate.discovery_mode == DiscoveryMode.DAY0_CAPTURE.value:
        return "settlement_capture"
    if candidate.discovery_mode == DiscoveryMode.OPENING_HUNT.value:
        return "opening_inertia"
    if edge.bin.is_shoulder:
        return "shoulder_sell"
    if edge.direction == "buy_yes":
        return "center_buy"
    return "opening_inertia"


def _strategy_key_for_hypothesis(candidate: MarketCandidate, hypothesis: FullFamilyHypothesis) -> str:
    if candidate.discovery_mode == DiscoveryMode.DAY0_CAPTURE.value:
        return "settlement_capture"
    if candidate.discovery_mode == DiscoveryMode.OPENING_HUNT.value:
        return "opening_inertia"
    if hypothesis.is_shoulder:
        return "shoulder_sell"
    if hypothesis.direction == "buy_yes":
        return "center_buy"
    return "opening_inertia"


def _entry_ci_rejection_reason(candidate: MarketCandidate, edge: BinEdge) -> str | None:
    if candidate.discovery_mode not in {
        DiscoveryMode.DAY0_CAPTURE.value,
        DiscoveryMode.UPDATE_REACTION.value,
    }:
        return None
    try:
        ci_lower = float(edge.ci_lower)
        ci_upper = float(edge.ci_upper)
    except (TypeError, ValueError):
        return "MISSING_CONFIDENCE_BAND"
    if not np.isfinite(ci_lower) or not np.isfinite(ci_upper):
        return "MISSING_CONFIDENCE_BAND"
    if ci_lower <= 0.0 or ci_upper <= ci_lower:
        return f"DEGENERATE_CONFIDENCE_BAND(ci_lower={ci_lower:.4f},ci_upper={ci_upper:.4f})"
    return None


def _selection_hypothesis_id(
    *,
    family_id: str,
    range_label: str,
    direction: str,
) -> str:
    payload = json.dumps(
        {
            "family_id": family_id,
            "range_label": range_label,
            "direction": direction,
        },
        sort_keys=True,
    )
    return "selection_hypothesis:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _record_selection_family_facts(
    conn,
    *,
    candidate: MarketCandidate,
    edges: list[BinEdge],
    filtered: list[BinEdge],
    hypotheses: list[FullFamilyHypothesis] | None = None,
    decision_snapshot_id: str,
    selected_method: str,
    recorded_at: str,
    decision_time_status: str | None = None,
) -> dict:
    """Persist tested selection hypotheses without changing active selection."""
    if conn is None:
        return {"status": "skipped_no_connection"}
    if not edges and not hypotheses:
        return {"status": "skipped_no_hypotheses"}

    selected_edge_ids = {id(edge) for edge in filtered}
    selected_edge_keys = {(edge.bin.label, edge.direction) for edge in filtered}
    cycle_mode = candidate.discovery_mode or "unknown"
    discovery_mode = candidate.discovery_mode or ""
    candidate_id = candidate.event_id or candidate.slug or f"{candidate.city.name}|{candidate.target_date}"
    rows = []
    if hypotheses is not None:
        # Slice A3 (PR #19 finding 7, 2026-04-26): route raw candidate metric
        # through the canonical normalizer so invalid inputs raise instead of
        # silently defaulting to HIGH at the family-id seam.
        family_id = make_hypothesis_family_id(
            cycle_mode=cycle_mode,
            city=candidate.city.name,
            target_date=candidate.target_date,
            # S4 R9 P10B: pass metric so HIGH and LOW candidates never share a budget
            temperature_metric=_normalize_temperature_metric(
                candidate.temperature_metric
            ).temperature_metric,
            discovery_mode=discovery_mode,
            decision_snapshot_id=decision_snapshot_id,
        )
        for hypothesis in hypotheses:
            strategy_key = _strategy_key_for_hypothesis(candidate, hypothesis)
            rows.append(
                {
                    "family_id": family_id,
                    "hypothesis_id": _selection_hypothesis_id(
                        family_id=family_id,
                        range_label=hypothesis.range_label,
                        direction=hypothesis.direction,
                    ),
                    "strategy_key": "",
                    "hypothesis_strategy_key": strategy_key,
                    "candidate_id": candidate_id,
                    "range_label": hypothesis.range_label,
                    "direction": hypothesis.direction,
                    "p_value": float(hypothesis.p_value),
                    "ci_lower": float(hypothesis.ci_lower),
                    "ci_upper": float(hypothesis.ci_upper),
                    "edge": float(hypothesis.edge),
                    "tested": True,
                    "passed_prefilter": bool(hypothesis.passed_prefilter),
                    "active_fdr_selected": (hypothesis.range_label, hypothesis.direction) in selected_edge_keys,
                    "p_model": float(hypothesis.p_model),
                    "p_market": float(hypothesis.p_market),
                    "p_posterior": float(hypothesis.p_posterior),
                    "entry_price": float(hypothesis.entry_price),
                }
            )
    else:
        for edge in edges:
            strategy_key = _strategy_key_for(candidate, edge)
            # Slice A3 (PR #19 finding 7, 2026-04-26): canonical normalizer
            # eliminates silent HIGH fallback at the edge family-id seam.
            family_id = make_edge_family_id(
                cycle_mode=cycle_mode,
                city=candidate.city.name,
                target_date=candidate.target_date,
                # S4 R9 P10B: pass metric so HIGH and LOW edges never share a budget
                temperature_metric=_normalize_temperature_metric(
                    candidate.temperature_metric
                ).temperature_metric,
                strategy_key=strategy_key,
                discovery_mode=discovery_mode,
                decision_snapshot_id=decision_snapshot_id,
            )
            rows.append(
                {
                    "family_id": family_id,
                    "hypothesis_id": _selection_hypothesis_id(
                        family_id=family_id,
                        range_label=edge.bin.label,
                        direction=edge.direction,
                    ),
                    "strategy_key": strategy_key,
                    "candidate_id": candidate_id,
                    "range_label": edge.bin.label,
                    "direction": edge.direction,
                    "p_value": float(edge.p_value),
                    "ci_lower": float(edge.ci_lower),
                    "ci_upper": float(edge.ci_upper),
                    "edge": float(edge.edge),
                    "tested": True,
                    "passed_prefilter": True,
                    "active_fdr_selected": id(edge) in selected_edge_ids,
                    "p_model": float(edge.p_model),
                    "p_market": float(edge.p_market),
                    "p_posterior": float(edge.p_posterior),
                    "entry_price": float(edge.entry_price),
                }
            )

    if not rows:
        return {"status": "skipped_no_hypotheses"}

    selected_rows = apply_familywise_fdr(rows)
    for row in selected_rows:
        row["selected_post_fdr"] = int(
            bool(row.get("selected_post_fdr")) and bool(row.get("passed_prefilter"))
        )
    family_meta: dict[str, dict] = {}
    for row in selected_rows:
        # Phase 1 (2026-04-16): carry the original family_id by reference from the
        # row dict — do NOT reconstruct via make_family_id/make_edge_family_id here.
        # Reconstructing would require knowing the scope (hyp vs edge) at this point,
        # and the row already carries the canonical ID written during the discovery pass.
        family_id = row["family_id"]
        meta = family_meta.setdefault(
            family_id,
            {
                "tested_hypotheses": 0,
                "passed_prefilter": 0,
                "selected_post_fdr": 0,
                "active_fdr_selected": 0,
                "selected_method": selected_method,
            },
        )
        meta["tested_hypotheses"] += 1
        meta["passed_prefilter"] += int(bool(row.get("passed_prefilter")))
        meta["selected_post_fdr"] += int(row.get("selected_post_fdr") or 0)
        meta["active_fdr_selected"] += int(bool(row.get("active_fdr_selected")))

    family_writes = 0
    hypothesis_writes = 0
    for family_id, meta in family_meta.items():
        first = next(row for row in selected_rows if row["family_id"] == family_id)
        result = log_selection_family_fact(
            conn,
            family_id=family_id,
            cycle_mode=cycle_mode,
            decision_snapshot_id=decision_snapshot_id,
            city=candidate.city.name,
            target_date=candidate.target_date,
            strategy_key=first["strategy_key"],
            discovery_mode=discovery_mode,
            created_at=recorded_at,
            meta=meta,
            decision_time_status=decision_time_status,
        )
        if result.get("status") == "written":
            family_writes += 1

    for row in selected_rows:
        selected_post_fdr = bool(row.get("selected_post_fdr"))
        result = log_selection_hypothesis_fact(
            conn,
            hypothesis_id=row["hypothesis_id"],
            family_id=row["family_id"],
            candidate_id=row["candidate_id"],
            city=candidate.city.name,
            target_date=candidate.target_date,
            range_label=row["range_label"],
            direction=row["direction"],
            p_value=row["p_value"],
            q_value=row.get("q_value"),
            ci_lower=row["ci_lower"],
            ci_upper=row["ci_upper"],
            edge=row["edge"],
            tested=True,
            passed_prefilter=bool(row.get("passed_prefilter")),
            selected_post_fdr=selected_post_fdr,
            rejection_stage=None if selected_post_fdr else "FDR_FILTERED",
            recorded_at=recorded_at,
            meta={
                "active_fdr_selected": bool(row.get("active_fdr_selected")),
                "hypothesis_strategy_key": row.get("hypothesis_strategy_key", row.get("strategy_key", "")),
                "p_model": row["p_model"],
                "p_market": row["p_market"],
                "p_posterior": row["p_posterior"],
                "entry_price": row["entry_price"],
            },
        )
        if result.get("status") == "written":
            hypothesis_writes += 1

    return {
        "status": "written",
        "families": family_writes,
        "hypotheses": hypothesis_writes,
    }


def _selected_edge_keys_from_full_family(
    candidate: MarketCandidate,
    hypotheses: list[FullFamilyHypothesis],
    *,
    decision_snapshot_id: str,
) -> set[tuple[str, str]]:
    if not hypotheses:
        return set()
    cycle_mode = candidate.discovery_mode or "unknown"
    discovery_mode = candidate.discovery_mode or ""
    rows = []
    # Slice A3 (PR #19 finding 7, 2026-04-26): canonical normalizer
    # eliminates silent HIGH fallback at the day0 hypothesis-replay seam.
    family_id = make_hypothesis_family_id(
        cycle_mode=cycle_mode,
        city=candidate.city.name,
        target_date=candidate.target_date,
        # S4 R9 P10B: pass metric so HIGH and LOW candidates never share a budget
        temperature_metric=_normalize_temperature_metric(
            candidate.temperature_metric
        ).temperature_metric,
        discovery_mode=discovery_mode,
        decision_snapshot_id=decision_snapshot_id,
    )
    for hypothesis in hypotheses:
        rows.append(
            {
                "family_id": family_id,
                "hypothesis_id": _selection_hypothesis_id(
                    family_id=family_id,
                    range_label=hypothesis.range_label,
                    direction=hypothesis.direction,
                ),
                "p_value": hypothesis.p_value,
                "tested": True,
                "passed_prefilter": hypothesis.passed_prefilter,
                "range_label": hypothesis.range_label,
                "direction": hypothesis.direction,
            }
        )
    selected_rows = apply_familywise_fdr(rows, q=DEFAULT_FDR_ALPHA)
    return {
        (str(row["range_label"]), str(row["direction"]))
        for row in selected_rows
        if bool(row.get("selected_post_fdr")) and bool(row.get("passed_prefilter"))
    }


def _availability_status_for_error(exc: Exception) -> str:
    text = str(exc).lower()
    name = exc.__class__.__name__
    if "429" in text or "rate" in text or "limit" in text or "capacity" in text:
        return "RATE_LIMITED"
    if "chain" in text:
        return "CHAIN_UNAVAILABLE"
    if name == "MissingCalibrationError":
        return "DATA_STALE"
    return "DATA_UNAVAILABLE"


def _get_day0_temporal_context(city: City, target_date: date, observation: "Optional[Day0ObservationContext]" = None):
    try:
        if observation is not None and not observation.observation_time:
            return None
        from src.signal.diurnal import build_day0_temporal_context
        observation_time = observation.observation_time if observation else None
        observation_source = observation.source if observation else ""
        return build_day0_temporal_context(
            city.name,
            target_date,
            city.timezone,
            observation_time=observation_time,
            observation_source=observation_source,
        )
    except Exception:
        return None


def evaluate_candidate(
    candidate: MarketCandidate,
    conn,
    portfolio: PortfolioState,
    clob: PolymarketClient,
    limits: RiskLimits,
    entry_bankroll: Optional[float] = None,
    decision_time: Optional[datetime] = None,
) -> list[EdgeDecision]:
    """Evaluate a market candidate through the full signal pipeline."""

    # Semantic Provenance Guard
    # Semantic Provenance Guard
    if False: _ = None.selected_method; _ = None.entry_method
    if False: _ = None.selected_method; _ = None.entry_method
    city = candidate.city
    target_date = candidate.target_date
    outcomes = candidate.outcomes
    # Slice A3-fix1 (post-review M2 from critic, 2026-04-26): pre-fix used
    # `getattr(candidate, "temperature_metric", "high")` here, which silently
    # substituted "high" before _normalize_temperature_metric could raise on
    # missing attribute. That recreated the same silent-HIGH default A3 just
    # removed at the normalizer body. Pass None instead so the normalizer
    # surfaces a missing attribute as a loud ValueError. MarketCandidate's
    # dataclass default at L91 still protects every standard-shape caller.
    temperature_metric = _normalize_temperature_metric(
        getattr(candidate, "temperature_metric", None)
    )
    is_day0_mode = candidate.discovery_mode == "day0_capture"
    selected_method = (
        EntryMethod.DAY0_OBSERVATION.value
        if is_day0_mode
        else EntryMethod.ENS_MEMBER_COUNTING.value
    )

    if is_day0_mode and candidate.observation is None:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=["Day0 observation unavailable"],
            availability_status="DATA_UNAVAILABLE",
            selected_method=selected_method,
            applied_validations=["day0_observation"],
        )]

    # DT#7 Phase 9C A4 (clause 3): refuse boundary-ambiguous candidates.
    # When ensemble_snapshots_v2 carries boundary_ambiguous=1 for this
    # (city, target_date, metric) row — as written at ingest time by
    # scripts/extract_tigge_mn2t6_localday_min.py per the boundary-leakage
    # law — the candidate must not be treated as confirmatory signal.
    # Pre-Golden-Window: v2 is empty; helper returns {} → gate returns
    # False → no refusal. Post-data-lift: v2 populated + low-track ingest
    # sets boundary_ambiguous per §DT#7 law → gate fires when appropriate.
    # Law: docs/authority/zeus_current_architecture.md §22 +
    #      docs/authority/zeus_dual_track_architecture.md §DT#7.
    v2_snapshot_meta = _read_v2_snapshot_metadata(
        conn, city.name, target_date,
        temperature_metric.temperature_metric,
    )
    if boundary_ambiguous_refuses_signal(v2_snapshot_meta):
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="MARKET_FILTER",
            rejection_reasons=["DT7_boundary_day_ambiguous"],
            availability_status="DATA_AVAILABLE",
            selected_method=selected_method,
            applied_validations=["dt7_boundary_day_gate"],
        )]

    # Build bins — skip unparseable (both boundaries None)
    bins = []
    token_map = {}
    for o in outcomes:
        low, high = o["range_low"], o["range_high"]
        if low is None and high is None:
            continue
        bins.append(Bin(low=low, high=high, label=o["title"], unit=city.settlement_unit))
        token_map[len(bins) - 1] = {
            "token_id": o["token_id"],
            "no_token_id": o["no_token_id"],
            "market_id": o["market_id"],
        }

    if len(bins) < 3:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="MARKET_FILTER",
            rejection_reasons=["< 3 parseable bins"],
            selected_method=selected_method,
            applied_validations=["market_filter"],
        )]

    try:
        validate_bin_topology(bins)
    except BinTopologyError as e:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="MARKET_FILTER",
            rejection_reasons=[f"bin topology: {e}"],
            selected_method=selected_method,
            applied_validations=["validate_bin_topology"],
        )]

    target_d = date.fromisoformat(target_date)
    lead_days = max(0.0, lead_days_to_date_start(target_d, city.timezone))
    ens_forecast_days = max(2, int(max(0.0, lead_days)) + 2)

    # Fetch ENS
    try:
        ens_result = fetch_ensemble(city, forecast_days=ens_forecast_days)
    except Exception as e:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=[str(e)],
            availability_status=_availability_status_for_error(e),
            selected_method=selected_method,
            applied_validations=["ens_fetch"],
        )]
    if ens_result is None or not validate_ensemble(ens_result):
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=["ENS fetch failed or < 51 members"],
            availability_status="DATA_UNAVAILABLE",
            selected_method=selected_method,
            applied_validations=["ens_fetch"],
        )]

    epistemic = EpistemicContext.enter_cycle(fallback_override=decision_time)
    settlement_semantics = SettlementSemantics.for_city(city)
    
    try:
        ens = EnsembleSignal(
            ens_result["members_hourly"],
            ens_result["times"],
            city, 
            target_d, 
            settlement_semantics=settlement_semantics,
            decision_time=decision_time,
            temperature_metric=temperature_metric,
        )
    except ValueError as e:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=[str(e)],
            availability_status="DATA_STALE",
            selected_method=selected_method,
            applied_validations=["ens_fetch"],
        )]

    decision_reference = ens_result.get("fetch_time")
    lead_days = max(0.0, lead_days_to_date_start(target_d, city.timezone, decision_reference))

    if is_day0_mode:
        temporal_context = _get_day0_temporal_context(city, target_d, candidate.observation)
        if temporal_context is None:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=["Solar/DST context unavailable for Day0"],
                availability_status="DATA_STALE",
                selected_method=selected_method,
                applied_validations=["day0_observation", "solar_context"],
            )]

        extrema, hours_remaining = remaining_member_extrema_for_day0(
            ens_result["members_hourly"],
            ens_result["times"],
            city.timezone,
            target_d,
            now=temporal_context.current_utc_timestamp,
            temperature_metric=temperature_metric,
        )
        if extrema is None:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=["No Day0 forecast hours remain for target date"],
                availability_status="DATA_STALE",
                selected_method=selected_method,
                applied_validations=["day0_observation", "ens_fetch"],
            )]

        if temperature_metric.is_low() and candidate.observation.low_so_far is None:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="OBSERVATION_UNAVAILABLE_LOW",
                rejection_reasons=["Day0 low observation unavailable"],
                availability_status="DATA_UNAVAILABLE",
                selected_method=selected_method,
                applied_validations=["day0_observation", "ens_fetch"],
            )]

        # P10D S1 / INV-16: thread causality_status from v2 snapshot metadata
        # into Day0SignalInputs so the Day0Router causality gate is live.
        # Pre-Golden-Window: v2_snapshot_meta is empty → fallback to "OK" (dormant gate).
        # Post-data-lift: v2 carries causality_status per ETL ingest law.
        # N/A_CAUSAL_DAY_ALREADY_STARTED signals the day has partially elapsed;
        # Day0Router routes LOW slots accordingly per _LOW_ALLOWED_CAUSALITY.
        causality_status = v2_snapshot_meta.get("causality_status", "OK")

        # INV-16 enforcement: reject LOW slots with causality_status outside the
        # allowed set before reaching any Platt lookup.  This is a SEPARATE
        # rejection axis from OBSERVATION_UNAVAILABLE_LOW — it fires when the
        # slot is partially historical for a reason other than missing observation.
        # The Day0Router already enforces _LOW_ALLOWED_CAUSALITY; this gate adds
        # an explicit evaluator-level rejection_stage for audit and operator clarity.
        _LOW_ALLOWED_CAUSALITY = frozenset({"OK", "N/A_CAUSAL_DAY_ALREADY_STARTED"})
        if temperature_metric.is_low() and causality_status not in _LOW_ALLOWED_CAUSALITY:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="CAUSAL_SLOT_NOT_OK",
                rejection_reasons=[
                    f"Day0 low slot rejected: causality_status={causality_status!r} "
                    f"not in allowed set {sorted(_LOW_ALLOWED_CAUSALITY)} (INV-16)"
                ],
                availability_status="DATA_AVAILABLE",
                selected_method=selected_method,
                applied_validations=["day0_observation", "ens_fetch", "causality_gate"],
            )]

        day0 = Day0Router.route(Day0SignalInputs(
            temperature_metric=temperature_metric,
            observed_high_so_far=float(candidate.observation.high_so_far) if candidate.observation.high_so_far is not None else None,
            observed_low_so_far=float(candidate.observation.low_so_far) if candidate.observation.low_so_far is not None else None,
            current_temp=float(candidate.observation.current_temp),
            hours_remaining=hours_remaining,
            member_maxes_remaining=extrema.maxes,
            member_mins_remaining=extrema.mins,
            unit=city.settlement_unit,
            observation_source=str(candidate.observation.source),
            observation_time=candidate.observation.observation_time,
            temporal_context=temporal_context,
            round_fn=settlement_semantics.round_values,
            causality_status=causality_status,
        ))
        p_raw = day0.p_vector(bins)
        day0_forecast_context = day0.forecast_context()
        raw_arr = extrema.maxes if extrema.maxes is not None else extrema.mins
        ensemble_spread = TemperatureDelta(
            float(np.std(raw_arr)), city.settlement_unit
        )
        entry_validations = ["day0_observation", "ens_fetch", "mc_instrument_noise", "diurnal_peak"]
        lead_days_for_calibration = 0.0
    else:
        p_raw = ens.p_raw_vector(bins)
        day0_forecast_context = None
        ensemble_spread = ens.spread()
        entry_validations = ["ens_fetch", "mc_instrument_noise"]
        lead_days_for_calibration = lead_days

    # Store ENS snapshot AFTER all semantic gates pass (#67 — no write-before-validate)
    snapshot_id = _store_ens_snapshot(conn, city, target_date, ens, ens_result)
    _store_snapshot_p_raw(conn, snapshot_id, p_raw, bias_corrected=ens.bias_corrected)

    # Calibration
    # K4 authority gate: verify no UNVERIFIED pairs are present for this bucket.
    # get_pairs_for_bucket defaults to authority='VERIFIED', so this check catches
    # any situation where UNVERIFIED rows are present (belt-and-suspenders).
    # Guard: skip if conn is None/unavailable (test stubs that don't provide a real DB).
    _authority_verified = False  # K1/#68: track whether gate actually ran and passed
    if conn is not None and hasattr(conn, 'execute'):
        from src.calibration.store import get_pairs_for_bucket as _get_pairs
        _cal_season = season_from_date(target_date, lat=city.lat)
        try:
            # Slice P2-A1 (PR #19 phase 2, 2026-04-26): scope contamination
            # check to the active metric track. Without metric, this gate
            # would alert on cross-metric UNVERIFIED rows that don't actually
            # affect this candidate's refit (HIGH eval shouldn't be blocked
            # by LOW UNVERIFIED noise). Pass metric only for HIGH (slice A1
            # raises NotImplementedError on legacy metric="low" reads;
            # LOW callers retain the broader unscoped check via metric=None,
            # which is correct since LOW writes don't go to legacy table).
            # Slice P2-fix5 (post-review MINOR #8 from code-reviewer, 2026-04-26):
            # use the typed `is_high()` helper rather than string-compare on
            # the inner attribute; that's why the typed atom exists.
            _gate_metric = "high" if temperature_metric.is_high() else None
            _unverified_pairs = _get_pairs(
                conn, city.cluster, _cal_season,
                authority_filter='UNVERIFIED',
                metric=_gate_metric,
            )
        except Exception as e:
            return [EdgeDecision(
                decision_id=_generate_decision_id(),
                tokens=tokens,
                edge=None,
                size_usd=0.0,
                should_trade=False,
                rejection_reasons=["authority gate failed due to DB query fault"],
                rejection_stage="AUTHORITY_GATE",
                availability_status="DATA_UNAVAILABLE",
                decision_snapshot_id=snapshot_id,
                selected_method="unknown",
            )]
        if _unverified_pairs:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="AUTHORITY_GATE",
                rejection_reasons=[
                    f"insufficient_verified_calibration: "
                    f"{len(_unverified_pairs)} UNVERIFIED calibration rows present "
                    f"for {city.name}/{_cal_season}"
                ],
                availability_status="DATA_STALE",
                selected_method=selected_method,
                applied_validations=entry_validations,
                decision_snapshot_id=snapshot_id,
                p_raw=p_raw,
            )]
        _authority_verified = True  # K1/#68: gate ran and no UNVERIFIED rows found
    # L3 Phase 9C: metric-aware calibrator lookup. `temperature_metric` is
    # MetricIdentity (normalized at L662 via _normalize_temperature_metric);
    # pull the string attribute for the kwarg.
    cal, cal_level = get_calibrator(
        conn, city, target_date,
        temperature_metric=temperature_metric.temperature_metric,
    )
    if cal is not None:
        p_cal = calibrate_and_normalize(
            p_raw,
            cal,
            lead_days_for_calibration,
            bin_widths=[b.width for b in bins],
        )
        entry_validations.extend(["platt_calibration", "normalization", "authority_verified"])
    else:
        # No calibration data is consumed on the uncalibrated path, so the
        # market-fusion authority gate is not applicable to Platt rows.
        _authority_verified = True
        p_cal = p_raw.copy()

    # Market prices via VWMP
    p_market = np.zeros(len(bins))
    market_is_complete = True
    mapped_outcomes = 0
    for i, o in enumerate(outcomes):
        if o["range_low"] is None and o["range_high"] is None:
            continue
        idx = next((j for j, b in enumerate(bins) if b.label == o["title"]), None)
        if idx is None:
            market_is_complete = False
            continue
        try:
            bid, ask, bid_sz, ask_sz = clob.get_best_bid_ask(o["token_id"])
            p_market[idx] = vwmp(bid, ask, bid_sz, ask_sz)

            # Injection Point 7: Data completeness - record microstructure snapshot
            try:
                import datetime as dt
                from src.state.db import log_microstructure
                log_microstructure(
                    conn,
                    token_id=o["token_id"],
                    city=city.name,
                    target_date=target_d.isoformat(),
                    range_label=bins[idx].label,
                    price=float(p_market[idx]),
                    volume=float(bid_sz + ask_sz),
                    bid=float(bid),
                    ask=float(ask),
                    spread=round(float(ask - bid), 4) if ask >= bid else 0.0,
                    source_timestamp=dt.datetime.now(dt.timezone.utc).isoformat(),
                )
            except Exception as micro_exc:
                logger.warning("Microstructure log DB insert failed for %s: %s", o["token_id"], micro_exc)
        except Exception as e:
            try:
                from src.contracts.exceptions import EmptyOrderbookError
            except ImportError:
                EmptyOrderbookError = type("Dummy", (Exception,), {})
            if isinstance(e, EmptyOrderbookError) or e.__class__.__name__ == "EmptyOrderbookError":
                logger.warning("Empty orderbook detected: %s", e)
                return [EdgeDecision(
                    False,
                    decision_id=_decision_id(),
                    rejection_stage="MARKET_LIQUIDITY",
                    rejection_reasons=[str(e)],
                    availability_status="DATA_UNAVAILABLE",
                    selected_method=selected_method,
                    applied_validations=entry_validations,
                    decision_snapshot_id=snapshot_id,
                    p_raw=p_raw,
                    p_cal=p_cal,
                    p_market=p_market,
                )]
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="MARKET_LIQUIDITY",
                rejection_reasons=[str(e)],
                availability_status=_availability_status_for_error(e),
                selected_method=selected_method,
                applied_validations=entry_validations,
                decision_snapshot_id=snapshot_id,
                p_raw=p_raw,
                p_cal=p_cal,
                p_market=p_market,
            )]

    agreement = "AGREE"
    if not is_day0_mode:
        try:
            gfs_result = fetch_ensemble(city, forecast_days=ens_forecast_days, model="gfs025")
        except Exception as e:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=[f"GFS crosscheck unavailable: {e}"],
                availability_status=_availability_status_for_error(e),
                selected_method=selected_method,
                applied_validations=[*entry_validations, "gfs_crosscheck_unavailable"],
                decision_snapshot_id=snapshot_id,
                p_raw=p_raw,
                p_cal=p_cal,
                p_market=p_market,
                agreement="CROSSCHECK_UNAVAILABLE",
            )]
        if gfs_result is None or not validate_ensemble(
            gfs_result,
            expected_members=ensemble_crosscheck_member_count(),
        ):
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=["GFS crosscheck unavailable"],
                availability_status="DATA_UNAVAILABLE",
                selected_method=selected_method,
                applied_validations=[*entry_validations, "gfs_crosscheck_unavailable"],
                decision_snapshot_id=snapshot_id,
                p_raw=p_raw,
                p_cal=p_cal,
                p_market=p_market,
                agreement="CROSSCHECK_UNAVAILABLE",
            )]
        try:
            gfs_tz_hours = select_hours_for_target_date(
                target_d,
                city.timezone,
                times=gfs_result["times"],
            )
            gfs_metric_values = (
                gfs_result["members_hourly"][:, gfs_tz_hours].min(axis=1)
                if temperature_metric.is_low()
                else gfs_result["members_hourly"][:, gfs_tz_hours].max(axis=1)
            )
            gfs_measured = settlement_semantics.round_values(gfs_metric_values)
            n_gfs = len(gfs_measured)
            gfs_p = np.zeros(len(bins))
            for i, b in enumerate(bins):
                if b.is_open_low:
                    gfs_p[i] = np.sum(gfs_measured <= b.high) / n_gfs
                elif b.is_open_high:
                    gfs_p[i] = np.sum(gfs_measured >= b.low) / n_gfs
                elif b.low is not None and b.high is not None:
                    gfs_p[i] = np.sum((gfs_measured >= b.low) & (gfs_measured <= b.high)) / n_gfs
            total = gfs_p.sum()
            if total > 0:
                gfs_p /= total
            agreement = model_agreement(p_raw, gfs_p)
        except Exception as e:
            logger.warning("GFS crosscheck failed: %s", e)
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=[f"GFS crosscheck unavailable: {e}"],
                availability_status="DATA_UNAVAILABLE",
                selected_method=selected_method,
                applied_validations=[*entry_validations, "gfs_crosscheck_unavailable"],
                decision_snapshot_id=snapshot_id,
                p_raw=p_raw,
                p_cal=p_cal,
                p_market=p_market,
                agreement="CROSSCHECK_UNAVAILABLE",
            )]

    if agreement == "CONFLICT":
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=["ECMWF/GFS CONFLICT"],
            selected_method=selected_method,
            applied_validations=[*entry_validations, "model_agreement"],
            decision_snapshot_id=snapshot_id,
            p_raw=p_raw,
            p_cal=p_cal,
            p_market=p_market,
            agreement=agreement,
        )]

    # Compute alpha — UNION resolution: K4.5 authority_verified gate (worktree)
    # + consumer-target gating with AlphaTargetMismatchError handling (data-improve).
    # K1/#68: authority_verified now tracks whether the gate actually ran and passed,
    # instead of being hardcoded True.
    try:
        alpha = compute_alpha(
            calibration_level=cal_level,
            ensemble_spread=ensemble_spread,
            model_agreement=agreement,
            lead_days=lead_days_for_calibration,
            hours_since_open=candidate.hours_since_open,
            authority_verified=_authority_verified,
        ).value_for_consumer("ev")
    except AlphaTargetMismatchError as exc:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=[f"ALPHA_TARGET_MISMATCH:{exc}"],
            availability_status="DATA_UNAVAILABLE",
            selected_method=selected_method,
            applied_validations=[*entry_validations, "alpha_target_contract"],
            decision_snapshot_id=snapshot_id,
            p_raw=p_raw,
            p_cal=p_cal,
            p_market=p_market,
            agreement=agreement,
        )]
    except AuthorityViolation as exc:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="AUTHORITY_GATE",
            rejection_reasons=[f"AUTHORITY_VIOLATION:{exc}"],
            availability_status="DATA_STALE",
            selected_method=selected_method,
            applied_validations=[*entry_validations, "authority_contract"],
            decision_snapshot_id=snapshot_id,
            p_raw=p_raw,
            p_cal=p_cal,
            p_market=p_market,
            agreement=agreement,
        )]
    if not is_day0_mode:
        entry_validations.append("model_agreement")
    entry_validations.append("alpha_posterior")

    forecast_source = _forecast_source_key(ens_result.get("model"))
    season = season_from_date(target_date, lat=city.lat)
    bias_reference = _load_model_bias_reference(
        conn,
        city_name=city.name,
        season=season,
        forecast_source=forecast_source,
    )

    # Edge detection
    # Flag missing mapped outcomes against the declared family topology
    if sum(1 for p in p_market if p > 0.0) < len(bins):
        market_is_complete = False

    analysis = MarketAnalysis(
        p_raw=p_raw,
        p_cal=p_cal,
        p_market=p_market,
        alpha=alpha,
        bins=bins,
        member_maxes=ens.member_extrema,
        calibrator=cal,
        lead_days=lead_days_for_calibration,
        unit=city.settlement_unit,
        round_fn=settlement_semantics.round_values,
        city_name=city.name,
        season=season,
        forecast_source=forecast_source,
        bias_corrected=bool(getattr(ens, "bias_corrected", False)),
        market_complete=market_is_complete,
        bias_reference=bias_reference,
    )
    if hasattr(analysis, "forecast_context"):
        forecast_context = analysis.forecast_context()
    else:
        forecast_context = {
            "uncertainty": analysis.sigma_context(),
            "location": analysis.mean_context(),
        }
    if day0_forecast_context is not None:
        forecast_context["day0"] = day0_forecast_context
    n_bootstrap = edge_n_bootstrap()
    edges = analysis.find_edges(n_bootstrap=n_bootstrap)
    _fdr_fallback = False
    try:
        full_family_hypotheses = scan_full_hypothesis_family(analysis, n_bootstrap=n_bootstrap)
    except Exception as exc:
        logger.error("Full-family hypothesis scan unavailable; failing closed for entry selection: %s", exc)
        _fdr_fallback = True
        full_family_hypotheses = []
    _fdr_family_size = len(full_family_hypotheses)
    entry_validations.append("bootstrap_ci")

    # FDR filter — full-family is the live standard.
    # Legacy fdr_filter() is preserved for audit/comparison recording only.
    legacy_filtered = fdr_filter(edges)
    if _fdr_fallback:
        filtered = []
    elif full_family_hypotheses:
        selected_edge_keys = _selected_edge_keys_from_full_family(
            candidate,
            full_family_hypotheses,
            decision_snapshot_id=snapshot_id,
        )
        filtered = [
            edge for edge in edges
            if (edge.bin.label, edge.direction) in selected_edge_keys
        ]
    else:
        # Full-family scan succeeded but returned zero hypotheses — anomalous
        # (any valid market has ≥1 bin × 2 directions). Fail closed instead of
        # silently falling back to the legacy denominator-undercount path.
        logger.warning(
            "Full-family scan returned 0 hypotheses for %s/%s; failing closed",
            candidate.city.name, candidate.target_date,
        )
        _fdr_fallback = True
        filtered = []
    entry_validations.append("fdr_filter")
    try:
        # B091: if decision_time was not forwarded from the cycle (tests or
        # degraded callers), DO NOT silently fabricate a fresh `now()` for
        # `recorded_at` and pretend it is the cycle's decision moment.
        # Fabrication is permitted as a last resort but MUST be observable.
        # decision_time_status extends the P9C vocab (replay.py "OK" / "SYNTHETIC_MIDDAY")
        # into the evaluator path. See B091 lower half.
        if decision_time is not None:
            _recorded_at = decision_time.isoformat()
            _decision_time_status = "OK"
        else:
            _fabricated_now = datetime.now(timezone.utc)
            logger.warning(
                "DECISION_TIME_FABRICATED_AT_SELECTION_FAMILY: city=%s target_date=%s snapshot_id=%s recorded_at=%s",
                candidate.city.name,
                candidate.target_date,
                snapshot_id,
                _fabricated_now,
            )
            _recorded_at = _fabricated_now.isoformat()
            _decision_time_status = "FABRICATED_SELECTION_FAMILY"
        _record_selection_family_facts(
            conn,
            candidate=candidate,
            edges=edges,
            filtered=filtered,
            hypotheses=full_family_hypotheses or None,
            decision_snapshot_id=snapshot_id,
            selected_method=selected_method,
            recorded_at=_recorded_at,
            decision_time_status=_decision_time_status,
        )
    except Exception as exc:
        logger.warning("Failed to record selection family facts: %s", exc)

    if not filtered:
        if _fdr_fallback:
            stage = "FDR_FAMILY_SCAN_UNAVAILABLE"
            rejection_reasons = ["full-family FDR scan unavailable; entry selection failed closed"]
        else:
            stage = "EDGE_INSUFFICIENT" if not edges else "FDR_FILTERED"
            rejection_reasons = [f"{len(edges)} edges found, {len(filtered)} passed FDR"]
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage=stage,
            rejection_reasons=rejection_reasons,
            selected_method=selected_method,
            applied_validations=list(entry_validations),
            decision_snapshot_id=snapshot_id,
            p_raw=p_raw,
            p_cal=p_cal,
            p_market=p_market,
            alpha=alpha,
            agreement=agreement,
            spread=float(getattr(ensemble_spread, "value", ens.spread_float())),
            n_edges_found=len(edges),
            n_edges_after_fdr=0,
            fdr_fallback_fired=_fdr_fallback,
            fdr_family_size=_fdr_family_size,
        )]

    bankroll_val = getattr(portfolio, "effective_bankroll", getattr(portfolio, "bankroll", 0.0)) if entry_bankroll is None else entry_bankroll
    sizing_bankroll = max(0.0, float(bankroll_val))
    current_heat = portfolio_heat_for_bankroll(portfolio, sizing_bankroll)
    projected_total_exposure_usd = current_heat * sizing_bankroll
    projected_city_exposure_usd: dict[str, float] = defaultdict(float)
    projected_cluster_exposure_usd: dict[str, float] = defaultdict(float)

    decisions = []
    for edge in filtered:
        bin_idx = bins.index(edge.bin)
        tokens = token_map[bin_idx]
        decision_validations = list(entry_validations)
        edge_source = _edge_source_for(candidate, edge)
        strategy_key = _strategy_key_for(candidate, edge)
        ci_rejection_reason = _entry_ci_rejection_reason(candidate, edge)
        if ci_rejection_reason is not None:
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="EDGE_INSUFFICIENT",
                rejection_reasons=[ci_rejection_reason],
                selected_method=selected_method,
                applied_validations=[*decision_validations, "confidence_band_guard"],
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
                strategy_key=strategy_key,
            ))
            continue
        # B091: strategy-policy time reference. Same contract as the
        # recorded_at fabrication above: fall back to now() when the cycle
        # did not provide a decision_time, but emit a structured WARNING
        # so the fabrication is observable and not silently blended into
        # policy resolution.
        if decision_time is not None:
            policy_now = decision_time
        else:
            policy_now = datetime.now(timezone.utc)
            logger.warning(
                "DECISION_TIME_FABRICATED_AT_STRATEGY_POLICY: strategy_key=%s policy_now=%s",
                strategy_key,
                policy_now,
            )
        policy = (
            resolve_strategy_policy(conn, strategy_key, policy_now)
            if conn is not None
            else _default_strategy_policy(strategy_key)
        )
        decision_validations.append("strategy_policy")

        ultra_low_price_reason = _center_buy_ultra_low_price_block_reason(strategy_key, edge)
        if ultra_low_price_reason:
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="MARKET_FILTER",
                rejection_reasons=[ultra_low_price_reason],
                selected_method=selected_method,
                applied_validations=[*decision_validations, "center_buy_ultra_low_price_guard"],
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
                strategy_key=strategy_key,
            ))
            continue

        # Anti-churn layers 5, 6, 7
        if is_reentry_blocked(portfolio, city.name, edge.bin.label, target_date):
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="ANTI_CHURN",
                rejection_reasons=["REENTRY_BLOCKED"],
                selected_method=selected_method,
                applied_validations=[*decision_validations, "anti_churn"],
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
                strategy_key=strategy_key,
            ))
            continue
        check_token = tokens["token_id"] if edge.direction == "buy_yes" else tokens["no_token_id"]
        if is_token_on_cooldown(portfolio, check_token):
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="ANTI_CHURN",
                rejection_reasons=["TOKEN_COOLDOWN"],
                selected_method=selected_method,
                applied_validations=[*decision_validations, "anti_churn"],
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
                strategy_key=strategy_key,
            ))
            continue
        if has_same_city_range_open(portfolio, city.name, edge.bin.label):
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="ANTI_CHURN",
                rejection_reasons=["CROSS_DATE_BLOCK"],
                selected_method=selected_method,
                applied_validations=[*decision_validations, "anti_churn"],
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
                strategy_key=strategy_key,
            ))
            continue

        # Oracle penalty gate — blacklisted cities skip trading entirely
        # S2 R4 P10B: pass temperature_metric so LOW candidates use separate oracle info
        oracle = get_oracle_info(city.name, temperature_metric.temperature_metric)
        if oracle.status == OracleStatus.BLACKLIST:
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="ORACLE_BLACKLISTED",
                rejection_reasons=[
                    f"oracle_error_rate={oracle.error_rate:.1%} > 10% — city blacklisted"
                ],
                selected_method=selected_method,
                applied_validations=[*decision_validations, "oracle_penalty"],
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
                strategy_key=strategy_key,
            ))
            continue

        # Kelly sizing
        decision_validations.extend(["kelly_sizing", "dynamic_multiplier"])
        if oracle.status == OracleStatus.CAUTION:
            decision_validations.append(
                f"oracle_penalty_{oracle.penalty_multiplier:.2f}x"
            )
        current_heat = (
            projected_total_exposure_usd / sizing_bankroll
            if sizing_bankroll > 0
            else 0.0
        )
        
        # Phase 3: RiskGraph Regime Throttling (K3: cluster == city.name)
        current_cluster_exp = cluster_exposure_for_bankroll(portfolio, city.name, sizing_bankroll)
        risk_throttle = 1.0
        if current_cluster_exp > 0.10: # Regime saturation starts
            risk_throttle *= 0.5
            decision_validations.append("regime_throttled_50pct")
        if current_heat > 0.25: # Global heat saturation 
            risk_throttle *= 0.5
            decision_validations.append("global_heat_throttled_50pct")

        try:
            km = dynamic_kelly_mult(
                base=settings["sizing"]["kelly_multiplier"],
                ci_width=edge.ci_upper - edge.ci_lower,
                lead_days=lead_days_for_calibration,
                portfolio_heat=current_heat,
            )
        except ValueError as exc:
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="SIZING_ERROR",
                rejection_reasons=[str(exc)],
                selected_method=selected_method,
                applied_validations=list(decision_validations),
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
                strategy_key=strategy_key,
            ))
            continue
        if policy.gated or policy.exit_only:
            reason = "POLICY_EXIT_ONLY" if policy.exit_only else "POLICY_GATED"
            if policy.sources:
                reason = f"{reason}({','.join(policy.sources)})"
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="RISK_REJECTED",
                rejection_reasons=[reason],
                selected_method=selected_method,
                applied_validations=list(decision_validations),
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
                strategy_key=strategy_key,
            ))
            continue
        if policy.threshold_multiplier > 1.0:
            km = km / policy.threshold_multiplier
            decision_validations.append(f"strategy_policy_threshold_{policy.threshold_multiplier:g}x")

        # Oracle penalty: reduce Kelly for CAUTION cities (3–10% error rate)
        if oracle.penalty_multiplier < 1.0:
            km *= oracle.penalty_multiplier
            decision_validations.append(f"strategy_policy_threshold_{policy.threshold_multiplier:g}x")
        
        # F2/D3: ExecutionPrice contract — compute fee-adjusted entry cost.
        try:
            fee_rate = _fee_rate_for_token(clob, check_token)
        except FeeRateUnavailableError as exc:
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="EXECUTION_PRICE_UNAVAILABLE",
                rejection_reasons=[str(exc)],
                availability_status="DATA_UNAVAILABLE",
                selected_method=selected_method,
                applied_validations=list(decision_validations),
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
                strategy_key=strategy_key,
            ))
            continue
        try:
            size = _size_at_execution_price_boundary(
                p_posterior=edge.p_posterior,
                entry_price=edge.entry_price,
                fee_rate=fee_rate,
                sizing_bankroll=sizing_bankroll,
                kelly_multiplier=km * risk_throttle,
                safety_cap_usd=settings["live_safety_cap_usd"],
            )
        except ValueError as exc:
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="SIZING_ERROR",
                rejection_reasons=[str(exc)],
                selected_method=selected_method,
                applied_validations=list(decision_validations),
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
                strategy_key=strategy_key,
            ))
            continue
        if policy.allocation_multiplier != 1.0:
            size *= policy.allocation_multiplier
            decision_validations.append(f"strategy_policy_allocation_{policy.allocation_multiplier:g}x")

        if size < limits.min_order_usd:
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="SIZING_TOO_SMALL",
                rejection_reasons=[f"${size:.2f} < ${limits.min_order_usd} (throttled: {risk_throttle})"],
                selected_method=selected_method,
                applied_validations=list(decision_validations),
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
                strategy_key=strategy_key,
            ))
            continue

        # Risk limits
        decision_validations.append("risk_limits")
        allowed, reason = check_position_allowed(
            size_usd=size,
            bankroll=sizing_bankroll,
            city=city.name,
            current_city_exposure=(
                city_exposure_for_bankroll(portfolio, city.name, sizing_bankroll)
                + (projected_city_exposure_usd[city.name] / sizing_bankroll if sizing_bankroll > 0 else 0.0)
            ),
            current_portfolio_heat=current_heat,
            limits=limits,
        )
        if not allowed:
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="RISK_REJECTED",
                rejection_reasons=[reason],
                selected_method=selected_method,
                applied_validations=list(decision_validations),
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
                strategy_key=strategy_key,
            ))
            continue

        edge_ctx = EdgeContext(
            p_raw=p_raw,
            p_cal=p_cal,
            p_market=p_market,
            p_posterior=edge.p_posterior,
            forward_edge=edge.forward_edge,
            alpha=alpha,
            confidence_band_upper=edge.ci_upper,
            confidence_band_lower=edge.ci_lower,
            entry_provenance=EntryMethod(selected_method),
            decision_snapshot_id=snapshot_id,
            n_edges_found=len(edges),
            n_edges_after_fdr=len(filtered),
        )

        # T4.1b 2026-04-23 (D4 Option E): capture entry-side DecisionEvidence
        # here — the single `should_trade=True` EdgeDecision accept site in
        # this file. `sample_size` sources from the shared bootstrap-count
        # helper (src.config.edge_n_bootstrap) so the evidence reflects the
        # exact count used for the family FDR scan. `confidence_level` sources
        # from DEFAULT_FDR_ALPHA (src.strategy.fdr_filter:19, backed by
        # settings["edge"]["fdr_alpha"]) so any α tuning in config propagates
        # here without code edits. `consecutive_confirmations=1` = 1 robust
        # confirmation (CI_lower > 0 across n_bootstrap draws) per the D4
        # contract docstring; exit-side `consecutive_confirmations>=1` is the
        # symmetry floor enforced by `assert_symmetric_with`.
        entry_evidence = DecisionEvidence(
            evidence_type="entry",
            statistical_method="bootstrap_ci_bh_fdr",
            sample_size=edge_n_bootstrap(),
            confidence_level=DEFAULT_FDR_ALPHA,
            fdr_corrected=True,
            consecutive_confirmations=1,
        )
        decisions.append(EdgeDecision(
            should_trade=True,
            edge=edge,
            tokens=tokens,
            size_usd=size,
            decision_id=_decision_id(),
            selected_method=selected_method,
            applied_validations=[*decision_validations, "anti_churn"],
            decision_snapshot_id=snapshot_id,
            edge_source=edge_source,
            strategy_key=strategy_key,
            p_raw=p_raw,
            p_cal=p_cal,
            p_market=p_market,
            alpha=alpha,
            agreement=agreement,
            spread=float(getattr(ensemble_spread, "value", ens.spread_float())),
            n_edges_found=len(edges),
            n_edges_after_fdr=len(filtered),
            edge_context=edge_ctx,
            settlement_semantics_json=_serialize_json(settlement_semantics),
            epistemic_context_json=_serialize_json({
                **_to_jsonable(epistemic),
                "forecast_context": forecast_context,
            }),
            edge_context_json=_serialize_json(edge_ctx),
            decision_evidence=entry_evidence,
        ))
        projected_total_exposure_usd += size
        projected_city_exposure_usd[city.name] += size
        projected_cluster_exposure_usd[city.name] += size

    if _fdr_fallback or _fdr_family_size:
        from dataclasses import replace
        decisions = [replace(d, fdr_fallback_fired=_fdr_fallback, fdr_family_size=_fdr_family_size) for d in decisions]
    return decisions


def _snapshot_time_value(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _snapshot_issue_time_value(ens_result: dict) -> Optional[str]:
    issue_time = _snapshot_time_value(ens_result.get("issue_time"))
    if issue_time is not None:
        return issue_time

    # Return None instead of synthetic sentinels for missing issue_time
    return None


def _snapshot_valid_time_value(target_date: str, ens_result: dict) -> Optional[str]:
    valid_time = _snapshot_time_value(ens_result.get("valid_time"))
    if valid_time is not None:
        return valid_time

    # Return None instead of synthetic sentinels for missing valid_time
    return None


def _ensemble_snapshots_table(conn) -> str:
    try:
        row = conn.execute(
            "SELECT 1 FROM world.sqlite_master WHERE type = 'table' AND name = 'ensemble_snapshots'"
        ).fetchone()
    except Exception:
        return "ensemble_snapshots"
    return "world.ensemble_snapshots" if row is not None else "ensemble_snapshots"


def _store_ens_snapshot(conn, city, target_date, ens, ens_result) -> str:
    """Store every ENS fetch and return the snapshot_id."""

    import json

    try:
        snapshots_table = _ensemble_snapshots_table(conn)
        issue_time_value = _snapshot_issue_time_value(ens_result)
        valid_time_value = _snapshot_valid_time_value(target_date, ens_result)
        fetch_time_value = _snapshot_time_value(ens_result.get("fetch_time"))
        if fetch_time_value is None:
            raise ValueError("ENS snapshot missing fetch_time")

        # P10D S3: stamp temperature_metric on each snapshot row so LOW rows
        # are distinguishable from HIGH rows in the legacy table.
        # ens.temperature_metric is a MetricIdentity — extract the string value.
        # Slice A3 (PR #19 finding 7, 2026-04-26): pre-A3 had a double-getattr
        # `or "high"` fallback that silently stamped HIGH on any snapshot whose
        # ens object lacked metric identity. That hid LOW writers and any
        # malformed upstream as HIGH in the canonical ensemble_snapshots table —
        # the same table the calibration_pairs replay reads back. Now fail
        # closed at the writer seam: refuse the INSERT rather than mis-stamp.
        _metric_identity = getattr(ens, "temperature_metric", None)
        snap_metric = getattr(_metric_identity, "temperature_metric", None) if _metric_identity is not None else None
        if snap_metric not in ("high", "low"):
            raise ValueError(
                "_store_ens_snapshot requires ens.temperature_metric to be a "
                "MetricIdentity with temperature_metric in {'high','low'}; "
                f"got ens.temperature_metric={_metric_identity!r}. Refusing to "
                "silently stamp 'high' on a snapshot whose upstream identity "
                "is missing or malformed (PR #19 F7 antibody)."
            )
        logger.debug("snapshot_metric=%s city=%s date=%s", snap_metric, city.name, target_date)

        conn.execute(f"""
            INSERT OR IGNORE INTO {snapshots_table}
            (city, target_date, issue_time, valid_time, available_at, fetch_time,
             lead_hours, members_json, spread, is_bimodal, model_version, data_version,
             temperature_metric)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            city.name,
            target_date,
            issue_time_value,
            valid_time_value,
            fetch_time_value,
            fetch_time_value,
            max(
                0.0,
                lead_hours_to_date_start(
                    target_date,
                    city.timezone,
                    ens_result.get("fetch_time"),
                ),
            ),
            # S3e: member_extrema stores HIGH maxes for HIGH rows and LOW mins for LOW rows.
            # Downstream (rebuild_calibration_pairs*) filters by temperature_metric column
            # to route to the correct settlement semantics. Do NOT use members_json without
            # checking temperature_metric first.
            json.dumps(ens.member_extrema.tolist()),
            ens.spread_float(),
            int(ens.is_bimodal()),
            ens_result["model"],
            "live_v1",
            snap_metric,
        ))
        row = conn.execute(f"""
            SELECT snapshot_id FROM {snapshots_table}
            WHERE city = ? AND target_date = ? AND issue_time = ? AND data_version = ?
            LIMIT 1
        """, (
            city.name,
            target_date,
            issue_time_value,
            "live_v1",
        )).fetchone()
        conn.commit()
        return str(row["snapshot_id"]) if row is not None else ""
    except Exception as e:
        logger.warning("Failed to store ENS snapshot: %s", e)
        return ""


def _store_snapshot_p_raw(conn, snapshot_id: str, p_raw: np.ndarray, *, bias_corrected: bool = False) -> None:
    """Persist the decision-time p_raw vector and bias_corrected flag onto the snapshot row."""

    if not snapshot_id:
        return

    import json

    try:
        snapshots_table = _ensemble_snapshots_table(conn)
        conn.execute(
            f"UPDATE {snapshots_table} SET p_raw_json = ?, bias_corrected = ? WHERE snapshot_id = ?",
            (json.dumps(p_raw.tolist()), int(bias_corrected), snapshot_id),
        )
        conn.commit()
    except Exception as e:
        logger.warning("Failed to store snapshot p_raw for %s: %s", snapshot_id, e)
