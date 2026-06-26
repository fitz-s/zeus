# Created: 2026-05-31
# Last reused or audited: 2026-06-18
# Authority basis: GOAL #36 continuous trading + PLAN_CONTINUOUS_REDECISION_MAX_ALPHA_2026-05-31.md.
#   Proves the continuous re-decision emit: scan_committed_snapshots(source=<per-cycle>) re-emits a
#   fresh FSR-equivalent each cycle (distinct event_id) instead of deduping to the consumed FSR, so
#   the reactor re-decides every cycle (fix for EDLI-mode "hours per order"). default source/None →
#   one-shot behavior unchanged; already_pending_keys skips queued families.
"""Relationship tests for the per-cycle re-emission seam (src.events.triggers.forecast_snapshot_ready)."""
from __future__ import annotations

import dataclasses
import inspect
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

import src.main as main
from src.events.event_writer import EventWriter
from src.events.event_store import EventStore
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.events.triggers.forecast_snapshot_ready import (
    CoverageFairnessRequest,
    ForecastSnapshotReadyTrigger,
    _filter_rows_to_restricted_families,
    executable_forecast_live_eligible_reader,
)
from src.data.replacement_cycle_advance_trigger import _held_position_families
from src.state.db import init_schema, init_schema_forecasts

ENTITY_KEY = "Chicago|2026-05-24|high|run-1"


@pytest.fixture(autouse=True)
def _replacement_authority_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        "src.events.triggers.forecast_snapshot_ready._replacement_live_enabled",
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


def _ready_payload(
    *,
    city: str = "Chicago",
    target_date: str = "2026-06-04",
    metric: str = "high",
    source_run_id: str = "run-1",
    snapshot_id: str = "snap-1",
    available_at: str = "2026-06-03T00:00:00+00:00",
) -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city=city,
        target_date=target_date,
        metric=metric,
        source_id="ecmwf-open-data",
        source_run_id=source_run_id,
        cycle="00",
        track="ens",
        snapshot_id=snapshot_id,
        snapshot_hash=snapshot_id,
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


def test_prune_working_set_expires_superseded_redecision_by_family():
    """Continuous redecision supersession keeps the newest EDLI_REDECISION_PENDING per family."""

    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    store = EventStore(world)

    def _rd(source_run_id: str, available_at: str):
        return make_opportunity_event(
            event_type="EDLI_REDECISION_PENDING",
            entity_key=f"London|2026-06-07|low|{source_run_id}",
            source="edli_redecision:screen",
            observed_at=available_at,
            available_at=available_at,
            received_at=available_at,
            causal_snapshot_id=source_run_id,
            payload=_ready_payload(
                city="London",
                target_date="2026-06-07",
                metric="low",
                source_run_id=source_run_id,
                snapshot_id=source_run_id,
                available_at=available_at,
            ),
            priority=50,
        )

    old = _rd("2026-06-05T00Z", "2026-06-05T00:00:00+00:00")
    new = _rd("2026-06-06T00Z", "2026-06-06T00:00:00+00:00")
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


def test_redecision_skip_set_is_event_type_scoped():
    """FSR backlog must not block screened/held EDLI_REDECISION_PENDING admission."""

    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    store = EventStore(world)
    fsr = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-06-04|high|run-fsr",
        source="forecast",
        observed_at="2026-06-03T00:00:00+00:00",
        available_at="2026-06-03T00:00:00+00:00",
        received_at="2026-06-03T00:00:00+00:00",
        causal_snapshot_id="snap-fsr",
        payload=_ready_payload(source_run_id="run-fsr", snapshot_id="snap-fsr"),
        priority=50,
    )
    redecision = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Tokyo|2026-06-04|high|run-rd",
        source="edli_redecision:screen",
        observed_at="2026-06-03T00:00:00+00:00",
        available_at="2026-06-03T00:00:00+00:00",
        received_at="2026-06-03T00:00:00+00:00",
        causal_snapshot_id="snap-rd",
        payload=_ready_payload(
            city="Tokyo",
            source_run_id="run-rd",
            snapshot_id="snap-rd",
        ),
        priority=50,
    )
    store.insert_or_ignore(fsr)
    store.insert_or_ignore(redecision)

    assert main._edli_pending_entity_keys(world) == {fsr.entity_key}
    assert main._edli_pending_entity_keys(
        world,
        event_types=("EDLI_REDECISION_PENDING",),
    ) == {redecision.entity_key}


def test_fsr_scan_suppresses_recent_same_evidence_no_value_refutation():
    fc = _seed_forecasts()
    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    trig = _trigger(fc, world)

    first = trig.scan_committed_snapshots(
        forecasts_conn=fc,
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
        source="cycle-1",
    )
    assert len(first) == 1
    event_id = first[0].event_id
    world.execute(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
            decision_time, city, target_date, metric, family_id, causal_snapshot_id,
            created_at, schema_version
        ) VALUES (?, ?, 'TRADE_SCORE', 'TRADE_SCORE_NON_POSITIVE', 'NO_EDGE',
                  '2026-05-24T04:18:00+00:00', 'Chicago', '2026-05-24', 'high',
                  'family-chicago-high', '1', '2026-05-24T04:18:00+00:00', 1)
        """,
        ("regret-" + event_id, event_id),
    )

    second = trig.scan_committed_snapshots(
        forecasts_conn=fc,
        decision_time=datetime(2026, 5, 24, 4, 20, tzinfo=timezone.utc),
        received_at="2026-05-24T04:20:00+00:00",
        source="cycle-2",
        suppress_recent_no_value_refutations=True,
    )

    assert second == []


def test_fsr_scan_keeps_price_conditioned_no_value_refutation_live():
    fc = _seed_forecasts()
    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    trig = _trigger(fc, world)

    first = trig.scan_committed_snapshots(
        forecasts_conn=fc,
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
        source="cycle-1",
    )
    assert len(first) == 1
    event_id = first[0].event_id
    world.execute(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
            decision_time, city, target_date, metric, family_id, causal_snapshot_id,
            executable_snapshot_id, created_at, schema_version
        ) VALUES (?, ?, 'TRADE_SCORE', 'TRADE_SCORE_NON_POSITIVE', 'NO_EDGE',
                  '2026-05-24T04:18:00+00:00', 'Chicago', '2026-05-24', 'high',
                  'family-chicago-high', '1', 'ems2-old-price',
                  '2026-05-24T04:18:00+00:00', 1)
        """,
        ("regret-" + event_id, event_id),
    )

    second = trig.scan_committed_snapshots(
        forecasts_conn=fc,
        decision_time=datetime(2026, 5, 24, 4, 20, tzinfo=timezone.utc),
        received_at="2026-05-24T04:20:00+00:00",
        source="cycle-2",
        suppress_recent_no_value_refutations=True,
    )

    assert len(second) == 1


