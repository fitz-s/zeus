# Created: 2026-06-15
# Last reused or audited: 2026-06-15
# Authority basis: GOAL #83 / task #118 L4 — the reactor's bounded per-cycle decision budget
#   (~3-4 slow family decisions) was 100% consumed by the Tier-0 DAY0_EXTREME_UPDATED lane,
#   structurally starving the Tier-1 FORECAST_SNAPSHOT_READY (rebuilt-spine) lane of processing
#   budget (measured: 0 FSR ever claimed). _fair_lane_interleave round-robins the two decision
#   lanes 1:1 so the spine lane always gets a fair half of the budget. Order-only; per-lane
#   (per-city-fair) order preserved.
"""Antibody for the fair cross-lane interleave that un-starves the spine lane."""
from __future__ import annotations

from types import SimpleNamespace

from src.events.reactor import _fair_lane_interleave

D = "DAY0_EXTREME_UPDATED"
F = "FORECAST_SNAPSHOT_READY"
R = "EDLI_REDECISION_PENDING"


def _ev(t, i=0):
    return SimpleNamespace(event_type=t, event_id=f"{t}-{i}")


def _types(events):
    return [e.event_type for e in events]


def test_interleave_alternates_lanes_forecast_first():
    # FORECAST-FIRST (2026-06-16): the harvest lane holds the FIRST slot so that under a
    # budget that completes only ~1 decision, the forecast/spine lane is the one that runs.
    out = _fair_lane_interleave([_ev(D, 0), _ev(D, 1), _ev(D, 2), _ev(F, 0), _ev(F, 1)])
    assert _types(out) == [F, D, F, D, D], "forecast holds first slot; lanes alternate 1:1"
    assert out[0].event_type == F, "harvest lane must get the first (guaranteed) budget slot"


def test_spine_lane_gets_half_the_budget_under_day0_flood():
    """The live failure: 88 day0 + 12 FSR. The first 8 (a realistic per-cycle budget) must
    contain 4 FSR — the spine lane is no longer starved off the budget."""
    events = [_ev(D, i) for i in range(88)] + [_ev(F, i) for i in range(12)]
    out = _fair_lane_interleave(events)
    first8 = _types(out)[:8]
    assert first8.count(F) == 4, f"spine lane must get half the budget, got {first8}"
    assert first8.count(D) == 4


def test_within_lane_order_preserved():
    """Per-lane (per-city-fair) order from fetch_pending must survive the interleave."""
    events = [_ev(D, 0), _ev(F, 0), _ev(D, 1), _ev(F, 1), _ev(D, 2)]
    out = _fair_lane_interleave(events)
    day0_order = [e.event_id for e in out if e.event_type == D]
    fsr_order = [e.event_id for e in out if e.event_type == F]
    assert day0_order == [f"{D}-0", f"{D}-1", f"{D}-2"]
    assert fsr_order == [f"{F}-0", f"{F}-1"]


def test_redecision_counts_as_forecast_lane():
    out = _fair_lane_interleave([_ev(D, 0), _ev(D, 1), _ev(R, 0)])
    assert _types(out) == [R, D, D], "EDLI_REDECISION_PENDING is a forecast-decision lane event (first slot)"


def test_no_forecast_events_unchanged_fast_path():
    events = [_ev(D, 0), _ev(D, 1), _ev(D, 2)]
    out = _fair_lane_interleave(events)
    assert out is events, "no spine event → identical list (cheap fast path)"


def test_no_day0_events_unchanged():
    events = [_ev(F, 0), _ev(F, 1)]
    out = _fair_lane_interleave(events)
    assert out is events, "only forecast events → unchanged"


def test_empty_unchanged():
    assert _fair_lane_interleave([]) == []
