# Created: 2026-07-03
# Last reused or audited: 2026-07-15
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
  so a convex-program solve can recover the global optimum — the legacy payoff_vector
  "quantile-of-concave is unimodal" assertion is unsafe and is NOT inherited.
  CVaR_α ≤ VaR_α, so this is also strictly more conservative than the served-band quantile.

* OPTIMIZER — the lower-CVaR Rockafellar–Uryasev convex program is the continuous authority.
  Deterministic cyclic coordinate ascent supplies a feasible warm start and the best-single-item
  dominance floor; it is not treated as a globality certificate. No RNG or wall clock enters.

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

import hashlib
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING, Any, Callable, Literal, Mapping, Optional, Sequence

import numpy as np
from scipy.optimize import (
    Bounds,
    LinearConstraint,
    NonlinearConstraint,
    minimize,
)

from src.contracts.executable_cost_curve import BookLevel, ExecutableCostCurve, FeeModel
from src.contracts.execution_intent import (
    POLYMARKET_MARKETABLE_BUY_MIN_NOTIONAL_USD,
    quantize_submit_shares_for_venue,
    quantize_submit_shares_for_venue_at_most,
    venue_submit_amount_precision_error,
)
from src.solve.exits import ZeroWealthOutcomeError
from src.solve.kappa import KappaPolicy
from src.solve.scenario_service import ScenarioService
from src.solve.types import (
    JointOutcomeScenarioSet,
    MenuItem,
    PlannedOrder,
    RepairCertificate,
    SolutionPlan,
    SolveMenu,
    WealthStateByAtom,
)

if TYPE_CHECKING:
    from src.decision.family_decision_engine import FamilyDecision

# Optimizer resolution — coarse-to-fine 1-D grid per coordinate (payoff_vector precedent).
_COARSE_STEPS = 200
_REFINE_STEPS = 64
_REFINE_PASSES = 3

# Coordinate-ascent convergence: the CVaR objective is CONCAVE, so a handful of sweeps over
# tens of items reaches the global optimum; stop when a full sweep gains < tol.
_CONVERGENCE_TOL = 1e-10
_MAX_SWEEPS = 12

# Strict interior margin so log() never sees a non-positive wealth.
_WEALTH_MARGIN = 1e-9

# Budget-face detection: run the (expensive) pairwise-exchange sweeps only when net spend is
# within this RELATIVE tolerance of spendable cash (so grid discretization of the last coordinate
# does not hide a binding budget); with real budget slack the single-coordinate optimum is global.
_BUDGET_BIND_REL = 1e-3

# Base share discretization. Immediate BUY feasibility is a price-dependent subset
# of this grid because the venue also constrains SDK maker/taker amount precision.
_SIZE_QUANTUM = Decimal("0.01")
_MAX_ORDERS = 15

_WORST_PRICE_MODEL = "avg_cost_size_aware_depth_capped_v1"

# CVaR tail stability (consult REV-2 follow-up): a robust ΔU at alpha needs enough draws in
# the alpha-tail to be meaningful. Below this the plan is STAMPED (diagnostics) so the promotion
# evidence gate can down-weight it; a one-draw band is stamped point_belief. Not a hard reject.
_MIN_TAIL_DRAWS = 20

# W3 live authority is memoryless: every native YES/NO leg is re-scored from the
# currently served joint-q band and the current executable cost curve.  This basis
# is carried through the existing receipt fields so downstream gates can distinguish
# it from settlement-fitted reliability/selection guards without inventing a second
# probability authority.
CURRENT_POSTERIOR_BAND_BASIS = "CURRENT_POSTERIOR_BAND"


class PayoffCoverageError(ValueError):
    """A menu item's AtomPayoffProjector does not cover the full scenario atom axis.

    Silently defaulting a missing atom's payoff to 0.0 turns an unmodelled LOSING state into
    free money (consult REV-2 follow-up). An item must cover every atom, or set
    ``AtomPayoffProjector.structural_zero=True`` to assert the zeros are intentional.
    """

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


class OptimizerConvergenceError(RuntimeError):
    """The certifying convex CVaR solve failed to dominate its feasible warm start."""


GlobalEligibilityReason = Literal[
    "DAY0_OBSERVATION_UNAVAILABLE",
    "PROBABILITY_AUTHORITY_MISSING",
    "PROBABILITY_AUTHORITY_SUPERSEDED",
    "PROBABILITY_AUTHORITY_EXPIRED",
    "JOINT_Q_MEMBERSHIP_MISMATCH",
    "Q_IDENTITY_SUPERSEDED",
    "Q_SAMPLE_CERTIFICATE_MISMATCH",
    "Q_SAMPLE_IDENTITY_SUPERSEDED",
    "BAND_ALPHA_MISMATCH",
    "BAND_TAIL_UNDERSAMPLED",
    "BOOK_IDENTITY_SUPERSEDED",
    "BOOK_CERTIFICATE_MISMATCH",
    "EXECUTION_AUTHORITY_MISSING",
    "EXECUTION_CURVE_SUPERSEDED",
    "QUOTE_EXPIRED",
    "SETTLEMENT_IDENTITY_SUPERSEDED",
    "CAPITAL_IDENTITY_SUPERSEDED",
    "COLLATERAL_UNKNOWN",
    "DEPTH_INFEASIBLE",
    "ROBUST_MAJORITY_LOSS",
    "FRACTIONAL_KELLY_TARGET_REACHED",
    "FRACTIONAL_KELLY_INCREMENT_BELOW_MINIMUM",
    "NON_POSITIVE_ROBUST_OBJECTIVE",
]


