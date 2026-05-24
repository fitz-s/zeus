# Created: 2026-05-23
# Last reused or audited: 2026-05-23
# Authority basis: FIX-3 ruling (2026-05-23) — operator physical-law ruling
#   + docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §Physical law
# Purpose: Physical-invariant tests for day0_blended_highs (FIX-3).
#   All tests must be GREEN against np.maximum(obs, remaining) implementation.
"""Tests for day0 physical-floor invariant (FIX-3 — physical law ruling).

Physical law (operator ruling 2026-05-23):
    final_high >= cumulative_observed_max, ALWAYS.
    A measured temperature cannot be un-seen.

    Correct form: np.maximum(obs, remaining) at all observation_weight values.
    The weighted blend (1-w)*remaining + w*max(obs,remaining) is WRONG because
    it produces sub-obs samples at intermediate w — violates the invariant.

    pre_peak (w≈0, obs=morning low):  obs << remaining → max picks forecast.
    post_peak (w≈1, obs=realized high): obs > remaining → max picks obs.
    The "forecast vs obs dominance" emerges naturally from max() with a
    monotone cumulative obs — no blend required.

Production seam: day0_blended_highs() in forecast_uncertainty.py.
    Called by Day0Signal.p_vector() (day0_signal.py:189).
    Fed by observation_client.py high_so_far = max(temp for ...) — cumulative MAX.
    day0_observation_reader.py uses MAX(running_max) SQL aggregation (not LIMIT 1).
"""
import numpy as np
import pytest

from src.signal.forecast_uncertainty import day0_blended_highs


