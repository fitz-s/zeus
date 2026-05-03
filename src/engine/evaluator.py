"""Evaluator: takes a market candidate, returns an EdgeDecision or NoTradeCase.

Contains ALL business logic for edge detection. Doesn't know about scheduling,
portfolio state, or execution. Pure function: candidate -> decision.
"""

import json
import logging
import hashlib
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from types import SimpleNamespace
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

import numpy as np

if TYPE_CHECKING:
    from src.data.observation_client import Day0ObservationContext

from src.calibration.manager import edge_threshold_multiplier, get_calibrator
from src.calibration.manager import season_from_date
from src.calibration.platt import calibrate_and_normalize
from src.config import (
    CONFIG_DIR,
    PROJECT_ROOT,
    City,
    EntryForecastConfig,
    EntryForecastRolloutMode,
    day0_n_mc,
    edge_n_bootstrap,
    ensemble_crosscheck_member_count,
    ensemble_crosscheck_model,
    ensemble_member_count,
    ensemble_n_mc,
    ensemble_primary_model,
    entry_forecast_config,
    get_mode,
    settings,
)
from src.data.executable_forecast_reader import read_executable_forecast
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
from src.data.forecast_fetch_plan import data_version_for_track, track_for_metric
from src.engine.time_context import lead_days_to_date_start, lead_hours_to_date_start
from src.signal.day0_router import Day0Router, Day0SignalInputs
from src.signal.day0_window import remaining_member_extrema_for_day0
from src.signal.ensemble_signal import EnsembleSignal, p_raw_vector_from_maxes, select_hours_for_target_date
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
from src.strategy.kelly import dynamic_kelly_mult, kelly_size, strategy_kelly_multiplier
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
from src.contracts.ensemble_snapshot_provenance import assert_data_version_allowed, validate_members_unit
from src.contracts.executable_market_snapshot_v2 import (
    MarketSnapshotMismatchError,
    canonicalize_legacy_fee_rate_value,
    canonicalize_fee_details,
    fee_rate_fraction_from_details,
)
from src.contracts.execution_price import ExecutionPrice, polymarket_fee
from src.contracts.alpha_decision import AlphaTargetMismatchError
from src.data.forecast_source_registry import SourceNotEnabled
from src.strategy.market_analysis import MarketAnalysis
from src.strategy.market_fusion import (
    AuthorityViolation,
    MODEL_ONLY_POSTERIOR_MODE,
    compute_alpha,
    vwmp,
)
from src.strategy.risk_limits import RiskLimits, check_position_allowed
from src.types import Bin, BinEdge
from src.types.market import BinTopologyError, validate_bin_topology
from src.types.temperature import TemperatureDelta

logger = logging.getLogger(__name__)
CENTER_BUY_ULTRA_LOW_PRICE_MAX_ENTRY = 0.02
DAY0_EXECUTABLE_OBSERVATION_SOURCES_BY_SETTLEMENT_TYPE = {
    "wu_icao": frozenset({"wu_api"}),
}
DAY0_EXECUTABLE_OBSERVATION_MAX_AGE_HOURS = 1.0
DAY0_EXECUTABLE_OBSERVATION_FUTURE_TOLERANCE_SECONDS = 60.0
NATIVE_MULTIBIN_BUY_NO_SHADOW_FLAG = "NATIVE_MULTIBIN_BUY_NO_SHADOW"
NATIVE_MULTIBIN_BUY_NO_LIVE_FLAG = "NATIVE_MULTIBIN_BUY_NO_LIVE"
NATIVE_BUY_NO_QUOTE_AVAILABLE_VALIDATION = "buy_no_native_quote_available"
NATIVE_BUY_NO_QUOTE_UNAVAILABLE_VALIDATION = "buy_no_native_quote_unavailable"


class FeeRateUnavailableError(RuntimeError):
    """Raised when token-specific execution fee cannot be established."""


def _strict_feature_flag(name: str, *, default: bool = False) -> bool:
    """Read a boolean feature flag, failing closed on malformed values."""

    flags = settings["feature_flags"]
    value = flags.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"feature flag {name} must be boolean, got {type(value).__name__}")
    return bool(value)


def native_multibin_buy_no_shadow_enabled() -> bool:
    return _strict_feature_flag(NATIVE_MULTIBIN_BUY_NO_SHADOW_FLAG)