def test_fsr_scan_suppresses_recent_redecision_same_evidence_no_value_refutation():
    fc = _seed_forecasts()
    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    store = EventStore(world)
    trig = _trigger(fc, world)
    prior_redecision = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key=ENTITY_KEY,
        source="edli_redecision:screen",
        observed_at="2026-05-24T04:15:00+00:00",
        available_at="2026-05-24T04:15:00+00:00",
        received_at="2026-05-24T04:16:00+00:00",
        causal_snapshot_id="1",
        payload=_ready_payload(
            city="Chicago",
            target_date="2026-05-24",
            metric="high",
            source_run_id="run-1",
            snapshot_id="1",
            available_at="2026-05-24T04:15:00+00:00",
        ),
        priority=50,
    )
    store.insert_or_ignore(prior_redecision)
    world.execute(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
            decision_time, city, target_date, metric, family_id, causal_snapshot_id,
            created_at, schema_version
        ) VALUES (?, ?, 'TRADE_SCORE', 'TRADE_SCORE_NON_POSITIVE', 'NO_EDGE',
                  '2026-05-24T04:18:00+00:00', 'Chicago', '2026-05-24', 'high',
                  'family-chicago-high', '1', '2026-05-24T04:18:00+00:00', 1)
        """,
        ("regret-" + prior_redecision.event_id, prior_redecision.event_id),
    )

    emitted = trig.scan_committed_snapshots(
        forecasts_conn=fc,
        decision_time=datetime(2026, 5, 24, 4, 20, tzinfo=timezone.utc),
        received_at="2026-05-24T04:20:00+00:00",
        source="cycle-after-redecision-no-value",
        suppress_recent_no_value_refutations=True,
    )

    assert emitted == []


def test_redecision_pending_family_keys_parse_only_valid_families():
    assert main._edli_redecision_family_keys_from_entity_keys(
        {
            "Tokyo|2026-06-18|low|run-rd",
            "Shenzhen|2026-06-19|high|run-rd",
            "Paris|2026-06-19|bogus|run-rd",
            "malformed",
        }
    ) == {
        ("Tokyo", "2026-06-18", "low"),
        ("Shenzhen", "2026-06-19", "high"),
    }


def test_redecision_screen_skips_forecast_scan_when_pending_covers_admission():
    """Already-pending admitted families must not trigger an expensive no-op re-emit scan."""

    screen_src = inspect.getsource(main._edli_continuous_redecision_screen_cycle)
    assert "pending_families = _edli_redecision_family_keys_from_entity_keys(pending)" in screen_src
    assert "emit_families = set(all_families) - pending_families" in screen_src
    assert "if emit_families:" in screen_src
    assert "restrict_to_families=emit_families" in screen_src
    assert "events_to_emit = []" in screen_src
    assert screen_src.index("emit_families = set(all_families) - pending_families") < screen_src.index(
        "trig.build_committed_snapshot_events"
    )


def test_redecision_screen_full_refresh_still_requires_scoped_freshness():
    """Refresh summary is not live authority; scoped condition freshness is."""

    screen_src = inspect.getsource(main._edli_continuous_redecision_screen_cycle)

    assert "fresh_entry_scope = _edli_families_with_fresh_scoped_executable_substrate" in screen_src
    assert "confirmation_refresh_verified" in screen_src
    assert screen_src.index("_edli_confirmation_refresh_unavailable") < screen_src.index(
        "fresh_entry_scope = _edli_families_with_fresh_scoped_executable_substrate"
    )
    assert "fresh_events.append(event)" not in screen_src


def test_unadmitted_redecision_pending_is_expired():
    """Pending redecisions must remain backed by current edge/rest/held admission."""

    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    store = EventStore(world)
    stale = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="San Francisco|2026-06-17|high|run-stale",
        source="escalation_cross-stale",
        observed_at="2026-06-17T15:00:00+00:00",
        available_at="2026-06-17T15:00:00+00:00",
        received_at="2026-06-17T15:00:00+00:00",
        causal_snapshot_id="snap-stale",
        payload=_ready_payload(
            city="San Francisco",
            target_date="2026-06-17",
            metric="high",
            source_run_id="run-stale",
            snapshot_id="snap-stale",
        ),
        priority=50,
    )
    admitted = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Tokyo|2026-06-18|low|run-held",
        source="cycle-admitted",
        observed_at="2026-06-17T15:00:00+00:00",
        available_at="2026-06-17T15:00:00+00:00",
        received_at="2026-06-17T15:00:00+00:00",
        causal_snapshot_id="snap-held",
        payload=_ready_payload(
            city="Tokyo",
            target_date="2026-06-18",
            metric="low",
            source_run_id="run-held",
            snapshot_id="snap-held",
        ),
        priority=50,
    )
    fsr = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Seoul|2026-06-19|low|run-fsr",
        source="forecast",
        observed_at="2026-06-17T15:00:00+00:00",
        available_at="2026-06-17T15:00:00+00:00",
        received_at="2026-06-17T15:00:00+00:00",
        causal_snapshot_id="snap-fsr",
        payload=_ready_payload(
            city="Seoul",
            target_date="2026-06-19",
            metric="low",
            source_run_id="run-fsr",
            snapshot_id="snap-fsr",
        ),
        priority=50,
    )
    for event in (stale, admitted, fsr):
        store.insert_or_ignore(event)

    expired = main._edli_expire_unadmitted_redecision_pending(
        world,
        {("Tokyo", "2026-06-18", "low")},
        decision_time="2026-06-17T16:00:00+00:00",
    )

    assert expired == 1
    statuses = dict(
        world.execute(
            """
            SELECT e.entity_key, p.processing_status
              FROM opportunity_events e
              JOIN opportunity_event_processing p ON p.event_id = e.event_id
             WHERE p.consumer_name = ?
            """,
            (store.consumer_name,),
        ).fetchall()
    )
    assert statuses[stale.entity_key] == "expired"
    assert statuses[admitted.entity_key] == "pending"
    assert statuses[fsr.entity_key] == "pending"


def test_fresh_unclaimed_redecision_pending_survives_admission_grace():
    """A just-emitted redecision must get a reactor claim window before expiry."""

    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    store = EventStore(world)
    fresh = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Berlin|2026-06-19|high|run-fresh",
        source="cycle-fresh",
        observed_at="2026-06-17T15:59:00+00:00",
        available_at="2026-06-17T15:59:00+00:00",
        received_at="2026-06-17T15:59:00+00:00",
        causal_snapshot_id="snap-fresh",
        payload=_ready_payload(
            city="Berlin",
            target_date="2026-06-19",
            metric="high",
            source_run_id="run-fresh",
            snapshot_id="snap-fresh",
        ),
        priority=50,
    )
    store.insert_or_ignore(fresh)

    expired = main._edli_expire_unadmitted_redecision_pending(
        world,
        set(),
        decision_time="2026-06-17T16:00:00+00:00",
    )

    row = world.execute(
        """
        SELECT p.processing_status, p.last_error
          FROM opportunity_events e
          JOIN opportunity_event_processing p ON p.event_id = e.event_id
         WHERE e.entity_key = ?
        """,
        (fresh.entity_key,),
    ).fetchone()
    assert expired == 0
    assert tuple(row) == ("pending", None)


def test_stale_admitted_redecision_pending_is_superseded_for_fresh_screen():
    """A current family must not be suppressed forever by an old pending row.

    The default expiry path preserves admitted rows; the screen can explicitly
    supersede an admitted row after the claim grace when it has just re-confirmed
    fresh scoped price substrate and is ready to emit a replacement event.
    """

    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    store = EventStore(world)
    stale = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Shenzhen|2026-06-27|high|run-stale",
        source="cycle-stale",
        observed_at="2026-06-26T10:00:00+00:00",
        available_at="2026-06-26T10:00:00+00:00",
        received_at="2026-06-26T10:00:00+00:00",
        causal_snapshot_id="snap-stale",
        payload=_ready_payload(
            city="Shenzhen",
            target_date="2026-06-27",
            metric="high",
            source_run_id="run-stale",
            snapshot_id="snap-stale",
        ),
        priority=50,
        created_at="2026-06-26T10:00:00+00:00",
    )
    store.insert_or_ignore(stale)

    admitted = {("Shenzhen", "2026-06-27", "high")}
    preserved = main._edli_expire_unadmitted_redecision_pending(
        world,
        admitted,
        decision_time="2026-06-26T10:06:00+00:00",
    )
    assert preserved == 0

    expired = main._edli_expire_unadmitted_redecision_pending(
        world,
        admitted,
        decision_time="2026-06-26T10:06:00+00:00",
        supersede_stale_admitted=True,
    )

    row = world.execute(
        """
        SELECT p.processing_status, p.last_error
          FROM opportunity_events e
          JOIN opportunity_event_processing p ON p.event_id = e.event_id
         WHERE e.entity_key = ?
        """,
        (stale.entity_key,),
    ).fetchone()
    assert expired == 1
    assert tuple(row) == (
        "expired",
        "REDECISION_SUPERSEDED_BY_FRESH_SCREEN:stale_pending_claim_grace_elapsed",
    )


def test_recent_rest_pull_redecision_survives_generic_no_edge_expiry():
    """Cancel/reprice continuity must survive after the rest leaves the open-rest set."""

    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    store = EventStore(world, consumer_name="edli_reactor_v1")
    payload = dataclasses.asdict(
        _ready_payload(
            city="Paris",
            target_date="2026-06-19",
            metric="low",
            source_run_id="run-rest-pull",
            snapshot_id="snap-rest-pull",
        )
    )
    payload["redecision_origin"] = "rest_pull"
    rest_pull = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Paris|2026-06-19|low|run-rest-pull",
        source="cycle-rest-pull",
        observed_at="2026-06-17T15:45:00+00:00",
        available_at="2026-06-17T15:45:00+00:00",
        received_at="2026-06-17T15:45:00+00:00",
        causal_snapshot_id="snap-rest-pull",
        payload=payload,
        priority=50,
        created_at="2026-06-17T15:45:00+00:00",
    )
    store.insert_or_ignore(rest_pull)

    expired = main._edli_expire_unadmitted_redecision_pending(
        world,
        set(),
        decision_time="2026-06-17T16:00:00+00:00",
    )

    row = world.execute(
        """
        SELECT p.processing_status, p.last_error
          FROM opportunity_events e
          JOIN opportunity_event_processing p ON p.event_id = e.event_id
         WHERE e.entity_key = ?
        """,
        (rest_pull.entity_key,),
    ).fetchone()
    assert expired == 0
    assert tuple(row) == ("pending", None)


def test_old_rest_pull_redecision_still_expires_without_current_edge():
    """The rest-pull grace is a continuity window, not an infinite pending queue."""

    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    store = EventStore(world, consumer_name="edli_reactor_v1")
    payload = dataclasses.asdict(
        _ready_payload(
            city="Paris",
            target_date="2026-06-19",
            metric="low",
            source_run_id="run-old-rest-pull",
            snapshot_id="snap-old-rest-pull",
        )
    )
    payload["redecision_origin"] = "rest_pull"
    old_rest_pull = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Paris|2026-06-19|low|run-old-rest-pull",
        source="cycle-old-rest-pull",
        observed_at="2026-06-17T15:00:00+00:00",
        available_at="2026-06-17T15:00:00+00:00",
        received_at="2026-06-17T15:00:00+00:00",
        causal_snapshot_id="snap-old-rest-pull",
        payload=payload,
        priority=50,
        created_at="2026-06-17T15:00:00+00:00",
    )
    store.insert_or_ignore(old_rest_pull)

    expired = main._edli_expire_unadmitted_redecision_pending(
        world,
        set(),
        decision_time="2026-06-17T16:00:00+00:00",
    )

    row = world.execute(
        """
        SELECT p.processing_status, p.last_error
          FROM opportunity_events e
          JOIN opportunity_event_processing p ON p.event_id = e.event_id
         WHERE e.entity_key = ?
        """,
        (old_rest_pull.entity_key,),
    ).fetchone()
    assert expired == 1
    assert tuple(row) == (
        "expired",
        "REDECISION_ADMISSION_EXPIRED:no_current_edge_or_rest_reprice_value",
    )


def test_unadmitted_stale_processing_redecision_is_expired_after_claim_lease():
    """Stale processing redecisions must not survive admission expiry forever."""

    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    store = EventStore(world, consumer_name="edli_reactor_v1")
    decision_time = datetime(2026, 6, 18, 10, 15, tzinfo=timezone.utc)
    stale_processing = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Munich|2026-06-19|high|run-stale-processing",
        source="cycle-stale-processing",
        observed_at="2026-06-18T09:12:39+00:00",
        available_at="2026-06-18T09:12:39+00:00",
        received_at="2026-06-18T09:12:39+00:00",
        causal_snapshot_id="snap-stale-processing",
        payload=_ready_payload(
            city="Munich",
            target_date="2026-06-19",
            metric="high",
            source_run_id="run-stale-processing",
            snapshot_id="snap-stale-processing",
        ),
        priority=50,
    )
    fresh_processing = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Paris|2026-06-19|high|run-fresh-processing",
        source="cycle-fresh-processing",
        observed_at="2026-06-18T10:14:30+00:00",
        available_at="2026-06-18T10:14:30+00:00",
        received_at="2026-06-18T10:14:30+00:00",
        causal_snapshot_id="snap-fresh-processing",
        payload=_ready_payload(
            city="Paris",
            target_date="2026-06-19",
            metric="high",
            source_run_id="run-fresh-processing",
            snapshot_id="snap-fresh-processing",
        ),
        priority=50,
    )
    for event in (stale_processing, fresh_processing):
        store.insert_or_ignore(event)
    assert store.claim(
        stale_processing.event_id,
        claimed_at=(decision_time - timedelta(seconds=301)).isoformat(),
    )
    assert store.claim(
        fresh_processing.event_id,
        claimed_at=(decision_time - timedelta(seconds=299)).isoformat(),
    )

    expired = main._edli_expire_unadmitted_redecision_pending(
        world,
        set(),
        decision_time=decision_time.isoformat(),
    )

    statuses = dict(
        world.execute(
            """
            SELECT e.entity_key, p.processing_status
              FROM opportunity_events e
              JOIN opportunity_event_processing p ON p.event_id = e.event_id
             WHERE p.consumer_name = ?
            """,
            (store.consumer_name,),
        ).fetchall()
    )
    assert expired == 1
    assert statuses[stale_processing.entity_key] == "expired"
    assert statuses[fresh_processing.entity_key] == "processing"


def test_redecision_admission_is_screen_job_only():
    """The reactor cycle may emit FSR discovery, but EDLI_REDECISION_PENDING belongs to the screen."""

    reactor_src = inspect.getsource(main._edli_event_reactor_cycle)
    screen_src = inspect.getsource(main._edli_continuous_redecision_screen_cycle)

    assert "event_type=REDECISION_EVENT_TYPE" not in reactor_src
    assert "event_type=REDECISION_EVENT_TYPE" in screen_src


def test_restricted_redecision_filters_before_fairness_window():
    """A small screened/held set must not disappear because the all-universe fair window missed it."""

    rows = [
        {
            "snapshot_city": "Chicago",
            "snapshot_target_date": "2026-06-19",
            "snapshot_temperature_metric": "high",
            "readiness_status": "LIVE_ELIGIBLE",
        },
        {
            "snapshot_city": "Tokyo",
            "snapshot_target_date": "2026-06-18",
            "snapshot_temperature_metric": "low",
            "readiness_status": "LIVE_ELIGIBLE",
        },
    ]
    restricted = _filter_rows_to_restricted_families(
        rows,
        {("Tokyo", "2026-06-18", "low")},
    )
    selected = CoverageFairnessRequest(limit=1, cycle_index=0).select_rows(restricted)

    assert [(
        selected[0]["snapshot_city"],
        selected[0]["snapshot_target_date"],
        selected[0]["snapshot_temperature_metric"],
    )] == [("Tokyo", "2026-06-18", "low")]

    src = inspect.getsource(ForecastSnapshotReadyTrigger.build_committed_snapshot_events)
    assert src.index("rows = _filter_rows_to_restricted_families") < src.index(
        "rows = CoverageFairnessRequest"
    )


def test_held_position_families_are_monitor_inputs_for_entry_suppression(monkeypatch):
    """Held families are monitor inputs and duplicate-entry suppressors, not entry re-emits."""

    monkeypatch.setattr(
        main,
        "_edli_reactor_held_family_provider",
        lambda: lambda: frozenset(
            {
                ("Tokyo", "2026-06-04", "high"),
                ("", "2026-06-04", "low"),
            }
        ),
    )

    assert main._edli_current_held_position_family_keys() == {
        ("Tokyo", "2026-06-04", "high")
    }


def test_held_position_families_reemit_when_forecast_phase_admits(monkeypatch):
    """Pre-settlement held exposure re-enters full redecision for fill-up/shift."""

    def _fake_market_phase_admits(*, city, target_date, metric, decision_time, market_row):
        assert decision_time == datetime(2026, 6, 17, 22, 45, tzinfo=timezone.utc)
        assert market_row == {}
        return city == "Tokyo"

    monkeypatch.setattr(
        "src.strategy.market_phase.market_phase_admits",
        _fake_market_phase_admits,
    )

    assert main._edli_reemittable_held_position_family_keys(
        {
            ("Tokyo", "2026-06-18", "low"),
            ("Shenzhen", "2026-06-19", "high"),
        },
        decision_time=datetime(2026, 6, 17, 22, 45, tzinfo=timezone.utc),
    ) == {("Tokyo", "2026-06-18", "low")}


def test_entry_redecision_families_use_forecast_phase_gate(monkeypatch):
    """New-entry redecision families must not count phase-dropped markets as admitted."""

    def _fake_market_phase_admits(*, city, target_date, metric, decision_time, market_row):
        assert decision_time == datetime(2026, 6, 18, 0, 5, tzinfo=timezone.utc)
        assert market_row == {}
        return city == "Shenzhen"

    monkeypatch.setattr(
        "src.strategy.market_phase.market_phase_admits",
        _fake_market_phase_admits,
    )

    assert main._edli_reemittable_forecast_family_keys(
        {
            ("Wellington", "2026-06-18", "high"),
            ("Shenzhen", "2026-06-19", "high"),
        },
        decision_time=datetime(2026, 6, 18, 0, 5, tzinfo=timezone.utc),
        log_context="entry-screen",
    ) == {("Shenzhen", "2026-06-19", "high")}


def test_redecision_screen_separates_entry_from_held_reemit():
    """Held families use a separate full-redecision admission path from new entry."""

    screen_src = inspect.getsource(main._edli_continuous_redecision_screen_cycle)

    assert "raw_entry_family_keys = screened_family_keys" in screen_src
    assert "family_keys = _edli_entry_redecision_family_keys" in screen_src
    assert "held_reemit_families = _edli_reemittable_held_position_family_keys" in screen_src
    assert "all_families = set(family_keys) | rest_pull_families | held_reemit_families" in screen_src
    assert "held_monitor_families=%d held_reemit_families=%d families_reemitted=%d" in screen_src
    assert "suppressed_existing_pending=%d" in screen_src
    assert "no_current_edge_or_rest_reprice_value" in inspect.getsource(
        main._edli_expire_unadmitted_redecision_pending
    )


def test_entry_redecision_excludes_current_held_families(monkeypatch):
    monkeypatch.setattr(
        main,
        "_edli_reemittable_forecast_family_keys",
        lambda families, *, decision_time, log_context: set(families),
    )

    admitted = main._edli_entry_redecision_family_keys(
        {
            ("Paris", "2026-06-19", "low"),
            ("Shanghai", "2026-06-19", "low"),
        },
        {("Paris", "2026-06-19", "low")},
        decision_time=_decision_time(),
    )

    assert admitted == {("Shanghai", "2026-06-19", "low")}


def test_unvalued_pending_redecision_is_kept_for_admitted_held_reemit_family():
    """Held redecision admission keeps pending work alive for the full selector."""

    world = sqlite3.connect(":memory:")
    init_schema(world)
    store = EventStore(world, consumer_name="edli_reactor_v1")
    held_only = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Tokyo|2026-06-18|low|run-held-only",
        source="cycle-held-only",
        observed_at="2026-06-17T15:00:00+00:00",
        available_at="2026-06-17T15:00:00+00:00",
        received_at="2026-06-17T15:00:00+00:00",
        causal_snapshot_id="snap-held-only",
        payload=_ready_payload(
            city="Tokyo",
            target_date="2026-06-18",
            metric="low",
            source_run_id="run-held-only",
            snapshot_id="snap-held-only",
        ),
        priority=50,
    )
    store.insert_or_ignore(held_only)

    expired = main._edli_expire_unadmitted_redecision_pending(
        world,
        {("Tokyo", "2026-06-18", "low")},
        decision_time="2026-06-17T16:00:00+00:00",
    )

    row = world.execute(
        """
        SELECT p.processing_status, p.last_error
          FROM opportunity_events e
          JOIN opportunity_event_processing p ON p.event_id = e.event_id
         WHERE e.entity_key = ?
        """,
        (held_only.entity_key,),
    ).fetchone()
    assert expired == 0
    assert tuple(row) == ("pending", None)


def test_unready_replacement_fsr_pending_expires_on_latest_spine_gap():
    world = sqlite3.connect(":memory:")
    init_schema(world)
    store = EventStore(world, consumer_name="edli_reactor_v1")
    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Cape Town|2026-06-19|high|rmf-2026-06-18",
        source="cycle-test",
        observed_at="2026-06-18T07:58:00+00:00",
        available_at="2026-06-18T07:58:00+00:00",
        received_at="2026-06-18T07:58:00+00:00",
        causal_snapshot_id="rmf-Cape Town|2026-06-19|high|2026-06-18",
        payload={
            "city": "Cape Town",
            "target_date": "2026-06-19",
            "metric": "high",
            "track": "replacement_0_1_aifs_openmeteo_soft_anchor",
            "snapshot_id": "rmf-Cape Town|2026-06-19|high|2026-06-18",
        },
        priority=50,
    )
    store.insert_or_ignore(event)

    forecasts = sqlite3.connect(":memory:")
    forecasts.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            product_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT,
            computed_at TEXT,
            runtime_layer TEXT
        )
        """
    )
    forecasts.execute(
        """
        CREATE TABLE raw_model_forecasts (
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT,
            forecast_value_c REAL
        )
        """
    )
    forecasts.execute(
        """
        INSERT INTO forecast_posteriors VALUES (
            1,
            'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1',
            'Cape Town',
            '2026-06-19',
            'high',
            '2026-06-18T00:00:00+00:00',
            '2026-06-18T06:39:00+00:00',
            '2026-06-18T07:58:00+00:00',
            'live'
        )
        """
    )
    forecasts.executemany(
        """
        INSERT INTO raw_model_forecasts VALUES (
            ?, 'Cape Town', '2026-06-19', 'high',
            '2026-06-17T18:00:00+00:00',
            '2026-06-17T19:00:00+00:00',
            ?
        )
        """,
        [("old-a", 18.0), ("old-b", 18.5), ("old-c", 19.0)],
    )

    expired = main._edli_expire_unready_forecast_snapshot_pending(
        world,
        forecasts,
        decision_time="2026-06-18T08:00:00+00:00",
    )

    row = world.execute(
        """
        SELECT p.processing_status, p.last_error
          FROM opportunity_events e
          JOIN opportunity_event_processing p ON p.event_id = e.event_id
         WHERE e.event_id = ?
        """,
        (event.event_id,),
    ).fetchone()
    assert expired == 1
    assert tuple(row) == (
        "expired",
        "FORECAST_ADMISSION_EXPIRED:latest_posterior_spine_unavailable",
    )


