# Created: 2026-07-18
# Last reused or audited: 2026-07-19
# Authority basis: docs/evidence/upstream_physical_2026_07_17/day0_mechanism_first_principles_audit.md
#   §M-1 (stale monitor bound, no margin) + §M-2/§H-3 (coverage count, not contiguity).
# Purpose: antibodies for the two monitor-lane fixes:
#   M-2/H-3 — GAP_SUSPECT coverage serves the monitor as a ONE-SIDED bound only
#             (never exit authority), and only for the attributed metric.
#   M-1     — stale evidence never moves an absorbing observed extreme inward
#             and remains non-actionable until the missing interval is bounded.
"""Deep-path tests for _refresh_day0_observation gap/staleness handling."""
from __future__ import annotations

import types
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from src.types import Bin


def _city():
    return types.SimpleNamespace(
        name="Buenos Aires",
        lat=-34.6037,
        timezone="America/Argentina/Buenos_Aires",
        cluster="South America",
        settlement_unit="C",
        settlement_source_type="wu_icao",
        wu_station="SABE",
    )


def _position(metric="high", bin_label="30°C"):
    return types.SimpleNamespace(
        temperature_metric=metric,
        bin_label=bin_label,
        unit="C",
        market_id="m-gap-test",
        direction="buy_yes",
        p_posterior=0.4,
        selected_method="day0_observation",
        entry_method="day0_observation",
    )


