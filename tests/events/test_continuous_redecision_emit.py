# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: GOAL #36 continuous trading + PLAN_CONTINUOUS_REDECISION_MAX_ALPHA_2026-05-31.md.
#   Proves the continuous re-decision emit: scan_committed_snapshots(source=<per-cycle>) re-emits a
#   fresh FSR-equivalent each cycle (distinct event_id) instead of deduping to the consumed FSR, so
#   the reactor re-decides every cycle (fix for EDLI-mode "hours per order"). default source/None →
#   one-shot behavior unchanged; already_pending_keys skips queued families.
"""Relationship tests for the per-cycle re-emission seam (src.events.triggers.forecast_snapshot_ready)."""
from __future__ import annotations

import inspect
import sqlite3
from datetime import datetime, timezone

import pytest

import src.main as main
from src.events.event_writer import EventWriter
from src.events.event_store import EventStore
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.events.triggers.forecast_snapshot_ready import (
    ForecastSnapshotReadyTrigger,
    executable_forecast_live_eligible_reader,
)
from src.state.db import init_schema, init_schema_forecasts

ENTITY_KEY = "Chicago|2026-05-24|high|run-1"


@pytest.fixture(autouse=True)
def _replacement_authority_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        "src.events.triggers.forecast_snapshot_ready._replacement_trade_authority_enabled",
        lambda: False,
    )


def _decision_time() -> datetime:
    return datetime(2026, 5, 24, 4, 30, tzinfo=timezone.utc)


def _seed_forecasts() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema_forecasts(c)
    c.execute(
        """INSERT INTO source_run (source_run_id, source_id, track, release_calendar_key, ingest_mode,
            origin_mode, source_cycle_time, source_available_at, captured_at, target_local_date,
            city_id, city_timezone, temperature_metric, dataset_id, expected_members, observed_members,
            expected_steps_json, observed_steps_json, completeness_status, status) VALUES (
            'run-1','ecmwf-open-data','ens','2026-05-24T00','SCHEDULED_LIVE','SCHEDULED_LIVE',
            '2026-05-24T00:00:00+00:00','2026-05-24T04:15:00+00:00','2026-05-24T04:16:00+00:00',
            '2026-05-24','chicago','America/Chicago','high','v1',51,51,'[0,3,6]','[0,3,6]','COMPLETE','SUCCESS')"""
    )
    c.execute(
        """INSERT INTO source_run_coverage (coverage_id, source_run_id, source_id, source_transport,
            release_calendar_key, track, city_id, city, city_timezone, target_local_date, temperature_metric,
            physical_quantity, observation_field, data_version, expected_members, observed_members,
            expected_steps_json, observed_steps_json, snapshot_ids_json, target_window_start_utc,
            target_window_end_utc, completeness_status, readiness_status, computed_at, expires_at) VALUES (
            'cov-1','run-1','ecmwf-open-data','ensemble_snapshots_db_reader','2026-05-24T00','ens',
            'chicago','Chicago','America/Chicago','2026-05-24','high','temperature','high_temp','v1',51,51,
            '[0,3,6]','[0,3,6]','[1]','2026-05-24T05:00:00+00:00','2026-05-25T05:00:00+00:00',
            'COMPLETE','LIVE_ELIGIBLE','2026-05-24T04:16:00+00:00','2026-05-25T04:16:00+00:00')"""
    )
    c.execute(
        """INSERT INTO ensemble_snapshots (snapshot_id, city, target_date, temperature_metric,
            physical_quantity, observation_field, issue_time, valid_time, available_at, fetch_time,
            lead_hours, members_json, model_version, dataset_id, source_id, source_transport, source_run_id,
            release_calendar_key, source_cycle_time, source_release_time, source_available_at, authority,
            causality_status, boundary_ambiguous, contributes_to_target_extrema,
            forecast_window_attribution_status, local_day_start_utc, step_horizon_hours, members_unit,
            raw_orderbook_hash_transition_delta_ms) VALUES (
            1,'Chicago','2026-05-24','high','temperature','high_temp','2026-05-24T00:00:00+00:00',
            '2026-05-24T06:00:00+00:00','2026-05-24T04:15:00+00:00','2026-05-24T04:16:00+00:00',6,
            '[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51]',
            'ecmwf','v1','ecmwf-open-data','ensemble_snapshots_db_reader','run-1','2026-05-24T00',
            '2026-05-24T00:00:00+00:00','2026-05-24T03:00:00+00:00','2026-05-24T04:15:00+00:00','VERIFIED',
            'OK',0,1,'FULLY_INSIDE_TARGET_LOCAL_DAY','2026-05-24T05:00:00+00:00',6,'F',0)"""
    )
    c.execute(
        "INSERT INTO market_events (market_slug, city, target_date, temperature_metric) VALUES (?,?,?,?)",
        ("chicago-high-2026-05-24", "Chicago", "2026-05-24", "high"),
    )
    return c


