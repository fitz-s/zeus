"""Tests for K4 Slice I — Lifecycle State Machine fixes (Bugs #51, #52, #53)."""

from __future__ import annotations

import pytest

from src.state.lifecycle_manager import (
    LifecyclePhase,
    LEGAL_LIFECYCLE_FOLDS,
    enter_day0_window_runtime_state,
    enter_settled_runtime_state,
    enter_voided_runtime_state,
    fold_lifecycle_phase,
    initial_entry_runtime_state_for_order_status,
    phase_for_runtime_position,
)


# --- Bug #51: phase_for_runtime_position returns UNKNOWN instead of raising ---


def test_phase_for_runtime_position_unknown_state_returns_unknown():
    result = phase_for_runtime_position(state="totally_bogus_state")
    assert result is LifecyclePhase.UNKNOWN


def test_phase_for_runtime_position_known_states_still_work():
    assert phase_for_runtime_position(state="entered") is LifecyclePhase.ACTIVE
    assert phase_for_runtime_position(state="holding") is LifecyclePhase.ACTIVE
    assert phase_for_runtime_position(state="voided") is LifecyclePhase.VOIDED
    assert phase_for_runtime_position(state="settled") is LifecyclePhase.SETTLED
    assert phase_for_runtime_position(state="economically_closed") is LifecyclePhase.ECONOMICALLY_CLOSED
    assert phase_for_runtime_position(state="admin_closed") is LifecyclePhase.ADMIN_CLOSED
    assert phase_for_runtime_position(state="quarantined") is LifecyclePhase.QUARANTINED
    assert phase_for_runtime_position(state="pending_exit") is LifecyclePhase.PENDING_EXIT
    assert phase_for_runtime_position(state="pending_tracked") is LifecyclePhase.PENDING_ENTRY
    assert phase_for_runtime_position(state="day0_window") is LifecyclePhase.DAY0_WINDOW


# --- Bug #52: enter_day0_window accepts PENDING_ENTRY and DAY0_WINDOW ---


def test_enter_day0_from_pending_entry():
    result = enter_day0_window_runtime_state("pending_tracked")
    assert result == "day0_window"


def test_enter_day0_from_day0_window_idempotent():
    result = enter_day0_window_runtime_state("day0_window")
    assert result == "day0_window"


def test_enter_day0_from_active_still_works():
    result = enter_day0_window_runtime_state("entered")
    assert result == "day0_window"


# --- Bug #53a: initial_entry_runtime_state_for_order_status terminal statuses ---


def test_order_status_filled_returns_entered():
    assert initial_entry_runtime_state_for_order_status("filled") == "entered"


def test_order_status_canceled_returns_voided():
    assert initial_entry_runtime_state_for_order_status("canceled") == "voided"


def test_order_status_rejected_returns_voided():
    assert initial_entry_runtime_state_for_order_status("rejected") == "voided"


def test_order_status_pending_returns_pending_tracked():
    assert initial_entry_runtime_state_for_order_status("pending") == "pending_tracked"


# --- Bug #53b: enter_settled_runtime_state without backoff_exhausted ---


def test_settled_from_pending_exit_without_backoff():
    result = enter_settled_runtime_state(
        "pending_exit",
        exit_state="sell_placed",
    )
    assert result == "settled"


def test_settled_from_active_still_works():
    result = enter_settled_runtime_state("entered")
    assert result == "settled"


# --- Fold table: UNKNOWN transitions ---


def test_unknown_phase_can_transition_to_quarantined():
    result = fold_lifecycle_phase(LifecyclePhase.UNKNOWN, LifecyclePhase.QUARANTINED)
    assert result is LifecyclePhase.QUARANTINED


def test_unknown_phase_can_transition_to_voided():
    result = fold_lifecycle_phase(LifecyclePhase.UNKNOWN, LifecyclePhase.VOIDED)
    assert result is LifecyclePhase.VOIDED


def test_unknown_phase_self_transition():
    result = fold_lifecycle_phase(LifecyclePhase.UNKNOWN, LifecyclePhase.UNKNOWN)
    assert result is LifecyclePhase.UNKNOWN


def test_unknown_phase_illegal_transition_raises():
    with pytest.raises(ValueError):
        fold_lifecycle_phase(LifecyclePhase.UNKNOWN, LifecyclePhase.ACTIVE)


# --- Critic fix: UNKNOWN positions can be voided via enter_voided_runtime_state ---


def test_unknown_position_can_be_voided_via_enter_function():
    """UNKNOWN phase must have an operational exit path — not a dead end."""
    result = enter_voided_runtime_state("garbage_state_xyz")
    assert result == "voided"


def test_unknown_in_open_exposure_phases():
    """UNKNOWN positions represent real financial exposure — must be visible to risk."""
    from src.state.db import OPEN_EXPOSURE_PHASES

    assert "unknown" in OPEN_EXPOSURE_PHASES