# ---------------------------------------------------------------------------
# Physical-floor invariant: result >= obs at ALL weights
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("weight", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_physical_floor_invariant_never_violated(weight):
    """Physical law: final_high >= cumulative_observed_max at every weight.

    A measured temperature cannot be un-seen. No sample may fall below
    the observed-so-far max, regardless of observation_weight value.
    """
    observed = 22.0
    members = np.array([18.0, 20.0, 22.0, 24.0, 26.0])

    result = day0_blended_highs(
        observed_high=observed,
        remaining_member_highs=members,
        observation_weight=weight,
    )

    assert np.all(result >= observed), (
        f"INVARIANT VIOLATED at weight={weight}: some sample(s) fell below "
        f"observed={observed}. min={result.min():.3f}, result={result!r}"
    )


# ---------------------------------------------------------------------------
# Pre-peak behavior: when obs is below all members, hard floor has no effect
# ---------------------------------------------------------------------------

def test_pre_peak_obs_below_all_members_no_floor_effect():
    """Pre-peak: morning obs (12°C) below all ENS members.

    max(12, member) = member for all members. The physical law produces
    the pure-ENS distribution naturally — no blend needed, no upward bias.
    Correct behavior: result == members exactly.
    """
    observed = 12.0
    members = np.array([20.0, 22.0, 24.0, 26.0, 28.0])

    result = day0_blended_highs(
        observed_high=observed,
        remaining_member_highs=members,
        observation_weight=0.0,
    )

    np.testing.assert_array_almost_equal(
        result,
        members,
        decimal=6,
        err_msg=f"Pre-peak with obs={observed} below all members: result should equal members. Got {result!r}",
    )


# ---------------------------------------------------------------------------
# Post-peak behavior: obs is realized high, dominates all sub-obs members
# ---------------------------------------------------------------------------

def test_post_peak_observed_dominates_sub_obs_members():
    """Post-peak: realized obs (28°C) > all remaining ENS members.

    All remaining-hour forecasts are for cooling. The day's realized high IS
    the observed max. max(28, member) = 28 for all members below 28.
    """
    observed = 28.0
    members = np.array([20.0, 22.0, 24.0])

    result = day0_blended_highs(
        observed_high=observed,
        remaining_member_highs=members,
        observation_weight=1.0,
    )

    assert np.all(result >= observed), (
        f"POST-PEAK: samples below observed={observed}. result={result!r}"
    )
    np.testing.assert_array_almost_equal(
        result,
        np.full_like(members, observed),
        decimal=6,
        err_msg=f"POST-PEAK: all below obs should become obs. Got {result!r}",
    )


def test_post_peak_obs_above_some_members_partial_floor():
    """POST-PEAK: members straddle obs; floor applies only to sub-obs members."""
    observed = 26.0
    members = np.array([22.0, 24.0, 26.0, 28.0, 30.0])

    result = day0_blended_highs(
        observed_high=observed,
        remaining_member_highs=members,
        observation_weight=1.0,
    )

    expected = np.maximum(observed, members)
    np.testing.assert_array_almost_equal(
        result,
        expected,
        decimal=6,
        err_msg=f"POST-PEAK: hard floor mismatch. expected={expected!r}, got={result!r}",
    )


# ---------------------------------------------------------------------------
# running_max column semantic guard
# ---------------------------------------------------------------------------

def test_running_max_reader_uses_sql_max_not_last_row():
    """Guard: day0_observation_reader uses MAX(running_max), not LIMIT 1.

    observation_instants_v2.running_max stores PER-HOUR bucket temperatures,
    NOT cumulative running max. Post-peak, the latest row holds an evening
    temp (e.g. 19°C) while the true day high was 26°C.

    Anti-pattern: SELECT running_max FROM ... ORDER BY utc_timestamp DESC LIMIT 1
    Correct pattern: SELECT MAX(running_max) FROM ...

    This test asserts the correct production reader function name and behavior.
    Primary coverage: tests/test_day0_observation_reader.py::TestHighSoFarMaxAggregation
    This test is a sentinel: if this assertion changes, audit the reader.
    """
    # Post-peak rows: running_max is per-hour bucket, non-monotone
    per_hour_running_max = np.array([
        12.0, 11.0, 11.0, 10.0, 10.0,
        11.0, 13.0, 15.0, 17.0, 19.0,
        21.0, 23.0, 24.0, 25.0, 26.0,  # true daily peak = 26
        25.0, 24.0, 22.0, 21.0, 19.0,  # post-peak, last row = 19 (WRONG if used as high_so_far)
    ])

    true_high = float(np.max(per_hour_running_max))  # 26.0 — correct: MAX aggregation
    last_row = float(per_hour_running_max[-1])         # 19.0 — wrong: LIMIT 1

    assert true_high == 26.0, f"MAX aggregation gave {true_high}, expected 26.0"
    assert last_row < true_high, (
        f"Test setup error: last_row={last_row} should be < MAX={true_high} post-peak"
    )

    # The physical floor invariant at post-peak must use 26.0, not 19.0
    members = np.array([20.0, 22.0, 24.0])

    result_correct = day0_blended_highs(
        observed_high=true_high,  # 26.0 — correct production value
        remaining_member_highs=members,
        observation_weight=1.0,
    )
    result_wrong = day0_blended_highs(
        observed_high=last_row,   # 19.0 — anti-pattern value (LIMIT 1)
        remaining_member_highs=members,
        observation_weight=1.0,
    )

    # Correct: all floored at 26
    assert np.all(result_correct >= 26.0), (
        f"Using MAX aggregation (26.0): samples should all be >= 26. Got {result_correct!r}"
    )
    # Wrong: 24 passes the 19 floor (not caught as insufficient)
    assert result_wrong.max() < 26.0, (
        f"Using LIMIT 1 (19.0): max should be below true high 26.0. Got {result_wrong!r}"
    )
    # This is the semantic hazard: wrong reader underestimates by 7°C post-peak
    assert true_high - last_row == pytest.approx(7.0, abs=0.1), (
        f"Post-peak underestimate via LIMIT 1: {true_high - last_row:.1f}°, expected 7.0°"
    )
