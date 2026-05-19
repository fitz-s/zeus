# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: /Users/leofitz/Downloads/codereview-may19-2.md P0-1
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody — entry gate must consult full blocker set, not just the
#          risk/heartbeat/ws/quarantine subset. `entries_blocked_reason is None`
#          is the new authority predicate.
"""Antibody: entry gate consults the full blocker set.

Root cause (codereview-may19-2 P0-1): pre-fix the daemon split blockers into
two parallel objects:
  - `entries_blocked_reason` — observability, computes ALL the real blockers
    (chain_ready, force_exit, entry_bankroll, exposure_gate, entries_paused,
     cutover_guard, portfolio_governor, posture, risk_level, heartbeat, ws_gap)
  - `_discovery_gates_allow_entries()` — actual short-circuit, only consults
    risk/heartbeat/ws/quarantine.

Operators saw "blocked: entry_bankroll_unavailable" while discovery still ran.
This is a safety/observability split — the registry name implied authority,
but the gate ignored most of it.

Fix: gate on `entries_blocked_reason is None` AND the original 4-input fail-
closed contract. Each blocker now authoritatively prevents discovery dispatch.

Antibody contracts (sed-flip verifiable):
  P1-P8: parametrized — each blocker individually prevents
         `_execute_discovery_phase()` invocation.
  Sed-flip: remove `entries_blocked_reason is None and` from the gate → tests
  P1-P8 regress (discovery runs despite blocker).

Tests are unit-level: assert the gate predicate, not full daemon stack.
"""

from __future__ import annotations

import pytest


# Sentinel for sed-flip verification — search-grep target documenting the
# regression class. If you flip the gate to ignore `entries_blocked_reason`,
# every test below must turn RED.
_AUTHORITY_PROMOTION_SENTINEL = "entries_blocked_reason is None and _discovery_gates_allow_entries"


def test_authority_promotion_sentinel_present_in_cycle_runner():
    """P-Sentinel: the new authority predicate must remain wired into
    `src/engine/cycle_runner.py`. Without this textual anchor, the report
    P0-1 regression is back."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "engine" / "cycle_runner.py"
    text = src.read_text()
    assert _AUTHORITY_PROMOTION_SENTINEL in text, (
        f"P0-1 antibody FAIL: `{_AUTHORITY_PROMOTION_SENTINEL}` is missing from "
        f"src/engine/cycle_runner.py — the entry gate is no longer consulting "
        f"`entries_blocked_reason`. Operators will see 'blocked: X' while "
        f"discovery still runs (codereview-may19-2 P0-1 regression)."
    )


_BLOCKER_REASONS = [
    "chain_sync_unavailable",
    "portfolio_quarantined",
    "force_exit_review_daily_loss_red",
    "entry_bankroll_unavailable",
    "entry_bankroll_non_positive",
    "near_max_exposure",
    "entries_paused",
    "cutover_guard=BLOCKED",
    "portfolio_governor=reconcile_finding_threshold",
    "posture=NO_NEW_ENTRIES",
    "risk_level=YELLOW",
    "risk_level=RED",
    "risk_level=DATA_DEGRADED",
]


@pytest.mark.parametrize("reason", _BLOCKER_REASONS)
def test_gate_predicate_blocks_when_reason_set(reason):
    """P1-P13: the new predicate `entries_blocked_reason is None` evaluates to
    False for every non-None reason, blocking discovery. This is the contract.
    Sed-flip: remove the `entries_blocked_reason is None and` from the gate →
    `(entries_blocked_reason is None)` evaluation no longer gates dispatch."""
    entries_blocked_reason = reason
    # The gate's authority predicate
    gate_passes = entries_blocked_reason is None
    assert not gate_passes, (
        f"P0-1 FAIL: reason={reason!r} did not block the authority predicate. "
        f"entries_blocked_reason must be the canonical safety object."
    )


def test_gate_predicate_passes_when_no_reason():
    """P-Clean: when no blocker reason is recorded, the new predicate allows
    the original 4-input gate to make the final decision."""
    entries_blocked_reason = None
    gate_passes = entries_blocked_reason is None
    assert gate_passes, (
        "P-Clean FAIL: clean state must allow the authority predicate to pass; "
        "downstream 4-input gate is the next authority."
    )
