# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: Operator MONEY-PATH directive 2026-06-09 (RULE 1: trades always
#   exist; "no opportunity found" = our defect). Funnel-starvation root cause: the
#   pending-family substrate warmer refreshed only 1-2 of ~150 live families per
#   cycle, so ~150 FORECAST_SNAPSHOT_READY events retried EXECUTABLE_SNAPSHOT_PENDING
#   forever and dead-lettered EXECUTABLE_SNAPSHOT_BLOCKED. Two structural decisions
#   (K=2): (K1) the topology-phase budget collapsed to ~0s; (K2) no rotating cursor,
#   so the same front slice was re-processed every cycle and the tail starved.
"""Antibody tests for the substrate-sweep funnel-starvation category.

These are RELATIONSHIP tests (Fitz methodology): they assert cross-module
invariants at the boundary between the substrate warmer's per-cycle budget/cursor
and the reactor's executable-snapshot gate, not just single-function behavior.

INVARIANTS (the category these make impossible):

  INV-SWEEP-1 (topology budget never zero): the topology/reconstruction phase —
    the phase that SELECTS which families' books to capture this cycle — must
    receive a strictly-positive share of the pre-capture window. A zero topology
    budget collapses the sweep to 1-2 families/cycle (the observed defect).

  INV-SWEEP-2 (full sweep within a bounded period): rotating the per-cycle
    starting offset by the number of families processed must visit EVERY family
    within ceil(n / per_cycle) cycles — no family may be permanently skipped while
    others are re-refreshed. This is what guarantees every live family gets fresh
    books (and thus an evaluation) within one sweep period.

  INV-SWEEP-3 (evaluation ceiling >= live family count): the reactor per-cycle
    proof limit must be allowed to reach the full live FORECAST_SNAPSHOT_READY set
    so honest no-edge is only ever declared after a FULL evaluation, never because
    the queue was truncated below the live family count.
"""
import os

import pytest


# ---------------------------------------------------------------------------
# INV-SWEEP-1: topology phase budget is strictly positive
# ---------------------------------------------------------------------------

def test_topology_deadline_gives_positive_budget_at_live_settings():
    """With the live warm budget (17s) and reserve (12s), the topology phase must
    get a STRICTLY POSITIVE slice of wall-clock — not the 0s collapse that limited
    the warmer to 1-2 families/cycle."""
    import time

    import src.data.substrate_observer as observer

    budget_s = 17.0
    reserve_s = 12.0
    start = time.monotonic()
    deadline = start + budget_s
    topo_deadline = observer._topology_lookup_deadline_for_snapshot_refresh(
        refresh_deadline=deadline,
        refresh_budget_s=budget_s,
        snapshot_reserve_s=reserve_s,
    )
    topology_window_s = topo_deadline - start
    assert topology_window_s > 0.0, (
        f"topology phase budget collapsed to {topology_window_s:.3f}s — the "
        "warmer can only reach 1-2 families/cycle and the live family set starves"
    )
    # And it must keep the MAJORITY of the pre-capture window (gamma is only needed
    # for families without cached topology, which is empty in steady state).
    pre_capture_s = budget_s - reserve_s
    assert topology_window_s >= pre_capture_s * 0.5, (
        f"topology phase got only {topology_window_s:.3f}s of the {pre_capture_s:.1f}s "
        "pre-capture window; gamma reserve must not pre-empt topology"
    )


def test_topology_deadline_positive_across_budget_reserve_grid():
    """The positive-topology-budget invariant must hold across the realistic
    budget/reserve envelope, not just one point."""
    import time

    import src.data.substrate_observer as observer

    for budget_s in (10.0, 15.0, 17.0, 20.0, 25.0):
        for reserve_s in (6.0, 9.0, 12.0):
            if reserve_s >= budget_s:
                continue
            start = time.monotonic()
            deadline = start + budget_s
            topo_deadline = observer._topology_lookup_deadline_for_snapshot_refresh(
                refresh_deadline=deadline,
                refresh_budget_s=budget_s,
                snapshot_reserve_s=reserve_s,
            )
            window = topo_deadline - start
            assert window > 0.0, (
                f"topology budget {window:.3f}s <= 0 at budget={budget_s} reserve={reserve_s}"
            )


