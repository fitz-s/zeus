# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: architecture doc §1 ARM/κ row (route live-submit through OperatorArm, no
#   second gate) + deletion-list row "Kelly haircut stack → κ"; W3.SEAM brief (today's decide()
#   is UNCONSTRAINED full-Kelly, the fractional haircut is a DOWNSTREAM submit-boundary layer).
"""κ — the solver's single fractional-shading policy.

MIGRATION LAW (the double-shading decision, packet §6):

Today the engine's sizing is full-Kelly (argmax robust ΔU, no fraction) and the ONLY
fractional shading is the downstream ``settings.sizing.kelly_multiplier`` layer applied
AFTER decide() at the submit boundary (event_reactor_adapter.py:5657-5819). That layer
is slated for W5 deletion, with κ as its replacement INSIDE the objective.

During the W3 promotion window BOTH layers exist. Ruling implemented here:

    κ = 1.0 while the downstream haircut layer is alive.

Rationale: exactly ONE owner of fractional shading at any time. With κ=1.0 the new
solver's ON-mode sizing semantics equal today's (full-Kelly objective, downstream
multiplier), so the promotion evidence gate measures the SOLVER change (joint menu vs
top-1), not a confounded double-shade. The packet that deletes the haircut stack (W5)
flips κ to the configured fraction IN THE SAME COMMIT — ownership transfers atomically,
never overlaps, never gaps. A κ≠1.0 while kelly_multiplier≠1.0 is a construction-time
error, enforced below.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KappaPolicy:
    """Fractional shading applied to the continuous solution before discrete repair.

    ``downstream_haircut_alive`` must reflect whether the kelly_multiplier submit-boundary
    layer still executes (True throughout W3/W4; False from the W5 deletion packet on).
    """

    kappa: float
    downstream_haircut_alive: bool

    def __post_init__(self) -> None:
        if not (0.0 < self.kappa <= 1.0):
            raise ValueError(f"kappa must be in (0, 1], got {self.kappa}")
        if self.downstream_haircut_alive and self.kappa != 1.0:
            raise ValueError(
                "double-shading forbidden: kappa must be 1.0 while the downstream "
                "kelly_multiplier haircut layer is alive (single-owner law; see module header)"
            )


def promotion_window_policy() -> KappaPolicy:
    """The W3 promotion-window policy: κ owned downstream, solver passes through."""
    return KappaPolicy(kappa=1.0, downstream_haircut_alive=True)
