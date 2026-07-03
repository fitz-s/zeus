# Created: 2026-07-03
# Last reused or audited: 2026-07-03
# Authority basis: design doc §3.3 menu/objective; architecture doc §1 SOLVE row;
#   W1.2 q_version stamping law (SCH-W1.2-ORDER-STATE); §4 decision 2 (correlation_rail);
#   CONSULT REV-2 rulings 2026-07-03 (joint outcome atom axis; wealth-by-atom + ledger state;
#   repair certificate; legacy projection; typed payoff projector; per-leg tick/min-size;
#   envelope metadata) — see design packet appendix "CONSULT REV-2 RULINGS".
"""Typed inputs/outputs of the W3 SOLVE.

Design constraints these types encode (do not weaken):

* JOINT OUTCOME ATOM AXIS (consult REV-2 blocker). Scenarios and wealth live on ONE axis
  of ``JointOutcomeAtom`` — each atom is a full joint outcome ``{family_key: bin_id}``.
  Single-family W3 is the degenerate case (one entry per atom); a C4 measured cross-family
  distribution swaps the ScenarioService with NO solver change because the solver already
  integrates over atoms, not concatenated marginal bins. Per-family marginals are DERIVED
  projections, never the primitive.
* Every planned order carries the ``q_version`` (posterior_identity_hash) it was decided
  on — the W1.2 stamp law: decision basis is frozen at decision time, NULL is reserved
  for non-decision rows (reconcile backfills), so a SOLVE output MUST always stamp.
* Every plan carries ``correlation_rail`` — architecture doc §4 decision 2: receipts stamp
  the rail in force (``"caps"`` single-family until C4; ``"caps_degraded_not_optimal"`` for
  any degraded multi-family mode, which BLOCKS promotion evidence — consult ruling 2).
* Wealth-by-atom (``WealthStateByAtom``) is the endowment state the C5 exit marginal rule
  needs. It is DERIVED per solve from the CAS ledger snapshot (open positions grouped by
  outcome atom + spendable cash net of reservations/resting/unsettled) — never persisted
  (derive-don't-store; W1.2 Option B). The derivation itself is ``exits.build_wealth_by_atom``
  (a later sub-slice); this module only types the result.
* A non-empty plan MUST carry a ``RepairCertificate`` proving the rounded discrete plan
  still improves expected log under worst-price checks (consult REV-2 blocker) — enforced in
  ``SolutionPlan.__post_init__``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Mapping, Optional, Sequence

import numpy as np

if TYPE_CHECKING:  # keep the package import-light and side-effect-free until G3 passes
    from src.execution.negrisk_routes import RouteCost

# --- joint outcome atom axis -------------------------------------------------

JointDrawSemantics = Literal[
    "POSTERIOR_Q_DRAWS",   # rows are posterior draws of the outcome distribution (W3 band)
    "PRODUCT_MEASURE",     # rows are an explicit independent-product measure (validated)
    "MEASURED_JOINT",      # rows are a C4 measured cross-family joint distribution
]


@dataclass(frozen=True)
class JointOutcomeAtom:
    """One joint outcome — a resolved ``bin_id`` for each family in scope.

    Single-family W3: exactly one entry. The ``atom_id`` is the canonical string form
    (``"family=bin|family2=bin2"`` over sorted families) so hashing/alignment is stable.
    """

    bins_by_family: Mapping[str, str]
    atom_id: str

    @staticmethod
    def canonical_id(bins_by_family: Mapping[str, str]) -> str:
        return "|".join(f"{f}={bins_by_family[f]}" for f in sorted(bins_by_family))

    @classmethod
    def of(cls, bins_by_family: Mapping[str, str]) -> "JointOutcomeAtom":
        return cls(bins_by_family=dict(bins_by_family), atom_id=cls.canonical_id(bins_by_family))


class ScenarioValidationError(ValueError):
    """A JointOutcomeScenarioSet is not coherent (shape / simplex / weights / dtype)."""


@dataclass(frozen=True)
class JointOutcomeScenarioSet:
    """The joint outcome scenarios the objective integrates over — the ONE ScenarioService
    product (consult REV-2: replaces the concatenated-marginal ``ScenarioSet``).

    ``q_draws`` has shape ``(n_draws, n_atoms)`` aligned 1:1 with ``atoms``. Under
    ``POSTERIOR_Q_DRAWS`` every row is a coherent simplex over the atoms (the served band's
    joint draws). ``draw_weights`` (optional) weights the draws; ``None`` means uniform.
    ``family_projections`` maps each family_key to the atom indices whose ``bins_by_family``
    the marginal reads — a DERIVED convenience view, not the primitive.

    ``scenario_hash`` is canonical over provider+version+semantics+alpha+atom axis+weights+
    q_draws bytes+band hashes (consult REV-2: hash must cover the full schema, not just
    per-family sample hashes) so any change of belief, weighting, or provider is loud.
    """

    atoms: tuple[JointOutcomeAtom, ...]
    q_draws: np.ndarray
    semantics: JointDrawSemantics
    alpha: float
    provider: str
    provider_version: str
    band_hashes_by_family: Mapping[str, str]
    scenario_hash: str
    draw_weights: Optional[np.ndarray] = None
    family_projections: Mapping[str, tuple[int, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        q = self.q_draws
        if q.ndim != 2 or q.shape[1] != len(self.atoms):
            raise ScenarioValidationError(
                f"q_draws shape {q.shape} does not match {len(self.atoms)} atoms"
            )
        if q.dtype != np.float64:
            raise ScenarioValidationError(f"q_draws must be float64 (canonical), got {q.dtype}")
        if not np.isfinite(q).all():
            raise ScenarioValidationError("q_draws contains non-finite values")
        if (q < 0.0).any():
            raise ScenarioValidationError("q_draws contains negative probabilities")
        if self.semantics == "POSTERIOR_Q_DRAWS":
            sums = q.sum(axis=1)
            if not np.allclose(sums, 1.0, atol=1e-9):
                raise ScenarioValidationError(
                    "POSTERIOR_Q_DRAWS rows must be simplex points (row sums == 1)"
                )
        if self.draw_weights is not None:
            w = self.draw_weights
            if w.shape != (q.shape[0],):
                raise ScenarioValidationError(f"draw_weights shape {w.shape} != ({q.shape[0]},)")
            if (w < 0.0).any() or not np.isfinite(w).all() or float(w.sum()) <= 0.0:
                raise ScenarioValidationError("draw_weights must be finite, nonnegative, sum>0")
        if not (0.0 < self.alpha < 1.0):
            raise ScenarioValidationError(f"DEGENERATE_ALPHA: alpha={self.alpha!r}")

    @property
    def atom_ids(self) -> tuple[str, ...]:
        return tuple(a.atom_id for a in self.atoms)

    @staticmethod
    def compute_hash(
        *,
        atoms: Sequence[JointOutcomeAtom],
        q_draws: np.ndarray,
        semantics: str,
        alpha: float,
        provider: str,
        provider_version: str,
        band_hashes_by_family: Mapping[str, str],
        draw_weights: Optional[np.ndarray],
    ) -> str:
        digest = hashlib.sha256()
        digest.update(provider.encode())
        digest.update(provider_version.encode())
        digest.update(semantics.encode())
        digest.update(repr(round(float(alpha), 12)).encode())
        for a in atoms:
            digest.update(a.atom_id.encode())
            digest.update(b"\x1e")
        digest.update(np.ascontiguousarray(q_draws, dtype=np.float64).tobytes())
        if draw_weights is None:
            digest.update(b"UNIFORM")
        else:
            digest.update(np.ascontiguousarray(draw_weights, dtype=np.float64).tobytes())
        for fam in sorted(band_hashes_by_family):
            digest.update(fam.encode())
            digest.update(band_hashes_by_family[fam].encode())
        return digest.hexdigest()

    @classmethod
    def build(
        cls,
        *,
        atoms: Sequence[JointOutcomeAtom],
        q_draws: np.ndarray,
        semantics: JointDrawSemantics,
        alpha: float,
        provider: str,
        provider_version: str,
        band_hashes_by_family: Mapping[str, str],
        draw_weights: Optional[np.ndarray] = None,
        family_projections: Optional[Mapping[str, tuple[int, ...]]] = None,
    ) -> "JointOutcomeScenarioSet":
        """Validate, canonicalize dtype, compute the schema-covering hash, construct."""
        atoms_t = tuple(atoms)
        q = np.ascontiguousarray(np.asarray(q_draws, dtype=np.float64))
        w = None if draw_weights is None else np.ascontiguousarray(np.asarray(draw_weights, dtype=np.float64))
        scenario_hash = cls.compute_hash(
            atoms=atoms_t,
            q_draws=q,
            semantics=semantics,
            alpha=alpha,
            provider=provider,
            provider_version=provider_version,
            band_hashes_by_family=band_hashes_by_family,
            draw_weights=w,
        )
        return cls(
            atoms=atoms_t,
            q_draws=q,
            semantics=semantics,
            alpha=float(alpha),
            provider=provider,
            provider_version=provider_version,
            band_hashes_by_family=dict(band_hashes_by_family),
            scenario_hash=scenario_hash,
            draw_weights=w,
            family_projections=dict(family_projections or {}),
        )


# --- endowment / wealth-by-atom ----------------------------------------------

@dataclass(frozen=True)
class WealthStateByAtom:
    """W_a: terminal wealth in each joint outcome atom under current holdings + cash.

    DERIVED at solve time from the CAS ledger snapshot (never persisted). ``wealth_by_atom``
    covers the scenario atom axis exactly; ``cash_usd`` is the ledger's spendable snapshot
    already NET of ``reservations_usd`` (pending order reservations). ``resting_orders_notional``
    and ``unsettled_proceeds_usd`` are carried for auditability and the C5 exit marginal;
    ``ledger_snapshot_id`` ties the wealth state to the ledger read it came from (consult
    REV-2 blocker: exits need ledger-aligned state, not per-family bins).
    """

    atom_ids: tuple[str, ...]
    wealth_by_atom: Mapping[str, float]
    cash_usd: float
    reservations_usd: float = 0.0
    resting_orders_notional: float = 0.0
    unsettled_proceeds_usd: float = 0.0
    ledger_snapshot_id: Optional[str] = None
    source_positions: tuple[str, ...] = ()

    def vector(self, atom_ids: Sequence[str]) -> np.ndarray:
        return np.array([float(self.wealth_by_atom[a]) for a in atom_ids], dtype=np.float64)


# --- menu --------------------------------------------------------------------

MenuItemKind = Literal[
    "buy_yes",          # taker along ask depth
    "buy_no",
    "sell_holding",     # taker along bid depth, bounded by held shares
    "convert_no_to_yes_basket",   # W2.4 primitive; executable=False until conversion
    "split_collateral",           #   route builder lands (packet §5)
    "merge_full_set",
    "maker_quote",      # post-only; DISABLED in W3 (taker-only, consult REV-2 ruling 6)
    "hold_cash",        # cash with release-time shadow value
]


@dataclass(frozen=True)
class AtomPayoffProjector:
    """Typed per-unit net-payoff projector over the joint outcome atom axis (consult REV-2:
    replaces ``Mapping[str, float]`` so payoff has provenance and can express cross-family
    atoms + the cash outlay of one unit).

    ``payoff_by_atom_id`` is the NET (after-cost) Arrow-Debreu payoff of ONE unit in each
    atom — a buy pays ``1 - avg_cost`` in its winning atoms and ``-avg_cost`` elsewhere.
    ``unit_cost_usd`` is the cash outlay per unit (``>= 0`` for a buy, ``< 0`` sell proceeds),
    carried explicitly for the repair budget accounting.
    """

    payoff_by_atom_id: Mapping[str, float]
    unit_cost_usd: float

    def vector(self, atom_ids: Sequence[str]) -> np.ndarray:
        return np.array([float(self.payoff_by_atom_id.get(a, 0.0)) for a in atom_ids], dtype=np.float64)


@dataclass(frozen=True)
class MenuItem:
    """One executable (or explicitly non-executable) action on the venue menu.

    Wraps a ``RouteCost`` where one exists (all order-shaped kinds); conversion kinds carry
    the W2.4 primitive identity instead. ``executable=False`` items stay in the menu so the
    solver's audit trail shows what was priced out (never clamp, mark and keep). ``min_tick_size``
    / ``min_order_size`` are PER-LEG/instrument (consult REV-2: a heterogeneous multi-leg menu
    cannot share one tick/min), so discrete repair rounds each order on its own grid.
    """

    item_id: str
    kind: MenuItemKind
    family_key: str                      # WeatherFamilyKey string form (city|date|metric)
    bin_id: Optional[str]
    route: Optional[RouteCost]           # None for conversion/cash kinds
    executable: bool
    non_executable_reason: Optional[str]
    unit_payoff: AtomPayoffProjector     # typed projector over scenario atoms (net, after-cost)
    max_units: Decimal                   # depth/holdings/reservation bound
    min_tick_size: Decimal
    min_order_size: Decimal


@dataclass(frozen=True)
class SolveMenu:
    """The full menu for one solve invocation (single family in W3; joint in C4+).

    Tick/min-size are PER ITEM now (consult REV-2), so the menu carries only identity +
    the deterministic ``menu_hash`` receipt anchor.
    """

    family_key: str
    items: tuple[MenuItem, ...]
    menu_hash: str                       # deterministic over items — receipt anchor


# --- plan --------------------------------------------------------------------

@dataclass(frozen=True)
class PlannedOrder:
    """One order (or venue primitive invocation) in the solved plan.

    ``q_version`` is MANDATORY (non-None) — a SOLVE output is by construction a
    decision-basis-bearing command (W1.2 stamp law). ``safe_prefix_index`` orders the plan
    into W2.1 safe prefixes: every prefix leaves acceptable exposure if later chunks fail.
    ``plan_generation`` / ``ledger_snapshot_id`` / ``invalidation_snapshot_id`` are the
    phase-2 INV-28/29 execution-envelope metadata (consult REV-2: fields now, wiring later —
    a partial fill discards unsubmitted children and re-solves from reconciled truth).
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
    plan_generation: int = 0
    ledger_snapshot_id: Optional[str] = None
    invalidation_snapshot_id: Optional[str] = None


