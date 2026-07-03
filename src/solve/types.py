# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: design doc §3.3 menu/objective; architecture doc §1 SOLVE row;
#   W1.2 q_version stamping law (SCH-W1.2-ORDER-STATE); §4 decision 2 (correlation_rail=caps).
"""Typed inputs/outputs of the W3 SOLVE.

Design constraints these types encode (do not weaken):

* Every planned order carries the ``q_version`` (posterior_identity_hash) it was decided
  on — the W1.2 stamp law: decision basis is frozen at decision time, NULL is reserved
  for non-decision rows (reconcile backfills), so a SOLVE output MUST always stamp.
* Every plan carries ``correlation_rail`` — architecture doc §4 decision 2: receipts
  stamp the rail in force (``"caps"`` until the C4 scenario service replaces the
  transitional independent-product measure) so settlement grading can measure what C4
  changes.
* Wealth-by-outcome (``WealthByOutcome``) is the endowment state the C5 exit marginal
  rule needs (``b·Σq_j/W_j > q_i/W_i``). No such state exists in the codebase today
  (W3.EXIT brief risk #1) — it is DERIVED per solve from open positions grouped by
  outcome bin, never persisted (derive-don't-store doctrine, cf. W1.2 Option B).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Mapping, Optional

if TYPE_CHECKING:  # heavy zeus types stay import-lazy; skeleton must be import-clean
    import numpy as np

    from src.execution.negrisk_routes import RouteCost
    from src.probability.joint_q_band import JointQBand


# --- menu -------------------------------------------------------------------

MenuItemKind = Literal[
    "buy_yes",          # taker along ask depth
    "buy_no",
    "sell_holding",     # taker along bid depth, bounded by held shares
    "convert_no_to_yes_basket",   # W2.4 primitive; executable=False until conversion
    "split_collateral",           #   route builder lands (packet §5)
    "merge_full_set",
    "maker_quote",      # post-only; valued with reservation cost + C2 shadow values
    "hold_cash",        # cash with release-time shadow value
]


@dataclass(frozen=True)
class MenuItem:
    """One executable (or explicitly non-executable) action on the venue menu.

    Wraps a ``RouteCost`` where one exists (all order-shaped kinds); conversion kinds
    carry the W2.4 primitive identity instead. ``executable=False`` items stay in the
    menu so the solver's audit trail shows what was priced out, mirroring
    ``negrisk_routes``' NO_DEPTH discipline (never clamp, mark and keep).
    """

    item_id: str
    kind: MenuItemKind
    family_key: str                      # WeatherFamilyKey string form (city|date|metric)
    bin_id: Optional[str]
    route: Optional["RouteCost"]         # None for conversion/cash kinds
    executable: bool
    non_executable_reason: Optional[str]
    # Payoff vector over the family's omega bins for ONE unit of this action, after
    # cost. Math core fills construction (generalizes payoff_vector's g_y).
    unit_payoff_by_bin: Mapping[str, float] = field(default_factory=dict)
    max_units: Decimal = Decimal("0")    # depth/holdings/reservation bound


@dataclass(frozen=True)
class SolveMenu:
    """The full menu for one solve invocation (single family in W3; joint in C4+)."""

    family_key: str
    items: tuple[MenuItem, ...]
    min_tick_size: Decimal
    min_order_size: Decimal
    menu_hash: str                       # deterministic over items — receipt anchor


# --- endowment / wealth-by-outcome -------------------------------------------

@dataclass(frozen=True)
class WealthByOutcome:
    """W_j: terminal wealth in each outcome bin under current holdings + cash.

    DERIVED at solve time (never persisted). ``wealth_by_bin_id`` covers the family's
    omega bins exactly; ``cash_usd`` is spendable per the CAS ledger's one-snapshot
    read (W1.1). The C5 exit marginal rule and the entry objective consume this as
    the log-growth baseline (payoff_vector precedent: A_y = exposure.a(y)).
    """

    family_key: str
    wealth_by_bin_id: Mapping[str, float]
    cash_usd: float
    source_positions: tuple[str, ...]    # position/trade ids folded in — audit trail


# --- scenarios ---------------------------------------------------------------

@dataclass(frozen=True)
class ScenarioSet:
    """Joint outcome scenarios the objective integrates over.

    ``samples`` has shape (n_draws, n_bins) over ``bin_ids`` — for the transitional
    single-family service this is EXACTLY the family's ``JointQBand.samples`` (each row
    a coherent simplex point). A C4 cross-family service returns the same shape over a
    concatenated bin axis with ``family_slices`` marking each family's span, so the
    solver needs no code change at the swap (§4 decision 2b).
    """

    bin_ids: tuple[str, ...]
    samples: "np.ndarray"
    family_slices: Mapping[str, tuple[int, int]]
    provider: str                        # "transitional_independent_product" | "c4_joint"
    sample_hash: str


# --- plan --------------------------------------------------------------------

@dataclass(frozen=True)
class PlannedOrder:
    """One order (or venue primitive invocation) in the solved plan.

    ``q_version`` is MANDATORY (non-None) — a SOLVE output is by construction a
    decision-basis-bearing command (W1.2 stamp law). ``safe_prefix_index`` orders the
    plan into W2.1 safe prefixes: every prefix leaves acceptable exposure if later
    chunks fail (design doc §3.3 discrete repair).
    """

    order_id: str
    menu_item_id: str
    kind: MenuItemKind
    side: Optional[Literal["buy", "sell"]]
    token_id: Optional[str]
    price: Optional[Decimal]
    size: Decimal
    q_version: str
    safe_prefix_index: int
    snapshot_id: Optional[str]           # FC-03: stamped at envelope build, not here


@dataclass(frozen=True)
class SolutionPlan:
    """The SOLVE output: the full planned action set plus receipt provenance.

    The legacy seam (FamilyDecision) is DERIVED from this via solver.py's shim; this
    object is the truth the receipts, the batch executor (W2.1), and the evidence gate
    consume.
    """

    plan_id: str
    family_key: str
    orders: tuple[PlannedOrder, ...]
    expected_delta_log_wealth: float     # robust (band-quantile) ΔU of the WHOLE plan
    delta_u_baseline_top1: Optional[float]  # what the top-1 picker would have scored —
                                            # the solver≥picker property test hook
    kappa_applied: float
    correlation_rail: Literal["caps", "c4_scenarios"]
    scenario_provider: str
    scenario_sample_hash: str
    menu_hash: str
    q_version: str                       # family-level decision basis (all orders share)
    no_trade_reason: Optional[str]       # None iff orders non-empty
    diagnostics: Mapping[str, float] = field(default_factory=dict)
