# Created: 2026-06-01
# Last reused/audited: 2026-06-01
# Authority basis: ELEVATION S3 (task #111) — variance-required Kelly.
"""Typed sizing context that carries the variance inputs Kelly requires.

ELEVATION S3 (task #103/#111): the live EDLI Kelly path previously sized
on a *flat* ``kelly_multiplier`` scalar, so a tight-CI edge and a wide-CI
edge with identical point estimates sized IDENTICALLY — variance was
UNCARRIED. ``dynamic_kelly_mult`` already knows how to haircut on CI
width and forecast lead, but the no-submit money-path adapter never fed
it those inputs.

``SizingContext`` is the typed carrier for exactly those two inputs:

- ``ci_width``  — the posterior-credible-interval width that feeds the
  ``dynamic_kelly_mult`` ci_width haircut. By construction (see
  ``from_candidate_proof``) this is ``2 * (q_posterior - q_lcb_5pct)``,
  i.e. twice the lower half-width of the posterior — a symmetric-width
  proxy derived from the existing 5th-percentile lower-confidence bound
  on ``_CandidateProof``. The resulting size is NON-INCREASING in CI
  width (strictly smaller across a haircut threshold): the
  ``dynamic_kelly_mult`` ci_width haircut is STEPWISE (>0.10 → ×0.7,
  >0.15 → ×0.5), so two widths both under 0.10 size identically while
  widths straddling a threshold size strictly smaller.
- ``lead_days`` — forecast lead in days, feeding the lead-time haircut
  (longer lead → less reliable forecast → smaller size).

Design intent (Fitz constraint #1 — make the category impossible, not
the instance): downstream sizing consumes a *typed* context rather than
a bare scalar, so "variance was silently dropped on the way to Kelly"
becomes unconstructable at this boundary — you cannot call the Kelly
adapter without handing it the variance inputs.

S3 does NOT gate on whether ``q`` is calibrated. It sizes correctly for
WHATEVER ``q_posterior`` / ``q_lcb_5pct`` it is handed; calibration of
``q`` is an upstream concern (S1/S2). This context merely transports the
variance already present on the proof into the sizing multiplier.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizingContext:
    """Variance inputs required to size a Kelly bet.

    Attributes:
        ci_width: Posterior credible-interval width fed to the
            ``dynamic_kelly_mult`` ci_width haircut. Non-negative.
        lead_days: Forecast lead in days fed to the lead-time haircut.
            Non-negative.
    """

    ci_width: float
    lead_days: float

    def __post_init__(self) -> None:
        # Fail-closed on nonsense inputs so a corrupted upstream value
        # routes to KELLY_PROOF_MISSING rather than silently sizing on a
        # negative / NaN haircut.
        ci = float(self.ci_width)
        lead = float(self.lead_days)
        if not (ci == ci) or not (lead == lead):  # NaN check
            raise ValueError(
                f"SizingContext requires finite inputs; got "
                f"ci_width={self.ci_width!r}, lead_days={self.lead_days!r}"
            )
        if ci < 0.0:
            raise ValueError(f"SizingContext.ci_width must be >= 0; got {ci}")
        if lead < 0.0:
            raise ValueError(f"SizingContext.lead_days must be >= 0; got {lead}")

    @classmethod
    def from_candidate_proof(
        cls,
        *,
        q_posterior: float,
        q_lcb_5pct: float,
        lead_days: float,
    ) -> "SizingContext":
        """Build a context from the variance already carried on a proof.

        ``ci_width = 2 * (q_posterior - q_lcb_5pct)`` — twice the lower
        half-width of the posterior, derived from the existing
        5th-percentile lower-confidence bound. Clamped at 0.0 so a proof
        whose lcb sits (numerically) above its posterior does not produce
        a negative width.
        """
        ci_width = 2.0 * (float(q_posterior) - float(q_lcb_5pct))
        if ci_width < 0.0:
            ci_width = 0.0
        return cls(ci_width=ci_width, lead_days=float(lead_days))
