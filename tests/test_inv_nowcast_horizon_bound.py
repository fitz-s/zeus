# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md §5.6 (Option B pivot)
"""INV-nowcast-horizon-bound antibody.

Invariant: Day0HighNowcastSignal.__init__ MUST raise NotApplicableHorizon when
Day0SignalInputs.hours_remaining > 6.

xfail-strict rationale:
    SCAFFOLD stub: Day0HighNowcastSignal.__init__ raises NotApplicableHorizon
    on construction when hours_remaining > 6 — guard is implemented in __init__
    (fail-fast on construction, not deferred to evaluate). This test is NOT xfail
    in SCAFFOLD — the guard fires immediately. xfail-strict is removed here and the
    test runs as a STRICT PASS in SCAFFOLD.

    If the guard were deferred to settlement_samples(), it would be xfail (since
    settlement_samples() raises NotImplementedError first). Guard-in-init is the
    correct Option B design: callers discover inapplicability at construction.

Relationship: Cross-module invariant (Fitz §3 pattern). Tests that Day0HighNowcastSignal
(src/signal/day0_high_nowcast_signal.py) correctly enforces the horizon applicability
boundary using the canonical Day0SignalInputs.hours_remaining field — NOT a phantom
market.max_hours_to_resolution field.
"""
import numpy as np
import pytest

from src.signal.day0_high_nowcast_signal import Day0HighNowcastSignal, NotApplicableHorizon
from src.signal.day0_router import Day0SignalInputs
from src.types.metric_identity import HIGH_LOCALDAY_MAX


def _make_inputs(hours_remaining: float) -> Day0SignalInputs:
    """Minimal Day0SignalInputs stub with hours_remaining set."""
    return Day0SignalInputs(
        temperature_metric=HIGH_LOCALDAY_MAX,
        current_temp=72.0,
        hours_remaining=hours_remaining,
        observed_high_so_far=68.0,
        observed_low_so_far=None,
        member_maxes_remaining=np.array([71.0, 73.0, 74.0]),
        member_mins_remaining=None,
    )


def test_day0_nowcast_horizon_bound_enforces_6h_ceiling():
    """INV-nowcast-horizon-bound: constructor raises NotApplicableHorizon for hours_remaining > 6.

    Uses canonical Day0SignalInputs.hours_remaining (day0_router.py:52) — NOT a phantom
    market.max_hours_to_resolution field. Guard fires on construction (fail-fast design).

    Fail-closed: markets with hours_remaining > 6 must fall back to Day0HighSignal
    (ensemble path). Silent construction of a nowcast signal for long-horizon markets
    would produce miscalibrated output in the decision pipeline.
    """
    inputs = _make_inputs(hours_remaining=8.0)

    with pytest.raises(NotApplicableHorizon):
        Day0HighNowcastSignal(
            observed_high_so_far=inputs.observed_high_so_far,
            member_maxes_remaining=inputs.member_maxes_remaining,
            current_temp=inputs.current_temp,
            hours_remaining=inputs.hours_remaining,
        )


def test_day0_nowcast_horizon_bound_allows_within_ceiling():
    """Boundary: hours_remaining <= 6 must NOT raise NotApplicableHorizon."""
    inputs = _make_inputs(hours_remaining=6.0)

    # Should not raise; settlement_samples() raises NotImplementedError (SCAFFOLD)
    # but construction succeeds — guard only fires on > 6, not = 6.
    signal = Day0HighNowcastSignal(
        observed_high_so_far=inputs.observed_high_so_far,
        member_maxes_remaining=inputs.member_maxes_remaining,
        current_temp=inputs.current_temp,
        hours_remaining=inputs.hours_remaining,
    )
    assert signal.hours_remaining == 6.0