@dataclass(frozen=True)
class ExecutableSellCurve:
    """Fee-deducted native BID depth for selling one already-held claim."""

    token_id: str
    side: Literal["YES", "NO"]
    snapshot_id: str
    book_hash: str
    levels: tuple[BookLevel, ...]
    fee_model: FeeModel
    min_tick: Decimal
    min_order_size: Decimal
    quote_ttl: timedelta

    def __post_init__(self) -> None:
        if (
            self.side not in {"YES", "NO"}
            or not self.token_id
            or not self.snapshot_id
            or not self.book_hash
            or not self.levels
            or self.min_tick <= 0
            or self.min_order_size <= 0
            or self.quote_ttl <= timedelta(0)
        ):
            raise ValueError("executable sell curve is incomplete")
        for level in self.levels:
            ratio = level.price / self.min_tick
            if abs(ratio - ratio.to_integral_value()) > Decimal("1e-9"):
                raise ValueError("sell level is not aligned to the venue tick")
        object.__setattr__(
            self,
            "levels",
            tuple(sorted(self.levels, key=lambda level: level.price, reverse=True)),
        )

    def net_price(self, price: Decimal) -> Decimal:
        return price - self.fee_model.fee_per_share(price)

    def proceeds_for_shares(
        self, shares: Decimal
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Return net proceeds, gross VWAP, and the deepest executable bid."""

        remaining = Decimal(shares)
        if remaining <= 0:
            raise ValueError("sell shares must be positive")
        net = Decimal("0")
        gross = Decimal("0")
        limit = Decimal("0")
        for level in self.levels:
            take = min(remaining, level.size)
            if take <= 0:
                continue
            net += take * self.net_price(level.price)
            gross += take * level.price
            limit = level.price
            remaining -= take
            if remaining <= Decimal("1e-9"):
                break
        if remaining > Decimal("1e-9"):
            raise ValueError("sell depth cannot fill the exact holding")
        return net, gross / Decimal(shares), limit


def executable_curve_identity(
    curve: ExecutableCostCurve | ExecutableSellCurve,
) -> str:
    """Bind depth, fee, tick, token, and snapshot into one execution certificate."""

    digest = hashlib.sha256()
    if isinstance(curve, ExecutableSellCurve):
        digest.update(b"SELL\x1f")
    for value in (
        curve.token_id,
        curve.side,
        curve.snapshot_id,
        curve.book_hash,
        curve.fee_model.fee_rate,
        curve.min_tick,
        curve.min_order_size,
        curve.quote_ttl.total_seconds(),
    ):
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\x1f")
    for level in curve.levels:
        digest.update(str(level.price).encode("utf-8"))
        digest.update(b"\x1e")
        digest.update(str(level.size).encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def q_sample_identity(
    family_key: str,
    bin_id: str,
    q_version: str,
    resolution_identity: str,
    band_alpha: float,
    band_basis: str,
    yes_q_samples: np.ndarray,
) -> str:
    """Bind the canonical YES sample axis; NO is its pointwise complement."""

    q = np.ascontiguousarray(np.asarray(yes_q_samples, dtype=np.float64))
    digest = hashlib.sha256()
    for value in (
        family_key,
        bin_id,
        q_version,
        resolution_identity,
        repr(float(band_alpha)),
        band_basis,
        q.shape,
    ):
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\x1f")
    digest.update(q.astype("<f8", copy=False).tobytes(order="C"))
    return digest.hexdigest()


@dataclass(frozen=True)
class OutcomeTokenBinding:
    """One MECE probability column bound to its actual binary token pair."""

    bin_id: str
    condition_id: str
    yes_token_id: str | None
    no_token_id: str | None

    def __post_init__(self) -> None:
        if not self.bin_id.strip() or not self.condition_id.strip():
            raise ValueError("outcome binding requires bin and condition identities")
        if self.yes_token_id is not None and not str(self.yes_token_id).strip():
            raise ValueError("YES token identity must be non-empty when present")
        if self.no_token_id is not None and not str(self.no_token_id).strip():
            raise ValueError("NO token identity must be non-empty when present")
        if (
            self.yes_token_id is not None
            and self.no_token_id is not None
            and self.yes_token_id == self.no_token_id
        ):
            raise ValueError("YES and NO token identities must differ")


def outcome_token_binding_identity(
    *,
    family_key: str,
    bindings: Sequence[OutcomeTokenBinding],
    resolution_identity: str,
    topology_identity: str,
) -> str:
    """Bind the complete settlement-bin to condition/native-token topology."""

    if not family_key or not resolution_identity or not topology_identity or not bindings:
        raise ValueError("family binding identity requires complete authority inputs")
    return _hash(
        family_key,
        resolution_identity,
        topology_identity,
        *(
            f"{binding.bin_id}:{binding.condition_id}:"
            f"{binding.yes_token_id or ''}:{binding.no_token_id or ''}"
            for binding in bindings
        ),
    )


def probability_sample_matrix_identity(samples: np.ndarray) -> str:
    """Canonical identity of one ordered row-simplex probability draw matrix."""

    matrix = np.ascontiguousarray(np.asarray(samples, dtype=np.float64))
    if matrix.ndim != 2 or not np.isfinite(matrix).all():
        raise ValueError("probability sample matrix must be finite and two-dimensional")
    digest = hashlib.sha256()
    digest.update(repr(matrix.shape).encode("utf-8"))
    digest.update(matrix.astype("<f8", copy=False).tobytes(order="C"))
    return digest.hexdigest()


def joint_probability_witness_identity(
    *,
    family_key: str,
    bindings: Sequence[OutcomeTokenBinding],
    q_version: str,
    resolution_identity: str,
    topology_identity: str,
    posterior_identity_hash: str,
    source_truth_identity: str,
    authority_certificate_hash: str,
    band_alpha: float,
    band_basis: str,
    yes_q_samples: np.ndarray,
    captured_at_utc: datetime,
) -> str:
    """Bind one complete family-simplex probability authority.

    A candidate-local probability is only a projection.  The authority is the full
    mutually-exclusive/exhaustive family draw matrix plus the current source,
    settlement, topology, and decision-certificate identities that produced it.
    """

    if captured_at_utc.tzinfo is None:
        raise ValueError("captured_at_utc must be timezone-aware")
    samples = np.ascontiguousarray(np.asarray(yes_q_samples, dtype=np.float64))
    digest = hashlib.sha256()
    for value in (
        family_key,
        tuple(
            (b.bin_id, b.condition_id, b.yes_token_id, b.no_token_id)
            for b in bindings
        ),
        q_version,
        resolution_identity,
        topology_identity,
        posterior_identity_hash,
        source_truth_identity,
        authority_certificate_hash,
        repr(float(band_alpha)),
        band_basis,
        samples.shape,
        captured_at_utc.isoformat(),
    ):
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\x1f")
    digest.update(samples.astype("<f8", copy=False).tobytes(order="C"))
    return digest.hexdigest()


@dataclass(frozen=True)
class JointOutcomeProbabilityWitness:
    """Current zero-sum outcome authority for one complete market family.

    Every row is one coherent draw over the MECE settlement bins and therefore sums
    to one.  A YES candidate consumes one column; NO consumes its pointwise
    complement.  The full matrix, not a caller-supplied scalar q, is the authority.
    """

    family_key: str
    bindings: tuple[OutcomeTokenBinding, ...]
    yes_q_samples: np.ndarray
    q_version: str
    resolution_identity: str
    topology_identity: str
    posterior_identity_hash: str
    source_truth_identity: str
    authority_certificate_hash: str
    band_alpha: float
    band_basis: str
    captured_at_utc: datetime
    max_age: timedelta
    witness_identity: str

    @property
    def bin_ids(self) -> tuple[str, ...]:
        return tuple(binding.bin_id for binding in self.bindings)

    @property
    def family_binding_identity(self) -> str:
        return outcome_token_binding_identity(
            family_key=self.family_key,
            bindings=self.bindings,
            resolution_identity=self.resolution_identity,
            topology_identity=self.topology_identity,
        )

    @property
    def sample_matrix_identity(self) -> str:
        return probability_sample_matrix_identity(self.yes_q_samples)

    def __post_init__(self) -> None:
        samples = np.asarray(self.yes_q_samples, dtype=np.float64)
        if (
            samples.ndim != 2
            or samples.shape[0] < 2
            or samples.shape[1] != len(self.bindings)
            or len(set(self.bin_ids)) != len(self.bindings)
            or not self.bindings
            or not np.isfinite(samples).all()
            or np.any(samples < 0.0)
            or np.any(samples > 1.0)
            or not np.allclose(samples.sum(axis=1), 1.0, atol=1e-9)
        ):
            raise ValueError("probability witness must be a finite MECE row-simplex matrix")
        if not (0.0 < self.band_alpha < 0.5):
            raise ValueError("probability witness alpha must lie in (0, 0.5)")
        if self.band_alpha * samples.shape[0] < _MIN_TAIL_DRAWS:
            raise ValueError("probability witness has too few tail draws")
        if self.captured_at_utc.tzinfo is None or self.max_age <= timedelta(0):
            raise ValueError("probability witness freshness contract is invalid")
        if not all(
            str(value).strip()
            for value in (
                self.family_key,
                self.q_version,
                self.resolution_identity,
                self.topology_identity,
                self.posterior_identity_hash,
                self.source_truth_identity,
                self.authority_certificate_hash,
                self.band_basis,
            )
        ):
            raise ValueError("probability witness authority identities must be non-empty")
        expected = joint_probability_witness_identity(
            family_key=self.family_key,
            bindings=self.bindings,
            q_version=self.q_version,
            resolution_identity=self.resolution_identity,
            topology_identity=self.topology_identity,
            posterior_identity_hash=self.posterior_identity_hash,
            source_truth_identity=self.source_truth_identity,
            authority_certificate_hash=self.authority_certificate_hash,
            band_alpha=self.band_alpha,
            band_basis=self.band_basis,
            yes_q_samples=samples,
            captured_at_utc=self.captured_at_utc,
        )
        if self.witness_identity != expected:
            raise ValueError("probability witness identity does not bind its family simplex")
        object.__setattr__(self, "yes_q_samples", np.ascontiguousarray(samples))


@dataclass(frozen=True)
class CurrentFamilyProbabilityAuthority:
    """Independent resolver output for the family authority current at selection."""

    family_key: str
    witness_identity: str
    q_version: str
    resolution_identity: str
    topology_identity: str
    posterior_identity_hash: str
    source_truth_identity: str
    authority_certificate_hash: str
    band_alpha: float
    band_basis: str

    @classmethod
    def from_witness(
        cls, witness: JointOutcomeProbabilityWitness
    ) -> "CurrentFamilyProbabilityAuthority":
        return cls(
            family_key=witness.family_key,
            witness_identity=witness.witness_identity,
            q_version=witness.q_version,
            resolution_identity=witness.resolution_identity,
            topology_identity=witness.topology_identity,
            posterior_identity_hash=witness.posterior_identity_hash,
            source_truth_identity=witness.source_truth_identity,
            authority_certificate_hash=witness.authority_certificate_hash,
            band_alpha=witness.band_alpha,
            band_basis=witness.band_basis,
        )


@dataclass(frozen=True)
class CurrentExecutionAuthority:
    """Independent JIT book resolver output used to refute stale prepared curves."""

    token_id: str
    side: Literal["YES", "NO"]
    book_snapshot_id: str
    execution_curve_identity: str
    action: Literal["BUY", "SELL"] = "BUY"


def global_auction_universe_identity(
    *,
    family_bindings: Sequence[tuple[str, str]],
    venue_universe_identity: str,
    captured_at_utc: datetime,
) -> str:
    if captured_at_utc.tzinfo is None:
        raise ValueError("captured_at_utc must be timezone-aware")
    normalized = tuple(
        sorted(
            (str(family_key), str(binding_identity))
            for family_key, binding_identity in family_bindings
        )
    )
    return _hash(
        *(f"{family_key}:{binding_identity}" for family_key, binding_identity in normalized),
        venue_universe_identity,
        captured_at_utc.isoformat(),
    )


@dataclass(frozen=True)
class GlobalAuctionUniverseWitness:
    """Current active-family/token binding that makes the word global auditable."""

    family_bindings: tuple[tuple[str, str], ...]
    venue_universe_identity: str
    captured_at_utc: datetime
    max_age: timedelta
    witness_identity: str

    def __post_init__(self) -> None:
        family_bindings = tuple(
            sorted(
                (str(family_key), str(binding_identity))
                for family_key, binding_identity in self.family_bindings
            )
        )
        keys = tuple(family_key for family_key, _ in family_bindings)
        if (
            not family_bindings
            or len(set(keys)) != len(keys)
            or not all(
                family_key and binding_identity
                for family_key, binding_identity in family_bindings
            )
        ):
            raise ValueError(
                "global auction universe must contain unique family/token bindings"
            )
        if not self.venue_universe_identity:
            raise ValueError("global auction universe requires venue identity")
        if self.captured_at_utc.tzinfo is None or self.max_age <= timedelta(0):
            raise ValueError("global auction universe freshness contract is invalid")
        expected = global_auction_universe_identity(
            family_bindings=family_bindings,
            venue_universe_identity=self.venue_universe_identity,
            captured_at_utc=self.captured_at_utc,
        )
        if self.witness_identity != expected:
            raise ValueError(
                "global auction universe identity does not bind its family/token topology"
            )
        object.__setattr__(self, "family_bindings", family_bindings)

    @property
    def family_keys(self) -> tuple[str, ...]:
        return tuple(family_key for family_key, _ in self.family_bindings)

    @property
    def binding_by_family(self) -> Mapping[str, str]:
        return dict(self.family_bindings)


def portfolio_wealth_identity(
    *,
    ledger_snapshot_id: str,
    position_set_hash: str,
    wealth_floor_usd: Decimal,
    wealth_ceiling_usd: Decimal,
    spendable_cash_usd: Decimal,
    reservations_usd: Decimal,
    collateral_authority: str,
    captured_at_utc: datetime,
) -> str:
    """Bind every capital number to one reconciled ledger/position generation."""

    if captured_at_utc.tzinfo is None:
        raise ValueError("captured_at_utc must be timezone-aware")
    return _hash(
        ledger_snapshot_id,
        position_set_hash,
        str(wealth_floor_usd),
        str(wealth_ceiling_usd),
        str(spendable_cash_usd),
        str(reservations_usd),
        collateral_authority,
        captured_at_utc.isoformat(),
    )


def portfolio_wealth_economic_identity(
    *,
    position_set_hash: str,
    wealth_floor_usd: Decimal,
    wealth_ceiling_usd: Decimal,
    spendable_cash_usd: Decimal,
    reservations_usd: Decimal,
    collateral_authority: str,
) -> str:
    """Bind the economic endowment independently of evidence refresh time.

    ``witness_identity`` remains the immutable certificate for one exact ledger
    observation.  This identity answers the narrower actuation question: did the
    cash, inventory, reservations, or authority used by the optimizer change?
    A heartbeat that proves the same balances more recently must not make a
    long-running full-universe auction impossible to actuate.
    """

    return _hash(
        position_set_hash,
        str(wealth_floor_usd),
        str(wealth_ceiling_usd),
        str(spendable_cash_usd),
        str(reservations_usd),
        collateral_authority,
    )


@dataclass(frozen=True)
class PortfolioWealthWitness:
    """Current capital truth used by every candidate in one auction epoch."""

    ledger_snapshot_id: str
    position_set_hash: str
    wealth_floor_usd: Decimal
    wealth_ceiling_usd: Decimal
    spendable_cash_usd: Decimal
    reservations_usd: Decimal
    collateral_authority: str
    captured_at_utc: datetime
    max_age: timedelta
    witness_identity: str

    @property
    def economic_identity(self) -> str:
        return portfolio_wealth_economic_identity(
            position_set_hash=self.position_set_hash,
            wealth_floor_usd=self.wealth_floor_usd,
            wealth_ceiling_usd=self.wealth_ceiling_usd,
            spendable_cash_usd=self.spendable_cash_usd,
            reservations_usd=self.reservations_usd,
            collateral_authority=self.collateral_authority,
        )

    def __post_init__(self) -> None:
        if self.captured_at_utc.tzinfo is None:
            raise ValueError("PortfolioWealthWitness.captured_at_utc must be timezone-aware")
        if self.max_age <= timedelta(0):
            raise ValueError("PortfolioWealthWitness.max_age must be positive")
        if (
            self.wealth_floor_usd <= 0
            or self.wealth_ceiling_usd < self.wealth_floor_usd
            or self.spendable_cash_usd < 0
            or self.reservations_usd < 0
        ):
            raise ValueError("portfolio wealth, cash, and reservations must be valid")
        expected = portfolio_wealth_identity(
            ledger_snapshot_id=self.ledger_snapshot_id,
            position_set_hash=self.position_set_hash,
            wealth_floor_usd=self.wealth_floor_usd,
            wealth_ceiling_usd=self.wealth_ceiling_usd,
            spendable_cash_usd=self.spendable_cash_usd,
            reservations_usd=self.reservations_usd,
            collateral_authority=self.collateral_authority,
            captured_at_utc=self.captured_at_utc,
        )
        if self.witness_identity != expected:
            raise ValueError("PortfolioWealthWitness identity does not bind its values")


@dataclass(frozen=True)
class CandidatePortfolioEndowment:
    """Ledger-aligned branch wealth before one additional native BUY.

    The loss branch is a lower bound and the win branch is an upper bound, so
    their use in incremental log utility remains conservative.  Current shares
    name the already-owned exposure to this exact native token; Fractional Kelly
    uses them to constrain the final holding across repeated auction epochs.
    """

    loss_wealth_floor_usd: Decimal
    win_wealth_ceiling_usd: Decimal
    current_token_shares: Decimal
    ledger_snapshot_id: str

    def __post_init__(self) -> None:
        loss = Decimal(self.loss_wealth_floor_usd)
        win = Decimal(self.win_wealth_ceiling_usd)
        shares = Decimal(self.current_token_shares)
        if (
            not self.ledger_snapshot_id.strip()
            or not all(value.is_finite() for value in (loss, win, shares))
            or loss <= 0
            or win <= 0
            or shares < 0
        ):
            raise ValueError("candidate portfolio endowment is invalid")


@dataclass(frozen=True)
class GlobalSingleOrderCandidate:
    """One current, native-side order hypothesis in the cross-family auction.

    It carries no probability scalar.  The selector derives q from the verified full
    family simplex after proving this exact condition/token membership.  The executable
    curve is the candidate's own side-native ask ladder, including fees.
    """

    candidate_id: str
    family_key: str
    bin_id: str
    condition_id: str
    side: Literal["YES", "NO"]
    token_id: str
    probability_witness_identity: str
    book_snapshot_id: str
    book_captured_at_utc: datetime
    execution_curve_identity: str
    ledger_snapshot_id: str
    executable_cost_curve: ExecutableCostCurve
    resolution_identity: str
    execution_mode: Literal["TAKER_LIMIT"] = "TAKER_LIMIT"
    eligibility_reason: GlobalEligibilityReason | None = None

    def __post_init__(self) -> None:
        if self.side not in {"YES", "NO"}:
            raise ValueError(f"unsupported native side: {self.side!r}")
        if not all(
            str(value).strip()
            for value in (
                self.candidate_id,
                self.family_key,
                self.bin_id,
                self.condition_id,
                self.token_id,
                self.probability_witness_identity,
                self.resolution_identity,
            )
        ):
            raise ValueError("global order candidate identities must be non-empty")
        if self.executable_cost_curve.side != self.side:
            raise ValueError("candidate side must match its own native executable cost curve")
        if self.book_captured_at_utc.tzinfo is None:
            raise ValueError("book_captured_at_utc must be timezone-aware")
        curve_identity = executable_curve_identity(self.executable_cost_curve)
        if (
            self.token_id != self.executable_cost_curve.token_id
            or self.book_snapshot_id != self.executable_cost_curve.snapshot_id
            or self.execution_curve_identity != curve_identity
        ):
            object.__setattr__(self, "eligibility_reason", "BOOK_CERTIFICATE_MISMATCH")
        if self.execution_mode != "TAKER_LIMIT":
            raise ValueError("global single-order candidates must be immediate taker-limit assets")


def global_candidate_from_native(
    native: Any,
    *,
    probability_witness: JointOutcomeProbabilityWitness,
    ledger_snapshot_id: str,
    book_captured_at_utc: datetime,
    eligibility_reason: GlobalEligibilityReason | None = None,
) -> GlobalSingleOrderCandidate:
    """Materialize one order only after proving q-column/token membership."""

    if getattr(native, "no_trade_reason", None) is not None:
        raise ValueError("native no-trade candidate is not globally executable")
    curve = getattr(native, "executable_cost_curve", None)
    if curve is None:
        raise ValueError("global candidate requires a full native executable curve")
    try:
        column = probability_witness.bin_ids.index(str(native.bin_id))
    except ValueError as exc:
        raise ValueError("native bin is absent from the family probability witness") from exc
    binding = probability_witness.bindings[column]
    expected_token = (
        binding.yes_token_id if native.side == "YES" else binding.no_token_id
    )
    if (
        not expected_token
        or str(native.family_key) != probability_witness.family_key
        or str(native.condition_id) != binding.condition_id
        or str(native.token_id) != expected_token
        or curve.token_id != expected_token
        or curve.side != native.side
    ):
        raise ValueError("native condition/token does not own the selected q column")
    return GlobalSingleOrderCandidate(
        candidate_id=_hash(
            probability_witness.family_key,
            str(native.hypothesis_id),
            binding.bin_id,
            binding.condition_id,
            str(native.side),
            str(expected_token),
        ),
        family_key=probability_witness.family_key,
        bin_id=binding.bin_id,
        condition_id=binding.condition_id,
        side=native.side,
        token_id=expected_token,
        probability_witness_identity=probability_witness.witness_identity,
        book_snapshot_id=curve.snapshot_id,
        book_captured_at_utc=book_captured_at_utc,
        execution_curve_identity=executable_curve_identity(curve),
        ledger_snapshot_id=str(ledger_snapshot_id),
        executable_cost_curve=curve,
        resolution_identity=probability_witness.resolution_identity,
        eligibility_reason=eligibility_reason,
    )


@dataclass(frozen=True)
class GlobalSingleOrderSellCandidate:
    """The venue-legal reducible part of one exact ledger holding."""

    candidate_id: str
    family_key: str
    bin_id: str
    condition_id: str
    side: Literal["YES", "NO"]
    token_id: str
    position_id: str
    held_shares: Decimal
    probability_witness_identity: str
    book_snapshot_id: str
    book_captured_at_utc: datetime
    execution_curve_identity: str
    ledger_snapshot_id: str
    executable_sell_curve: ExecutableSellCurve
    resolution_identity: str
    action: Literal["SELL"] = "SELL"
    eligibility_reason: GlobalEligibilityReason | None = None

    def __post_init__(self) -> None:
        if self.side not in {"YES", "NO"}:
            raise ValueError(f"unsupported native side: {self.side!r}")
        if not all(
            str(value).strip()
            for value in (
                self.candidate_id,
                self.family_key,
                self.bin_id,
                self.condition_id,
                self.token_id,
                self.position_id,
                self.probability_witness_identity,
                self.ledger_snapshot_id,
                self.resolution_identity,
            )
        ):
            raise ValueError("global sell candidate identities must be non-empty")
        if (
            not Decimal(self.held_shares).is_finite()
            or Decimal(self.held_shares) <= 0
            or Decimal(self.held_shares) % Decimal("0.01") != 0
        ):
            raise ValueError("global sell requires exact venue-legal centishares")
        curve = self.executable_sell_curve
        if curve.side != self.side or curve.token_id != self.token_id:
            raise ValueError("sell candidate must use its held token's native bid curve")
        if self.book_captured_at_utc.tzinfo is None:
            raise ValueError("book_captured_at_utc must be timezone-aware")
        if (
            self.book_snapshot_id != curve.snapshot_id
            or self.execution_curve_identity != executable_curve_identity(curve)
        ):
            object.__setattr__(self, "eligibility_reason", "BOOK_CERTIFICATE_MISMATCH")


def global_sell_candidate_from_holding(
    holding: Any,
    *,
    probability_witness: JointOutcomeProbabilityWitness,
    ledger_snapshot_id: str,
    executable_sell_curve: ExecutableSellCurve,
    book_captured_at_utc: datetime,
) -> GlobalSingleOrderSellCandidate | None:
    """Materialize the venue-legal reducible part of an exact ledger holding."""

    try:
        column = probability_witness.bin_ids.index(str(holding.bin_id))
    except ValueError as exc:
        raise ValueError("holding bin is absent from the family probability witness") from exc
    binding = probability_witness.bindings[column]
    side = str(holding.side)
    expected_token = binding.yes_token_id if side == "YES" else binding.no_token_id
    if (
        side not in {"YES", "NO"}
        or str(holding.family_key) != probability_witness.family_key
        or not expected_token
        or str(holding.token_id) != expected_token
        or executable_sell_curve.token_id != expected_token
        or executable_sell_curve.side != side
    ):
        raise ValueError("holding condition/token does not own the selected q column")
    ledger_shares = Decimal(holding.shares)
    sellable_shares = ledger_shares.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    if sellable_shares <= 0:
        return None
    return GlobalSingleOrderSellCandidate(
        candidate_id=_hash(
            "SELL",
            probability_witness.family_key,
            str(holding.position_id),
            binding.bin_id,
            binding.condition_id,
            side,
            str(expected_token),
            str(ledger_shares),
            str(sellable_shares),
        ),
        family_key=probability_witness.family_key,
        bin_id=binding.bin_id,
        condition_id=binding.condition_id,
        side=side,  # type: ignore[arg-type]
        token_id=str(expected_token),
        position_id=str(holding.position_id),
        held_shares=sellable_shares,
        probability_witness_identity=probability_witness.witness_identity,
        book_snapshot_id=executable_sell_curve.snapshot_id,
        book_captured_at_utc=book_captured_at_utc,
        execution_curve_identity=executable_curve_identity(executable_sell_curve),
        ledger_snapshot_id=str(ledger_snapshot_id),
        executable_sell_curve=executable_sell_curve,
        resolution_identity=probability_witness.resolution_identity,
    )


GlobalSingleOrderAnyCandidate = (
    GlobalSingleOrderCandidate | GlobalSingleOrderSellCandidate
)


@dataclass(frozen=True)
class BinaryTerminalWealthCertificate:
    """Exact binary payoff branches plus conservative branch probabilities."""

    win_probability_lcb: float
    loss_probability_ucb: float
    loss_payoff_usd: Decimal
    win_payoff_usd: Decimal
    median_payoff_usd: Decimal
    wealth_after_loss_usd: Decimal
    wealth_after_win_usd: Decimal
    expected_value_diagnostic_usd: float

    def __post_init__(self) -> None:
        if self.win_probability_lcb > 0.5:
            median_coherent = self.median_payoff_usd == self.win_payoff_usd
        elif self.win_probability_lcb < 0.5:
            median_coherent = self.median_payoff_usd == self.loss_payoff_usd
        else:
            median_coherent = (
                self.loss_payoff_usd
                <= self.median_payoff_usd
                <= self.win_payoff_usd
            )
        if (
            not math.isfinite(self.win_probability_lcb)
            or not math.isfinite(self.loss_probability_ucb)
            or not math.isclose(
                self.win_probability_lcb + self.loss_probability_ucb,
                1.0,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or not 0.0 <= self.win_probability_lcb <= 1.0
            or not 0.0 <= self.loss_probability_ucb <= 1.0
            or self.loss_payoff_usd >= 0
            or self.win_payoff_usd <= 0
            or not median_coherent
            or self.wealth_after_loss_usd <= 0
            or self.wealth_after_win_usd <= 0
            or not math.isfinite(self.expected_value_diagnostic_usd)
        ):
            raise ValueError("terminal-wealth certificate is not branch coherent")


@dataclass(frozen=True)
class GlobalSingleOrderCandidateEvaluation:
    """One candidate's complete result inside the current global auction."""

    candidate_id: str
    family_key: str
    bin_id: str
    condition_id: str
    side: Literal["YES", "NO"]
    token_id: str
    action: Literal["BUY", "SELL"]
    status: Literal["REJECTED", "SCORED", "SELECTED"]
    position_id: str | None = None
    held_shares: Decimal = Decimal("0")
    rejection_reason: str | None = None
    shares: Decimal = Decimal("0")
    cost_usd: Decimal = Decimal("0")
    cash_proceeds_usd: Decimal = Decimal("0")
    robust_delta_log_wealth: float = 0.0
    robust_ev_usd: float = 0.0
    capital_efficiency: float = 0.0
    limit_price: Decimal = Decimal("0")
    expected_fill_price_before_fee: Decimal = Decimal("0")
    max_spend_usd: Decimal = Decimal("0")
    current_token_shares: Decimal = Decimal("0")
    full_kelly_target_shares: Decimal = Decimal("0")
    fractional_kelly_target_shares: Decimal = Decimal("0")
    terminal_wealth: BinaryTerminalWealthCertificate | None = None

    def __post_init__(self) -> None:
        if (
            not all(
                str(value).strip()
                for value in (
                    self.candidate_id,
                    self.family_key,
                    self.bin_id,
                    self.condition_id,
                    self.token_id,
                )
            )
            or self.side not in {"YES", "NO"}
            or self.action not in {"BUY", "SELL"}
        ):
            raise ValueError("global candidate evaluation identity is incomplete")
        if self.action == "BUY" and (
            self.position_id is not None or self.held_shares != 0
        ):
            raise ValueError("BUY evaluation cannot carry a held-position binding")
        if self.action == "SELL" and (
            not str(self.position_id or "").strip()
            or not Decimal(self.held_shares).is_finite()
            or Decimal(self.held_shares) <= 0
            or Decimal(self.held_shares) % Decimal("0.01") != 0
        ):
            raise ValueError("SELL evaluation requires an exact held-position binding")
        if self.status == "REJECTED":
            reason = str(self.rejection_reason or "").strip()
            carries_economics = any(
                (
                    self.shares != 0,
                    self.cost_usd != 0,
                    self.cash_proceeds_usd != 0,
                    self.limit_price != 0,
                    self.expected_fill_price_before_fee != 0,
                    self.terminal_wealth is not None,
                )
            )
            if not reason:
                raise ValueError("rejected candidate evaluation cannot carry economics")
            if not carries_economics:
                if (
                    self.robust_delta_log_wealth != 0.0
                    or self.robust_ev_usd != 0.0
                    or self.capital_efficiency != 0.0
                    or self.max_spend_usd != 0
                    or self.current_token_shares != 0
                    or self.full_kelly_target_shares != 0
                    or self.fractional_kelly_target_shares != 0
                ):
                    raise ValueError(
                        "rejected candidate evaluation cannot carry partial economics"
                    )
                return
            terminal = self.terminal_wealth
            if (
                self.action != "SELL"
                or reason
                not in {
                    "NON_POSITIVE_ROBUST_OBJECTIVE",
                    "NON_POSITIVE_ROBUST_FILL_PREFIX",
                }
                or self.shares <= 0
                or self.shares > self.held_shares
                or self.cost_usd <= 0
                or self.cash_proceeds_usd <= 0
                or self.cash_proceeds_usd != self.shares - self.cost_usd
                or not math.isfinite(self.robust_delta_log_wealth)
                or not math.isfinite(self.robust_ev_usd)
                or not math.isfinite(self.capital_efficiency)
                or self.limit_price <= 0
                or self.expected_fill_price_before_fee < self.limit_price
                or self.max_spend_usd != 0
                or self.current_token_shares != 0
                or self.full_kelly_target_shares != 0
                or self.fractional_kelly_target_shares != 0
                or terminal is None
                or terminal.loss_payoff_usd != -self.cost_usd
                or terminal.win_payoff_usd != self.cash_proceeds_usd
                or not math.isclose(
                    terminal.expected_value_diagnostic_usd,
                    self.robust_ev_usd,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
                or (
                    reason == "NON_POSITIVE_ROBUST_OBJECTIVE"
                    and self.robust_delta_log_wealth > 0.0
                    and self.robust_ev_usd > 0.0
                )
                or (
                    reason == "NON_POSITIVE_ROBUST_FILL_PREFIX"
                    and not (
                        self.robust_delta_log_wealth > 0.0
                        and self.robust_ev_usd > 0.0
                    )
                )
            ):
                raise ValueError(
                    "rejected SELL evaluation lacks coherent counterfactual economics"
                )
            return
        if (
            self.status not in {"SCORED", "SELECTED"}
            or self.rejection_reason is not None
            or self.shares <= 0
            or self.cost_usd <= 0
            or self.robust_delta_log_wealth <= 0.0
            or self.robust_ev_usd <= 0.0
            or self.capital_efficiency <= 0.0
            or self.limit_price <= 0
            or self.expected_fill_price_before_fee <= 0
            or self.terminal_wealth is None
        ):
            raise ValueError("scored candidate evaluation lacks positive economics")
        if self.action == "BUY" and (
            self.current_token_shares < 0
            or self.full_kelly_target_shares <= 0
            or self.fractional_kelly_target_shares <= self.current_token_shares
            or self.fractional_kelly_target_shares
            > self.full_kelly_target_shares
            or self.shares
            > self.fractional_kelly_target_shares - self.current_token_shares
        ):
            raise ValueError("BUY evaluation is not cumulative fractional-Kelly coherent")
        if self.action == "SELL" and (
            self.current_token_shares != 0
            or self.full_kelly_target_shares != 0
            or self.fractional_kelly_target_shares != 0
            or self.shares > self.held_shares
        ):
            raise ValueError(
                "SELL evaluation must reduce no more than its bound holding"
            )


@dataclass(frozen=True)
class GlobalSingleOrderDecision:
    """The one order that wins the current cross-family feasible-set auction."""

    candidate: GlobalSingleOrderAnyCandidate | None
    shares: Decimal
    cost_usd: Decimal
    robust_delta_log_wealth: float
    robust_ev_usd: float
    capital_efficiency: float
    no_trade_reason: str | None
    limit_price: Decimal = Decimal("0")
    expected_fill_price_before_fee: Decimal = Decimal("0")
    max_spend_usd: Decimal = Decimal("0")
    cash_proceeds_usd: Decimal = Decimal("0")
    current_token_shares: Decimal = Decimal("0")
    full_kelly_target_shares: Decimal = Decimal("0")
    fractional_kelly_target_shares: Decimal = Decimal("0")
    terminal_wealth: BinaryTerminalWealthCertificate | None = None
    rejection_reasons: Mapping[str, str] = field(default_factory=dict)
    candidate_evaluations: tuple[GlobalSingleOrderCandidateEvaluation, ...] = ()
    candidate_input_count: int | None = None

    def __post_init__(self) -> None:
        if self.candidate_input_count is not None and (
            self.candidate_input_count < 0
            or self.candidate_input_count != len(self.candidate_evaluations)
        ):
            raise ValueError("global candidate input/evaluation coverage disagrees")
        if self.candidate_evaluations:
            candidate_ids = tuple(
                evaluation.candidate_id for evaluation in self.candidate_evaluations
            )
            selected = tuple(
                evaluation
                for evaluation in self.candidate_evaluations
                if evaluation.status == "SELECTED"
            )
            if len(candidate_ids) != len(set(candidate_ids)) or len(selected) != (
                1 if self.candidate is not None else 0
            ):
                raise ValueError("global candidate evaluations are not one-to-one")
            if self.candidate is not None:
                winner = selected[0]
                if (
                    winner.candidate_id != self.candidate.candidate_id
                    or winner.shares != self.shares
                    or winner.cost_usd != self.cost_usd
                    or winner.cash_proceeds_usd != self.cash_proceeds_usd
                    or winner.robust_delta_log_wealth
                    != self.robust_delta_log_wealth
                    or winner.robust_ev_usd != self.robust_ev_usd
                    or winner.capital_efficiency != self.capital_efficiency
                    or winner.current_token_shares
                    != self.current_token_shares
                    or winner.full_kelly_target_shares
                    != self.full_kelly_target_shares
                    or winner.fractional_kelly_target_shares
                    != self.fractional_kelly_target_shares
                ):
                    raise ValueError("selected candidate evaluation disagrees with decision")
        if self.candidate is None:
            if self.no_trade_reason is None:
                raise ValueError("global no-trade decision requires a reason")
            if self.shares != 0 or self.cost_usd != 0:
                raise ValueError("global no-trade decision cannot allocate capital")
            if (
                self.limit_price != 0
                or self.expected_fill_price_before_fee != 0
                or self.max_spend_usd != 0
                or self.cash_proceeds_usd != 0
                or self.current_token_shares != 0
                or self.full_kelly_target_shares != 0
                or self.fractional_kelly_target_shares != 0
                or self.terminal_wealth is not None
            ):
                raise ValueError("global no-trade decision cannot carry an execution boundary")
            return
        if getattr(self.candidate, "action", "BUY") == "SELL":
            if (
                self.no_trade_reason is not None
                or self.shares <= 0
                or self.shares > self.candidate.held_shares
                or self.cost_usd <= 0
                or self.cash_proceeds_usd <= 0
                or self.cash_proceeds_usd != self.shares - self.cost_usd
                or self.limit_price <= 0
                or self.expected_fill_price_before_fee < self.limit_price
                or self.max_spend_usd != 0
                or self.current_token_shares != 0
                or self.full_kelly_target_shares != 0
                or self.fractional_kelly_target_shares != 0
                or self.terminal_wealth is None
                or self.terminal_wealth.loss_payoff_usd != -self.cost_usd
                or self.terminal_wealth.win_payoff_usd != self.cash_proceeds_usd
                or not math.isclose(
                    self.terminal_wealth.expected_value_diagnostic_usd,
                    self.robust_ev_usd,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            ):
                raise ValueError("global sell decision is not held-position coherent")
            return
        if (
            self.no_trade_reason is not None
            or self.shares <= 0
            or self.cost_usd <= 0
            or self.limit_price <= 0
            or self.expected_fill_price_before_fee <= 0
            or self.expected_fill_price_before_fee > self.limit_price
            or self.max_spend_usd < self.cost_usd
            or self.cash_proceeds_usd != 0
            or self.current_token_shares < 0
            or self.full_kelly_target_shares <= 0
            or self.fractional_kelly_target_shares <= self.current_token_shares
            or self.fractional_kelly_target_shares
            > self.full_kelly_target_shares
            or self.shares
            > self.fractional_kelly_target_shares - self.current_token_shares
            or self.terminal_wealth is None
            or self.terminal_wealth.loss_payoff_usd != -self.cost_usd
            or self.terminal_wealth.win_payoff_usd != self.shares - self.cost_usd
            or not math.isclose(
                self.terminal_wealth.expected_value_diagnostic_usd,
                self.robust_ev_usd,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ):
            raise ValueError(
                "global trade decision requires positive shares/cost/limit and sufficient max spend"
            )


def _global_candidate_evaluations(
    candidates: Sequence[GlobalSingleOrderAnyCandidate],
    *,
    rejections: Mapping[str, str],
    scores: Sequence[GlobalSingleOrderDecision] = (),
    winner_id: str | None = None,
    default_rejection: str | None = None,
) -> tuple[GlobalSingleOrderCandidateEvaluation, ...]:
    """Retain every candidate's eligibility/economic result for one epoch."""

    scored_by_id = {
        score.candidate.candidate_id: score
        for score in scores
        if score.candidate is not None
    }
    evaluations: list[GlobalSingleOrderCandidateEvaluation] = []
    for candidate in candidates:
        is_sell = isinstance(candidate, GlobalSingleOrderSellCandidate)
        action: Literal["BUY", "SELL"] = "SELL" if is_sell else "BUY"
        position_id = candidate.position_id if is_sell else None
        held_shares = candidate.held_shares if is_sell else Decimal("0")
        score = scored_by_id.get(candidate.candidate_id)
        if score is None:
            reason = rejections.get(candidate.candidate_id) or default_rejection
            if reason is None:
                raise ValueError(
                    f"global candidate result missing: {candidate.candidate_id}"
                )
            evaluations.append(
                GlobalSingleOrderCandidateEvaluation(
                    candidate_id=candidate.candidate_id,
                    family_key=candidate.family_key,
                    bin_id=candidate.bin_id,
                    condition_id=candidate.condition_id,
                    side=candidate.side,
                    token_id=candidate.token_id,
                    action=action,
                    status="REJECTED",
                    position_id=position_id,
                    held_shares=held_shares,
                    rejection_reason=reason,
                )
            )
            continue
        rejection_reason = rejections.get(candidate.candidate_id)
        evaluations.append(
            GlobalSingleOrderCandidateEvaluation(
                candidate_id=candidate.candidate_id,
                family_key=candidate.family_key,
                bin_id=candidate.bin_id,
                condition_id=candidate.condition_id,
                side=candidate.side,
                token_id=candidate.token_id,
                action=action,
                status=(
                    "SELECTED"
                    if candidate.candidate_id == winner_id
                    else "REJECTED"
                    if rejection_reason is not None
                    else "SCORED"
                ),
                position_id=position_id,
                held_shares=held_shares,
                rejection_reason=rejection_reason,
                shares=score.shares,
                cost_usd=score.cost_usd,
                cash_proceeds_usd=score.cash_proceeds_usd,
                robust_delta_log_wealth=score.robust_delta_log_wealth,
                robust_ev_usd=score.robust_ev_usd,
                capital_efficiency=score.capital_efficiency,
                limit_price=score.limit_price,
                expected_fill_price_before_fee=(
                    score.expected_fill_price_before_fee
                ),
                max_spend_usd=score.max_spend_usd,
                current_token_shares=score.current_token_shares,
                full_kelly_target_shares=score.full_kelly_target_shares,
                fractional_kelly_target_shares=(
                    score.fractional_kelly_target_shares
                ),
                terminal_wealth=score.terminal_wealth,
            )
        )
    return tuple(evaluations)


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


# ---------------------------------------------------------------------------
# Robust objective + optimizer internals (importable by the property tests).
# ---------------------------------------------------------------------------

def _lower_cvar(du: np.ndarray, weights: np.ndarray, alpha: float) -> float:
    """Lower-tail CVaR at ``alpha`` — the (weighted) mean of the worst ``alpha`` fraction.

    CONCAVE-PRESERVING (consult REV-2): each per-draw ``du_k`` is concave in the stake vector,
    and the lower-tail CVaR of concave functions is concave, which licenses the certifying convex
    solve. This replaces the raw α-quantile (VaR), whose order statistic of concave functions is
    not concave. ``-inf`` draws (a ruined atom carries positive mass) propagate to ``-inf``
    correctly.

    Zero/negative weights are FILTERED before the sort (consult REV-2 follow-up): a zero-weight
    row would be ``0 * -inf = NaN`` in the tail sum if it were a ruin draw; a weight of exactly
    zero carries no belief mass and must not contribute.
    """
    keep = weights > 0.0
    if not keep.all():
        du = du[keep]
        weights = weights[keep]
    if du.size == 0:
        return float("-inf")
    order = np.argsort(du, kind="stable")
    d = du[order]
    w = weights[order]
    total = float(w.sum())
    target = alpha * total
    if target <= 0.0:
        return float(d[0])
    cumw = np.cumsum(w)
    idx = int(np.searchsorted(cumw, target, side="left"))
    idx = min(idx, len(d) - 1)
    full_sum = float((w[:idx] * d[:idx]).sum()) if idx > 0 else 0.0
    w_before = float(cumw[idx - 1]) if idx > 0 else 0.0
    frac = target - w_before
    boundary = frac * float(d[idx]) if frac > 0.0 else 0.0
    return (full_sum + boundary) / target


def _executable_items(menu: SolveMenu) -> list:
    """The stakeable menu items: executable, positive depth, with a payoff projector."""
    return [
        it
        for it in menu.items
        if it.executable and Decimal(it.max_units) > 0 and it.unit_payoff.payoff_by_atom_id
    ]


def _build_arrays(
    menu: SolveMenu, wealth: WealthStateByAtom, atom_ids: tuple[str, ...]
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list]:
    """Baseline wealth ``W0``, unit-payoff matrix ``P`` (n_items × n_atoms), depth caps, costs.

    Validates that every atom has a strictly positive endowment (else ``ZeroWealthOutcomeError``),
    that the wealth state covers the scenario atom axis, and that every executable item's payoff
    projector covers the full atom axis (else ``PayoffCoverageError`` — a missing atom silently
    defaulting to 0.0 would turn an unmodelled losing state into free money).
    """
    missing = [a for a in atom_ids if a not in wealth.wealth_by_atom]
    if missing:
        raise ZeroWealthOutcomeError(
            f"WealthStateByAtom missing atoms {missing} present in the scenario axis"
        )
    w0 = wealth.vector(atom_ids)
    nonpos = [atom_ids[a] for a in range(len(atom_ids)) if not w0[a] > 0.0]
    if nonpos:
        raise ZeroWealthOutcomeError(
            f"non-positive endowment wealth in atoms {nonpos} — log-utility undefined"
        )
    items = _executable_items(menu)
    payoff = np.zeros((len(items), len(atom_ids)), dtype=np.float64)
    caps = np.zeros(len(items), dtype=np.float64)
    costs = np.zeros(len(items), dtype=np.float64)
    for i, it in enumerate(items):
        if not it.unit_payoff.covers(atom_ids):
            raise PayoffCoverageError(
                f"menu item {it.item_id!r} payoff projector does not cover all atoms "
                f"{atom_ids}; set AtomPayoffProjector.structural_zero=True to intend zeros"
            )
        payoff[i] = it.unit_payoff.vector(atom_ids)
        caps[i] = float(it.max_units)
        costs[i] = float(it.unit_payoff.unit_cost_usd)
    return w0, payoff, caps, costs, items


def _objective(
    x: np.ndarray,
    w0: np.ndarray,
    payoff: np.ndarray,
    q_draws: np.ndarray,
    weights: np.ndarray,
    alpha: float,
) -> float:
    """Robust plan ΔU: lower-tail CVaR_α across draws of expected Δlog-wealth over atoms."""
    w_end = w0 + x @ payoff
    pos = w_end > 0.0
    if pos.all():
        g = np.log(w_end) - np.log(w0)
        du = q_draws @ g
    else:
        g = np.zeros_like(w0)
        g[pos] = np.log(w_end[pos]) - np.log(w0[pos])
        du = q_draws @ g
        bad = (q_draws[:, ~pos] > 0.0).any(axis=1)
        if bad.any():
            du = np.where(bad, -np.inf, du)
    return _lower_cvar(du, weights, alpha)


def _single_order_cost(curve: ExecutableCostCurve, shares: Decimal) -> Decimal:
    """Exact all-in spend for ``shares`` on the side-native ask ladder."""

    remaining = Decimal(shares)
    if remaining <= 0 or remaining < curve.min_order_size:
        raise ValueError("share size is below the executable minimum")
    cost = Decimal("0")
    for level in curve.levels:
        take = min(remaining, level.size)
        if take > 0:
            cost += take * curve.fee_model.all_in_price(level.price)
            remaining -= take
        if remaining <= Decimal("1e-18"):
            return cost
    raise ValueError("share size exceeds executable depth")


def _single_order_max_shares_by_cost(
    curve: ExecutableCostCurve,
    *,
    cost_limit_usd: Decimal,
) -> Decimal:
    """Largest share-grid size whose depth-walked loss fits ``cost_limit_usd``."""

    remaining = Decimal(cost_limit_usd)
    if remaining <= 0:
        return Decimal("0")
    shares = Decimal("0")
    fee_model = curve.fee_model
    for level in curve.levels:
        unit_cost = fee_model.all_in_price(level.price)
        take = min(level.size, remaining / unit_cost)
        if take > 0:
            shares += take
            remaining -= take * unit_cost
        if take < level.size:
            break
    return (
        shares / _SIZE_QUANTUM
    ).to_integral_value(rounding=ROUND_FLOOR) * _SIZE_QUANTUM


def _single_order_max_shares(
    curve: ExecutableCostCurve,
    *,
    spend_limit_usd: Decimal,
) -> Decimal:
    """Largest venue-grid size whose worst admitted limit fill fits cash.

    The current-book VWAP is the expected spend, but the executable request is a
    limit order. Collateral must therefore cover every requested share at the
    deepest admitted level. This makes the mathematical optimum fundable by the
    exact command that will represent it.
    """

    spend_limit = Decimal(spend_limit_usd)
    cumulative = Decimal("0")
    shares = Decimal("0")
    for level in curve.levels:
        prior_cumulative = cumulative
        price = curve.fee_model.all_in_price(level.price)
        cumulative += level.size
        affordable_at_limit = spend_limit / price
        if affordable_at_limit < prior_cumulative:
            break
        shares = min(cumulative, affordable_at_limit)
        if shares < cumulative:
            break
    return (shares / _SIZE_QUANTUM).to_integral_value(rounding=ROUND_FLOOR) * _SIZE_QUANTUM


def _single_order_min_marketable_shares(
    curve: ExecutableCostCurve,
) -> Decimal | None:
    """Smallest share-grid size satisfying both venue minimums.

    The venue share floor and the marketable BUY notional floor are separate
    constraints.  The submitted notional uses the deepest raw limit price, so
    scan the monotone ask ladder instead of dividing by one assumed price.
    """

    level_start = Decimal("0")
    for level in curve.levels:
        level_end = level_start + level.size
        required = max(
            curve.min_order_size,
            POLYMARKET_MARKETABLE_BUY_MIN_NOTIONAL_USD / level.price,
            level_start,
        )
        required = (
            required / _SIZE_QUANTUM
        ).to_integral_value(rounding=ROUND_CEILING) * _SIZE_QUANTUM
        if level_start > 0 and required <= level_start:
            required += _SIZE_QUANTUM
        if required <= level_end:
            return required
        level_start = level_end
    return None


def _single_order_execution_boundary(
    candidate: GlobalSingleOrderCandidate,
    shares: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """Return raw limit, raw VWAP, and fee-aware max spend for exact shares."""

    remaining = Decimal(shares)
    if remaining <= 0:
        raise ValueError("single-order execution boundary requires positive shares")
    limit_price: Decimal | None = None
    raw_cost = Decimal("0")
    for level in candidate.executable_cost_curve.levels:
        take = min(level.size, remaining)
        if take > 0:
            limit_price = level.price
            raw_cost += take * level.price
            remaining -= take
        if remaining <= Decimal("1e-18"):
            break
    if remaining > Decimal("1e-18") or limit_price is None:
        raise ValueError("single-order execution boundary exceeds executable depth")
    all_in_limit = candidate.executable_cost_curve.fee_model.all_in_price(limit_price)
    return limit_price, raw_cost / Decimal(shares), Decimal(shares) * all_in_limit


def _single_order_venue_legal_neighbor(
    candidate: GlobalSingleOrderCandidate,
    shares: Decimal,
    *,
    at_most: bool,
) -> Decimal | None:
    """Nearest venue-legal FOK BUY size on one side of ``shares``.

    Venue legality depends on the deepest consumed price, while changing size can
    change that price. Iterate the monotone normalization to a fixed point instead
    of treating the 0.01-share base grid as the complete executable set.
    """

    current = Decimal(shares)
    direction = "buy_yes" if candidate.side == "YES" else "buy_no"
    # Each normalization is monotone and can cross a ladder boundary only once;
    # one final pass proves stability at the last reached boundary.
    for _ in range(len(candidate.executable_cost_curve.levels) + 2):
        try:
            limit_price, _, _ = _single_order_execution_boundary(candidate, current)
            tick = candidate.executable_cost_curve.min_tick
            price_decimals = abs(tick.normalize().as_tuple().exponent)
            scale = 10 ** price_decimals
            price_units = int(round(float(limit_price) * scale))
            legal_step = scale // math.gcd(abs(price_units), scale)
            raw_units = int(
                (current / _SIZE_QUANTUM).to_integral_value(
                    rounding=ROUND_FLOOR if at_most else ROUND_CEILING
                )
            )
            anchor = raw_units // legal_step
            unit_candidates = {
                multiple * legal_step + offset
                for multiple in range(max(0, anchor - 2), anchor + 4)
                for offset in range(-2, 3)
                if multiple * legal_step + offset > 0
            }
            bounded = sorted(
                (
                    Decimal(units) * _SIZE_QUANTUM
                    for units in unit_candidates
                    if (
                        Decimal(units) * _SIZE_QUANTUM <= current
                        if at_most
                        else Decimal(units) * _SIZE_QUANTUM >= current
                    )
                ),
                reverse=at_most,
            )
            normalized = next(
                (
                    candidate_shares
                    for candidate_shares in bounded
                    if venue_submit_amount_precision_error(
                        direction=direction,
                        final_limit_price=limit_price,
                        submitted_shares=candidate_shares,
                        order_type="FOK",
                        tick_size=tick,
                    )
                    is None
                ),
                None,
            )
            if normalized is None:
                # Preserve the canonical SDK-faithful contract as a correctness
                # fallback for any future tick/rounding shape the modular bound
                # does not cover.
                quantize = (
                    quantize_submit_shares_for_venue_at_most
                    if at_most
                    else quantize_submit_shares_for_venue
                )
                normalized = quantize(
                    direction,
                    current,
                    final_limit_price=limit_price,
                    order_type="FOK",
                    tick_size=tick,
                )
        except ValueError:
            return None
        if normalized == current:
            return current
        if (at_most and normalized > current) or (not at_most and normalized < current):
            raise AssertionError("venue share normalization moved in the wrong direction")
        current = normalized
    raise AssertionError("venue share normalization did not converge")


def _single_order_metrics(
    candidate: GlobalSingleOrderCandidate,
    *,
    q_samples: np.ndarray,
    shares: Decimal,
    wealth_floor_usd: Decimal,
    wealth_ceiling_usd: Decimal,
    alpha: float,
    robust_q: float | None = None,
) -> tuple[float, float, float, Decimal]:
    """Return robust Δlog, robust EV, Δlog/cost, and exact cost.

    The contract has only two settlement payoffs.  Expected ROI is not the
    objective; capital efficiency is the conservative terminal-wealth growth
    purchased per dollar of current capital.
    """

    cost = _single_order_cost(candidate.executable_cost_curve, shares)
    floor = float(wealth_floor_usd)
    ceiling = float(wealth_ceiling_usd)
    lose_wealth = floor - float(cost)
    win_wealth = ceiling - float(cost) + float(shares)
    if lose_wealth <= 0.0 or win_wealth <= 0.0:
        return float("-inf"), float("-inf"), float("-inf"), cost
    if robust_q is None:
        q = np.asarray(q_samples, dtype=np.float64)
        robust_q = _lower_cvar(q, np.ones(q.size, dtype=np.float64), alpha)
    # Coupling-robust endowment bound: wins use the portfolio ceiling and losses
    # use the floor. Both outcome returns are positive-slope affine transforms of
    # q; lower-CVaR is translation-equivariant and positive-homogeneous, so one
    # tail reduction of q exactly serves every stake probe for this candidate.
    lose_du = math.log(lose_wealth / floor)
    win_du = math.log(win_wealth / ceiling)
    robust_du = lose_du + float(robust_q) * (win_du - lose_du)
    robust_ev = float(robust_q) * float(shares) - float(cost)
    efficiency = robust_du / float(cost) if cost > 0 else float("-inf")
    return float(robust_du), float(robust_ev), float(efficiency), cost


def _binary_terminal_wealth_certificate(
    *,
    robust_q: float,
    shares: Decimal,
    cost_usd: Decimal,
    wealth_floor_usd: Decimal,
    wealth_ceiling_usd: Decimal,
) -> BinaryTerminalWealthCertificate:
    """Certify the only two settlement branches without renaming EV as profit."""

    loss_payoff = -Decimal(cost_usd)
    win_payoff = Decimal(shares) - Decimal(cost_usd)
    return BinaryTerminalWealthCertificate(
        win_probability_lcb=float(robust_q),
        loss_probability_ucb=float(1.0 - robust_q),
        loss_payoff_usd=loss_payoff,
        win_payoff_usd=win_payoff,
        median_payoff_usd=(win_payoff if robust_q > 0.5 else loss_payoff),
        wealth_after_loss_usd=Decimal(wealth_floor_usd) + loss_payoff,
        wealth_after_win_usd=Decimal(wealth_ceiling_usd) + win_payoff,
        expected_value_diagnostic_usd=(
            float(robust_q) * float(shares) - float(cost_usd)
        ),
    )


def _single_order_stationary_probes(
    curve: ExecutableCostCurve,
    *,
    robust_q: Decimal,
    wealth_floor_usd: Decimal,
    wealth_ceiling_usd: Decimal,
    min_shares: Decimal,
    max_shares: Decimal,
) -> set[Decimal]:
    """Return every continuous optimum candidate on a piecewise-linear ladder.

    For positive shares the win-vs-lose log-return gap is positive, so lower-CVaR
    of the affine-in-q objective is the same objective evaluated at lower-CVaR(q).
    On one ladder level ``cost(s) = p*s + d``; the resulting binary log-wealth
    objective is concave and has at most one stationary point.  Therefore the
    global continuous optimum is among those stationary points and ladder/capital
    boundaries.  Venue-grid neighbors are applied by the caller.
    """

    one = Decimal("1")
    probes = {Decimal(min_shares), Decimal(max_shares)}
    level_start = Decimal("0")
    cost_start = Decimal("0")
    for level in curve.levels:
        price = curve.fee_model.all_in_price(level.price)
        level_end = level_start + level.size
        segment_lo = max(level_start, min_shares)
        segment_hi = min(level_end, max_shares)
        if segment_lo <= segment_hi:
            probes.update((segment_lo, segment_hi))
            denominator = price * (one - price)
            if denominator != 0:
                cost_intercept = cost_start - price * level_start
                stationary = (
                    robust_q
                    * (one - price)
                    * (wealth_floor_usd - cost_intercept)
                    - (one - robust_q)
                    * price
                    * (wealth_ceiling_usd - cost_intercept)
                ) / denominator
                if segment_lo <= stationary <= segment_hi:
                    probes.add(stationary)
        if level_end >= max_shares:
            break
        cost_start += level.size * price
        level_start = level_end
    return probes


def _score_global_single_order(
    candidate: GlobalSingleOrderCandidate,
    *,
    q_samples: np.ndarray,
    band_alpha: float,
    wealth_floor_usd: Decimal,
    wealth_ceiling_usd: Decimal,
    spendable_cash_usd: Decimal,
    capital_limit_usd: Decimal,
    fractional_kelly_multiplier: Decimal = Decimal("1"),
    payoff_q_lcb: float | None = None,
    current_token_shares: Decimal = Decimal("0"),
) -> GlobalSingleOrderDecision:
    """Find the executable fractional-Kelly optimum for one candidate.

    The current book and terminal-wealth objective identify the additional shares
    that reach the full-Kelly final holding from the reconciled current holding.
    The operator-owned multiplier applies to that FINAL holding, not independently
    to every auction epoch.  Only the remaining shares below the cumulative target
    are executable; a venue minimum may not promote a smaller target into a trade.
    Cash and allocator capacity remain independent hard outer bounds.
    """

    multiplier = Decimal(fractional_kelly_multiplier)
    held_shares = Decimal(current_token_shares)
    if not multiplier.is_finite() or not Decimal("0") < multiplier <= Decimal("1"):
        raise ValueError("fractional Kelly multiplier must be finite and in (0, 1]")
    if not held_shares.is_finite() or held_shares < 0:
        raise ValueError("current token shares must be finite and non-negative")
    affordability_limit = min(
        Decimal(spendable_cash_usd),
        Decimal(wealth_floor_usd) * (Decimal("1") - Decimal(str(_WEALTH_MARGIN))),
    )
    spend_limit = min(Decimal(capital_limit_usd), affordability_limit)
    capacity_max_shares = _single_order_max_shares(
        candidate.executable_cost_curve,
        spend_limit_usd=spend_limit,
    )
    optimization_limit = (
        spend_limit if multiplier == Decimal("1") else affordability_limit
    )
    raw_max_shares = _single_order_max_shares(
        candidate.executable_cost_curve,
        spend_limit_usd=optimization_limit,
    )
    raw_min_shares = _single_order_min_marketable_shares(
        candidate.executable_cost_curve
    )
    if (
        raw_min_shares is None
        or raw_max_shares < raw_min_shares
        or capacity_max_shares < raw_min_shares
    ):
        return GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason="DEPTH_INFEASIBLE",
            rejection_reasons={candidate.candidate_id: "DEPTH_INFEASIBLE"},
        )

    q = np.asarray(q_samples, dtype=np.float64)
    robust_q = _lower_cvar(q, np.ones(q.size, dtype=np.float64), band_alpha)
    if payoff_q_lcb is not None:
        if not math.isfinite(payoff_q_lcb) or not 0.0 <= payoff_q_lcb <= 1.0:
            raise ValueError("candidate payoff q lower bound must be finite in [0, 1]")
        robust_q = min(robust_q, payoff_q_lcb)
    raw_probes = _single_order_stationary_probes(
        candidate.executable_cost_curve,
        robust_q=Decimal(str(robust_q)),
        wealth_floor_usd=wealth_floor_usd,
        wealth_ceiling_usd=wealth_ceiling_usd,
        min_shares=raw_min_shares,
        max_shares=raw_max_shares,
    )

    probes: set[Decimal] = set()
    for raw_probe in raw_probes:
        for at_most in (True, False):
            legal = _single_order_venue_legal_neighbor(
                candidate, raw_probe, at_most=at_most
            )
            if legal is not None:
                probes.add(legal)

    full_best: tuple[
        float,
        float,
        float,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
    ] | None = None
    for shares in sorted(probes):
        if shares < raw_min_shares or shares > raw_max_shares:
            continue
        try:
            robust_du, robust_ev, efficiency, cost = _single_order_metrics(
                candidate,
                q_samples=q_samples,
                shares=shares,
                wealth_floor_usd=wealth_floor_usd,
                wealth_ceiling_usd=wealth_ceiling_usd,
                alpha=band_alpha,
                robust_q=robust_q,
            )
            limit_price, expected_fill_price, max_spend = _single_order_execution_boundary(
                candidate, shares
            )
        except ValueError:
            continue
        if max_spend > optimization_limit:
            continue
        if full_best is None or robust_du > full_best[0] + 1e-15 or (
            math.isclose(robust_du, full_best[0], rel_tol=0.0, abs_tol=1e-15)
            and (cost, -efficiency, candidate.candidate_id)
            < (full_best[3], -full_best[2], candidate.candidate_id)
        ):
            full_best = (
                robust_du,
                robust_ev,
                efficiency,
                cost,
                shares,
                limit_price,
                expected_fill_price,
                max_spend,
            )

    if full_best is None:
        return GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason="NON_POSITIVE_ROBUST_OBJECTIVE",
            rejection_reasons={candidate.candidate_id: "NON_POSITIVE_ROBUST_OBJECTIVE"},
        )

    legal_min_shares = _single_order_venue_legal_neighbor(
        candidate, raw_min_shares, at_most=False
    )
    if legal_min_shares is None:
        legal_min_shares = raw_min_shares
    full_kelly_target_shares = held_shares + full_best[4]
    fractional_kelly_target_shares = full_kelly_target_shares * multiplier
    remaining_target_shares = fractional_kelly_target_shares - held_shares
    if remaining_target_shares <= 0:
        reason = "FRACTIONAL_KELLY_TARGET_REACHED"
        return GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason=reason,
            rejection_reasons={candidate.candidate_id: reason},
        )
    fractional_legal_max = _single_order_venue_legal_neighbor(
        candidate,
        remaining_target_shares,
        at_most=True,
    )
    if fractional_legal_max is None or fractional_legal_max < legal_min_shares:
        reason = "FRACTIONAL_KELLY_INCREMENT_BELOW_MINIMUM"
        return GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason=reason,
            rejection_reasons={candidate.candidate_id: reason},
        )
    fractional_max_shares = min(
        capacity_max_shares,
        fractional_legal_max,
    )
    if fractional_max_shares < legal_min_shares:
        projected_probes: set[Decimal] = set()
    else:
        fractional_raw_probes = _single_order_stationary_probes(
            candidate.executable_cost_curve,
            robust_q=Decimal(str(robust_q)),
            wealth_floor_usd=wealth_floor_usd,
            wealth_ceiling_usd=wealth_ceiling_usd,
            min_shares=legal_min_shares,
            max_shares=fractional_max_shares,
        )
        projected_probes = set()
        for raw_probe in fractional_raw_probes:
            for at_most in (True, False):
                legal = _single_order_venue_legal_neighbor(
                    candidate, raw_probe, at_most=at_most
                )
                if legal is not None:
                    projected_probes.add(legal)

    best = None
    for shares in sorted(projected_probes):
        if shares < legal_min_shares or shares > fractional_max_shares:
            continue
        try:
            robust_du, robust_ev, efficiency, cost = _single_order_metrics(
                candidate,
                q_samples=q_samples,
                shares=shares,
                wealth_floor_usd=wealth_floor_usd,
                wealth_ceiling_usd=wealth_ceiling_usd,
                alpha=band_alpha,
                robust_q=robust_q,
            )
            limit_price, expected_fill_price, max_spend = _single_order_execution_boundary(
                candidate, shares
            )
        except ValueError:
            continue
        if max_spend > spend_limit:
            continue
        if best is None or robust_du > best[0] + 1e-15 or (
            math.isclose(robust_du, best[0], rel_tol=0.0, abs_tol=1e-15)
            and (cost, -efficiency, candidate.candidate_id)
            < (best[3], -best[2], candidate.candidate_id)
        ):
            best = (
                robust_du,
                robust_ev,
                efficiency,
                cost,
                shares,
                limit_price,
                expected_fill_price,
                max_spend,
            )

    if best is None or not (best[0] > 0.0 and best[1] > 0.0):
        return GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason="NON_POSITIVE_ROBUST_OBJECTIVE",
            rejection_reasons={candidate.candidate_id: "NON_POSITIVE_ROBUST_OBJECTIVE"},
        )
    (
        robust_du,
        robust_ev,
        efficiency,
        cost,
        shares,
        limit_price,
        expected_fill_price,
        max_spend,
    ) = best
    return GlobalSingleOrderDecision(
        candidate=candidate,
        shares=shares,
        cost_usd=cost,
        robust_delta_log_wealth=robust_du,
        robust_ev_usd=robust_ev,
        capital_efficiency=efficiency,
        no_trade_reason=None,
        limit_price=limit_price,
        expected_fill_price_before_fee=expected_fill_price,
        max_spend_usd=max_spend,
        current_token_shares=held_shares,
        full_kelly_target_shares=full_kelly_target_shares,
        fractional_kelly_target_shares=fractional_kelly_target_shares,
        terminal_wealth=_binary_terminal_wealth_certificate(
            robust_q=robust_q,
            shares=shares,
            cost_usd=cost,
            wealth_floor_usd=wealth_floor_usd,
            wealth_ceiling_usd=wealth_ceiling_usd,
        ),
    )


