# Lifecycle: created=2026-04-17; last_reviewed=2026-04-29; last_reused=2026-04-29
# Purpose: Lock market_scanner provenance and source-contract drift behavior.
# Reuse: Inspect src/data/market_scanner.py and scripts/watch_source_contract.py before relying on these assertions.
# Authority basis: audit bug B017 (STILL_OPEN P1 SD-H), Fitz methodology constraint #4 "Data Provenance > Code Correctness"
"""B017 relationship tests: market_scanner cache must expose provenance.

These tests pin the cross-module invariant:

  "When the underlying Gamma fetch fails, any events returned from
   ``_get_active_events_snapshot`` MUST carry authority != 'VERIFIED',
   and ``get_last_scan_authority()`` MUST reflect the same state that
   downstream callers would observe."

They run against the module-level globals so they must reset cache
state between cases (conftest-free isolation).
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from src.backtest.economics import check_economics_readiness
from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
from src.data import market_scanner as ms
from src.data.market_scanner import (
    MarketSnapshot,
    build_market_support_topology,
    _clear_active_events_cache,
    _get_active_events,
    _get_active_events_snapshot,
    _parse_event,
    get_last_scan_authority,
)
from src.state import db as state_db
from src.state.db import log_executable_snapshot_market_price_linkage, log_forward_market_substrate
from src.state.schema.v2_schema import apply_v2_schema
from src.state.snapshot_repo import init_snapshot_schema, insert_snapshot


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch, tmp_path):
    """Reset scanner module state and isolate quarantine state around every test."""
    monkeypatch.setenv(
        ms.SOURCE_CONTRACT_QUARANTINE_PATH_ENV,
        str(tmp_path / "source_contract_quarantine.json"),
    )
    _clear_active_events_cache()
    yield
    _clear_active_events_cache()


def _make_dummy_event(market_id: str = "m1") -> dict:
    """Minimal event shape enough to survive downstream filtering."""
    return {
        "id": "evt-1",
        "slug": "temp-evt-1",
        "title": "Highest temperature in Test City",
        "markets": [
            {
                "id": market_id,
                "question": "Temp 40-50F",
                "outcomePrices": "[0.3, 0.7]",
                "clobTokenIds": '["yes-tok", "no-tok"]',
                "outcomes": '["Yes", "No"]',
                "startDate": "2026-04-17T00:00:00Z",
                "endDate": "2026-04-17T23:00:00Z",
                "active": True,
                "closed": False,
            }
        ],
    }


def _gamma_temperature_event(
    *,
    event_id: str = "event1",
    market_id: str = "market1",
    title: str = "Highest temperature in Los Angeles on April 29?",
    slug: str = "highest-temperature-in-los-angeles-on-april-29-2026",
    question: str = "Will the high temperature in Los Angeles be 68°F or higher?",
    resolution_source: str | None = "https://www.wunderground.com/history/daily/us/ca/los-angeles/KLAX",
    market_resolution_source: str | None = None,
) -> dict:
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*°?([FC])\s+or\s+higher", question)
    threshold = int(float(match.group(1))) if match else 68
    unit = match.group(2) if match else "F"
    if unit == "F":
        low_label = f"Will the high temperature be {threshold - 3}°{unit} or below?"
        center_label = f"Will the high temperature be {threshold - 2}-{threshold - 1}°{unit}?"
    else:
        low_label = f"Will the high temperature be {threshold - 2}°{unit} or below?"
        center_label = f"Will the high temperature be {threshold - 1}°{unit} on April 29?"

    def _market(
        *,
        market_id_value: str,
        condition_id: str,
        question_value: str,
        token_suffix: str,
        yes_price: float,
    ) -> dict:
        market = {
            "id": market_id_value,
            "question": question_value,
            "outcomePrices": json.dumps([yes_price, round(1.0 - yes_price, 2)]),
            "outcomes": '["Yes", "No"]',
            "clobTokenIds": json.dumps([f"token_yes_{token_suffix}", f"token_no_{token_suffix}"]),
            "conditionId": condition_id,
            "active": True,
            "closed": False,
            "acceptingOrders": True,
            "enableOrderBook": True,
        }
        if market_resolution_source is not None:
            market["resolutionSource"] = market_resolution_source
        return market

    markets = [
        _market(
            market_id_value=f"{market_id}-low",
            condition_id="cond-low",
            question_value=low_label,
            token_suffix="low",
            yes_price=0.10,
        ),
        _market(
            market_id_value=f"{market_id}-center",
            condition_id="cond-center",
            question_value=center_label,
            token_suffix="center",
            yes_price=0.35,
        ),
        _market(
            market_id_value=market_id,
            condition_id="cond1",
            question_value=question,
            token_suffix="primary",
            yes_price=0.55,
        ),
    ]
    event = {
        "id": event_id,
        "slug": slug,
        "title": title,
        "markets": markets,
    }
    if resolution_source is not None:
        event["resolutionSource"] = resolution_source
    return event


def _gamma_support_event_with_closed_low_shoulder() -> dict:
    event = _gamma_temperature_event(
        event_id="support-event",
        market_id="low-shoulder-market",
        question="Will the high temperature in Los Angeles be 60°F or below?",
    )
    event["markets"] = [
        {
            "id": "low-shoulder-market",
            "question": "Will the high temperature in Los Angeles be 60°F or below?",
            "outcomePrices": "[0.01, 0.99]",
            "outcomes": '["Yes", "No"]',
            "clobTokenIds": '["yes-low-closed", "no-low-closed"]',
            "conditionId": "cond-low-closed",
            "questionID": "qid-low-closed",
            "active": True,
            "closed": True,
            "acceptingOrders": False,
            "enableOrderBook": False,
        },
        {
            "id": "center-market",
            "question": "Will the high temperature in Los Angeles be 61-62°F?",
            "outcomePrices": "[0.35, 0.65]",
            "outcomes": '["Yes", "No"]',
            "clobTokenIds": '["yes-center", "no-center"]',
            "conditionId": "cond-center",
            "questionID": "qid-center",
            "active": True,
            "closed": False,
            "acceptingOrders": True,
            "enableOrderBook": True,
        },
        {
            "id": "high-shoulder-market",
            "question": "Will the high temperature in Los Angeles be 63°F or higher?",
            "outcomePrices": "[0.64, 0.36]",
            "outcomes": '["Yes", "No"]',
            "clobTokenIds": '["yes-high", "no-high"]',
            "conditionId": "cond-high",
            "questionID": "qid-high",
            "active": True,
            "closed": False,
            "acceptingOrders": True,
            "enableOrderBook": True,
        },
    ]
    return event


def _complete_release_evidence(prefix: str = "docs/operations/source_transition") -> dict:
    release_evidence = {key: True for key in ms.REQUIRED_SOURCE_CONVERSION_EVIDENCE}
    release_evidence["evidence_refs"] = {
        key: f"{prefix}/{key}.md"
        for key in ms.REQUIRED_SOURCE_CONVERSION_EVIDENCE
    }
    return release_evidence


def _make_forward_substrate_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE market_events_v2 (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
            condition_id TEXT,
            token_id TEXT,
            range_label TEXT,
            range_low REAL,
            range_high REAL,
            outcome TEXT,
            created_at TEXT,
            recorded_at TEXT NOT NULL,
            UNIQUE(market_slug, condition_id)
        );
        CREATE TABLE market_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market_slug TEXT NOT NULL,
            token_id TEXT NOT NULL,
            price REAL NOT NULL,
            recorded_at TEXT NOT NULL,
            hours_since_open REAL,
            hours_to_resolution REAL,
            market_price_linkage TEXT NOT NULL DEFAULT 'price_only',
            source TEXT NOT NULL DEFAULT 'GAMMA_SCANNER',
            best_bid REAL,
            best_ask REAL,
            raw_orderbook_hash TEXT,
            snapshot_id TEXT,
            condition_id TEXT,
            UNIQUE(token_id, recorded_at)
        );
        """
    )
    return conn


def _make_full_linkage_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_v2_schema(conn)
    init_snapshot_schema(conn)
    return conn


def _insert_full_linkage_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_id: str = "snap-full-linkage",
    best_bid: Decimal = Decimal("0.42"),
    best_ask: Decimal = Decimal("0.44"),
) -> None:
    captured_at = datetime(2026, 4, 30, 16, 0, tzinfo=timezone.utc)
    insert_snapshot(
        conn,
        ExecutableMarketSnapshotV2(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-full-linkage",
            event_id="event-full-linkage",
            event_slug="highest-temperature-in-chicago-on-april-30-2026",
            condition_id="cond-full-linkage",
            question_id="question-full-linkage",
            yes_token_id="yes-full-linkage",
            no_token_id="no-full-linkage",
            selected_outcome_token_id="yes-full-linkage",
            outcome_label="YES",
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            fee_details={"source": "test"},
            token_map_raw={"YES": "yes-full-linkage", "NO": "no-full-linkage"},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=best_bid,
            orderbook_top_ask=best_ask,
            orderbook_depth_jsonb='{"asks":[{"price":"0.44","size":"100"}],"bids":[{"price":"0.42","size":"100"}]}',
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=captured_at,
            freshness_deadline=captured_at + timedelta(seconds=30),
        ),
    )


