# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: architecture doc §1 SOLVE row (menu from negrisk_routes, REUSE-grade);
#   W3.MATH brief (RouteCost/NegRiskRouteSet field inventory, conversion_routes stays ()).
"""Menu adapter — NegRiskRouteSet (+ holdings + maker/cash lanes) → SolveMenu.

``negrisk_routes.build_negrisk_route_set`` already enumerates direct/synthetic/pair/
full-basket routes size-aware on executable ladders, marking depth-starved legs
``executable=False`` (never clamping). This adapter is a PURE reshaping layer: it must
not re-price, must not drop non-executable items (audit trail), and must not invent
conversion routes — ``conversion_routes`` is unconditionally ``()`` today because no
conversion-route BUILDER exists in negrisk_routes (W3.MATH brief: the W2.4 adapter
primitives exist but nothing constructs a CONVERSION_SELL_BASKET RouteCost). Flipping
conversions executable = writing that builder — packet §5, a separate reviewed step,
NOT this adapter silently synthesizing routes.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Mapping, Optional

from src.solve.types import SolveMenu, WealthByOutcome

if TYPE_CHECKING:
    from src.execution.family_book import FamilyBook
    from src.execution.negrisk_routes import NegRiskRouteSet


def build_solve_menu(
    route_set: "NegRiskRouteSet",
    *,
    family_key: str,
    family_book: "FamilyBook",
    holdings_by_bin_id: Mapping[str, Decimal],
    wealth: Optional[WealthByOutcome] = None,
    include_maker_lane: bool = False,
) -> SolveMenu:
    """Adapt the priced route menu into the solver's SolveMenu.

    Contract (math core / sub-slice 2 implements):
    * every RouteCost in direct_yes/direct_no/synthetic_not_i/pair_arbs/full_basket_arbs
      becomes one MenuItem with unit_payoff_by_bin derived from its legs' payoff at the
      route's avg_cost (generalizing payoff_vector's per-candidate payoff construction);
    * held positions add sell_holding items along BID depth, max_units = held shares;
    * conversion_routes pass through as-is (empty today; items appear when the builder
      lands, still executable=False until W2.4 dry-run gate clears);
    * maker lane (post-only quotes) only when include_maker_lane AND REST_ELIGIBLE —
      W4 wires that predicate; W3 default False;
    * hold_cash is always the last item (max_units = spendable);
    * menu_hash is deterministic over item identity + pricing inputs.
    """
    raise NotImplementedError(
        "W3 sub-slice 2: pure reshaping of priced RouteCosts into MenuItems — no re-pricing, "
        "no dropping non-executable items, no synthesizing conversion routes"
    )