def test_unready_replacement_sweep_batches_forecast_reads():
    src = inspect.getsource(main._edli_expire_unready_forecast_snapshot_pending)
    assert "date(source_cycle_time)" not in src
    assert "runtime_layer = 'live'" in src
    assert "endpoint = 'single_runs'" in src
    assert "INDEXED BY idx_raw_model_forecasts_endpoint_family_cycle_members" in src
    assert "latest_cycle_by_family" in src
    assert "member_count_by_family_cycle" in src
    assert "COUNT(DISTINCT model)" in src


def test_unready_replacement_sweep_is_gated_by_active_rmf_queue():
    world = sqlite3.connect(":memory:")
    init_schema(world)
    store = EventStore(world, consumer_name="edli_reactor_v1")
    for city in ("Tokyo", "Paris", "Taipei"):
        store.insert_or_ignore(
            make_opportunity_event(
                event_type="FORECAST_SNAPSHOT_READY",
                entity_key=f"{city}|2026-06-25|high|rmf-2026-06-24",
                source="cycle-test",
                observed_at="2026-06-24T06:00:00+00:00",
                available_at="2026-06-24T06:00:00+00:00",
                received_at="2026-06-24T06:00:00+00:00",
                causal_snapshot_id=f"rmf-{city}|2026-06-25|high|2026-06-24",
                payload={
                    "city": city,
                    "target_date": "2026-06-25",
                    "metric": "high",
                    "snapshot_id": f"rmf-{city}|2026-06-25|high|2026-06-24",
                },
                priority=50,
            )
        )

    assert main._edli_active_rmf_forecast_snapshot_pending_count(world, limit=2) == 2
    assert main._edli_active_rmf_forecast_snapshot_pending_count(world, limit=10) == 3

    cfg_src = inspect.getsource(main._edli_unready_fsr_prune_min_active_pending)
    assert "reactor_unready_fsr_prune_min_active_pending" in cfg_src
    prune_src = inspect.getsource(main._edli_prune_pending_working_set)
    assert prune_src.index("_edli_active_rmf_forecast_snapshot_pending_count") < prune_src.index(
        "_edli_expire_unready_forecast_snapshot_pending"
    )


