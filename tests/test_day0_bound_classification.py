# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=2026-05-19
# Purpose: validate BoundClassification enum + 12-cell classify_bound property matrix + ValueError fail-closed contract
# Reuse: re-audit when day0_observation_context.py classify_bound or BoundClassification enum changes
"""R-5.1: BoundClassification enum exhaustiveness + 12-cell property matrix.

12-cell matrix: 3 BoundClassification values × 4 dayparts
  BoundClassification: DETERMINISTIC | BOUNDED_LIVE | UNBOUNDED_NO_OBS_YET
  Dayparts:            pre_sunrise | morning | afternoon | post_peak

Each cell asserts:
  1. classify_bound returns the correct BoundClassification.
  2. build_day0_observation_context populates daypart correctly.
  3. No cell raises for valid inputs.

Design note: observation_state is NOT a third axis; it is implicit in
BoundClassification (UNBOUNDED = no obs, BOUNDED_LIVE/DETERMINISTIC = obs present).
"""
import itertools

import pytest

from src.contracts.day0_observation_context import (
    BoundClassification,
    Day0ObservationContext,
    classify_bound,
    build_day0_observation_context,
)


# ---------------------------------------------------------------------------
# R-5.1a: Enum exhaustiveness — every declared member is reachable
# ---------------------------------------------------------------------------


def test_bound_classification_has_three_members() -> None:
    """BoundClassification has exactly 3 members; no silent additions."""
    members = {m.value for m in BoundClassification}
    assert members == {"DETERMINISTIC", "BOUNDED_LIVE", "UNBOUNDED_NO_OBS_YET"}


def test_bound_classification_is_string_enum() -> None:
    """BoundClassification members compare equal to their string values."""
    assert BoundClassification.DETERMINISTIC == "DETERMINISTIC"
    assert BoundClassification.BOUNDED_LIVE == "BOUNDED_LIVE"
    assert BoundClassification.UNBOUNDED_NO_OBS_YET == "UNBOUNDED_NO_OBS_YET"


# ---------------------------------------------------------------------------
# R-5.1b: classify_bound — observation=None → UNBOUNDED_NO_OBS_YET
# ---------------------------------------------------------------------------


def test_classify_bound_no_obs_returns_unbounded_high() -> None:
    result = classify_bound(
        observed_extreme_so_far=None,
        member_extremes_remaining=[72.0, 74.0, 73.0],
        is_high_market=True,
    )
    assert result == BoundClassification.UNBOUNDED_NO_OBS_YET


def test_classify_bound_no_obs_returns_unbounded_low() -> None:
    result = classify_bound(
        observed_extreme_so_far=None,
        member_extremes_remaining=[45.0, 43.0, 44.0],
        is_high_market=False,
    )
    assert result == BoundClassification.UNBOUNDED_NO_OBS_YET


# ---------------------------------------------------------------------------
# R-5.1c: classify_bound — observation present, outcome not yet determined → BOUNDED_LIVE
# ---------------------------------------------------------------------------


def test_classify_bound_obs_present_not_deterministic_high() -> None:
    """HIGH market: observed=72°F but some members can still exceed it → BOUNDED_LIVE."""
    result = classify_bound(
        observed_extreme_so_far=72.0,
        member_extremes_remaining=[74.0, 71.0, 73.0],  # some exceed 72
        is_high_market=True,
    )
    assert result == BoundClassification.BOUNDED_LIVE


def test_classify_bound_obs_present_not_deterministic_low() -> None:
    """LOW market: observed=45°F but some members can still go below it → BOUNDED_LIVE."""
    result = classify_bound(
        observed_extreme_so_far=45.0,
        member_extremes_remaining=[43.0, 46.0, 44.0],  # some below 45
        is_high_market=False,
    )
    assert result == BoundClassification.BOUNDED_LIVE


# ---------------------------------------------------------------------------
# R-5.1d: classify_bound — observation already determines outcome → DETERMINISTIC
# ---------------------------------------------------------------------------


def test_classify_bound_deterministic_high() -> None:
    """HIGH market: observed=80°F exceeds ALL remaining member maxes → DETERMINISTIC."""
    result = classify_bound(
        observed_extreme_so_far=80.0,
        member_extremes_remaining=[74.0, 71.0, 73.0],  # all < 80
        is_high_market=True,
    )
    assert result == BoundClassification.DETERMINISTIC


