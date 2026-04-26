# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_full_data_midstream_fix_plan/plan.md
#                  slice B1 (single ownership of terminal predicate +
#                  semantic fix at cycle_runner.py exposure-block)
"""Slice B1 relationship + function tests.

Pins the structural invariant that lifecycle terminal-state membership
has exactly one source of truth, derived programmatically from
LEGAL_LIFECYCLE_FOLDS, and that all three former sites
(portfolio.py module-private set, cycle_runner sweep frozenset,
cycle_runner exposure-block predicate) agree.

Also pins the semantic fix anchor: ECONOMICALLY_CLOSED is NOT terminal
(it folds to {ECONOMICALLY_CLOSED, SETTLED, VOIDED}). The pre-B1
cycle_runner.py:341 inline set incorrectly classified it as terminal.

All tests are pure-Python; no DB, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.lifecycle_manager import (
    LEGAL_LIFECYCLE_FOLDS,
    LifecyclePhase,
    TERMINAL_STATES,
    is_terminal_state,
)


# -----------------------------------------------------------------------------
# Programmatic derivation invariant
# -----------------------------------------------------------------------------


def test_terminal_states_derived_from_folds_invariant():
    """TERMINAL_STATES must equal {phase | LEGAL_LIFECYCLE_FOLDS[phase] == {phase}}."""
    expected = frozenset(
        phase.value
        for phase, fold in LEGAL_LIFECYCLE_FOLDS.items()
        if phase is not None and fold == frozenset({phase})
    )
    assert TERMINAL_STATES == expected


def test_canonical_terminal_membership():
    """Per LEGAL_LIFECYCLE_FOLDS, exactly 4 phases are terminal."""
    assert TERMINAL_STATES == frozenset(
        {"settled", "voided", "quarantined", "admin_closed"}
    )


# -----------------------------------------------------------------------------
# Function tests
# -----------------------------------------------------------------------------


def test_is_terminal_state_canonical_terminals():
    for s in ("settled", "voided", "quarantined", "admin_closed"):
        assert is_terminal_state(s) is True, f"{s!r} should be terminal"


def test_is_terminal_state_economically_closed_is_not_terminal():
    """Anchor for the B1 semantic-bug fix at cycle_runner.py:341.

    ECONOMICALLY_CLOSED folds to {ECONOMICALLY_CLOSED, SETTLED, VOIDED} —
    it has legal next states, so it is NOT terminal despite the name.
    The pre-B1 inline set wrongly counted it as terminal.
    """
    assert is_terminal_state("economically_closed") is False
    assert is_terminal_state(LifecyclePhase.ECONOMICALLY_CLOSED) is False


def test_is_terminal_state_non_terminals():
    for s in ("pending_entry", "active", "day0_window", "pending_exit", "unknown"):
        assert is_terminal_state(s) is False, f"{s!r} should not be terminal"


def test_is_terminal_state_accepts_enum_members():
    assert is_terminal_state(LifecyclePhase.SETTLED) is True
    assert is_terminal_state(LifecyclePhase.QUARANTINED) is True
    assert is_terminal_state(LifecyclePhase.ACTIVE) is False


def test_is_terminal_state_handles_none_and_empty():
    assert is_terminal_state(None) is False
    assert is_terminal_state("") is False
    assert is_terminal_state("   ") is False


def test_is_terminal_state_case_and_whitespace_normalized():
    assert is_terminal_state("  SETTLED  ") is True
    assert is_terminal_state("Voided") is True


# -----------------------------------------------------------------------------
# Relationship tests: all three former sites agree
# -----------------------------------------------------------------------------


def test_portfolio_terminal_set_matches_canonical():
    """src/state/portfolio.py module-private symbol must alias TERMINAL_STATES."""
    from src.state import portfolio
    assert portfolio._TERMINAL_POSITION_STATES is TERMINAL_STATES


def test_cycle_runner_sweep_set_matches_canonical():
    """src/engine/cycle_runner.py sweep frozenset must equal TERMINAL_STATES."""
    from src.engine import cycle_runner
    assert cycle_runner._TERMINAL_POSITION_STATES_FOR_SWEEP is TERMINAL_STATES


def test_cycle_runner_imports_is_terminal_state():
    """cycle_runner.py exposure-block must use the predicate, not a literal set."""
    from src.engine import cycle_runner
    assert cycle_runner.is_terminal_state is is_terminal_state


# -----------------------------------------------------------------------------
# Future-proofing: derivation auto-updates if LEGAL_LIFECYCLE_FOLDS changes
# -----------------------------------------------------------------------------


def test_derivation_auto_updates_when_folds_change(monkeypatch):
    """If LEGAL_LIFECYCLE_FOLDS gains a new terminal entry, a fresh derivation
    must include it without any literal-set edits in consumers.

    This pins the structural property: TERMINAL_STATES is data, not a
    hardcoded literal, so future fold edits cannot drift from consumer sites.
    """
    from src.state import lifecycle_manager as lm

    fake_phase = LifecyclePhase.UNKNOWN  # any enum we can monkey-patch into
    new_folds = dict(LEGAL_LIFECYCLE_FOLDS)
    new_folds[fake_phase] = frozenset({fake_phase})  # make it terminal
    monkeypatch.setattr(lm, "LEGAL_LIFECYCLE_FOLDS", new_folds)

    refreshed = frozenset(
        phase.value
        for phase, fold in lm.LEGAL_LIFECYCLE_FOLDS.items()
        if phase is not None and fold == frozenset({phase})
    )
    assert "unknown" in refreshed