def test_held_position_family_provider_excludes_closed_phases():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_current (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            shares REAL,
            chain_shares REAL,
            cost_basis_usd REAL,
            chain_cost_basis_usd REAL,
            size_usd REAL,
            chain_state TEXT,
            phase TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO position_current VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("Tokyo", "2026-06-18", "low", 19.5, 19.5, 12.0, 12.0, 12.0, "synced", "day0_window"),
            ("Shenzhen", "2026-06-19", "high", 60.0, 60.0, 44.4, 44.4, 44.4, "synced", "active"),
            ("Hong Kong", "2026-06-08", "high", 10.0, 10.0, 8.0, 8.0, 8.0, "synced", "economically_closed"),
            ("Warsaw", "2026-06-08", "high", 15.75, 15.75, 9.0, 9.0, 9.0, "synced", "admin_closed"),
            ("Seoul", "2026-06-08", "high", 7.0, 7.0, 5.0, 5.0, 5.0, "synced", "quarantined"),
            ("Busan", "2026-06-20", "high", 22.0, 22.0, 15.0, 15.0, 15.0, "synced", "pending_entry"),
            ("Osaka", "2026-06-21", "high", 12.0, 12.0, 0.0, 0.0, 0.0, "synced", "active"),
            ("Paris", "2026-06-22", "low", 10.0, 0.0, 8.0, 0.0, 8.0, "local_only", "active"),
            ("Munich", "2026-06-22", "high", 5.0, 5.0, 3.0, 3.0, 3.0, "chain_confirmed_zero", "active"),
        ],
    )

    assert _held_position_families(conn) == {
        ("Tokyo", "2026-06-18", "low"),
        ("Shenzhen", "2026-06-19", "high"),
    }


