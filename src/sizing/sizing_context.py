# Created: 2026-06-01
# Last reused/audited: 2026-07-23
# Authority basis: ELEVATION S3 (task #111) — variance-required Kelly;
#   task #107 (portfolio/multi Kelly) — correlation-aware pressure context.
"""Typed sizing context that carries the variance inputs Kelly requires.

ELEVATION S3 (task #103/#111): the live EDLI Kelly path previously sized
on a *flat* ``kelly_multiplier`` scalar, so a tight-CI edge and a wide-CI
edge with identical point estimates sized IDENTICALLY — variance was
UNCARRIED. ``dynamic_kelly_mult`` already knows how to haircut on CI
width and forecast lead, but the no-submit money-path adapter never fed
it those inputs.

``SizingContext`` is the typed carrier for those modifier inputs.  On the
current-q global-solver path, the conservative q-band already carries the
same posterior uncertainty into terminal wealth.  That path therefore moves
the observed width to ``counted_ci_width`` and presents ``ci_width=0`` to the
legacy dynamic multiplier so the uncertainty is counted exactly once.

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

import math
from dataclasses import dataclass, replace


def effective_bankroll(
    bankroll_usd: float,
    corr_committed_usd: float,
    *,
    f_cap: float = 1.0,
) -> float:
    """Legacy budget-reduced bankroll helper.

    Task #107 (portfolio/multi Kelly), the corr-weighted budget enforcer.
    Retained for historical tests/back-compat only. The live money path no
    longer uses this helper as a hard gate: as of 2026-06-07, committed
    exposure feeds a soft marginal Kelly pressure multiplier instead of
    subtracting from a global portfolio budget and forcing size to zero.

    DESIGN RECONCILIATION (design /tmp/kelly-107-design.md §3a vs §4 invariants):
    the §3a prose formula ``B_eff = B - corr_committed`` with
    ``s = f*·f_cap·B_eff`` guarantees only ``Σ stakes ≤ B`` and leaves a first
    (uncommitted) bet at full single-Kelly. This helper is retained only for
    historical tests/back-compat; the live path now uses continuous marginal
    pressure in ``evaluate_kelly`` instead of this hard budget subtraction:

        B_eff = max(0, f_cap·B - corr_committed) / f_cap

    Then ``kelly_size`` returns ``f*·f_cap·B_eff = f*·max(0, f_cap·B -
    corr_committed)``, so correlation-weighted simultaneous stakes sum to ≤
    ``f_cap·B``. With ``f_cap`` left at its 1.0 default the function reduces to
    the literal §3a ``B - corr_committed`` for callers that want the raw form.

    NOTE: this function alone does NOT model the current live multi-Kelly
    sizing law. Use ``SizingContext`` + ``evaluate_kelly`` for live behavior.

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


def effective_bankroll_raw(
    bankroll_usd: float,
    raw_committed_usd: float,
    max_heat_pct: float,
    *,
    f_cap: float = 1.0,
) -> float:
    """Legacy absolute raw-dollar budget helper.

    Task #107 verifier fix: ``effective_bankroll`` (corr-weighted) alone does
    not stop distant-city (corr=0.10) bets from summing past the bankroll. The
    corr weighting barely reduces committed capital for distant cities, so the
    corr-reduced B_eff stays near full B and each bet sizes near full K3 cap.
    15 independent $17 bets = $255 — exceeding the bankroll.

    Older live code used this function to enforce an ABSOLUTE floor: total raw
    cash deployed (no correlation discount) must not exceed ``max_heat_pct·B``:

        B_eff_raw = max(0, max_heat_pct·B - raw_committed) / f_cap

    As of 2026-06-07, the live path does NOT take this min as a hard gate.
    Raw exposure is normalized into soft portfolio pressure and only shrinks
    the marginal Kelly multiplier.

    ``raw_committed_usd``: actual dollars deployed in open + pending + same-cycle
    reserved positions (NOT correlation-weighted). Computed by the reactor as
    ``total_exposure_usd(state) + Σ reservation_usd``.

    NEVER amplifies: returns ≤ B (not ≤ B·max_heat_pct, since B_eff is the
    kelly_size bankroll argument and kelly.py multiplies by f_cap itself).
    """
    b = float(bankroll_usd)
    raw = float(raw_committed_usd)
    heat_cap = float(max_heat_pct)
    cap = float(f_cap)
    abs_budget = heat_cap * b
    if cap <= 0.0:
        return max(0.0, abs_budget - raw)
    return max(0.0, abs_budget - raw) / cap