def global_sell_fill_prefix_objective(
    decision: GlobalSingleOrderDecision,
    *,
    filled_shares: Decimal,
    net_proceeds_usd: Decimal,
) -> tuple[float, float]:
    """Score any FAK SELL prefix against continuing to hold those claims."""

    candidate = decision.candidate
    terminal = decision.terminal_wealth
    shares = Decimal(filled_shares)
    proceeds = Decimal(net_proceeds_usd)
    if (
        candidate is None
        or getattr(candidate, "action", "BUY") != "SELL"
        or terminal is None
        or shares <= 0
        or shares > decision.shares
        or proceeds <= 0
        or proceeds >= shares
    ):
        raise ValueError("sell fill prefix is not certificate-coherent")
    loss_baseline = terminal.wealth_after_loss_usd - terminal.loss_payoff_usd
    win_baseline = terminal.wealth_after_win_usd - terminal.win_payoff_usd
    loss_after = loss_baseline - shares + proceeds
    win_after = win_baseline + proceeds
    if min(loss_baseline, win_baseline, loss_after, win_after) <= 0:
        return float("-inf"), float("-inf")
    robust_du = terminal.loss_probability_ucb * math.log(
        float(loss_after / loss_baseline)
    ) + terminal.win_probability_lcb * math.log(float(win_after / win_baseline))
    robust_ev = terminal.win_probability_lcb * float(shares) - float(
        shares - proceeds
    )
    return robust_du, robust_ev


