# Created: 2026-06-01
# Last reused/audited: 2026-06-03
# Authority basis: ELEVATION S3 (task #111) — variance-required Kelly;
#   task #107 (portfolio/multi Kelly) — correlation-aware effective-bankroll.
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


def effective_bankroll(
    bankroll_usd: float,
    corr_committed_usd: float,
    *,
    f_cap: float = 1.0,
) -> float:
    """Budget-reduced bankroll to hand to ``kelly_size`` (placement A).

    Task #107 (portfolio/multi Kelly), the budget enforcer. This is the
    *only* place the bankroll is reduced — ``kelly.py`` stays untouched.

    DESIGN RECONCILIATION (design /tmp/kelly-107-design.md §3a vs §4 invariants):
    the §3a prose formula ``B_eff = B - corr_committed`` with
    ``s = f*·f_cap·B_eff`` guarantees only ``Σ stakes ≤ B`` and leaves a first
    (uncommitted) bet at full single-Kelly (~22-27% of B) — which VIOLATES the
    spec's own stated invariants INV-K1 (``Σ ≤ B·f_cap``) and INV-K3 (single
    ``≤ max_single_position_pct·B``, the named headline RED→GREEN). The
    invariants are the operator's law (relationship tests); the prose formula
    is the under-specified approximation. To satisfy BOTH while keeping
    ``s = f*·f_cap·B_eff`` (so kelly.py is untouched), the budget that
    committed capital draws down is the fractional-Kelly capital-at-risk
    ceiling ``f_cap·B`` (== ``max_correlated_pct·B`` in config), expressed back
    in raw-bankroll space so kelly.py's own ``·f_cap`` reproduces it:

        B_eff = max(0, f_cap·B - corr_committed) / f_cap

    Then ``kelly_size`` returns ``f*·f_cap·B_eff = f*·max(0, f_cap·B -
    corr_committed)``, so the simultaneous stakes sum to ≤ ``f_cap·B`` (INV-K1),
    and the per-bet ``max_single_position_pct`` clamp (applied by the caller,
    INV-K3) is the second belt. With ``f_cap`` left at its 1.0 default the
    function reduces to the literal §3a ``B - corr_committed`` for callers that
    want the raw form.

    NEVER amplifies: ``B_eff ≤ B`` whenever committed ≥ 0 (INV-K8). Clamps to
    0.0 when committed ≥ ``f_cap·B`` (fail-closed → ``kelly_size`` returns 0.0
    on a non-positive bankroll, INV-K6).
    """
    b = float(bankroll_usd)
    committed = float(corr_committed_usd)
    cap = float(f_cap)
    if cap <= 0.0:
        return max(0.0, b - committed)
    budget = cap * b
    return max(0.0, budget - committed) / cap


