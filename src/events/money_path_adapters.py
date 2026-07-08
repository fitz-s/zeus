# Last reused or audited: 2026-06-05
# Authority basis: Operator GOAL 2026-06-04 — Kelly size=0 observability (zero-receipt root-cause);
#   P1 ZERO-SUBMIT FIX A (2026-06-05, iron-rule-1) — f_cap budget-ceiling vs variance-haircut
#   semantic mismatch in evaluate_kelly (corr/raw effective-bankroll).
#   P0 LIVE-FLOW FIX (2026-06-07) — portfolio heat is a soft marginal Kelly
#   pressure input, not a hard total-portfolio cut.
#   MINOR (2026-06-05): tightened the raw-cap comment — every factor IN THIS CALL (ci/lead/heat)
#   is ≤ 1.0; city/strategy multipliers ([0.0,2.0] fail-open) are intentionally NOT passed here.
"""No-submit money-path adapter contracts for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.contracts.execution_price import ExecutionPrice
from src.riskguard.risk_level import RiskLevel

if TYPE_CHECKING:
    from src.sizing.sizing_context import SizingContext


@dataclass(frozen=True)
class FdrProof:
    fdr_family_id: str
    attempted_hypotheses: int
    selected_hypotheses: tuple[str, ...]
    selected_post_fdr: tuple[str, ...]
    passed: bool


@dataclass(frozen=True)
class KellyProof:
    kelly_decision_id: str
    execution_price: ExecutionPrice
    size_usd: float
    passed: bool
    # Optional diagnostic fields (all default None — existing constructors unaffected).
    # Populated by evaluate_kelly to pinpoint why size collapsed to 0.
    effective_multiplier: float | None = None
    sizing_bankroll: float | None = None
    eff_corr_bankroll: float | None = None
    eff_raw_bankroll: float | None = None
    corr_committed_usd: float | None = None
    raw_committed_usd: float | None = None
    ci_width: float | None = None
    lead_days: float | None = None
    portfolio_heat: float | None = None
    single_cap_usd: float | None = None
    binding_constraint: str | None = None


@dataclass(frozen=True)
class RiskProof:
    risk_decision_id: str
    level: RiskLevel
    passed: bool


def evaluate_fdr_full_family(
    *,
    family_id: str,
    all_hypothesis_ids: tuple[str, ...],
    selected_hypothesis_ids: tuple[str, ...],
    hypothesis_p_values: dict[str, float],
    passed_prefilter: dict[str, bool] | None = None,
    alpha: float | None = None,
    duplicate_event: bool = False,
) -> FdrProof:
    """Apply Zeus' canonical family-wise BH FDR over the full event family."""

    from src.strategy.selection_family import DEFAULT_FDR_ALPHA, apply_familywise_fdr

    if duplicate_event:
        return FdrProof(family_id, len(all_hypothesis_ids), tuple(), tuple(), False)
    if not all_hypothesis_ids:
        raise ValueError("FDR requires full family hypotheses")
    missing = [hypothesis_id for hypothesis_id in all_hypothesis_ids if hypothesis_id not in hypothesis_p_values]
    if missing:
        raise ValueError(f"FDR requires p-values for every family hypothesis: {missing!r}")
    selected = tuple(h for h in selected_hypothesis_ids if h in set(all_hypothesis_ids))
    rows = [
        {
            "family_id": family_id,
            "hypothesis_id": hypothesis_id,
            "p_value": float(hypothesis_p_values[hypothesis_id]),
            "tested": True,
            "passed_prefilter": bool((passed_prefilter or {}).get(hypothesis_id, True)),
        }
        for hypothesis_id in all_hypothesis_ids
    ]
    selected_rows = apply_familywise_fdr(rows, q=DEFAULT_FDR_ALPHA if alpha is None else float(alpha))
    selected_post = tuple(
        str(row["hypothesis_id"])
        for row in selected_rows
        if bool(row.get("selected_post_fdr")) and bool(row.get("passed_prefilter"))
    )
    return FdrProof(
        fdr_family_id=family_id,
        attempted_hypotheses=len(all_hypothesis_ids),
        selected_hypotheses=selected,
        selected_post_fdr=selected_post,
        passed=any(hypothesis_id in selected_post for hypothesis_id in selected),
    )


