# Last reused or audited: 2026-06-05
# Authority basis: Operator GOAL 2026-06-04 — Kelly size=0 observability (zero-receipt root-cause);
#   P1 ZERO-SUBMIT FIX A (2026-06-05, iron-rule-1) — f_cap budget-ceiling vs variance-haircut
#   semantic mismatch in evaluate_kelly (corr/raw effective-bankroll). INV-K1/K1b preserved.
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

    from src.strategy.fdr_filter import DEFAULT_FDR_ALPHA
    from src.strategy.selection_family import apply_familywise_fdr

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
    from src.sizing.sizing_context import effective_bankroll, effective_bankroll_raw
    from src.strategy.kelly import dynamic_kelly_mult, kelly_size

    if sizing_context is None and kelly_multiplier is None:
        raise ValueError(
            "evaluate_kelly requires either a SizingContext (variance-required, "
            "preferred) or a flat kelly_multiplier (legacy)"
        )

    execution_price.assert_kelly_safe()

    # Task #107 (portfolio/multi Kelly): source portfolio_heat for the existing
    # kelly.py >0.40 damper (placement B, threshold UNCHANGED). This is a
    # secondary observable damper, NOT the primary budget (design §3c verdict).
    portfolio_heat = 0.0
    if sizing_context is not None and sizing_context.has_portfolio_context:
        _heat_bankroll = float(sizing_context.bankroll_usd)
        if _heat_bankroll > 0.0:
            portfolio_heat = float(sizing_context.corr_committed_usd) / _heat_bankroll

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

    # Task #107 placement A (the budget ENFORCER): size against the bankroll NET
    # of committed capital. Two limits applied — the binding one (min) is used:
    #
    # (corr limit) effective_bankroll: corr-weighted reduction, ensures corr-
    #   weighted simultaneous stakes ≤ f_cap·B (INV-K1 corr path).
    #
    # (raw limit) effective_bankroll_raw: ABSOLUTE raw-dollar floor — total cash
    #   deployed across ALL positions regardless of correlation. Fixes the
    #   verifier defect: 15 distant cities at corr=0.10 floor could deploy $254
    #   against a $170 bankroll because the corr weighting barely reduces the
    #   effective bankroll for independent bets. Raw constraint: Σ raw deployed
    #   ≤ max_portfolio_heat_pct·B (0.5·B = $85 at $170). INV-K1b.
    #
    # A context with no portfolio fields (the #103 3-arg ``from_candidate_proof``,
    # ``has_portfolio_context`` False) sizes against the raw bankroll exactly as
    # before #107 — INV-K8 holds with EQUALITY for the unwired case.
    sizing_bankroll = float(bankroll_usd)
    if sizing_context is not None and sizing_context.has_portfolio_context:
        _sdc = sizing_defaults()
        _b = float(sizing_context.bankroll_usd)
        # P1 ZERO-SUBMIT FIX A (2026-06-05): ``f_cap`` is the BUDGET CEILING, not
        # the variance haircut. Two DISTINCT concepts were previously wired to one
        # variable: ``effective_bankroll``/``effective_bankroll_raw`` were passed
        # ``f_cap=effective_multiplier`` (the Kelly VARIANCE HAIRCUT ~0.04–0.18),
        # but their contract (sizing_context.py:47-93/96-133) is that ``f_cap`` is
        # the CORRELATED-RISK / heat CEILING that committed capital draws down. The
        # haircut is far below the ceiling live, so ``f_cap·B`` collapsed the corr
        # budget to ~$8.9 (mult·B) instead of $42.5 (max_correlated_pct·B); the
        # first same-cycle candidate exhausted it and every later positive-edge
        # candidate got KELLY_REJECTED:corr_budget:size=0.0000 → zero submits.
        #
        # The ceiling and the per-bet haircut are INDEPENDENT (proven by the
        # INV-K1/K1b relationship tests, NOT by the §3a "cancellation" prose):
        #   stake = f*·effective_multiplier·effective_bankroll(B, committed, f_cap)
        #         = f*·m·(f_cap·B − committed)/f_cap = (f*·m/f_cap)·(f_cap·B − committed)
        # Since m ≤ base ≤ kelly_multiplier and f* ≤ 1, choosing
        #   f_cap_corr = max_correlated_pct  AND  f_cap_raw = the kelly base cap
        # gives f*·m/f_cap ≤ 1, so each corr-weighted stake ≤ (max_correlated_pct·B
        # − committed) → Σ ≤ max_correlated_pct·B (INV-K1) and each raw stake ≤
        # (max_portfolio_heat_pct·B − raw) → Σ ≤ max_portfolio_heat_pct·B (INV-K1b).
        # The /f_cap and ·m do NOT cancel — and must not: the haircut only makes
        # each bet smaller (more conservative), never breaching the ceiling.
        #
        # The raw-path ceiling is the kelly base cap (== the MAXIMUM possible
        # ``effective_multiplier`` FOR THIS CALL, since every factor IN THIS CALL
        # — ci, lead, heat — is ≤ 1.0). city/strategy multipliers are intentionally
        # NOT passed to the dynamic_kelly_mult call above (no city=/strategy_key=),
        # so the [0.0, 2.0] fail-open city_kelly_multiplier (kelly.py:341-369)
        # cannot apply here. Do NOT add city=/strategy_key= to that call without
        # re-deriving the raw cap — a city mult can reach 2.0 and would breach
        # INV-K1b. When ``kelly_multiplier`` is supplied it is that base; else the
        # ``evaluate_kelly`` default of 0.25.
        _f_cap_corr = float(_sdc["max_correlated_pct"])
        _kelly_base_cap = 0.25 if kelly_multiplier is None else float(kelly_multiplier)
        # Corr-weighted limit (INV-K1): ceiling = max_correlated_pct·B.
        _eff_corr = effective_bankroll(
            _b,
            float(sizing_context.corr_committed_usd),
            f_cap=_f_cap_corr,
        )
        # Absolute raw-dollar limit (INV-K1b): ceiling = max_portfolio_heat_pct·B,
        # reproduced in raw-bankroll space by the kelly base cap so f*·m/f_cap ≤ 1.
        _eff_raw = effective_bankroll_raw(
            _b,
            float(sizing_context.raw_committed_usd),
            float(_sdc["max_portfolio_heat_pct"]),
            f_cap=_kelly_base_cap,
        )
        sizing_bankroll = min(_eff_corr, _eff_raw)

    size_usd = kelly_size(
        float(p_posterior),
        execution_price,
        sizing_bankroll,
        kelly_mult=effective_multiplier,
    )

    # Task #107 INV-K3 (the single-bet cap): no single bet may exceed
    # ``max_single_position_pct·B`` (config 0.10) — the named headline defect
    # (live receipts showed 25-27%). Effective-bankroll reduction does NOT bound
    # a first (uncommitted) bet, so this hard clamp against the FULL bankroll is
    # the second belt. Only ever shrinks (never amplifies, INV-K8). Applied only
    # on the portfolio-aware path; unwired callers keep exact single-Kelly.
    _single_cap_usd: float | None = None
    if sizing_context is not None and sizing_context.has_portfolio_context:
        max_single_pct = float(sizing_defaults()["max_single_position_pct"])
        _single_cap_usd = max_single_pct * float(sizing_context.bankroll_usd)
        if size_usd > _single_cap_usd:
            size_usd = _single_cap_usd

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
        # _eff_corr / _eff_raw were already computed above.
        _eff_corr_diag = float(_eff_corr)
        _eff_raw_diag = float(_eff_raw)

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
        elif (
            sizing_context is not None
            and sizing_context.has_portfolio_context
            and _eff_corr_diag is not None
            and _eff_raw_diag is not None
        ):
            if _eff_corr_diag <= _eff_raw_diag:
                _binding = "corr_budget"
            else:
                _binding = "raw_heat_budget"
        else:
            # Non-portfolio path collapsed — should not normally happen if edge>0.
            _binding = "sized_ok" if size_usd > 0.0 else "zero_edge"
    elif (
        _single_cap_usd is not None
        and size_usd == _single_cap_usd
        and size_usd < kelly_size(
            float(p_posterior),
            execution_price,
            float(sizing_bankroll),
            kelly_mult=float(effective_multiplier),
        )
    ):
        _binding = "single_cap"
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
        single_cap_usd=_single_cap_usd,
        binding_constraint=_binding,
    )


def evaluate_riskguard(*, risk_decision_id: str, level: RiskLevel) -> RiskProof:
    return RiskProof(
        risk_decision_id=risk_decision_id,
        level=level,
        passed=level is RiskLevel.GREEN,
    )