@dataclass(frozen=True)
class SizingContext:
    """Variance + portfolio inputs required to size a Kelly bet.

    Attributes:
        ci_width: Posterior credible-interval width fed to the
            ``dynamic_kelly_mult`` ci_width haircut. Non-negative.
        lead_days: Forecast lead in days fed to the lead-time haircut.
            Non-negative.
        bankroll_usd: On-chain bankroll truth ``B`` — the SAME value passed
            to ``kelly_size``. Task #107: when present (> 0) together with
            ``corr_committed_usd``, the Kelly adapter sizes against
            ``effective_bankroll(B, corr_committed)`` instead of the full
            ``B``. Default 0.0 means "no portfolio context" — the adapter
            then sizes against the raw bankroll exactly as pre-#107 (#103
            callers and tests are unaffected: they construct via the 3-arg
            ``from_candidate_proof`` which leaves these at 0.0).
        corr_committed_usd: ``Σ_i c_i · corr(city_new, city_i)`` over OTHER
            open + pending + same-cycle in-flight positions (computed by
            ``portfolio.correlated_committed_usd``). Non-negative. The
            correlation-weighted capital already at risk; subtracted from
            the bankroll. Default 0.0 (no portfolio context).
    """

    ci_width: float
    lead_days: float
    bankroll_usd: float = 0.0
    corr_committed_usd: float = 0.0

    def __post_init__(self) -> None:
        # Fail-closed on nonsense inputs so a corrupted upstream value
        # routes to KELLY_PROOF_MISSING rather than silently sizing on a
        # negative / NaN haircut.
        ci = float(self.ci_width)
        lead = float(self.lead_days)
        bankroll = float(self.bankroll_usd)
        corr_committed = float(self.corr_committed_usd)
        finite = (
            ci == ci
            and lead == lead
            and bankroll == bankroll
            and corr_committed == corr_committed
        )  # NaN check (NaN != NaN)
        if not finite:
            raise ValueError(
                f"SizingContext requires finite inputs; got "
                f"ci_width={self.ci_width!r}, lead_days={self.lead_days!r}, "
                f"bankroll_usd={self.bankroll_usd!r}, "
                f"corr_committed_usd={self.corr_committed_usd!r}"
            )
        if ci < 0.0:
            raise ValueError(f"SizingContext.ci_width must be >= 0; got {ci}")
        if lead < 0.0:
            raise ValueError(f"SizingContext.lead_days must be >= 0; got {lead}")
        if bankroll < 0.0:
            raise ValueError(
                f"SizingContext.bankroll_usd must be >= 0; got {bankroll}"
            )
        if corr_committed < 0.0:
            raise ValueError(
                f"SizingContext.corr_committed_usd must be >= 0; "
                f"got {corr_committed}"
            )

    @property
    def has_portfolio_context(self) -> bool:
        """True when this context carries a usable portfolio budget.

        The portfolio-aware effective-bankroll reduction engages only when a
        positive bankroll is carried. A 0.0 bankroll means "no portfolio
        context" (pre-#107 / #103 callers) → the adapter sizes against the
        raw bankroll, preserving exact single-Kelly behaviour.
        """
        return self.bankroll_usd > 0.0

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

        This is the original #103 3-arg constructor; it carries NO portfolio
        context (``bankroll_usd`` / ``corr_committed_usd`` default to 0.0),
        so the Kelly adapter sizes against the raw bankroll exactly as
        before #107. Unchanged for back-compat.
        """
        ci_width = 2.0 * (float(q_posterior) - float(q_lcb_5pct))
        if ci_width < 0.0:
            ci_width = 0.0
        return cls(ci_width=ci_width, lead_days=float(lead_days))

    @classmethod
    def from_candidate_proof_with_portfolio(
        cls,
        *,
        q_posterior: float,
        q_lcb_5pct: float,
        lead_days: float,
        bankroll_usd: float,
        corr_committed_usd: float,
    ) -> "SizingContext":
        """Build a portfolio-aware context (task #107).

        Identical CI/lead derivation to ``from_candidate_proof`` PLUS the
        portfolio budget inputs. Sizing then runs against
        ``effective_bankroll(bankroll_usd, corr_committed_usd)``.

        Validates ``bankroll_usd > 0`` (a non-positive bankroll has no
        budget to allocate — fail-closed) and ``corr_committed_usd >= 0``
        (committed capital is never negative). Both route to
        KELLY_PROOF_MISSING via the reactor's try/except envelope.
        """
        bankroll = float(bankroll_usd)
        corr_committed = float(corr_committed_usd)
        if not (bankroll > 0.0):
            raise ValueError(
                f"from_candidate_proof_with_portfolio requires bankroll_usd > 0; "
                f"got {bankroll_usd!r}"
            )
        if corr_committed < 0.0:
            raise ValueError(
                f"from_candidate_proof_with_portfolio requires "
                f"corr_committed_usd >= 0; got {corr_committed_usd!r}"
            )
        ci_width = 2.0 * (float(q_posterior) - float(q_lcb_5pct))
        if ci_width < 0.0:
            ci_width = 0.0
        return cls(
            ci_width=ci_width,
            lead_days=float(lead_days),
            bankroll_usd=bankroll,
            corr_committed_usd=corr_committed,
        )
