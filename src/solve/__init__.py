# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: docs/rebuild/order_engine_first_principles_design_2026-07-02.md §3.3 (SOLVE)
#   + docs/rebuild/schema_packets/w3_solve_design_packet_2026-07-03.md (W3 design packet).
"""W3 SOLVE package — joint expected-log-wealth planner over the full venue menu.

INTERFACE SKELETON (W3 sub-slice 1). Math bodies land in sub-slice 2 (opus math core);
every NotImplementedError below carries the one-line contract the body must satisfy.

Layering (see w3_solve_design_packet_2026-07-03.md §2):
  types.py            — typed inputs/outputs (menu, endowment, scenarios, plan)
  scenario_service.py — ScenarioService protocol + transitional independent-product impl
  menu_adapter.py     — NegRiskRouteSet → SolveMenu adaptation
  solver.py           — solve() → SolutionPlan, plus the FamilyDecision-shaped seam shim
  kappa.py            — fractional shading policy (κ=1.0 during promotion window)
  exits.py            — exits-as-same-solve (C5 marginal rule) interface

The ONLY production seam this package will be wired into is
src/engine/qkernel_spine_bridge.py:1332 (engine construction) behind the time-boxed
promotion flag; nothing imports this package yet.
"""

from src.solve.types import (  # noqa: F401
    AtomPayoffProjector,
    JointOutcomeAtom,
    JointOutcomeScenarioSet,
    LegacyDecisionProjection,
    MenuItem,
    PlannedOrder,
    RepairCertificate,
    SolutionPlan,
    SolveMenu,
    WealthStateByAtom,
)
