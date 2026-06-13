# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: docs/authority/exit_portfolio_execution_authority_2026-06-13.md P1-P2,P5;
#   docs/authority/consult3_exit_portfolio_execution_2026-06-13_raw.txt Q2(a)/(b)/(e);
#   plan docs/operations/current/plans/2026-06-13_horse_race_kelly.md (task #63)
"""Horse-race Kelly: closed-form K-bin mutually-exclusive allocation.

This is the **portfolio-correct replacement** for per-candidate "edge > threshold"
sizing inside one event family (authority §P1). Within a family of K mutually
exclusive ordered bins, exactly one state occurs; a YES contract on bin k costs the
effective price ``p_k`` and pays 1 iff k occurs. Allocate fractions ``f_k >= 0`` and
cash ``s >= 0`` with ``s + Σ f_k = 1``, maximizing the expected log growth

    max_{s,f}  Σ_j q_j · log(s + f_j / p_j),  s + Σ f_j = 1, s,f_j >= 0.

**Why this supersedes the live per-candidate path.** The current sizing path
(``src/strategy/kelly.py::kelly_size`` + ``CandidateEvaluation.robust_kelly_fraction_lcb``)
sizes each bin in ISOLATION against its own price, then the ΔU ranker picks one leg.
Bins do NOT compete for capital and the cash threshold ``s*`` is never solved. Authority
§K2: LCB-Kelly ≡ fractional-Kelly only for ONE isolated binary bet (with an
edge-dependent λ) — so the existing per-candidate ``kelly_multiplier`` λ stacking is
NOT portfolio-valid. The horse-race solves the joint water-filling: ``s*`` is endogenous
(it is the shadow price of cash, NOT a fixed per-candidate edge gate or fixed
``kelly_multiplier``), so bins compete and total exposure is capped by the MATH, not by an
artificial throttle (NO-caps law).

**q input = q_lcb (conservative posterior).** Per the standing q_lcb+Kelly law and the
authority §P1/§K2 derivation, the ``q`` passed to ``horse_race_allocation`` is the
conservative lower-bound posterior (``q_lcb_5pct`` on the live path), NOT the point
posterior. This keeps the allocation consistent with the rest of the chain.

Closed form (authority §P1, reference impl consult3 raw §"Q2 reference implementation"):
    * not overround (Σ p_k <= 1):  f_k* = q_k, s* = max(0, 1 - Σ q_k).
    * overround:  active set A(s) = {k : q_k/p_k > s};
                  s* = (1 - Σ_{A} q_k) / (1 - Σ_{A} p_k);
                  f_k* = (q_k - p_k·s*)_+.
    * no-bet region:  max_k q_k/p_k <= 1  ->  all f_k* = 0, s* = 1.

All functions are PURE (no I/O, no settings reads, no DB). The reactor wires the shadow
compute + flag gate; this module only does the math.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = [
    "HorseRaceAllocation",
    "horse_race_allocation",
    "binary_second_moment",
    "portfolio_qp_allocation",
    "dominance_lp_check",
]


@dataclass(frozen=True)
class HorseRaceAllocation:
    """Result of the closed-form K-bin horse-race Kelly allocation.

    Attributes:
        f: per-bin wealth fraction allocated to YES on bin k (>= 0), length K.
        s_cash: residual cash fraction (>= 0). ``s_cash + f.sum() == 1`` (up to tol).
        regime: which branch produced the result — one of
            ``"underround"`` (Σp<=1, full f=q), ``"overround"`` (active-set water-fill),
            or ``"no_bet"`` (max q/p <= 1, all cash).
        active_count: number of bins with f_k > 0 (the active set size).
        expected_log_growth: Σ_k q_k·log(s + f_k/p_k) at the solution (objective value).
    """

    f: tuple[float, ...]
    s_cash: float
    regime: str
    active_count: int
    expected_log_growth: float


def _expected_log_growth(
    q: np.ndarray, p: np.ndarray, f: np.ndarray, s_cash: float, tol: float
) -> float:
    """Σ_j q_j·log(s + f_j/p_j) over outcomes with positive mass.

    Returns ``-inf`` if any positive-mass outcome has non-positive wealth (the
    log is undefined / the allocation bankrupts a possible state).
    """
    wealth = s_cash + f / p
    mask = q > tol
    if np.any(wealth[mask] <= 0.0):
        return float("-inf")
    return float(np.sum(q[mask] * np.log(wealth[mask])))


def horse_race_allocation(
    p,
    q,
    *,
    tol: float = 1e-10,
    normalize_q: bool = True,
) -> HorseRaceAllocation:
    """Exact closed-form Kelly allocation for a mutually-exclusive K-outcome family.

    Args:
        p: effective YES ask prices per $1 payout (incl. fees), length K. All > 0.
        q: posterior probabilities for each bin (use **q_lcb**, the conservative
           lower-bound posterior, on the live path). Length K.
        tol: numerical tolerance for the active-set inequalities and feasibility.
        normalize_q: when True (default), q is renormalized to sum to 1 over the
           K bins. The horse-race objective assumes Σq = 1 (exactly one of the K
           bins occurs). When the K bins do NOT cover the outcome space (e.g. an
           OUTSIDE residual carries mass), pass ``normalize_q=False`` and include
           the residual outcome's probability implicitly via a higher Σp<1 cash
           preference — but for a complete family Σq should be ~1. See note below.

    Returns:
        HorseRaceAllocation. ``f`` and ``s_cash`` are wealth fractions summing to 1.

    Raises:
        ValueError: if p and q lengths differ, K==0, or any p_k <= 0.

    Note on normalization: authority §P1 states the K-bin family as a complete
    partition (Σq=1). When q is a raw per-bin posterior vector that may not sum to
    exactly 1 (float drift, or a partially-covered family), ``normalize_q=True``
    projects it onto the simplex so the closed form is well-posed. The active-set
    fixed point is scale-invariant in q up to the s* numerator/denominator, so the
    normalization only affects the no-bet vs bet boundary, not the relative tilt.
    """
    q = np.asarray(q, dtype=float).ravel()
    p = np.asarray(p, dtype=float).ravel()
    if q.shape[0] != p.shape[0]:
        raise ValueError(f"p and q length mismatch: {p.shape[0]} != {q.shape[0]}")
    K = q.shape[0]
    if K == 0:
        raise ValueError("horse_race_allocation requires K >= 1 bins")
    if np.any(p <= 0.0):
        raise ValueError("all effective prices p must be strictly positive")
    if np.any(q < -tol):
        raise ValueError("posterior q must be non-negative")
    q = np.maximum(q, 0.0)

    q_sum = float(q.sum())
    if normalize_q:
        if q_sum <= tol:
            # Degenerate: no probability mass anywhere -> hold all cash.
            return HorseRaceAllocation(
                f=tuple([0.0] * K), s_cash=1.0, regime="no_bet",
                active_count=0, expected_log_growth=0.0,
            )
        q = q / q_sum

    ratios = q / p

    # No-bet region: no bin has positive expected edge per dollar.
    # max_k q_k/p_k <= 1  ->  all-cash is optimal (authority §P1).
    if float(np.max(ratios)) <= 1.0 + tol:
        return HorseRaceAllocation(
            f=tuple([0.0] * K), s_cash=1.0, regime="no_bet",
            active_count=0, expected_log_growth=0.0,
        )

    # Underround / fair: Σp <= 1 -> the no-cash all-active Kelly solution f_k = q_k.
    # (Cash is the residual 1 - Σq, which is 0 when Σq=1 — i.e. s*=0 for a complete
    # normalized family. We keep max(0, ...) for robustness to un-normalized q.)
    if float(p.sum()) <= 1.0 + tol:
        f = q.copy()
        s_cash = max(0.0, 1.0 - float(f.sum()))
        active = int(np.count_nonzero(f > tol))
        return HorseRaceAllocation(
            f=tuple(float(x) for x in f), s_cash=float(s_cash), regime="underround",
            active_count=active,
            expected_log_growth=_expected_log_growth(q, p, f, s_cash, tol),
        )

    # Overround: solve the active-set fixed point. Bins ordered by q/p descending;
    # scan candidate active sets A = top-m bins and keep the feasible one with the
    # best objective (authority reference impl). The fixed point is:
    #   s* = (1 - Σ_A q) / (1 - Σ_A p),  active iff q_k/p_k > s*.
    order = np.argsort(-ratios)
    best: tuple[float, np.ndarray, float, int] | None = None

    for m in range(K + 1):
        A = order[:m]
        Q_A = float(q[A].sum()) if m else 0.0
        P_A = float(p[A].sum()) if m else 0.0
        if P_A >= 1.0 - tol:
            # Active set prices saturate the budget -> s* denominator non-positive.
            continue
        s = (1.0 - Q_A) / (1.0 - P_A)
        if s < -tol or s > 1.0 + tol:
            continue
        # Consistency: every bin in A must satisfy q_k/p_k > s (>= with tol),
        # every bin outside A must satisfy q_k/p_k <= s.
        min_active = float(ratios[A].min()) if m else float("inf")
        max_inactive = float(ratios[order[m:]].max()) if m < K else float("-inf")
        if min_active + tol >= s and s + tol >= max_inactive:
            f = np.maximum(q - p * s, 0.0)
            s_cash = 1.0 - float(f.sum())
            obj = _expected_log_growth(q, p, f, s_cash, tol)
            if best is None or obj > best[0]:
                best = (obj, f, s_cash, m)

    if best is None:
        # No feasible active set (should not happen given the no-bet guard above);
        # fail safe to all-cash.
        return HorseRaceAllocation(
            f=tuple([0.0] * K), s_cash=1.0, regime="no_bet",
            active_count=0, expected_log_growth=0.0,
        )

    obj, f, s_cash, _m = best
    s_cash = max(0.0, float(s_cash))
    active = int(np.count_nonzero(f > tol))
    return HorseRaceAllocation(
        f=tuple(float(x) for x in f), s_cash=s_cash, regime="overround",
        active_count=active, expected_log_growth=float(obj),
    )


# ---------------------------------------------------------------------------
# P2 — cross-family correlation second-order (mean-variance) Kelly QP.
# ---------------------------------------------------------------------------

def binary_second_moment(
    q,
    p,
    corr=None,
    payout=None,
):
    """Build (mu, M) for YES-like binary contracts (authority §P2 reference impl).

    For each contract i, the per-dollar return is ``X_i = (payout_i/p_i)·Y_i - 1``
    where ``Y_i`` is the {0,1} settlement indicator with P(Y_i=1)=q_i. The
    second-order Kelly objective is ``max_f  mu^T f - ½ f^T M f`` with
    ``mu = E[X]`` and ``M = E[X X^T]``.

    Cross-contract covariance uses the binary-moment construction
    ``Cov(Y_i,Y_j) = R_ij·√(q_i(1-q_i) q_j(1-q_j))`` (authority §P2), Fréchet-clipped
    so the implied joint indicator distribution is feasible
    (``max(0, q_i+q_j-1) <= E[Y_i Y_j] <= min(q_i, q_j)``).

    Args:
        q: marginal YES probabilities (use q_lcb on the live path), length N.
        p: effective YES prices, length N.
        corr: N×N correlation matrix of the indicators Y (must be locally valid /
            projected). None -> identity (independent).
        payout: per-contract payout per $1 (None -> all ones).

    Returns:
        (mu, M): mu length N, M is N×N.
    """
    q = np.asarray(q, dtype=float).ravel()
    p = np.asarray(p, dtype=float).ravel()
    N = q.shape[0]
    if p.shape[0] != N:
        raise ValueError(f"p and q length mismatch: {p.shape[0]} != {N}")
    if np.any(p <= 0.0):
        raise ValueError("all prices p must be strictly positive")
    payout = np.ones(N) if payout is None else np.asarray(payout, dtype=float).ravel()
    corr = np.eye(N) if corr is None else np.asarray(corr, dtype=float)

    scale = payout / p
    mu = scale * q - 1.0

    # E[Y_i Y_j] = q_i q_j + Cov, Cov = R_ij·sd_i·sd_j
    sd = np.sqrt(np.clip(q * (1.0 - q), 0.0, None))
    EYij = np.outer(q, q) + corr * np.outer(sd, sd)
    # Exact diagonal: E[Y_i^2] = E[Y_i] = q_i.
    np.fill_diagonal(EYij, q)
    # Fréchet bounds on E[Y_i Y_j].
    lower = np.maximum(0.0, q[:, None] + q[None, :] - 1.0)
    upper = np.minimum(q[:, None], q[None, :])
    EYij = np.minimum(np.maximum(EYij, lower), upper)

    # M = E[X X^T], X_i = scale_i·Y_i - 1.
    M = np.outer(scale, scale) * EYij
    M -= np.outer(scale * q, np.ones(N))
    M -= np.outer(np.ones(N), scale * q)
    M += 1.0
    return mu, M


def portfolio_qp_allocation(
    q,
    p,
    corr=None,
    payout=None,
    *,
    budget: float = 1.0,
    bounds=None,
):
    """Second-order (mean-variance) Kelly QP across correlated binary contracts (§P2).

    Solves ``max_f  mu^T f - ½ f^T M f`` subject to ``Σf <= budget`` and ``f >= 0``
    (long-only YES tilts; the caller may pass explicit bounds for NO legs). This is
    the tractable approximation to joint Kelly for cross-family same-day weather
    regimes. **License (authority §P2)**: only trust the QP when (i) the posterior UB
    on ``ρ = max|f^T X|`` is < 1, (ii) the Taylor-remainder UB is below the objective
    margin to the next materially-different portfolio, and (iii) the active set is
    invariant over posterior draws — else fall back to exact Monte-Carlo scenario
    Kelly. The license check is the CALLER's responsibility (this pass leaves the QP
    shadow/optional; the family-level horse-race is the priority).

    Requires scipy. Raises ``RuntimeError`` if the solver fails.
    """
    from scipy.optimize import minimize

    mu, M = binary_second_moment(q, p, corr=corr, payout=payout)
    N = mu.shape[0]
    if bounds is None:
        bounds = [(0.0, budget) for _ in range(N)]

    def obj(f):
        return -(mu @ f - 0.5 * f @ M @ f)

    def grad(f):
        return -(mu - M @ f)

    cons = [{"type": "ineq", "fun": lambda f: budget - float(np.sum(f))}]
    res = minimize(
        obj, np.zeros(N), jac=grad, bounds=bounds, constraints=cons, method="SLSQP"
    )
    if not res.success:
        raise RuntimeError(f"portfolio_qp_allocation SLSQP failed: {res.message}")
    return np.asarray(res.x, dtype=float)


def taylor_remainder_bound(payoff_states, f) -> float:
    """Upper bound on the 3rd-order Taylor remainder of log(1+Z), Z = X f (§P2).

    Used to LICENSE the QP approximation: if this bound exceeds the utility margin
    to the next candidate portfolio, the QP is not trustworthy and an exact
    scenario Kelly should be used instead. Returns ``inf`` when |Z| reaches 1.
    """
    X = np.asarray(payoff_states, dtype=float)
    z = X @ np.asarray(f, dtype=float).ravel()
    rho = float(np.max(np.abs(z))) if z.size else 0.0
    if rho >= 1.0:
        return float("inf")
    return float(np.mean(np.abs(z) ** 3 / (3.0 * (1.0 - rho))))


# ---------------------------------------------------------------------------
# P5 — dominance / arbitrage LP pre-check (run BEFORE Kelly).
# ---------------------------------------------------------------------------

def dominance_lp_check(
    payoff_matrix,
    costs,
    candidate,
    *,
    tol: float = 1e-9,
):
    """Cheap LP: is a candidate payoff vector replicable more cheaply by a basket? (§P5).

    Authority §P5 / Q2(e): YES(a)+NO(b) on adjacent bins CAN be growth-optimal, but a
    candidate must NOT be entered if its payoff is DOMINATED — reproducible at lower
    cost by a non-negative basket of the other available contracts. For a candidate
    portfolio ``h`` with payoff vector ``A h`` and cost ``c^T h``, solve

        min_{x >= 0}  c^T x   s.t.  A x >= A h.

    If the optimum cost is strictly less than ``c^T h`` (by more than ``tol``), the
    candidate is dominated (an arbitrage-cheaper replication exists) and must be
    rejected before sizing.

    Args:
        payoff_matrix: A, shape (K_states, J_contracts) — column j is contract j's
            payoff per $1 across the K mutually-exclusive states.
        costs: c, length J — effective ask cost per unit of each contract.
        candidate: h, length J — the candidate portfolio's unit weights (the basket
            being checked; e.g. a one-hot vector for a single bin, or the YES(a)+NO(b)
            pair). Its target payoff is ``A @ candidate``.
        tol: cost-improvement threshold to call something "dominated".

    Returns:
        dict with keys: ``dominated`` (bool), ``candidate_cost`` (float),
        ``replication_cost`` (float or None if LP infeasible/failed),
        ``reason`` (str).

    Requires scipy. Fails OPEN (``dominated=False``) if the LP cannot be solved —
    a dominance check that errors must never block a trade by itself.
    """
    A = np.asarray(payoff_matrix, dtype=float)
    c = np.asarray(costs, dtype=float).ravel()
    h = np.asarray(candidate, dtype=float).ravel()
    if A.ndim != 2:
        raise ValueError("payoff_matrix must be 2-D (states x contracts)")
    K_states, J = A.shape
    if c.shape[0] != J or h.shape[0] != J:
        raise ValueError("costs and candidate must have one entry per contract (column)")

    candidate_cost = float(c @ h)
    target = A @ h  # required payoff per state

    try:
        from scipy.optimize import linprog

        # min c^T x  s.t.  A x >= target, x >= 0  ->  -A x <= -target.
        res = linprog(
            c=c,
            A_ub=-A,
            b_ub=-target,
            bounds=[(0.0, None)] * J,
            method="highs",
        )
    except Exception as exc:  # noqa: BLE001 — fail open; never block on a checker error
        return {
            "dominated": False,
            "candidate_cost": candidate_cost,
            "replication_cost": None,
            "reason": f"lp_error:{type(exc).__name__}",
        }

    if not res.success:
        return {
            "dominated": False,
            "candidate_cost": candidate_cost,
            "replication_cost": None,
            "reason": f"lp_not_solved:{getattr(res, 'message', 'unknown')}",
        }

    replication_cost = float(res.fun)
    dominated = replication_cost < candidate_cost - tol
    return {
        "dominated": dominated,
        "candidate_cost": candidate_cost,
        "replication_cost": replication_cost,
        "reason": (
            "dominated_cheaper_replication"
            if dominated
            else "not_dominated"
        ),
    }