def global_buy_fak_prefix_certificate(
    decision: GlobalSingleOrderDecision,
    *,
    execution_curve_identity: str | None = None,
) -> dict[str, object]:
    """Prove every non-zero FAK fill up to the selected BUY size is beneficial.

    Every admitted fill has price no worse than the limit.  A positive rounded
    five-decimal fee is at most twice its unrounded value; this bound is
    independent of maker-fragment count and share quantum.  Price and fee shape
    are evaluated jointly: for an admitted fee rate at most 50%,
    ``p + 2*f*p*(1-p)`` is monotone through the binary price domain, so the
    executable limit is the coherent worst unit cost.  Binary expected log
    wealth is concave in filled shares and is zero at no fill, so a positive
    full-size endpoint proves every interior prefix positive as well.  EV is
    linear and uses the same endpoint proof.
    """

    candidate = decision.candidate
    terminal = getattr(decision, "terminal_wealth", None)
    if (
        candidate is None
        or getattr(candidate, "action", "BUY") != "BUY"
        or terminal is None
        or decision.shares <= 0
        or not (Decimal("0") < decision.limit_price < Decimal("1"))
    ):
        raise ValueError("buy FAK prefix decision is not certificate-coherent")
    curve = getattr(candidate, "executable_cost_curve", None)
    if curve is None or getattr(curve, "fee_model", None) is None:
        raise ValueError("buy FAK prefix curve is missing")

    fee_rate = Decimal(curve.fee_model.fee_rate)
    limit = Decimal(decision.limit_price)
    shares = Decimal(decision.shares)
    if (
        not fee_rate.is_finite()
        or fee_rate < Decimal("0")
        or fee_rate > Decimal("0.5")
    ):
        raise ValueError("buy FAK prefix fee rate is outside the monotone joint bound")
    max_fee_shape = limit * (Decimal("1") - limit)
    worst_fee_per_share = Decimal("2") * fee_rate * max_fee_shape
    unit_cost = limit + worst_fee_per_share
    full_cost = unit_cost * shares
    win_q = Decimal(str(terminal.win_probability_lcb))
    loss_q = Decimal(str(terminal.loss_probability_ucb))
    loss_baseline = terminal.wealth_after_loss_usd - terminal.loss_payoff_usd
    win_baseline = terminal.wealth_after_win_usd - terminal.win_payoff_usd
    loss_after = loss_baseline - full_cost
    win_after = win_baseline - full_cost + shares
    if (
        not all(
            value.is_finite()
            for value in (
                fee_rate,
                unit_cost,
                full_cost,
                win_q,
                loss_q,
                loss_baseline,
                win_baseline,
                loss_after,
                win_after,
            )
        )
        or not math.isclose(float(win_q + loss_q), 1.0, rel_tol=0.0, abs_tol=1e-12)
        or min(loss_baseline, win_baseline, loss_after, win_after) <= 0
    ):
        raise ValueError("buy FAK prefix wealth bound is invalid")
    robust_du = float(loss_q) * math.log(float(loss_after / loss_baseline)) + float(
        win_q
    ) * math.log(float(win_after / win_baseline))
    robust_ev = float(win_q * shares - full_cost)
    if not math.isfinite(robust_du) or robust_du <= 0 or robust_ev <= 0:
        raise ValueError("buy FAK full-size worst-limit prefix is non-positive")
    return {
        "global_buy_fak_prefix_semantics": (
            "CONCAVE_WORST_LIMIT_ALL_NONZERO_PREFIXES_POSITIVE"
        ),
        "global_buy_fak_fee_rate_source": "CURRENT_EXECUTABLE_CURVE",
        "global_buy_fak_execution_curve_identity": str(
            execution_curve_identity or candidate.execution_curve_identity
        ),
        "global_buy_fak_fee_rate": str(fee_rate),
        "global_buy_fak_fee_rounding_bound": (
            "ROUNDED_FEE_AT_MOST_TWO_X_UNROUNDED"
        ),
        "global_buy_fak_worst_fee_shape": str(max_fee_shape),
        "global_buy_fak_worst_fee_per_share": str(worst_fee_per_share),
        "global_buy_fak_worst_unit_cost": str(unit_cost),
        "global_buy_fak_full_worst_cost_usd": str(full_cost),
        "global_buy_fak_full_robust_delta_log_wealth": robust_du,
        "global_buy_fak_full_robust_ev_usd": robust_ev,
    }


