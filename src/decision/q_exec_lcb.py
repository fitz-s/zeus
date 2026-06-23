# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: chatgpt-consult round-3 (rid REQ-20260623-040544-df089d) commit-1 spec; real-chain
#   money-path audit docs/evidence/live_order_pathology/2026-06-23_end_to_end_moneypath_audit.md
#   (q over-confidence: buy_no realized ~65% vs served q_lcb ~0.83; 5-15% claimed-edge band loses).
#   Operator law: no flag/shadow/default-off, no abstain-to-halt, no fixed buckets, no N_MIN, no
#   edge floor, no overfit. Reuses ONLY the pure math (beta_lower_bound_95) from selection_calibrator,
#   NOT its env-gated/fail-closed/min-N runtime seam.
"""Always-on execution-conditioned conservative lower bound q_exec_lcb.

The served model bound (``model_q_lcb``, the JointQBand 5% posterior quantile) is a model-posterior
downside quantile; it does NOT have realized-frequency coverage after adaptive selection and the
maker adverse-fill event ("we got filled"). The settled book is net-negative precisely because the
served bound is over-confident (realized hit-rate sits BELOW served q_lcb).

This module serves a conservative bound that can only DEFLATE the model bound:

    q_exec_lcb = min(model_q_lcb, block_lower_bound_5pct(raw_side_prob))

``block_lower_bound`` is an isotonic (PAVA, NO fixed buckets) nondecreasing calibration of realized
``won`` over ``raw_side_prob`` within the candidate's (actual_exec_class x side) group, with a
one-sided 5% beta lower bound on the pooled block that contains ``raw_side_prob``.

Design invariants (the operator laws this fix must respect):
  * ALWAYS-ON: imported unconditionally; no env flag, no default-off, no shadow.
  * NEVER ABSTAINS: with no evidence anywhere in the parent chain it returns ``model_q_lcb``
    unchanged — it can deflate an over-confident bound, never halt continuous trading.
  * NO fixed q buckets, NO N_MIN, NO edge floor, NO q haircut: thin groups widen the lower bound
    naturally (beta LCB of a small n is wide/low) and PAVA pools adjacent raw-prob regions.
  * actual_exec_class conditions on "FILLED AS maker", not "intended maker": MAKER_FILL never
    borrows TAKER/ALL_EXECUTED evidence (that would erase the adverse-fill conditioning the fix
    exists to enforce). With no maker-fill parent the maker candidate gets ``model_q_lcb`` and the
    caller reroutes it to the taker lane (taker-if-edge-survives-spread).
"""
from __future__ import annotations

from dataclasses import dataclass

from src.decision.selection_calibrator import beta_lower_bound_95

ALL_SIDES = "ALL"
ALL_EXECUTED = "ALL_EXECUTED"
TAKER_CROSS = "TAKER_CROSS"
MAKER_FILL = "MAKER_FILL"


@dataclass(frozen=True)
class ExecutionOutcomeFact:
    """One settled, filled money-path outcome — the only evidence q_exec_lcb is built from.

    ``actual_exec_class`` is TAKER_CROSS when the filled leg actually crossed (FOK/FAK/deadline
    cross) and MAKER_FILL only when a resting maker order actually filled before any cross. It is the
    realized fill event, never the intended mode.
    """

    decision_time: str
    settled_at: str
    side: str
    actual_exec_class: str
    raw_side_prob: float
    model_q_lcb: float
    fill_price: float
    won: int


@dataclass(frozen=True)
class _Pool:
    lo: float  # smallest raw_side_prob in the pooled level set
    hi: float  # largest raw_side_prob in the pooled level set
    n: int
    hits: int


@dataclass(frozen=True)
class _Block:
    """A (exec_class, side) calibration block: PAVA pools sorted by raw_side_prob, nondecreasing rate."""

    pools: tuple[_Pool, ...]

    def lower_bound_at(self, raw: float) -> float:
        """5% beta lower bound of the pooled level set covering ``raw`` (right-continuous step)."""
        chosen = self.pools[0]
        for pool in self.pools:
            if pool.lo <= raw:
                chosen = pool
            else:
                break
        return beta_lower_bound_95(chosen.hits, chosen.n)


