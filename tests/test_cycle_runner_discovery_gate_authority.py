# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: codereview-may19.md P0-1 + codereview-may19-2.md P0-1
"""Antibody: _discovery_gates_allow_entries() is the SINGLE authority for entry dispatch.

Root cause (codereview-may19.md P0-1): prior to the structural refactor the gate
only consumed 4 inputs (risk_level, heartbeat_status, ws_gap_status, has_quarantine),
while run_cycle() computed entries_blocked_reason covering 12+ additional blockers
(chain_ready, force_exit, entry_bankroll, exposure_gate_hit, entries_paused,
cutover_guard, portfolio_governor, posture, etc). Discovery could run while operators
saw "blocked: entries_paused" — safety and observability were architecturally divided.

Fix (codereview-may19.md P0-1 structural refactor): gate function now takes all
blockers as kwargs and is the single authority. entries_blocked_reason is
operator-facing observability that MIRRORS the gate decision, not a gate input.

Antibody contracts (sed-flip verifiable):
  T-sentinel: def line must be kwargs-only with all required arg names.
  T-parametrized (≥12): each single blocker active → gate returns False.
  T-clean: all-clear kwargs → gate returns True.
  T-fail-closed: block_registry=None → gate returns False.
  T-comment-rot: "observability only; not consulted" must be gone from cycle_runner.py.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.engine.cycle_runner import _discovery_gates_allow_entries
from src.control.entries_block_registry import BlockStage
from src.riskguard.risk_level import RiskLevel


# ── T-Sentinel ────────────────────────────────────────────────────────────────

def test_gate_signature_is_kwargs_only():
    """T-sentinel: gate def line must be kwargs-only with all required arg names.

    Sed-flip: restore 4-arg positional signature → this grep fails.
    If the function reverts to a positional signature, callers that pass
    one of the newly-required args positionally will silently skip the check.
    """
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "engine" / "cycle_runner.py"
    text = src.read_text()

    required_args = [
        "cutover_summary",
        "governor_status",
        "entry_bankroll",
        "entries_paused",
        "block_registry",
        "force_exit",
        "current_posture",
        "chain_ready",
        "exposure_gate_hit",
        "has_quarantine",
        "risk_level",
        "heartbeat_status",
        "ws_gap_status",
    ]
    assert "def _discovery_gates_allow_entries(\n    *," in text, (
        "P0-1 SENTINEL FAIL: _discovery_gates_allow_entries must use kwargs-only "
        "signature (first arg after '(' must be bare '*,'). Structural refactor reverted."
    )
    for arg in required_args:
        assert arg in text, (
            f"P0-1 SENTINEL FAIL: required arg '{arg}' not found in "
            "src/engine/cycle_runner.py — kwarg was dropped from gate signature."
        )


def test_no_misleading_observability_comment():
    """T-comment-rot: the 'observability only; not consulted' string must be gone.

    That comment was FALSE after the P0-1 fix and labeled the safety object
    as observability. It must not reappear.
    """
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "engine" / "cycle_runner.py"
    text = src.read_text()
    assert "observability only; not consulted" not in text, (
        "P0-1 COMMENT-ROT FAIL: misleading comment 'observability only; not consulted' "
        "is still present in src/engine/cycle_runner.py. Remove it — it labels the "
        "safety object as observability and cites a gate-purge that no longer matches reality."
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_clear_registry() -> MagicMock:
    """Registry mock where is_clear(DISCOVERY) returns True."""
    r = MagicMock()
    r.is_clear.return_value = True
    return r


def _ok_kwargs() -> dict:
    """Return an all-clear kwargs dict that produces gate=True."""
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


# ── T-Clean ───────────────────────────────────────────────────────────────────

def test_gate_passes_when_all_clear():
    """T-clean: all-clear kwargs → gate returns True."""
    assert _discovery_gates_allow_entries(**_ok_kwargs()) is True


# ── T-Fail-Closed ─────────────────────────────────────────────────────────────

def test_fail_closed_when_registry_none():
    """T-fail-closed: block_registry=None (construction failed) → gate returns False.

    Registry-unavailable is itself a blocking condition; the gate must not allow
    discovery when it cannot verify the block registry is clear.
    """
    kwargs = _ok_kwargs()
    kwargs["block_registry"] = None
    assert _discovery_gates_allow_entries(**kwargs) is False


def test_fail_closed_when_registry_not_clear():
    """T-fail-closed: block_registry.is_clear(DISCOVERY)=False → gate returns False."""
    kwargs = _ok_kwargs()
    r = MagicMock()
    r.is_clear.return_value = False
    kwargs["block_registry"] = r
    assert _discovery_gates_allow_entries(**kwargs) is False


# ── T-Parametrized (one test per blocker) ─────────────────────────────────────

@pytest.mark.parametrize("risk_level", [
    RiskLevel.YELLOW,
    RiskLevel.ORANGE,
    RiskLevel.RED,
    RiskLevel.DATA_DEGRADED,
])
def test_gate_blocks_on_non_green_risk(risk_level):
    """T-risk: any non-GREEN risk_level → gate returns False."""
    kwargs = _ok_kwargs()
    kwargs["risk_level"] = risk_level
    assert _discovery_gates_allow_entries(**kwargs) is False, (
        f"P0-1 FAIL: risk_level={risk_level.value} did not block the gate"
    )


def test_gate_blocks_on_chain_not_ready():
    """T-chain: chain_ready=False → gate returns False."""
    kwargs = _ok_kwargs()
    kwargs["chain_ready"] = False
    assert _discovery_gates_allow_entries(**kwargs) is False


def test_gate_blocks_on_quarantine():
    """T-quarantine: has_quarantine=True → gate returns False."""
    kwargs = _ok_kwargs()
    kwargs["has_quarantine"] = True
    assert _discovery_gates_allow_entries(**kwargs) is False


def test_gate_blocks_on_force_exit():
    """T-force-exit: force_exit=True → gate returns False."""
    kwargs = _ok_kwargs()
    kwargs["force_exit"] = True
    assert _discovery_gates_allow_entries(**kwargs) is False


def test_gate_blocks_on_entries_paused():
    """T-entries-paused: entries_paused=True → gate returns False."""
    kwargs = _ok_kwargs()
    kwargs["entries_paused"] = True
    assert _discovery_gates_allow_entries(**kwargs) is False


def test_gate_blocks_on_posture_no_new_entries():
    """T-posture: current_posture=NO_NEW_ENTRIES → gate returns False."""
    kwargs = _ok_kwargs()
    kwargs["current_posture"] = "NO_NEW_ENTRIES"
    assert _discovery_gates_allow_entries(**kwargs) is False


def test_gate_blocks_on_entry_bankroll_none():
    """T-bankroll-none: entry_bankroll=None → gate returns False."""
    kwargs = _ok_kwargs()
    kwargs["entry_bankroll"] = None
    assert _discovery_gates_allow_entries(**kwargs) is False


def test_gate_blocks_on_entry_bankroll_zero():
    """T-bankroll-zero: entry_bankroll=0 → gate returns False."""
    kwargs = _ok_kwargs()
    kwargs["entry_bankroll"] = 0
    assert _discovery_gates_allow_entries(**kwargs) is False


def test_gate_blocks_on_entry_bankroll_negative():
    """T-bankroll-neg: entry_bankroll=-1.0 → gate returns False."""
    kwargs = _ok_kwargs()
    kwargs["entry_bankroll"] = -1.0
    assert _discovery_gates_allow_entries(**kwargs) is False


def test_gate_blocks_on_exposure_gate_hit():
    """T-exposure: exposure_gate_hit=True → gate returns False."""
    kwargs = _ok_kwargs()
    kwargs["exposure_gate_hit"] = True
    assert _discovery_gates_allow_entries(**kwargs) is False


def test_gate_blocks_on_cutover_not_allowing():
    """T-cutover: cutover_summary entry.allow_submit=False → gate returns False."""
    kwargs = _ok_kwargs()
    kwargs["cutover_summary"] = {"entry": {"allow_submit": False}}
    assert _discovery_gates_allow_entries(**kwargs) is False


def test_gate_blocks_on_heartbeat_not_allowing():
    """T-heartbeat: heartbeat_status entry.allow_submit=False → gate returns False."""
    kwargs = _ok_kwargs()
    kwargs["heartbeat_status"] = {"entry": {"allow_submit": False}}
    assert _discovery_gates_allow_entries(**kwargs) is False


def test_gate_blocks_on_ws_gap_not_allowing():
    """T-ws-gap: ws_gap_status entry.allow_submit=False → gate returns False."""
    kwargs = _ok_kwargs()
    kwargs["ws_gap_status"] = {"entry": {"allow_submit": False}}
    assert _discovery_gates_allow_entries(**kwargs) is False


def test_gate_blocks_on_governor_not_allowing():
    """T-governor: governor_status entry.allow_submit=False → gate returns False."""
    kwargs = _ok_kwargs()
    kwargs["governor_status"] = {"entry": {"allow_submit": False}}
    assert _discovery_gates_allow_entries(**kwargs) is False


def test_gate_blocks_on_block_registry_discovery_not_clear():
    """T-registry-not-clear: block_registry.is_clear(DISCOVERY)=False → False."""
    kwargs = _ok_kwargs()
    r = MagicMock()
    r.is_clear.return_value = False
    kwargs["block_registry"] = r
    result = _discovery_gates_allow_entries(**kwargs)
    assert result is False
    r.is_clear.assert_called_with(BlockStage.DISCOVERY)