def _forward_market() -> dict:
    return {
        "slug": "lowest-temperature-in-chicago-on-april-30-2026",
        "city": "Chicago",
        "target_date": "2026-04-30",
        "temperature_metric": "low",
        "hours_since_open": 2.5,
        "hours_to_resolution": 18.0,
        "outcomes": [
            {
                "condition_id": "cond-low-shoulder",
                "token_id": "yes-low-shoulder",
                "no_token_id": "no-low-shoulder",
                "title": "35°F or lower",
                "range_low": None,
                "range_high": 35.0,
                "price": 0.31,
                "no_price": 0.69,
                "market_start_at": "2026-04-29T12:00:00Z",
            },
            {
                "condition_id": "cond-low-range",
                "token_id": "yes-low-range",
                "no_token_id": "no-low-range",
                "title": "36-37°F",
                "range_low": 36.0,
                "range_high": 37.0,
                "price": "0.42",
                "no_price": "0.58",
                "market_start_at": "2026-04-29T12:00:00Z",
            },
        ],
    }


class TestB017MarketSnapshotProvenance:
    """Snapshot API exposes provenance on every code path."""

    def test_b017_fresh_fetch_authority_is_verified(self, monkeypatch):
        """A successful fetch returns authority=VERIFIED and
        stale_age_seconds=0."""
        monkeypatch.setattr(
            ms, "_fetch_events_by_tags", lambda: [_make_dummy_event()]
        )
        snap = _get_active_events_snapshot()
        assert isinstance(snap, MarketSnapshot)
        assert snap.authority == "VERIFIED"
        assert snap.stale_age_seconds == 0.0
        assert snap.fetched_at_utc is not None
        assert len(snap.events) == 1
        assert get_last_scan_authority() == "VERIFIED"

    def test_b017_network_failure_with_cache_returns_stale(self, monkeypatch):
        """When the fetch raises, a populated cache is returned but
        authority=STALE and stale_age_seconds>=0."""
        # First, prime the cache with one successful fetch.
        monkeypatch.setattr(
            ms, "_fetch_events_by_tags", lambda: [_make_dummy_event("m-primed")]
        )
        _get_active_events_snapshot()
        assert get_last_scan_authority() == "VERIFIED"

        # Force the cache to look expired so the next call re-fetches.
        ms._ACTIVE_EVENTS_CACHE_AT -= ms._ACTIVE_EVENTS_TTL + 1.0

        def _raise(*_a, **_kw):
            raise httpx.ConnectError("simulated network failure")

        monkeypatch.setattr(ms, "_fetch_events_by_tags", _raise)

        snap = _get_active_events_snapshot()
        assert snap.authority == "STALE"
        assert snap.stale_age_seconds is not None
        assert snap.stale_age_seconds > 0
        assert any(
            m["id"] == "m-primed"
            for evt in snap.events
            for m in evt.get("markets", [])
        )
        assert get_last_scan_authority() == "STALE"

    def test_b017_network_failure_without_cache_returns_empty_fallback(
        self, monkeypatch
    ):
        """No cache + fetch failure => authority=EMPTY_FALLBACK and
        empty events, NOT VERIFIED."""
        def _raise(*_a, **_kw):
            raise httpx.ConnectError("simulated network failure")

        monkeypatch.setattr(ms, "_fetch_events_by_tags", _raise)

        snap = _get_active_events_snapshot()
        assert snap.authority == "EMPTY_FALLBACK"
        assert snap.events == []
        assert snap.stale_age_seconds is None
        assert get_last_scan_authority() == "EMPTY_FALLBACK"

    def test_b017_legacy_api_still_returns_list_for_backwards_compat(
        self, monkeypatch
    ):
        """Dual-Track callers use ``_get_active_events`` (returns
        list[dict]). That signature MUST not change."""
        monkeypatch.setattr(
            ms, "_fetch_events_by_tags", lambda: [_make_dummy_event()]
        )
        result = _get_active_events()
        assert isinstance(result, list)
        assert all(isinstance(e, dict) for e in result)

    def test_b017_authority_reflects_last_call_not_last_fetch(
        self, monkeypatch
    ):
        """After a VERIFIED call followed by a STALE call,
        ``get_last_scan_authority()`` reports STALE (the latest call),
        not VERIFIED."""
        monkeypatch.setattr(
            ms, "_fetch_events_by_tags", lambda: [_make_dummy_event()]
        )
        _get_active_events_snapshot()
        assert get_last_scan_authority() == "VERIFIED"

        ms._ACTIVE_EVENTS_CACHE_AT -= ms._ACTIVE_EVENTS_TTL + 1.0

        def _raise(*_a, **_kw):
            raise httpx.ReadTimeout("simulated timeout")

        monkeypatch.setattr(ms, "_fetch_events_by_tags", _raise)
        _get_active_events_snapshot()
        assert get_last_scan_authority() == "STALE"