def native_multibin_buy_no_live_enabled() -> bool:
    shadow_enabled = native_multibin_buy_no_shadow_enabled()
    live_enabled = _strict_feature_flag(NATIVE_MULTIBIN_BUY_NO_LIVE_FLAG)
    if live_enabled and not shadow_enabled:
        raise ValueError(
            f"{NATIVE_MULTIBIN_BUY_NO_LIVE_FLAG}=true requires "
            f"{NATIVE_MULTIBIN_BUY_NO_SHADOW_FLAG}=true"
        )
    return live_enabled


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
    sizing_bankroll: float = 0.0
    kelly_multiplier_used: float = 0.0
    execution_fee_rate: float = 0.0
    safety_cap_usd: float | None = None

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

    Decision-time ordering: executable gates may only read the exact
    decision_snapshot_id written for this candidate. A city/date/metric
    "latest" fallback can be a different fetch cycle and is not evidence
    for the current decision.

    Returns:
        dict with `boundary_ambiguous`, `causality_status`, and `snapshot_id`
        keys when row exists; empty dict when row is absent OR v2 table is
        not present OR no snapshot_id is supplied (backward compat for
        legacy-only databases and pre-persistence callers).
    """
    if not snapshot_id:
        return {}

    # Resolve schema prefix (world.ensemble_snapshots_v2 when world DB
    # attached; bare ensemble_snapshots_v2 in monolithic test DBs).
    import sqlite3
    for sp in ("world.", ""):
        try:
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


def _day0_observation_source_rejection_reason(
    city: City,
    observation: "Day0ObservationContext",
    *,
    consumer_label: str = "executable entry",
) -> str | None:
    settlement_source_type = str(
        getattr(city, "settlement_source_type", "") or ""
    ).strip()
    if isinstance(observation, dict):
        source_raw = observation.get("source")
    else:
        source_raw = getattr(observation, "source", "")
    source = str(source_raw or "").strip()
    allowed_sources = DAY0_EXECUTABLE_OBSERVATION_SOURCES_BY_SETTLEMENT_TYPE.get(
        settlement_source_type
    )
    if allowed_sources is None:
        return (
            f"Day0 observation source role is not authorized for {consumer_label}: "
            f"city={city.name} settlement_source_type={settlement_source_type!r} "
            f"observation_source={source!r}"
        )
    if source not in allowed_sources:
        return (
            f"Day0 observation source is not authorized for {consumer_label}: "
            f"city={city.name} settlement_source_type={settlement_source_type!r} "
            f"observation_source={source!r} allowed={sorted(allowed_sources)}"
        )
    return None


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


def _day0_observation_quality_rejection_reason(
    city: City,
    observation: "Day0ObservationContext",
    temperature_metric: MetricIdentity,
    *,
    decision_time: datetime | None,
) -> str | None:
    required_fields = ["current_temp"]
    if temperature_metric.is_low():
        required_fields.append("low_so_far")
    else:
        required_fields.append("high_so_far")

    missing_or_nonfinite = [
        field
        for field in required_fields
        if _finite_day0_observation_float(observation, field) is None
    ]
    if missing_or_nonfinite:
        return (
            "Day0 observation contains missing or non-finite required values: "
            f"city={city.name} fields={missing_or_nonfinite}"
        )

    observed_at = _parse_day0_observation_time_utc(
        _day0_observation_field(observation, "observation_time")
    )
    if observed_at is None:
        return f"Day0 observation timestamp is unavailable or unparseable: city={city.name}"

    reference_time = decision_time or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    reference_time = reference_time.astimezone(timezone.utc)
    age_seconds = (reference_time - observed_at).total_seconds()
    if age_seconds < -DAY0_EXECUTABLE_OBSERVATION_FUTURE_TOLERANCE_SECONDS:
        return (
            "Day0 observation timestamp is after the decision boundary: "
            f"city={city.name} observed_at={observed_at.isoformat()} "
            f"decision_time={reference_time.isoformat()}"
        )
    age_hours = max(0.0, age_seconds / 3600.0)
    if age_hours > DAY0_EXECUTABLE_OBSERVATION_MAX_AGE_HOURS:
        return (
            "Day0 observation is stale for executable probability generation: "
            f"city={city.name} age_hours={age_hours:.3f} "
            f"max_age_hours={DAY0_EXECUTABLE_OBSERVATION_MAX_AGE_HOURS:.3f}"
        )
    return None


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
    details_getter = getattr(clob, "get_fee_rate_details", None)
    if callable(details_getter):
        try:
            return fee_rate_fraction_from_details(
                canonicalize_fee_details(
                    details_getter(token_id),
                    source="clob_fee_rate",
                    token_id=token_id,
                )
            )
        except Exception as exc:
            raise FeeRateUnavailableError(f"fee-rate lookup failed for {token_id}: {exc}") from exc

    getter = getattr(clob, "get_fee_rate", None)
    if callable(getter):
        try:
            details = canonicalize_legacy_fee_rate_value(
                getter(token_id),
                source="legacy_get_fee_rate",
                token_id=token_id,
            )
            return fee_rate_fraction_from_details(details)
        except MarketSnapshotMismatchError as exc:
            raise FeeRateUnavailableError(f"fee-rate lookup failed for {token_id}: {exc}") from exc
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


def _forecast_model_family(model_name: str | None) -> str:
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


def _forecast_source_key(*, source_id: str | None, model_name: str | None) -> str:
    source = str(source_id or "").strip().lower()
    if source:
        return source
    return _forecast_model_family(model_name)


def _forecast_evidence_text(value) -> str:
    return str(value or "").strip()


def _raw_payload_hash_is_valid(value) -> bool:
    text = _forecast_evidence_text(value)
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


def _forecast_available_time_value(ens_result: dict) -> Optional[str]:
    return _snapshot_time_value(ens_result.get("available_at"))


def _forecast_evidence_datetime(value) -> Optional[datetime]:
    """Parse forecast evidence timestamps into UTC datetimes.

    Entry decisions need a causality proof, not just timestamp strings. Naive
    timestamps are treated as UTC because existing Zeus forecast evidence uses
    UTC wall-clock values when tzinfo is absent.
    """

    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _forecast_evidence_text(value)
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entry_forecast_evidence_errors(
    ens_result: dict,
    target_date: str,
    decision_time: Optional[datetime],
) -> list[str]:
    """Validate the executable-entry forecast evidence contract.

    Monitor/diagnostic fallbacks may have weaker provenance, but entry must not
    proceed unless the forecast source, timing, payload hash, and authority
    fields are explicit enough to audit later and are knowable before the
    decision was made.
    """

    errors: list[str] = []
    for field in ("source_id", "model", "degradation_level", "forecast_source_role", "authority_tier"):
        if not _forecast_evidence_text(ens_result.get(field)):
            errors.append(f"forecast_evidence_missing_{field}")

    if not _raw_payload_hash_is_valid(ens_result.get("raw_payload_hash")):
        errors.append("forecast_evidence_missing_raw_payload_hash")

    decision_time_value = _snapshot_time_value(decision_time)
    decision_time_dt = _forecast_evidence_datetime(decision_time_value)
    if decision_time_value is None:
        errors.append("forecast_evidence_missing_decision_time")
    elif decision_time_dt is None:
        errors.append("forecast_evidence_invalid_decision_time")

    issue_time_value = _snapshot_issue_time_value(ens_result)
    issue_time_dt = _forecast_evidence_datetime(issue_time_value)
    if issue_time_value is None:
        errors.append("forecast_evidence_missing_issue_time")
    elif issue_time_dt is None:
        errors.append("forecast_evidence_invalid_issue_time")

    valid_time_value = _snapshot_valid_time_value(target_date, ens_result)
    valid_time_dt = _forecast_evidence_datetime(valid_time_value)
    if valid_time_value is None:
        errors.append("forecast_evidence_missing_valid_time")
    elif valid_time_dt is None:
        errors.append("forecast_evidence_invalid_valid_time")

    fetch_time_value = _snapshot_time_value(ens_result.get("fetch_time"))
    fetch_time_dt = _forecast_evidence_datetime(fetch_time_value)
    if fetch_time_value is None:
        errors.append("forecast_evidence_missing_fetch_time")
    elif fetch_time_dt is None:
        errors.append("forecast_evidence_invalid_fetch_time")

    available_time_value = _forecast_available_time_value(ens_result)
    available_time_dt = _forecast_evidence_datetime(available_time_value)
    if available_time_value is None:
        errors.append("forecast_evidence_missing_available_at")
    elif available_time_dt is None:
        errors.append("forecast_evidence_invalid_available_at")

    if decision_time_dt is not None:
        if issue_time_dt is not None and issue_time_dt > decision_time_dt:
            errors.append("forecast_evidence_issue_after_decision")
        if fetch_time_dt is not None and fetch_time_dt > decision_time_dt:
            errors.append("forecast_evidence_fetch_after_decision")
        if available_time_dt is not None and available_time_dt > decision_time_dt:
            errors.append("forecast_evidence_available_after_decision")

    if issue_time_dt is not None and fetch_time_dt is not None and issue_time_dt > fetch_time_dt:
        errors.append("forecast_evidence_issue_after_fetch_time")
    if issue_time_dt is not None and available_time_dt is not None and issue_time_dt > available_time_dt:
        errors.append("forecast_evidence_issue_after_available_at")
    if available_time_dt is not None and fetch_time_dt is not None and available_time_dt > fetch_time_dt:
        errors.append("forecast_evidence_available_after_fetch_time")

    role = _forecast_evidence_text(ens_result.get("forecast_source_role"))
    if role and role != "entry_primary":
        errors.append(f"forecast_evidence_role_not_entry_primary:{role}")

    degradation = _forecast_evidence_text(ens_result.get("degradation_level"))
    if degradation and degradation != "OK":
        errors.append(f"forecast_evidence_degraded:{degradation}")

    authority = _forecast_evidence_text(ens_result.get("authority_tier"))
    if authority and authority != "FORECAST":
        errors.append(f"forecast_evidence_authority_not_forecast:{authority}")

    return errors


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


def _live_entry_forecast_config_or_blocker() -> tuple[EntryForecastConfig | None, str | None]:
    try:
        return entry_forecast_config(), None
    except Exception as exc:
        return None, f"ENTRY_FORECAST_CONFIG_INVALID:{exc}"


def _live_entry_forecast_rollout_blocker(cfg: EntryForecastConfig) -> str | None:
    if cfg.rollout_mode is EntryForecastRolloutMode.BLOCKED:
        return "ENTRY_FORECAST_ROLLOUT_BLOCKED"
    if cfg.rollout_mode is EntryForecastRolloutMode.SHADOW:
        return "ENTRY_FORECAST_ROLLOUT_SHADOW"
    if cfg.rollout_mode is EntryForecastRolloutMode.CANARY:
        return "ENTRY_FORECAST_ROLLOUT_CANARY"
    if cfg.rollout_mode is EntryForecastRolloutMode.LIVE:
        return None
    return "ENTRY_FORECAST_ROLLOUT_MODE_UNKNOWN"


def _entry_forecast_city_id(city: City) -> str:
    return city.name.upper().replace(" ", "_")


def _entry_forecast_market_family(candidate: MarketCandidate, temperature_metric: MetricIdentity) -> str:
    return str(
        candidate.event_id
        or candidate.slug
        or f"{candidate.city.name}|{candidate.target_date}|{temperature_metric.temperature_metric}"
    )


def _entry_forecast_condition_id(support_outcomes: list[dict]) -> str:
    for outcome in support_outcomes:
        for key in ("condition_id", "market_id", "question_id"):
            value = str(outcome.get(key) or "").strip()
            if value:
                return value
    return ""


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
    if edge.direction == "buy_no" and edge.bin.is_shoulder:
        return "shoulder_sell"
    if edge.direction == "buy_yes" and not edge.bin.is_shoulder:
        return "center_buy"
    return "unclassified"


def _strategy_key_for(candidate: MarketCandidate, edge: BinEdge) -> str | None:
    if candidate.discovery_mode == DiscoveryMode.DAY0_CAPTURE.value:
        return "settlement_capture"
    if candidate.discovery_mode == DiscoveryMode.OPENING_HUNT.value:
        return "opening_inertia"
    if edge.direction == "buy_no" and edge.bin.is_shoulder:
        return "shoulder_sell"
    if edge.direction == "buy_yes" and not edge.bin.is_shoulder:
        return "center_buy"
    return None


def _strategy_key_for_hypothesis(candidate: MarketCandidate, hypothesis: FullFamilyHypothesis) -> str | None:
    if candidate.discovery_mode == DiscoveryMode.DAY0_CAPTURE.value:
        return "settlement_capture"
    if candidate.discovery_mode == DiscoveryMode.OPENING_HUNT.value:
        return "opening_inertia"
    if hypothesis.direction == "buy_no" and hypothesis.is_shoulder:
        return "shoulder_sell"
    if hypothesis.direction == "buy_yes" and not hypothesis.is_shoulder:
        return "center_buy"
    return None


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


def _forecast_times_as_strings(times: list) -> list[str]:
    return [
        ts.isoformat() if isinstance(ts, datetime) else str(ts)
        for ts in times
    ]


def _remaining_hour_indices_for_day0(
    target_day_indices: np.ndarray,
    *,
    times: list[str],
    timezone_name: str,
    now: datetime,
) -> np.ndarray:
    tz = ZoneInfo(timezone_name)
    now_local = now.astimezone(tz)
    remaining = [
        int(idx)
        for idx in target_day_indices
        if _parse_forecast_timestamp(times[int(idx)]).astimezone(tz) >= now_local
    ]
    return np.array(remaining, dtype=int)


def _validate_ensemble_for_required_hours(
    result: dict,
    *,
    expected_members: int | None = None,
    required_hour_indices: np.ndarray | None,
) -> bool:
    try:
        if required_hour_indices is None:
            if expected_members is None:
                return validate_ensemble(result)
            return validate_ensemble(result, expected_members=expected_members)
        if expected_members is None:
            return validate_ensemble(result, required_hour_indices=required_hour_indices)
        return validate_ensemble(
            result,
            expected_members=expected_members,
            required_hour_indices=required_hour_indices,
        )
    except TypeError as e:
        if "required_hour_indices" not in str(e):
            raise
        if expected_members is None:
            return validate_ensemble(result)
        return validate_ensemble(result, expected_members=expected_members)


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
            if strategy_key is None:
                continue
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
                "hypothesis_strategy_key": row.get("hypothesis_strategy_key") or row.get("strategy_key", ""),
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
) -> set[tuple[int, str]]:
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
                "support_index": int(hypothesis.index),
                "range_label": hypothesis.range_label,
                "direction": hypothesis.direction,
            }
        )
    selected_rows = apply_familywise_fdr(rows, q=DEFAULT_FDR_ALPHA)
    return {
        (int(row["support_index"]), str(row["direction"]))
        for row in selected_rows
        if bool(row.get("selected_post_fdr")) and bool(row.get("passed_prefilter"))
    }


def _filter_executable_selected_edges(
    edges: list[BinEdge],
    selected_edge_keys: set[tuple[int, str]],
) -> list[BinEdge]:
    edge_keys = {
        (int(edge.support_index), edge.direction)
        for edge in edges
        if edge.support_index is not None
    }
    missing = selected_edge_keys - edge_keys
    if missing:
        missing_s = ", ".join(
            f"support_index={support_index}/{direction}"
            for support_index, direction in sorted(missing)
        )
        raise ValueError(f"FDR_SELECTED_EDGE_UNEXECUTABLE:{missing_s}")
    return [
        edge for edge in edges
        if edge.support_index is not None
        and (int(edge.support_index), edge.direction) in selected_edge_keys
    ]


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
    entry_provenance_context = SimpleNamespace(
        selected_method=selected_method,
        entry_method=selected_method,
    )
    if not entry_provenance_context.selected_method or not entry_provenance_context.entry_method:
        raise ValueError("entry provenance context is required before probability evaluation")

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

    if is_day0_mode:
        source_rejection_reason = _day0_observation_source_rejection_reason(
            city, candidate.observation
        )
        if source_rejection_reason is not None:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="OBSERVATION_SOURCE_UNAUTHORIZED",
                rejection_reasons=[source_rejection_reason],
                availability_status="DATA_UNAVAILABLE",
                selected_method=selected_method,
                applied_validations=["day0_observation", "observation_source_policy"],
            )]
        observation_quality_rejection = _day0_observation_quality_rejection_reason(
            city,
            candidate.observation,
            temperature_metric,
            decision_time=decision_time,
        )
        if observation_quality_rejection is not None:
            availability_status = (
                "DATA_STALE"
                if "stale" in observation_quality_rejection
                or "after the decision boundary" in observation_quality_rejection
                else "DATA_UNAVAILABLE"
            )
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=[observation_quality_rejection],
                availability_status=availability_status,
                selected_method=selected_method,
                applied_validations=["day0_observation", "observation_quality_gate"],
            )]

    entry_forecast_cfg: EntryForecastConfig | None = None
    if get_mode() == "live":
        entry_forecast_cfg, live_entry_forecast_blocker = _live_entry_forecast_config_or_blocker()
        if live_entry_forecast_blocker is None and entry_forecast_cfg is not None:
            live_entry_forecast_blocker = _live_entry_forecast_rollout_blocker(entry_forecast_cfg)
        if live_entry_forecast_blocker is not None:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=[live_entry_forecast_blocker],
                availability_status="DATA_UNAVAILABLE",
                selected_method=selected_method,
                applied_validations=["entry_forecast_rollout", "legacy_entry_primary_fetch_blocked"],
            )]

    # Build the complete support vector first. Closed/non-accepting shoulder
    # children still define p_raw topology; only executable children get token
    # payloads for quote and order paths.
    bins = []
    token_map = {}
    executable_mask_values: list[bool] = []
    support_outcomes: list[dict] = []
    for o in outcomes:
        low, high = o.get("range_low"), o.get("range_high")
        if low is None and high is None:
            continue
        support_index = len(bins)
        declared_support_index = o.get("support_index")
        if declared_support_index is not None:
            try:
                declared_support_index = int(declared_support_index)
            except (TypeError, ValueError):
                return [EdgeDecision(
                    False,
                    decision_id=_decision_id(),
                    rejection_stage="MARKET_FILTER",
                    rejection_reasons=[f"invalid support_index for {o.get('title', '')!r}"],
                    selected_method=selected_method,
                    applied_validations=["market_filter", "support_topology"],
                )]
            if declared_support_index != support_index:
                return [EdgeDecision(
                    False,
                    decision_id=_decision_id(),
                    rejection_stage="MARKET_FILTER",
                    rejection_reasons=[
                        f"support_index mismatch for {o.get('title', '')!r}: "
                        f"declared {declared_support_index}, expected {support_index}"
                    ],
                    selected_method=selected_method,
                    applied_validations=["market_filter", "support_topology"],
                )]

        bins.append(Bin(low=low, high=high, label=o["title"], unit=city.settlement_unit))
        support_outcomes.append(o)
        executable = bool(o.get("executable", True))
        executable = executable and bool(o.get("token_id")) and bool(o.get("no_token_id")) and bool(o.get("market_id"))
        executable_mask_values.append(executable)
        if executable:
            token_payload = {
                "token_id": o["token_id"],
                "no_token_id": o["no_token_id"],
                "market_id": o["market_id"],
            }
            executable_snapshot_id = o.get("executable_snapshot_id") or o.get("snapshot_id")
            if executable_snapshot_id:
                token_payload["executable_snapshot_id"] = str(executable_snapshot_id)
            executable_tick = o.get("executable_snapshot_min_tick_size", o.get("min_tick_size"))
            if executable_tick is not None:
                token_payload["executable_snapshot_min_tick_size"] = executable_tick
            executable_min_order = o.get("executable_snapshot_min_order_size", o.get("min_order_size"))
            if executable_min_order is not None:
                token_payload["executable_snapshot_min_order_size"] = executable_min_order
            if "executable_snapshot_neg_risk" in o:
                token_payload["executable_snapshot_neg_risk"] = o["executable_snapshot_neg_risk"]
            elif "neg_risk" in o:
                token_payload["executable_snapshot_neg_risk"] = o["neg_risk"]
            token_map[support_index] = token_payload

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
    executable_mask = np.asarray(executable_mask_values, dtype=bool)
    executable_count = int(np.count_nonzero(executable_mask))
    if executable_count < 1:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="MARKET_FILTER",
            rejection_reasons=["support topology has no executable bins"],
            selected_method=selected_method,
            applied_validations=["market_filter", "support_topology"],
        )]
    p_raw_topology = {
        "schema_version": 1,
        "topology_status": "complete",
        "unit": city.settlement_unit,
        "support_count": len(bins),
        "executable_count": executable_count,
        "executable_hypothesis_count": executable_count,
        "executable_mask": [bool(v) for v in executable_mask],
        "skipped_support_indexes": [
            idx for idx, is_executable in enumerate(executable_mask) if not bool(is_executable)
        ],
        "market_fusion_status_by_support_index": [
            {
                "support_index": idx,
                "status": "pending_executable_quote"
                if bool(executable_mask[idx])
                else "disabled_non_executable",
            }
            for idx in range(len(bins))
        ],
        "requires_atomic_topology": bool(np.any(~executable_mask)),
        "support": [
            {
                "support_index": idx,
                "label": b.label,
                "low": b.low,
                "high": b.high,
                "unit": b.unit,
                "executable": bool(executable_mask[idx]),
                "market_id": str(support_outcomes[idx].get("market_id") or ""),
                "condition_id": str(
                    support_outcomes[idx].get("condition_id")
                    or support_outcomes[idx].get("market_id")
                    or ""
                ),
                "question_id": str(support_outcomes[idx].get("question_id") or ""),
                "gamma_market_id": str(support_outcomes[idx].get("gamma_market_id") or ""),
            }
            for idx, b in enumerate(bins)
        ],
    }

    target_d = date.fromisoformat(target_date)
    lead_days = max(0.0, lead_days_to_date_start(target_d, city.timezone))
    ens_forecast_days = max(2, int(max(0.0, lead_days)) + 2)

    primary_model = ensemble_primary_model()

    if entry_forecast_cfg is not None:
        if is_day0_mode:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=["ENTRY_FORECAST_DAY0_EXECUTABLE_PATH_NOT_WIRED"],
                availability_status="DATA_UNAVAILABLE",
                selected_method=selected_method,
                applied_validations=["entry_forecast_reader", "legacy_entry_primary_fetch_blocked"],
            )]
        if conn is None or not hasattr(conn, "execute"):
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=["ENTRY_FORECAST_READER_DB_UNAVAILABLE"],
                availability_status="DATA_UNAVAILABLE",
                selected_method=selected_method,
                applied_validations=["entry_forecast_reader", "legacy_entry_primary_fetch_blocked"],
            )]
        decision_instant = decision_time or datetime.now(timezone.utc)
        track = track_for_metric(entry_forecast_cfg, temperature_metric.temperature_metric)
        reader_result = read_executable_forecast(
            conn,
            city_id=_entry_forecast_city_id(city),
            city_name=city.name,
            city_timezone=city.timezone,
            target_local_date=target_d,
            temperature_metric=temperature_metric.temperature_metric,
            source_id=entry_forecast_cfg.source_id,
            source_transport=entry_forecast_cfg.source_transport.value,
            data_version=data_version_for_track(track),
            track=track,
            strategy_key="entry_forecast",
            market_family=_entry_forecast_market_family(candidate, temperature_metric),
            condition_id=_entry_forecast_condition_id(support_outcomes),
            decision_time=decision_instant,
        )
        if not reader_result.ok or reader_result.bundle is None:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=[reader_result.reason_code],
                availability_status="DATA_UNAVAILABLE",
                selected_method=selected_method,
                applied_validations=["entry_forecast_reader", "legacy_entry_primary_fetch_blocked"],
            )]
        ens_result = reader_result.bundle.to_ens_result()
    else:
        # Fetch ENS
        try:
            ens_result = fetch_ensemble(
                city,
                forecast_days=ens_forecast_days,
                model=primary_model,
                role="entry_primary",
            )
        except SourceNotEnabled as e:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=[str(e)],
                availability_status="DATA_STALE",
                selected_method=selected_method,
                applied_validations=["ens_fetch", "forecast_source_policy"],
            )]
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
    if ens_result is None:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=["ENS fetch failed or < 51 members"],
            availability_status="DATA_UNAVAILABLE",
            selected_method=selected_method,
            applied_validations=["ens_fetch"],
        )]
    n_members_meta = ens_result.get("n_members")
    if n_members_meta is not None and int(n_members_meta) < ensemble_member_count():
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=["ENS fetch failed or < 51 members"],
            availability_status="DATA_UNAVAILABLE",
            selected_method=selected_method,
            applied_validations=["ens_fetch"],
        )]
    if ens_result.get("degradation_level") == "DEGRADED_FORECAST_FALLBACK":
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=[
                f"Forecast source {ens_result.get('source_id', 'unknown')} is "
                "DEGRADED_FORECAST_FALLBACK; entry requires primary forecast authority"
            ],
            availability_status="DATA_STALE",
            selected_method=selected_method,
            applied_validations=["ens_fetch", "forecast_source_policy"],
        )]
    forecast_evidence_errors = _entry_forecast_evidence_errors(
        ens_result,
        target_date,
        decision_time,
    )
    if forecast_evidence_errors:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=[
                "Forecast source evidence incomplete for executable entry: "
                + ", ".join(forecast_evidence_errors)
            ],
            availability_status="DATA_UNAVAILABLE",
            selected_method=selected_method,
            applied_validations=["ens_fetch", "forecast_source_evidence"],
        )]
    period_extrema_members = ens_result.get("period_extrema_members")
    using_period_extrema = period_extrema_members is not None
    if using_period_extrema:
        ens_times = []
        ens_tz_hours = None
    else:
        try:
            ens_times = _forecast_times_as_strings(ens_result["times"])
        except (KeyError, TypeError) as e:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=[str(e)],
                availability_status="DATA_STALE",
                selected_method=selected_method,
                applied_validations=["ens_fetch"],
            )]
        try:
            ens_tz_hours = select_hours_for_target_date(
                target_d,
                city.timezone,
                times=ens_times,
            )
        except ValueError:
            ens_tz_hours = None
    day0_temporal_context = None
    required_hour_indices = ens_tz_hours
    if is_day0_mode:
        day0_temporal_context = _get_day0_temporal_context(city, target_d, candidate.observation)
        if day0_temporal_context is None:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=["Solar/DST context unavailable for Day0"],
                availability_status="DATA_STALE",
                selected_method=selected_method,
                applied_validations=["day0_observation", "solar_context"],
            )]
        if ens_tz_hours is not None:
            required_hour_indices = _remaining_hour_indices_for_day0(
                ens_tz_hours,
                times=ens_times,
                timezone_name=city.timezone,
                now=day0_temporal_context.current_utc_timestamp,
            )
            if len(required_hour_indices) == 0:
                return [EdgeDecision(
                    False,
                    decision_id=_decision_id(),
                    rejection_stage="SIGNAL_QUALITY",
                    rejection_reasons=["No Day0 forecast hours remain for target date"],
                    availability_status="DATA_STALE",
                    selected_method=selected_method,
                    applied_validations=["day0_observation", "ens_fetch"],
                )]
    if not using_period_extrema and not _validate_ensemble_for_required_hours(
        ens_result,
        required_hour_indices=required_hour_indices,
    ):
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=["ENS fetch failed, < 51 members, or insufficient finite required-hour members"],
            availability_status="DATA_UNAVAILABLE",
            selected_method=selected_method,
            applied_validations=["ens_fetch"],
        )]

    epistemic = EpistemicContext.enter_cycle(fallback_override=decision_time)
    settlement_semantics = SettlementSemantics.for_city(city)

    ens = None
    if not using_period_extrema:
        try:
            ens = EnsembleSignal(
                ens_result["members_hourly"],
                ens_times,
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
        temporal_context = day0_temporal_context

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

        # P10D S1 / INV-16: thread causality_status from the current Day0
        # observation context into Day0SignalInputs. Snapshot metadata is
        # only authority after the exact decision_snapshot_id exists; do not
        # consult a latest city/date/metric snapshot before routing.
        # N/A_CAUSAL_DAY_ALREADY_STARTED signals the day has partially elapsed;
        # Day0Router routes LOW slots accordingly per _LOW_ALLOWED_CAUSALITY.
        if isinstance(candidate.observation, dict):
            causality_status = str(
                candidate.observation.get("causality_status") or "OK"
            )
        else:
            causality_status = str(
                getattr(candidate.observation, "causality_status", "OK") or "OK"
            )

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

        observed_high_so_far = _finite_day0_observation_float(
            candidate.observation,
            "high_so_far",
        )
        observed_low_so_far = _finite_day0_observation_float(
            candidate.observation,
            "low_so_far",
        )
        current_temp = _finite_day0_observation_float(
            candidate.observation,
            "current_temp",
        )
        if current_temp is None:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=["Day0 current observation became unavailable before signal routing"],
                availability_status="DATA_UNAVAILABLE",
                selected_method=selected_method,
                applied_validations=["day0_observation", "observation_quality_gate"],
            )]

        day0 = Day0Router.route(Day0SignalInputs(
            temperature_metric=temperature_metric,
            observed_high_so_far=observed_high_so_far,
            observed_low_so_far=observed_low_so_far,
            current_temp=current_temp,
            hours_remaining=hours_remaining,
            member_maxes_remaining=extrema.maxes,
            member_mins_remaining=extrema.mins,
            unit=city.settlement_unit,
            observation_source=str(_day0_observation_field(candidate.observation, "source", "")),
            observation_time=_day0_observation_field(candidate.observation, "observation_time"),
            temporal_context=temporal_context,
            round_fn=settlement_semantics.round_values,
            causality_status=causality_status,
        ))
        # 2026-04-30 BLOCKER #2 fix: pass n_mc explicitly to mirror the pattern
        # at monitor_refresh.py:502 and surface any future config drift in
        # code review. Pre-fix, p_vector() omitted n_mc and relied on the
        # callee re-resolving day0_n_mc() at call time — correct today (10000)
        # but contract-implicit.
        p_raw = day0.p_vector(bins, n_mc=day0_n_mc())
        day0_forecast_context = day0.forecast_context()
        raw_arr = extrema.maxes if extrema.maxes is not None else extrema.mins
        required_member_floor = ensemble_member_count() if required_hour_indices is not None else 1
        if raw_arr is None or np.count_nonzero(np.isfinite(raw_arr)) < required_member_floor:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=["Day0 forecast has insufficient finite remaining ensemble members"],
                availability_status="DATA_UNAVAILABLE",
                selected_method=selected_method,
                applied_validations=["day0_observation", "ens_fetch"],
            )]
        ensemble_spread = TemperatureDelta(
            float(np.std(raw_arr)), city.settlement_unit
        )
        analysis_member_extrema = raw_arr
        entry_validations = ["day0_observation", "ens_fetch", "mc_instrument_noise", "diurnal_peak"]
        lead_days_for_calibration = 0.0
    else:
        # 2026-04-30 BLOCKER #2 fix: pass n_mc explicitly (mirrors
        # monitor_refresh.py:205). Same rationale as the day0 branch above.
        if using_period_extrema:
            expected_members_unit = "degC" if city.settlement_unit == "C" else "degF"
            if ens_result.get("members_unit") != expected_members_unit:
                return [EdgeDecision(
                    False,
                    decision_id=_decision_id(),
                    rejection_stage="SIGNAL_QUALITY",
                    rejection_reasons=["EXECUTABLE_FORECAST_MEMBERS_UNIT_MISMATCH"],
                    availability_status="DATA_UNAVAILABLE",
                    selected_method=selected_method,
                    applied_validations=["entry_forecast_reader", "members_unit"],
                )]
            member_extrema = np.asarray(period_extrema_members, dtype=float)
            if (
                member_extrema.ndim != 1
                or len(member_extrema) < ensemble_member_count()
                or not np.isfinite(member_extrema).all()
            ):
                return [EdgeDecision(
                    False,
                    decision_id=_decision_id(),
                    rejection_stage="SIGNAL_QUALITY",
                    rejection_reasons=["EXECUTABLE_FORECAST_MEMBER_EXTREMA_INVALID"],
                    availability_status="DATA_UNAVAILABLE",
                    selected_method=selected_method,
                    applied_validations=["entry_forecast_reader", "period_extrema_members_adapter"],
                )]
            p_raw = p_raw_vector_from_maxes(
                member_extrema,
                city,
                settlement_semantics,
                bins,
                n_mc=ensemble_n_mc(),
            )
            ensemble_spread = TemperatureDelta(
                float(np.std(member_extrema)),
                city.settlement_unit,
            )
            analysis_member_extrema = member_extrema
            entry_validations = [
                "entry_forecast_reader",
                "entry_readiness",
                "period_extrema_members_adapter",
                "mc_instrument_noise",
            ]
        else:
            assert ens is not None
            p_raw = ens.p_raw_vector(bins, n_mc=ensemble_n_mc())
            ensemble_spread = ens.spread()
            analysis_member_extrema = ens.member_extrema
            entry_validations = ["ens_fetch", "mc_instrument_noise"]
        day0_forecast_context = None
        lead_days_for_calibration = lead_days

    if not _valid_probability_vector(p_raw, len(bins)):
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=["P_raw is non-finite, negative, non-normalized, out of [0,1], or has wrong bin cardinality"],
            availability_status="DATA_UNAVAILABLE",
            selected_method=selected_method,
            applied_validations=entry_validations,
        )]

    # Store ENS snapshot AFTER all semantic gates pass (#67 — no write-before-validate).
    # Executable reader rows already have an audited ensemble_snapshots_v2 id;
    # reuse it instead of writing a second legacy snapshot for the same source run.
    if using_period_extrema:
        snapshot_id = str(ens_result.get("executable_snapshot_id") or "")
    else:
        assert ens is not None
        snapshot_id = _store_ens_snapshot(conn, city, target_date, ens, ens_result)
    if not snapshot_id:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=["ENS snapshot persistence failed: decision_snapshot_id unavailable"],
            availability_status="DATA_UNAVAILABLE",
            selected_method=selected_method,
            applied_validations=[*entry_validations, "ens_snapshot_persistence"],
            p_raw=p_raw,
        )]

    v2_snapshot_meta = _read_v2_snapshot_metadata(
        conn,
        city.name,
        target_date,
        temperature_metric.temperature_metric,
        snapshot_id=snapshot_id,
    )
    if boundary_ambiguous_refuses_signal(v2_snapshot_meta):
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="MARKET_FILTER",
            rejection_reasons=["DT7_boundary_day_ambiguous"],
            availability_status="DATA_AVAILABLE",
            selected_method=selected_method,
            applied_validations=[*entry_validations, "dt7_boundary_day_gate"],
            decision_snapshot_id=snapshot_id,
            p_raw=p_raw,
        )]

    p_raw_persisted = _store_snapshot_p_raw(
        conn,
        snapshot_id,
        p_raw,
        bias_corrected=bool(getattr(ens, "bias_corrected", False)),
        p_raw_topology=p_raw_topology,
    )
    if p_raw_persisted is False:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=["ENS snapshot p_raw persistence failed: canonical p_raw unavailable"],
            availability_status="DATA_UNAVAILABLE",
            selected_method=selected_method,
            applied_validations=[*entry_validations, "ens_snapshot_p_raw_persistence"],
            p_raw=p_raw,
        )]

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
                decision_id=_decision_id(),
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

    if not _valid_probability_vector(p_cal, len(bins)):
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="SIGNAL_QUALITY",
            rejection_reasons=["P_cal is non-finite, negative, non-normalized, out of [0,1], or has wrong bin cardinality"],
            availability_status="DATA_UNAVAILABLE",
            selected_method=selected_method,
            applied_validations=entry_validations,
            decision_snapshot_id=snapshot_id,
            p_raw=p_raw,
            p_cal=p_cal,
        )]

    try:
        maturity_multiplier = edge_threshold_multiplier(cal_level)
    except Exception as exc:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="CALIBRATION_IMMATURE",
            rejection_reasons=[f"invalid calibration maturity level {cal_level!r}: {exc}"],
            availability_status="DATA_UNAVAILABLE",
            selected_method=selected_method,
            applied_validations=[*entry_validations, "calibration_maturity_invalid"],
            decision_snapshot_id=snapshot_id,
            p_raw=p_raw,
            p_cal=p_cal,
        )]
    maturity_validation = [
        f"calibration_maturity_level_{cal_level}",
        f"calibration_maturity_threshold_{maturity_multiplier:g}x",
    ]
    entry_validations.extend(maturity_validation)
    if cal is None or cal_level >= 4:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="CALIBRATION_IMMATURE",
            rejection_reasons=[
                "calibration_level=4 has no Platt model; raw-probability entries "
                f"are blocked before edge/FDR selection (required_threshold={maturity_multiplier:g}x)"
            ],
            availability_status="DATA_UNAVAILABLE",
            selected_method=selected_method,
            applied_validations=[*entry_validations, "raw_probability_entry_blocked"],
            decision_snapshot_id=snapshot_id,
            p_raw=p_raw,
            p_cal=p_cal,
        )]

    # Market prices via VWMP
    p_market = np.zeros(len(bins))
    p_market_no = np.zeros(len(bins))
    buy_no_quote_available = np.zeros(len(bins), dtype=bool)
    market_is_complete = True
    try:
        # The legacy flag name says "multibin", but the authority rule is now
        # broader: every executable buy_no needs native NO-token quote evidence.
        probe_native_no_quotes = native_multibin_buy_no_shadow_enabled()
    except ValueError as exc:
        return [EdgeDecision(
            False,
            decision_id=_decision_id(),
            rejection_stage="RISK_REJECTED",
            rejection_reasons=[str(exc)],
            availability_status="CONFIG_INVALID",
            selected_method=selected_method,
            applied_validations=[*entry_validations, "native_multibin_buy_no_flag_invalid"],
            decision_snapshot_id=snapshot_id,
            p_raw=p_raw,
            p_cal=p_cal,
        )]
    native_no_quote_unavailable_labels: list[str] = []
    for i, o in enumerate(outcomes):
        if o.get("range_low") is None and o.get("range_high") is None:
            continue
        raw_support_index = o.get("support_index")
        if raw_support_index is not None:
            try:
                idx = int(raw_support_index)
            except (TypeError, ValueError):
                market_is_complete = False
                continue
            if idx < 0 or idx >= len(bins):
                market_is_complete = False
                continue
        else:
            idx = next((j for j, b in enumerate(bins) if b.label == o["title"]), None)
        if idx is None:
            market_is_complete = False
            continue
        if not bool(executable_mask[idx]):
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
            if probe_native_no_quotes:
                no_token_id = str(o.get("no_token_id") or "")
                if not no_token_id:
                    logger.warning("Native NO quote unavailable for %s: missing no_token_id", o["title"])
                    native_no_quote_unavailable_labels.append(str(o["title"]))
                else:
                    try:
                        no_bid, no_ask, no_bid_sz, no_ask_sz = clob.get_best_bid_ask(no_token_id)
                        p_market_no[idx] = vwmp(no_bid, no_ask, no_bid_sz, no_ask_sz)
                        buy_no_quote_available[idx] = True
                    except Exception as no_exc:
                        logger.warning(
                            "Native NO quote unavailable for %s/%s; buy_no disabled for this bin: %s",
                            o["title"],
                            no_token_id,
                            no_exc,
                        )
                        native_no_quote_unavailable_labels.append(str(o["title"]))
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
        crosscheck_model = ensemble_crosscheck_model()
        try:
            crosscheck_result = fetch_ensemble(
                city,
                forecast_days=ens_forecast_days,
                model=crosscheck_model,
                role="diagnostic",
            )
        except Exception as e:
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=[f"{crosscheck_model} crosscheck unavailable: {e}"],
                availability_status=_availability_status_for_error(e),
                selected_method=selected_method,
                applied_validations=[*entry_validations, "crosscheck_unavailable"],
                decision_snapshot_id=snapshot_id,
                p_raw=p_raw,
                p_cal=p_cal,
                p_market=p_market,
                agreement="CROSSCHECK_UNAVAILABLE",
            )]
        if crosscheck_result is None or not validate_ensemble(
            crosscheck_result,
            expected_members=ensemble_crosscheck_member_count(),
        ):
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=[f"{crosscheck_model} crosscheck unavailable"],
                availability_status="DATA_UNAVAILABLE",
                selected_method=selected_method,
                applied_validations=[*entry_validations, "crosscheck_unavailable"],
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
                times=_forecast_times_as_strings(crosscheck_result["times"]),
            )
            if not _validate_ensemble_for_required_hours(
                crosscheck_result,
                expected_members=ensemble_crosscheck_member_count(),
                required_hour_indices=gfs_tz_hours,
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
            gfs_metric_values = (
                crosscheck_result["members_hourly"][:, gfs_tz_hours].min(axis=1)
                if temperature_metric.is_low()
                else crosscheck_result["members_hourly"][:, gfs_tz_hours].max(axis=1)
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
            logger.warning("%s crosscheck failed: %s", crosscheck_model, e)
            return [EdgeDecision(
                False,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=[f"{crosscheck_model} crosscheck unavailable: {e}"],
                availability_status="DATA_UNAVAILABLE",
                selected_method=selected_method,
                applied_validations=[*entry_validations, "crosscheck_unavailable"],
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
            rejection_reasons=[f"{primary_model}/{crosscheck_model} CONFLICT"],
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
    entry_validations.append("model_only_posterior")
    entry_validations.append("alpha_posterior")
    if probe_native_no_quotes:
        if native_no_quote_unavailable_labels:
            entry_validations.append(NATIVE_BUY_NO_QUOTE_UNAVAILABLE_VALIDATION)
        else:
            entry_validations.append(NATIVE_BUY_NO_QUOTE_AVAILABLE_VALIDATION)

    forecast_source = _forecast_source_key(
        source_id=ens_result.get("source_id"),
        model_name=ens_result.get("model"),
    )
    forecast_model_family = _forecast_model_family(ens_result.get("model"))
    season = season_from_date(target_date, lat=city.lat)
    bias_reference = _load_model_bias_reference(
        conn,
        city_name=city.name,
        season=season,
        forecast_source=forecast_source,
    )

    # Edge detection
    # Flag missing mapped outcomes against the declared family topology
    mapped_executable_outcomes = sum(
        1
        for idx, is_executable in enumerate(executable_mask)
        if bool(is_executable) and p_market[idx] > 0.0
    )
    if mapped_executable_outcomes < executable_count:
        market_is_complete = False

    analysis = MarketAnalysis(
        p_raw=p_raw,
        p_cal=p_cal,
        p_market=p_market,
        p_market_no=p_market_no if probe_native_no_quotes else None,
        buy_no_quote_available=buy_no_quote_available if probe_native_no_quotes else None,
        executable_mask=executable_mask,
        alpha=alpha,
        bins=bins,
        member_maxes=analysis_member_extrema,
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
        posterior_mode=MODEL_ONLY_POSTERIOR_MODE,
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
    forecast_issue_time = _snapshot_issue_time_value(ens_result)
    forecast_valid_time = _snapshot_valid_time_value(target_date, ens_result)
    forecast_fetch_time = _snapshot_time_value(ens_result.get("fetch_time"))
    forecast_available_at = _forecast_available_time_value(ens_result)
    forecast_context["forecast_source_id"] = forecast_source
    forecast_context["model_family"] = forecast_model_family
    forecast_context["forecast_issue_time"] = forecast_issue_time
    forecast_context["forecast_valid_time"] = forecast_valid_time
    forecast_context["forecast_fetch_time"] = forecast_fetch_time
    forecast_context["forecast_available_at"] = forecast_available_at
    forecast_context["raw_payload_hash"] = ens_result.get("raw_payload_hash")
    forecast_context["degradation_level"] = ens_result.get("degradation_level")
    forecast_context["forecast_source_role"] = ens_result.get("forecast_source_role")
    forecast_context["authority_tier"] = ens_result.get("authority_tier")
    forecast_context["decision_time"] = (
        decision_time.isoformat() if isinstance(decision_time, datetime) else None
    )
    forecast_context["decision_time_status"] = "OK" if decision_time is not None else "NOT_SUPPLIED_DIRECT_EVALUATOR_CALL"
    n_bootstrap = edge_n_bootstrap()
    edges = analysis.find_edges(n_bootstrap=n_bootstrap)
    _fdr_fallback = False
    _fdr_selection_unexecutable = ""
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
        try:
            filtered = _filter_executable_selected_edges(edges, selected_edge_keys)
        except ValueError as exc:
            logger.warning(
                "Full-family FDR selected an unmaterialized edge for %s/%s: %s",
                candidate.city.name,
                candidate.target_date,
                exc,
            )
            _fdr_selection_unexecutable = str(exc)
            filtered = []
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
        elif _fdr_selection_unexecutable:
            stage = "FDR_SELECTION_UNEXECUTABLE"
            rejection_reasons = [_fdr_selection_unexecutable]
        else:
            stage = "EDGE_INSUFFICIENT" if not edges else "FDR_FILTERED"
            rejection_reasons = [f"{len(edges)} edges found, {len(filtered)} passed FDR"]
            if native_no_quote_unavailable_labels:
                labels = ",".join(native_no_quote_unavailable_labels[:3])
                if len(native_no_quote_unavailable_labels) > 3:
                    labels = f"{labels},..."
                rejection_reasons.append(f"BUY_NO_NATIVE_QUOTE_UNAVAILABLE:{labels}")
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
        decision_validations = list(entry_validations)
        if edge.support_index is None:
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="FDR_SELECTION_UNEXECUTABLE",
                rejection_reasons=["selected edge is missing canonical support_index"],
                selected_method=selected_method,
                applied_validations=[*decision_validations, "support_topology"],
                decision_snapshot_id=snapshot_id,
            ))
            continue
        bin_idx = int(edge.support_index)
        if bin_idx < 0 or bin_idx >= len(bins) or bin_idx not in token_map or not bool(executable_mask[bin_idx]):
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="FDR_SELECTION_UNEXECUTABLE",
                rejection_reasons=[f"selected support index {bin_idx} has no executable token payload"],
                selected_method=selected_method,
                applied_validations=[*decision_validations, "support_topology"],
                decision_snapshot_id=snapshot_id,
            ))
            continue
        tokens = token_map[bin_idx]
        edge_source = _edge_source_for(candidate, edge)
        strategy_key = _strategy_key_for(candidate, edge)
        if strategy_key is None:
            decisions.append(EdgeDecision(
                False,
                edge=edge,
                decision_id=_decision_id(),
                rejection_stage="SIGNAL_QUALITY",
                rejection_reasons=["strategy_key_unclassified"],
                selected_method=selected_method,
                applied_validations=[*decision_validations, "strategy_key_classification"],
                decision_snapshot_id=snapshot_id,
                edge_source=edge_source,
            ))
            continue
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

        # Oracle penalty gate — blacklisted cities skip trading entirely.
        # S2 R4 P10B: pass temperature_metric so LOW candidates use separate oracle info.
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
                strategy_key=strategy_key,
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
        per_strategy_multiplier = strategy_kelly_multiplier(strategy_key)
        decision_validations.append(f"strategy_kelly_multiplier_{per_strategy_multiplier:g}x")

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
            sizing_bankroll=sizing_bankroll,
            kelly_multiplier_used=km * risk_throttle,
            execution_fee_rate=fee_rate,
            safety_cap_usd=settings["live_safety_cap_usd"],
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

    first_valid_time = _snapshot_time_value(ens_result.get("first_valid_time"))
    if first_valid_time is not None:
        return first_valid_time

    # Return None instead of synthetic sentinels for missing valid_time.
    return None


def _ensemble_snapshots_table(conn) -> str:
    try:
        row = conn.execute(
            "SELECT 1 FROM world.sqlite_master WHERE type = 'table' AND name = 'ensemble_snapshots'"
        ).fetchone()
    except Exception:
        return "ensemble_snapshots"
    return "world.ensemble_snapshots" if row is not None else "ensemble_snapshots"


def _ensemble_snapshots_v2_table(conn) -> str:
    try:
        row = conn.execute(
            "SELECT 1 FROM world.sqlite_master WHERE type = 'table' AND name = 'ensemble_snapshots_v2'"
        ).fetchone()
        if row is not None:
            return "world.ensemble_snapshots_v2"
    except Exception:
        pass
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ensemble_snapshots_v2'"
        ).fetchone()
    except Exception:
        return ""
    return "ensemble_snapshots_v2" if row is not None else ""


def _members_unit_for_snapshot(city, ens_result: dict) -> str:
    explicit = ens_result.get("members_unit")
    if explicit:
        return str(explicit)
    settlement_unit = str(getattr(city, "settlement_unit", "") or "").upper()
    if settlement_unit in {"F", "C"}:
        return f"deg{settlement_unit}"
    return "degC"


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
        and row["data_version"] == data_version
        and row["model_version"] == model_version
        and row["issue_time"] == issue_time
        and row["valid_time"] == valid_time
        and row["available_at"] == available_at
        and row["fetch_time"] == fetch_time
    )


def _snapshot_identity_matches_conflict_key(
    row,
    *,
    city,
    target_date: str,
    temperature_metric: str,
    data_version: str,
    issue_time: str | None,
) -> bool:
    """Identity check using ONLY ensemble_snapshots_v2's ON CONFLICT key.

    Mirrors ON CONFLICT(city, target_date, temperature_metric, issue_time,
    data_version) on the v2 table. Mutable fields (model_version, available_at,
    fetch_time, valid_time, ...) are intentionally excluded so legacy
    projection can mirror v2's UPDATE-on-conflict instead of fail-closing
    when the same snapshot is refreshed in-cycle.
    """
    return (
        row is not None
        and row["city"] == city.name
        and row["target_date"] == target_date
        and row["temperature_metric"] == temperature_metric
        and row["data_version"] == data_version
        and row["issue_time"] == issue_time
    )


def _legacy_snapshot_projection_row(conn, legacy_table: str, snapshot_id: str):
    return conn.execute(f"""
        SELECT city, target_date, issue_time, valid_time, available_at,
               fetch_time, model_version, data_version, temperature_metric
        FROM {legacy_table}
        WHERE snapshot_id = ?
    """, (snapshot_id,)).fetchone()


def _ensure_legacy_snapshot_projection(
    conn,
    *,
    legacy_table: str,
    snapshot_id: str,
    city,
    target_date: str,
    issue_time: str | None,
    valid_time: str | None,
    available_at: str,
    fetch_time: str,
    lead_hours: float,
    members_json: str,
    spread: float,
    is_bimodal: int,
    model_version: str,
    data_version: str,
    authority: str,
    temperature_metric: str,
) -> None:
    existing = _legacy_snapshot_projection_row(conn, legacy_table, snapshot_id)
    if existing is not None:
        # Codex P1 follow-up to PR #37: ensemble_snapshots_v2 INSERT uses
        #   ON CONFLICT(city, target_date, temperature_metric, issue_time,
        #               data_version)
        #   DO UPDATE SET model_version, available_at, fetch_time, valid_time,
        #                 lead_hours, members_json, spread, is_bimodal, ...
        # so the same snapshot_id is reused whenever a snapshot is refreshed
        # in-cycle. The legacy projection must mirror that upsert. Otherwise
        # any mid-cycle ensemble refresh raises a spurious identity mismatch
        # and aborts ENS storage for the rest of the cycle — which is the
        # same halt-class failure mode PR #40 removed for the oracle gate.
        #
        # Conflict-key fields stay immutable: a different city / target_date /
        # temperature_metric / issue_time / data_version under the same
        # snapshot_id is genuine row reuse and remains a fail-closed error.
        if not _snapshot_identity_matches_conflict_key(
            existing,
            city=city,
            target_date=target_date,
            temperature_metric=temperature_metric,
            data_version=data_version,
            issue_time=issue_time,
        ):
            raise ValueError(
                "legacy ensemble snapshot projection refused: snapshot_id "
                f"{snapshot_id} already belongs to {existing['city']}/"
                f"{existing['target_date']}/{existing['temperature_metric']}"
            )
        conn.execute(f"""
            UPDATE {legacy_table}
            SET model_version = ?,
                available_at = ?,
                fetch_time = ?,
                valid_time = ?,
                lead_hours = ?,
                members_json = ?,
                spread = ?,
                is_bimodal = ?,
                authority = ?
            WHERE snapshot_id = ?
        """, (
            model_version,
            available_at,
            fetch_time,
            valid_time,
            lead_hours,
            members_json,
            spread,
            is_bimodal,
            authority,
            snapshot_id,
        ))
        return

    conn.execute(f"""
        INSERT INTO {legacy_table}
        (snapshot_id, city, target_date, issue_time, valid_time, available_at,
         fetch_time, lead_hours, members_json, spread, is_bimodal,
         model_version, data_version, authority, temperature_metric)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        snapshot_id,
        city.name,
        target_date,
        issue_time,
        valid_time,
        available_at,
        fetch_time,
        lead_hours,
        members_json,
        spread,
        is_bimodal,
        model_version,
        data_version,
        authority,
        temperature_metric,
    ))
    inserted = _legacy_snapshot_projection_row(conn, legacy_table, snapshot_id)
    if not _snapshot_identity_matches(
        inserted,
        city=city,
        target_date=target_date,
        temperature_metric=temperature_metric,
        data_version=data_version,
        model_version=model_version,
        issue_time=issue_time,
        valid_time=valid_time,
        available_at=available_at,
        fetch_time=fetch_time,
    ):
        raise ValueError(
            "legacy ensemble snapshot projection verification failed for "
            f"snapshot_id {snapshot_id}"
        )


