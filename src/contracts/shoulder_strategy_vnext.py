# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T2 + 04_PHASE_3_SHOULDER.md

"""ShoulderStrategyVNext — 21-field frozen dataclass for open-shoulder strategy context.

Verbatim field list from authority 04_PHASE_3_SHOULDER.md §"Required object model"
(verifier recount 2026-05-21: 21 rows from is_open_shoulder through no_trade_reason).

SCAFFOLD — classify_shoulder_candidate body raises NotImplementedError.
Production logic wired in T2 production pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from src.contracts.execution_price import ExecutionPrice
from src.contracts.no_trade_reason import NoTradeReason
from src.contracts.weather_regime_tag import WeatherRegimeTag

# Type aliases — str-backed IDs; no runtime enforcement beyond str.
HypothesisFamilyId = str
ClusterId = str
BinId = str


@dataclass(frozen=True)
class ShoulderStrategyVNext:
    """21-field frozen dataclass for open-shoulder strategy context.

    Fields are verbatim from authority 04_PHASE_3_SHOULDER.md §"Required object model".
    """

    # ── Gate ──────────────────────────────────────────────────────────────────
    is_open_shoulder: bool

    # ── Topology ──────────────────────────────────────────────────────────────
    shoulder_side: Literal["upper", "lower"]
    metric: Literal["high", "low"]
    tail_direction: Literal["above_threshold", "below_threshold"]
    finite_adjacent_bin: Optional[BinId]

    # ── Probabilities ─────────────────────────────────────────────────────────
    tail_probability_raw: float
    tail_probability_calibrated: float
    tail_probability_stressed: float

    # ── Regime + diagnostics ──────────────────────────────────────────────────
    tail_regime_tag: WeatherRegimeTag
    retail_lottery_bias_score: float
    extreme_weather_underpricing_score: float
    source_anomaly_score: float

    # ── Market quotes ─────────────────────────────────────────────────────────
    native_yes_quote: Optional[ExecutionPrice]
    native_no_quote: Optional[ExecutionPrice]
    liquidity_gate: bool

    # ── Family + cluster ──────────────────────────────────────────────────────
    shoulder_family_id: HypothesisFamilyId
    tail_correlation_cluster: ClusterId

    # ── Risk sizing ───────────────────────────────────────────────────────────
    max_loss_scenario: float
    kelly_haircut: float
    max_exposure_cap: float

    # ── Decision gate ─────────────────────────────────────────────────────────
    no_trade_reason: Optional[NoTradeReason]


def classify_shoulder_candidate(
    edge,
    candidate,
    market_phase,
    conn,
) -> Optional[ShoulderStrategyVNext]:
    """Classify a shoulder candidate into a ShoulderStrategyVNext contract.

    Pattern B (thin) per dossier §7.4 variant 5: populate topology fields from
    edge; all probabilistic/diagnostic fields = nan/None/0.0;
    no_trade_reason = SHOULDER_NO_TRADE_GATE.

    Returns None when edge is not an open-shoulder buy_no (topology gate fails).
    Never raises on missing probabilistic data — thin mode is the intended path
    for Phase 3 T2.

    T3+ will wire tail_probability_* from ensemble context when available.
    """
    # Topology gate — only open-shoulder buy_no edges become ShoulderStrategyVNext.
    if not (edge.direction == "buy_no" and edge.bin.is_shoulder):
        return None

    b = edge.bin
    shoulder_side: Literal["upper", "lower"] = "upper" if b.is_open_high else "lower"
    tail_direction: Literal["above_threshold", "below_threshold"] = (
        "above_threshold" if b.is_open_high else "below_threshold"
    )

    # metric: "high" or "low" from candidate's temperature_metric field (str).
    # Validate to the two legal values; fallback to "high" (fail-safe direction).
    raw_metric = getattr(candidate, "temperature_metric", "high")
    metric: Literal["high", "low"] = (
        "low" if raw_metric == "low" else "high"
    )

    # Regime — requires conn; fail-open to UNKNOWN when conn=None (thin path).
    if conn is not None:
        try:
            from datetime import date as _date
            from src.contracts.weather_regime_tag import regime_tag_for as _regime_tag_for
            city_name = getattr(getattr(candidate, "city", None), "name", "")
            city_tz = getattr(getattr(candidate, "city", None), "timezone", "UTC")
            td_str = getattr(candidate, "target_date", "")
            from datetime import datetime as _datetime, timezone as _tz
            _target_date = _date.fromisoformat(td_str) if td_str else _date.today()
            _now = _datetime.now(_tz.utc)
            tail_regime_tag = _regime_tag_for(city_name, _target_date, _now, conn)
        except Exception:
            from src.contracts.weather_regime_tag import WeatherRegimeTag
            tail_regime_tag = WeatherRegimeTag.UNKNOWN
    else:
        from src.contracts.weather_regime_tag import WeatherRegimeTag
        tail_regime_tag = WeatherRegimeTag.UNKNOWN

    # Cluster — depends on regime.
    city_name_for_cluster = getattr(getattr(candidate, "city", None), "name", "")
    td_str_for_cluster = getattr(candidate, "target_date", "")
    try:
        from datetime import date as _date2
        from src.strategy.correlation_cluster import tail_correlation_cluster_for as _cluster_for
        _td2 = _date2.fromisoformat(td_str_for_cluster) if td_str_for_cluster else _date2.today()
        tail_correlation_cluster = _cluster_for(city_name_for_cluster, tail_regime_tag, _td2)
    except Exception:
        tail_correlation_cluster = ""

    # shoulder_family_id — grammar: shoulder:{city}:{metric}:{target_date}:{source}:{regime}
    # source: use candidate.slug or "thin" in Pattern B (no ensemble source_id available).
    source_for_family = (
        getattr(candidate, "slug", "") or
        getattr(candidate, "event_id", "") or
        "thin"
    )
    regime_for_family = tail_regime_tag.value if tail_regime_tag.value else "unknown"
    city_for_family = getattr(getattr(candidate, "city", None), "name", "")
    target_date_for_family = getattr(candidate, "target_date", "")
    try:
        from src.strategy.selection_family import make_shoulder_hypothesis_family_id
        shoulder_family_id = make_shoulder_hypothesis_family_id(
            city=city_for_family,
            metric=metric,
            target_date=target_date_for_family,
            source=source_for_family,
            regime=regime_for_family,
        )
    except Exception:
        shoulder_family_id = (
            f"shoulder:{city_for_family}:{metric}:{target_date_for_family}"
            f":{source_for_family}:{regime_for_family}"
        )

    nan = float("nan")

    return ShoulderStrategyVNext(
        # Gate
        is_open_shoulder=True,
        # Topology
        shoulder_side=shoulder_side,
        metric=metric,
        tail_direction=tail_direction,
        finite_adjacent_bin=None,  # T3+: derive from adjacent finite bin
        # Probabilities — thin: unavailable without ensemble context
        tail_probability_raw=nan,
        tail_probability_calibrated=nan,
        tail_probability_stressed=nan,
        # Regime + diagnostics
        tail_regime_tag=tail_regime_tag,
        retail_lottery_bias_score=nan,
        extreme_weather_underpricing_score=nan,
        source_anomaly_score=nan,
        # Market quotes — thin: not available at BinEdge level
        native_yes_quote=None,
        native_no_quote=None,
        liquidity_gate=False,
        # Family + cluster
        shoulder_family_id=shoulder_family_id,
        tail_correlation_cluster=tail_correlation_cluster,
        # Risk sizing — thin: deferred to T3
        max_loss_scenario=0.0,
        kelly_haircut=0.0,
        max_exposure_cap=0.0,
        # Decision gate — thin: no-trade until probabilistic fields populated
        no_trade_reason=NoTradeReason.SHOULDER_NO_TRADE_GATE,
    )