def test_held_position_family_provider_includes_market_closed_hold_exposure():
    """Market-closed rows with chain exposure remain held-family redecision inputs."""

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_current (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            shares REAL,
            chain_shares REAL,
            cost_basis_usd REAL,
            chain_cost_basis_usd REAL,
            size_usd REAL,
            chain_state TEXT,
            phase TEXT,
            order_status TEXT,
            exit_reason TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO position_current VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "Seattle",
                "2026-06-19",
                "high",
                9.39,
                9.39,
                6.85,
                6.85,
                6.85,
                "synced",
                "pending_exit",
                "backoff_exhausted",
                "MARKET_CLOSED_AWAITING_SETTLEMENT",
            ),
            (
                "Tokyo",
                "2026-06-21",
                "low",
                11.33,
                11.33,
                7.02,
                7.02,
                7.02,
                "synced",
                "active",
                "filled",
                "",
            ),
            (
                "Paris",
                "2026-06-20",
                "low",
                5.06,
                5.06,
                3.79,
                3.79,
                3.79,
                "synced",
                "pending_exit",
                "backoff_exhausted",
                "MODEL_DIVERGENCE_PANIC",
            ),
        ],
    )

    assert _held_position_families(conn) == {
        ("Seattle", "2026-06-19", "high"),
        ("Tokyo", "2026-06-21", "low"),
        ("Paris", "2026-06-20", "low"),
    }


