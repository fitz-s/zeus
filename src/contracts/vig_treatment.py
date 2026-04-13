"""Vig normalization contract — D5 resolution.

D5 gap: p_market includes vig (~0.90–1.10). compute_posterior() blends
α × p_cal + (1-α) × p_market then normalizes. This smears vig bias across all
bins because the blend happens before vig removal. The normalization step partially
corrects it but the blend coefficients were already contaminated.

Resolution: Vig normalization (p_market_clean = p_market / vig) must happen
BEFORE the blend, under a declared VigTreatment contract.

See: docs/zeus_FINAL_spec.md §P9.3 D5
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class VigTreatment:
    """Typed record of vig normalization applied before market-model blending.

    Resolves D5: ensures vig is removed before the posterior blend, not after,
    and that the cleaning step is explicit and auditable.

    Attributes:
        raw_market_prices: Original p_market array summing to plausible vig (~0.90–1.10).
        vig_factor: The vig total (sum of raw_market_prices). Used to derive
            clean_prices = raw_market_prices / vig_factor.
        clean_prices: Normalized market probabilities summing to 1.0, suitable
            for blending with p_cal.
        applied_before_blend: Must be True. If False, the vig was removed AFTER
            blending, which is the D5 violation pattern.
    """

    raw_market_prices: np.ndarray
    vig_factor: float
    clean_prices: np.ndarray
    applied_before_blend: bool

    def __post_init__(self) -> None:
        if not np.all(np.isfinite(self.raw_market_prices)):
            raise ValueError("VigTreatment.raw_market_prices must be finite")
        if np.any(self.raw_market_prices < 0.0):
            raise ValueError("VigTreatment.raw_market_prices must be non-negative")
        if not np.all(np.isfinite(self.clean_prices)):
            raise ValueError("VigTreatment.clean_prices must be finite")
        if np.any(self.clean_prices < 0.0):
            raise ValueError("VigTreatment.clean_prices must be non-negative")
        if self.vig_factor <= 0.0:
            raise ValueError(
                f"VigTreatment.vig_factor must be > 0, got {self.vig_factor}"
            )
        if len(self.raw_market_prices) != len(self.clean_prices):
            raise ValueError(
                f"VigTreatment: raw_market_prices length {len(self.raw_market_prices)} "
                f"!= clean_prices length {len(self.clean_prices)}"
            )

        # Verify clean_prices actually sum to ~1.0 (tolerance for float arithmetic)
        clean_sum = float(np.sum(self.clean_prices))
        if not (0.99 <= clean_sum <= 1.01):
            raise ValueError(
                f"VigTreatment.clean_prices must sum to ~1.0, got {clean_sum:.4f}. "
                "Ensure clean_prices = raw_market_prices / vig_factor."
            )

        if not self.applied_before_blend:
            raise VigOrderError(
                "VigTreatment.applied_before_blend=False. Vig must be removed "
                "BEFORE blending with p_cal. Post-blend normalization smears vig "
                "bias across all bins (D5 violation)."
            )

    @classmethod
    def from_raw(cls, raw_market_prices: np.ndarray) -> "VigTreatment":
        """Factory: compute vig_factor and clean_prices from raw market prices.

        This is the canonical way to construct a VigTreatment — it ensures
        vig removal happens at construction time (before any blend call).
        """
        if not np.all(np.isfinite(raw_market_prices)):
            raise ValueError("raw_market_prices must be finite")
        if np.any(raw_market_prices < 0.0):
            raise ValueError("raw_market_prices must be non-negative")
        vig_factor = float(np.sum(raw_market_prices))
        if vig_factor <= 0.0:
            raise ValueError(
                f"raw_market_prices sum to {vig_factor}, cannot compute vig"
            )
        clean_prices = raw_market_prices / vig_factor
        return cls(
            raw_market_prices=raw_market_prices,
            vig_factor=vig_factor,
            clean_prices=clean_prices,
            applied_before_blend=True,
        )


class VigOrderError(Exception):
    """Raised when VigTreatment is constructed with applied_before_blend=False.
    This is the D5 runtime contract violation — vig applied after blending
    smears the vig bias across all bins, distorting edge signal.
    """
