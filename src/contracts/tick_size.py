"""Minimum price increment typed contract — T5.b resolution.

T5.a (ExecutionPrice) typed the entry-path price at the Kelly boundary.
T5.b follows the same pattern for minimum price increment (tick size).
Polymarket weather markets trade in $0.01 probability-unit ticks. Bare
``0.01`` literals are scattered across the executor and
``semantic_types.py`` — each one carries no type, no provenance, no
single authority. Grep-driven hunts would be the only way to propagate
a change if Polymarket ever introduced finer ticks on liquid markets.

Resolution: every tick-size reference flows through
``TickSize.for_market(...)``. Changing the assumed tick is one edit;
every call site inherits automatically.

See: docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T5.b
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TickSize:
    """Typed minimum price increment for a Polymarket market.

    Invariants enforced at ``__post_init__`` prevent NaN, non-finite,
    zero, negative, or degenerate values from constructing. Callers
    that compute a tick at runtime (e.g., pulling from a venue API)
    will fail at the contract boundary rather than silently poisoning
    downstream sizing / limit math.

    Attributes:
        value: Tick increment in probability units. Must be finite,
            > 0, and <= 0.5. Values > 0.5 are degenerate because they
            push ``min_valid_price`` above ``max_valid_price``.
        currency: Unit of value. Only ``"probability_units"`` is
            meaningful for Polymarket weather markets; the discriminator
            is explicit so future expansion to USD-denominated venues
            stays type-safe.
    """

    value: float
    currency: Literal["probability_units"]

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise ValueError(
                f"TickSize.value must be finite, got {self.value}"
            )
        if self.value <= 0.0:
            raise ValueError(
                f"TickSize.value must be > 0, got {self.value}"
            )
        if self.value > 0.5:
            raise ValueError(
                f"TickSize.value must be <= 0.5 (degenerate: "
                f"min_valid_price would exceed max_valid_price), "
                f"got {self.value}"
            )

    @property
    def min_valid_price(self) -> float:
        """Smallest valid limit price: one tick above 0."""
        return self.value

    @property
    def max_valid_price(self) -> float:
        """Largest valid limit price: one tick below 1."""
        return 1.0 - self.value

    def clamp_to_valid_range(self, price: float) -> float:
        """Clamp ``price`` to ``[min_valid_price, max_valid_price]``.

        Does NOT reject NaN — callers that cannot tolerate non-finite
        input must guard upstream (see
        ``src/execution/executor.py::execute_exit_order`` for the
        exit-path reject pattern that bundles the T5.a NaN-clamp
        follow-up closure). Keeping this method lenient preserves
        backward compatibility for pure-compute call sites in
        ``src/contracts/semantic_types.py`` whose callers rely on NaN
        propagating to a downstream typed-contract boundary.
        """
        return max(self.min_valid_price, min(self.max_valid_price, price))

    @classmethod
    def for_market(
        cls,
        market_id: str | None = None,
        token_id: str | None = None,
    ) -> "TickSize":
        """Canonical TickSize for a Polymarket market.

        All Polymarket weather markets currently trade in $0.01
        probability-unit ticks. ``market_id`` and ``token_id`` are in
        the signature so the contract is future-proof against
        differentiated ticks (e.g., $0.001 on liquid center bins)
        without requiring caller signature changes — a future
        introduction of per-market tick resolution flows through this
        one classmethod.
        """
        return cls(value=0.01, currency="probability_units")


# Module-level constant for call sites that do not need per-market
# resolution (e.g., semantic_types.py's generic clamp, which runs
# upstream of market_id availability).
POLYMARKET_WEATHER_TICK: TickSize = TickSize(
    value=0.01,
    currency="probability_units",
)