def _score_global_single_order_sell(
    candidate: GlobalSingleOrderSellCandidate,
    *,
    held_payoff_q_samples: np.ndarray,
    band_alpha: float,
    wealth_floor_usd: Decimal,
    wealth_ceiling_usd: Decimal,
) -> GlobalSingleOrderDecision:
    """Select the venue-legal SELL size maximizing hold-relative log wealth."""

    held_shares = Decimal(candidate.held_shares)
    curve = candidate.executable_sell_curve
    quantum = Decimal("0.01")
    min_shares = (
        Decimal(curve.min_order_size) / quantum
    ).to_integral_value(rounding=ROUND_CEILING) * quantum
    max_shares = min(
        held_shares,
        sum((Decimal(level.size) for level in curve.levels), Decimal("0")),
    )
    max_shares = (
        max_shares / quantum
    ).to_integral_value(rounding=ROUND_FLOOR) * quantum
    if max_shares < min_shares:
        return GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason="DEPTH_INFEASIBLE",
            rejection_reasons={candidate.candidate_id: "DEPTH_INFEASIBLE"},
        )
    held_q = np.asarray(held_payoff_q_samples, dtype=np.float64)
    favorable_q_samples = 1.0 - held_q
    robust_q = _lower_cvar(
        favorable_q_samples,
        np.ones(favorable_q_samples.size, dtype=np.float64),
        band_alpha,
    )

    floor = Decimal(wealth_floor_usd)
    ceiling = Decimal(wealth_ceiling_usd)
    loss_baseline = floor + held_shares
    win_baseline = ceiling

    # Net proceeds are piecewise linear.  On each bid level the log objective
    # is concave, so its only possible maximum is a level boundary or the one
    # stationary point.  Probe the adjacent venue-cent sizes around each exact
    # point; this is the complete discrete feasible set, not a size heuristic.
    probes = {min_shares, max_shares}
    prefix_shares = Decimal("0")
    prefix_proceeds = Decimal("0")
    robust_q_decimal = Decimal(str(robust_q))
    for level in curve.levels:
        level_end = min(max_shares, prefix_shares + Decimal(level.size))
        if level_end < min_shares:
            prefix_proceeds += Decimal(level.size) * curve.net_price(level.price)
            prefix_shares += Decimal(level.size)
            continue
        net_price = curve.net_price(level.price)
        intercept = prefix_proceeds - net_price * prefix_shares
        denominator = net_price * (Decimal("1") - net_price)
        if denominator > 0:
            numerator = (
                robust_q_decimal * net_price * (loss_baseline + intercept)
                + (Decimal("1") - robust_q_decimal)
                * (net_price - Decimal("1"))
                * (win_baseline + intercept)
            )
            stationary = numerator / denominator
            if prefix_shares <= stationary <= level_end:
                probes.add(stationary)
        probes.add(prefix_shares)
        probes.add(level_end)
        take = max(Decimal("0"), level_end - prefix_shares)
        prefix_proceeds += take * net_price
        prefix_shares = level_end
        if prefix_shares >= max_shares:
            break

    venue_probes: set[Decimal] = set()
    for probe in probes:
        floor_probe = (
            probe / quantum
        ).to_integral_value(rounding=ROUND_FLOOR) * quantum
        ceil_probe = (
            probe / quantum
        ).to_integral_value(rounding=ROUND_CEILING) * quantum
        for sized in (
            floor_probe - quantum,
            floor_probe,
            ceil_probe,
            ceil_probe + quantum,
        ):
            if min_shares <= sized <= max_shares:
                venue_probes.add(sized)

    best: tuple[
        float,
        float,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
    ] | None = None
    for shares in sorted(venue_probes):
        proceeds, expected_fill_price, limit_price = curve.proceeds_for_shares(shares)
        loss_at_risk = shares - proceeds
        if proceeds <= 0 or loss_at_risk <= 0:
            raise ValueError(
                "sell proceeds must define a positive bounded hold-relative loss"
            )
        loss_after = loss_baseline - shares + proceeds
        win_after = win_baseline + proceeds
        if min(loss_baseline, loss_after, win_baseline, win_after) <= 0:
            robust_du = float("-inf")
        else:
            loss_du = math.log(float(loss_after / loss_baseline))
            win_du = math.log(float(win_after / win_baseline))
            robust_du = loss_du + robust_q * (win_du - loss_du)
        robust_ev = float(proceeds) - (1.0 - robust_q) * float(shares)
        efficiency = robust_du / float(loss_at_risk)
        scored_point = (
            robust_du,
            efficiency,
            -loss_at_risk,
            shares,
            proceeds,
            expected_fill_price,
            limit_price,
            loss_at_risk,
        )
        if best is None or scored_point[:3] > best[:3]:
            best = scored_point

    if best is None:
        raise ValueError("sell optimizer produced no venue-legal size")
    (
        robust_du,
        efficiency,
        _negative_loss_at_risk,
        shares,
        proceeds,
        expected_fill_price,
        limit_price,
        loss_at_risk,
    ) = best
    loss_after = loss_baseline - shares + proceeds
    win_after = win_baseline + proceeds
    robust_ev = float(proceeds) - (1.0 - robust_q) * float(shares)
    terminal = BinaryTerminalWealthCertificate(
        win_probability_lcb=float(robust_q),
        loss_probability_ucb=float(1.0 - robust_q),
        loss_payoff_usd=-loss_at_risk,
        win_payoff_usd=proceeds,
        median_payoff_usd=(
            proceeds if robust_q > 0.5 else -loss_at_risk
        ),
        wealth_after_loss_usd=loss_after,
        wealth_after_win_usd=win_after,
        expected_value_diagnostic_usd=float(robust_ev),
    )
    scored = GlobalSingleOrderDecision(
        candidate=candidate,
        shares=shares,
        cost_usd=loss_at_risk,
        robust_delta_log_wealth=float(robust_du),
        robust_ev_usd=float(robust_ev),
        capital_efficiency=float(efficiency),
        no_trade_reason=None,
        limit_price=limit_price,
        expected_fill_price_before_fee=expected_fill_price,
        max_spend_usd=Decimal("0"),
        cash_proceeds_usd=proceeds,
        terminal_wealth=terminal,
    )
    if not (robust_du > 0.0 and robust_ev > 0.0):
        return replace(
            scored,
            rejection_reasons={
                candidate.candidate_id: "NON_POSITIVE_ROBUST_OBJECTIVE"
            },
        )
    # FAK may stop at any point on the consumed BID prefix.  Within a level the
    # robust log objective is concave; positive values at every level boundary
    # (including the exact full size) prove every intermediate prefix remains
    # strictly better than CASH. Earlier bids are never worse than later bids.
    filled = Decimal("0")
    prefix_proceeds = Decimal("0")
    remaining = shares
    for level in curve.levels:
        take = min(remaining, level.size)
        if take <= 0:
            continue
        filled += take
        prefix_proceeds += take * curve.net_price(level.price)
        prefix_du, prefix_ev = global_sell_fill_prefix_objective(
            scored,
            filled_shares=filled,
            net_proceeds_usd=prefix_proceeds,
        )
        if not (prefix_du > 0.0 and prefix_ev > 0.0):
            return replace(
                scored,
                rejection_reasons={
                    candidate.candidate_id: "NON_POSITIVE_ROBUST_FILL_PREFIX"
                },
            )
        remaining -= take
        if remaining <= Decimal("1e-9"):
            break
    return scored


def _probability_witness_rejection_reason(
    candidate: GlobalSingleOrderAnyCandidate,
    witness: JointOutcomeProbabilityWitness | None,
    current: CurrentFamilyProbabilityAuthority | None,
    *,
    decision_at_utc: datetime,
) -> tuple[GlobalEligibilityReason | None, np.ndarray | None]:
    """Verify that candidate q is one projection of a current complete simplex."""

    if witness is None or witness.family_key != candidate.family_key:
        return "PROBABILITY_AUTHORITY_MISSING", None
    age = decision_at_utc - witness.captured_at_utc
    if age.total_seconds() < 0.0 or age > witness.max_age:
        return "PROBABILITY_AUTHORITY_EXPIRED", None
    if (
        current is None
        or current.family_key != witness.family_key
        or current.witness_identity != witness.witness_identity
        or current.q_version != witness.q_version
        or current.resolution_identity != witness.resolution_identity
        or current.topology_identity != witness.topology_identity
        or current.posterior_identity_hash != witness.posterior_identity_hash
        or current.source_truth_identity != witness.source_truth_identity
        or current.authority_certificate_hash != witness.authority_certificate_hash
        or current.band_alpha != witness.band_alpha
        or current.band_basis != witness.band_basis
    ):
        return "PROBABILITY_AUTHORITY_SUPERSEDED", None
    try:
        column = witness.bin_ids.index(candidate.bin_id)
    except ValueError:
        return "JOINT_Q_MEMBERSHIP_MISMATCH", None
    binding = witness.bindings[column]
    expected_token = (
        binding.yes_token_id if candidate.side == "YES" else binding.no_token_id
    )
    if (
        not expected_token
        or candidate.condition_id != binding.condition_id
        or candidate.token_id != expected_token
        or candidate.probability_witness_identity != witness.witness_identity
        or candidate.resolution_identity != witness.resolution_identity
    ):
        return "JOINT_Q_MEMBERSHIP_MISMATCH", None
    yes_q = witness.yes_q_samples[:, column]
    payoff_q = yes_q if candidate.side == "YES" else 1.0 - yes_q
    return None, np.ascontiguousarray(payoff_q)


