"""Alpha blending decision contract — D1 resolution.

D1 gap: compute_alpha() produces an α value validated against Brier score, but
downstream (Kelly sizing) uses it as if it were an EV-optimized edge weight.
Brier-optimized α converges Zeus toward market, making edge → 0 when the market
is already calibrated.

Resolution: α outputs are wrapped in AlphaDecision. Downstream consumers must
declare their optimization_target. A Brier-optimized alpha fed into an EV-seeking
sizing path raises a runtime contract violation.

See: docs/zeus_FINAL_spec.md §P9.3 D1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AlphaDecision:
    """Typed α value carrying its optimization target and evidence basis.

    Resolves D1: prevents Brier-optimized α from silently flowing into EV-seeking
    sizing without a declared target mismatch check.

    Attributes:
        value: The blending weight α ∈ [0.20, 0.85].
        optimization_target: What objective drove this α.
            "brier_score"  — calibration accuracy (compute_alpha default)
            "ev"           — expected value maximization
            "risk_cap"     — defensive/risk-limit shrinkage
        evidence_basis: Free-form string describing the data or sweep that
            justified this value (e.g. "D4 sweep 2026-03-31, r=+0.214").
        ci_bound: Half-width of the confidence interval around alpha.value,
            propagated from the upstream calibration confidence band.
    """

    value: float
    optimization_target: Literal["brier_score", "ev", "risk_cap"]
    evidence_basis: str
    ci_bound: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise ValueError(
                f"AlphaDecision.value must be in [0, 1], got {self.value}"
            )
        if self.ci_bound < 0.0:
            raise ValueError(
                f"AlphaDecision.ci_bound must be >= 0, got {self.ci_bound}"
            )
        if not self.evidence_basis:
            raise ValueError("AlphaDecision.evidence_basis must not be empty")

    def assert_target_compatible(self, consumer_target: Literal["brier_score", "ev", "risk_cap"]) -> None:
        """Raise ContractViolationError if this alpha's optimization target is
        incompatible with the consumer's declared intent.

        A Brier-optimized alpha fed into an EV-seeking consumer is the canonical
        D1 violation: it silently drives edge → 0.
        """
        if self.optimization_target == "brier_score" and consumer_target == "ev":
            raise AlphaTargetMismatchError(
                f"AlphaDecision was optimized for '{self.optimization_target}' "
                f"but consumer declares target='{consumer_target}'. "
                "Brier-optimized alpha in EV-seeking sizing drives edge → 0. "
                "Re-derive alpha against EV objective or wrap with explicit override."
            )


class AlphaTargetMismatchError(Exception):
    """Raised when an AlphaDecision's optimization_target is incompatible with
    the consuming module's declared objective. This is the D1 runtime contract
    violation — prevents Brier-optimized alpha from silently poisoning Kelly sizing.
    """
