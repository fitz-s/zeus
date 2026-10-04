# Created: 2026-10-04
# Last reused or audited: 2026-10-04
# Lifecycle: created=2026-10-04; last_reviewed=2026-10-04; last_reused=2026-10-04
# Purpose: A held position past its contract-local target day takes q from the final daily product, or waits on it as a typed state.
# Reuse: Run when changing monitor_refresh post-local-day routing, the belief-staleness tracker, or the incomplete-exit warning.
# Authority basis: Hong Kong c133991e / b5988a09 / dae091b0 logged BELIEF_AUTHORITY_FAULT every monitor cycle after 2026-10-02/03 closed,
#   because the pinned-posterior stage raised before any post-day decline tag existed; the outcome is physically fixed by the final daily row.
"""ANTIBODY: past-day held belief is the final observation, or a typed wait.

After the contract-local target day ends the outcome is a function of the
settlement product's final daily extreme. Three relationships hold:

1. A VERIFIED final row from the configured settlement family collapses the held
   side to 1/0 through the settlement-grid bin semantics, for YES and NO, point
   and shoulder bins, high and low.
2. Without that row the position waits as ``POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE``
   whatever forecast-side reader failure raised first: no belief-authority fault,
   no forecast reseed (a forecast cannot repair a fixed day).
3. Positions that are not past their day, and settlement families with no final
   daily lane, keep the fault, the reseed and the warning exactly as before.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import src.engine.monitor_refresh as mr
from src.config import cities_by_name
from src.state.portfolio import Position

CONDITION_ID = "0x" + "55" * 32
POST_DAY = "POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE"
FORECAST_SIDE_BLOCK = (
    "GLOBAL_HELD_PINNED_COMPLETE_POSTERIOR_BLOCKED:REPLACEMENT_LIVE_CYCLE_AGE_EXCEEDS_BOUND"
)
PAST_DAY = "2026-07-15"


def _hong_kong():
    return SimpleNamespace(
        name="Hong Kong", timezone="Asia/Hong_Kong", settlement_unit="C",
        wu_station=None, settlement_source_type="hko",
    )


def _final_rows(*, high=None, low=None, authority="VERIFIED"):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE observations (city TEXT, target_date TEXT, source TEXT, "
        "station_id TEXT, authority TEXT, unit TEXT, high_temp REAL, low_temp REAL, "
        "fetched_at TEXT)"
    )
    if high is not None or low is not None:
        conn.execute(
            "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)",
            ("Hong Kong", PAST_DAY, "hko_daily_api", "HKO", authority, "C",
             high, low, "2026-07-16T02:35:00+00:00"),
        )
    return conn


def _position(*, metric="high", direction="buy_yes", bin_text="29°C",
              target_date=PAST_DAY, city="Hong Kong", trade_id="t-postday"):
    word = "highest" if metric == "high" else "lowest"
    pos = Position(
        trade_id=trade_id, market_id="m-postday", city=city, cluster=city,
        target_date=target_date,
        bin_label=f"Will the {word} temperature in {city} be {bin_text} on July 15?",
        direction=direction, unit="C", temperature_metric=metric,
        entry_method="qkernel_spine", entry_price=0.55, p_posterior=0.46,
    )
    pos.condition_id = CONDITION_ID
    return pos


def _forecast_side_fails(monkeypatch, *, reseeds):
    monkeypatch.setattr(
        mr, "_refresh_current_global_day0_probability",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError(FORECAST_SIDE_BLOCK)),
    )
    monkeypatch.setattr(
        mr, "_enqueue_single_family_belief_reseed_failsoft",
        lambda **kw: reseeds.append(kw) or {"status": "ok"},
    )


# --- 1. final observation present: q from the settlement grid --------------------

@pytest.mark.parametrize(
    ("metric", "direction", "bin_text", "high", "low", "held_q"),
    [
        # HKO settles on the truncated grid: 29.9 is the 29 bin, not 30.
        ("high", "buy_yes", "29°C", 29.9, 25.0, 1.0),
        ("high", "buy_no", "29°C", 29.9, 25.0, 0.0),
        ("high", "buy_yes", "29°C", 30.1, 25.0, 0.0),
        ("high", "buy_no", "29°C", 30.1, 25.0, 1.0),
        ("high", "buy_yes", "33°C or higher", 34.2, 25.0, 1.0),
        ("high", "buy_no", "33°C or higher", 34.2, 25.0, 0.0),
        ("high", "buy_yes", "33°C or higher", 32.9, 25.0, 0.0),
        ("high", "buy_no", "33°C or higher", 32.9, 25.0, 1.0),
        ("low", "buy_no", "25°C", 30.0, 24.9, 1.0),
        ("low", "buy_yes", "25°C", 30.0, 24.9, 0.0),
        ("low", "buy_yes", "22°C or below", 30.0, 22.9, 1.0),
        ("low", "buy_no", "22°C or below", 30.0, 22.9, 0.0),
    ],
)
def test_past_day_with_final_row_collapses_held_q(
    monkeypatch, metric, direction, bin_text, high, low, held_q
):
    _forecast_side_fails(monkeypatch, reseeds=[])
    pos = _position(metric=metric, direction=direction, bin_text=bin_text)

    q, refreshed, fresh = mr.monitor_probability_refresh(
        pos, conn=_final_rows(high=high, low=low), city=_hong_kong(),
        target_d=date.fromisoformat(PAST_DAY),
    )

    assert fresh is True
    assert q == pytest.approx(held_q)
    assert POST_DAY not in refreshed.applied_validations
    assert any(
        v.startswith("belief_source=day0_final_daily_observation")
        for v in refreshed.applied_validations
    )


@pytest.mark.parametrize("authority", ["UNVERIFIED", "QUARANTINED"])
def test_past_day_unverified_row_is_never_a_belief(monkeypatch, authority):
    reseeds: list[dict] = []
    _forecast_side_fails(monkeypatch, reseeds=reseeds)
    pos = _position()

    q, refreshed, fresh = mr.monitor_probability_refresh(
        pos, conn=_final_rows(high=29.9, low=25.0, authority=authority),
        city=_hong_kong(), target_d=date.fromisoformat(PAST_DAY),
    )

    assert fresh is False
    assert q == pytest.approx(pos.p_posterior)
    assert POST_DAY in refreshed.applied_validations
    assert reseeds == []


# --- 2. final observation absent: typed wait, no fault, no reseed ----------------

def test_past_day_without_final_row_waits_without_fault_or_reseed(monkeypatch, caplog):
    reseeds: list[dict] = []
    _forecast_side_fails(monkeypatch, reseeds=reseeds)
    pos = _position()
    mr._belief_stale_cycles.clear()
    caplog.set_level("INFO", logger=mr.__name__)

    for _ in range(mr._BELIEF_STALE_FAULT_THRESHOLD + 3):
        q, refreshed, fresh = mr.monitor_probability_refresh(
            pos, conn=_final_rows(), city=_hong_kong(),
            target_d=date.fromisoformat(PAST_DAY),
        )
        assert fresh is False
        assert q == pytest.approx(pos.p_posterior)
        assert POST_DAY in refreshed.applied_validations
        assert mr.position_awaits_final_observation(refreshed)
        refreshed.last_monitor_prob_is_fresh = False
        refreshed.last_monitor_market_price_is_fresh = True
        mr._track_belief_staleness(refreshed)
        assert "BELIEF_AUTHORITY_FAULT" not in refreshed.applied_validations

    assert reseeds == []
    assert mr._belief_stale_cycles == {}
    assert not [r for r in caplog.records if r.levelno >= 30]
    assert not [r for r in caplog.records if "BELIEF_AUTHORITY_FAULT" in r.getMessage()]


def test_refresh_position_reports_the_wait_not_a_fault(monkeypatch, caplog):
    """The whole refresh_position path: the wait tag survives to the tracker."""
    _forecast_side_fails(monkeypatch, reseeds=[])
    monkeypatch.setattr(
        mr, "monitor_quote_refresh",
        lambda *_a, **_k: mr.HeldTokenMonitorQuote(
            token_id="held", best_bid=0.0, best_ask=0.001, bid_size=0.0,
            ask_size=100.0, mark_price=0.0,
            source_timestamp="2026-10-04T00:00:00+00:00",
        ),
    )
    pos = _position(trade_id="t-postday-full")
    mr._belief_stale_cycles.clear()
    caplog.set_level("INFO", logger=mr.__name__)

    for _ in range(mr._BELIEF_STALE_FAULT_THRESHOLD + 1):
        mr.refresh_position(_final_rows(), object(), pos)

    assert pos.last_monitor_prob_is_fresh is False
    assert mr.position_awaits_final_observation(pos)
    assert "BELIEF_AUTHORITY_FAULT" not in pos.applied_validations
    assert not [r for r in caplog.records if "BELIEF_AUTHORITY_FAULT" in r.getMessage()]


def test_noaa_past_day_waits_on_its_final_page(monkeypatch):
    import src.execution.day0_hard_fact_exit as hfe

    reseeds: list[dict] = []
    _forecast_side_fails(monkeypatch, reseeds=reseeds)
    monkeypatch.setattr(hfe, "_final_daily_observation_extreme", lambda **_k: None)
    monkeypatch.setattr(hfe, "evaluate_hard_fact_exit", lambda **_k: None)
    city = SimpleNamespace(
        name="Seattle", timezone="America/Los_Angeles", settlement_source_type="noaa",
    )
    pos = _position(city="Seattle")

    q, refreshed, fresh = mr.monitor_probability_refresh(
        pos, conn=object(), city=city, target_d=date.fromisoformat(PAST_DAY),
    )

    assert fresh is False
    assert POST_DAY in refreshed.applied_validations
    assert reseeds == []


# --- 3. nothing else changes ------------------------------------------------------

def _local_today(tz_name: str) -> str:
    return datetime.now(timezone.utc).astimezone(ZoneInfo(tz_name)).date().isoformat()


@pytest.mark.parametrize("day", ["future", "same_day"])
def test_not_yet_past_day_keeps_fault_reseed_and_warning(monkeypatch, caplog, day):
    reseeds: list[dict] = []
    _forecast_side_fails(monkeypatch, reseeds=reseeds)
    target = "2099-01-01" if day == "future" else _local_today("Asia/Hong_Kong")
    pos = _position(target_date=target)
    pos.entry_method = "day0_observation"
    mr._belief_stale_cycles.clear()
    caplog.set_level("INFO", logger=mr.__name__)

    for _ in range(mr._BELIEF_STALE_FAULT_THRESHOLD):
        q, refreshed, fresh = mr.monitor_probability_refresh(
            pos, conn=_final_rows(), city=_hong_kong(),
            target_d=date.fromisoformat(target),
        )
        assert fresh is False
        assert POST_DAY not in refreshed.applied_validations
        refreshed.last_monitor_prob_is_fresh = False
        refreshed.last_monitor_market_price_is_fresh = True
        mr._track_belief_staleness(refreshed)

    assert "BELIEF_AUTHORITY_FAULT" in refreshed.applied_validations
    assert len(reseeds) == mr._BELIEF_STALE_FAULT_THRESHOLD
    assert [r for r in caplog.records if "BELIEF_AUTHORITY_FAULT" in r.getMessage()]
    assert [
        r for r in caplog.records
        if r.levelno == 30 and "current global Day0 probability unavailable" in r.getMessage()
    ]


def test_wu_past_day_has_no_final_lane_and_keeps_fault_and_reseed(monkeypatch):
    reseeds: list[dict] = []
    _forecast_side_fails(monkeypatch, reseeds=reseeds)
    city = SimpleNamespace(
        name="Auckland", timezone="Pacific/Auckland", settlement_source_type="wu_icao",
    )
    pos = _position(city="Auckland")

    q, refreshed, fresh = mr.monitor_probability_refresh(
        pos, conn=sqlite3.connect(":memory:"), city=city,
        target_d=date.fromisoformat(PAST_DAY),
    )

    assert fresh is False
    assert POST_DAY not in refreshed.applied_validations
    assert len(reseeds) == 1


def test_every_final_lane_family_is_a_configured_settlement_type():
    """The wait is keyed on the lane's own family set, never a city name."""
    families = {c.settlement_source_type for c in cities_by_name.values()}
    assert mr._POST_LOCAL_DAY_HARD_FACT_ELIGIBLE_SOURCE_TYPES <= families


