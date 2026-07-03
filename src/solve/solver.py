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

import hashlib
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

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

# Venue discretization: sizes on a 0.01 grid; the W2.1 batch executor submits ≤15 per plan.
_SIZE_QUANTUM = Decimal("0.01")
_MAX_ORDERS = 15

_WORST_PRICE_MODEL = "avg_cost_size_aware_depth_capped_v1"

# CVaR tail stability (consult REV-2 follow-up): a robust ΔU at alpha needs enough draws in
# the alpha-tail to be meaningful. Below this the plan is STAMPED (diagnostics) so the promotion
# evidence gate can down-weight it; a one-draw band is stamped point_belief. Not a hard reject.
_MIN_TAIL_DRAWS = 20


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
    and the lower-tail CVaR of concave functions is concave — so the objective is concave and
    coordinate ascent reaches the global optimum. This replaces the raw α-quantile (VaR), whose
    order statistic of concave functions is not concave. ``-inf`` draws (a ruined atom carries
    positive mass) propagate to ``-inf`` correctly.

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
    lo = -xi0 * ci   # t at which new_x_i hits 0
    hi = xj0 * cj    # t at which new_x_j hits 0
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
    Because the CVaR objective is concave, the ascent reaches the global optimum; every coordinate
    respects the depth/wealth/budget feasibility bound. ``sweeps`` is stamped as an optimizer-gap
    diagnostic (converged before ``_MAX_SWEEPS`` ⇒ a stationary point of the concave objective).
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
                        sweep_gain += _pair_exchange(i, j, x, w0, payoff, costs, q_draws, weights, alpha)
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

    return x_a, float(u_a), x_top1, float(best_single_u), total_sweeps[0]


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
        token_id = None
        route = it.route
        if route is not None:
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
    net of reservations — the seam-swap threads the real read; tests inject) and, optionally,
    ``ledger_snapshot_id_provider``. The endowment wealth VECTOR is the legacy ``portfolio`` A_y
    (like-for-like with the picker). ``engine`` may be injected for tests in place of a real
    FamilyDecisionEngine. NOTHING wires this shim yet — the seam swap + G3 harness are the next packet.
    """

    def __init__(
        self,
        *,
        engine: Any = None,
        spendable_cash_provider: Any = None,
        ledger_snapshot_id_provider: Any = None,
        **engine_kwargs: Any,
    ) -> None:
        self._engine = engine
        self._engine_kwargs = engine_kwargs
        self._spendable_cash_provider = spendable_cash_provider
        self._ledger_snapshot_id_provider = ledger_snapshot_id_provider
        self._route_set_builder = engine_kwargs.get("route_set_builder")
        self._enable_negrisk_routes = bool(engine_kwargs.get("enable_negrisk_routes", False))
        # Surfaced for tests / audit; the projection VALUES also flow via ``selected`` downstream.
        self.last_projection: Any = None

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
        legacy = self._inner_engine().decide(
            case, omega, snapshots,
            portfolio=portfolio, matrix=matrix, captured_at_utc=captured_at_utc,
            sizing_candidates=sizing_candidates, max_stake_usd=max_stake_usd,
            shares_for_routing=shares_for_routing, served_joint_q=served_joint_q,
            served_band=served_band, served_payoff_q_lcb_by_side=served_payoff_q_lcb_by_side,
        )

        # Ineligible / no-q path: no belief was integrated — pass the legacy no-trade through.
        if legacy.joint_q is None or legacy.band is None or legacy.family_book is None:
            return validate_family_decision_contract(legacy)

        family_key = str(case.family_id)
        bin_ids = [b.bin_id for b in omega.bins]
        atom_ids = tuple(JointOutcomeAtom.canonical_id({family_key: b}) for b in bin_ids)

        # Same route surface the engine used, reshaped into the solver menu (phase-1 direct-native).
        route_set = self._route_set_builder(
            legacy.family_book, shares=shares_for_routing, enable_negrisk_routes=self._enable_negrisk_routes
        )
        menu = build_solve_menu(
            route_set, family_key=family_key, family_book=legacy.family_book, holdings_by_bin_id={}
        )

        # Endowment wealth = legacy A_y (like-for-like); spendable cash for the budget is INJECTED.
        spendable = float(self._spendable_cash_provider()) if self._spendable_cash_provider is not None else None
        if spendable is None:
            # No injected ledger read (pre-seam-swap default): fall back to the endowment min so the
            # budget never fabricates spendable cash the ledger has not confirmed.
            spendable = float(min(float(portfolio.a(b)) for b in bin_ids))
        ledger_snapshot_id = (
            self._ledger_snapshot_id_provider() if self._ledger_snapshot_id_provider is not None else None
        )
        holdings_payout = {
            JointOutcomeAtom.canonical_id({family_key: b}): float(portfolio.a(b)) - spendable
            for b in bin_ids
        }
        wealth = build_wealth_by_atom(
            family_key=family_key, atom_ids=atom_ids, holdings_payout_by_atom_id=holdings_payout,
            spendable_cash_usd=spendable, ledger_snapshot_id=ledger_snapshot_id,
        )

        scenarios = TransitionalIndependentProduct()
        bands_by_family = {family_key: legacy.band}
        q_version = str(legacy.joint_q.identity_hash)
        plan = solve(
            menu, scenarios=scenarios, wealth=wealth, kappa_policy=promotion_window_policy(),
            bands_by_family=bands_by_family, q_version=q_version,
        )

        # candidate_decisions: coherence lockstep — the shim emits coherence_allows=True (§4 dec 1).
        candidate_decisions = tuple(replace(d, coherence_allows=True) for d in legacy.candidate_decisions)
        econ_by_route = {c.route_id: c for c in legacy.candidates}

        selected, no_trade_reason, projection = self._project_primary_leg(
            plan=plan, menu=menu, wealth=wealth, scenarios=scenarios, bands_by_family=bands_by_family,
            atom_ids=atom_ids, econ_by_route=econ_by_route, replace=replace,
            LegacyDecisionProjection=LegacyDecisionProjection,
        )
        self.last_projection = projection

        receipt_hash = _hash(
            legacy.decision_id, plan.plan_id, q_version,
            selected.route_id if selected is not None else f"NO_TRADE:{no_trade_reason}",
        )
        decision = replace(
            legacy,
            selected=selected,
            no_trade_reason=no_trade_reason,
            candidate_decisions=candidate_decisions,
            receipt_hash=receipt_hash,
        )
        return validate_family_decision_contract(decision)

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
