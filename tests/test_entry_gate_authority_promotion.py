# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: /Users/leofitz/Downloads/codereview-may19-2.md P0-1 (updated sentinel post-structural-refactor)
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody — entry gate must consult full blocker set via kwargs-only
#          gate function, not just the risk/heartbeat/ws/quarantine subset.
"""Antibody: entry gate consults the full blocker set (post-structural-refactor).

Root cause (codereview-may19-2 P0-1): pre-fix the daemon split blockers into
two parallel objects:
  - `entries_blocked_reason` — observability, computes ALL the real blockers
    (chain_ready, force_exit, entry_bankroll, exposure_gate, entries_paused,
     cutover_guard, portfolio_governor, posture, risk_level, heartbeat, ws_gap)
  - `_discovery_gates_allow_entries()` — actual short-circuit, only consulted
    risk/heartbeat/ws/quarantine.

Operators saw "blocked: entry_bankroll_unavailable" while discovery still ran.
This is a safety/observability split — the safety object was architecturally
divided from the authority object.

Structural fix (codereview-may19.md P0-1): _discovery_gates_allow_entries() was
rewritten to a kwargs-only signature that accepts ALL blockers. The gate is now
the SINGLE authority. entries_blocked_reason is observability mirroring the gate,
not a gate input. See tests/test_cycle_runner_discovery_gate_authority.py for the
full parametrized matrix.

Sentinel update: the original sentinel (entries_blocked_reason is None and
_discovery_gates_allow_entries) is no longer valid — it referenced the prior
external-wrapper form. New sentinel: kwargs-only gate function present.

Antibody contracts (sed-flip verifiable):
  P-Sentinel: gate function must have kwargs-only signature in cycle_runner.py.
  P1-P13: each blocker active → gate function returns False.
  Sed-flip: restore 4-arg gate → sentinel fails AND direct parametrized calls fail.
"""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from src.engine.cycle_runner import _discovery_gates_allow_entries
from src.control.entries_block_registry import BlockStage
from src.riskguard.risk_level import RiskLevel


# Sentinel for sed-flip verification — the structural fact that the gate uses
# a kwargs-only signature with the full blocker set. If you restore a positional
# 4-arg gate, this grep fails AND all parametrized tests below fail.
_AUTHORITY_PROMOTION_SENTINEL = "def _discovery_gates_allow_entries(\n    *,"


def test_authority_promotion_sentinel_present_in_cycle_runner():
    """P-Sentinel: the kwargs-only gate signature must remain in cycle_runner.py.

    Without this structural anchor, the P0-1 regression is back — a positional
    4-arg gate silently skips chain_ready/force_exit/bankroll/posture/etc.
    """
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "engine" / "cycle_runner.py"
    text = src.read_text()
    assert _AUTHORITY_PROMOTION_SENTINEL in text, (
        f"P0-1 antibody FAIL: kwargs-only gate signature is missing from "
        f"src/engine/cycle_runner.py — _discovery_gates_allow_entries() has "
        f"been reverted to a positional/partial signature. The full blocker set "
        f"is no longer consulted (codereview-may19.md P0-1 regression)."
    )


def _make_clear_registry() -> MagicMock:
    r = MagicMock()
    r.is_clear.return_value = True
    return r


def _ok_kwargs() -> dict:
    return dict(
        risk_level=RiskLevel.GREEN,
        heartbeat_status={"entry": {"allow_submit": True}},
        ws_gap_status={"entry": {"allow_submit": True}},
        cutover_summary={"entry": {"allow_submit": True}},
        governor_status={"entry": {"allow_submit": True}},
        current_posture="NORMAL",
        chain_ready=True,
        has_quarantine=False,
        force_exit=False,
        entry_bankroll=1000.0,
        exposure_gate_hit=False,
        entries_paused=False,
        block_registry=_make_clear_registry(),
    )


_BLOCKER_CASES: list[tuple[str, dict]] = [
    ("chain_sync_unavailable", {"chain_ready": False}),
    ("portfolio_quarantined", {"has_quarantine": True}),
    ("force_exit_review_daily_loss_red", {"force_exit": True}),
    ("entry_bankroll_unavailable", {"entry_bankroll": None}),
    ("entry_bankroll_non_positive", {"entry_bankroll": 0}),
    ("entry_bankroll_negative", {"entry_bankroll": -1.0}),
    ("near_max_exposure", {"exposure_gate_hit": True}),
    ("entries_paused", {"entries_paused": True}),
    ("cutover_guard=BLOCKED", {"cutover_summary": {"entry": {"allow_submit": False}}}),
    ("heartbeat_lost", {"heartbeat_status": {"entry": {"allow_submit": False}}}),
    ("ws_gap_disconnected", {"ws_gap_status": {"entry": {"allow_submit": False}}}),
    ("portfolio_governor", {"governor_status": {"entry": {"allow_submit": False}}}),
    ("posture=NO_NEW_ENTRIES", {"current_posture": "NO_NEW_ENTRIES"}),
    ("risk_level=YELLOW", {"risk_level": RiskLevel.YELLOW}),
    ("risk_level=RED", {"risk_level": RiskLevel.RED}),
    ("risk_level=DATA_DEGRADED", {"risk_level": RiskLevel.DATA_DEGRADED}),
]


@pytest.mark.parametrize("label,override", _BLOCKER_CASES)
def test_gate_predicate_blocks_when_reason_set(label, override):
    """P1-P16: each single blocker active → gate returns False.

    Sed-flip: restore 4-arg gate or remove any kwarg → the corresponding
    blocker no longer prevents discovery dispatch.
    """
    kwargs = _ok_kwargs()
    kwargs.update(override)
    result = _discovery_gates_allow_entries(**kwargs)
    assert result is False, (
        f"P0-1 FAIL: blocker '{label}' did not block the gate. "
        f"_discovery_gates_allow_entries must be the canonical safety authority."
    )


def test_gate_predicate_passes_when_no_reason():
    """P-Clean: all-clear kwargs → gate returns True."""
    assert _discovery_gates_allow_entries(**_ok_kwargs()) is True
