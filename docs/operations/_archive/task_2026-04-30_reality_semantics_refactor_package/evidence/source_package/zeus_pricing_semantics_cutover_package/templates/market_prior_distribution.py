"""Illustrative skeleton only. Adapt to Zeus contracts after topology admission."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Sequence


@dataclass(frozen=True)
class MarketPriorDistribution:
    """Named market-prior estimator output.

    This is not a raw token quote. It is a probability distribution with lineage
    and validation status. Live posterior fusion may consume this only when
    validated_for_live is true, unless mode is explicit legacy/shadow.
    """

    values_yes: tuple[Decimal, ...]
    estimator_version: str
    source_quote_hashes: tuple[str, ...]
    family_complete: bool
    side_convention: Literal["YES_FAMILY", "NO_FAMILY", "MIXED_UNSUPPORTED"]
    vig_treatment: str
    freshness_status: Literal["FRESH", "STALE", "UNKNOWN"]
    liquidity_filter_status: Literal["PASS", "FAIL", "UNKNOWN"]
    neg_risk_policy: str
    validated_for_live: bool
    validation_evidence_id: str | None = None

    def assert_distribution(self) -> None:
        if not self.values_yes:
            raise ValueError("MarketPriorDistribution requires at least one value")
        total = sum(self.values_yes)
        if any(v < 0 or v > 1 for v in self.values_yes):
            raise ValueError("market prior values must be within [0, 1]")
        # Tolerance belongs in project settings if needed.
        if abs(total - Decimal("1")) > Decimal("0.000001"):
            raise ValueError(f"market prior must sum to 1, got {total}")
        if self.side_convention != "YES_FAMILY":
            raise ValueError("live prior currently requires YES_FAMILY convention")
        if not self.family_complete:
            raise ValueError("market prior requires complete family")
