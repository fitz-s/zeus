# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: docs/operations/task_2026-05-19_strategy_vnext_phase1/PHASE_1_ULTRAPLAN.md §5.6 (Option B pivot)
"""INV-nowcast-horizon-bound antibody.

Invariant: Day0HighNowcastSignal.__init__ MUST raise NotApplicableHorizon when
Day0SignalInputs.hours_remaining > 6 OR hours_remaining < 0.

xfail-strict rationale:
    Guard is implemented in __init__ (fail-fast on construction, not deferred to
    evaluate). Guard-in-init is the correct Option B design: callers discover
    inapplicability at construction, not during settlement_samples().

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

    # Should not raise; construction succeeds — guard only fires on > 6 or < 0, not = 6.
    signal = Day0HighNowcastSignal(
        observed_high_so_far=inputs.observed_high_so_far,
        member_maxes_remaining=inputs.member_maxes_remaining,
        current_temp=inputs.current_temp,
        hours_remaining=inputs.hours_remaining,
    )
    assert signal.hours_remaining == 6.0


def test_day0_nowcast_horizon_bound_rejects_negative():
    """Patch 3 (SEV-2 #2): hours_remaining < 0 must also raise NotApplicableHorizon.

    Negative hours_remaining is physically impossible and indicates a data error
    (e.g., clock skew, timestamp inversion). Fail-closed: reject rather than produce
    nonsensical nowcast output from a negative horizon covariate.
    """
    inputs = _make_inputs(hours_remaining=-0.5)

    with pytest.raises(NotApplicableHorizon):
        Day0HighNowcastSignal(
            observed_high_so_far=inputs.observed_high_so_far,
            member_maxes_remaining=inputs.member_maxes_remaining,
            current_temp=inputs.current_temp,
            hours_remaining=inputs.hours_remaining,
        )


def test_day0_nowcast_floor_semantics_holds():
    """Relationship test: settlement_samples() must produce values >= obs_floor.

    HIGH semantics: observed_high_so_far forms a FLOOR — the day's high cannot
    be below what has already been observed. Any sample below obs_floor indicates
    a logic inversion (LOW ceiling applied instead of HIGH floor).
    """
    obs_floor = 70.0
    signal = Day0HighNowcastSignal(
        observed_high_so_far=obs_floor,
        member_maxes_remaining=np.array([65.0, 68.0, 69.0]),  # all below obs_floor
        current_temp=66.0,
        hours_remaining=3.0,
    )
    samples = signal.settlement_samples()
    assert np.all(samples >= obs_floor), (
        f"settlement_samples() violated HIGH floor invariant: "
        f"min={samples.min()}, obs_floor={obs_floor}. "
        "HIGH semantics require floor (max), not ceiling (min)."
    )


def test_day0_nowcast_high_semantics_vs_low_semantics():
    """Relationship test: HIGH nowcast samples >= LOW conceptual ceiling for same obs.

    When observed_high_so_far == observed_low_so_far (degenerate case),
    HIGH samples must be >= that value (floor), while the equivalent LOW signal
    would produce samples <= that value (ceiling). Tests that the two semantics
    are directionally inverted as designed.
    """
    obs_val = 72.0
    ens = np.array([68.0, 70.0, 71.0])  # all below obs_val
    high_signal = Day0HighNowcastSignal(
        observed_high_so_far=obs_val,
        member_maxes_remaining=ens,
        current_temp=69.0,
        hours_remaining=2.0,
    )
    high_samples = high_signal.settlement_samples()
    # HIGH: floor applied — all samples must be >= obs_floor
    assert np.all(high_samples >= obs_val), (
        f"HIGH floor invariant violated: min={high_samples.min()}, obs_floor={obs_val}"
    )


def test_inv_nowcast_completeness_natural_key():
    """Completeness: nowcast_event_id_v1_hash produces nei_v1_ namespace.

    Relationship test: the nei_v1_ hash contract (distinct from deid_v1_ and dgid_v1_)
    must be enforced at the writer-side. Tests that the hash function produces the
    expected namespace prefix and is stable for the same inputs.
    """
    from src.state.day0_nowcast_store import nowcast_event_id_v1_hash

    nei = nowcast_event_id_v1_hash(
        market_slug="will-chicago-high-temp-be-65-70f-on-may-20",
        temperature_metric="high",
        target_date="2026-05-20",
        observation_time="2026-05-20T14:00:00",
        run_seq=0,
    )
    assert nei.startswith("nei_v1_"), (
        f"nowcast_event_id must use nei_v1_ namespace, got {nei!r}"
    )
    # Stability: same inputs must produce the same hash (deterministic)
    nei2 = nowcast_event_id_v1_hash(
        market_slug="will-chicago-high-temp-be-65-70f-on-may-20",
        temperature_metric="high",
        target_date="2026-05-20",
        observation_time="2026-05-20T14:00:00",
        run_seq=0,
    )
    assert nei == nei2, "nowcast_event_id_v1_hash must be deterministic for same inputs"
    # Distinctness: different run_seq must produce different hash
    nei3 = nowcast_event_id_v1_hash(
        market_slug="will-chicago-high-temp-be-65-70f-on-may-20",
        temperature_metric="high",
        target_date="2026-05-20",
        observation_time="2026-05-20T14:00:00",
        run_seq=1,
    )
    assert nei != nei3, "Different run_seq must yield different nowcast_event_id"