def select_global_single_order(
    candidates: Sequence[GlobalSingleOrderAnyCandidate],
    *,
    probability_witnesses: Mapping[str, JointOutcomeProbabilityWitness],
    universe_witness: GlobalAuctionUniverseWitness,
    current_universe_identity_resolver: Callable[[], str | None],
    current_probability_resolver: Callable[
        [str], CurrentFamilyProbabilityAuthority | None
    ],
    current_execution_resolver: Callable[
        [GlobalSingleOrderAnyCandidate], CurrentExecutionAuthority | None
    ],
    current_wealth_identity_resolver: Callable[[], str | None],
    wealth_witness: PortfolioWealthWitness,
    capital_limit_usd: Decimal,
    fractional_kelly_multiplier: Decimal = Decimal("1"),
    decision_at_utc: datetime,
    candidate_capital_limit_resolver: Callable[
        [GlobalSingleOrderAnyCandidate], Decimal
    ]
    | None = None,
    candidate_portfolio_endowment_resolver: Callable[
        [GlobalSingleOrderAnyCandidate], CandidatePortfolioEndowment
    ]
    | None = None,
    candidate_payoff_q_lcb_resolver: Callable[
        [GlobalSingleOrderAnyCandidate], float | None
    ]
    | None = None,
    candidate_policy_rejection_resolver: Callable[
        [GlobalSingleOrderAnyCandidate], str | None
    ]
    | None = None,
) -> GlobalSingleOrderDecision:
    """Select one current executable order across every family and native side.

    Eligibility is lexically prior to economics.  A cheap stale/unsupported tail never
    receives a score.  Candidate q is not self-authenticating: it must be the exact YES
    column (or pointwise NO complement) of a current complete family-simplex witness.
    Because exactly one new order may win, cross-family coupling is not fabricated.
    """

    if decision_at_utc.tzinfo is None:
        raise ValueError("decision_at_utc must be timezone-aware")
    universe_age = decision_at_utc - universe_witness.captured_at_utc
    try:
        current_universe_identity = current_universe_identity_resolver()
    except Exception:  # noqa: BLE001 - authority loss is a typed no-trade
        current_universe_identity = None
    expected_families = set(universe_witness.family_keys)
    supplied_families = set(probability_witnesses)
    candidate_families = {candidate.family_key for candidate in candidates}
    supplied_bindings = {
        family_key: witness.family_binding_identity
        for family_key, witness in probability_witnesses.items()
    }
    if (
        universe_witness.witness_identity != current_universe_identity
        or universe_age.total_seconds() < 0.0
        or universe_age > universe_witness.max_age
        or supplied_families != expected_families
        or supplied_bindings != universe_witness.binding_by_family
        or not candidate_families.issubset(expected_families)
    ):
        reason = "GLOBAL_FEASIBLE_SET_INCOMPLETE"
        return GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason=reason,
            rejection_reasons={
                candidate.candidate_id: reason
                for candidate in candidates
            },
            candidate_evaluations=_global_candidate_evaluations(
                candidates,
                rejections={},
                default_rejection=reason,
            ),
            candidate_input_count=len(candidates),
        )
    if wealth_witness.collateral_authority not in {"CHAIN", "VENUE"}:
        reason = "COLLATERAL_UNKNOWN"
        return GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason=reason,
            rejection_reasons={c.candidate_id: reason for c in candidates},
            candidate_evaluations=_global_candidate_evaluations(
                candidates,
                rejections={},
                default_rejection=reason,
            ),
            candidate_input_count=len(candidates),
        )
    witness_age = decision_at_utc - wealth_witness.captured_at_utc
    try:
        current_wealth_identity = current_wealth_identity_resolver()
    except Exception:  # noqa: BLE001 - authority loss is a typed no-trade
        current_wealth_identity = None
    witness_current = (
        wealth_witness.economic_identity == current_wealth_identity
        and 0.0 <= witness_age.total_seconds()
        and witness_age <= wealth_witness.max_age
    )
    if not witness_current:
        reason = "CAPITAL_IDENTITY_SUPERSEDED"
        return GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason=reason,
            rejection_reasons={
                c.candidate_id: reason for c in candidates
            },
            candidate_evaluations=_global_candidate_evaluations(
                candidates,
                rejections={},
                default_rejection=reason,
            ),
            candidate_input_count=len(candidates),
        )
    if capital_limit_usd < 0:
        raise ValueError("capital limit must be non-negative")
    multiplier = Decimal(fractional_kelly_multiplier)
    if not multiplier.is_finite() or not Decimal("0") < multiplier <= Decimal("1"):
        raise ValueError("fractional Kelly multiplier must be finite and in (0, 1]")

    rejections: dict[str, str] = {}
    eligible: list[
        tuple[GlobalSingleOrderAnyCandidate, np.ndarray, float, str]
    ] = []
    for candidate in candidates:
        reason: str | None = candidate.eligibility_reason
        q_samples: np.ndarray | None = None
        probability_witness = probability_witnesses.get(candidate.family_key)
        if reason is None and candidate_policy_rejection_resolver is not None:
            try:
                policy_reason = candidate_policy_rejection_resolver(candidate)
            except Exception:  # noqa: BLE001 - lost policy authority invalidates the epoch
                policy_reason = "CANDIDATE_POLICY_AUTHORITY_MISSING"
            if policy_reason is not None:
                reason = str(policy_reason).strip() or "CANDIDATE_POLICY_AUTHORITY_INVALID"
        if reason is None:
            try:
                current_probability = current_probability_resolver(candidate.family_key)
            except Exception:  # noqa: BLE001 - authority loss is a typed no-trade
                current_probability = None
            reason, q_samples = _probability_witness_rejection_reason(
                candidate,
                probability_witness,
                current_probability,
                decision_at_utc=decision_at_utc,
            )
        if reason is None:
            try:
                current_execution = current_execution_resolver(candidate)
            except Exception:  # noqa: BLE001 - authority loss is a typed no-trade
                current_execution = None
            if current_execution is None:
                reason = "EXECUTION_AUTHORITY_MISSING"
            elif (
                current_execution.token_id != candidate.token_id
                or current_execution.side != candidate.side
                or current_execution.book_snapshot_id != candidate.book_snapshot_id
                or getattr(current_execution, "action", "BUY")
                != getattr(candidate, "action", "BUY")
            ):
                reason = "BOOK_IDENTITY_SUPERSEDED"
            elif (
                current_execution.execution_curve_identity
                != candidate.execution_curve_identity
            ):
                reason = "EXECUTION_CURVE_SUPERSEDED"
        quote_age = decision_at_utc - candidate.book_captured_at_utc
        candidate_curve = (
            candidate.executable_sell_curve
            if isinstance(candidate, GlobalSingleOrderSellCandidate)
            else candidate.executable_cost_curve
        )
        if (
            reason is None
            and (
                quote_age.total_seconds() < 0.0
                or quote_age > candidate_curve.quote_ttl
            )
        ):
            reason = "QUOTE_EXPIRED"
        if (
            reason is None
            and candidate.ledger_snapshot_id != wealth_witness.ledger_snapshot_id
        ):
            reason = "CAPITAL_IDENTITY_SUPERSEDED"
        if reason is not None:
            rejections[candidate.candidate_id] = reason
            continue
        assert probability_witness is not None and q_samples is not None
        eligible.append(
            (
                candidate,
                q_samples,
                probability_witness.band_alpha,
                probability_witness.band_basis,
            )
        )

    # A dynamic authority change invalidates the epoch; it does not merely remove
    # one asset from the ranking. Choosing an unchanged runner-up after another
    # candidate's q/book/capital identity moved would prove a global optimum in
    # neither the old nor the new feasible set. Rebuild the complete set next cycle.
    epoch_invalidating_reasons = {
        "PROBABILITY_AUTHORITY_MISSING",
        "PROBABILITY_AUTHORITY_EXPIRED",
        "PROBABILITY_AUTHORITY_SUPERSEDED",
        "EXECUTION_AUTHORITY_MISSING",
        "BOOK_IDENTITY_SUPERSEDED",
        "EXECUTION_CURVE_SUPERSEDED",
        "QUOTE_EXPIRED",
        "CAPITAL_IDENTITY_SUPERSEDED",
        "CANDIDATE_POLICY_AUTHORITY_MISSING",
        "CANDIDATE_POLICY_AUTHORITY_INVALID",
    }
    if any(reason in epoch_invalidating_reasons for reason in rejections.values()):
        no_trade_reason = "GLOBAL_EPOCH_SUPERSEDED"
        return GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason=no_trade_reason,
            rejection_reasons=rejections,
            candidate_evaluations=_global_candidate_evaluations(
                candidates,
                rejections=rejections,
                default_rejection=no_trade_reason,
            ),
            candidate_input_count=len(candidates),
        )

    band_alphas = {alpha for _, _, alpha, _basis in eligible}
    if len(band_alphas) > 1:
        rejections.update(
            {c.candidate_id: "BAND_ALPHA_MISMATCH" for c, _, _, _ in eligible}
        )
        return GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason="BAND_ALPHA_MISMATCH",
            rejection_reasons=rejections,
            candidate_evaluations=_global_candidate_evaluations(
                candidates,
                rejections=rejections,
                default_rejection="BAND_ALPHA_MISMATCH",
            ),
            candidate_input_count=len(candidates),
        )

    scored: list[GlobalSingleOrderDecision] = []
    for candidate, q_samples, band_alpha, _band_basis in eligible:
        if isinstance(candidate, GlobalSingleOrderSellCandidate):
            score = _score_global_single_order_sell(
                candidate,
                held_payoff_q_samples=q_samples,
                band_alpha=band_alpha,
                wealth_floor_usd=wealth_witness.wealth_floor_usd,
                wealth_ceiling_usd=wealth_witness.wealth_ceiling_usd,
            )
            if score.candidate is None:
                rejections.update(score.rejection_reasons)
            else:
                scored.append(score)
                rejections.update(score.rejection_reasons)
            continue
        candidate_capital_limit = capital_limit_usd
        if candidate_capital_limit_resolver is not None:
            try:
                candidate_capital_limit = min(
                    capital_limit_usd,
                    Decimal(candidate_capital_limit_resolver(candidate)),
                )
            except Exception:  # noqa: BLE001 - lost allocator authority invalidates the epoch
                reason = "CAPITAL_CONSTRAINT_UNAVAILABLE"
                failure_rejections = {
                    **rejections,
                    candidate.candidate_id: reason,
                }
                return GlobalSingleOrderDecision(
                    candidate=None,
                    shares=Decimal("0"),
                    cost_usd=Decimal("0"),
                    robust_delta_log_wealth=0.0,
                    robust_ev_usd=0.0,
                    capital_efficiency=0.0,
                    no_trade_reason="GLOBAL_EPOCH_SUPERSEDED",
                    rejection_reasons=failure_rejections,
                    candidate_evaluations=_global_candidate_evaluations(
                        candidates,
                        rejections=failure_rejections,
                        scores=scored,
                        default_rejection="GLOBAL_EPOCH_SUPERSEDED",
                    ),
                    candidate_input_count=len(candidates),
                )
        if candidate_capital_limit <= 0:
            rejections[candidate.candidate_id] = "CAPITAL_CAPACITY_EXHAUSTED"
            continue
        candidate_endowment = CandidatePortfolioEndowment(
            loss_wealth_floor_usd=wealth_witness.wealth_floor_usd,
            win_wealth_ceiling_usd=wealth_witness.wealth_ceiling_usd,
            current_token_shares=Decimal("0"),
            ledger_snapshot_id=wealth_witness.ledger_snapshot_id,
        )
        if candidate_portfolio_endowment_resolver is not None:
            try:
                candidate_endowment = candidate_portfolio_endowment_resolver(
                    candidate
                )
                if (
                    not isinstance(candidate_endowment, CandidatePortfolioEndowment)
                    or candidate_endowment.ledger_snapshot_id
                    != wealth_witness.ledger_snapshot_id
                ):
                    raise ValueError("candidate endowment ledger mismatch")
            except Exception:  # noqa: BLE001 - lost portfolio authority invalidates the epoch
                reason = "PORTFOLIO_ENDOWMENT_UNAVAILABLE"
                failure_rejections = {
                    **rejections,
                    candidate.candidate_id: reason,
                }
                return GlobalSingleOrderDecision(
                    candidate=None,
                    shares=Decimal("0"),
                    cost_usd=Decimal("0"),
                    robust_delta_log_wealth=0.0,
                    robust_ev_usd=0.0,
                    capital_efficiency=0.0,
                    no_trade_reason="GLOBAL_EPOCH_SUPERSEDED",
                    rejection_reasons=failure_rejections,
                    candidate_evaluations=_global_candidate_evaluations(
                        candidates,
                        rejections=failure_rejections,
                        scores=scored,
                        default_rejection="GLOBAL_EPOCH_SUPERSEDED",
                    ),
                    candidate_input_count=len(candidates),
                )
        candidate_payoff_q_lcb = None
        if candidate_payoff_q_lcb_resolver is not None:
            try:
                candidate_payoff_q_lcb = candidate_payoff_q_lcb_resolver(candidate)
            except Exception:  # noqa: BLE001 - malformed bound invalidates this candidate
                rejections[candidate.candidate_id] = "PAYOFF_Q_LCB_UNAVAILABLE"
                continue
            if candidate_payoff_q_lcb is not None and (
                not math.isfinite(candidate_payoff_q_lcb)
                or not 0.0 <= candidate_payoff_q_lcb <= 1.0
            ):
                rejections[candidate.candidate_id] = "PAYOFF_Q_LCB_INVALID"
                continue
        score = _score_global_single_order(
            candidate,
            q_samples=q_samples,
            band_alpha=band_alpha,
            wealth_floor_usd=candidate_endowment.loss_wealth_floor_usd,
            wealth_ceiling_usd=candidate_endowment.win_wealth_ceiling_usd,
            spendable_cash_usd=wealth_witness.spendable_cash_usd,
            capital_limit_usd=candidate_capital_limit,
            fractional_kelly_multiplier=multiplier,
            payoff_q_lcb=candidate_payoff_q_lcb,
            current_token_shares=candidate_endowment.current_token_shares,
        )
        if score.candidate is None:
            rejections.update(score.rejection_reasons)
        else:
            scored.append(score)

    positive_scored = tuple(
        score
        for score in scored
        if score.candidate is not None
        and score.candidate.candidate_id not in rejections
        and score.robust_delta_log_wealth > 0.0
        and score.robust_ev_usd > 0.0
    )
    if not positive_scored:
        no_trade_reason = (
            "ROBUST_MAJORITY_LOSS"
            if rejections
            and set(rejections.values()) == {"ROBUST_MAJORITY_LOSS"}
            else "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER"
        )
        return GlobalSingleOrderDecision(
            candidate=None,
            shares=Decimal("0"),
            cost_usd=Decimal("0"),
            robust_delta_log_wealth=0.0,
            robust_ev_usd=0.0,
            capital_efficiency=0.0,
            no_trade_reason=no_trade_reason,
            rejection_reasons=rejections,
            candidate_evaluations=_global_candidate_evaluations(
                candidates,
                rejections=rejections,
                scores=scored,
            ),
            candidate_input_count=len(candidates),
        )

    # Current-epoch capital growth is the only objective identified by current truth.
    # A cross-epoch rate would require an authoritative capital-release distribution,
    # future opportunity-arrival process, and reinvestment policy.  None may be guessed
    # from a target date.  Maximize robust Δlog now; at a numerical tie, prefer higher
    # robust terminal-wealth growth per dollar and then less cash.
    winner = min(
        positive_scored,
        key=lambda score: (
            -round(score.robust_delta_log_wealth, 15),
            -round(score.capital_efficiency, 15),
            score.cost_usd,
            score.candidate.candidate_id if score.candidate is not None else "",
        ),
    )
    winner_id = winner.candidate.candidate_id if winner.candidate is not None else None
    return GlobalSingleOrderDecision(
        candidate=winner.candidate,
        shares=winner.shares,
        cost_usd=winner.cost_usd,
        robust_delta_log_wealth=winner.robust_delta_log_wealth,
        robust_ev_usd=winner.robust_ev_usd,
        capital_efficiency=winner.capital_efficiency,
        no_trade_reason=None,
        limit_price=winner.limit_price,
        expected_fill_price_before_fee=winner.expected_fill_price_before_fee,
        max_spend_usd=winner.max_spend_usd,
        cash_proceeds_usd=winner.cash_proceeds_usd,
        current_token_shares=winner.current_token_shares,
        full_kelly_target_shares=winner.full_kelly_target_shares,
        fractional_kelly_target_shares=(
            winner.fractional_kelly_target_shares
        ),
        terminal_wealth=winner.terminal_wealth,
        rejection_reasons=rejections,
        candidate_evaluations=_global_candidate_evaluations(
            candidates,
            rejections=rejections,
            scores=scored,
            winner_id=winner_id,
        ),
        candidate_input_count=len(candidates),
    )


def _ru_cvar_optimum(
    *,
    seed: np.ndarray,
    w0: np.ndarray,
    payoff: np.ndarray,
    caps: np.ndarray,
    costs: np.ndarray,
    cash: float,
    q_draws: np.ndarray,
    weights: np.ndarray,
    alpha: float,
) -> tuple[np.ndarray, float, int]:
    """Certify the continuous global optimum through a lower-CVaR cutting-plane program.

    Lower CVaR is the minimum weighted expectation over the bounded tail-mixture polytope.
    The master maximizes ``eta`` subject to ``eta <= r·du(x)`` for each discovered tail mixture
    ``r``; at every candidate the current worst-tail mixture is added.  Each ``du_k`` is concave,
    so every cut is a convex-feasible superlevel constraint.  When the master upper bound ``eta``
    meets the actual lower CVaR, the global gap is certified without one slack variable per draw.
    """
    keep = np.asarray(weights, dtype=np.float64) > 0.0
    q = np.asarray(q_draws, dtype=np.float64)[keep]
    w = np.asarray(weights, dtype=np.float64)[keep]
    if q.shape[0] == 0:
        raise OptimizerConvergenceError("RU CVaR solve has no positive-weight belief draws")

    n_items = payoff.shape[0]
    n_draws = q.shape[0]

    def _draw_utility(x: np.ndarray) -> np.ndarray:
        w_end = w0 + x @ payoff
        if not np.all(w_end > 0.0):
            return np.full(n_draws, -np.inf, dtype=np.float64)
        return q @ np.log(w_end / w0)

    def _tail_mixture(du: np.ndarray) -> np.ndarray:
        """The exact weighted worst-alpha mixture whose dot product equals lower CVaR."""
        order = np.argsort(du, kind="stable")
        target = float(alpha) * float(w.sum())
        remaining = target
        mixture = np.zeros(n_draws, dtype=np.float64)
        for idx in order:
            take = min(float(w[idx]), remaining)
            if take > 0.0:
                mixture[idx] = take / target
                remaining -= take
            if remaining <= 1e-15:
                break
        return mixture

    seed = np.clip(np.asarray(seed, dtype=np.float64), 0.0, caps)
    seed_du = _draw_utility(seed)
    if not np.all(np.isfinite(seed_du)):
        raise OptimizerConvergenceError("RU CVaR warm start has non-positive terminal wealth")
    warm_seed = seed.copy()
    warm_du = seed_du.copy()
    cuts = [_tail_mixture(seed_du)]
    n_vars = n_items + 1
    budget_row = np.concatenate((costs, np.zeros(1))).reshape(1, n_vars)
    wealth_rows = np.hstack(
        (payoff.T, np.zeros((w0.size, 1), dtype=np.float64))
    )
    wealth_floor = np.maximum(w0 * _WEALTH_MARGIN, 1e-12)
    bounds = Bounds(
        np.concatenate((np.zeros(n_items), np.array([-np.inf]))),
        np.concatenate((caps, np.array([np.inf]))),
    )
    objective_jac = np.concatenate((np.zeros(n_items), np.array([-1.0])))
    total_iterations = 0
    for _cut_round in range(64):
        mixture_matrix = np.stack(cuts)

        def _cut_values(v: np.ndarray) -> np.ndarray:
            return mixture_matrix @ _draw_utility(v[:n_items]) - v[n_items]

        def _cut_jac(v: np.ndarray) -> np.ndarray:
            x = v[:n_items]
            w_end = w0 + x @ payoff
            draw_grad = q @ (payoff.T / w_end[:, None])
            jac = np.empty((len(cuts), n_items + 1), dtype=np.float64)
            jac[:, :n_items] = mixture_matrix @ draw_grad
            jac[:, n_items] = -1.0
            return jac

        seed_eta = float(np.min(mixture_matrix @ seed_du))
        warm_eta = float(np.min(mixture_matrix @ warm_du))
        if warm_eta > seed_eta:
            start_x, eta0 = warm_seed, warm_eta
        else:
            start_x, eta0 = seed, seed_eta
        v0 = np.concatenate((start_x, np.array([eta0])))
        constraints = (
            LinearConstraint(budget_row, -np.inf, cash),
            LinearConstraint(wealth_rows, wealth_floor - w0, np.inf),
            NonlinearConstraint(_cut_values, 0.0, np.inf, jac=_cut_jac),
        )
        result = minimize(
            lambda v: -float(v[n_items]),
            v0,
            jac=lambda _v: objective_jac,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-12, "maxiter": 300, "disp": False},
        )
        total_iterations += int(result.nit)
        x = np.asarray(result.x[:n_items], dtype=np.float64)
        du = _draw_utility(x)
        u = _lower_cvar(du, w, alpha)
        gap = float(result.x[n_items]) - float(u)
        violations = (
            float(costs @ x) > cash + 1e-7
            or np.any(w0 + x @ payoff < wealth_floor - 1e-7)
            or np.any(x < -1e-8)
            or np.any(x > caps + 1e-8)
            or np.min(_cut_values(result.x)) < -1e-7
        )
        if result.success and not violations and np.isfinite(u) and gap <= 2e-9:
            return x, float(u), total_iterations
        if violations or not np.isfinite(u):
            raise OptimizerConvergenceError(
                f"RU CVaR master became infeasible: success={result.success}, "
                f"message={result.message!s}, violations={violations}"
            )
        next_cut = _tail_mixture(du)
        if any(np.array_equal(next_cut, prior) for prior in cuts):
            raise OptimizerConvergenceError(
                f"RU CVaR master stalled: success={result.success}, gap={gap:.12g}, "
                f"message={result.message!s}"
            )
        cuts.append(next_cut)
        seed, seed_du = x, du
    raise OptimizerConvergenceError("RU CVaR master exceeded 64 tail-cut rounds")


def _feasible_hi(
    i: int, x: np.ndarray, w0: np.ndarray, payoff: np.ndarray, caps: np.ndarray, costs: np.ndarray, cash: float
) -> float:
    """Largest stake for coordinate ``i`` (others fixed) under all three bounds: depth cap,
    every-atom wealth > 0, and the executable-cash budget.

    The budget bound is the consult REV-2 follow-up blocker: ``W_end > 0`` does NOT imply
    affordability — buying several mutually exclusive claims can leave positive terminal wealth in
    every atom while the UPFRONT outlay ``Σ cost_k·x_k`` exceeds spendable cash. So the coordinate
    is also capped so that net spend stays within ``cash`` (sells free up budget: ``cost_i < 0``).
    """
    base = w0 + x @ payoff - x[i] * payoff[i]
    p_i = payoff[i]
    losing = p_i < 0.0
    hi = float(caps[i])
    if losing.any():
        ruin = base[losing] / (-p_i[losing])
        hi = min(hi, float(ruin.min()) * (1.0 - _WEALTH_MARGIN))
    if costs[i] > 0.0:
        spend_others = float(costs @ x) - float(costs[i]) * float(x[i])
        remaining = cash - spend_others
        hi = min(hi, max(remaining, 0.0) / float(costs[i]))
    return max(hi, 0.0)


def _coarse_fine_argmax(f, lo: float, hi: float) -> tuple[float, float]:
    """Coarse-to-fine 1-D argmax of ``f`` over ``[lo, hi]`` (payoff_vector's grid resolution)."""
    best_u = -np.inf
    best_x = lo
    span_lo, span_hi = lo, hi
    steps = _COARSE_STEPS
    for _pass in range(_REFINE_PASSES + 1):
        width = span_hi - span_lo
        if width <= 0.0:
            break
        step = width / steps
        pass_best_u = -np.inf
        pass_best_x = span_lo
        val = span_lo
        for _ in range(steps + 1):
            u = f(val)
            if u > pass_best_u:
                pass_best_u = u
                pass_best_x = val
            val += step
        if pass_best_u > best_u:
            best_u = pass_best_u
            best_x = pass_best_x
        span_lo = max(lo, pass_best_x - step)
        span_hi = min(hi, pass_best_x + step)
        steps = _REFINE_STEPS
    return best_x, float(best_u)


def _grid_max_coordinate(
    i: int,
    x: np.ndarray,
    hi: float,
    w0: np.ndarray,
    payoff: np.ndarray,
    q_draws: np.ndarray,
    weights: np.ndarray,
    alpha: float,
) -> tuple[float, float]:
    """Coarse-to-fine 1-D argmax of the CVaR objective over ``x_i ∈ [0, hi]`` (others fixed)."""
    if hi <= 0.0:
        x0 = x.copy()
        x0[i] = 0.0
        return 0.0, _objective(x0, w0, payoff, q_draws, weights, alpha)
    trial = x.copy()

    def _u(val: float) -> float:
        trial[i] = val
        return _objective(trial, w0, payoff, q_draws, weights, alpha)

    return _coarse_fine_argmax(_u, 0.0, hi)


