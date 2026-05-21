# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/04_PHASE_3_SHOULDER.md §"Required object model" + docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §2 T1

"""Relationship and invariant tests for WeatherRegimeTag + regime_tag_for.

Probe coverage (plan §2 T1, §3, 10_VERIFIER_PROBES.md):
  P-3-2  : WeatherRegimeTag has exactly 6 members with exact names
  G2     : regime_tag_for returns UNKNOWN when observation history is insufficient
  R-1    : UNKNOWN regime does not aggregate into any cluster
  P-3-10 : shoulder Day0-bound xfail relationship test (pending Phase 5/6)

Tests land BEFORE production logic per Fitz methodology (relationship tests →
implementation → function tests). regime_tag_for body raises NotImplementedError
until SCAFFOLD critic PASS — stubs below verify the INTERFACE CONTRACT and
structural invariants that must hold when the body is implemented.
"""

from __future__ import annotations

import pytest

from src.contracts.weather_regime_tag import WeatherRegimeTag


# ---------------------------------------------------------------------------
# P-3-2: Enum roster — exactly 6 members with exact names
# ---------------------------------------------------------------------------

def test_weather_regime_tag_has_exactly_six_members():
    """P-3-2: WeatherRegimeTag enum has exactly 6 members per plan §2 T1."""
    members = set(WeatherRegimeTag)
    assert len(members) == 6, (
        f"Expected exactly 6 WeatherRegimeTag members, got {len(members)}: {members}"
    )


def test_weather_regime_tag_exact_member_roster():
    """P-3-2: All 6 members present with exact names per authority §"Required object model"."""
    expected = {
        WeatherRegimeTag.HEAT_DOME,
        WeatherRegimeTag.COLD_SNAP,
        WeatherRegimeTag.NORMAL,
        WeatherRegimeTag.SHOULDER_SEASON,
        WeatherRegimeTag.SOURCE_ANOMALY,
        WeatherRegimeTag.UNKNOWN,
    }
    actual = set(WeatherRegimeTag)
    assert actual == expected, (
        f"Roster mismatch.\nExpected: {expected}\nGot: {actual}"
    )


def test_weather_regime_tag_is_str_enum():
    """WeatherRegimeTag members are str-comparable (StrEnum contract)."""
    assert str(WeatherRegimeTag.HEAT_DOME) == "heat_dome"
    assert str(WeatherRegimeTag.COLD_SNAP) == "cold_snap"
    assert str(WeatherRegimeTag.NORMAL) == "normal"
    assert str(WeatherRegimeTag.SHOULDER_SEASON) == "shoulder_season"
    assert str(WeatherRegimeTag.SOURCE_ANOMALY) == "source_anomaly"
    assert str(WeatherRegimeTag.UNKNOWN) == "unknown"


def test_weather_regime_tag_members_round_trip():
    """All 6 members can be reconstructed from their string values."""
    for member in WeatherRegimeTag:
        assert WeatherRegimeTag(str(member)) is member


# ---------------------------------------------------------------------------
# G2: regime_tag_for returns UNKNOWN when observation history insufficient
# (interface contract test — body is NotImplementedError until SCAFFOLD PASS)
# ---------------------------------------------------------------------------

def test_regime_tag_for_signature_accepts_required_args():
    """regime_tag_for accepts (city, target_date, decision_time, conn) per plan §2 T1."""
    from src.contracts.weather_regime_tag import regime_tag_for
    import inspect
    sig = inspect.signature(regime_tag_for)
    params = list(sig.parameters.keys())
    assert params == ["city", "target_date", "decision_time", "conn"], (
        f"regime_tag_for signature mismatch: {params}"
    )


