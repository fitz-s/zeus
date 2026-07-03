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

# Venue discretization: sizes on a 0.01 grid; the W2.1 batch executor submits ≤15 per plan.
_SIZE_QUANTUM = Decimal("0.01")
_MAX_ORDERS = 15

_WORST_PRICE_MODEL = "avg_cost_size_aware_depth_capped_v1"

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
    """
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
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list]:
    """Baseline wealth ``W0``, unit-payoff matrix ``P`` (n_items × n_atoms), depth caps.

    Validates that every atom has a strictly positive endowment (else ``ZeroWealthOutcomeError``)
    and that the wealth state covers the scenario atom axis.
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
    for i, it in enumerate(items):
        payoff[i] = it.unit_payoff.vector(atom_ids)
        caps[i] = float(it.max_units)
    return w0, payoff, caps, items


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


def _feasible_hi(i: int, x: np.ndarray, w0: np.ndarray, payoff: np.ndarray, caps: np.ndarray) -> float:
    """Largest stake for coordinate ``i`` (others fixed) keeping every atom's wealth > 0."""
    base = w0 + x @ payoff - x[i] * payoff[i]
    p_i = payoff[i]
    losing = p_i < 0.0
    hi = float(caps[i])
    if losing.any():
        ruin = base[losing] / (-p_i[losing])
        hi = min(hi, float(ruin.min()) * (1.0 - _WEALTH_MARGIN))
    return max(hi, 0.0)


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

    best_u = -np.inf
    best_x = 0.0
    span_lo, span_hi = 0.0, hi
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
            u = _u(val)
            if u > pass_best_u:
                pass_best_u = u
                pass_best_x = val
            val += step
        if pass_best_u > best_u:
            best_u = pass_best_u
            best_x = pass_best_x
        span_lo = max(0.0, pass_best_x - step)
        span_hi = min(hi, pass_best_x + step)
        steps = _REFINE_STEPS
    return best_x, float(best_u)


