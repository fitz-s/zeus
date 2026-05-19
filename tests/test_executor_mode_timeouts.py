# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: operator codereview-may19 P1-1; .omc/plans/2026-05-19-codereview-may19-p11-imminent-timeout.md
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Antibody — IMMINENT_OPEN_CAPTURE mode has a registered MODE_TIMEOUTS entry
#          so cycle_runtime._mode_timeout_seconds() does not raise at execute_intent boundary.
"""Antibody tests: every discovery mode registered with the scheduler MUST
have a corresponding MODE_TIMEOUTS entry, otherwise live final-intent path
crashes with "Unknown execution mode" before submit.

Root cause (2026-05-19 alpha-loss session): main.py scheduler adds
DiscoveryMode.IMMINENT_OPEN_CAPTURE (added in PR #205) but executor.py's
MODE_TIMEOUTS dictionary was never updated. cycle_runtime._mode_timeout_seconds()
imports MODE_TIMEOUTS and raises if the key is absent — every candidate the
imminent mode found crashed at this boundary BEFORE reaching the venue submit
call. Daemon decision_log id=1116/1117 confirmed 47-52 candidates per cycle,
0 entry orders submitted, all 13 control gates CLEAR.

Sed-flip verification: remove the "imminent_open_capture" key from
MODE_TIMEOUTS → test_mode_timeout_seconds_accepts_imminent_open_capture
raises RuntimeError → RED.
"""

from __future__ import annotations

import pytest


def test_imminent_open_capture_has_a_timeout():
    """The mode MUST be present in MODE_TIMEOUTS."""
    from src.execution.executor import MODE_TIMEOUTS

    assert "imminent_open_capture" in MODE_TIMEOUTS, (
        "P1-1 antibody FAIL: imminent_open_capture missing from MODE_TIMEOUTS. "
        "Every candidate the scheduler-registered imminent mode discovers will "
        "crash at cycle_runtime._mode_timeout_seconds() with 'Unknown execution mode' "
        "before reaching venue submit. Live trading is dead-on-arrival."
    )
    timeout = MODE_TIMEOUTS["imminent_open_capture"]
    assert isinstance(timeout, int) and timeout > 0, (
        f"P1-1 antibody FAIL: imminent_open_capture timeout={timeout!r} is not a "
        f"positive int. Must be seconds-of-fill-window."
    )


def test_mode_timeout_seconds_accepts_imminent_open_capture():
    """The runtime resolver must NOT raise for imminent_open_capture.

    Sed-flip: deleting the imminent_open_capture entry from MODE_TIMEOUTS
    makes this call raise RuntimeError (via the `if normalized not in
    MODE_TIMEOUTS` guard at cycle_runtime.py:357).
    """
    from src.engine.cycle_runtime import _mode_timeout_seconds

    # Must not raise.
    timeout = _mode_timeout_seconds("imminent_open_capture")
    assert isinstance(timeout, int) and timeout > 0, (
        f"P1-1 antibody FAIL: _mode_timeout_seconds returned {timeout!r}; "
        f"expected a positive int (seconds-of-fill-window)."
    )


def test_imminent_open_capture_timeout_mirrors_day0_capture():
    """imminent_open_capture and day0_capture share the 0-24h fast-resolve
    window semantics, so their timeouts should track each other. Tuning one
    without the other is a structural bug — both modes operate on the same
    market class. Keeping them equal makes the relationship explicit so future
    edits don't accidentally diverge them."""
    from src.execution.executor import MODE_TIMEOUTS

    assert MODE_TIMEOUTS["imminent_open_capture"] == MODE_TIMEOUTS["day0_capture"], (
        f"P1-1 contract drift: imminent_open_capture={MODE_TIMEOUTS['imminent_open_capture']} "
        f"!= day0_capture={MODE_TIMEOUTS['day0_capture']}. Both modes operate on the same "
        f"0-24h fast-resolve market class; their timeouts should track each other."
    )


def test_every_scheduler_registered_discovery_mode_has_a_timeout():
    """Cross-module relationship invariant: every DiscoveryMode that is added
    to the scheduler in main.py MUST have a corresponding MODE_TIMEOUTS entry.
    This invariant test prevents future drift — if a new mode is added to the
    scheduler without registering its timeout, this test fires immediately."""
    from src.engine.cycle_runner import DiscoveryMode
    from src.execution.executor import MODE_TIMEOUTS

    # Modes that are scheduler-registered for live discovery.
    # Update this set whenever a new live-discovery mode lands in main.py.
    live_discovery_modes = {
        DiscoveryMode.OPENING_HUNT.value,
        DiscoveryMode.UPDATE_REACTION.value,
        DiscoveryMode.DAY0_CAPTURE.value,
        DiscoveryMode.IMMINENT_OPEN_CAPTURE.value,
    }
    missing = live_discovery_modes - set(MODE_TIMEOUTS.keys())
    assert not missing, (
        f"Cross-module invariant FAIL: live-discovery modes registered with "
        f"the scheduler but missing from MODE_TIMEOUTS: {sorted(missing)}. "
        f"Every such mode will crash at cycle_runtime._mode_timeout_seconds() "
        f"before reaching venue submit. Register the mode in "
        f"src/execution/executor.py:MODE_TIMEOUTS."
    )