class TestSourceContractGate:
    """Gamma resolutionSource must match the configured settlement contract."""

    def test_matching_wu_station_carries_source_contract(self):
        event = _gamma_temperature_event()

        parsed = _parse_event(
            event,
            datetime(2026, 4, 28, tzinfo=timezone.utc),
            min_hours=0.0,
        )

        assert parsed is not None
        assert parsed["city"].name == "Los Angeles"
        assert parsed["source_contract"]["status"] == "MATCH"
        assert parsed["source_contract"]["source_family"] == "wu_icao"
        assert parsed["source_contract"]["station_id"] == "KLAX"
        assert parsed["resolution_source"].endswith("/KLAX")

    def test_contract_support_retains_closed_non_executable_shoulder(self):
        event = _gamma_support_event_with_closed_low_shoulder()

        parsed = _parse_event(
            event,
            datetime(2026, 4, 28, tzinfo=timezone.utc),
            min_hours=0.0,
        )

        assert parsed is not None
        assert [outcome["title"] for outcome in parsed["outcomes"]] == [
            "Will the high temperature in Los Angeles be 60°F or below?",
            "Will the high temperature in Los Angeles be 61-62°F?",
            "Will the high temperature in Los Angeles be 63°F or higher?",
        ]
        assert parsed["support_topology"]["topology_status"] == "complete"
        assert parsed["support_topology"]["executable_mask"] == [False, True, True]
        assert parsed["outcomes"][0]["executable"] is False
        assert 0 not in parsed["support_topology"]["token_payload_by_support_index"]
        assert set(parsed["support_topology"]["token_payload_by_support_index"]) == {1, 2}

    def test_all_child_support_gap_fails_closed_even_when_children_are_executable(self):
        event = _gamma_support_event_with_closed_low_shoulder()
        event["markets"][1]["question"] = (
            "Will the high temperature in Los Angeles be 62-63°F?"
        )

        parsed = _parse_event(
            event,
            datetime(2026, 4, 28, tzinfo=timezone.utc),
            min_hours=0.0,
        )

        assert parsed is None

    def test_support_topology_builder_separates_support_from_executability(self):
        topology = build_market_support_topology(
            _gamma_support_event_with_closed_low_shoulder(),
            unit="F",
        )

        assert [b.label for b in topology.support_bins] == [
            "Will the high temperature in Los Angeles be 60°F or below?",
            "Will the high temperature in Los Angeles be 61-62°F?",
            "Will the high temperature in Los Angeles be 63°F or higher?",
        ]
        assert topology.executable_mask == (False, True, True)
        assert [outcome["support_index"] for outcome in topology.support_outcomes] == [0, 1, 2]
        assert [outcome["support_index"] for outcome in topology.executable_outcomes] == [1, 2]

    def test_missing_tradability_flags_are_not_inferred_executable(self):
        event = _gamma_support_event_with_closed_low_shoulder()
        event["markets"][1].pop("acceptingOrders")

        topology = build_market_support_topology(event, unit="F")

        assert topology.executable_mask == (False, False, True)
        assert [outcome["support_index"] for outcome in topology.executable_outcomes] == [2]
        assert set(topology.token_payload_by_support_index) == {2}

    def test_current_yes_price_returns_none_for_non_executable_support_child(self, monkeypatch):
        event = _gamma_support_event_with_closed_low_shoulder()
        monkeypatch.setattr(ms, "_get_active_events", lambda: [event])

        assert ms.get_current_yes_price("cond-low-closed") is None

    def test_paris_lfpb_is_rejected_while_configured_lfpg(self):
        event = _gamma_temperature_event(
            title="Highest temperature in Paris on April 29?",
            slug="highest-temperature-in-paris-on-april-29-2026",
            question="Will the high temperature in Paris be 20°C or higher?",
            resolution_source=(
                "https://www.wunderground.com/history/daily/fr/"
                "bonneuil-en-france/LFPB"
            ),
        )

        parsed = _parse_event(
            event,
            datetime(2026, 4, 28, tzinfo=timezone.utc),
            min_hours=0.0,
        )

        assert parsed is None

    def test_multiple_station_sources_are_rejected(self):
        event = _gamma_temperature_event(
            market_resolution_source=(
                "https://www.wunderground.com/history/daily/us/ca/"
                "los-angeles/KSMO"
            )
        )

        parsed = _parse_event(
            event,
            datetime(2026, 4, 28, tzinfo=timezone.utc),
            min_hours=0.0,
        )

        assert parsed is None

    def test_city_matching_reads_runtime_city_config(self, monkeypatch):
        from src.config import City

        live_city = City(
            name="Reload City",
            lat=1.0,
            lon=2.0,
            timezone="UTC",
            settlement_unit="C",
            cluster="Reload City",
            wu_station="TEST",
            aliases=("Reload City",),
            slug_names=("reload-city",),
            airport_name="Reload Test Airport",
            settlement_source="https://www.wunderground.com/history/daily/xx/reload/TEST",
            country_code="XX",
        )
        monkeypatch.setattr(ms.runtime_config, "runtime_cities", lambda: [live_city])

        matched = ms._match_city(
            "highest temperature in reload city",
            "highest-temperature-in-reload-city-on-april-29-2026",
        )

        assert matched is live_city

    def test_imported_city_map_reference_hot_reloads(self, monkeypatch):
        from src import config as runtime_config
        from src.config import City

        original_load_cities = runtime_config.load_cities
        original_mtime = runtime_config._cities_config_mtime_ns
        imported_map = runtime_config.cities_by_name
        loaded_mtime = runtime_config._cities_loaded_mtime_ns
        reloaded_city = City(
            name="Hot Reload City",
            lat=10.0,
            lon=20.0,
            timezone="UTC",
            settlement_unit="C",
            cluster="Hot Reload City",
            wu_station="HOT1",
            aliases=("Hot Reload City",),
            slug_names=("hot-reload-city",),
            airport_name="Hot Reload Airport",
            settlement_source="https://www.wunderground.com/history/daily/xx/hot/HOT1",
            country_code="XX",
        )
        try:
            monkeypatch.setattr(runtime_config, "load_cities", lambda path=None: [reloaded_city])
            monkeypatch.setattr(
                runtime_config,
                "_cities_config_mtime_ns",
                lambda path=None: loaded_mtime + 1,
            )

            assert imported_map.get("Hot Reload City") is reloaded_city
            assert runtime_config.cities_by_name is imported_map
        finally:
            monkeypatch.setattr(runtime_config, "load_cities", original_load_cities)
            monkeypatch.setattr(runtime_config, "_cities_config_mtime_ns", original_mtime)
            runtime_config.reload_cities_if_changed(force=True)

    def test_unknown_resolution_source_url_is_rejected(self):
        event = _gamma_temperature_event(
            resolution_source="https://example.com/weather/stations/KLAX"
        )

        parsed = _parse_event(
            event,
            datetime(2026, 4, 28, tzinfo=timezone.utc),
            min_hours=0.0,
        )

        assert parsed is None

    def test_stationless_wu_source_is_rejected(self):
        event = _gamma_temperature_event(
            resolution_source="https://www.wunderground.com/weather/us/ca/los-angeles"
        )
        city = ms._match_city(
            str(event.get("title") or "").lower(),
            str(event.get("slug") or ""),
        )
        assert city is not None

        contract = ms._check_source_contract(event, city)
        parsed = _parse_event(
            event,
            datetime(2026, 4, 28, tzinfo=timezone.utc),
            min_hours=0.0,
        )

        assert contract.status == "UNSUPPORTED"
        assert contract.reason == (
            "resolutionSource does not prove the configured settlement station"
        )
        assert parsed is None

    def test_missing_resolution_source_is_tagged_and_not_discoverable(
        self, monkeypatch
    ):
        event = _gamma_temperature_event(resolution_source=None)
        parsed = _parse_event(
            event,
            datetime(2026, 4, 28, tzinfo=timezone.utc),
            min_hours=0.0,
        )

        assert parsed is not None
        assert parsed["source_contract"]["status"] == "MISSING"

        monkeypatch.setattr(ms, "_get_active_events", lambda: [event])

        assert ms.find_weather_markets(min_hours_to_resolution=0.0) == []

    def test_watch_report_alerts_on_source_drift(self):
        from scripts.watch_source_contract import analyze_events, exit_code_for_report

        event = _gamma_temperature_event(
            title="Highest temperature in Paris on April 29?",
            slug="highest-temperature-in-paris-on-april-29-2026",
            question="Will the high temperature in Paris be 20°C or higher?",
            resolution_source=(
                "https://www.wunderground.com/history/daily/fr/"
                "bonneuil-en-france/LFPB"
            ),
        )

        report = analyze_events([event], checked_at_utc=datetime(2026, 4, 29, tzinfo=timezone.utc))

        assert report["status"] == "ALERT"
        assert report["summary"]["ALERT"] == 1
        assert report["events"][0]["city"] == "Paris"
        assert report["events"][0]["source_contract"]["station_id"] == "LFPB"
        assert exit_code_for_report(report, fail_on="WARN") == 2

    def test_watch_report_warns_on_missing_source(self):
        from scripts.watch_source_contract import analyze_events, exit_code_for_report

        event = _gamma_temperature_event(resolution_source=None)

        report = analyze_events([event], checked_at_utc=datetime(2026, 4, 29, tzinfo=timezone.utc))

        assert report["status"] == "WARN"
        assert report["summary"]["WARN"] == 1
        assert report["events"][0]["source_contract"]["status"] == "MISSING"
        assert exit_code_for_report(report, fail_on="WARN") == 1
        assert exit_code_for_report(report, fail_on="ALERT") == 0

    def test_watch_alert_persists_city_quarantine_and_blocks_new_entries(
        self, monkeypatch, tmp_path
    ):
        from scripts.watch_source_contract import analyze_events, apply_source_quarantines

        quarantine_path = tmp_path / "source_contract_quarantine.json"
        monkeypatch.setenv(ms.SOURCE_CONTRACT_QUARANTINE_PATH_ENV, str(quarantine_path))
        drift_event = _gamma_temperature_event(
            title="Highest temperature in Paris on April 29?",
            slug="highest-temperature-in-paris-on-april-29-2026",
            question="Will the high temperature in Paris be 20°C or higher?",
            resolution_source=(
                "https://www.wunderground.com/history/daily/fr/"
                "bonneuil-en-france/LFPB"
            ),
        )
        report = analyze_events(
            [drift_event],
            checked_at_utc=datetime(2026, 4, 29, tzinfo=timezone.utc),
        )

        actions = apply_source_quarantines(
            report,
            quarantine_path=quarantine_path,
            observed_at="2026-04-29T00:00:00+00:00",
        )

        assert actions == [
            {
                "action": "quarantine_city_source",
                "status": "written",
                "city": "Paris",
                "path": str(quarantine_path),
                "event_ids": ["event1"],
            }
        ]
        assert ms.is_city_source_quarantined("Paris", path=quarantine_path) is True

        matching_event_after_reconfig = _gamma_temperature_event(
            title="Highest temperature in Paris on April 30?",
            slug="highest-temperature-in-paris-on-april-30-2026",
            question="Will the high temperature in Paris be 21°C or higher?",
            resolution_source=(
                "https://www.wunderground.com/history/daily/fr/"
                "paris/LFPG"
            ),
        )
        monkeypatch.setattr(ms, "_get_active_events", lambda: [matching_event_after_reconfig])

        assert ms.find_weather_markets(min_hours_to_resolution=0.0) == []

    def test_source_quarantine_does_not_block_existing_position_price_paths(
        self, monkeypatch, tmp_path
    ):
        quarantine_path = tmp_path / "source_contract_quarantine.json"
        monkeypatch.setenv(ms.SOURCE_CONTRACT_QUARANTINE_PATH_ENV, str(quarantine_path))
        ms.upsert_source_contract_quarantine(
            "Paris",
            reason="source_contract_mismatch",
            evidence={"events": []},
            observed_at="2026-04-29T00:00:00+00:00",
            path=quarantine_path,
        )
        active_event = _gamma_temperature_event(
            market_id="paris-existing-market",
            title="Highest temperature in Paris on April 29?",
            slug="highest-temperature-in-paris-on-april-29-2026",
            question="Will the high temperature in Paris be 20°C or higher?",
            resolution_source=(
                "https://www.wunderground.com/history/daily/fr/"
                "bonneuil-en-france/LFPB"
            ),
        )
        monkeypatch.setattr(ms, "_get_active_events", lambda: [active_event])

        assert ms.get_current_yes_price("cond1") == pytest.approx(0.55)
        siblings = ms.get_sibling_outcomes("cond1")
        assert len(siblings) == 3
        held = next(outcome for outcome in siblings if outcome["market_id"] == "cond1")
        assert held["token_id"] == "token_yes_primary"
        assert held["no_token_id"] == "token_no_primary"

    def test_source_quarantine_release_requires_conversion_evidence_refs(self, tmp_path):
        quarantine_path = tmp_path / "source_contract_quarantine.json"
        ms.upsert_source_contract_quarantine(
            "Paris",
            reason="source_contract_mismatch",
            evidence={"event_ids": ["event1"]},
            observed_at="2026-04-29T00:00:00+00:00",
            path=quarantine_path,
        )

        blocked = ms.release_source_contract_quarantine(
            "Paris",
            released_by="operator",
            evidence={"config_updated": True},
            released_at="2026-04-29T01:00:00+00:00",
            path=quarantine_path,
        )

        assert blocked["status"] == "blocked"
        assert blocked["missing_evidence"] == [
            "config_updated:evidence_ref",
            "source_validity_updated",
            "backfill_completed",
            "settlements_rebuilt",
            "calibration_rebuilt",
            "verification_passed",
        ]
        assert ms.is_city_source_quarantined("Paris", path=quarantine_path) is True

        release_evidence = _complete_release_evidence()
        released = ms.release_source_contract_quarantine(
            "Paris",
            released_by="operator",
            evidence=release_evidence,
            released_at="2026-04-29T02:00:00+00:00",
            path=quarantine_path,
        )

        assert released["status"] == "released"
        assert released["entry"]["release_evidence"] == release_evidence
        assert released["transition_record"]["city"] == "Paris"
        assert ms.is_city_source_quarantined("Paris", path=quarantine_path) is False

    def test_release_records_source_transition_history(self, tmp_path, capsys):
        from scripts.watch_source_contract import (
            analyze_events,
            apply_source_quarantines,
            build_history_report,
            main as watch_source_contract_main,
            render_history_report,
        )

        quarantine_path = tmp_path / "source_contract_quarantine.json"
        drift_event = _gamma_temperature_event(
            title="Highest temperature in Paris on April 29?",
            slug="highest-temperature-in-paris-on-april-29-2026",
            question="Will the high temperature in Paris be 20°C or higher?",
            resolution_source=(
                "https://www.wunderground.com/history/daily/fr/"
                "bonneuil-en-france/LFPB"
            ),
        )
        report = analyze_events(
            [drift_event],
            checked_at_utc=datetime(2026, 4, 29, tzinfo=timezone.utc),
        )
        apply_source_quarantines(
            report,
            quarantine_path=quarantine_path,
            observed_at="2026-04-29T00:00:00+00:00",
        )
        release_evidence = _complete_release_evidence(
            "docs/operations/source_transition/paris_2026-04-29"
        )

        released = ms.release_source_contract_quarantine(
            "Paris",
            released_by="operator",
            evidence=release_evidence,
            released_at="2026-04-29T02:00:00+00:00",
            path=quarantine_path,
        )

        assert released["status"] == "released"
        record = released["transition_record"]
        assert record["city"] == "Paris"
        assert record["transition_branch"] == "same_provider_station_change"
        assert record["detected_at"] == "2026-04-29T00:00:00+00:00"
        assert record["released_at"] == "2026-04-29T02:00:00+00:00"
        assert record["affected_target_dates"] == ["2026-04-29"]
        assert record["event_ids"] == ["event1"]
        assert record["from_source_contract"] == {
            "source_families": ["wu_icao"],
            "station_ids": ["LFPG"],
        }
        assert record["to_source_contract"]["source_families"] == ["wu_icao"]
        assert record["to_source_contract"]["station_ids"] == ["LFPB"]
        assert record["to_source_contract"]["resolution_sources"] == [
            "https://www.wunderground.com/history/daily/fr/bonneuil-en-france/LFPB"
        ]
        for key in ms.REQUIRED_SOURCE_CONVERSION_EVIDENCE:
            assert record["completed_release_evidence"][key] == {
                "completed": True,
                "evidence_ref": release_evidence["evidence_refs"][key],
            }

        history = ms.source_contract_transition_history("Paris", path=quarantine_path)
        assert history == [record]
        history_report = build_history_report("Paris", quarantine_path=quarantine_path)
        assert history_report["record_count"] == 1
        assert history_report["history"] == [record]
        text = render_history_report(history_report)
        assert "source-contract-transition-history city=Paris records=1" in text
        assert "branch=same_provider_station_change" in text
        assert "to=['wu_icao']/['LFPB']" in text

        exit_code = watch_source_contract_main(
            [
                "--history",
                "Paris",
                "--json",
                "--quarantine-path",
                str(quarantine_path),
            ]
        )
        cli_report = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert cli_report["record_count"] == 1
        assert cli_report["history"][0]["to_source_contract"]["station_ids"] == ["LFPB"]

    def test_requarantine_after_release_starts_new_detection_window(self, tmp_path):
        quarantine_path = tmp_path / "source_contract_quarantine.json"
        ms.upsert_source_contract_quarantine(
            "Paris",
            reason="source_contract_mismatch",
            evidence={"events": []},
            observed_at="2026-04-29T00:00:00+00:00",
            path=quarantine_path,
        )
        released = ms.release_source_contract_quarantine(
            "Paris",
            released_by="operator",
            evidence=_complete_release_evidence(),
            released_at="2026-04-29T02:00:00+00:00",
            path=quarantine_path,
        )
        assert released["status"] == "released"

        ms.upsert_source_contract_quarantine(
            "Paris",
            reason="source_contract_mismatch",
            evidence={"events": []},
            observed_at="2026-05-02T00:00:00+00:00",
            path=quarantine_path,
        )

        active = ms.active_source_contract_quarantines(path=quarantine_path)
        assert active["Paris"]["first_seen_at"] == "2026-05-02T00:00:00+00:00"
        assert active["Paris"]["last_seen_at"] == "2026-05-02T00:00:00+00:00"
        history = ms.source_contract_transition_history("Paris", path=quarantine_path)
        assert len(history) == 1
        assert history[0]["released_at"] == "2026-04-29T02:00:00+00:00"

    def test_conversion_plan_classifies_same_provider_station_change(self, tmp_path):
        from scripts.watch_source_contract import (
            analyze_events,
            apply_source_quarantines,
            build_conversion_plan,
        )

        quarantine_path = tmp_path / "source_contract_quarantine.json"
        drift_event = _gamma_temperature_event(
            title="Highest temperature in Paris on April 29?",
            slug="highest-temperature-in-paris-on-april-29-2026",
            question="Will the high temperature in Paris be 20°C or higher?",
            resolution_source=(
                "https://www.wunderground.com/history/daily/fr/"
                "bonneuil-en-france/LFPB"
            ),
        )
        report = analyze_events(
            [drift_event],
            checked_at_utc=datetime(2026, 4, 29, tzinfo=timezone.utc),
        )
        apply_source_quarantines(
            report,
            quarantine_path=quarantine_path,
            observed_at="2026-04-29T00:00:00+00:00",
        )

        plan = build_conversion_plan("Paris", quarantine_path=quarantine_path)

        assert plan["status"] == "active_quarantine"
        assert plan["transition_branch"] == "same_provider_station_change"
        assert plan["release_contract"]["required_evidence"] == list(
            ms.REQUIRED_SOURCE_CONVERSION_EVIDENCE
        )
        assert set(plan["release_contract"]["required_evidence_refs"]) == set(
            ms.REQUIRED_SOURCE_CONVERSION_EVIDENCE
        )

    def test_conversion_plan_classifies_provider_family_change(self, tmp_path):
        from scripts.watch_source_contract import (
            analyze_events,
            apply_source_quarantines,
            build_conversion_plan,
        )

        quarantine_path = tmp_path / "source_contract_quarantine.json"
        provider_change_event = _gamma_temperature_event(
            resolution_source="https://api.weather.gov/stations/KLAX/observations/latest"
        )
        report = analyze_events(
            [provider_change_event],
            checked_at_utc=datetime(2026, 4, 29, tzinfo=timezone.utc),
        )
        apply_source_quarantines(
            report,
            quarantine_path=quarantine_path,
            observed_at="2026-04-29T00:00:00+00:00",
        )

        plan = build_conversion_plan("Los Angeles", quarantine_path=quarantine_path)

        assert plan["status"] == "active_quarantine"
        assert plan["transition_branch"] == "provider_family_change_requires_new_source_role"
        assert plan["quarantine_entry"]["evidence"]["events"][0]["source_contract"][
            "source_family"
        ] == "noaa"
        assert plan["quarantine_entry"]["evidence"]["events"][0]["source_contract"][
            "configured_source_family"
        ] == "wu_icao"

    def test_conversion_plan_classifies_unsupported_source(self, tmp_path):
        from scripts.watch_source_contract import (
            analyze_events,
            apply_source_quarantines,
            build_conversion_plan,
        )

        quarantine_path = tmp_path / "source_contract_quarantine.json"
        unsupported_event = _gamma_temperature_event(
            resolution_source="https://unsupported.example/weather/KLAX"
        )
        report = analyze_events(
            [unsupported_event],
            checked_at_utc=datetime(2026, 4, 29, tzinfo=timezone.utc),
        )
        apply_source_quarantines(
            report,
            quarantine_path=quarantine_path,
            observed_at="2026-04-29T00:00:00+00:00",
        )

        plan = build_conversion_plan("Los Angeles", quarantine_path=quarantine_path)

        assert plan["status"] == "active_quarantine"
        assert (
            plan["transition_branch"]
            == "unsupported_source_requires_manual_provider_adapter_review"
        )
        assert plan["quarantine_entry"]["evidence"]["events"][0]["source_contract"][
            "status"
        ] == "UNSUPPORTED"

    def test_auto_convert_plans_paris_same_provider_station_change(self, tmp_path):
        from scripts import source_contract_auto_convert as auto
        from scripts.watch_source_contract import analyze_events

        events = [
            _gamma_temperature_event(
                event_id="paris-high-20260429",
                title="Highest temperature in Paris on April 29?",
                slug="highest-temperature-in-paris-on-april-29-2026",
                question="Will the high temperature in Paris be 20°C or higher?",
                resolution_source=(
                    "https://www.wunderground.com/history/daily/fr/"
                    "bonneuil-en-france/LFPB"
                ),
            ),
            _gamma_temperature_event(
                event_id="paris-low-20260501",
                title="Lowest temperature in Paris on May 1?",
                slug="lowest-temperature-in-paris-on-may-1-2026",
                question="Will the low temperature in Paris be 10°C or lower?",
                resolution_source=(
                    "https://www.wunderground.com/history/daily/fr/"
                    "bonneuil-en-france/LFPB"
                ),
            ),
        ]
        report = analyze_events(
            events,
            checked_at_utc=datetime(2026, 4, 30, tzinfo=timezone.utc),
        )

        receipt = auto.build_receipt(
            report,
            policy=auto.RuntimePolicy(
                history_days=1095,
                min_alert_markets=2,
                min_target_dates=1,
                today=auto.date(2026, 4, 30),
            ),
            run_id="test-run",
            quarantine_actions=[],
        )

        assert receipt["status"] == "planned"
        candidate = receipt["candidates"][0]
        assert candidate["city"] == "Paris"
        assert candidate["transition_branch"] == "same_provider_station_change"
        assert candidate["confirmation_status"] == "auto_confirmed"
        assert candidate["source_contract"]["from_station_ids"] == ["LFPG"]
        assert candidate["source_contract"]["to_station_ids"] == ["LFPB"]
        assert candidate["affected_metrics"] == ["high", "low"]
        assert candidate["date_scope"]["affected_market_start"] == "2026-04-29"
        assert candidate["date_scope"]["affected_market_end"] == "2026-05-01"
        assert candidate["date_scope"]["executable_wu_fetch_end"] == "2026-04-28"
        assert candidate["date_scope"]["future_or_recent_dates_not_fetchable_by_wu_history"] == [
            "2026-04-29",
            "2026-05-01",
        ]
        assert len(candidate["runtime_gaps_before_apply"]) == 1
        assert "not fetchable by WU history" in candidate["runtime_gaps_before_apply"][0]
        assert any(
            "not fetchable by WU history" in blocker
            for blocker in auto._candidate_apply_ready(candidate)
        )
        mini_packet = candidate["mini_llm_execution"]
        assert mini_packet["mini_model_can_directly_complete"] is False
        assert mini_packet["current_authority"] == "report_and_dry_run_only"
        assert (
            "Do not mutate production DB truth except through the exact scoped commands in this receipt after DB backup succeeds."
        ) in mini_packet[
            "forbidden_actions"
        ]
        assert mini_packet["evidence_manifest"]["config_updated"][
            "expected_artifact"
        ].endswith("/test-run/paris/config_update.json")
        locator = mini_packet["workspace_locator"]
        assert locator["repo_root"] == str(auto.ROOT)
        assert any(
            item["path"] == "scripts/watch_source_contract.py"
            for item in locator["code_navigation"]
        )
        assert any(
            item["path"] == "config/cities.json"
            and item["access"] == "deterministic_controller_write_only_under_execute_apply"
            for item in locator["code_navigation"]
        )
        safe_contract = mini_packet["safe_execution_contract"]
        assert safe_contract["command_policy"] == "exact_allowed_command_only"
        assert "state/zeus-world.db (only via exact scoped rebuild commands from the receipt)" in safe_contract["allowed_write_globs_current_phase"]
        assert any("--apply/--no-dry-run/--force outside" in token for token in safe_contract["forbidden_command_tokens"])
        assert mini_packet["report_template"] == {
            "city": "Paris",
            "can_complete_remaining_conversion": False,
            "source_quarantine_should_remain_active": True,
            "blocking_reasons": candidate["runtime_gaps_before_apply"],
            "next_safe_action": "write report, keep quarantine active, and request missing deterministic capability",
        }
        controller_apply = next(
            item for item in candidate["command_plan"] if item["id"] == "controller_apply"
        )
        assert controller_apply["command"][:6] == [
            sys.executable,
            "scripts/source_contract_auto_convert.py",
            "--city",
            "Paris",
            "--execute-apply",
            "--force",
        ]
        backfill = next(
            item for item in candidate["command_plan"] if item["id"] == "wu_backfill_dry_run"
        )
        assert backfill["command"] == [
            sys.executable,
            "scripts/backfill_wu_daily_all.py",
            "--cities",
            "Paris",
            "--start-date",
            "2023-04-30",
            "--end-date",
            "2026-04-28",
            "--missing-only",
            "--replace-station-mismatch",
            "--db",
            str(auto.DEFAULT_WORLD_DB_PATH),
            "--dry-run",
        ]
        settlement_apply = next(
            item for item in candidate["command_plan"] if item["id"] == "settlements_rebuild_apply"
        )
        assert "--temperature-metric" in settlement_apply["command"]
        assert "all" in settlement_apply["command"]
        assert "--apply" in settlement_apply["command"]
        platt_apply = next(
            item for item in candidate["command_plan"] if item["id"] == "platt_refit_apply"
        )
        assert "--city" in platt_apply["command"]
        assert "Paris" in platt_apply["command"]
        assert "--start-date" in platt_apply["command"]
        assert "2023-04-30" in platt_apply["command"]
        assert "--end-date" in platt_apply["command"]
        assert "2026-04-28" in platt_apply["command"]
        season_args = [
            platt_apply["command"][idx + 1]
            for idx, token in enumerate(platt_apply["command"])
            if token == "--season"
        ]
        assert set(season_args) == {"DJF", "MAM", "JJA", "SON"}
        assert platt_apply["bucket_scope"]["data_versions"] == "derived_from_scoped_calibration_pairs"

    def test_auto_convert_blocks_single_market_below_threshold(self):
        from scripts import source_contract_auto_convert as auto
        from scripts.watch_source_contract import analyze_events

        event = _gamma_temperature_event(
            title="Highest temperature in Paris on April 29?",
            slug="highest-temperature-in-paris-on-april-29-2026",
            question="Will the high temperature in Paris be 20°C or higher?",
            resolution_source=(
                "https://www.wunderground.com/history/daily/fr/"
                "bonneuil-en-france/LFPB"
            ),
        )
        report = analyze_events(
            [event],
            checked_at_utc=datetime(2026, 4, 30, tzinfo=timezone.utc),
        )

        receipt = auto.build_receipt(
            report,
            policy=auto.RuntimePolicy(today=auto.date(2026, 4, 30)),
        )

        assert receipt["status"] == "blocked"
        candidate = receipt["candidates"][0]
        assert candidate["confirmation_status"] == "manual_review_required"
        assert candidate["command_plan"] == []
        assert candidate["threshold_blockers"] == [
            "alert market count 1 is below threshold 2"
        ]

    def test_auto_convert_blocks_provider_family_change(self):
        from scripts import source_contract_auto_convert as auto
        from scripts.watch_source_contract import analyze_events

        event = _gamma_temperature_event(
            resolution_source="https://api.weather.gov/stations/KLAX/observations/latest"
        )
        report = analyze_events(
            [event],
            checked_at_utc=datetime(2026, 4, 30, tzinfo=timezone.utc),
        )

        receipt = auto.build_receipt(
            report,
            policy=auto.RuntimePolicy(today=auto.date(2026, 4, 30)),
        )

        assert receipt["status"] == "blocked"
        candidate = receipt["candidates"][0]
        assert (
            candidate["transition_branch"]
            == "provider_family_change_requires_new_source_role"
        )
        assert candidate["threshold_blockers"] == [
            "provider family changed; a new source-role adapter/config path is required before automation can continue"
        ]

    def test_auto_convert_receipt_persistence_and_discord_required_exit(
        self, monkeypatch, tmp_path, capsys
    ):
        from scripts import source_contract_auto_convert as auto

        fixture = tmp_path / "events.json"
        fixture.write_text(
            json.dumps(
                [
                    _gamma_temperature_event(
                        event_id="paris-high-20260429",
                        title="Highest temperature in Paris on April 29?",
                        slug="highest-temperature-in-paris-on-april-29-2026",
                        question="Will the high temperature in Paris be 20°C or higher?",
                        resolution_source=(
                            "https://www.wunderground.com/history/daily/fr/"
                            "bonneuil-en-france/LFPB"
                        ),
                    ),
                    _gamma_temperature_event(
                        event_id="paris-high-20260430",
                        title="Highest temperature in Paris on April 30?",
                        slug="highest-temperature-in-paris-on-april-30-2026",
                        question="Will the high temperature in Paris be 21°C or higher?",
                        resolution_source=(
                            "https://www.wunderground.com/history/daily/fr/"
                            "bonneuil-en-france/LFPB"
                        ),
                    ),
                ]
            )
        )
        monkeypatch.setattr(
            auto,
            "send_discord_notification",
            lambda receipt, notify_noop=False: {
                "attempted": True,
                "sent": False,
                "status": "skipped_no_webhook",
            },
        )

        exit_code = auto.main(
            [
                "--fixture",
                str(fixture),
                "--receipt-dir",
                str(tmp_path / "receipts"),
                "--lock-path",
                str(tmp_path / "source_auto.lock"),
                "--quarantine-path",
                str(tmp_path / "quarantine.json"),
                "--run-id",
                "cron-run",
                "--today",
                "2026-04-30",
                "--discord",
                "--discord-required",
                "--write-mini-report",
                "--json",
            ]
        )

        assert exit_code == 2
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "planned"
        assert output["cron_lock"]["status"] == "held"
        assert output["cron_lock"]["path"] == str(tmp_path / "source_auto.lock")
        assert output["notification"]["status"] == "skipped_no_webhook"
        controller_step = next(
            step
            for step in output["candidates"][0]["mini_llm_execution"]["step_protocol"]
            if step["id"] == "execute_apply_controller"
        )
        assert "--fixture" in controller_step["allowed_command"]
        assert str(fixture) in controller_step["allowed_command"]
        assert "--quarantine-path" in controller_step["allowed_command"]
        receipt_path = tmp_path / "receipts" / "cron-run.json"
        latest_path = tmp_path / "receipts" / "latest.json"
        report_path = tmp_path / "receipts" / "cron-run.mini_report.md"
        assert json.loads(receipt_path.read_text())["run_id"] == "cron-run"
        assert json.loads(latest_path.read_text())["run_id"] == "cron-run"
        assert report_path.exists()
        report_text = report_path.read_text()
        assert "can_complete_remaining_conversion: `False`" in report_text
        assert "not fetchable by WU history" in report_text
        assert "exact scoped commands in this receipt" in report_text
        assert "`scripts/watch_source_contract.py`" in report_text
        assert "allowed: `state/zeus-world.db (only via exact scoped rebuild commands from the receipt)`" in report_text

    def test_auto_convert_fixture_apply_refuses_default_write_surfaces(
        self, tmp_path, capsys
    ):
        from scripts import source_contract_auto_convert as auto

        fixture = tmp_path / "events.json"
        fixture.write_text(
            json.dumps(
                [
                    _gamma_temperature_event(
                        event_id="paris-high-20260429",
                        title="Highest temperature in Paris on April 29?",
                        slug="highest-temperature-in-paris-on-april-29-2026",
                        question="Will the high temperature in Paris be 20°C or higher?",
                        resolution_source=(
                            "https://www.wunderground.com/history/daily/fr/"
                            "bonneuil-en-france/LFPB"
                        ),
                    ),
                    _gamma_temperature_event(
                        event_id="paris-low-20260501",
                        title="Lowest temperature in Paris on May 1?",
                        slug="lowest-temperature-in-paris-on-may-1-2026",
                        question="Will the low temperature in Paris be 10°C or lower?",
                        resolution_source=(
                            "https://www.wunderground.com/history/daily/fr/"
                            "bonneuil-en-france/LFPB"
                        ),
                    ),
                ]
            ),
            encoding="utf-8",
        )

        exit_code = auto.main(
            [
                "--fixture",
                str(fixture),
                "--receipt-dir",
                str(tmp_path / "receipts"),
                "--lock-path",
                str(tmp_path / "source_auto.lock"),
                "--quarantine-path",
                str(tmp_path / "quarantine.json"),
                "--run-id",
                "fixture-prod-block",
                "--today",
                "2026-05-03",
                "--execute-apply",
                "--force",
                "--json",
            ]
        )

        assert exit_code == 2
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "failed"
        assert "fixture-backed release" in output["error"]
        assert "default world DB" in output["error"]
        assert "default city config" in output["error"]

    def test_auto_convert_execute_apply_writes_evidence_and_releases_quarantine(
        self, monkeypatch, tmp_path, capsys
    ):
        from scripts import source_contract_auto_convert as auto

        fixture = tmp_path / "events.json"
        fixture.write_text(
            json.dumps(
                [
                    _gamma_temperature_event(
                        event_id="paris-high-20260429",
                        title="Highest temperature in Paris on April 29?",
                        slug="highest-temperature-in-paris-on-april-29-2026",
                        question="Will the high temperature in Paris be 20°C or higher?",
                        resolution_source=(
                            "https://www.wunderground.com/history/daily/fr/"
                            "bonneuil-en-france/LFPB"
                        ),
                    ),
                    _gamma_temperature_event(
                        event_id="paris-low-20260501",
                        title="Lowest temperature in Paris on May 1?",
                        slug="lowest-temperature-in-paris-on-may-1-2026",
                        question="Will the low temperature in Paris be 10°C or lower?",
                        resolution_source=(
                            "https://www.wunderground.com/history/daily/fr/"
                            "bonneuil-en-france/LFPB"
                        ),
                    ),
                ]
            )
        )
        config_path = tmp_path / "cities.json"
        config_path.write_text(auto.DEFAULT_CITY_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        source_validity_path = tmp_path / "current_source_validity.md"
        source_validity_path.write_text("# Current Source Validity\n", encoding="utf-8")
        db_path = tmp_path / "zeus-world.db"
        db_path.write_bytes(b"sqlite placeholder")
        quarantine_path = tmp_path / "quarantine.json"
        receipts_dir = tmp_path / "receipts"
        evidence_base = tmp_path / "evidence"
        commands: list[list[str]] = []

        def _fake_run_command(command, *, cwd, artifact_path):
            commands.append([str(part) for part in command])
            receipt = {
                "command": [str(part) for part in command],
                "cwd": str(cwd),
                "returncode": 0,
                "stdout": "ok",
                "stderr": "",
            }
            if "scripts/watch_source_contract.py" in command:
                receipt["stdout"] = json.dumps(
                    {
                        "status": "OK",
                        "authority": "FIXTURE",
                        "events": [],
                        "summary": {"OK": 2, "WARN": 0, "ALERT": 0, "DATA_UNAVAILABLE": 0},
                    }
                )
            auto._write_json_atomic(artifact_path, receipt)
            return receipt

        monkeypatch.setattr(auto, "_run_command", _fake_run_command)

        exit_code = auto.main(
            [
                "--fixture",
                str(fixture),
                "--receipt-dir",
                str(receipts_dir),
                "--lock-path",
                str(tmp_path / "source_auto.lock"),
                "--quarantine-path",
                str(quarantine_path),
                "--run-id",
                "apply-run",
                "--today",
                "2026-05-03",
                "--execute-apply",
                "--force",
                "--no-station-metadata-network",
                "--config-path",
                str(config_path),
                "--source-validity-path",
                str(source_validity_path),
                "--db",
                str(db_path),
                "--evidence-root-base",
                str(evidence_base),
                "--json",
            ]
        )

        assert exit_code == 0
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "applied"
        candidate = output["candidates"][0]
        assert candidate["apply_status"] == "applied"
        assert candidate["release_ready"] is True
        assert candidate["release_result"]["status"] == "released"
        evidence_refs = candidate["release_evidence"]["evidence_refs"]
        assert set(evidence_refs) == set(ms.REQUIRED_SOURCE_CONVERSION_EVIDENCE)
        for ref in evidence_refs.values():
            assert ref.startswith(str(evidence_base / "apply-run" / "paris"))

        city_rows = json.loads(config_path.read_text(encoding="utf-8"))["cities"]
        paris = next(row for row in city_rows if row["name"] == "Paris")
        assert paris["wu_station"] == "LFPB"
        assert paris["settlement_source_type"] == "wu_icao"
        assert paris["settlement_source"].endswith("/LFPB")
        assert paris["airport_name"] == "Paris-Le Bourget Airport"
        assert paris["lat"] == pytest.approx(48.969398)
        assert paris["lon"] == pytest.approx(2.44139)
        assert paris["wu_pws"] is None

        history = ms.source_contract_transition_history("Paris", path=quarantine_path)
        assert len(history) == 1
        assert history[0]["to_source_contract"]["station_ids"] == ["LFPB"]
        assert ms.is_city_source_quarantined("Paris", path=quarantine_path) is False
        assert "Source Auto-Conversion Applied: Paris" in source_validity_path.read_text(encoding="utf-8")
        assert (evidence_base / "apply-run" / "paris" / "db_backup.json").exists()
        assert any("scripts/backfill_wu_daily_all.py" in cmd for command in commands for cmd in command)
        assert any("--apply" in command for command in commands)
        assert any("--no-dry-run" in command for command in commands)

    def test_auto_convert_execute_apply_rolls_back_config_and_source_fact_on_failure(
        self, monkeypatch, tmp_path, capsys
    ):
        from scripts import source_contract_auto_convert as auto

        fixture = tmp_path / "events.json"
        fixture.write_text(
            json.dumps(
                [
                    _gamma_temperature_event(
                        event_id="paris-high-20260429",
                        title="Highest temperature in Paris on April 29?",
                        slug="highest-temperature-in-paris-on-april-29-2026",
                        question="Will the high temperature in Paris be 20°C or higher?",
                        resolution_source=(
                            "https://www.wunderground.com/history/daily/fr/"
                            "bonneuil-en-france/LFPB"
                        ),
                    ),
                    _gamma_temperature_event(
                        event_id="paris-low-20260501",
                        title="Lowest temperature in Paris on May 1?",
                        slug="lowest-temperature-in-paris-on-may-1-2026",
                        question="Will the low temperature in Paris be 10°C or lower?",
                        resolution_source=(
                            "https://www.wunderground.com/history/daily/fr/"
                            "bonneuil-en-france/LFPB"
                        ),
                    ),
                ]
            )
        )
        config_path = tmp_path / "cities.json"
        original_config = auto.DEFAULT_CITY_CONFIG_PATH.read_bytes()
        config_path.write_bytes(original_config)
        source_validity_path = tmp_path / "current_source_validity.md"
        original_source_validity = b"# Current Source Validity\n"
        source_validity_path.write_bytes(original_source_validity)
        db_path = tmp_path / "zeus-world.db"
        db_path.write_bytes(b"sqlite placeholder")
        quarantine_path = tmp_path / "quarantine.json"
        evidence_base = tmp_path / "evidence"

        def _fail_run_command(command, *, cwd, artifact_path):
            raise RuntimeError("synthetic downstream failure")

        monkeypatch.setattr(auto, "_run_command", _fail_run_command)

        exit_code = auto.main(
            [
                "--fixture",
                str(fixture),
                "--receipt-dir",
                str(tmp_path / "receipts"),
                "--lock-path",
                str(tmp_path / "source_auto.lock"),
                "--quarantine-path",
                str(quarantine_path),
                "--run-id",
                "rollback-run",
                "--today",
                "2026-05-03",
                "--execute-apply",
                "--force",
                "--no-station-metadata-network",
                "--config-path",
                str(config_path),
                "--source-validity-path",
                str(source_validity_path),
                "--db",
                str(db_path),
                "--evidence-root-base",
                str(evidence_base),
                "--json",
            ]
        )

        assert exit_code == 2
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "failed"
        candidate = output["candidates"][0]
        assert candidate["apply_status"] == "failed"
        assert "synthetic downstream failure" in candidate["apply_error"]
        assert config_path.read_bytes() == original_config
        assert source_validity_path.read_bytes() == original_source_validity
        rollback_path = evidence_base / "rollback-run" / "paris" / "rollback_manifest.json"
        assert rollback_path.exists()
        rollback = json.loads(rollback_path.read_text(encoding="utf-8"))
        assert rollback["status"] == "complete"
        assert {item["status"] for item in rollback["restored"]} == {"restored"}

    def test_platt_refit_derives_exact_bucket_keys_from_city_date_scope(self):
        from scripts import refit_platt_v2
        from src.types.metric_identity import HIGH_LOCALDAY_MAX

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE calibration_pairs_v2 (
                temperature_metric TEXT,
                training_allowed INTEGER,
                authority TEXT,
                decision_group_id TEXT,
                p_raw REAL,
                city TEXT,
                target_date TEXT,
                cluster TEXT,
                season TEXT,
                data_version TEXT
            )
            """
        )

        def insert_bucket(*, city: str, target_date: str, season: str, data_version: str) -> None:
            for idx in range(refit_platt_v2.MIN_DECISION_GROUPS):
                conn.execute(
                    """
                    INSERT INTO calibration_pairs_v2 (
                        temperature_metric, training_allowed, authority,
                        decision_group_id, p_raw, city, target_date,
                        cluster, season, data_version
                    ) VALUES ('high', 1, 'VERIFIED', ?, 0.5, ?, ?, 'Europe', ?, ?)
                    """,
                    (f"{city}-{target_date}-{season}-{data_version}-{idx}", city, target_date, season, data_version),
                )

        insert_bucket(city="Paris", target_date="2026-04-28", season="MAM", data_version="affected_v1")
        insert_bucket(city="London", target_date="2026-04-28", season="MAM", data_version="unaffected_same_season")
        insert_bucket(city="Paris", target_date="2026-01-15", season="DJF", data_version="outside_window")

        rows = refit_platt_v2._fetch_buckets(
            conn,
            HIGH_LOCALDAY_MAX,
            city_filter="Paris",
            start_date="2026-04-28",
            end_date="2026-04-28",
            cluster_filter="Europe",
            season_filter=["MAM"],
        )

        assert [(row["season"], row["data_version"]) for row in rows] == [
            ("MAM", "affected_v1")
        ]

    def test_venus_sensing_report_source_watch_persists_quarantine(
        self, monkeypatch, tmp_path
    ):
        from scripts import venus_sensing_report
        from scripts import watch_source_contract

        quarantine_path = tmp_path / "source_contract_quarantine.json"
        monkeypatch.setenv(ms.SOURCE_CONTRACT_QUARANTINE_PATH_ENV, str(quarantine_path))
        monkeypatch.delenv(venus_sensing_report.SOURCE_WATCH_REPORT_ONLY_ENV, raising=False)
        drift_event = _gamma_temperature_event(
            title="Highest temperature in Paris on April 29?",
            slug="highest-temperature-in-paris-on-april-29-2026",
            question="Will the high temperature in Paris be 20°C or higher?",
            resolution_source=(
                "https://www.wunderground.com/history/daily/fr/"
                "bonneuil-en-france/LFPB"
            ),
        )
        monkeypatch.setattr(
            watch_source_contract,
            "fetch_active_events",
            lambda: ([drift_event], "VERIFIED"),
        )

        report = venus_sensing_report._collect_source_contract_watch()

        assert report["status"] == "ALERT"
        assert report["quarantine_actions"] == [
            {
                "action": "quarantine_city_source",
                "status": "written",
                "city": "Paris",
                "path": str(quarantine_path),
                "event_ids": ["event1"],
            }
        ]
        assert ms.is_city_source_quarantined("Paris", path=quarantine_path) is True

    def test_venus_sensing_report_source_watch_report_only_does_not_write(
        self, monkeypatch, tmp_path
    ):
        from scripts import venus_sensing_report
        from scripts import watch_source_contract

        quarantine_path = tmp_path / "source_contract_quarantine.json"
        monkeypatch.setenv(ms.SOURCE_CONTRACT_QUARANTINE_PATH_ENV, str(quarantine_path))
        monkeypatch.setenv(venus_sensing_report.SOURCE_WATCH_REPORT_ONLY_ENV, "1")
        drift_event = _gamma_temperature_event(
            title="Highest temperature in Paris on April 29?",
            slug="highest-temperature-in-paris-on-april-29-2026",
            question="Will the high temperature in Paris be 20°C or higher?",
            resolution_source=(
                "https://www.wunderground.com/history/daily/fr/"
                "bonneuil-en-france/LFPB"
            ),
        )
        monkeypatch.setattr(
            watch_source_contract,
            "fetch_active_events",
            lambda: ([drift_event], "VERIFIED"),
        )

        report = venus_sensing_report._collect_source_contract_watch()

        assert report["status"] == "ALERT"
        assert report["quarantine_actions"] == []
        assert quarantine_path.exists() is False

    def test_venus_sensing_report_preserves_alert_when_quarantine_write_fails(
        self, monkeypatch
    ):
        from scripts import venus_sensing_report
        from scripts import watch_source_contract

        monkeypatch.delenv(venus_sensing_report.SOURCE_WATCH_REPORT_ONLY_ENV, raising=False)
        drift_event = _gamma_temperature_event(
            title="Highest temperature in Paris on April 29?",
            slug="highest-temperature-in-paris-on-april-29-2026",
            question="Will the high temperature in Paris be 20°C or higher?",
            resolution_source=(
                "https://www.wunderground.com/history/daily/fr/"
                "bonneuil-en-france/LFPB"
            ),
        )
        monkeypatch.setattr(
            watch_source_contract,
            "fetch_active_events",
            lambda: ([drift_event], "VERIFIED"),
        )

        def _raise(_report):
            raise OSError("cannot write quarantine")

        monkeypatch.setattr(watch_source_contract, "apply_source_quarantines", _raise)

        report = venus_sensing_report._collect_source_contract_watch()

        assert report["status"] == "ALERT"
        assert report["summary"]["ALERT"] == 1
        assert report["quarantine_actions"] == [
            {"action": "quarantine_city_source", "status": "error"}
        ]
        assert report["quarantine_error"] == "cannot write quarantine"


class TestForwardMarketSubstrateProducer:
    """Forward substrate writer is explicit, authority-gated, and idempotent."""

    def test_forward_substrate_writes_verified_scanner_rows_without_unblocking_economics(
        self, monkeypatch
    ):
        """Verified Gamma scanner facts populate only market/price substrate."""
        monkeypatch.setattr(
            state_db,
            "get_connection",
            lambda *_a, **_kw: pytest.fail("writer must not open a default DB"),
        )
        conn = _make_forward_substrate_conn()

        result = log_forward_market_substrate(
            conn,
            markets=[_forward_market()],
            recorded_at="2026-04-29T16:00:00Z",
            scan_authority="VERIFIED",
        )

        assert result["status"] == "written"
        assert result["market_events_inserted"] == 2
        assert result["price_rows_inserted"] == 4
        event_rows = conn.execute(
            """
            SELECT market_slug, city, target_date, temperature_metric,
                   condition_id, token_id, range_label, range_low, range_high,
                   outcome
            FROM market_events_v2
            ORDER BY condition_id
            """
        ).fetchall()
        assert len(event_rows) == 2
        assert {row["temperature_metric"] for row in event_rows} == {"low"}
        assert all(row["outcome"] is None for row in event_rows)
        shoulder = [row for row in event_rows if row["condition_id"] == "cond-low-shoulder"][0]
        assert shoulder["range_low"] is None
        assert shoulder["range_high"] == 35.0
        assert conn.execute("SELECT COUNT(*) FROM market_price_history").fetchone()[0] == 4
        price_linkage = {
            row["market_price_linkage"]
            for row in conn.execute("SELECT market_price_linkage FROM market_price_history")
        }
        assert price_linkage == {"price_only"}

        readiness = check_economics_readiness(conn)
        assert readiness.ready is False
        assert "empty_table:market_events_v2" not in readiness.blockers
        assert "empty_table:market_price_history" not in readiness.blockers
        assert "no_full_market_price_linkage_rows" in readiness.blockers
        assert "missing_table:venue_trade_facts" in readiness.blockers
        assert "no_market_event_outcomes" in readiness.blockers
        assert "economics_engine_not_implemented" in readiness.blockers

    def test_executable_snapshot_price_linkage_writes_full_clob_row_without_unblocking_engine(
        self, monkeypatch
    ):
        """Executable snapshot top-of-book facts become full-linkage substrate."""
        monkeypatch.setattr(
            state_db,
            "get_connection",
            lambda *_a, **_kw: pytest.fail("writer must not open a default DB"),
        )
        conn = _make_full_linkage_conn()
        _insert_full_linkage_snapshot(conn)

        result = log_executable_snapshot_market_price_linkage(
            conn,
            snapshot_id="snap-full-linkage",
        )

        assert result["status"] == "inserted"
        row = conn.execute(
            """
            SELECT market_slug, token_id, price, market_price_linkage, source,
                   best_bid, best_ask, raw_orderbook_hash, snapshot_id,
                   condition_id
            FROM market_price_history
            WHERE snapshot_id = 'snap-full-linkage'
            """
        ).fetchone()
        assert row["market_slug"] == "highest-temperature-in-chicago-on-april-30-2026"
        assert row["token_id"] == "yes-full-linkage"
        assert row["price"] == pytest.approx(0.43)
        assert row["market_price_linkage"] == "full"
        assert row["source"] == "CLOB_ORDERBOOK"
        assert row["best_bid"] == pytest.approx(0.42)
        assert row["best_ask"] == pytest.approx(0.44)
        assert row["raw_orderbook_hash"] == "c" * 64
        assert row["condition_id"] == "cond-full-linkage"
        readiness = check_economics_readiness(conn)
        assert "no_full_market_price_linkage_rows" not in readiness.blockers
        assert "economics_engine_not_implemented" in readiness.blockers

    def test_executable_snapshot_price_linkage_is_idempotent_and_does_not_overwrite_conflicts(
        self,
    ):
        """Full-linkage writer is point-in-time and conflict-reporting."""
        conn = _make_full_linkage_conn()
        _insert_full_linkage_snapshot(conn)
        first = log_executable_snapshot_market_price_linkage(
            conn,
            snapshot_id="snap-full-linkage",
            recorded_at="2026-04-30T16:00:00+00:00",
        )
        second = log_executable_snapshot_market_price_linkage(
            conn,
            snapshot_id="snap-full-linkage",
            recorded_at="2026-04-30T16:00:00+00:00",
        )

        assert first["status"] == "inserted"
        assert second["status"] == "unchanged"

        _insert_full_linkage_snapshot(
            conn,
            snapshot_id="snap-full-linkage-conflict",
            best_bid=Decimal("0.46"),
            best_ask=Decimal("0.48"),
        )
        conflict = log_executable_snapshot_market_price_linkage(
            conn,
            snapshot_id="snap-full-linkage-conflict",
            recorded_at="2026-04-30T16:00:00+00:00",
        )

        assert conflict["status"] == "conflict"
        stored = conn.execute(
            """
            SELECT COUNT(*), MIN(price), MAX(price)
            FROM market_price_history
            WHERE token_id = 'yes-full-linkage'
            """
        ).fetchone()
        assert stored[0] == 1
        assert stored[1] == pytest.approx(0.43)
        assert stored[2] == pytest.approx(0.43)

    def test_executable_snapshot_price_linkage_refuses_bad_or_absent_snapshot_facts(self):
        """Missing and crossed-orderbook snapshots do not create full-linkage rows."""
        conn = _make_full_linkage_conn()

        missing = log_executable_snapshot_market_price_linkage(
            conn,
            snapshot_id="missing-snapshot",
        )
        assert missing["status"] == "refused_missing_snapshot"

        _insert_full_linkage_snapshot(
            conn,
            snapshot_id="snap-crossed",
            best_bid=Decimal("0.55"),
            best_ask=Decimal("0.44"),
        )
        crossed = log_executable_snapshot_market_price_linkage(
            conn,
            snapshot_id="snap-crossed",
        )

        assert crossed["status"] == "refused_crossed_orderbook"
        assert conn.execute("SELECT COUNT(*) FROM market_price_history").fetchone()[0] == 0

    def test_forward_substrate_skips_when_required_tables_are_absent(self):
        """Capability-absent behavior is fail-loud and does not create tables."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row

        result = log_forward_market_substrate(
            conn,
            markets=[_forward_market()],
            recorded_at="2026-04-29T16:00:00Z",
            scan_authority="VERIFIED",
        )

        assert result["status"] == "skipped_missing_tables"
        assert set(result["missing_tables"]) == {"market_events_v2", "market_price_history"}
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0] == 0

    @pytest.mark.parametrize("authority", ["STALE", "EMPTY_FALLBACK", "", None])
    def test_forward_substrate_refuses_degraded_scan_authority(self, authority):
        """Only a fresh VERIFIED scan can create forward market substrate."""
        conn = _make_forward_substrate_conn()

        result = log_forward_market_substrate(
            conn,
            markets=[_forward_market()],
            recorded_at="2026-04-29T16:00:00Z",
            scan_authority=authority,
        )

        assert result["status"] == "refused_degraded_authority"
        assert conn.execute("SELECT COUNT(*) FROM market_events_v2").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM market_price_history").fetchone()[0] == 0

    def test_forward_substrate_refuses_missing_identity_or_range_facts(self):
        """Missing condition/token/range facts are not inferred from neighbors."""
        conn = _make_forward_substrate_conn()
        market = _forward_market()
        market["outcomes"] = [
            {
                "token_id": "yes-missing-condition",
                "no_token_id": "no-missing-condition",
                "title": "35°F or lower",
                "range_low": None,
                "range_high": 35.0,
                "price": 0.31,
                "no_price": 0.69,
            },
            {
                "condition_id": "cond-missing-range",
                "token_id": "yes-missing-range",
                "no_token_id": "no-missing-range",
                "title": "unparseable range",
                "range_low": None,
                "range_high": None,
                "price": 0.42,
                "no_price": 0.58,
            },
        ]

        result = log_forward_market_substrate(
            conn,
            markets=[market],
            recorded_at="2026-04-29T16:00:00Z",
            scan_authority="VERIFIED",
        )

        assert result["status"] == "skipped_no_valid_rows"
        assert result["outcomes_skipped_missing_facts"] == 2
        assert conn.execute("SELECT COUNT(*) FROM market_events_v2").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM market_price_history").fetchone()[0] == 0

    def test_forward_substrate_is_idempotent_and_does_not_overwrite_conflicts(self):
        """Repeated facts are unchanged; conflicting token-time facts are reported."""
        conn = _make_forward_substrate_conn()
        first = log_forward_market_substrate(
            conn,
            markets=[_forward_market()],
            recorded_at="2026-04-29T16:00:00Z",
            scan_authority="VERIFIED",
        )
        second = log_forward_market_substrate(
            conn,
            markets=[_forward_market()],
            recorded_at="2026-04-29T16:00:00Z",
            scan_authority="VERIFIED",
        )

        assert first["status"] == "written"
        assert second["status"] == "unchanged"
        assert second["market_events_unchanged"] == 2
        assert second["price_rows_unchanged"] == 4
        assert conn.execute("SELECT COUNT(*) FROM market_events_v2").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM market_price_history").fetchone()[0] == 4

        conflicting = _forward_market()
        conflicting["outcomes"][0]["price"] = 0.99
        conflict = log_forward_market_substrate(
            conn,
            markets=[conflicting],
            recorded_at="2026-04-29T16:00:00Z",
            scan_authority="VERIFIED",
        )

        assert conflict["status"] == "written_with_conflicts"
        assert conflict["price_rows_conflicted"] == 1
        stored_price = conn.execute(
            """
            SELECT price
            FROM market_price_history
            WHERE token_id = 'yes-low-shoulder'
              AND recorded_at = '2026-04-29T16:00:00Z'
            """
        ).fetchone()[0]
        assert stored_price == 0.31

    def test_forward_substrate_does_not_append_prices_for_resolved_events(self):
        """A resolved market_events_v2 row is not unresolved scanner substrate."""
        conn = _make_forward_substrate_conn()
        conn.execute(
            """
            INSERT INTO market_events_v2 (
                market_slug, city, target_date, temperature_metric,
                condition_id, token_id, range_label, range_low, range_high,
                outcome, created_at, recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "lowest-temperature-in-chicago-on-april-30-2026",
                "Chicago",
                "2026-04-30",
                "low",
                "cond-low-shoulder",
                "yes-low-shoulder",
                "35°F or lower",
                None,
                35.0,
                "YES",
                "2026-04-29T12:00:00Z",
                "2026-04-29T15:00:00Z",
            ),
        )
        market = _forward_market()
        market["outcomes"] = [market["outcomes"][0]]

        result = log_forward_market_substrate(
            conn,
            markets=[market],
            recorded_at="2026-04-29T16:00:00Z",
            scan_authority="VERIFIED",
        )

        assert result["status"] == "skipped_no_valid_rows"
        assert result["outcomes_skipped_with_outcome_fact"] == 1
        assert conn.execute("SELECT COUNT(*) FROM market_events_v2").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM market_price_history").fetchone()[0] == 0

    def test_forward_substrate_does_not_append_prices_for_event_identity_conflicts(self):
        """Rejected event identity conflicts cannot create orphan price facts."""
        conn = _make_forward_substrate_conn()
        market = _forward_market()
        market["outcomes"] = [market["outcomes"][0]]
        first = log_forward_market_substrate(
            conn,
            markets=[market],
            recorded_at="2026-04-29T16:00:00Z",
            scan_authority="VERIFIED",
        )
        assert first["status"] == "written"
        assert first["market_events_inserted"] == 1
        assert first["price_rows_inserted"] == 2

        conflicting = _forward_market()
        conflicting["outcomes"] = [conflicting["outcomes"][0]]
        conflicting["outcomes"][0]["token_id"] = "yes-conflicting-token"
        conflicting["outcomes"][0]["no_token_id"] = "no-conflicting-token"
        conflict = log_forward_market_substrate(
            conn,
            markets=[conflicting],
            recorded_at="2026-04-29T16:00:00Z",
            scan_authority="VERIFIED",
        )

        assert conflict["status"] == "written_with_conflicts"
        assert conflict["market_events_conflicted"] == 1
        assert conflict["price_rows_inserted"] == 0
        assert conn.execute("SELECT COUNT(*) FROM market_events_v2").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM market_price_history").fetchone()[0] == 2
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM market_price_history
            WHERE token_id IN ('yes-conflicting-token', 'no-conflicting-token')
            """
        ).fetchone()[0] == 0