# ---------------------------------------------------------------------------
# INV-SWEEP-2: rotating cursor visits every family within a bounded period
# ---------------------------------------------------------------------------

def _simulate_sweep(n_families: int, per_cycle: int, cycles: int) -> set[int]:
    """Replay the rotating-cursor logic from _refresh_pending_family_snapshots in
    isolation: each cycle starts at cursor % n and processes `per_cycle` families,
    then advances the cursor by the count processed. Returns the set of family
    indices visited across `cycles` cycles."""
    cursor = 0
    visited: set[int] = set()
    for _ in range(cycles):
        start = cursor % n_families
        rotated = list(range(start, n_families)) + list(range(0, start))
        processed = min(per_cycle, n_families)
        for i in range(processed):
            visited.add(rotated[i])
        cursor = (start + max(1, processed)) % n_families
    return visited


def test_rotating_cursor_sweeps_all_families_within_one_period():
    """Every family must be visited within ceil(n / per_cycle) cycles — the
    bounded sweep period. This is the antibody for 'tail families starve forever'."""
    n = 153
    per_cycle = 20
    import math

    period = math.ceil(n / per_cycle)
    visited = _simulate_sweep(n_families=n, per_cycle=per_cycle, cycles=period)
    assert visited == set(range(n)), (
        f"after one sweep period ({period} cycles) only {len(visited)}/{n} families "
        "were refreshed — some live family is permanently starved of fresh books"
    )


def test_rotating_cursor_no_permanent_starvation_small_slice():
    """Even when only ONE family fits per cycle (worst case), the cursor must still
    eventually visit every family — never lock onto the same front slice."""
    n = 50
    visited = _simulate_sweep(n_families=n, per_cycle=1, cycles=n)
    assert visited == set(range(n)), (
        "a 1-family-per-cycle warmer must still sweep all families over n cycles; "
        f"got {len(visited)}/{n} — this is the exact pre-fix starvation"
    )


def test_substrate_refresh_cursor_module_global_exists():
    """The rotating cursor must be a real module-global the warmer mutates, not a
    per-call local (which would reset to the same front slice every cycle)."""
    import src.data.substrate_observer as S

    assert hasattr(S, "_SUBSTRATE_REFRESH_CURSOR"), (
        "the rotating cursor _SUBSTRATE_REFRESH_CURSOR is missing — without a "
        "persistent cursor every cycle restarts at the newest families and the "
        "tail starves"
    )


def test_substrate_gamma_refresh_cursor_module_global_exists():
    """Gamma slug lookup needs its own cursor.

    A family sweep can process every pending family while the bounded Gamma slice
    only submits the first few slugs. Without a persistent Gamma cursor, every
    cycle probes the same prefix and tail families never get topology/bin identity.
    """
    import src.data.substrate_observer as S

    assert hasattr(S, "_SUBSTRATE_GAMMA_REFRESH_CURSOR")


# ---------------------------------------------------------------------------
# INV-SWEEP-3: reactor evaluation ceiling can reach the live family count
# ---------------------------------------------------------------------------

def test_proof_limit_ceiling_covers_live_family_set():
    """The per-cycle proof limit ceiling must be allowed to reach the full live
    FORECAST_SNAPSHOT_READY set (~200 events across ~50 cities × 3 dates) so honest
    no-edge is only declared after a FULL evaluation. A 50-cap truncates the queue
    below the live family count — unevaluated markets, which the operator forbids."""
    # R4-b3 (2026-07-08): _edli_positive_int_or_unbounded moved from
    # src/main.py to src.events.reactor with the reactor+prune cluster.
    from src.events import reactor

    # The live set is ~50 cities × up to 3 active target dates ≈ 150-210 events.
    # The ceiling must comfortably exceed this so a single cycle is not capped below
    # the admissible queue.
    cfg = {"no_submit_proof_limit": 100000}
    resolved = reactor._edli_positive_int_or_unbounded(
        cfg, "no_submit_proof_limit", default=10, maximum=400
    )
    assert resolved >= 200, (
        f"proof_limit ceiling resolved to {resolved} (<200) — the reactor cannot "
        "sweep the full live family set in a cycle, so some live families go "
        "unevaluated every cadence"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