def _obs(
    *,
    now,
    age_minutes=10.0,
    coverage_status="OK",
    gap_suspect_metrics=None,
    max_gap_minutes=None,
    high_so_far=30.0,
    low_so_far=18.0,
):
    obs_time = (now - timedelta(minutes=age_minutes)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return types.SimpleNamespace(
        high_so_far=high_so_far,
        low_so_far=low_so_far,
        current_temp=25.0,
        source="wu_icao_history",
        observation_time=obs_time,
        observation_available_at=obs_time,
        coverage_status=coverage_status,
        gap_suspect_metrics=gap_suspect_metrics,
        max_gap_minutes=max_gap_minutes,
    )


@pytest.fixture
def wired(monkeypatch):
    """Wire the deep _refresh_day0_observation path with fakes; capture router inputs."""
    from src.engine import monitor_refresh

    captured: dict[str, object] = {}
    now = datetime.now(timezone.utc)

    monkeypatch.setattr(
        monitor_refresh, "_day0_observation_source_rejection_reason",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "src.signal.diurnal.build_day0_temporal_context",
        lambda *a, **k: types.SimpleNamespace(
            daypart="post_peak",
            post_peak_confidence=0.9,
            current_utc_timestamp=now,
            solar_day=None,
            current_local_hour=18.0,
            daylight_progress=1.0,
        ),
    )
    monkeypatch.setattr(monitor_refresh, "_read_day0_hourly_vectors", lambda **k: None)
    monkeypatch.setattr(
        monitor_refresh, "_read_day0_raw_model_extrema",
        lambda **k: {
            "member_extrema": np.array([28.0, 29.0, 31.0]),
            "source_id": "openmeteo_single_runs",
            "forecast_source_role": "day0_remaining_window_live",
            "source_cycle_time": "2026-07-18T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(monitor_refresh, "_local_hours_remaining", lambda *a, **k: 5.0)
    monkeypatch.setattr(
        monitor_refresh, "_day0_observed_extreme_from_canonical_surface",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        monitor_refresh, "_day0_extreme_authority_rejection_reason",
        lambda **k: None,
    )
    monkeypatch.setattr(
        monitor_refresh, "_build_all_bins",
        lambda *a, **k: (
            [
                Bin(low=28, high=28, label="28°C", unit="C"),
                Bin(low=29, high=29, label="29°C", unit="C"),
                Bin(low=30, high=30, label="30°C", unit="C"),
                Bin(low=31, high=31, label="31°C", unit="C"),
            ],
            2,
        ),
    )

    def _fake_route(inputs):
        captured["inputs"] = inputs
        return types.SimpleNamespace(
            p_vector=lambda bins, n_mc=None: np.array([0.1, 0.2, 0.5, 0.2])
        )

    monkeypatch.setattr(monitor_refresh.Day0Router, "route", _fake_route)
    monkeypatch.setattr(monitor_refresh, "_maybe_write_day0_nowcast", lambda **k: None)
    monkeypatch.setattr(monitor_refresh, "_maybe_write_day0_metric_fact", lambda **k: None)
    # Deterministic staleness budget (default config lookup would also give 100
    # for unknown cities, but pin it against config drift).
    monkeypatch.setattr(
        "src.signal.day0_obs_latency.staleness_budget_minutes",
        lambda city, **k: 100.0,
    )
    return monitor_refresh, captured, now, monkeypatch


def _run(monitor_refresh, position, obs, monkeypatch):
    monkeypatch.setattr(monitor_refresh, "_fetch_day0_observation", lambda *a, **k: obs)
    return monitor_refresh._refresh_day0_observation(
        position=position,
        current_p_market=0.3,
        conn=None,
        city=_city(),
        target_d=date.today(),
    )


class TestGapSuspectMonitorBoundOnly:
    """M-2/H-3: a metric-relevant gap remains visible but non-actionable."""

    def test_gap_suspect_serves_bound_only_and_blocks_exit_authority(self, wired):
        monitor_refresh, captured, now, monkeypatch = wired
        position = _position()
        obs = _obs(
            now=now, coverage_status="GAP_SUSPECT",
            gap_suspect_metrics=("high",), max_gap_minutes=240.0,
        )

        prob, applied = _run(monitor_refresh, position, obs, monkeypatch)

        assert getattr(position, monitor_refresh._MONITOR_PROBABILITY_FRESH_ATTR) is False
        assert prob == pytest.approx(0.4)
        assert "day0_observation_bound_only:coverage_gap_suspect" in applied
        assert "day0_bound_only_probability_not_actionable" in applied
        receipt = position._day0_monitor_probability_receipt
        assert receipt["zero_probability_exit_authority"] is False
        assert receipt["zero_probability_exit_authority_reason"] == (
            "coverage_gap_suspect_not_hard_fact"
        )
        assert receipt["observation"]["max_gap_minutes"] == pytest.approx(240.0)

    def test_gap_suspect_for_other_metric_keeps_authority(self, wired):
        """A LOW-window hole must not degrade a HIGH market's monitor lane."""
        monitor_refresh, captured, now, monkeypatch = wired
        position = _position()
        obs = _obs(
            now=now, coverage_status="GAP_SUSPECT",
            gap_suspect_metrics=("low",), max_gap_minutes=240.0,
        )

        prob, applied = _run(monitor_refresh, position, obs, monkeypatch)

        assert "day0_observation_bound_only:coverage_gap_suspect" not in applied
        receipt = position._day0_monitor_probability_receipt
        assert receipt["zero_probability_exit_authority"] is True
        assert receipt["zero_probability_exit_authority_reason"] == "mature_day0_extreme"

    def test_gap_suspect_without_attribution_fails_closed(self, wired):
        """Producer gave no gap_suspect_metrics: treat every metric as suspect."""
        monitor_refresh, captured, now, monkeypatch = wired
        position = _position()
        obs = _obs(
            now=now, coverage_status="GAP_SUSPECT",
            gap_suspect_metrics=None, max_gap_minutes=None,
        )

        _, applied = _run(monitor_refresh, position, obs, monkeypatch)

        assert "day0_observation_bound_only:coverage_gap_suspect" in applied
        assert getattr(position, monitor_refresh._MONITOR_PROBABILITY_FRESH_ATTR) is False
        receipt = position._day0_monitor_probability_receipt
        assert receipt["zero_probability_exit_authority"] is False


class TestStaleObservationPhysicalSupport:
    """M-1: staleness cannot reverse an already observed physical extreme."""

    def test_fresh_observation_margin_inert(self, wired):
        monitor_refresh, captured, now, monkeypatch = wired
        position = _position()
        obs = _obs(now=now, age_minutes=10.0)

        _, applied = _run(monitor_refresh, position, obs, monkeypatch)

        inputs = captured["inputs"]
        assert inputs.observed_high_so_far == pytest.approx(30.0)
        assert not any(a.startswith("day0_stale_bound_margin_applied") for a in applied)
        receipt = position._day0_monitor_probability_receipt
        assert receipt["observation"]["stale_bound_margin_native"] == pytest.approx(0.0)
        assert receipt["observation"]["belief_observed_high_so_far"] == pytest.approx(30.0)

    def test_stale_high_keeps_absorbing_floor_and_is_not_actionable(self, wired):
        monitor_refresh, captured, now, monkeypatch = wired
        position = _position()
        obs = _obs(now=now, age_minutes=220.0)
        stale_reason = (
            "Day0 observation is stale for executable probability generation: "
            "city=Buenos Aires age_hours=3.667 max_age_hours=1.000"
        )
        monkeypatch.setattr(
            monitor_refresh, "_day0_observation_quality_rejection_reason",
            lambda *a, **k: stale_reason,
        )
        monkeypatch.setattr(
            monitor_refresh, "_stale_day0_observation_can_remain_monitor_authority",
            lambda **k: True,
        )

        prob, applied = _run(monitor_refresh, position, obs, monkeypatch)

        inputs = captured["inputs"]
        assert inputs.observed_high_so_far == pytest.approx(30.0)
        assert prob == pytest.approx(0.4)
        assert getattr(position, monitor_refresh._MONITOR_PROBABILITY_FRESH_ATTR) is False
        assert "day0_bound_only_probability_not_actionable" in applied
        receipt = position._day0_monitor_probability_receipt
        assert receipt["observation"]["observed_high_so_far"] == pytest.approx(30.0)
        assert receipt["observation"]["belief_observed_high_so_far"] == pytest.approx(30.0)
        assert receipt["observation"]["stale_bound_margin_native"] == pytest.approx(0.0)

    def test_stale_low_keeps_absorbing_ceiling_and_is_not_actionable(self, wired):
        monitor_refresh, captured, now, monkeypatch = wired
        position = _position(metric="low", bin_label="18°C")
        obs = _obs(now=now, age_minutes=220.0)
        stale_reason = (
            "Day0 observation is stale for executable probability generation: "
            "city=Buenos Aires age_hours=3.667 max_age_hours=1.000"
        )
        monkeypatch.setattr(
            monitor_refresh, "_day0_observation_quality_rejection_reason",
            lambda *a, **k: stale_reason,
        )
        monkeypatch.setattr(
            monitor_refresh, "_stale_day0_observation_can_remain_monitor_authority",
            lambda **k: True,
        )

        _, applied = _run(monitor_refresh, position, obs, monkeypatch)

        inputs = captured["inputs"]
        assert inputs.observed_low_so_far == pytest.approx(18.0)
        assert getattr(position, monitor_refresh._MONITOR_PROBABILITY_FRESH_ATTR) is False
        assert "day0_bound_only_probability_not_actionable" in applied
        receipt = position._day0_monitor_probability_receipt
        assert receipt["observation"]["observed_low_so_far"] == pytest.approx(18.0)
        assert receipt["observation"]["belief_observed_low_so_far"] == pytest.approx(18.0)

    def test_absorbing_high_distribution_has_no_mass_below_observation(self):
        from src.signal.day0_high_distribution import build_day0_high_distribution

        outcomes = build_day0_high_distribution(
            observed_high_so_far=30.0,
            future_member_maxes=np.array([25.0, 26.0, 27.0, 28.0, 30.0]),
            round_fn=lambda values: np.asarray(values),
            precision=1.0,
        )
        assert np.all(outcomes >= 30.0)
        assert np.mean(outcomes == 30.0) == pytest.approx(1.0)