def _optimize_continuous(
    w0: np.ndarray,
    payoff: np.ndarray,
    caps: np.ndarray,
    q_draws: np.ndarray,
    weights: np.ndarray,
    alpha: float,
) -> tuple[np.ndarray, float, np.ndarray, float]:
    """Joint continuous optimum + the best single-item (top-1 picker) optimum.

    Returns ``(x_joint, U_joint, x_top1, U_top1)``; ``x_joint`` is the coordinate-ascent optimum
    seeded at the best single item, so ``U_joint ≥ U_top1`` always (the dominance guarantee).
    Because the CVaR objective is concave, the ascent reaches the global optimum.
    """
    n_items = payoff.shape[0]
    zeros = np.zeros(n_items, dtype=np.float64)
    if n_items == 0:
        return zeros, 0.0, zeros.copy(), 0.0

    best_single_u = 0.0
    x_top1 = zeros.copy()
    for i in range(n_items):
        hi = _feasible_hi(i, zeros, w0, payoff, caps)
        xi, ui = _grid_max_coordinate(i, zeros, hi, w0, payoff, q_draws, weights, alpha)
        if ui > best_single_u:
            best_single_u = ui
            x_top1 = zeros.copy()
            x_top1[i] = xi

    x = x_top1.copy()
    u_cur = _objective(x, w0, payoff, q_draws, weights, alpha)
    for _sweep in range(_MAX_SWEEPS):
        sweep_gain = 0.0
        for i in range(n_items):
            hi = _feasible_hi(i, x, w0, payoff, caps)
            xi, ui = _grid_max_coordinate(i, x, hi, w0, payoff, q_draws, weights, alpha)
            if ui > u_cur + _CONVERGENCE_TOL:
                sweep_gain += ui - u_cur
                x[i] = xi
                u_cur = ui
        if sweep_gain < _CONVERGENCE_TOL:
            break
    return x, float(u_cur), x_top1, float(best_single_u)


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
    q_draws: np.ndarray,
    weights: np.ndarray,
    alpha: float,
    kappa: float,
) -> dict:
    """κ-scale, quantize on each item's own grid, cap at _MAX_ORDERS, re-evaluate worst-price.

    Returns a dict with the discrete stake vector, its re-evaluated CVaR ΔU, the surviving
    ``(item_index, size)`` list, and the RepairCertificate provenance (deltas / promoted /
    dropped). The caller trades only if ``u_disc > 0``.
    """
    n_items = payoff.shape[0]
    scaled = kappa * x_cont
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
        def _marginal(idx_size: tuple[int, Decimal]) -> float:
            i, size = idx_size
            xi = np.zeros(n_items, dtype=np.float64)
            xi[i] = float(size)
            return _objective(xi, w0, payoff, q_draws, weights, alpha)

        sized_sorted = sorted(sized, key=_marginal, reverse=True)
        for i, _s in sized_sorted[_MAX_ORDERS:]:
            dropped.append((items[i].item_id, "batch_cap_15"))
        sized = sized_sorted[:_MAX_ORDERS]

    x_disc = np.zeros(n_items, dtype=np.float64)
    for i, size in sized:
        x_disc[i] = float(size)
    u_disc = _objective(x_disc, w0, payoff, q_draws, weights, alpha)
    return {
        "x_disc": x_disc,
        "u_disc": u_disc,
        "sized": sized,
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


def _quantize_price(price: Optional[Decimal], min_tick_size: Decimal) -> Optional[Decimal]:
    if price is None:
        return None
    if min_tick_size <= 0:
        return Decimal(price)
    return (Decimal(price) / min_tick_size).to_integral_value(rounding=ROUND_HALF_EVEN) * min_tick_size


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

    w0, payoff, caps, items = _build_arrays(menu, wealth, atom_ids)
    provider = scenario_set.provider
    sample_hash = scenario_set.scenario_hash

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
        return _no_trade("NO_EXECUTABLE_MENU_ITEMS", 0.0, {"n_items": 0.0})

    x_joint, u_joint, x_top1, u_top1 = _optimize_continuous(w0, payoff, caps, q_draws, weights, alpha)

    rep_joint = _repair(x_joint, items=items, w0=w0, payoff=payoff, q_draws=q_draws, weights=weights, alpha=alpha, kappa=kappa)
    rep_top1 = _repair(x_top1, items=items, w0=w0, payoff=payoff, q_draws=q_draws, weights=weights, alpha=alpha, kappa=kappa)
    baseline_top1 = rep_top1["u_disc"]

    chosen = rep_joint if rep_joint["u_disc"] >= rep_top1["u_disc"] else rep_top1
    chosen_u = chosen["u_disc"]
    continuous_obj = _objective(kappa * x_joint, w0, payoff, q_draws, weights, alpha)

    diagnostics = {
        "continuous_delta_u_joint": u_joint,
        "continuous_delta_u_top1": u_top1,
        "discrete_delta_u_joint": rep_joint["u_disc"],
        "discrete_delta_u_top1": rep_top1["u_disc"],
        "continuous_units_total": float(x_joint.sum()),
        "n_menu_items": float(len(items)),
        "n_draws": float(n_draws),
        "alpha": float(alpha),
    }

    if not chosen["sized"] or not chosen_u > 0.0:
        return _no_trade("NO_IMPROVING_DISCRETE_PLAN", baseline_top1, diagnostics)

    # Safe-prefix ordering: most-improving order first, so every filled prefix improves (W2.1).
    def _marginal(idx_size: tuple[int, Decimal]) -> float:
        i, size = idx_size
        xi = np.zeros(payoff.shape[0], dtype=np.float64)
        xi[i] = float(size)
        return _objective(xi, w0, payoff, q_draws, weights, alpha)

    ordered = sorted(chosen["sized"], key=_marginal, reverse=True)

    orders: list[PlannedOrder] = []
    prefix_bounds: list[float] = []
    spent = 0.0
    running = np.zeros(payoff.shape[0], dtype=np.float64)
    for prefix_index, (i, size) in enumerate(ordered):
        it = items[i]
        running[i] = float(size)
        prefix_bounds.append(_objective(running, w0, payoff, q_draws, weights, alpha))
        spent += float(size) * float(it.unit_payoff.unit_cost_usd)
        route = it.route
        token_id = None
        price = None
        if route is not None:
            legs = getattr(route, "legs", ())
            if legs:
                token_id = getattr(legs[0], "token_id", None)
            avg_cost = getattr(route, "avg_cost", None)
            price = _quantize_price(getattr(avg_cost, "value", avg_cost), it.min_tick_size)
        orders.append(
            PlannedOrder(
                order_id=_hash(menu.menu_hash, it.item_id, str(size)),
                menu_item_id=it.item_id,
                kind=it.kind,
                side=_order_side(it.kind),
                token_id=token_id,
                price=price,
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
    certificate = RepairCertificate(
        continuous_objective=continuous_obj,
        repaired_objective=chosen_u,
        worst_price_model=_WORST_PRICE_MODEL,
        tick_size_deltas=chosen["tick_deltas"],
        min_size_promoted=chosen["promoted"],
        dropped_items=chosen["dropped"],
        batch_partition=batch_partition,
        safe_prefix_objective_bounds=tuple(prefix_bounds),
        budget_after_repair_usd=float(wealth.cash_usd) - spent,
    )

    return SolutionPlan(
        plan_id=_hash(menu.family_key, menu.menu_hash, sample_hash, q_version, *order_ids),
        family_key=menu.family_key,
        orders=tuple(orders),
        expected_delta_log_wealth=chosen_u,
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
