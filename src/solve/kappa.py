# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: architecture doc §1 ARM/κ row (route live-submit through OperatorArm, no
#   second gate) + deletion-list row "Kelly haircut stack → κ"; W3.SEAM brief (today's decide()
#   is UNCONSTRAINED full-Kelly, the fractional haircut is a DOWNSTREAM submit-boundary layer);
#   CONSULT REV-2 ruling 6 (κ is a typed Decimal value object with canonical serialization,
#   not a bare float at a Kelly boundary).
"""κ — the solver's single fractional-shading policy.

MIGRATION LAW (the double-shading decision, packet §6):

Today the engine's sizing is full-Kelly (argmax robust ΔU, no fraction) and the ONLY
fractional shading is the downstream ``settings.sizing.kelly_multiplier`` layer applied
AFTER decide() at the submit boundary (event_reactor_adapter.py:5657-5819). That layer is
slated for W5 deletion, with κ as its replacement INSIDE the objective.

During the W3 promotion window BOTH layers exist. Ruling implemented here:

    κ = 1.0 while the downstream haircut layer is alive.

Rationale: exactly ONE owner of fractional shading at any time. With κ=1.0 the new solver's
ON-mode sizing semantics equal today's (full-Kelly objective, downstream multiplier), so the
promotion evidence gate measures the SOLVER change (joint menu vs top-1), not a confounded
double-shade. The packet that deletes the haircut stack (W5) flips κ to the configured
fraction IN THE SAME COMMIT — ownership transfers atomically, never overlaps, never gaps. A
κ≠1.0 while kelly_multiplier≠1.0 is a construction-time error, enforced below.

κ IS A TYPED VALUE (consult REV-2): a bare float at a Kelly seam invites drift; ``Kappa``
wraps a ``Decimal`` with canonical serialization so receipts and the promotion evidence
record the exact shading applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Kappa:
    """A fractional-shading factor in ``(0, 1]`` as a typed Decimal value object."""

    value: Decimal

    def __post_init__(self) -> None:
        if not (Decimal("0") < self.value <= Decimal("1")):
            raise ValueError(f"kappa must be in (0, 1], got {self.value}")

    @classmethod
    def of(cls, x: object) -> "Kappa":
        return cls(value=Decimal(str(x)))

    def as_float(self) -> float:
        return float(self.value)

    def canonical(self) -> str:
        """Canonical serialization for receipts/evidence (trailing-zero-stable)."""
        return format(self.value.normalize(), "f")


@dataclass(frozen=True)
class KappaPolicy:
    """Fractional shading applied to the continuous solution before discrete repair.

    ``downstream_haircut_alive`` must reflect whether the kelly_multiplier submit-boundary
    layer still executes (True throughout W3/W4; False from the W5 deletion packet on).
    """

    kappa: Kappa
    downstream_haircut_alive: bool

    def __post_init__(self) -> None:
        if self.downstream_haircut_alive and self.kappa.value != Decimal("1"):
            raise ValueError(
                "double-shading forbidden: kappa must be 1.0 while the downstream "
                "kelly_multiplier haircut layer is alive (single-owner law; see module header)"
            )


def promotion_window_policy() -> KappaPolicy:
    """The W3 promotion-window policy: κ owned downstream, solver passes through."""
    return KappaPolicy(kappa=Kappa.of("1.0"), downstream_haircut_alive=True)
