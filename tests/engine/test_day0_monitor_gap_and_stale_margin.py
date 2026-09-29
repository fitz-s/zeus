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
    monkeypatch.setattr(
        monitor_refresh, "_day0_observed_extreme_from_canonical_surface",
        lambda *a, **k: None,
    )

    def _fake_route(inputs):
        captured["inputs"] = inputs
        return types.SimpleNamespace(
            p_vector=lambda bins, n_mc=None: np.array([0.1, 0.2, 0.5, 0.2])
        )

    # Deterministic staleness budget (default config lookup would also give 100
    # for unknown cities, but pin it against config drift).
    monkeypatch.setattr(
        "src.signal.day0_obs_latency.staleness_budget_minutes",
        lambda city, **k: 100.0,
    )
    return monitor_refresh, captured, now, monkeypatch


class TestStaleObservationPhysicalSupport:
    """M-1: staleness cannot reverse an already observed physical extreme."""

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
