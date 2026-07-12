# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: .omc/plans/2026-05-19-lifecycle-pending-exit-guard.md
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody — enter_pending_exit_runtime_state must NOT raise when
#          origin phase is terminal-ish. Pre-fix: economically_closed origin
#          raises ValueError, crashing day0_capture and starving the entry
#          pipeline lock for ~8min per cycle.
"""Antibody tests: pending_exit guard for terminal-ish origin phases.

Root cause (2026-05-19T23:00:39Z day0_capture crash):
  enter_pending_exit_runtime_state had no guard against terminal-ish origin
  phases. Other enter_*_runtime_state siblings (day0_window, economically_closed,
  settled, etc.) all check phase preconditions and raise descriptive errors
  BEFORE attempting the fold. This sibling skipped the check, so any caller
  passing an economically_closed/settled/voided position hits the generic
  `fold_lifecycle_phase` ValueError, which then propagates up through
  day0_capture → _run_mode → daemon ERROR log, releasing the cycle lock only
  after 8 minutes of imminent_open_capture starvation.

Fix: add idempotency guard. Terminal-ish origin phases return their current
value as a no-op (the position has already moved past the point where a
pending_exit transition would be meaningful). This mirrors the safety pattern
the other enter_* siblings already implement explicitly.

Antibody contracts (sed-flip verifiable):
  T1: ECONOMICALLY_CLOSED origin → no raise; returns "economically_closed".
  T2: SETTLED origin → no raise; returns "settled".
  T3: VOIDED origin → no raise; returns "voided".
  T4: ADMIN_CLOSED origin → no raise; returns "admin_closed".
  T6: ACTIVE origin → existing behavior preserved; returns "pending_exit".
  T7: PENDING_EXIT origin → idempotent self-fold; returns "pending_exit".
  T8 (sed-flip): removing the guard block makes T1-T4 raise ValueError.

T5/T9 RETIRED (BRIDGE RETIREMENT, docs/rebuild/quarantine_excision_2026-07-11.md,
post-T5-migration): T5 pinned a raw legacy 'quarantined' origin as a no-op, and
T9 pinned that a QUARANTINED + entry_authority_quarantined chain state could
still transition to pending_exit. The T5 schema migration has run — no writer
mints 'quarantined' and the DB CHECK no longer admits the literal — and this
packet deleted the mixed-epoch tolerance branch from
enter_pending_exit_runtime_state. A raw 'quarantined' origin now falls through
to the normal UNKNOWN-phase handling and fails loudly (ValueError), matching
every other invented/retired phase string (see test_pending_entry_origin_still_raises).
"""

from __future__ import annotations

import pytest

from src.state.lifecycle_manager import (
    LifecyclePhase,
    enter_pending_exit_runtime_state,
)


# Mapping from origin runtime state → expected return value.
# Terminal-ish origin phases are no-ops; ACTIVE/holding becomes pending_exit;
# PENDING_EXIT is idempotent self-fold.
_TERMINAL_NOOP_ORIGINS = [
    ("economically_closed", LifecyclePhase.ECONOMICALLY_CLOSED.value),
    ("settled", LifecyclePhase.SETTLED.value),
    ("voided", LifecyclePhase.VOIDED.value),
    ("admin_closed", LifecyclePhase.ADMIN_CLOSED.value),
]


@pytest.mark.parametrize("origin_state,expected", _TERMINAL_NOOP_ORIGINS)
def test_terminal_origin_is_noop_not_raise(origin_state, expected):
    """T1-T5: terminal-ish origin phases must NOT raise. Sed-flip target:
    removing the guard block in enter_pending_exit_runtime_state regresses
    every one of these (raises ValueError 'illegal lifecycle phase fold')."""
    result = enter_pending_exit_runtime_state(origin_state)
    assert result == expected, (
        f"FAIL: origin={origin_state!r} → got {result!r}; expected {expected!r}. "
        f"Terminal-ish origin must be no-op (return its own phase value), "
        f"not raise ValueError. Anchor incident: 2026-05-19T23:00:39Z day0_capture crash."
    )


def test_active_origin_transitions_to_pending_exit():
    """T6: existing behavior preserved. ACTIVE → PENDING_EXIT must still work."""
    result = enter_pending_exit_runtime_state("holding")
    assert result == LifecyclePhase.PENDING_EXIT.value, (
        f"T6 FAIL: ACTIVE/holding origin must transition to pending_exit; got {result!r}"
    )


def test_pending_exit_origin_is_idempotent():
    """T7: PENDING_EXIT origin is idempotent self-fold (legal per fold table)."""
    result = enter_pending_exit_runtime_state("pending_exit")
    assert result == LifecyclePhase.PENDING_EXIT.value, (
        f"T7 FAIL: PENDING_EXIT self-fold must return pending_exit; got {result!r}"
    )


def test_day0_window_origin_transitions_to_pending_exit():
    """T6b: DAY0_WINDOW → PENDING_EXIT is a legal transition (per fold table
    line 53-60). Ensure the guard does not over-block."""
    result = enter_pending_exit_runtime_state("day0_window")
    assert result == LifecyclePhase.PENDING_EXIT.value, (
        f"T6b FAIL: DAY0_WINDOW must still transition to pending_exit; got {result!r}"
    )


def test_pending_entry_origin_still_raises():
    """T6c: PENDING_ENTRY → PENDING_EXIT is NOT in the legal fold table
    (line 36-43). Must still raise so the bug surfaces, not silently no-op."""
    with pytest.raises(ValueError, match="illegal lifecycle phase fold"):
        enter_pending_exit_runtime_state("pending_tracked")


def test_legacy_quarantined_origin_now_fails_loud():
    """BRIDGE RETIREMENT (docs/rebuild/quarantine_excision_2026-07-11.md,
    post-T5-migration): a raw 'quarantined' origin string used to be a
    special-cased no-op (T5, retired above). No writer mints it and the DB
    CHECK no longer admits the literal post-migration, so it now derives to
    UNKNOWN like any other invented/retired phase string and fails loudly,
    matching test_pending_entry_origin_still_raises."""
    with pytest.raises(ValueError, match="illegal lifecycle phase fold"):
        enter_pending_exit_runtime_state(
            "quarantined", chain_state="entry_authority_quarantined"
        )
