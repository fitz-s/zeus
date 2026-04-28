# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/A1.yaml
"""Strategy candidate stubs for the A1 benchmark harness.

These classes advertise candidate identities only. They intentionally do not
compute alpha, place orders, or authorize live promotion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class StrategyProtocol(Protocol):
    strategy_key: str

    def describe(self) -> str: ...


@dataclass(frozen=True)
class CandidateMetadata:
    strategy_key: str
    family: str
    description: str
    executable_alpha: bool = False


@dataclass(frozen=True)
class BaseStrategyCandidate:
    metadata: CandidateMetadata

    @property
    def strategy_key(self) -> str:
        return self.metadata.strategy_key

    def describe(self) -> str:
        return self.metadata.description


from .weather_event_arbitrage import WeatherEventArbitrage
from .stale_quote_detector import StaleQuoteDetector
from .resolution_window_maker import ResolutionWindowMaker
from .neg_risk_basket import NegRiskBasket
from .cross_market_correlation_hedge import CrossMarketCorrelationHedge
from .liquidity_provision_with_heartbeat import LiquidityProvisionWithHeartbeat

__all__ = [
    "BaseStrategyCandidate",
    "CandidateMetadata",
    "CrossMarketCorrelationHedge",
    "LiquidityProvisionWithHeartbeat",
    "NegRiskBasket",
    "ResolutionWindowMaker",
    "StaleQuoteDetector",
    "StrategyProtocol",
    "WeatherEventArbitrage",
]
