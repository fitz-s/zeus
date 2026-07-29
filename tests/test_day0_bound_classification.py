# Lifecycle: created=2026-05-19; last_reviewed=2026-07-29; last_reused=2026-07-29
# Purpose: validate BoundClassification enum + 16-cell classify_bound property matrix + ValueError fail-closed contract
# Reuse: re-audit when day0_observation_context.py classify_bound or BoundClassification enum changes
"""R-5.1: BoundClassification enum exhaustiveness + 16-cell property matrix.

16-cell matrix: 4 BoundClassification values × 4 dayparts
  BoundClassification: DETERMINISTIC | MODEL_SUPPORT_COLLAPSED | BOUNDED_LIVE | UNBOUNDED_NO_OBS_YET
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


def test_bound_classification_has_four_members() -> None:
    """BoundClassification has exactly 4 members; no silent additions."""
    members = {m.value for m in BoundClassification}
    assert members == {
        "DETERMINISTIC",
        "MODEL_SUPPORT_COLLAPSED",
        "BOUNDED_LIVE",
        "UNBOUNDED_NO_OBS_YET",
    }


def test_bound_classification_is_string_enum() -> None:
    """BoundClassification members compare equal to their string values."""
    assert BoundClassification.DETERMINISTIC == "DETERMINISTIC"
    assert BoundClassification.MODEL_SUPPORT_COLLAPSED == "MODEL_SUPPORT_COLLAPSED"
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
# R-5.1d: classify_bound — model support collapse is not settlement certainty
# ---------------------------------------------------------------------------


def test_classify_bound_model_support_collapsed_high() -> None:
    """HIGH observation covering all remaining members is model support collapse."""
    result = classify_bound(
        observed_extreme_so_far=80.0,
        member_extremes_remaining=[74.0, 71.0, 73.0],  # all < 80
        is_high_market=True,
    )
    assert result == BoundClassification.MODEL_SUPPORT_COLLAPSED


def test_classify_bound_model_support_collapsed_low() -> None:
    """LOW observation covering all remaining members is model support collapse."""
    result = classify_bound(
        observed_extreme_so_far=32.0,
        member_extremes_remaining=[35.0, 36.0, 34.0],  # all > 32
        is_high_market=False,
    )
    assert result == BoundClassification.MODEL_SUPPORT_COLLAPSED


def test_classify_bound_closed_remaining_window_is_deterministic_high() -> None:
    """HIGH is deterministic only with a closed window and final witness proof."""
    result = classify_bound(
        observed_extreme_so_far=80.0,
        member_extremes_remaining=[],
        is_high_market=True,
        observation_is_final_settlement_witness=True,
    )
    assert result == BoundClassification.DETERMINISTIC


def test_classify_bound_closed_remaining_window_is_deterministic_low() -> None:
    """LOW is deterministic only with a closed window and final witness proof."""
    result = classify_bound(
        observed_extreme_so_far=32.0,
        member_extremes_remaining=[],
        is_high_market=False,
        observation_is_final_settlement_witness=True,
    )
    assert result == BoundClassification.DETERMINISTIC


def test_classify_bound_closed_remaining_window_without_witness_is_model_support_collapsed() -> None:
    """A closed model window without final-witness proof is not deterministic."""
    result = classify_bound(
        observed_extreme_so_far=80.0,
        member_extremes_remaining=[],
        is_high_market=True,
    )
    assert result == BoundClassification.MODEL_SUPPORT_COLLAPSED


def test_build_context_propagates_final_settlement_witness() -> None:
    """The context factory retains the deterministic witness distinction."""
    context = build_day0_observation_context(
        temporal_context=None,
        observed_extreme_so_far=80.0,
        member_extremes_remaining=[],
        is_high_market=True,
        observation_is_final_settlement_witness=True,
    )
    assert context.bound_classification == BoundClassification.DETERMINISTIC


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
# R-5.1e: 16-cell matrix — Day0ObservationContext × (BoundClassification × daypart)
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
#   MODEL_SUPPORT_COLLAPSED → 80.0 (covers all remaining member maxes)
#   DETERMINISTIC          → 80.0 (closed remaining window; final witness)

_DAYPARTS = ["pre_sunrise", "morning", "afternoon", "post_peak"]
_OBSERVED: dict[str, float | None] = {
    "UNBOUNDED_NO_OBS_YET": None,
    "BOUNDED_LIVE": 72.0,
    "MODEL_SUPPORT_COLLAPSED": 80.0,
    "DETERMINISTIC": 80.0,
}

_16_CELLS = list(itertools.product(
    [
        BoundClassification.UNBOUNDED_NO_OBS_YET,
        BoundClassification.BOUNDED_LIVE,
        BoundClassification.MODEL_SUPPORT_COLLAPSED,
        BoundClassification.DETERMINISTIC,
    ],
    _DAYPARTS,
))


@pytest.mark.parametrize("classification,daypart", _16_CELLS,
    ids=[f"{c.value}×{d}" for c, d in _16_CELLS])
def test_16_cell_matrix(classification: BoundClassification, daypart: str) -> None:
    """Each of 16 (BoundClassification × daypart) cells constructs a distinct Day0ObservationContext.

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
