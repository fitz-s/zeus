"""Tail alpha scaling decision contract — D2 resolution.

D2 gap: market_fusion.compute_posterior() applies TAIL_ALPHA_SCALE=0.5 to tail
bins, validated against Brier score. But buy_no's ~87.5% base win rate comes from
market tail overpricing (lottery effect). Scaling α toward market on tails directly
halves the edge buy_no depends on — the calibration gain destroys the profit source.

Resolution: Any tail α scaling must be wrapped in TailTreatment and must declare
whether it serves calibration_accuracy OR profit. If profit, it must be validated
against buy_no P&L, not Brier.

See: docs/zeus_FINAL_spec.md §P9.3 D2
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TailTreatment:
    """Typed tail-bin α scaling decision carrying its declared optimization objective.

    Resolves D2: prevents TAIL_ALPHA_SCALE from silently destroying buy_no edge
    under the pretense of Brier calibration improvement.

    Attributes:
        scale_factor: Multiplier applied to α in tail bins, e.g. 0.5.
            Must be in (0.0, 1.0].
        serves: What objective this scaling serves.
            "calibration_accuracy" — reduces Brier on tail bins
            "profit"               — must be validated against buy_no P&L, not Brier
        validated_against: Dataset or sweep reference used to justify scale_factor
            (e.g. "D3 sweep 2026-03-31 bins=[open-ended], Brier −0.042").
    """

    scale_factor: float
    serves: Literal["calibration_accuracy", "profit"]
    validated_against: str

    def __post_init__(self) -> None:
        if not 0.0 < self.scale_factor <= 1.0:
            raise ValueError(
                f"TailTreatment.scale_factor must be in (0, 1], got {self.scale_factor}"
            )
        if self.serves not in {"calibration_accuracy", "profit"}:
            raise ValueError(
                "TailTreatment.serves must be 'calibration_accuracy' or 'profit', "
                f"got {self.serves!r}"
            )
        if not self.validated_against:
            raise ValueError("TailTreatment.validated_against must not be empty")

    def warn_if_profit_unvalidated(self) -> None:
        """Log a warning if serves='profit' but validation reference looks Brier-based.

        A scale_factor that was only validated on Brier must not claim serves='profit'
        without buy_no P&L evidence.
        """
        if self.serves == "profit" and "brier" in self.validated_against.lower():
            import warnings
            warnings.warn(
                f"TailTreatment declares serves='profit' but validated_against "
                f"reference appears Brier-based: '{self.validated_against}'. "
                "Validate against buy_no P&L before claiming profit optimization.",
                stacklevel=2,
            )
