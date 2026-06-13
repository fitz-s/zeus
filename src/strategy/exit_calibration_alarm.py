# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: docs/authority/exit_portfolio_execution_authority_2026-06-13.md
#   E5b (anytime-valid likelihood-ratio e-process; SUSPEND raw-posterior authority
#   when E_n >= h*; h* DERIVED from the Q4d false-alarm vs missed-miscalibration
#   cost functional, NOT fixed at 20 or 1/0.05) + raw doc Q1 Correction 2
#   (e-process definition, Ville time-uniform control) + Q4(d) cost equation.
#   Same anytime-valid substrate as calibration addendum A3 sell-the-mode alarm.
#   Plan: docs/evidence/plans/2026-06-13_exit_capability.md.
"""Anytime-valid exit-calibration alarm (E5b — suspends a miscalibrated posterior).

E2-E4 (src/strategy/exit_policy.py) assume q_t is correct. When it is NOT — the
Denver/4-loss class, agent says "still winning" while the market correctly
disagrees — the exit rules hold the loser. E5a (src/strategy/exit_belief.py)
re-weights toward the market when resolved snapshots license it; E5b is the
COMPLEMENTARY anytime-valid monitor that decides WHEN to stop trusting the raw
posterior at all.

The likelihood-ratio e-process over resolved forecasts i=1..n compares the agent
forecast q_i to an alternative r_i (the fitted market blend):

    E_n = Π_i  [ r_i^{Y_i} (1-r_i)^{1-Y_i} ] / [ q_i^{Y_i} (1-q_i)^{1-Y_i} ]

Under the null "agent q_i are conditionally correct", E_n is a nonnegative
martingale with unit expectation; Ville's inequality gives time-uniform control:
P(∃n : E_n >= h*) <= 1/h*. (Note the orientation: E_n GROWS when the ALTERNATIVE
r explains the outcomes better than the agent q — i.e. evidence that the agent is
miscalibrated. This is the ratio that accumulates against the agent.)

SUSPENSION RULE (E5b): when E_n >= h*, SUSPEND raw-posterior authority and make
the exit rule use the market-blend q_exit (E5a) instead.

h* IS DERIVED, NOT HARDCODED. From the Q4(d) cost functional, the decision to
raise the alarm is licensed when the expected utility of suspending (avoiding the
missed-miscalibration loss c_miss) beats the false-alarm cost c_false plus the
implementation cost c_impl. For a likelihood-ratio test this is the Wald boundary
    h* = c_miss / (c_false + c_impl)
i.e. the e-value must exceed the cost ratio of a missed miscalibration to a false
alarm. Ville's bound then PINS the worst-case false-alarm rate to 1/h* = (c_false
+ c_impl)/c_miss — so the cost inputs, not a habitual "20" or "1/0.05", set both
the threshold and its guaranteed error rate. The caller supplies c_miss/c_false/
c_impl in log-growth units (realized loss of holding a miscalibrated loser vs.
the regret of suspending a correct posterior).

Pure math, no DB, no engine imports. The e-process state (running log E_n and n)
is carried by the caller across cycles / resolved snapshots and shadow-logged.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_EPS = 1e-9


def _clip_prob(q: float, eps: float = _EPS) -> float:
    return float(np.clip(q, eps, 1.0 - eps))


def derive_h_star(c_miss: float, c_false: float, c_impl: float = 0.0) -> float:
    """Cost-derived e-process suspension threshold h* (Q4d, NOT hardcoded).

    h* = c_miss / (c_false + c_impl).

    * c_miss  — utility cost (log-growth units) of a MISSED miscalibration:
                holding a losing position because the raw posterior refused exit.
    * c_false — utility cost of a FALSE alarm: suspending a posterior that was in
                fact correct (and exiting on the market blend prematurely).
    * c_impl  — implementation/operational cost of acting on the alarm.

    Ville's inequality pins the worst-case false-alarm probability to 1/h* =
    (c_false + c_impl) / c_miss. A larger missed-miscalibration cost lowers h*
    (alarm fires on weaker evidence); a larger false-alarm/impl cost raises it.
    There is no habitual "20" or "1/0.05" — the cost inputs set the boundary.
    """
    c_miss = float(c_miss)
    denom = float(c_false) + float(c_impl)
    if c_miss <= 0.0:
        raise ValueError("c_miss must be positive")
    if denom <= 0.0:
        raise ValueError("c_false + c_impl must be positive")
    return c_miss / denom


def log_e_increment(q: float, r: float, y: int) -> float:
    """Single-step log e-process increment for resolved outcome y in {0,1}.

    Δlog E = [ y·log r + (1-y)·log(1-r) ] − [ y·log q + (1-y)·log(1-q) ]

    where q is the agent forecast and r is the alternative (market blend). The
    increment is POSITIVE when the alternative r assigned higher likelihood to the
    realized outcome than the agent q did — evidence the agent is miscalibrated.
    """
    q = _clip_prob(q)
    r = _clip_prob(r)
    yy = 1.0 if int(y) == 1 else 0.0
    ll_alt = yy * np.log(r) + (1.0 - yy) * np.log(1.0 - r)
    ll_agent = yy * np.log(q) + (1.0 - yy) * np.log(1.0 - q)
    return float(ll_alt - ll_agent)


@dataclass(frozen=True)
class AlarmState:
    """Running anytime-valid e-process state for one held-side class."""

    log_e: float            # running log E_n (sum of increments)
    n: int                  # number of resolved observations folded in
    h_star: float           # cost-derived threshold
    suspended: bool         # True ⇒ E_n >= h* ⇒ suspend raw-posterior authority
    e_value: float          # exp(log_e), the current e-value E_n
    source: str


def update_alarm(
    prev_log_e: float,
    prev_n: int,
    q: float,
    r: float,
    y: int,
    *,
    c_miss: float,
    c_false: float,
    c_impl: float = 0.0,
) -> AlarmState:
    """Fold one resolved observation into the e-process and test the suspension.

    Args:
        prev_log_e: running log E_n carried from the previous observation (0.0 at
            the start of a class's monitoring window).
        prev_n: count carried from the previous observation.
        q: agent forecast for THIS resolved snapshot (held-side prob).
        r: alternative (market-blend) forecast for the same snapshot.
        y: realized binary outcome (1 = held side won).
        c_miss, c_false, c_impl: cost inputs for the derived h* (see derive_h_star).

    Returns:
        AlarmState with the updated running log E_n, the e-value, and whether the
        suspension threshold h* has been crossed. The e-process is a nonnegative
        martingale under the null; once suspended it STAYS suspended for the class
        (Ville's any-time guarantee) — the caller need not re-cross every cycle.
    """
    h_star = derive_h_star(c_miss, c_false, c_impl)
    log_e = float(prev_log_e) + log_e_increment(q, r, y)
    n = int(prev_n) + 1
    e_value = float(np.exp(log_e))
    suspended = bool(e_value >= h_star)
    return AlarmState(
        log_e=log_e,
        n=n,
        h_star=h_star,
        suspended=suspended,
        e_value=e_value,
        source=(
            f"exit_alarm n={n} E_n={e_value:.4f} h*={h_star:.4f} "
            f"suspended={suspended}"
        ),
    )


def evaluate_alarm_series(
    q_series,
    r_series,
    y_series,
    *,
    c_miss: float,
    c_false: float,
    c_impl: float = 0.0,
) -> AlarmState:
    """Run the e-process over a whole resolved series (batch / replay convenience).

    Returns the FINAL AlarmState after folding every observation. ``suspended`` is
    True iff the running e-value crossed h* at any point (Ville: once crossed, the
    anytime-valid alarm holds). Useful for the shadow-replay g* / alarm audit and
    for relationship tests.
    """
    q_arr = np.asarray(q_series, dtype=float).ravel()
    r_arr = np.asarray(r_series, dtype=float).ravel()
    y_arr = np.asarray(y_series, dtype=float).ravel()
    if not (q_arr.size == r_arr.size == y_arr.size):
        raise ValueError("q_series, r_series, y_series must be the same length")
    h_star = derive_h_star(c_miss, c_false, c_impl)

    log_e = 0.0
    ever_suspended = False
    n = 0
    for q, r, y in zip(q_arr, r_arr, y_arr):
        log_e += log_e_increment(float(q), float(r), int(round(float(y))))
        n += 1
        if np.exp(log_e) >= h_star:
            ever_suspended = True
    e_value = float(np.exp(log_e))
    return AlarmState(
        log_e=float(log_e),
        n=n,
        h_star=h_star,
        suspended=bool(ever_suspended),
        e_value=e_value,
        source=(
            f"exit_alarm_series n={n} E_n={e_value:.4f} h*={h_star:.4f} "
            f"ever_suspended={ever_suspended}"
        ),
    )
