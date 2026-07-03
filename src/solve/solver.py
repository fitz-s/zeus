# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: design doc §3.3 (objective: expected log terminal wealth over joint
#   scenarios, full menu, scale by κ, discrete repair, safe prefixes); seam contract verbatim
#   from qkernel_spine_bridge.py:1332-1400 + family_decision_engine.py:583-635 (FamilyDecision).
"""The joint SOLVE and its legacy-seam shim.

TWO-LAYER OUTPUT (the key W3 interface decision, packet §3):

1. ``solve()`` → ``SolutionPlan`` — the real product: a multi-order plan over the full
   menu (buy/sell/convert/maker/cash), κ-scaled, discretely repaired, safe-prefix
   ordered, q_version-stamped. The batch executor (W2.1) and the receipts consume this.

2. ``SolveEngineShim`` — satisfies the EXACT current seam: constructible where
   ``FamilyDecisionEngine`` is constructed (qkernel_spine_bridge.py:1332) and callable
   with the EXACT ``decide()`` kwargs of qkernel_spine_bridge.py:1379, returning the
   EXACT ``FamilyDecision`` shape. The shim derives the legacy single-selection view
   from the plan (primary order → selected CandidateEconomics; plan-level no-trade →
   no_trade_reason) so every downstream consumer — the proof overlay at
   qkernel_spine_bridge.py:1684, _record_qkernel_selection_family_facts at
   event_reactor_adapter.py:4415, receipts — keeps working UNCHANGED during promotion.

   SOFT-FAIL HAZARD (W3.SEAM brief): _record_qkernel_selection_family_facts reads
   FamilyDecision fields via getattr-with-default — a missing/renamed field degrades
   attribution SILENTLY. Mitigation: the shim VALIDATES at construction time that its
   output object carries every field in ``_REQUIRED_FAMILY_DECISION_FIELDS`` (loud
   AssertionError in tests, never a silent skip in production attribution).

   COHERENCE CONTRACT (§4 decision 1): the shim does NOT populate a coherence veto —
   candidate_decisions carry coherence_allows=True unconditionally, and the overlay's
   COHERENCE_BLOCKED guard is retired in the same packet that flips the flag ON
   (lockstep edit noted in the packet §7; the report is re-emitted as a divergence
   event + re-decision priority key in W4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from src.solve.kappa import KappaPolicy
from src.solve.scenario_service import ScenarioService
from src.solve.types import SolutionPlan, SolveMenu, WealthByOutcome

if TYPE_CHECKING:
    from src.decision.family_decision_engine import FamilyDecision

# Every field _record_qkernel_selection_family_facts / the proof overlay / receipts read
# off FamilyDecision (getattr-with-default consumers — silent-degrade class). The shim
# asserts presence at construction; renaming any of these is a contract break.
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


def solve(
    menu: SolveMenu,
    *,
    scenarios: ScenarioService,
    wealth: WealthByOutcome,
    kappa_policy: KappaPolicy,
    bands_by_family: Any,          # Mapping[str, JointQBand] — typed loosely to stay import-light
    q_version: str,
    max_stake_usd: Optional[Any] = None,
) -> SolutionPlan:
    """The joint SOLVE (math core, W3 sub-slice 2).

    Contract the body must satisfy (property-test anchors, packet §4):
    * objective: robust (band-quantile α, payoff_vector precedent) expected Δlog-wealth
      of the WHOLE plan against the WealthByOutcome baseline, integrated over
      scenarios.scenarios(bands_by_family).samples — never a per-item greedy sum;
    * solver ≥ top-1: expected_delta_log_wealth ≥ delta_u_baseline_top1 on EVERY input
      (the top-1 pick is a feasible plan, so the optimum dominates by construction —
      violating this is a bug, and the field exists so tests assert it);
    * zero-edge → zero-stake; monotone in q; κ scales the continuous solution BEFORE
      discrete repair; repair rounds onto min_tick_size/min_order_size and re-verifies
      the rounded plan still improves expected log under worst-price checks, else
      no-trade;
    * plan orders carry safe_prefix_index per W2.1's injected acceptability predicate;
    * every PlannedOrder.q_version == q_version (stamp law).
    """
    raise NotImplementedError(
        "W3 sub-slice 2 (opus math core): joint robust Δlog-wealth over ScenarioSet, "
        "κ-scaled, discrete-repaired, safe-prefixed — see docstring contract"
    )


class SolveEngineShim:
    """Drop-in replacement at the qkernel_spine_bridge.py:1332 construction seam.

    Accepts the SAME constructor surface the bridge passes to FamilyDecisionEngine
    (fresh_model_reader, day0_reader, predictive_builder, enable_negrisk_routes,
    family_book_builder, route_set_builder, selection_objective, n_band_draws,
    band_alpha) and the SAME decide() call of :1379. Internally: assemble SolveMenu
    (menu_adapter) → solve() → derive FamilyDecision.
    """

    def __init__(self, **engine_kwargs: Any) -> None:
        # Sub-slice 3 wires: store the injected builders/readers; the shim reuses the
        # bridge's served-belief inputs verbatim (one-belief law — never rebuild σ).
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

        Derivation contract (sub-slice 3): plan's primary order → ``selected``
        CandidateEconomics; full plan → candidate_decisions provenance (coherence_allows
        =True per §4 decision 1); no-trade plan → no_trade_reason; receipt_hash over the
        plan tuple; then ``_assert_contract_fields`` before returning.
        """
        raise NotImplementedError(
            "W3 sub-slice 3: menu assembly + solve() + FamilyDecision derivation + "
            "_assert_contract_fields — see class docstring"
        )

    @staticmethod
    def _assert_contract_fields(decision: "FamilyDecision") -> "FamilyDecision":
        """Loud guard against the getattr-soft-fail class: every consumer-read field present."""
        missing = [f for f in _REQUIRED_FAMILY_DECISION_FIELDS if not hasattr(decision, f)]
        if missing:
            raise AssertionError(
                f"FamilyDecision contract break — missing fields {missing}; downstream "
                "consumers read these via getattr-with-default and would degrade silently"
            )
        return decision