def test_held_position_family_provider_accepts_chain_confirmed_quantity():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_current (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            shares REAL,
            chain_shares REAL,
            cost_basis_usd REAL,
            size_usd REAL,
            chain_cost_basis_usd REAL,
            chain_state TEXT,
            phase TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO position_current VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("Shenzhen", "2026-06-19", "high", 0.0, 60.0, 0.0, 0.0, 44.4, "synced", "active"),
            ("Busan", "2026-06-20", "high", 0.0, 10.0, 0.0, 0.0, 7.0, "synced", "pending_entry"),
            ("Paris", "2026-06-21", "low", 0.0, 10.0, 0.0, 0.0, 7.0, "local_only", "active"),
        ],
    )

    assert _held_position_families(conn) == {("Shenzhen", "2026-06-19", "high")}


def test_held_condition_scope_excludes_zero_chain_local_ghosts(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_current (
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            condition_id TEXT,
            chain_state TEXT,
            chain_shares REAL,
            phase TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO position_current VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("Hong Kong", "2026-06-09", "high", "ghost-cond", "local_only", 0.0, "active"),
            ("Hong Kong", "2026-06-26", "low", "live-cond", "synced", 5.0, "day0_window"),
            ("Singapore", "2026-06-26", "high", "exit-cond", "exit_pending_missing", 1.0, "pending_exit"),
            ("Paris", "2026-06-26", "low", "zero-cond", "synced", 0.0, "active"),
        ],
    )
    monkeypatch.setattr("src.state.db.get_trade_connection_read_only", lambda: conn)

    assert main._edli_current_held_position_condition_scope() == {
        ("Hong Kong", "2026-06-26", "low"): {"live-cond"},
        ("Singapore", "2026-06-26", "high"): {"exit-cond"},
    }