def _trigger(fc, world):
    return ForecastSnapshotReadyTrigger(
        EventWriter(world), live_eligibility_reader=executable_forecast_live_eligible_reader(fc)
    )


def _scan(trig, fc, *, source=None, already_pending_keys=None, decision_time=None):
    decision_time = decision_time or _decision_time()
    return trig.scan_committed_snapshots(
        forecasts_conn=fc, decision_time=decision_time, received_at="2026-05-24T04:17:00+00:00",
        source=source, already_pending_keys=already_pending_keys,
    )


def test_per_cycle_source_reemits_distinct_event_ids():
    """R-live-2 (one-shot killer): two cycles with distinct source → two DISTINCT events for the
    same committed family (continuous re-decision), not deduped to one."""
    fc = _seed_forecasts()
    world = sqlite3.connect(":memory:"); init_schema(world)
    trig = _trigger(fc, world)
    _scan(trig, fc, source="edli_redecision:cycle1")
    _scan(trig, fc, source="edli_redecision:cycle2")
    rows = world.execute("SELECT event_id, entity_key FROM opportunity_events").fetchall()
    assert len(rows) == 2, "distinct per-cycle source must re-emit (continuous, not one-shot)"
    assert rows[0][0] != rows[1][0], "re-emitted events must have distinct event_ids"
    assert all(r[1] == ENTITY_KEY for r in rows)


def test_default_source_dedups_one_shot_unchanged():
    """Backward-compat: no source → two scans dedup to ONE event (original one-shot behavior)."""
    fc = _seed_forecasts()
    world = sqlite3.connect(":memory:"); init_schema(world)
    trig = _trigger(fc, world)
    _scan(trig, fc)
    _scan(trig, fc)
    assert world.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 1


def test_already_pending_key_is_skipped():
    """already_pending_keys containing the family entity_key → NOT re-emitted (bounds the queue)."""
    fc = _seed_forecasts()
    world = sqlite3.connect(":memory:"); init_schema(world)
    trig = _trigger(fc, world)
    res = _scan(trig, fc, source="edli_redecision:cycleX", already_pending_keys={ENTITY_KEY})
    assert res == []
    assert world.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0


def test_continuous_redecision_does_not_reemit_settlement_day_forecast_only():
    """Per-cycle redecision must not refill the queue with forecast_only markets
    whose target local day has already begun. Those candidates are guaranteed to
    fail the reactor phase backstop and otherwise burn the bounded proof budget.
    """
    fc = _seed_forecasts()
    world = sqlite3.connect(":memory:"); init_schema(world)
    trig = _trigger(fc, world)
    settlement_day = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)

    res = _scan(
        trig,
        fc,
        source="edli_redecision:cycle-settlement-day",
        decision_time=settlement_day,
    )

    assert res == []
    assert world.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0