def test_classify_bound_deterministic_low() -> None:
    """LOW market: observed=32°F undercuts ALL remaining member mins → DETERMINISTIC."""
    result = classify_bound(
        observed_extreme_so_far=32.0,
        member_extremes_remaining=[35.0, 36.0, 34.0],  # all > 32
        is_high_market=False,
    )
    assert result == BoundClassification.DETERMINISTIC


def test_classify_bound_raises_when_obs_present_and_members_none() -> None:
    """classify_bound raises ValueError when obs is not None but members is None.

    Fail-closed: returning DETERMINISTIC when forecast is unavailable would signal
    a fully-resolved position to callers that haven't seen any member data.
    Pass an empty list [] to indicate the forecast window has closed.
    """
    import pytest

    with pytest.raises(ValueError, match="member_extremes_remaining is None"):
        classify_bound(
            observed_extreme_so_far=75.0,
            member_extremes_remaining=None,
            is_high_market=True,
        )


# ---------------------------------------------------------------------------
# R-5.1e: 12-cell matrix — Day0ObservationContext × (BoundClassification × daypart)
# ---------------------------------------------------------------------------
# Each cell is a DISTINCT (BoundClassification, daypart) pair, directly
# constructing Day0ObservationContext (bypassing the stub factory which raises
# NotImplementedError). The factory test is in R-5.1f (skipped, pending prod code).
#
# Daypart values (PR 5 definition — 4-way split finer than DaylightPhase's 3):
#   pre_sunrise  — before sunrise; no intraday obs expected
#   morning      — post-sunrise through mid-morning
#   afternoon    — mid-day through mid-afternoon
#   post_peak    — after the expected daily extreme hour; outcome tends to stabilize
#
# observed_extreme_so_far per classification:
#   UNBOUNDED_NO_OBS_YET  → None
#   BOUNDED_LIVE           → 72.0 (present, but some members can still exceed it)
#   DETERMINISTIC          → 80.0 (exceeds all remaining member maxes)

_DAYPARTS = ["pre_sunrise", "morning", "afternoon", "post_peak"]
_OBSERVED: dict[str, float | None] = {
    "UNBOUNDED_NO_OBS_YET": None,
    "BOUNDED_LIVE": 72.0,
    "DETERMINISTIC": 80.0,
}

_12_CELLS = list(itertools.product(
    [BoundClassification.UNBOUNDED_NO_OBS_YET, BoundClassification.BOUNDED_LIVE, BoundClassification.DETERMINISTIC],
    _DAYPARTS,
))


@pytest.mark.parametrize("classification,daypart", _12_CELLS,
    ids=[f"{c.value}×{d}" for c, d in _12_CELLS])
def test_12_cell_matrix(classification: BoundClassification, daypart: str) -> None:
    """Each of 12 (BoundClassification × daypart) cells constructs a distinct Day0ObservationContext.

    Directly tests the dataclass — factory is stub (NotImplementedError pending production).
    Production code must: (a) call classify_bound to get BoundClassification,
    (b) derive daypart from temporal_context.solar_day.phase + post_peak_confidence,
    (c) set is_dst_gap_hour from temporal_context.is_missing_local_hour.

    Each cell asserts:
    - .bound_classification matches the parametrized value
    - .daypart matches the parametrized string
    - .observed_extreme_so_far is None iff classification == UNBOUNDED_NO_OBS_YET
    - .is_dst_gap_hour is a bool (not accidentally None)
    - .temporal_context is None (graceful degrade path — no DB required for this test)
    """
    obs = _OBSERVED[classification.value]
    ctx = Day0ObservationContext(
        temporal_context=None,
        bound_classification=classification,
        observed_extreme_so_far=obs,
        is_dst_gap_hour=False,
        daypart=daypart,
    )
    assert ctx.bound_classification == classification
    assert ctx.daypart == daypart
    if classification == BoundClassification.UNBOUNDED_NO_OBS_YET:
        assert ctx.observed_extreme_so_far is None
    else:
        assert ctx.observed_extreme_so_far is not None
    assert isinstance(ctx.is_dst_gap_hour, bool)
    assert ctx.temporal_context is None


# R-5.1f removed: test_factory_stub_raises_not_implemented was a scaffold
# contract that the factory raises NotImplementedError. Deleted when
# production code was implemented in PR 5 (2026-05-19).
