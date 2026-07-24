# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: docs/rebuild/order_engine_first_principles_design_2026-07-02.md §3.3 (SOLVE)
#   + docs/rebuild/schema_packets/w3_solve_design_packet_2026-07-03.md (W3 design packet).
"""Current-state expected-log-wealth contracts used by the live global selector.

Layering (see w3_solve_design_packet_2026-07-03.md §2):
  types.py            — typed inputs/outputs (menu, endowment, scenarios, plan)
  scenario_service.py — ScenarioService protocol + transitional independent-product impl
  menu_adapter.py     — NegRiskRouteSet → SolveMenu adaptation
  solver.py           — current probability, payoff, executable-curve, and wealth helpers
  exits.py            — exits-as-same-solve (C5 marginal rule) interface

The live global selector imports the required helpers directly. There is no
alternate engine-construction seam or activation flag.
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