def test_regime_tag_for_raises_not_implemented():
    """regime_tag_for body is NotImplementedError until SCAFFOLD critic PASS (T1 contract)."""
    from src.contracts.weather_regime_tag import regime_tag_for
    with pytest.raises(NotImplementedError, match="T1 production pending SCAFFOLD critic PASS"):
        regime_tag_for("Chicago", None, None, None)  # type: ignore[arg-type]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "pending T1 body: insufficient observation history → UNKNOWN regime. "
        "regime_tag_for currently raises NotImplementedError. Once production logic lands, "
        "passing conn with empty observation_history table must return UNKNOWN (not NORMAL). "
        "strict=True: must XFAIL (NotImplementedError); a spurious PASS before body lands is an error."
    ),
)
def test_regime_tag_for_returns_unknown_when_observation_history_insufficient():
    """G2: When observation history is insufficient, classifier returns UNKNOWN — not a silent default.

    Once T1 production body lands:
    - Passing conn with empty observation_history table → UNKNOWN
    - UNKNOWN is NOT the same as NORMAL (no silent fallback to center regime)
    """
    from src.contracts.weather_regime_tag import regime_tag_for
    import datetime
    import sqlite3
    conn = sqlite3.connect(":memory:")
    result = regime_tag_for("Chicago", datetime.date(2026, 7, 15), datetime.datetime.now(), conn)
    assert result == WeatherRegimeTag.UNKNOWN, (
        f"Expected UNKNOWN for empty observation history, got {result!r}"
    )


# ---------------------------------------------------------------------------
# R-1: UNKNOWN regime does not aggregate into any cluster
# (cross-module invariant: WeatherRegimeTag.UNKNOWN → empty cluster ID)
# ---------------------------------------------------------------------------

def test_inv_unknown_regime_does_not_aggregate_cluster():
    """R-1: tail_correlation_cluster_for returns empty string for UNKNOWN regime.

    UNKNOWN regime must never contribute to a weather-system cluster for shoulder
    cap aggregation. plan §5 R-1 antibody.
    """
    from src.strategy.correlation_cluster import tail_correlation_cluster_for
    import datetime
    with pytest.raises(NotImplementedError):
        # Production body not yet landed — test structure is the deliverable
        tail_correlation_cluster_for(
            "Chicago",
            WeatherRegimeTag.UNKNOWN,
            datetime.date(2026, 7, 15),
        )
    # TODO(T1 body): When T1 production lands, replace the raises block with:
    #   cluster_id = tail_correlation_cluster_for("Chicago", WeatherRegimeTag.UNKNOWN, date(2026, 7, 15))
    #   assert cluster_id == "", f"UNKNOWN regime must produce empty cluster ID, got {cluster_id!r}"
    # This assertion is the R-1 antibody — do NOT skip it or weaken it to "truthy".


# ---------------------------------------------------------------------------
# P-3-10: Day0-bound xfail relationship test (pending Phase 5/6)
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    reason=(
        "Pending Phase 5/6 Day0BoundState 6-class upgrade per dossier §6.2. "
        "SHOULDER_DAY0_BOUND_NOT_ELIMINATED NoTradeReason lands in T2. "
        "Full wire requires BoundClassification upgrade from 3-class (current Phase 0 PR 5 scaffold) "
        "to 6-class Day0BoundState (dossier §6.2). plan §3 invariant 6."
    ),
    strict=False,
)
def test_shoulder_day0_bound_eliminates_tail():
    """P-3-10: After Day0BoundState HIGH_IMPOSSIBLE_DETERMINISTIC, upper shoulder tail is eliminated.

    Relationship: Day0BoundState.HIGH_IMPOSSIBLE_DETERMINISTIC AND source-matched
    observation → SHOULDER_DAY0_BOUND_NOT_ELIMINATED NoTradeReason is NOT triggered
    (bound has fired, tail is eliminated, shoulder sell is safe).

    Inverse: Upper shoulder sell BEFORE Day0 bound fires → SHOULDER_DAY0_BOUND_NOT_ELIMINATED
    is the rejection reason; candidate is blocked.

    This xfail becomes a real PASS after Phase 5/6 ships Day0BoundState 6-class upgrade.
    """
    pytest.fail("Day0BoundState 6-class not yet on origin/main (Phase 5/6 work)")
