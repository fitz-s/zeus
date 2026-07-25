# Created: 2026-07-25
# Last reused or audited: 2026-07-25
# Authority basis: measured live walk-forward regression (operator evidence
#   2026-07-25, n=291 settled positions): win-rate vs entry-price by band shows
#   [0,0.2) +1.0pp, [0.2,0.4) -4.7pp, [0.4,0.6) -14.2pp (win 37.0% vs price
#   51.2%, n=54), [0.6,0.8) +8.4pp, [0.8,1.0) +10.1pp. Deep-review-ruled
#   mechanism: SELECTION-CONDITIONED overconfidence — the system only enters at
#   mid-price when its own q disagrees strongly with the market, so q_lcb is
#   not conservative enough conditional on the system's OWN selection policy (a
#   winner's-curse exposure q_lcb cannot see, because the LCB bounds parameter
#   uncertainty of q, not the conditioning event "we chose to trade despite
#   market disagreement").
#
#   Fix: ONE walk-forward calibration debit d_t(s) applied in the entry law's
#   EXISTING margin slot (the `penalty` parameter of
#   `select_mode_consistent_ev`/`select_rest_then_cross_mode`,
#   src/strategy/live_inference/mode_consistent_ev.py, and its sibling in
#   `robust_trade_score`, src/strategy/live_inference/trade_score.py — the
#   SAME slot RULE 1, docs/evidence/deadloop_2026-06-14/trade_score_suppression.md,
#   zeroed out a FIXED, undocumented 0.01 constant from). This debit is NOT
#   that constant reborn: it is walk-forward evidence-derived (recomputed from
#   settled history at every decision, never a fixed literal), state-
#   conditioned (exactly 2 coarse states), shrinks to EXACTLY 0 with no
#   evidence (n=0 -> d_t=0), and is one-sided (max(0, ...) — an underconfident
#   cohort never becomes a size bonus). It owns ONLY the selection-conditioned
#   residual and never touches q_lcb/q_posterior construction (INV-40
#   single-count law; see tests/strategy/test_single_count_uncertainty.py).
"""Selection-conditioned overconfidence debit for the live entry margin slot.

d_t(s) = max(0, shrunk prequential mean of (q_lcb - Y) | s), where:

  s     = coarse decision-state, "ordinary" or "high" model-market
          disagreement: |q_decision - executable_price| >=
          DISAGREEMENT_HIGH_THRESHOLD. Both quantities are the frozen
          decision-time q and the executable entry-quote price ALREADY
          captured for the SAME held token — no new market-price input enters
          probability authority; this only compares two quantities the caller
          already has at decision time.

  Y     = the settled binary outcome of the held side (1 = won), matching the
          `won` column semantics in settlement_attribution: the traded
          direction paid off.

  mean  = over exactly ONE observation per (city, target_date,
          temperature_metric) cluster (never per pair-row / per position), to
          avoid correlated double-count of positions traded on the same
          underlying market instance (the same clustering unit
          settlement_skill_attribution / _load_settlements uses as this
          codebase's "family").

  walk-forward = only clusters whose rows are settled STRICTLY BEFORE the
          current decision time (settled_at < decision_time) — no
          look-ahead. Mirrors the settled-before-decision-time query shape of
          `src.analysis.settlement_skill_attribution._fresh_posterior_for_family`
          (its `before=` cutoff on a time column).

  shrink = lam = n / (n + SHRINKAGE_N0), the same shrink-to-zero shape as
          `eb_bias` (src/forecast/bayes_precision_fusion.py:68-79,
          KAPPA=8.0). SHRINKAGE_N0=20.0 is a fixed a-priori structural
          constant (not tuned per-decision) — larger than KAPPA because this
          debit trades a coarser 2-state split (vs eb_bias's continuous
          per-instrument residual) for a coarser, noisier signal, so more
          evidence should be required before trusting the local mean over 0.

THRESHOLD DERIVATION (DISAGREEMENT_HIGH_THRESHOLD): chosen from a read-only
grid search over the settled sample in `settlement_attribution` (world.db,
run 2026-07-25; one observation per (city, target_date, temperature_metric)
cluster, n=165 usable clusters at that time): the "high" cohort's mean
residual pulls furthest from the "ordinary" cohort's at threshold=0.40 (high:
n=18, mean(q_lcb-won)=0.264; ordinary: n=147, mean(q_lcb-won)=0.155; gap=0.109,
the largest among {0.20, 0.25, 0.30, 0.32, 0.35, 0.38, 0.40, 0.42, 0.45, 0.50}
tested). Below 0.40 the split is noisier (no clean separation); above 0.45 the
high cohort thins to n<=13. This is a fixed a-priori structural constant in
the same spirit as KAPPA=8.0 / TAU0_FLOOR in bayes_precision_fusion.py — not
tuned per-decision, open to re-derivation as the settled sample grows (the
walk-forward query itself is what stays live; only this threshold and N0 are
frozen constants).

OWNERSHIP (INV-40 single-count law): this module owns EXACTLY ONE thing — the
selection-conditioned residual debit applied as a margin subtraction at the
entry law's existing `penalty` slot. It must NEVER be applied inside
q_lcb/q_posterior construction (src/calibration/emos.py,
src/forecast/bayes_precision_fusion.py remain the sole q authority) and must
never be added a second time as a q-side widening for the same decision. A
candidate whose historical cohort shows d_t > 0 is rejected by the entry law
naturally (robust edge <= 0), never by a separate refuse-band, entropy
multiplier, or hard-coded constant.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

# Fixed a-priori structural constants (see module docstring for derivation).
DISAGREEMENT_HIGH_THRESHOLD = 0.40
SHRINKAGE_N0 = 20.0

DecisionState = Literal["ordinary", "high"]


def decision_state(
    q_decision: float, executable_price: float, *, threshold: float = DISAGREEMENT_HIGH_THRESHOLD
) -> DecisionState:
    """Classify a decision into the coarse model-market disagreement state.

    Both inputs are already-captured decision-time quantities in the same
    held-side probability space; this compares them, it does not construct or
    widen either one.
    """
    disagreement = abs(float(q_decision) - float(executable_price))
    return "high" if disagreement >= float(threshold) else "ordinary"


@dataclass(frozen=True)
class SelectionDebit:
    state: DecisionState
    d_t: float
    effective_n: int
    mean_residual: float


def _shrunk_mean(residuals: list[float], *, n0: float) -> tuple[float, float]:
    """(mean_residual, lam) with lam = n / (n + n0); (0.0, 0.0) at n=0."""
    n = len(residuals)
    if n == 0:
        return 0.0, 0.0
    mean_residual = sum(residuals) / n
    lam = n / (n + n0)
    return mean_residual, lam


def compute_selection_debit(
    residuals: list[float],
    *,
    state: DecisionState,
    n0: float = SHRINKAGE_N0,
) -> SelectionDebit:
    """Pure: one-sided shrunk prequential mean of (q_lcb - Y) for one state.

    `residuals` must already be walk-forward-eligible (settled strictly before
    decision time) and cluster-deduped (one residual per (city, target_date,
    temperature_metric) cluster) — this function does no querying and no
    deduplication; it only shrinks and clamps.

    One-sided: max(0, lam * mean_residual) — an underconfident cohort (mean
    residual <= 0) never becomes a size bonus; the debit only ever subtracts
    from G-, and only when settled evidence shows the state has been
    overconfident (q_lcb overstated the realized win rate).
    """
    mean_residual, lam = _shrunk_mean(residuals, n0=n0)
    d_t = max(0.0, lam * mean_residual)
    return SelectionDebit(
        state=state,
        d_t=d_t,
        effective_n=len(residuals),
        mean_residual=mean_residual,
    )


def cluster_residuals_by_state(
    world_conn: sqlite3.Connection,
    *,
    decision_time_iso: str,
    threshold: float = DISAGREEMENT_HIGH_THRESHOLD,
) -> dict[DecisionState, list[float]]:
    """Walk-forward residuals (q_lcb_5pct - won), one per settled
    (city, target_date, temperature_metric) cluster, split by disagreement
    state. Only clusters whose member rows are ALL settled strictly before
    `decision_time_iso` are included (no look-ahead).

    Reads `settlement_attribution` (world.db; sole writer
    src/analysis/settlement_skill_attribution.py) — one row per settled
    position, already carrying q_live (frozen decision q), avg_fill_price (the
    executable entry price captured at fill), q_lcb_5pct, won, city,
    target_date, temperature_metric, settled_at.
    """
    rows = world_conn.execute(
        """
        SELECT city, target_date, temperature_metric, q_live, avg_fill_price,
               q_lcb_5pct, won
        FROM settlement_attribution
        WHERE settled_at IS NOT NULL AND settled_at < ?
          AND q_live IS NOT NULL AND avg_fill_price IS NOT NULL
          AND q_lcb_5pct IS NOT NULL AND won IS NOT NULL
          AND city IS NOT NULL AND target_date IS NOT NULL
          AND temperature_metric IS NOT NULL
        """,
        (decision_time_iso,),
    ).fetchall()

    clusters: dict[tuple[str, str, str], list[tuple[float, float, float, int]]] = {}
    for city, target_date, metric, q_live, avg_fill_price, q_lcb, won in rows:
        key = (str(city), str(target_date), str(metric))
        clusters.setdefault(key, []).append(
            (float(q_live), float(avg_fill_price), float(q_lcb), int(won))
        )

    by_state: dict[DecisionState, list[float]] = {"ordinary": [], "high": []}
    for members in clusters.values():
        cluster_disagreement = sum(abs(q_live - price) for q_live, price, _, _ in members) / len(members)
        cluster_residual = sum(q_lcb - won for _, _, q_lcb, won in members) / len(members)
        state: DecisionState = "high" if cluster_disagreement >= threshold else "ordinary"
        by_state[state].append(cluster_residual)
    return by_state


def load_walk_forward_selection_debit(
    world_conn: sqlite3.Connection,
    *,
    decision_time_iso: str,
    threshold: float = DISAGREEMENT_HIGH_THRESHOLD,
    n0: float = SHRINKAGE_N0,
) -> dict[DecisionState, SelectionDebit]:
    """Both states' walk-forward debits as of one decision time, in one query."""
    by_state = cluster_residuals_by_state(
        world_conn, decision_time_iso=decision_time_iso, threshold=threshold
    )
    return {
        state: compute_selection_debit(residuals, state=state, n0=n0)
        for state, residuals in by_state.items()
    }
