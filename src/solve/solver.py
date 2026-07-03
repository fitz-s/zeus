# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: design doc §3.3 (objective: expected log terminal wealth over joint
#   scenarios, full menu, scale by κ, discrete repair, safe prefixes); seam contract verbatim
#   from qkernel_spine_bridge.py:1332-1400 + family_decision_engine.py:583-635 (FamilyDecision);
#   CONSULT REV-2 rulings 2026-07-03 (CVaR robust objective; dominance baseline in the SAME
#   feasible set; FamilyDecisionContract validator; max_stake_usd shim-only; single-family only).
"""The joint SOLVE and its legacy-seam shim.

TWO-LAYER OUTPUT (packet §3): ``solve()`` → ``SolutionPlan`` is the truth (a multi-order plan
over the full menu, κ-scaled, discretely repaired with a certificate, safe-prefix ordered,
q_version-stamped). ``SolveEngineShim`` satisfies the frozen FamilyDecision seam and derives
the legacy single-selection view — plus a ``LegacyDecisionProjection`` so phase-1 promotion
evidence grades the ACTUALLY-executed primary leg, never the full plan's ΔU (consult REV-2).

MATH CORE (W3 sub-slice 2) fills ``solve()``:

* OBJECTIVE — robust expected Δlog-wealth over the joint outcome ATOMS. Wealth in atom ``a``
  under stake vector ``x`` (units per menu item) against the endowment ``W0[a]`` (cash + held
  claims) is the affine ``W_end(a) = W0[a] + Σ_i x_i · unit_payoff_i(a)``. The robust score is
  the LOWER-TAIL CVaR at the band's α of the per-draw expected log-growth:

      du_k(x) = Σ_a q_draws[k, a] · (log W_end(a) - log W0[a])
      U(x)    = CVaR_α( { du_k(x) } )            # mean of the worst α-fraction of draws

  CVaR (not the raw α-quantile) is used deliberately (consult REV-2): each ``du_k`` is concave
  in ``x`` (log of an affine wealth), and the lower-tail CVaR of concave functions is CONCAVE,
  so the objective is concave and coordinate ascent reaches the GLOBAL optimum — the legacy
  payoff_vector "quantile-of-concave is unimodal" assertion is unsafe and is NOT inherited.
  CVaR_α ≤ VaR_α, so this is also strictly more conservative than the served-band quantile.

* OPTIMIZER — deterministic cyclic coordinate ascent, each coordinate maximized by a
  coarse-to-fine 1-D grid holding the others fixed, sweeping until a full sweep improves ``U``
  by less than ``_CONVERGENCE_TOL`` or ``_MAX_SWEEPS`` is hit. No RNG, no wall clock; the only
  sampling is the served band draws. Seeded at the best single item so the plan dominates the
  top-1 picker by construction.

* DOMINANCE BASELINE — the top-1 pick is the best SINGLE menu item taken through the SAME
  feasible set (same depth/budget, same κ, same discrete repair, same worst-price model), not
  the legacy raw candidate score (consult REV-2). ``delta_u_baseline_top1`` is that repaired
  single-order plan's ΔU; the emitted plan is ``max`` over {joint, top1}, so it never scores
  below the picker at the EXECUTED level.

* DISCRETE REPAIR — κ scales the continuous solution; scaled stakes are quantized on each
  item's OWN tick/min grid (sub-floor-but-positive promoted UP to min_order_size), capped at
  depth and at ``_MAX_ORDERS``, and the rounded plan is RE-EVALUATED under the worst-price
  model. A plan is submit-worthy ONLY if its repaired ΔU is still ``> 0``; the proof is a
  ``RepairCertificate`` on the SolutionPlan (enforced by SolutionPlan.__post_init__).

* SCOPE — single-family only (multi-family fails closed in the ScenarioService); a non-positive
  endowment atom is refused up front with a typed ``ZeroWealthOutcomeError``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.solve.kappa import KappaPolicy
from src.solve.scenario_service import ScenarioService
from src.solve.types import SolutionPlan, SolveMenu, WealthStateByAtom

if TYPE_CHECKING:
    from src.decision.family_decision_engine import FamilyDecision

# Every field _record_qkernel_selection_family_facts / the proof overlay / receipts read off
# FamilyDecision (getattr-with-default consumers — silent-degrade class). The contract validator
# asserts presence AND non-null semantics; renaming/nulling any of these is a contract break.
_REQUIRED_FAMILY_DECISION_FIELDS = (
    "decision_id",
    "case",
    "predictive",
    "omega",
    "joint_q",
    "band",
    "family_book",
    "market_coherence",
    "candidates",
    "selected",
    "no_trade_reason",
    "receipt_hash",
    "candidate_decisions",
    "market_implied_q",
    "portfolio_comparisons",
)


class FamilyDecisionContractError(AssertionError):
    """A FamilyDecision violates the frozen seam contract (missing/nulled consumer field)."""


def validate_family_decision_contract(decision: "FamilyDecision") -> "FamilyDecision":
    """Loud guard against the getattr-soft-fail class (consult REV-2: presence is not enough).

    Checks every consumer-read field is PRESENT and carries non-null semantics where required:
    a stable ``decision_id``/``receipt_hash``, a ``candidate_decisions`` tuple the facts writer
    can iterate, and exactly one of ``selected`` (trade) / ``no_trade_reason`` (no-trade). A
    break raises loudly here rather than degrading attribution silently downstream.
    """
    missing = [f for f in _REQUIRED_FAMILY_DECISION_FIELDS if not hasattr(decision, f)]
    if missing:
        raise FamilyDecisionContractError(
            f"FamilyDecision contract break — missing fields {missing}; downstream consumers read "
            "these via getattr-with-default and would degrade silently"
        )
    if not getattr(decision, "decision_id", None):
        raise FamilyDecisionContractError("FamilyDecision.decision_id must be a non-empty id")
    if not getattr(decision, "receipt_hash", None):
        raise FamilyDecisionContractError("FamilyDecision.receipt_hash must be a non-empty hash")
    if not isinstance(getattr(decision, "candidate_decisions", None), tuple):
        raise FamilyDecisionContractError(
            "FamilyDecision.candidate_decisions must be a tuple (the facts writer iterates it)"
        )
    selected = getattr(decision, "selected", None)
    no_trade_reason = getattr(decision, "no_trade_reason", None)
    if (selected is None) == (no_trade_reason is None):
        raise FamilyDecisionContractError(
            "FamilyDecision must carry exactly one of selected (trade) / no_trade_reason (no-trade)"
        )
    return decision


def solve(
    menu: SolveMenu,
    *,
    scenarios: ScenarioService,
    wealth: WealthStateByAtom,
    kappa_policy: KappaPolicy,
    bands_by_family: Any,          # Mapping[str, JointQBand] — typed loosely to stay import-light
    q_version: str,
) -> SolutionPlan:
    """The joint SOLVE (math core, W3 sub-slice 2) — see module docstring for the contract.

    ``max_stake_usd`` is intentionally ABSENT from the core signature (consult REV-2 ruling 6):
    the solver is budget-aware via ``WealthStateByAtom.cash_usd`` (the ledger's spendable
    snapshot, present in every atom's wealth); any legacy cash cap is a shim-side concern
    converted to a cash constraint before core solve, never a second authority in the math.
    """
    raise NotImplementedError(
        "W3 sub-slice 2 (opus math core): joint CVaR Δlog-wealth over JointOutcomeScenarioSet, "
        "κ-scaled, discrete-repaired with a RepairCertificate, safe-prefixed — see docstring"
    )


class SolveEngineShim:
    """Drop-in replacement at the qkernel_spine_bridge.py:1332 construction seam.

    Accepts the SAME constructor surface the bridge passes to FamilyDecisionEngine and the SAME
    decide() call of :1379. Internally: assemble SolveMenu (menu_adapter) → solve() → derive
    FamilyDecision + a LegacyDecisionProjection (phase-1 evidence grades the projection, never
    SolutionPlan.expected_delta_log_wealth — consult REV-2).
    """

    def __init__(self, **engine_kwargs: Any) -> None:
        # Sub-slice 3 wires: store the injected builders/readers; the shim reuses the bridge's
        # served-belief inputs verbatim (one-belief law — never rebuild σ).
        self._engine_kwargs = engine_kwargs

    def decide(
        self,
        case: Any,
        omega: Any,
        snapshots: Any,
        *,
        portfolio: Any,
        matrix: Any,
        captured_at_utc: Any,
        sizing_candidates: Any,
        max_stake_usd: Any,
        shares_for_routing: Any,
        served_joint_q: Any,
        served_band: Any,
        served_payoff_q_lcb_by_side: Any,
    ) -> "FamilyDecision":
        """EXACT seam signature (qkernel_spine_bridge.py:1379). Returns FamilyDecision.

        Derivation contract (sub-slice 3): plan's primary order → ``selected``; full plan →
        candidate_decisions provenance (coherence_allows=True per §4 decision 1); no-trade →
        no_trade_reason; a LegacyDecisionProjection re-scoring the primary leg standalone at its
        post-downstream-haircut size (phase-1 no-trade if that ΔU ≤ 0); ``max_stake_usd``
        converted to a cash constraint here (shim-only); then ``validate_family_decision_contract``.
        """
        raise NotImplementedError(
            "W3 sub-slice 3: menu assembly + solve() + FamilyDecision derivation + "
            "LegacyDecisionProjection + validate_family_decision_contract — see class docstring"
        )