def _aggregate(facts: list[ExecutionOutcomeFact]) -> list[tuple[float, int, int]]:
    """Collapse facts to (raw_side_prob, n, hits), sorted ascending by raw_side_prob."""
    by_x: dict[float, tuple[int, int]] = {}
    for f in facts:
        x = float(f.raw_side_prob)
        n, hits = by_x.get(x, (0, 0))
        by_x[x] = (n + 1, hits + int(f.won))
    return [(x, by_x[x][0], by_x[x][1]) for x in sorted(by_x)]


def _pava_pools(points: list[tuple[float, int, int]]) -> tuple[_Pool, ...]:
    """Pool-Adjacent-Violators: merge adjacent points until pooled hit-rates are nondecreasing."""
    pools: list[list[float | int]] = []  # [lo, hi, n, hits]
    for x, n, hits in points:
        cur: list[float | int] = [x, x, n, hits]
        # merge while the previous pool's rate exceeds the current pool's rate (a violation of
        # nondecreasing isotonic order)
        while pools and (pools[-1][3] / pools[-1][2]) > (cur[3] / cur[2]):
            prev = pools.pop()
            cur = [prev[0], cur[1], prev[2] + cur[2], prev[3] + cur[3]]
        pools.append(cur)
    return tuple(_Pool(float(lo), float(hi), int(n), int(hits)) for lo, hi, n, hits in pools)


def build_exec_blocks(facts: list[ExecutionOutcomeFact]) -> dict[tuple[str, str], _Block]:
    """Build calibration blocks keyed by (actual_exec_class, side) plus the parent aggregates.

    Parent aggregates built so the fallback chain in ``q_exec_lcb`` can borrow coarser evidence:
      * (exec_class, ALL_SIDES) for every executed class,
      * (ALL_EXECUTED, side) and (ALL_EXECUTED, ALL_SIDES) — these are the TAKER chain's coarsest
        parents and the 133-settled-row root that is covered today.
    MAKER_FILL rows are deliberately EXCLUDED from the ALL_EXECUTED roots: the maker chain stops at
    (MAKER_FILL, ALL_SIDES), so maker can never be authorized by all-executed/taker evidence, and a
    taker candidate falling back to ALL_EXECUTED is never made *less* conservative by maker rows.
    """
    groups: dict[tuple[str, str], list[ExecutionOutcomeFact]] = {}

    def add(key: tuple[str, str], fact: ExecutionOutcomeFact) -> None:
        groups.setdefault(key, []).append(fact)

    for f in facts:
        side = str(f.side)
        exec_class = str(f.actual_exec_class)
        add((exec_class, side), f)
        add((exec_class, ALL_SIDES), f)
        if exec_class != MAKER_FILL:
            add((ALL_EXECUTED, side), f)
            add((ALL_EXECUTED, ALL_SIDES), f)

    blocks: dict[tuple[str, str], _Block] = {}
    for key, group_facts in groups.items():
        pools = _pava_pools(_aggregate(group_facts))
        if pools:
            blocks[key] = _Block(pools)
    return blocks


def _parent_chain(exec_class: str, side: str) -> list[tuple[str, str]]:
    """Coarsening fallback order. MAKER_FILL stops at (MAKER_FILL, ALL_SIDES) — never borrows taker."""
    if exec_class == MAKER_FILL:
        return [(MAKER_FILL, side), (MAKER_FILL, ALL_SIDES)]
    return [
        (exec_class, side),
        (exec_class, ALL_SIDES),
        (ALL_EXECUTED, side),
        (ALL_EXECUTED, ALL_SIDES),
    ]


def q_exec_lcb(
    *,
    model_q_lcb: float,
    raw_side_prob: float,
    exec_class: str,
    side: str,
    blocks: dict[tuple[str, str], _Block],
) -> float:
    """Serve min(model_q_lcb, finest-covered block lower bound). Never abstains.

    Walks the parent chain from finest to coarsest; the FIRST covered block decides (its lower bound
    already widens for low n). With no covered block anywhere, returns ``model_q_lcb`` unchanged.
    """
    m = float(model_q_lcb)
    for key in _parent_chain(str(exec_class), str(side)):
        block = blocks.get(key)
        if block is not None:
            return min(m, block.lower_bound_at(float(raw_side_prob)))
    return m