def evaluate_kelly(
    *,
    kelly_decision_id: str,
    p_posterior: float,
    execution_price: ExecutionPrice,
    bankroll_usd: float,
    sizing_context: "SizingContext | None" = None,
    kelly_multiplier: float | None = None,
) -> KellyProof:
    """Run Zeus' typed Kelly sizing with the supplied executable price.

    S3 (variance-required Kelly, task #103/#111): the Kelly multiplier is
    no longer a flat scalar handed in by the caller. Callers pass a typed
    ``SizingContext`` carrying ``ci_width`` and ``lead_days``, and this
    adapter derives the multiplier via ``dynamic_kelly_mult`` so that size
    is NON-INCREASING in CI width (strictly smaller across a haircut
    threshold). ``dynamic_kelly_mult``'s ci_width haircut is STEPWISE
    (>0.10 → ×0.7, >0.15 → ×0.5), so two widths both under 0.10 size
    identically while widths straddling a threshold size strictly smaller.
    This makes the pre-S3 defect — variance silently dropped on the way to
    Kelly so two candidates identical except CI width sized identically —
    unconstructable at this boundary.

    The legacy ``kelly_multiplier`` flat-scalar parameter remains accepted
    for back-compat with callers not yet migrated (and is the explicit
    base for ``dynamic_kelly_mult`` when a context IS supplied). At least
    one of ``sizing_context`` / ``kelly_multiplier`` must be provided; the
    two are NOT mutually exclusive. The supported combinations are:

      * ``sizing_context`` only            → variance-required (preferred);
        base defaults to 0.25 and is haircut by ``dynamic_kelly_mult``.
      * ``sizing_context`` + ``kelly_multiplier`` → ``kelly_multiplier`` is
        the explicit base fed to ``dynamic_kelly_mult`` (still haircut by
        CI width / lead).
      * ``kelly_multiplier`` only          → legacy flat scalar, used as-is.
      * neither                            → ``ValueError`` (fail-closed).

    ``KellyProof.passed = size_usd > 0`` — when the variance haircut (or a
    zero edge) collapses the size to 0.0, ``passed`` correctly flips
    True -> False.
    """

    from src.config import sizing_defaults
    from src.strategy.kelly import dynamic_kelly_mult, kelly_size

    if sizing_context is None and kelly_multiplier is None:
        raise ValueError(
            "evaluate_kelly requires either a SizingContext (variance-required, "
            "preferred) or a flat kelly_multiplier (legacy)"
        )

    execution_price.assert_kelly_safe()

    # Task #107 (portfolio/multi Kelly), corrected 2026-06-07:
    # portfolio state is a MARGINAL Kelly pressure input, not a hard total-
    # portfolio cut. Fractional Kelly already scales every new bet. A hard
    # ``max_portfolio_heat_pct * B - raw_committed`` effective-bankroll gate or
    # per-order clipping turns half/quarter Kelly into a fixed cash rule. That
    # is mathematically different from multi-Kelly and was the live zero-flow /
    # bad-sizing blocker.
    #
    # The pressure fed to ``dynamic_kelly_mult`` is normalized by the configured
    # soft budgets:
    #   raw_pressure  = raw_committed / (max_portfolio_heat_pct * B)
    #   corr_pressure = corr_committed / (max_correlated_pct * B)
    # The maximum is used so raw heat and local correlation can both reduce the
    # next marginal size. ``dynamic_kelly_mult`` applies continuous attenuation;
    # it neither fabricates a zero-size proof nor clips to a single-position cap.
    portfolio_heat = 0.0
    if sizing_context is not None and sizing_context.has_portfolio_context:
        _heat_bankroll = float(sizing_context.bankroll_usd)
        if _heat_bankroll > 0.0:
            _sdc_for_heat = sizing_defaults()
            _raw_budget = (
                float(_sdc_for_heat["max_portfolio_heat_pct"]) * _heat_bankroll
            )
            _corr_budget = (
                float(_sdc_for_heat["max_correlated_pct"]) * _heat_bankroll
            )
            _raw_pressure = (
                float(sizing_context.raw_committed_usd) / _raw_budget
                if _raw_budget > 0.0
                else 0.0
            )
            _corr_pressure = (
                float(sizing_context.corr_committed_usd) / _corr_budget
                if _corr_budget > 0.0
                else 0.0
            )
            portfolio_heat = max(0.0, _raw_pressure, _corr_pressure)

    if sizing_context is not None:
        base = 0.25 if kelly_multiplier is None else float(kelly_multiplier)
        effective_multiplier = dynamic_kelly_mult(
            base=base,
            ci_width=float(sizing_context.ci_width),
            lead_days=float(sizing_context.lead_days),
            portfolio_heat=portfolio_heat,
        )
    else:
        effective_multiplier = float(kelly_multiplier)

    # Multi-Kelly sizing: size against the actual bankroll. Portfolio exposure
    # has already entered through the dynamic multiplier above. Do not subtract
    # committed capital from a global heat budget here; that reintroduces a hard
    # cap and makes positive-edge marginal opportunities impossible after a
    # portfolio crosses the arbitrary soft-budget line.
    sizing_bankroll = float(bankroll_usd)

    size_usd = kelly_size(
        float(p_posterior),
        execution_price,
        sizing_bankroll,
        kelly_mult=effective_multiplier,
    )

    # ── Single-position concentration ceiling — the "total-portfolio layer" ──
    # INV-K3 antibody, RESTORED. Task #107's 2026-06-07 live-flow fix correctly
    # removed the depleting effective-bankroll GATE (``f_cap·B − committed``
    # sized Kelly against a budget that shrank to zero as the book filled and
    # hard-zeroed positive-edge candidates). But it ALSO dropped the
    # single-position concentration CEILING, leaving INV-K3/K1/K8 in
    # tests/test_portfolio_kelly_relationships.py RED and the live path able to
    # size one bet at 22-27% of bankroll. This restores the ceiling.
    #
    # Why this is NOT a re-introduction of the #107 bug (the distinction is
    # load-bearing — do not "simplify" it away):
    #   * The gate #107 removed was a SUBTRACTION (B_eff = cap·B − committed)
    #     that DEPLETES toward zero, so kelly_size(B_eff)→0 for positive edges.
    #   * This is a pure UPPER BOUND: ``min(size, ceiling)`` on an
    #     already-positive ``size`` can never reach 0 (ceiling = pct·equity > 0
    #     whenever equity > 0). It only clips the strong-edge TAIL; weak/modest
    #     edges sit below the ceiling and keep their full Kelly proportionality.
    #     Set ``max_single_position_pct`` loose enough that typical fractional-
    #     Kelly bets fall under it → a genuine tail-limit, NOT a flat cash rule.
    #
    # Why a FRACTION (not the deleted tiny_live fixed-dollar cap): the ceiling
    # scales with wealth — pct·B is $50 at $1k, $500 at $10k — so it is a
    # structural concentration limit, not a one-off special-case dollar clamp.
    #
    # Base = the SIZING bankroll (free spendable cash, "可用现金一层"), NOT total
    # equity. This is required by two established antibodies:
    #   * INV-K8 (no-amplify): portfolio-aware size ≤ single-Kelly size always.
    #     An equity (cash+committed) base would make the ceiling GROW with the
    #     book (pct·(cash+committed) > pct·cash), letting the portfolio path
    #     exceed the single path — amplification.
    #   * INV-K4 (monotone): adding committed capital must never increase the
    #     next bet. A ceiling that rises with committed capital would raise a
    #     ceiling-bound bet. A cash base instead SHRINKS as cash converts to
    #     open positions (live), keeping the ceiling monotone-safe.
    # The total-PORTFOLIO dimension ("总portfolio一层") is already carried by the
    # separate portfolio-heat attenuation above (raw+corr committed → smaller
    # ``effective_multiplier``); it must not also re-enter as a larger ceiling.
    # Applies ONLY in the portfolio-aware path. The legacy/bare no-context path
    # (from_candidate_proof, or callers that pass only a flat kelly_multiplier)
    # stays pure fractional Kelly with single_cap_usd=None — the concentration
    # ceiling is a PORTFOLIO-level control. This is safe for live: the reactor's
    # real-submit path fails CLOSED unless portfolio context is present
    # (src/main.py:5106 → live_submit_effective=False), so every real order is
    # portfolio-aware and therefore capped, while bare-adapter tests/tools keep
    # single-asset semantics.
    _has_portfolio_ctx = (
        sizing_context is not None and sizing_context.has_portfolio_context
    )
    _single_pos_pct = float(sizing_defaults()["max_single_position_pct"])
    single_cap_usd: float | None = (
        _single_pos_pct * sizing_bankroll
        if (_has_portfolio_ctx and _single_pos_pct > 0.0)
        else None
    )
    _capped_by_single_position = False
    if single_cap_usd is not None and size_usd > single_cap_usd:
        size_usd = single_cap_usd
        _capped_by_single_position = True

    # ── Diagnostic: compute binding_constraint (purely observational) ────────
    # Determines WHICH limit drove size to 0 (or which path produced the result).
    # Does NOT alter size_usd or passed.
    _binding: str | None = None
    if sizing_context is not None:
        _ci_w = float(sizing_context.ci_width)
        _lead = float(sizing_context.lead_days)
    else:
        _ci_w = None
        _lead = None

    # Capture intermediate bankrolls for the non-portfolio path too.
    _eff_corr_diag: float | None = None
    _eff_raw_diag: float | None = None
    _corr_committed_diag: float | None = None
    _raw_committed_diag: float | None = None

    if sizing_context is not None and sizing_context.has_portfolio_context:
        _corr_committed_diag = float(sizing_context.corr_committed_usd)
        _raw_committed_diag = float(sizing_context.raw_committed_usd)
        # These legacy diagnostic fields now report the bankroll actually used
        # for marginal sizing. The heat/correlation pressure is carried in
        # ``portfolio_heat`` and ``effective_multiplier`` instead of a hard
        # reduced-bankroll gate.
        _eff_corr_diag = float(sizing_bankroll)
        _eff_raw_diag = float(sizing_bankroll)

    # Determine binding_constraint label.
    if size_usd == 0.0:
        # Check if zero-edge: would kelly_size on the UNCONSTRAINED full bankroll
        # also be 0? (i.e., edge itself is non-positive, not a budget limit)
        _unconstrained = kelly_size(
            float(p_posterior),
            execution_price,
            float(bankroll_usd),
            kelly_mult=float(effective_multiplier),
        )
        if _unconstrained <= 0.0:
            _binding = "zero_edge"
        else:
            # Positive-edge sizing should not collapse solely because portfolio
            # heat is high; heat is continuous multiplier pressure.
            _binding = "positive_edge_unexpected_zero"
    elif _capped_by_single_position:
        _binding = "single_position_ceiling"
    else:
        _binding = "sized_ok"

    return KellyProof(
        kelly_decision_id=kelly_decision_id,
        execution_price=execution_price,
        size_usd=size_usd,
        passed=size_usd > 0,
        effective_multiplier=float(effective_multiplier),
        sizing_bankroll=float(sizing_bankroll),
        eff_corr_bankroll=_eff_corr_diag,
        eff_raw_bankroll=_eff_raw_diag,
        corr_committed_usd=_corr_committed_diag,
        raw_committed_usd=_raw_committed_diag,
        ci_width=_ci_w,
        lead_days=_lead,
        portfolio_heat=float(portfolio_heat),
        single_cap_usd=single_cap_usd,
        binding_constraint=_binding,
    )


def evaluate_riskguard(*, risk_decision_id: str, level: RiskLevel) -> RiskProof:
    return RiskProof(
        risk_decision_id=risk_decision_id,
        level=level,
        passed=level is RiskLevel.GREEN,
    )
