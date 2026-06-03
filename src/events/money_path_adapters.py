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
    from src.sizing.sizing_context import effective_bankroll
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
    # of correlation-weighted already-committed capital. The budget lives in the
    # bankroll argument; kelly.py's formula stays untouched. ``effective_bankroll``
    # divides the committed-capital reduction back by ``effective_multiplier`` so
    # kelly.py's own ``·effective_multiplier`` reproduces the design's
    # ``s = f*·f_cap·B_eff``; the simultaneous stakes then sum to ≤
    # ``effective_multiplier·B`` (INV-K1). A context with no portfolio fields
    # (the #103 3-arg ``from_candidate_proof``, ``has_portfolio_context`` False)
    # sizes against the raw bankroll exactly as before #107 — INV-K8 holds with
    # EQUALITY for the unwired case.
    sizing_bankroll = float(bankroll_usd)
    if sizing_context is not None and sizing_context.has_portfolio_context:
        sizing_bankroll = effective_bankroll(
            float(sizing_context.bankroll_usd),
            float(sizing_context.corr_committed_usd),
            f_cap=float(effective_multiplier),
        )

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
    if sizing_context is not None and sizing_context.has_portfolio_context:
        max_single_pct = float(sizing_defaults()["max_single_position_pct"])
        single_cap_usd = max_single_pct * float(sizing_context.bankroll_usd)
        if size_usd > single_cap_usd:
            size_usd = single_cap_usd

    return KellyProof(
        kelly_decision_id=kelly_decision_id,
        execution_price=execution_price,
        size_usd=size_usd,
        passed=size_usd > 0,
    )


def evaluate_riskguard(*, risk_decision_id: str, level: RiskLevel) -> RiskProof:
    return RiskProof(
        risk_decision_id=risk_decision_id,
        level=level,
        passed=level is RiskLevel.GREEN,
    )