@dataclass(frozen=True)
class RepairCertificate:
    """Proof that the continuous→discrete repair did NOT turn an improving plan worsening
    (consult REV-2 blocker). A non-empty ``SolutionPlan`` MUST carry one with
    ``repaired_objective > 0``; the re-evaluation under ``worst_price_model`` IS the proof.
    """

    continuous_objective: float
    repaired_objective: float            # robust ΔU of the ROUNDED plan under worst prices
    worst_price_model: str
    tick_size_deltas: Mapping[str, str]  # item_id -> "continuous_units->rounded_units"
    min_size_promoted: tuple[str, ...]   # item_ids promoted UP to their min_order_size
    dropped_items: tuple[tuple[str, str], ...]  # (item_id, reason) rounded/capped out
    batch_partition: tuple[tuple[str, ...], ...]  # ≤15-per-chunk order_id partition
    safe_prefix_objective_bounds: tuple[float, ...]  # robust ΔU of each growing safe prefix
    budget_after_repair_usd: float


@dataclass(frozen=True)
class SolutionPlan:
    """The SOLVE output: the full planned action set plus receipt provenance.

    The legacy seam (FamilyDecision) is DERIVED from this via solver.py's shim; this object
    is the truth the receipts, the batch executor (W2.1), and the evidence gate consume.
    A non-empty plan REQUIRES ``repair_certificate`` with ``repaired_objective > 0``.
    """

    plan_id: str
    family_key: str
    orders: tuple[PlannedOrder, ...]
    expected_delta_log_wealth: float     # robust (CVaR) ΔU of the WHOLE repaired plan
    delta_u_baseline_top1: Optional[float]  # best single order in the SAME feasible set —
                                            # the solver≥picker dominance hook (post-repair)
    kappa_applied: float
    correlation_rail: Literal["caps", "c4_scenarios", "caps_degraded_not_optimal"]
    scenario_provider: str
    scenario_sample_hash: str
    menu_hash: str
    q_version: str                       # family-level decision basis (all orders share)
    no_trade_reason: Optional[str]       # None iff orders non-empty
    repair_certificate: Optional[RepairCertificate] = None
    diagnostics: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.orders:
            if self.no_trade_reason is not None:
                raise ValueError("a plan with orders must not carry a no_trade_reason")
            cert = self.repair_certificate
            if cert is None or not cert.repaired_objective > 0.0:
                raise ValueError(
                    "non-empty SolutionPlan requires a RepairCertificate with repaired_objective > 0 "
                    "(consult REV-2 blocker: repair must PROVE the rounded plan still improves)"
                )
        elif self.no_trade_reason is None:
            raise ValueError("a no-trade plan must carry a no_trade_reason")


@dataclass(frozen=True)
class LegacyDecisionProjection:
    """Shim-side phase-1 evidence artifact (consult REV-2 blocker).

    Phase-1 execution submits ONLY the primary leg through the frozen seam, so promotion
    evidence must grade THIS projection — the primary leg re-scored STANDALONE at its
    post-downstream-haircut size — never ``SolutionPlan.expected_delta_log_wealth`` (which
    describes the unexecuted full plan). Phase-1 rule: ``standalone_primary_delta_u <= 0``
    (the leg is only good because of unexecuted hedges) → NO-TRADE in phase 1.
    """

    primary_order_id: Optional[str]
    projected_selected: Optional[str]    # menu_item_id of the primary leg
    standalone_primary_delta_u: float    # ΔU of the primary leg ALONE, post-haircut size
    projection_reason: str
    downstream_haircut_alive: bool
    submitted_size_after_haircut: Decimal

    @property
    def phase1_tradeable(self) -> bool:
        return self.primary_order_id is not None and self.standalone_primary_delta_u > 0.0
