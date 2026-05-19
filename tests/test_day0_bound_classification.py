# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md PR 5 row; INV-09
# SCAFFOLD ONLY — all tests are @pytest.mark.skip pending PR 5 production code.
# See docs/operations/task_2026-05-17_strategy_vnext_phase0/scaffolds/pr5_scaffold_report.md
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
import pytest

from src.contracts.day0_observation_context import (
    BoundClassification,
    classify_bound,
    build_day0_observation_context,
)


# ---------------------------------------------------------------------------
# R-5.1a: Enum exhaustiveness — every declared member is reachable
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
def test_bound_classification_has_three_members() -> None:
    """BoundClassification has exactly 3 members; no silent additions."""
    members = {m.value for m in BoundClassification}
    assert members == {"DETERMINISTIC", "BOUNDED_LIVE", "UNBOUNDED_NO_OBS_YET"}


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
def test_bound_classification_is_string_enum() -> None:
    """BoundClassification members compare equal to their string values."""
    assert BoundClassification.DETERMINISTIC == "DETERMINISTIC"
    assert BoundClassification.BOUNDED_LIVE == "BOUNDED_LIVE"
    assert BoundClassification.UNBOUNDED_NO_OBS_YET == "UNBOUNDED_NO_OBS_YET"


# ---------------------------------------------------------------------------
# R-5.1b: classify_bound — observation=None → UNBOUNDED_NO_OBS_YET
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
def test_classify_bound_no_obs_returns_unbounded_high() -> None:
    result = classify_bound(
        observed_extreme_so_far=None,
        member_extremes_remaining=[72.0, 74.0, 73.0],
        is_high_market=True,
    )
    assert result == BoundClassification.UNBOUNDED_NO_OBS_YET


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
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


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
def test_classify_bound_obs_present_not_deterministic_high() -> None:
    """HIGH market: observed=72°F but some members can still exceed it → BOUNDED_LIVE."""
    result = classify_bound(
        observed_extreme_so_far=72.0,
        member_extremes_remaining=[74.0, 71.0, 73.0],  # some exceed 72
        is_high_market=True,
    )
    assert result == BoundClassification.BOUNDED_LIVE


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
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


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
def test_classify_bound_deterministic_high() -> None:
    """HIGH market: observed=80°F exceeds ALL remaining member maxes → DETERMINISTIC."""
    result = classify_bound(
        observed_extreme_so_far=80.0,
        member_extremes_remaining=[74.0, 71.0, 73.0],  # all < 80
        is_high_market=True,
    )
    assert result == BoundClassification.DETERMINISTIC


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
def test_classify_bound_deterministic_low() -> None:
    """LOW market: observed=32°F undercuts ALL remaining member mins → DETERMINISTIC."""
    result = classify_bound(
        observed_extreme_so_far=32.0,
        member_extremes_remaining=[35.0, 36.0, 34.0],  # all > 32
        is_high_market=False,
    )
    assert result == BoundClassification.DETERMINISTIC


# ---------------------------------------------------------------------------
# R-5.1e: 12-cell matrix — build_day0_observation_context × (classification × daypart)
# ---------------------------------------------------------------------------
# Each cell: (BoundClassification, daypart) pair.
# Daypart values: pre_sunrise | morning | afternoon | post_peak
# Production code must populate .daypart correctly from temporal_context.

_DAYPARTS = ["pre_sunrise", "morning", "afternoon", "post_peak"]


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
@pytest.mark.parametrize("daypart", _DAYPARTS)
def test_12_cell_matrix_unbounded_no_obs_yet(daypart: str) -> None:
    """UNBOUNDED_NO_OBS_YET × 4 dayparts: context builds without error, daypart set."""
    ctx = build_day0_observation_context(
        temporal_context=None,  # temporal_context may be None on DB degrade
        observed_extreme_so_far=None,
        member_extremes_remaining=[72.0, 73.0, 71.0],
        is_high_market=True,
    )
    assert ctx.bound_classification == BoundClassification.UNBOUNDED_NO_OBS_YET
    assert ctx.daypart == daypart  # SCAFFOLD: production code sets daypart from temporal_context


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
@pytest.mark.parametrize("daypart", _DAYPARTS)
def test_12_cell_matrix_bounded_live(daypart: str) -> None:
    """BOUNDED_LIVE × 4 dayparts: context builds without error, daypart set."""
    ctx = build_day0_observation_context(
        temporal_context=None,
        observed_extreme_so_far=72.0,
        member_extremes_remaining=[74.0, 71.0, 73.0],
        is_high_market=True,
    )
    assert ctx.bound_classification == BoundClassification.BOUNDED_LIVE
    assert ctx.daypart == daypart


@pytest.mark.skip(reason="SCAFFOLD only — PR 5 production code pending")
@pytest.mark.parametrize("daypart", _DAYPARTS)
def test_12_cell_matrix_deterministic(daypart: str) -> None:
    """DETERMINISTIC × 4 dayparts: context builds without error, daypart set."""
    ctx = build_day0_observation_context(
        temporal_context=None,
        observed_extreme_so_far=80.0,
        member_extremes_remaining=[74.0, 71.0, 73.0],
        is_high_market=True,
    )
    assert ctx.bound_classification == BoundClassification.DETERMINISTIC
    assert ctx.daypart == daypart
