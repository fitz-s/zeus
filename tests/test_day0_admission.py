# Created: 2026-06-17
# Authority basis: operator delta-package v2 (real_upgrade #3) — Day0 live admission circuit breakers.
"""Contract tests for day0_live_admission_rejection_reason (8 gates + admit + bypass).

M-3 (Day0 first-principles audit 2026-07-18): `in_post_extreme_quiet_window`
(former gate 6) was deleted — see the commit body and day0_admission.py's gate
6 comment for why it was judged redundant with the strict quote>observation
ordering gate + the H-2 submit-time hard-fact re-check.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.engine.day0_admission import (
    Day0AdmissionContext,
    day0_live_admission_rejection_reason,
)

T = datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc)


def _ctx(**kw) -> Day0AdmissionContext:
    base = dict(
        event_type="DAY0_EXTREME_UPDATED", city="Chicago", metric="high",
        settlement_source_type="wu_icao", fast_obs_supported=True,
        source_health_state="OK_FAST_AND_WU", execution_mode="maker",
        quote_time_utc=T, latest_observation_available_at_utc=T - timedelta(minutes=5),
        in_final_localday_noentry_window=False,
        selected_bin_edge_distance_quanta=3.0, edge_survives_one_bin_stress=True,
        city_allowlist=frozenset({"Chicago"}),
    )
    base.update(kw)
    return Day0AdmissionContext(**base)


def test_admissible_returns_none() -> None:
    assert day0_live_admission_rejection_reason(_ctx()) is None


def test_non_day0_event_bypasses() -> None:
    assert day0_live_admission_rejection_reason(_ctx(event_type="FORECAST_SNAPSHOT_READY", city="Nowhere", city_allowlist=frozenset())) is None


def test_city_not_allowlisted() -> None:
    assert day0_live_admission_rejection_reason(_ctx(city="Lagos")) == "DAY0_CITY_NOT_ALLOWLISTED"


def test_low_metric_is_live_by_default() -> None:
    assert day0_live_admission_rejection_reason(_ctx(metric="low")) is None


def test_metric_not_in_stage_when_stage_override_excludes_it() -> None:
    assert (
        day0_live_admission_rejection_reason(
            _ctx(metric="low", metric_allowlist=frozenset({"high"}))
        )
        == "DAY0_METRIC_NOT_IN_STAGE"
    )


def test_fast_obs_unsupported() -> None:
    assert day0_live_admission_rejection_reason(_ctx(fast_obs_supported=False)) == "DAY0_FAST_OBS_UNSUPPORTED"


def test_source_health_not_admissible() -> None:
    assert day0_live_admission_rejection_reason(_ctx(source_health_state="OK_WU_ONLY")) == "DAY0_SOURCE_HEALTH_NOT_ADMISSIBLE"


def test_quote_time_missing() -> None:
    assert day0_live_admission_rejection_reason(_ctx(quote_time_utc=None)) == "DAY0_QUOTE_TIME_MISSING"


def test_quote_stale_vs_observation() -> None:
    stale = _ctx(quote_time_utc=T - timedelta(minutes=30), latest_observation_available_at_utc=T)
    assert day0_live_admission_rejection_reason(stale) == "DAY0_QUOTE_STALE_VS_OBSERVATION"


def test_quote_equal_to_observation_rejects_strict_ordering() -> None:
    # M-12: quote == observation availability cannot have priced the post-update
    # book; the ordering property is STRICT (quote > observation).
    equal = _ctx(quote_time_utc=T, latest_observation_available_at_utc=T)
    assert day0_live_admission_rejection_reason(equal) == "DAY0_QUOTE_STALE_VS_OBSERVATION"
    newer = _ctx(quote_time_utc=T, latest_observation_available_at_utc=T - timedelta(seconds=1))
    assert day0_live_admission_rejection_reason(newer) is None


def test_one_bin_edge_fragile() -> None:
    assert day0_live_admission_rejection_reason(
        _ctx(selected_bin_edge_distance_quanta=0.5, edge_survives_one_bin_stress=False)
    ) == "DAY0_ONE_BIN_EDGE_FRAGILE"
    # survives stress -> not rejected on this gate
    assert day0_live_admission_rejection_reason(
        _ctx(selected_bin_edge_distance_quanta=0.5, edge_survives_one_bin_stress=True)
    ) is None


def test_final_localday_noentry() -> None:
    assert day0_live_admission_rejection_reason(_ctx(in_final_localday_noentry_window=True)) == "DAY0_FINAL_LOCALDAY_NOENTRY"


def test_taker_entry_forbidden_until_calibrated() -> None:
    assert day0_live_admission_rejection_reason(_ctx(execution_mode="taker")) == "DAY0_TAKER_ENTRY_FORBIDDEN"
    assert day0_live_admission_rejection_reason(_ctx(execution_mode="auto_cross")) == "DAY0_TAKER_ENTRY_FORBIDDEN"
    # maker allowed; and if maker_only relaxed, taker passes this gate
    assert day0_live_admission_rejection_reason(_ctx(execution_mode="taker", maker_only_required=False)) is None