@dataclass(frozen=True)
class SizingContext:
    """Variance + portfolio inputs required to size a Kelly bet.

    Attributes:
        ci_width: Posterior credible-interval width fed to the
            ``dynamic_kelly_mult`` ci_width haircut. Non-negative.
        lead_days: Forecast lead in days fed to the lead-time haircut.
            Non-negative.
        bankroll_usd: On-chain bankroll truth ``B`` — the SAME value passed
            to ``kelly_size``. When present (> 0), the Kelly adapter sizes the
            marginal bet against ``B`` and uses existing portfolio exposure as
            multiplier pressure, not as an arbitrary hard heat-budget bankroll
            subtraction. Default 0.0 means "no portfolio context" — the adapter
            then sizes against the raw bankroll exactly as pre-#107 (#103
            callers and tests are unaffected: they construct via the 3-arg
            ``from_candidate_proof`` which leaves these at 0.0).
        corr_committed_usd: ``Σ_i c_i · corr(city_new, city_i)`` over OTHER
            open + pending + same-cycle in-flight positions (computed by
            ``portfolio.correlated_committed_usd``). Non-negative. The
            correlation-weighted capital already at risk; normalized into
            soft marginal Kelly pressure. Default 0.0 (no portfolio context).
        raw_committed_usd: Total RAW dollars deployed across all open +
            pending + same-cycle in-flight positions (NO correlation
            weighting). Computed as ``total_exposure_usd(state) + Σ
            reservation_usd``. Used by ``evaluate_kelly`` as soft raw heat
            pressure; it must not become a hard total-portfolio cap. Default
            0.0 (no portfolio context).
        counted_ci_width: CI width already consumed by the current-q global
            solver's conservative q-band. It is offline provenance only and
            must not feed another Kelly multiplier haircut.
    """

    ci_width: float
    lead_days: float
    bankroll_usd: float = 0.0
    corr_committed_usd: float = 0.0
    raw_committed_usd: float = 0.0
    counted_ci_width: float = 0.0

    def __post_init__(self) -> None:
        # Fail-closed on nonsense inputs so a corrupted upstream value
        # routes to KELLY_PROOF_MISSING rather than silently sizing on a
        # negative / NaN / infinite haircut.
        # Use math.isfinite (not x == x): x == x only rejects NaN; it
        # PASSES inf and -inf, which would produce nonsensical sizes.
        # math.isfinite rejects NaN, +inf, and -inf, matching the pattern
        # in src/contracts/execution_price.py.
        ci = float(self.ci_width)
        lead = float(self.lead_days)
        bankroll = float(self.bankroll_usd)
        corr_committed = float(self.corr_committed_usd)
        raw_committed = float(self.raw_committed_usd)
        counted_ci = float(self.counted_ci_width)
        if not (
            math.isfinite(ci)
            and math.isfinite(lead)
            and math.isfinite(bankroll)
            and math.isfinite(corr_committed)
            and math.isfinite(raw_committed)
            and math.isfinite(counted_ci)
        ):
            raise ValueError(
                f"SizingContext requires finite inputs (no NaN, no inf); got "
                f"ci_width={self.ci_width!r}, lead_days={self.lead_days!r}, "
                f"bankroll_usd={self.bankroll_usd!r}, "
                f"corr_committed_usd={self.corr_committed_usd!r}, "
                f"raw_committed_usd={self.raw_committed_usd!r}, "
                f"counted_ci_width={self.counted_ci_width!r}"
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
        if raw_committed < 0.0:
            raise ValueError(
                f"SizingContext.raw_committed_usd must be >= 0; "
                f"got {raw_committed}"
            )
        if counted_ci < 0.0:
            raise ValueError(
                f"SizingContext.counted_ci_width must be >= 0; got {counted_ci}"
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

    def for_current_q_global_solver(self) -> "SizingContext":
        """Move CI width from the Kelly modifier into q-band provenance.

        The global solver has already optimized terminal wealth against the
        conservative current-q band.  Reapplying the same width through
        ``dynamic_kelly_mult`` would be a second uncertainty haircut.  Lead,
        portfolio heat, bankroll, and committed-capital fields are preserved.
        """

        return replace(
            self,
            ci_width=0.0,
            counted_ci_width=max(
                float(self.counted_ci_width),
                float(self.ci_width),
            ),
        )

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
        raw_committed_usd: float = 0.0,
    ) -> "SizingContext":
        """Build a portfolio-aware context (task #107).

        Identical CI/lead derivation to ``from_candidate_proof`` PLUS the
        portfolio pressure inputs. Sizing uses the actual bankroll; the
        corr/raw committed values are normalized by ``evaluate_kelly`` into a
        soft marginal Kelly heat multiplier. They must not subtract from a
        global budget and hard-zero positive-edge candidates.

        Validates ``bankroll_usd > 0`` (a non-positive bankroll has no
        budget to allocate — fail-closed), ``corr_committed_usd >= 0``, and
        ``raw_committed_usd >= 0``. All route to KELLY_PROOF_MISSING via the
        reactor's try/except envelope.

        ``raw_committed_usd`` defaults to 0.0 for callers that only supply
        the corr-weighted input. The reactor always supplies both.
        """
        bankroll = float(bankroll_usd)
        corr_committed = float(corr_committed_usd)
        raw_committed = float(raw_committed_usd)
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
        if raw_committed < 0.0:
            raise ValueError(
                f"from_candidate_proof_with_portfolio requires "
                f"raw_committed_usd >= 0; got {raw_committed_usd!r}"
            )
        ci_width = 2.0 * (float(q_posterior) - float(q_lcb_5pct))
        if ci_width < 0.0:
            ci_width = 0.0
        return cls(
            ci_width=ci_width,
            lead_days=float(lead_days),
            bankroll_usd=bankroll,
            corr_committed_usd=corr_committed,
            raw_committed_usd=raw_committed,
        )
