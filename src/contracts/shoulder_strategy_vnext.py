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

    Wires registry-driven classification per plan §2 T2 (kills hardcoded
    shoulder triplicate at evaluator.py L1462/L1478/L1494).

    SCAFFOLD — production logic in T2 production pass.
    """
    raise NotImplementedError("T2 production pass owns classify_shoulder_candidate body")