def _store_ens_snapshot(conn, city, target_date, ens, ens_result) -> str:
    """Store every ENS fetch and return the snapshot_id."""

    try:
        legacy_table = _ensemble_snapshots_table(conn)
        v2_table = _ensemble_snapshots_v2_table(conn)
        issue_time_value = _snapshot_issue_time_value(ens_result)
        valid_time_value = _snapshot_valid_time_value(target_date, ens_result)
        fetch_time_value = _snapshot_time_value(ens_result.get("fetch_time"))
        if fetch_time_value is None:
            raise ValueError("ENS snapshot missing fetch_time")
        available_at_value = _forecast_available_time_value(ens_result) or fetch_time_value

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

        data_version = _metric_identity.data_version
        assert_data_version_allowed(
            data_version,
            context=f"evaluator._store_ens_snapshot:{city.name}:{target_date}:{snap_metric}",
        )
        members_unit = _members_unit_for_snapshot(city, ens_result)
        validate_members_unit(
            members_unit,
            context=f"evaluator._store_ens_snapshot:{city.name}:{target_date}:{snap_metric}",
        )
        member_extrema = (
            ens.member_extrema
            if isinstance(getattr(ens, "member_extrema", None), np.ndarray)
            else ens.member_maxes
        )
        members_json = json.dumps(member_extrema.tolist())
        degradation_level = str(ens_result.get("degradation_level") or "OK")
        source_role = str(ens_result.get("forecast_source_role") or "entry_primary")
        source_id = str(ens_result.get("source_id") or ens_result.get("model") or "")
        training_allowed = int(
            issue_time_value is not None
            and degradation_level == "OK"
            and source_role == "entry_primary"
        )
        causality_status = "OK" if training_allowed else (
            "RUNTIME_ONLY_FALLBACK" if source_role != "entry_primary" or degradation_level != "OK" else "UNKNOWN"
        )
        authority = "VERIFIED" if degradation_level == "OK" and source_role == "entry_primary" else "UNVERIFIED"
        provenance_json = json.dumps({
            "writer": "evaluator._store_ens_snapshot",
            "source_id": source_id,
            "model": ens_result.get("model"),
            "raw_payload_hash": ens_result.get("raw_payload_hash"),
            "authority_tier": ens_result.get("authority_tier"),
            "degradation_level": degradation_level,
            "forecast_source_role": source_role,
            "legacy_projection_table": legacy_table,
        }, sort_keys=True)
        lead_hours = max(
            0.0,
            lead_hours_to_date_start(
                target_date,
                city.timezone,
                ens_result.get("fetch_time"),
            ),
        )

        snapshot_id = ""
        spread = ens.spread_float()
        is_bimodal = int(ens.is_bimodal())
        model_version = ens_result["model"]
        if v2_table:
            conn.execute(f"""
                INSERT INTO {v2_table}
                (city, target_date, temperature_metric, physical_quantity, observation_field,
                 issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
                 spread, is_bimodal, model_version, data_version, training_allowed,
                 causality_status, boundary_ambiguous, provenance_json, authority,
                 members_unit, unit)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(city, target_date, temperature_metric, issue_time, data_version)
                DO UPDATE SET
                    physical_quantity = excluded.physical_quantity,
                    observation_field = excluded.observation_field,
                    valid_time = excluded.valid_time,
                    available_at = excluded.available_at,
                    fetch_time = excluded.fetch_time,
                    lead_hours = excluded.lead_hours,
                    members_json = excluded.members_json,
                    spread = excluded.spread,
                    is_bimodal = excluded.is_bimodal,
                    model_version = excluded.model_version,
                    training_allowed = excluded.training_allowed,
                    causality_status = excluded.causality_status,
                    boundary_ambiguous = excluded.boundary_ambiguous,
                    provenance_json = excluded.provenance_json,
                    authority = excluded.authority,
                    members_unit = excluded.members_unit,
                    unit = excluded.unit
            """, (
                city.name,
                target_date,
                snap_metric,
                _metric_identity.physical_quantity,
                _metric_identity.observation_field,
                issue_time_value,
                valid_time_value,
                available_at_value,
                fetch_time_value,
                lead_hours,
                members_json,
                spread,
                is_bimodal,
                model_version,
                data_version,
                training_allowed,
                causality_status,
                0,
                provenance_json,
                authority,
                members_unit,
                getattr(city, "settlement_unit", None),
            ))
            row = conn.execute(f"""
                SELECT snapshot_id FROM {v2_table}
                WHERE city = ?
                  AND target_date = ?
                  AND temperature_metric = ?
                  AND data_version = ?
                  AND model_version = ?
                  AND available_at = ?
                  AND fetch_time = ?
                  AND ((issue_time IS NULL AND ? IS NULL) OR issue_time = ?)
                  AND ((valid_time IS NULL AND ? IS NULL) OR valid_time = ?)
                ORDER BY snapshot_id DESC
                LIMIT 1
            """, (
                city.name,
                target_date,
                snap_metric,
                data_version,
                model_version,
                available_at_value,
                fetch_time_value,
                issue_time_value,
                issue_time_value,
                valid_time_value,
                valid_time_value,
            )).fetchone()
            snapshot_id = str(row["snapshot_id"]) if row is not None else ""
            if not snapshot_id:
                raise ValueError(
                    "canonical ensemble_snapshots_v2 insert/lookup failed; "
                    "refusing to fall back to legacy ensemble_snapshots authority"
                )
            _ensure_legacy_snapshot_projection(
                conn,
                legacy_table=legacy_table,
                snapshot_id=snapshot_id,
                city=city,
                target_date=target_date,
                issue_time=issue_time_value,
                valid_time=valid_time_value,
                available_at=available_at_value,
                fetch_time=fetch_time_value,
                lead_hours=lead_hours,
                members_json=members_json,
                spread=spread,
                is_bimodal=is_bimodal,
                model_version=model_version,
                data_version=data_version,
                authority=authority,
                temperature_metric=snap_metric,
            )
        else:
            conn.execute(f"""
                INSERT OR IGNORE INTO {legacy_table}
                (city, target_date, issue_time, valid_time, available_at, fetch_time,
                 lead_hours, members_json, spread, is_bimodal, model_version,
                 data_version, authority, temperature_metric)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                city.name,
                target_date,
                issue_time_value,
                valid_time_value,
                available_at_value,
                fetch_time_value,
                lead_hours,
                members_json,
                spread,
                is_bimodal,
                model_version,
                data_version,
                authority,
                snap_metric,
            ))
            row = conn.execute(f"""
                SELECT snapshot_id FROM {legacy_table}
                WHERE city = ?
                  AND target_date = ?
                  AND data_version = ?
                  AND temperature_metric = ?
                  AND model_version = ?
                  AND available_at = ?
                  AND fetch_time = ?
                  AND ((issue_time IS NULL AND ? IS NULL) OR issue_time = ?)
                  AND ((valid_time IS NULL AND ? IS NULL) OR valid_time = ?)
                ORDER BY snapshot_id DESC
                LIMIT 1
            """, (
                city.name,
                target_date,
                data_version,
                snap_metric,
                model_version,
                available_at_value,
                fetch_time_value,
                issue_time_value,
                issue_time_value,
                valid_time_value,
                valid_time_value,
            )).fetchone()
            snapshot_id = str(row["snapshot_id"]) if row is not None else ""
        conn.commit()
        return snapshot_id
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("Failed to store ENS snapshot: %s", e)
        return ""


def _store_snapshot_p_raw(
    conn,
    snapshot_id: str,
    p_raw: np.ndarray,
    *,
    bias_corrected: bool = False,
    p_raw_topology: dict | None = None,
) -> bool:
    """Persist the decision-time p_raw vector and bias_corrected flag onto the snapshot row."""

    if not snapshot_id:
        return False

    import json

    try:
        p_raw_json = json.dumps(p_raw.tolist())
        topology_payload = None
        if p_raw_topology is not None:
            topology_payload = json.loads(json.dumps(p_raw_topology, sort_keys=True))
            expected_count = int(len(p_raw))
            if topology_payload.get("schema_version") != 1:
                raise ValueError("p_raw_topology schema_version must be 1")
            if topology_payload.get("topology_status") != "complete":
                raise ValueError("p_raw_topology topology_status must be complete")
            topology_support_count = topology_payload.get("support_count")
            if topology_support_count != expected_count:
                raise ValueError(
                    "p_raw_topology support_count does not match p_raw cardinality: "
                    f"{topology_support_count} != {expected_count}"
                )
            for key in (
                "executable_mask",
                "support",
                "market_fusion_status_by_support_index",
            ):
                value = topology_payload.get(key)
                if not isinstance(value, list) or len(value) != expected_count:
                    raise ValueError(
                        f"p_raw_topology {key} cardinality does not match p_raw: "
                        f"{len(value) if isinstance(value, list) else 'missing'} != {expected_count}"
                    )
            executable_mask = topology_payload["executable_mask"]
            if not all(isinstance(is_executable, bool) for is_executable in executable_mask):
                raise ValueError("p_raw_topology executable_mask must contain booleans")
            executable_count = int(sum(1 for is_executable in executable_mask if is_executable))
            if topology_payload.get("executable_count") != executable_count:
                raise ValueError("p_raw_topology executable_count does not match executable_mask")
            executable_hypothesis_count = topology_payload.get("executable_hypothesis_count")
            if executable_hypothesis_count != executable_count:
                raise ValueError(
                    "p_raw_topology executable_hypothesis_count does not match executable_mask"
                )
            skipped_support_indexes = [
                idx for idx, is_executable in enumerate(executable_mask) if not is_executable
            ]
            if topology_payload.get("skipped_support_indexes") != skipped_support_indexes:
                raise ValueError(
                    "p_raw_topology skipped_support_indexes does not match executable_mask"
                )
            if bool(topology_payload.get("requires_atomic_topology")) != bool(skipped_support_indexes):
                raise ValueError(
                    "p_raw_topology requires_atomic_topology does not match executable_mask"
                )
            allowed_fusion_status = {
                True: "pending_executable_quote",
                False: "disabled_non_executable",
            }
            for idx, support in enumerate(topology_payload["support"]):
                if not isinstance(support, dict):
                    raise ValueError("p_raw_topology support entries must be objects")
                if support.get("support_index") != idx:
                    raise ValueError("p_raw_topology support_index sequence is invalid")
                if support.get("executable") != executable_mask[idx]:
                    raise ValueError("p_raw_topology support executable flag mismatch")
            for idx, status in enumerate(topology_payload["market_fusion_status_by_support_index"]):
                if not isinstance(status, dict):
                    raise ValueError("p_raw_topology fusion status entries must be objects")
                if status.get("support_index") != idx:
                    raise ValueError("p_raw_topology fusion support_index sequence is invalid")
                if status.get("status") != allowed_fusion_status[executable_mask[idx]]:
                    raise ValueError(
                        "p_raw_topology fusion status does not match executable_mask"
                    )
        snapshots_table = _ensemble_snapshots_table(conn)
        v2_table = _ensemble_snapshots_v2_table(conn)
        if v2_table:
            v2_row = conn.execute(f"""
                SELECT city, target_date, issue_time, valid_time, available_at,
                       fetch_time, model_version, data_version, temperature_metric,
                       provenance_json
                FROM {v2_table}
                WHERE snapshot_id = ?
            """, (snapshot_id,)).fetchone()
            if v2_row is None:
                if topology_payload and bool(topology_payload.get("requires_atomic_topology")):
                    raise ValueError(
                        "canonical p_raw_topology persistence requires ensemble_snapshots_v2 "
                        f"for partial-executable support snapshot_id {snapshot_id}"
                    )
                result = conn.execute(
                    f"UPDATE {snapshots_table} SET p_raw_json = ?, bias_corrected = ? WHERE snapshot_id = ?",
                    (p_raw_json, int(bias_corrected), snapshot_id),
                )
                if result.rowcount != 1:
                    raise ValueError(
                        "legacy-only ensemble_snapshots p_raw update affected "
                        f"{result.rowcount} rows for snapshot_id {snapshot_id}"
                    )
                conn.commit()
                return True
            if topology_payload is not None:
                try:
                    provenance = json.loads(v2_row["provenance_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    provenance = {}
                if not isinstance(provenance, dict):
                    provenance = {}
                provenance["p_raw_topology"] = topology_payload
                provenance_json = json.dumps(provenance, sort_keys=True)
                result = conn.execute(
                    f"UPDATE {v2_table} SET p_raw_json = ?, provenance_json = ? WHERE snapshot_id = ?",
                    (p_raw_json, provenance_json, snapshot_id),
                )
            else:
                result = conn.execute(
                    f"UPDATE {v2_table} SET p_raw_json = ? WHERE snapshot_id = ?",
                    (p_raw_json, snapshot_id),
                )
            if result.rowcount != 1:
                raise ValueError(
                    "canonical ensemble_snapshots_v2 p_raw update affected "
                    f"{result.rowcount} rows for snapshot_id {snapshot_id}"
                )
            result = conn.execute(f"""
                UPDATE {snapshots_table}
                SET p_raw_json = ?, bias_corrected = ?
                WHERE snapshot_id = ?
                  AND city = ?
                  AND target_date = ?
                  AND data_version = ?
                  AND temperature_metric = ?
                  AND model_version = ?
                  AND available_at = ?
                  AND fetch_time = ?
                  AND ((issue_time IS NULL AND ? IS NULL) OR issue_time = ?)
                  AND ((valid_time IS NULL AND ? IS NULL) OR valid_time = ?)
            """, (
                p_raw_json,
                int(bias_corrected),
                snapshot_id,
                v2_row["city"],
                v2_row["target_date"],
                v2_row["data_version"],
                v2_row["temperature_metric"],
                v2_row["model_version"],
                v2_row["available_at"],
                v2_row["fetch_time"],
                v2_row["issue_time"],
                v2_row["issue_time"],
                v2_row["valid_time"],
                v2_row["valid_time"],
            ))
            if result.rowcount != 1:
                raise ValueError(
                    "legacy ensemble_snapshots p_raw projection update affected "
                    f"{result.rowcount} rows for canonical snapshot_id {snapshot_id}"
                )
            conn.commit()
            return True
        result = conn.execute(
            f"UPDATE {snapshots_table} SET p_raw_json = ?, bias_corrected = ? WHERE snapshot_id = ?",
            (p_raw_json, int(bias_corrected), snapshot_id),
        )
        if result.rowcount != 1:
            raise ValueError(
                "legacy ensemble_snapshots p_raw update affected "
                f"{result.rowcount} rows for snapshot_id {snapshot_id}"
            )
        conn.commit()
        return True
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("Failed to store snapshot p_raw for %s: %s", snapshot_id, e)
        return False