def test_redecision_cycle_prunes_before_snapshotting_pending_keys():
    """The reactor cycle must prune the working set before taking the redecision skip snapshot."""

    src = inspect.getsource(main._edli_event_reactor_cycle)
    assert src.index("_edli_prune_pending_working_set(") < src.index("_edli_pending_entity_keys(")


def test_reactor_prune_archives_orphan_processing_rows():
    """Active rows without opportunity_events provenance must leave the working set."""

    src = inspect.getsource(main._edli_prune_pending_working_set)
    assert "archive_orphan_processing_rows" in src
    assert src.index("archive_orphan_processing_rows") < src.index("archive_expired_candidates")


def test_reactor_prune_budget_exhaustion_restores_busy_timeout(monkeypatch):
    """Maintenance prune may skip remaining work, but must not poison later claim waits."""

    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    world.execute("PRAGMA busy_timeout = 30000")
    store = EventStore(world)

    monkeypatch.setattr(
        main,
        "_settings_section",
        lambda name, default=None: {
            "reactor_prune_interval_seconds": 0,
            "reactor_prune_batch_limit": 10,
            "reactor_prune_budget_seconds": 0.001,
            "reactor_prune_busy_timeout_ms": 1,
        }
        if name == "edli"
        else (default if default is not None else {}),
    )

    def _slow_first_step(*, batch_limit):
        time.sleep(0.01)
        return 0

    monkeypatch.setattr(store, "archive_orphan_processing_rows", _slow_first_step)
    monkeypatch.setattr(
        store,
        "requeue_misclassified_local_pre_submit_rejections",
        lambda *, batch_limit: pytest.fail("budget exhaustion should skip later prune steps"),
    )

    main._edli_prune_pending_working_set(
        store,
        decision_time=datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc),
    )

    assert world.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