def test_prune_working_set_expires_stale_fsr_before_skip_snapshot(monkeypatch):
    """A stale FSR row must be expired before the continuous-redecision skip set snapshots."""

    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    store = EventStore(world)
    stale = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-06-04|high|snap-stale",
        source="forecast",
        observed_at="2026-06-03T00:00:00+00:00",
        available_at="2026-06-03T00:00:00+00:00",
        received_at="2026-06-03T00:00:00+00:00",
        causal_snapshot_id="snap-stale",
        payload=ForecastSnapshotReadyPayload(
            city="Chicago",
            target_date="2026-06-04",
            metric="high",
            source_id="ecmwf-open-data",
            source_run_id="run-1",
            cycle="00",
            track="ens",
            snapshot_id="snap-stale",
            snapshot_hash="snap-stale",
            captured_at="2026-06-03T00:00:00+00:00",
            available_at="2026-06-03T00:00:00+00:00",
            required_fields_present=True,
            required_steps_present=True,
            member_count=51,
            min_members_floor=40,
            completeness_status="COMPLETE",
            required_steps=[0, 3, 6],
            observed_steps=[0, 3, 6],
            expected_members=51,
            source_run_status="COMMITTED",
            source_run_completeness_status="COMPLETE",
            coverage_completeness_status="COMPLETE",
            coverage_readiness_status="LIVE_ELIGIBLE",
        ),
        priority=0,
    )
    store.insert_or_ignore(stale)

    decision_time = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    assert stale.entity_key in main._edli_pending_entity_keys(world)
    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda name, default=None: {
            "reactor_prune_enabled": True,
            "reactor_prune_interval_seconds": 0,
            "reactor_prune_batch_limit": 10,
        }
        if name == "edli"
        else (default if default is not None else {}),
    )

    main._edli_prune_pending_working_set(store, decision_time=decision_time)

    assert stale.entity_key not in main._edli_pending_entity_keys(world)
    status = world.execute(
        "SELECT processing_status FROM opportunity_event_processing "
        "WHERE consumer_name = ? AND event_id = ?",
        (store.consumer_name, stale.event_id),
    ).fetchone()[0]
    assert status == "expired"


def test_prune_working_set_expires_superseded_fsr_across_source_runs():
    """Supersession is by weather family, not entity_key with source_run baked in."""

    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    store = EventStore(world)

    def _fsr(source_run_id: str, available_at: str):
        return make_opportunity_event(
            event_type="FORECAST_SNAPSHOT_READY",
            entity_key=f"London|2026-06-07|low|ecmwf_open_data:mn2t6_low:{source_run_id}",
            source="forecast",
            observed_at=available_at,
            available_at=available_at,
            received_at=available_at,
            causal_snapshot_id=source_run_id,
            payload=ForecastSnapshotReadyPayload(
                city="London",
                target_date="2026-06-07",
                metric="low",
                source_id="ecmwf-open-data",
                source_run_id=source_run_id,
                cycle="00",
                track="ens",
                snapshot_id=source_run_id,
                snapshot_hash=source_run_id,
                captured_at=available_at,
                available_at=available_at,
                required_fields_present=True,
                required_steps_present=True,
                member_count=51,
                min_members_floor=40,
                completeness_status="COMPLETE",
                required_steps=[0, 3, 6],
                observed_steps=[0, 3, 6],
                expected_members=51,
                source_run_status="COMMITTED",
                source_run_completeness_status="COMPLETE",
                coverage_completeness_status="COMPLETE",
                coverage_readiness_status="LIVE_ELIGIBLE",
            ),
            priority=0,
        )

    old = _fsr("2026-06-05T00Z", "2026-06-05T00:00:00+00:00")
    new = _fsr("2026-06-06T00Z", "2026-06-06T00:00:00+00:00")
    store.insert_or_ignore(old)
    store.insert_or_ignore(new)

    archived = store.archive_superseded_forecast_snapshot_events(batch_limit=10)

    assert archived == 1
    statuses = dict(
        world.execute(
            """
            SELECT event_id, processing_status
              FROM opportunity_event_processing
             WHERE consumer_name = ?
            """,
            (store.consumer_name,),
        ).fetchall()
    )
    assert statuses[old.event_id] == "expired"
    assert statuses[new.event_id] == "pending"


def test_redecision_skip_set_ignores_pending_channel_events():
    """Channel-cache events must not make forecast families look already pending."""

    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    store = EventStore(world)
    channel = make_opportunity_event(
        event_type="BOOK_SNAPSHOT",
        entity_key="0xcondition|token-1|BOOK_SNAPSHOT",
        source="market_channel",
        observed_at="2026-06-05T00:00:00+00:00",
        available_at="2026-06-05T00:00:00+00:00",
        received_at="2026-06-05T00:00:00+00:00",
        causal_snapshot_id="book-1",
        payload={
            "condition_id": "0xcondition",
            "token_id": "token-1",
            "best_bid": 0.39,
            "best_ask": 0.40,
        },
        priority=0,
    )
    store.insert_or_ignore(channel)

    assert main._edli_pending_entity_keys(world) == set()


def test_redecision_cycle_prunes_before_snapshotting_pending_keys():
    """The reactor cycle must prune the working set before taking the redecision skip snapshot."""

    src = inspect.getsource(main._edli_event_reactor_cycle)
    assert src.index("_edli_prune_pending_working_set(") < src.index("_edli_pending_entity_keys(")
