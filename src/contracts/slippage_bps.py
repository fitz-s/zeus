"""Slippage in basis points — typed contract for T5.d.

Plan row T5.d emphasizes: fresh ``SlippageBps`` semantic type, NOT
aliased on ``TemperatureDelta``. Temperature deltas and price
slippages share superficial shape (signed numeric with a unit) but
carry orthogonal domain semantics; aliasing would open a category of
cross-domain type-reuse bugs (``TemperatureDelta(5, "F")`` +
``TemperatureDelta(10, "bps")`` would both type-check as
``TemperatureDelta`` yet mean completely different things).

Resolution: dedicated ``SlippageBps`` dataclass with explicit
``direction`` semantics (adverse / favorable / zero) so every
constructed slippage is unambiguously interpretable without the
caller having to remember a sign convention.

See: docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T5.d
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from src.contracts.execution_price import ExecutionPrice


@dataclass(frozen=True)
class SlippageBps:
    """Execution-price slippage expressed in basis points.

    One basis point = 0.01% = 0.0001 fractional deviation. ``value_bps``
    is stored as a NON-NEGATIVE magnitude; ``direction`` carries the
    sign semantics:

    - ``"adverse"``  — fill was worse than expected (buy paid more or
      sell received less).
    - ``"favorable"`` — fill was better than expected (buy paid less
      or sell received more).
    - ``"zero"``     — fill equalled expected; ``value_bps`` must be 0.

    Callers that want the pure fractional deviation can use the
    ``fraction`` property; it returns a NON-NEGATIVE value — the sign
    is always carried by ``direction`` to avoid the silent-convention
    failure mode that motivates this contract.
    """

    value_bps: float
    direction: Literal["adverse", "favorable", "zero"]

    def __post_init__(self) -> None:
        if not math.isfinite(self.value_bps):
            raise ValueError(
                f"SlippageBps.value_bps must be finite, got {self.value_bps}"
            )
        if self.value_bps < 0.0:
            raise ValueError(
                f"SlippageBps.value_bps must be non-negative magnitude; "
                f"sign is encoded in direction. Got {self.value_bps}"
            )
        if self.direction == "zero" and self.value_bps != 0.0:
            raise ValueError(
                f"SlippageBps.direction='zero' requires value_bps=0, "
                f"got value_bps={self.value_bps}"
            )
        if self.direction in ("adverse", "favorable") and self.value_bps == 0.0:
            raise ValueError(
                f"SlippageBps.direction='{self.direction}' is incompatible "
                f"with value_bps=0; use direction='zero' for exact fills"
            )

    @property
    def fraction(self) -> float:
        """Return slippage as a fractional deviation (NOT percent).

        Always non-negative — sign is carried by ``direction``.
        ``value_bps=50`` → ``fraction=0.005`` (50 bps = 0.5%).
        """
        return self.value_bps / 10_000.0

    @classmethod
    def from_prices(
        cls,
        *,
        actual: "ExecutionPrice",
        expected: "ExecutionPrice",
        side: Literal["buy", "sell"],
    ) -> "SlippageBps":
        """Compute slippage from an actual vs expected ExecutionPrice pair.

        Direction semantics are side-dependent:
        - BUY: actual > expected → adverse (paid more); actual <
          expected → favorable; equal → zero.
        - SELL: actual > expected → favorable (received more);
          actual < expected → adverse; equal → zero.

        Both prices must share the same currency (typically
        ``probability_units`` for Polymarket); cross-currency slippage
        is ill-defined and rejected.
        """
        if actual.currency != expected.currency:
            raise ValueError(
                f"SlippageBps.from_prices: currency mismatch "
                f"actual={actual.currency!r} expected={expected.currency!r}"
            )
        if side not in ("buy", "sell"):
            raise ValueError(
                f"SlippageBps.from_prices: side must be 'buy' or 'sell', "
                f"got {side!r}"
            )
        if expected.value <= 0.0:
            raise ValueError(
                f"SlippageBps.from_prices: expected_price.value must be > 0 "
                f"to compute relative slippage, got {expected.value}"
            )

        raw_delta = actual.value - expected.value  # signed

        # Side flips the sense of "adverse vs favorable":
        # BUY  paying more = adverse  => adverse when raw_delta > 0
        # SELL receiving more = favorable => favorable when raw_delta > 0
        if raw_delta == 0.0:
            return cls(value_bps=0.0, direction="zero")

        magnitude_bps = abs(raw_delta) / expected.value * 10_000.0

        if side == "buy":
            direction: Literal["adverse", "favorable"] = (
                "adverse" if raw_delta > 0 else "favorable"
            )
        else:  # sell
            direction = "favorable" if raw_delta > 0 else "adverse"

        return cls(value_bps=magnitude_bps, direction=direction)