def test_sqlite_deadline_interrupts_and_clears():
    """Reactor maintenance budgets must interrupt SQLite itself, not only Python between steps."""

    conn = sqlite3.connect(":memory:")
    deadline = time.monotonic() - 0.001
    main._edli_install_sqlite_deadline(conn, deadline_monotonic=deadline)
    try:
        with pytest.raises(sqlite3.OperationalError, match="interrupted"):
            conn.execute(
                """
                WITH RECURSIVE x(n) AS (
                    SELECT 1
                    UNION ALL
                    SELECT n + 1 FROM x WHERE n < 1000000
                )
                SELECT sum(n) FROM x
                """
            ).fetchone()
    finally:
        main._edli_clear_sqlite_progress_handler(conn)

    assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_forecast_snapshot_build_is_reactor_budgeted():
    """FSR event build is before process_pending; it must not be an unbounded reactor stage."""

    src = inspect.getsource(main._edli_event_reactor_cycle)
    assert "reactor_forecast_snapshot_build_budget_seconds" in inspect.getsource(
        main._edli_forecast_snapshot_build_budget_seconds
    )
    assert "budget_seconds=_edli_forecast_snapshot_build_budget_seconds(edli_cfg)" in src


def test_day0_emit_is_reactor_budgeted():
    """Day0 catch-up runs under the world mutex, so it needs a hard SQLite deadline."""

    cycle_src = inspect.getsource(main._edli_event_reactor_cycle)
    emit_src = inspect.getsource(main._edli_emit_day0_extreme_events)
    assert "reactor_day0_emit_budget_seconds" in inspect.getsource(
        main._edli_day0_emit_budget_seconds
    )
    assert "reactor_day0_emit_busy_timeout_ms" in inspect.getsource(
        main._edli_day0_emit_busy_timeout_ms
    )
    assert "budget_seconds=_edli_day0_emit_budget_seconds(edli_cfg)" in cycle_src
    assert "_edli_install_sqlite_deadline(world_conn" in emit_src
    assert "_edli_install_sqlite_deadline(trade_conn" in emit_src
    assert "_edli_set_sqlite_busy_timeout_ms(world_conn, day0_busy_timeout_ms)" in emit_src
    assert "_edli_set_sqlite_busy_timeout_ms(trade_conn, day0_busy_timeout_ms)" in emit_src
    assert "EDLI day0 emit budget exhausted" in emit_src


def test_decision_triggered_refresh_is_cycle_bounded():
    """Inline recapture is live value, but must not monopolize the reactor cycle."""

    src = inspect.getsource(main._edli_decision_family_snapshot_refresher)
    assert "reactor_decision_refresh_max_per_cycle" in src
    assert "reactor_decision_refresh_cycle_budget_seconds" in src
    assert "budget_seconds=call_budget_s" in src
    assert "refresh_attempts += 1" in src


def test_requeue_misclassified_local_pre_submit_rejections_reports_actual_changes():
    """SQLite CTE UPDATE rowcount can be -1; live logs need the real change count."""

    world = sqlite3.connect(":memory:")
    world.row_factory = sqlite3.Row
    init_schema(world)
    store = EventStore(world)
    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Paris|2026-06-26|high|run-1",
        source="forecast",
        observed_at="2026-06-26T10:00:00+00:00",
        available_at="2026-06-26T10:00:00+00:00",
        received_at="2026-06-26T10:00:00+00:00",
        payload=ForecastSnapshotReadyPayload(
            city="Paris",
            target_date="2026-06-26",
            metric="high",
            source_id="ecmwf-open-data",
            source_run_id="run-1",
            cycle="00",
            track="ens",
            snapshot_id="snap-1",
            snapshot_hash="snap-1",
            captured_at="2026-06-26T10:00:00+00:00",
            available_at="2026-06-26T10:00:00+00:00",
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
        created_at="2026-06-26T10:00:00+00:00",
    )
    store.insert_or_ignore(event)
    world.execute(
        """
        UPDATE opportunity_event_processing
           SET processing_status = 'processed',
               processed_at = '2026-06-26T10:00:01+00:00',
               updated_at = '2026-06-26T10:00:01+00:00'
         WHERE consumer_name = ? AND event_id = ?
        """,
        (store.consumer_name, event.event_id),
    )
    payload = {
        "event_id": event.event_id,
        "reason_code": "entries_paused:operator",
        "pre_submit_rejection": 0,
        "venue_order_id": "",
    }
    world.execute(
        """
        INSERT INTO edli_live_order_events (
            aggregate_event_id, aggregate_id, event_sequence, event_type,
            parent_event_hash, event_hash, payload_json, payload_hash,
            source_authority, occurred_at, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "agg-event-1",
            "agg-1",
            1,
            "SubmitRejected",
            None,
            "hash-1",
            json.dumps(payload, sort_keys=True),
            "payload-hash-1",
            "existing_executor",
            "2026-06-26T10:00:02+00:00",
            "2026-06-26T10:00:02+00:00",
            1,
        ),
    )

    assert store.requeue_misclassified_local_pre_submit_rejections(batch_limit=10) == 1
    row = world.execute(
        """
        SELECT processing_status, last_error
          FROM opportunity_event_processing
         WHERE consumer_name = ? AND event_id = ?
        """,
        (store.consumer_name, event.event_id),
    ).fetchone()
    assert row["processing_status"] == "pending"
    assert row["last_error"] == "RECOVERED_MISCLASSIFIED_LOCAL_PRESUBMIT_REJECTION"