# --- 4. the exit cycle's own warning ---------------------------------------------

@pytest.mark.parametrize(
    ("tags", "level"),
    [
        ([POST_DAY], "INFO"),
        (["monitor_probability_stale"], "WARNING"),
    ],
)
def test_exit_authority_incomplete_is_info_only_while_awaiting_final_observation(
    monkeypatch, caplog, tags, level
):
    import types

    import numpy as np

    from src.engine import cycle_runtime
    from src.engine.cycle_runner import CycleArtifact
    from src.state.portfolio import PortfolioState
    from src.state.strategy_tracker import StrategyTracker
    from tests.test_runtime_guards import _monitor_chain_deps, _position as rg_position

    pos = rg_position(trade_id="monitor-postday", state="day0_window")

    def _refresh(*_a, **_k):
        pos.applied_validations = list(tags)
        return types.SimpleNamespace(
            p_market=np.array([]), p_posterior=0.41, divergence_score=0.0,
            market_velocity_1h=0.0, forward_edge=0.0,
        )

    monkeypatch.setattr("src.engine.monitor_refresh.refresh_position", _refresh)
    caplog.set_level("INFO", logger="test_monitor_chain_missing")

    cycle_runtime.execute_monitoring_phase(
        conn=None,
        clob=types.SimpleNamespace(),
        portfolio=PortfolioState(positions=[pos]),
        artifact=CycleArtifact(mode="day0_capture", started_at="2026-04-01T20:00:00Z"),
        tracker=StrategyTracker(),
        summary={"monitors": 0, "exits": 0},
        deps=_monitor_chain_deps(datetime(2026, 4, 1, 23, 0, tzinfo=timezone.utc)),
    )

    lines = [r for r in caplog.records if "Exit authority incomplete" in r.getMessage()]
    assert len(lines) == 1
    assert lines[0].levelname == level