def _pair_exchange(
    i: int,
    j: int,
    x: np.ndarray,
    w0: np.ndarray,
    payoff: np.ndarray,
    caps: np.ndarray,
    costs: np.ndarray,
    q_draws: np.ndarray,
    weights: np.ndarray,
    alpha: float,
) -> float:
    """BUDGET-NEUTRAL pairwise exchange for coordinates ``i, j`` — the step pure coordinate
    ascent cannot take (consult REV-2 follow-up: it stalls on the budget face).

    When the executable-cash budget binds, the optimum lives on the constraint face and moving a
    single coordinate is infeasible; only a simultaneous transfer stays in budget. Transferring
    budget ``t`` from ``j`` to ``i`` (``x_i += t/c_i``, ``x_j -= t/c_j``) keeps net spend EXACTLY
    fixed, so a 1-D search over ``t`` climbs the concave objective along the face. For a single
    linear budget constraint, pairwise transfers span its feasible directions, so interleaving
    these with single-coordinate sweeps reaches the global optimum. Returns the ΔU gained.
    """
    ci, cj = float(costs[i]), float(costs[j])
    if ci <= 0.0 or cj <= 0.0:
        return 0.0  # only positive-cost (buy) pairs are coupled through the budget
    xi0, xj0 = float(x[i]), float(x[j])
    # Preserve both coordinates' venue-depth caps as well as non-negativity. The old exchange
    # bounded only the lower side and could manufacture stake beyond priced depth while keeping
    # the cash budget constant — an infeasible warm start that falsely outscored the convex solve.
    lo = max(-xi0 * ci, (xj0 - float(caps[j])) * cj)
    hi = min((float(caps[i]) - xi0) * ci, xj0 * cj)
    # Preserve strictly positive terminal wealth along the exchange ray.
    w_cur = w0 + x @ payoff
    direction = payoff[i] / ci - payoff[j] / cj
    wealth_floor = np.maximum(w0 * _WEALTH_MARGIN, 1e-12)
    for atom in range(w0.size):
        if direction[atom] < 0.0:
            hi = min(hi, (w_cur[atom] - wealth_floor[atom]) / -direction[atom])
        elif direction[atom] > 0.0:
            lo = max(lo, (wealth_floor[atom] - w_cur[atom]) / direction[atom])
    if hi - lo <= 0.0:
        return 0.0
    trial = x.copy()

    def _u(t: float) -> float:
        nxi = xi0 + t / ci
        nxj = xj0 - t / cj
        if nxi < 0.0 or nxj < 0.0:
            return -np.inf
        trial[i] = nxi
        trial[j] = nxj
        return _objective(trial, w0, payoff, q_draws, weights, alpha)

    u0 = _objective(x, w0, payoff, q_draws, weights, alpha)
    best_t, best_u = _coarse_fine_argmax(_u, lo, hi)
    if best_u > u0 + _CONVERGENCE_TOL:
        x[i] = xi0 + best_t / ci
        x[j] = xj0 - best_t / cj
        return best_u - u0
    return 0.0


def _optimize_continuous(
    w0: np.ndarray,
    payoff: np.ndarray,
    caps: np.ndarray,
    costs: np.ndarray,
    cash: float,
    q_draws: np.ndarray,
    weights: np.ndarray,
    alpha: float,
) -> tuple[np.ndarray, float, np.ndarray, float, int]:
    """Joint continuous optimum + the best single-item (top-1 picker) optimum.

    Returns ``(x_joint, U_joint, x_top1, U_top1, sweeps)``; ``x_joint`` is the coordinate-ascent
    optimum seeded at the best single item, so ``U_joint ≥ U_top1`` always (dominance guarantee).
    The coordinate/pair/radial ascent constructs a deterministic feasible warm start. The final
    joint vector comes from the certifying Rockafellar–Uryasev convex program; failure to dominate
    the warm start is loud rather than silently relabeling a heuristic as globally optimal.
    """
    n_items = payoff.shape[0]
    zeros = np.zeros(n_items, dtype=np.float64)
    if n_items == 0:
        return zeros, 0.0, zeros.copy(), 0.0, 0

    total_sweeps = [0]

    def _radial(x: np.ndarray, u_cur: float) -> float:
        """Scale the whole stake vector by ``t ≥ 0`` — the BALANCED-GROWTH direction that neither a
        single-coordinate move nor a budget-neutral exchange can climb (both a full-set arb and a
        symmetric diversification hedge grow all legs proportionally). Returns the gain."""
        if float(x.sum()) <= 0.0:
            return 0.0
        t_max = np.inf
        spend = float(costs @ x)
        if spend > 0.0 and cash > 0.0:
            t_max = min(t_max, cash / spend)
        pos = x > 0.0
        if pos.any():
            t_max = min(t_max, float(np.min(caps[pos] / x[pos])))
        if not np.isfinite(t_max) or t_max <= 0.0:
            t_max = 1.0
        base = x.copy()

        def _u(t: float) -> float:
            return _objective(t * base, w0, payoff, q_draws, weights, alpha)

        best_t, best_u = _coarse_fine_argmax(_u, 0.0, t_max * (1.0 - _WEALTH_MARGIN))
        if best_u > u_cur + _CONVERGENCE_TOL:
            x[:] = best_t * base
            return best_u - u_cur
        return 0.0

    def _ascend(seed: np.ndarray) -> tuple[np.ndarray, float]:
        x = seed.copy()
        u_cur = _objective(x, w0, payoff, q_draws, weights, alpha)
        for _sweep in range(_MAX_SWEEPS):
            total_sweeps[0] += 1
            sweep_gain = 0.0
            # single-coordinate sweep (handles the budget-slack interior)
            for i in range(n_items):
                hi = _feasible_hi(i, x, w0, payoff, caps, costs, cash)
                xi, ui = _grid_max_coordinate(i, x, hi, w0, payoff, q_draws, weights, alpha)
                if ui > u_cur + _CONVERGENCE_TOL:
                    sweep_gain += ui - u_cur
                    x[i] = xi
                    u_cur = ui
            # budget-neutral pairwise-exchange sweep (handles the budget FACE, where a single
            # coordinate move is infeasible). ONLY when the budget is (near-)binding: with slack the
            # concave box optimum is already global, so pairwise is a no-op — skipping it keeps the
            # live reactor-cycle cost bounded (payoff_vector lesson).
            if float(costs @ x) >= cash - (_BUDGET_BIND_REL * cash + 1e-9):
                for i in range(n_items):
                    for j in range(i + 1, n_items):
                        sweep_gain += _pair_exchange(
                            i, j, x, w0, payoff, caps, costs, q_draws, weights, alpha
                        )
            # radial balanced-growth step (handles the direction both arbs and symmetric hedges need)
            sweep_gain += _radial(x, _objective(x, w0, payoff, q_draws, weights, alpha))
            u_cur = _objective(x, w0, payoff, q_draws, weights, alpha)
            if sweep_gain < _CONVERGENCE_TOL:
                break
        return x, float(u_cur)

    # Top-1 seed: the best single item alone.
    best_single_u = 0.0
    x_top1 = zeros.copy()
    for i in range(n_items):
        hi = _feasible_hi(i, zeros, w0, payoff, caps, costs, cash)
        xi, ui = _grid_max_coordinate(i, zeros, hi, w0, payoff, q_draws, weights, alpha)
        if ui > best_single_u:
            best_single_u = ui
            x_top1 = zeros.copy()
            x_top1[i] = xi

    x_a, u_a = _ascend(x_top1)

    # Diversified seed — ONLY when no single item improves alone (best_single_u <= 0, so x_top1 is
    # the origin and its ascend is stuck there). That is exactly the from-origin hedge: a small
    # stake on every POSITIVE-MEAN item at once lands inside the hedge's basin, because CVaR's
    # directional derivative is superadditive (∂U/∂(e_i+e_j) can be > 0 while each ∂U/∂e_i ≤ 0).
    # When a positive single base DOES exist, the top1-seeded sweeps already add diversifying legs,
    # so the second ascend is skipped — keeping the live reactor-cycle cost bounded.
    if best_single_u <= 0.0:
        mean_q = (weights @ q_draws) / float(weights.sum())
        x_div = zeros.copy()
        for i in range(n_items):
            if float(mean_q @ payoff[i]) > 0.0:  # positive MEAN edge (tail may be adverse alone)
                hi = _feasible_hi(i, zeros, w0, payoff, caps, costs, cash)
                x_div[i] = 0.02 * hi
        div_spend = float(costs @ x_div)
        if div_spend > cash > 0.0:
            x_div *= cash / div_spend  # keep the seed inside the executable budget
        if float(x_div.sum()) > 0.0:
            x_b, u_b = _ascend(x_div)
            if u_b > u_a:
                x_a, u_a = x_b, u_b

    x_ru, u_ru, ru_iterations = _ru_cvar_optimum(
        seed=x_a,
        w0=w0,
        payoff=payoff,
        caps=caps,
        costs=costs,
        cash=cash,
        q_draws=q_draws,
        weights=weights,
        alpha=alpha,
    )
    if u_ru < u_a - 1e-8:
        raise OptimizerConvergenceError(
            f"RU CVaR objective {u_ru:.12g} failed to dominate feasible warm start {u_a:.12g}"
        )
    return x_ru, float(u_ru), x_top1, float(best_single_u), total_sweeps[0] + ru_iterations


def _quantize_size(units: float, item: MenuItem) -> Optional[Decimal]:
    """Venue-quantize a continuous stake on the item's OWN grid, or ``None`` if sub-depth.

    Sub-floor-but-positive stakes are promoted UP to ``min_order_size`` (the smallest executable
    size — the sign-flip case the re-evaluation gate then judges); above-floor stakes round to
    the ``_SIZE_QUANTUM`` grid; everything is capped at depth.
    """
    if units <= 0.0:
        return None
    min_order = Decimal(item.min_order_size)
    u = Decimal(str(units))
    if u < min_order:
        size = min_order
    else:
        size = (u / _SIZE_QUANTUM).to_integral_value(rounding=ROUND_HALF_EVEN) * _SIZE_QUANTUM
    depth_cap = (Decimal(item.max_units) / _SIZE_QUANTUM).to_integral_value(rounding=ROUND_FLOOR) * _SIZE_QUANTUM
    if size > depth_cap:
        size = depth_cap
    if size < min_order or size <= 0:
        return None
    return size


def _repair(
    x_cont: np.ndarray,
    *,
    items: list,
    w0: np.ndarray,
    payoff: np.ndarray,
    costs: np.ndarray,
    cash: float,
    q_draws: np.ndarray,
    weights: np.ndarray,
    alpha: float,
    kappa: float,
) -> dict:
    """κ-scale, quantize on each item's own grid, cap at _MAX_ORDERS, ENFORCE the executable
    budget, re-evaluate under the worst-price model.

    Returns a dict with the discrete stake vector, its re-evaluated CVaR ΔU, the surviving
    ``(item_index, size)`` list, and the RepairCertificate provenance (deltas / promoted /
    dropped). The caller trades only if ``u_disc > 0``. Rounding UP to ``min_order_size`` can push
    net spend past the continuous budget, so after quantization the least-valuable positive-cost
    orders are dropped until ``Σ cost_i·size_i ≤ cash`` (consult REV-2 follow-up blocker).
    """
    n_items = payoff.shape[0]
    scaled = kappa * x_cont

    def _marginal(idx_size: tuple[int, Decimal]) -> float:
        i, size = idx_size
        xi = np.zeros(n_items, dtype=np.float64)
        xi[i] = float(size)
        return _objective(xi, w0, payoff, q_draws, weights, alpha)

    sized: list[tuple[int, Decimal]] = []
    tick_deltas: dict[str, str] = {}
    promoted: list[str] = []
    dropped: list[tuple[str, str]] = []
    for i in range(n_items):
        cont_units = float(scaled[i])
        size = _quantize_size(cont_units, items[i])
        if size is None:
            if cont_units > 0.0:
                dropped.append((items[i].item_id, "sub_depth_or_min_size"))
            continue
        if cont_units > 0.0 and Decimal(str(cont_units)) < Decimal(items[i].min_order_size):
            promoted.append(items[i].item_id)
        tick_deltas[items[i].item_id] = f"{cont_units:.6f}->{size}"
        sized.append((i, size))

    if len(sized) > _MAX_ORDERS:
        sized_sorted = sorted(sized, key=_marginal, reverse=True)
        for i, _s in sized_sorted[_MAX_ORDERS:]:
            dropped.append((items[i].item_id, "batch_cap_15"))
        sized = sized_sorted[:_MAX_ORDERS]

    # Executable-budget enforcement: drop the least-valuable positive-cost orders until the net
    # buy outlay fits within spendable cash.
    def _spend(pairs: list[tuple[int, Decimal]]) -> float:
        return float(sum(float(costs[i]) * float(sz) for i, sz in pairs))

    while _spend(sized) > cash and sized:
        droppable = [(i, sz) for i, sz in sized if costs[i] > 0.0]
        if not droppable:
            break  # only sells/zero-cost left; net spend cannot exceed cash further
        worst = min(droppable, key=_marginal)
        sized.remove(worst)
        dropped.append((items[worst[0]].item_id, "budget_exceeded"))

    x_disc = np.zeros(n_items, dtype=np.float64)
    for i, size in sized:
        x_disc[i] = float(size)
    u_disc = _objective(x_disc, w0, payoff, q_draws, weights, alpha)
    return {
        "x_disc": x_disc,
        "u_disc": u_disc,
        "sized": sized,
        "spend": _spend(sized),
        "tick_deltas": tick_deltas,
        "promoted": tuple(promoted),
        "dropped": tuple(dropped),
    }


# ---------------------------------------------------------------------------
# Plan assembly.
# ---------------------------------------------------------------------------

def _order_side(kind: str) -> Optional[str]:
    if kind in ("buy_yes", "buy_no"):
        return "buy"
    if kind == "sell_holding":
        return "sell"
    return None


def _hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for p in parts:
        digest.update(p.encode())
        digest.update(b"\x1f")
    return digest.hexdigest()


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
    scenario_set: JointOutcomeScenarioSet = scenarios.scenarios(bands_by_family)
    atom_ids = scenario_set.atom_ids
    q_draws = scenario_set.q_draws
    n_draws = q_draws.shape[0]
    weights = (
        scenario_set.draw_weights
        if scenario_set.draw_weights is not None
        else np.ones(n_draws, dtype=np.float64)
    )
    alpha = scenario_set.alpha
    kappa = kappa_policy.kappa.as_float()

    w0, payoff, caps, costs, items = _build_arrays(menu, wealth, atom_ids)
    provider = scenario_set.provider
    sample_hash = scenario_set.scenario_hash
    cash = float(wealth.cash_usd)

    # Tail-stability + point-belief stamps (consult REV-2 follow-up): the promotion evidence
    # gate down-weights a plan whose robust ΔU rests on too few tail draws. ESS handles weights.
    eff_draws = float(weights.sum() ** 2 / float((weights ** 2).sum())) if weights.size else 0.0
    tail_draws = alpha * eff_draws
    base_diag = {
        "n_draws": float(n_draws),
        "alpha": float(alpha),
        "effective_tail_draws": tail_draws,
        "tail_floor_ok": 1.0 if tail_draws >= _MIN_TAIL_DRAWS else 0.0,
        "point_belief": 1.0 if n_draws <= 1 else 0.0,
        "spendable_cash_usd": cash,
    }

    def _no_trade(reason: str, baseline: float, diagnostics: dict) -> SolutionPlan:
        return SolutionPlan(
            plan_id=_hash(menu.family_key, menu.menu_hash, sample_hash, q_version, "NO_TRADE"),
            family_key=menu.family_key,
            orders=(),
            expected_delta_log_wealth=0.0,
            delta_u_baseline_top1=baseline,
            kappa_applied=kappa,
            correlation_rail="caps",
            scenario_provider=provider,
            scenario_sample_hash=sample_hash,
            menu_hash=menu.menu_hash,
            q_version=q_version,
            no_trade_reason=reason,
            repair_certificate=None,
            diagnostics=diagnostics,
        )

    if not items:
        return _no_trade("NO_EXECUTABLE_MENU_ITEMS", 0.0, {**base_diag, "n_menu_items": 0.0})

    x_joint, u_joint, x_top1, u_top1, sweeps = _optimize_continuous(
        w0, payoff, caps, costs, cash, q_draws, weights, alpha
    )

    rep_joint = _repair(x_joint, items=items, w0=w0, payoff=payoff, costs=costs, cash=cash, q_draws=q_draws, weights=weights, alpha=alpha, kappa=kappa)
    rep_top1 = _repair(x_top1, items=items, w0=w0, payoff=payoff, costs=costs, cash=cash, q_draws=q_draws, weights=weights, alpha=alpha, kappa=kappa)
    baseline_top1 = rep_top1["u_disc"]

    diagnostics = {
        **base_diag,
        "continuous_delta_u_joint": u_joint,
        "continuous_delta_u_top1": u_top1,
        "discrete_delta_u_joint": rep_joint["u_disc"],
        "discrete_delta_u_top1": rep_top1["u_disc"],
        "continuous_units_total": float(x_joint.sum()),
        "n_menu_items": float(len(items)),
        "optimizer_sweeps": float(sweeps),
    }

    # Safe-prefix ordering: most-improving order first, so every filled prefix improves (W2.1).
    def _marginal(idx_size: tuple[int, Decimal]) -> float:
        i, size = idx_size
        xi = np.zeros(payoff.shape[0], dtype=np.float64)
        xi[i] = float(size)
        return _objective(xi, w0, payoff, q_draws, weights, alpha)

    parent_vec = {"joint": kappa * x_joint, "top1": kappa * x_top1}

    def _assemble(rep: dict, source: str) -> Optional[dict]:
        sized = rep["sized"]
        if not sized or not rep["u_disc"] > 0.0:
            return None
        ordered = sorted(sized, key=_marginal, reverse=True)
        running = np.zeros(payoff.shape[0], dtype=np.float64)
        prefix_bounds: list[float] = []
        for i, size in ordered:
            running[i] = float(size)
            prefix_bounds.append(_objective(running, w0, payoff, q_draws, weights, alpha))
        return {
            "source": source,
            "rep": rep,
            "ordered": ordered,
            "prefix_bounds": prefix_bounds,
            "safe": all(b > 0.0 for b in prefix_bounds),          # every prefix improves
            "affordable": rep["spend"] <= cash + 1e-9,            # net outlay within cash
            "u_disc": rep["u_disc"],
        }

    candidates = [c for c in (_assemble(rep_joint, "joint"), _assemble(rep_top1, "top1")) if c is not None]
    valid = [c for c in candidates if c["safe"] and c["affordable"]]
    if not valid:
        if any(not c["safe"] for c in candidates):
            return _no_trade("UNSAFE_PREFIX_DECOMPOSITION", baseline_top1, diagnostics)
        if any(not c["affordable"] for c in candidates):
            return _no_trade("BUDGET_EXCEEDED", baseline_top1, diagnostics)
        return _no_trade("NO_IMPROVING_DISCRETE_PLAN", baseline_top1, diagnostics)
    chosen = max(valid, key=lambda c: c["u_disc"])
    diagnostics["chosen_source_joint"] = 1.0 if chosen["source"] == "joint" else 0.0

    orders: list[PlannedOrder] = []
    for prefix_index, (i, size) in enumerate(chosen["ordered"]):
        it = items[i]
        token_id = it.token_id
        route = it.route
        if token_id is None and route is not None:
            legs = getattr(route, "legs", ())
            if legs:
                token_id = getattr(legs[0], "token_id", None)
        orders.append(
            PlannedOrder(
                order_id=_hash(menu.menu_hash, it.item_id, str(size)),
                menu_item_id=it.item_id,
                kind=it.kind,
                side=_order_side(it.kind),
                token_id=token_id,
                price=None,  # phase-1: the executable price is assigned by the existing submit path
                size=size,
                q_version=q_version,
                safe_prefix_index=prefix_index,
                snapshot_id=None,
                ledger_snapshot_id=wealth.ledger_snapshot_id,
            )
        )

    order_ids = [o.order_id for o in orders]
    batch_partition = tuple(
        tuple(order_ids[k : k + _MAX_ORDERS]) for k in range(0, len(order_ids), _MAX_ORDERS)
    )
    continuous_obj = _objective(parent_vec[chosen["source"]], w0, payoff, q_draws, weights, alpha)
    certificate = RepairCertificate(
        continuous_objective=continuous_obj,
        repaired_objective=chosen["u_disc"],
        chosen_source=chosen["source"],  # type: ignore[arg-type]
        worst_price_model=_WORST_PRICE_MODEL,
        tick_size_deltas=chosen["rep"]["tick_deltas"],
        min_size_promoted=chosen["rep"]["promoted"],
        dropped_items=chosen["rep"]["dropped"],
        batch_partition=batch_partition,
        safe_prefix_objective_bounds=tuple(chosen["prefix_bounds"]),
        budget_after_repair_usd=cash - chosen["rep"]["spend"],
    )

    return SolutionPlan(
        plan_id=_hash(menu.family_key, menu.menu_hash, sample_hash, q_version, *order_ids),
        family_key=menu.family_key,
        orders=tuple(orders),
        expected_delta_log_wealth=chosen["u_disc"],
        delta_u_baseline_top1=baseline_top1,
        kappa_applied=kappa,
        correlation_rail="caps",
        scenario_provider=provider,
        scenario_sample_hash=sample_hash,
        menu_hash=menu.menu_hash,
        q_version=q_version,
        no_trade_reason=None,
        repair_certificate=certificate,
        diagnostics=diagnostics,
    )


