# Created: 2026-08-27
# Last reused or audited: 2026-08-27
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 9 ("Market-anchored walk-forward calibrator") — live wiring. The
#   calibrator math lives in src/calibration/market_anchored_residual.py; this
#   module carries only the sealed per-candidate RESULT so the solver
#   (src/solve/solver.py) and the actuation certificate
#   (src/engine/event_reactor_adapter.py) act on ONE value.
"""Sealed per-candidate acting-probability correction.

The market-anchored calibrator is fitted and applied ONCE per candidate, at
solve time, where the market price p0 and the raw payoff probability q_raw are
both in scope. The result travels with the decision as this frozen record.

Why a carried record rather than re-deriving at certificate time: the
certificate seam re-projects the family witness to re-prove the candidate's
probability has not been superseded. If it re-fitted the calibrator instead, a
TTL boundary crossed between solve and certificate would silently change the
acting probability and fire a spurious supersession. Carrying ``raw_q`` keeps
that supersession check exact — the certificate still compares the re-projected
RAW witness value against ``raw_q`` — while ``corrected_q`` is the single value
every downstream economics field, cut probability, and receipt agrees on.

This module is a pure contract: no math, no database, no calibrator import. It
sits in ``src/contracts`` precisely so ``src/solve`` and ``src/engine`` can both
name the shape without either depending on ``src/calibration``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PayoffQCorrection:
    """One candidate's market-anchored correction, sealed at solve time.

    ``raw_q`` and ``corrected_q`` are both in the HELD-TOKEN space — the
    probability that the candidate's own token pays — which is the space the
    solver sizes in and the certificate asserts on. ``p0`` is the decision-time
    all-in market unit cost of that same token, i.e. the market's implied
    probability it pays, and is the anchor the correction shrinks toward.

    The remaining fields are provenance for settlement attribution to later
    grade corrected-versus-raw decisions; nothing downstream computes from them.
    """

    family_key: str
    bin_id: str
    side: str
    token_id: str
    raw_q: float
    corrected_q: float
    p0: float
    lead_bucket: str
    alpha_lead: float
    beta: float
    lambda_: float
    training_cutoff: str
    n_train: int
    param_hash: str

    def __post_init__(self) -> None:
        if not all(
            str(value).strip()
            for value in (
                self.family_key,
                self.bin_id,
                self.side,
                self.token_id,
                self.lead_bucket,
                self.training_cutoff,
                self.param_hash,
            )
        ):
            raise ValueError("payoff q correction requires complete identity")
        if self.side not in {"YES", "NO"}:
            raise ValueError("payoff q correction side must be YES or NO")
        if not all(
            math.isfinite(value) and 0.0 <= value <= 1.0
            for value in (self.raw_q, self.corrected_q, self.p0)
        ):
            raise ValueError("payoff q correction probabilities must lie in [0, 1]")
        if not all(
            math.isfinite(value)
            for value in (self.alpha_lead, self.beta, self.lambda_)
        ):
            raise ValueError("payoff q correction parameters must be finite")
        if self.n_train < 0:
            raise ValueError("payoff q correction training count is invalid")

    def matches(
        self, *, family_key: str, bin_id: str, side: str, token_id: str
    ) -> bool:
        """True when this record was sealed for exactly this candidate leg."""

        return (
            self.family_key == family_key
            and self.bin_id == bin_id
            and self.side == side
            and self.token_id == token_id
        )

    def as_cert_fields(self) -> dict[str, object]:
        """Provenance block stamped onto the qkernel economics certificate."""

        return {
            "applied": True,
            "q_raw": float(self.raw_q),
            "q_corrected": float(self.corrected_q),
            "p0": float(self.p0),
            "lead_bucket": self.lead_bucket,
            "alpha_lead": float(self.alpha_lead),
            "beta": float(self.beta),
            "lambda": float(self.lambda_),
            "training_cutoff": self.training_cutoff,
            "n_train": int(self.n_train),
            "param_hash": self.param_hash,
        }