def _read_config_kelly_multiplier() -> float:
    """The downstream kelly_multiplier config factor — the ONE reproducible piece of the submit
    boundary haircut at decide() time (consult REV-2 follow-up judgment call).

    The FULL variance-adjusted haircut (SizingContext / evaluate_kelly, event_reactor_adapter.py
    :5657) also needs bankroll + portfolio-state provider + lead_days that are NOT in the frozen
    :1379 kwargs, so the shim reproduces only the config base factor and the promotion evidence
    grades the ACTUAL submitted size from the settlement receipt. Never invent a bankroll side
    channel. Defaults to 1.0 (the W3 κ posture) if the config is unreadable.
    """
    try:
        from src.config import settings

        value = float(settings["sizing"]["kelly_multiplier"])
        return value if value > 0.0 else 1.0
    except Exception:  # noqa: BLE001 - a config read fault must not crash the decision path
        return 1.0


class SolveEngineShim:
    """Drop-in replacement at the qkernel_spine_bridge.py:1332 construction seam.

    Accepts the SAME constructor surface the bridge passes to FamilyDecisionEngine and the SAME
    decide() call of :1379. It COMPOSES an inner FamilyDecisionEngine for the decision scaffolding
    (predictive, served joint_q/band pass-through, family_book, market_coherence, market_implied_q,
    and the enumerated candidate economics) and REPLACES the selection with the joint solver over
    a SolveMenu built from the same route surface. The primary leg is re-scored STANDALONE at its
    post-downstream-haircut size (``LegacyDecisionProjection``); phase-1 evidence grades that
    projection — its ΔU/size are stamped into ``selected`` so the existing proof overlay / facts
    writer grade the projection, NEVER the joint plan's ΔU (consult REV-2).

    INJECTED INPUTS (W3.3 ruling): ``spendable_cash_provider`` (the CAS ledger's spendable amount,
    net of reservations), ``ledger_snapshot_id_provider``, and optionally a
    ``holdings_snapshot_provider(family_key, ledger_snapshot_id)`` returning exact native YES/NO
    holdings from that same ledger epoch. The endowment wealth VECTOR is the legacy ``portfolio``
    A_y (like-for-like with the picker); holdings are never inferred from it. ``engine`` may be
    injected for tests in place of a real FamilyDecisionEngine. Wired behind the
    w3_solve_enabled feature flag: qkernel_spine_bridge.py wraps the engine with this shim at its
    construction seam (:1412) when w3_solve_enabled() is True.
    """

    def __init__(
        self,
        *,
        engine: Any = None,
        spendable_cash_provider: Any = None,
        ledger_snapshot_id_provider: Any = None,
        holdings_snapshot_provider: Any = None,
        **engine_kwargs: Any,
    ) -> None:
        self._engine = engine
        self._engine_kwargs = engine_kwargs
        self._spendable_cash_provider = spendable_cash_provider
        self._ledger_snapshot_id_provider = ledger_snapshot_id_provider
        self._holdings_snapshot_provider = holdings_snapshot_provider
        # Route-surface inputs: prefer explicit kwargs, else read them off the composed engine
        # (the seam wraps an already-constructed FamilyDecisionEngine as `engine=` so the bridge
        # edit stays a one-liner — no need to re-pass the builder it already holds).
        self._route_set_builder = engine_kwargs.get("route_set_builder")
        if self._route_set_builder is None and engine is not None:
            self._route_set_builder = getattr(engine, "_route_set_builder", None)
        if "enable_negrisk_routes" in engine_kwargs:
            self._enable_negrisk_routes = bool(engine_kwargs["enable_negrisk_routes"])
        elif engine is not None:
            self._enable_negrisk_routes = bool(getattr(engine, "_enable_negrisk_routes", False))
        else:
            self._enable_negrisk_routes = False
        # Surfaced for tests / audit; the projection VALUES also flow via ``selected`` downstream.
        # ``last_plan`` is the joint SolutionPlan (its ΔU is DISTINCT from the projection's
        # standalone post-haircut ΔU — the two must never be sourced from the same quantity).
        self.last_projection: Any = None
        self.last_plan: Any = None

    def _inner_engine(self) -> Any:
        if self._engine is None:
            from src.decision.family_decision_engine import FamilyDecisionEngine

            self._engine = FamilyDecisionEngine(**self._engine_kwargs)
        return self._engine

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
        """EXACT seam signature (qkernel_spine_bridge.py:1379). Returns a validated FamilyDecision."""
        from dataclasses import replace

        from src.solve.exits import build_wealth_by_atom
        from src.solve.kappa import promotion_window_policy
        from src.solve.menu_adapter import build_solve_menu
        from src.solve.scenario_service import TransitionalIndependentProduct
        from src.solve.types import JointOutcomeAtom, LegacyDecisionProjection

        self.last_projection = None
        self.last_plan = None
        legacy = self._inner_engine().decide(
            case, omega, snapshots,
            portfolio=portfolio, matrix=matrix, captured_at_utc=captured_at_utc,
            sizing_candidates=sizing_candidates, max_stake_usd=max_stake_usd,
            shares_for_routing=shares_for_routing, served_joint_q=served_joint_q,
            served_band=served_band, served_payoff_q_lcb_by_side=served_payoff_q_lcb_by_side,
            current_state_solve=True,
        )

        # Ineligible / no-q path: no belief was integrated — pass the legacy no-trade through.
        if legacy.joint_q is None or legacy.band is None or legacy.family_book is None:
            return validate_family_decision_contract(legacy)

        family_key = str(case.family_id)
        bin_ids = [b.bin_id for b in omega.bins]
        atom_ids = tuple(JointOutcomeAtom.canonical_id({family_key: b}) for b in bin_ids)

        # Same route surface the engine used (phase-1 direct-native).
        route_set = self._route_set_builder(
            legacy.family_book, shares=shares_for_routing, enable_negrisk_routes=self._enable_negrisk_routes
        )

        # Spendable cash and native holdings must come from one ledger generation.  The legacy
        # ``portfolio`` vector is only a compatibility fallback for the pre-wiring test seam; once
        # a holdings provider is present, terminal wealth is derived from exact held YES/NO shares
        # instead of interpreting cost-basis exposure as a settlement payoff.
        spendable = float(self._spendable_cash_provider()) if self._spendable_cash_provider is not None else None
        if spendable is None:
            # No injected ledger read (pre-seam-swap default): fall back to the endowment min so the
            # budget never fabricates spendable cash the ledger has not confirmed.
            spendable = float(min(float(portfolio.a(b)) for b in bin_ids))
        ledger_snapshot_id = (
            self._ledger_snapshot_id_provider() if self._ledger_snapshot_id_provider is not None else None
        )
        holdings = None
        if self._holdings_snapshot_provider is not None:
            holdings = self._holdings_snapshot_provider(family_key, ledger_snapshot_id)
            if holdings is None:
                raise ValueError("holdings_snapshot_provider returned no ledger snapshot")
            if holdings.ledger_snapshot_id != ledger_snapshot_id:
                raise ValueError("holdings and spendable cash use different ledger snapshots")
            holdings_payout = {
                JointOutcomeAtom.canonical_id({family_key: b}): sum(
                    float(holding.shares)
                    for holding in holdings.holdings
                    if (
                        b == holding.bin_id
                        if holding.side == "YES"
                        else b != holding.bin_id
                    )
                )
                for b in bin_ids
            }
            source_positions = tuple(holding.position_id for holding in holdings.holdings)
        else:
            holdings_payout = {
                JointOutcomeAtom.canonical_id({family_key: b}): float(portfolio.a(b)) - spendable
                for b in bin_ids
            }
            source_positions = ()
        wealth = build_wealth_by_atom(
            family_key=family_key, atom_ids=atom_ids, holdings_payout_by_atom_id=holdings_payout,
            spendable_cash_usd=spendable, ledger_snapshot_id=ledger_snapshot_id,
            source_positions=source_positions,
        )
        menu = build_solve_menu(
            route_set,
            family_key=family_key,
            family_book=legacy.family_book,
            holdings=holdings,
            wealth=wealth,
        )

        scenarios = TransitionalIndependentProduct()
        bands_by_family = {family_key: legacy.band}
        q_version = str(legacy.joint_q.identity_hash)
        plan = solve(
            menu, scenarios=scenarios, wealth=wealth, kappa_policy=promotion_window_policy(),
            bands_by_family=bands_by_family, q_version=q_version,
        )
        self.last_plan = plan

        # Re-score every native leg from CURRENT state only.  The composed legacy engine is
        # scaffolding (predictive/q/book/route construction); its settlement-fitted reliability,
        # selection-calibrator, direction and market-coherence verdicts have no authority in W3.
        candidate_decisions = self._current_candidate_decisions(
            legacy=legacy,
            matrix=matrix,
            portfolio=portfolio,
            sizing_candidates=sizing_candidates,
            max_stake_usd=max_stake_usd,
            served_payoff_q_lcb_by_side=served_payoff_q_lcb_by_side,
            replace=replace,
        )
        econ_by_route = {d.economics.route_id: d.economics for d in candidate_decisions}

        selected, no_trade_reason, projection = self._project_primary_leg(
            plan=plan, menu=menu, wealth=wealth, scenarios=scenarios, bands_by_family=bands_by_family,
            atom_ids=atom_ids, econ_by_route=econ_by_route, replace=replace,
            LegacyDecisionProjection=LegacyDecisionProjection,
        )
        self.last_projection = projection

        if selected is not None:
            candidate_decisions = tuple(
                replace(d, economics=selected)
                if d.economics.route_id == selected.route_id
                else d
                for d in candidate_decisions
            )

        receipt_hash = _hash(
            legacy.decision_id, plan.plan_id, q_version,
            selected.route_id if selected is not None else f"NO_TRADE:{no_trade_reason}",
        )
        decision = replace(
            legacy,
            selected=selected,
            no_trade_reason=no_trade_reason,
            candidates=tuple(d.economics for d in candidate_decisions),
            candidate_decisions=candidate_decisions,
            receipt_hash=receipt_hash,
        )
        return validate_family_decision_contract(decision)

    @staticmethod
    def _current_candidate_decisions(
        *,
        legacy,
        matrix,
        portfolio,
        sizing_candidates,
        max_stake_usd,
        served_payoff_q_lcb_by_side,
        replace,
    ):
        """Return symmetric YES/NO economics from the served band and live cost curves.

        Missing or malformed current sizing evidence removes the leg from the executable menu.
        ``served_payoff_q_lcb_by_side`` belongs to the pre-W3 proof surface and may carry a
        settlement-history coverage shrink.  Current-state W3 instead derives every YES/NO
        lower bound from ``legacy.band.samples`` inside ``compute_candidate_economics`` so the
        local certificate and the global auction consume the same decision-time simplex.
        """
        from src.decision.family_decision_engine import (
            DAY0_REMAINING_DAY_GUARD_BASIS,
        )
        from src.decision.payoff_vector import compute_candidate_economics

        sample_hash = str(getattr(legacy.band, "sample_hash", "") or "")
        n_draws = int(getattr(getattr(legacy.band, "samples", None), "shape", (0,))[0] or 0)
        alpha = float(getattr(legacy.band, "alpha", 0.05) or 0.05)
        current = []
        for decision in legacy.candidate_decisions:
            route = decision.route
            sizing = sizing_candidates.get((route.bin_id, route.side))
            if sizing is None or not sizing.is_tradeable:
                continue
            try:
                economics = compute_candidate_economics(
                    route,
                    joint_q=legacy.joint_q,
                    band=legacy.band,
                    sizing_candidate=sizing,
                    matrix=matrix,
                    exposure=portfolio,
                    max_stake_usd=max_stake_usd,
                    alpha=alpha,
                    guarded_payoff_q_lcb=None,
                )
            except Exception:  # noqa: BLE001 - missing current economics is a fail-closed leg.
                continue
            hard_fact = (
                str(getattr(decision, "q_lcb_guard_basis", "") or "")
                == DAY0_REMAINING_DAY_GUARD_BASIS
                and str(getattr(decision, "selection_guard_basis", "") or "")
                == DAY0_REMAINING_DAY_GUARD_BASIS
                and str(getattr(decision, "q_lcb_guard_cell_key", "") or "")
                == "day0_monotone_hard_fact_q_lcb"
                and str(getattr(decision, "selection_guard_cell_key", "") or "")
                == "day0_monotone_hard_fact_q_lcb"
                and getattr(decision, "q_lcb_guard_abstained", None) is False
                and getattr(decision, "selection_guard_abstained", None) is False
            )
            guard_basis = (
                DAY0_REMAINING_DAY_GUARD_BASIS
                if hard_fact
                else CURRENT_POSTERIOR_BAND_BASIS
            )
            guard_cell = (
                "day0_monotone_hard_fact_q_lcb" if hard_fact else sample_hash
            )
            current.append(
                replace(
                    decision,
                    economics=economics,
                    direction_law_ok=True,
                    coherence_allows=True,
                    q_lcb_guard_basis=guard_basis,
                    q_lcb_guard_abstained=False,
                    q_lcb_guard_cell_key=guard_cell,
                    selection_guard_basis=guard_basis,
                    selection_guard_abstained=False,
                    selection_guard_cell_key=guard_cell,
                    selection_guard_n=n_draws,
                    selection_guard_q_safe=economics.payoff_q_lcb,
                )
            )
        return tuple(current)

    def _project_primary_leg(
        self, *, plan, menu, wealth, scenarios, bands_by_family, atom_ids, econ_by_route, replace,
        LegacyDecisionProjection,
    ):
        """Phase-1 selection: derive the primary leg, re-score it STANDALONE at its post-haircut
        size, and gate on ``phase1_tradeable``. Returns ``(selected, no_trade_reason, projection)``.
        """
        from decimal import Decimal

        haircut = _read_config_kelly_multiplier()

        if not plan.orders:
            projection = LegacyDecisionProjection(
                primary_order_id=None, projected_selected=None, standalone_primary_delta_u=0.0,
                projection_reason=plan.no_trade_reason or "NO_TRADE", downstream_haircut_alive=True,
                submitted_size_after_haircut=Decimal("0"),
            )
            return None, (plan.no_trade_reason or "NO_IMPROVING_DISCRETE_PLAN"), projection

        primary = min(plan.orders, key=lambda o: o.safe_prefix_index)  # safe_prefix_index 0
        econ = econ_by_route.get(primary.menu_item_id)

        # Standalone re-score of the primary leg ALONE at the post-haircut size.
        scenario_set = scenarios.scenarios(bands_by_family)
        q_draws = scenario_set.q_draws
        weights = (
            scenario_set.draw_weights if scenario_set.draw_weights is not None
            else np.ones(q_draws.shape[0], dtype=np.float64)
        )
        alpha = scenario_set.alpha
        w0, payoff, caps, costs, items = _build_arrays(menu, wealth, atom_ids)
        idx = next((i for i, it in enumerate(items) if it.item_id == primary.menu_item_id), None)
        post_haircut_units = Decimal(str(float(primary.size) * haircut))
        standalone_du = float("-inf")
        if idx is not None:
            x = np.zeros(payoff.shape[0], dtype=np.float64)
            x[idx] = float(post_haircut_units)
            standalone_du = _objective(x, w0, payoff, q_draws, weights, alpha)

        direct_executable = primary.kind in ("buy_yes", "buy_no") and idx is not None
        projection = LegacyDecisionProjection(
            primary_order_id=primary.order_id,
            projected_selected=primary.menu_item_id,
            standalone_primary_delta_u=standalone_du,
            projection_reason="PHASE1_PRIMARY_LEG",
            downstream_haircut_alive=True,
            submitted_size_after_haircut=post_haircut_units,
        )
        # Phase-1 gate: primary leg must be direct-executable AND still improving alone post-haircut.
        if not (direct_executable and projection.phase1_tradeable):
            return None, "PHASE1_PRIMARY_LEG_NOT_TRADEABLE", projection

        # Stamp the PROJECTION (standalone post-haircut ΔU + size) into `selected` so downstream
        # evidence grades the executed leg, never the joint plan's ΔU. Size in USD = units × cost.
        unit_cost = float(items[idx].unit_payoff.unit_cost_usd)
        post_haircut_stake_usd = Decimal(str(float(post_haircut_units) * unit_cost))
        if econ is not None:
            selected = replace(
                econ, optimal_stake_usd=post_haircut_stake_usd, optimal_delta_u=standalone_du,
            )
        else:
            selected = None
            return None, "PHASE1_PRIMARY_LEG_ECONOMICS_MISSING", projection
        return selected, None, projection
