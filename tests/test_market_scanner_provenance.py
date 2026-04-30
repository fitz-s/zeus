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
import sqlite3
from datetime import datetime, timezone

import httpx
import pytest

from src.backtest.economics import check_economics_readiness
from src.data import market_scanner as ms
from src.data.market_scanner import (
    MarketSnapshot,
    _clear_active_events_cache,
    _get_active_events,
    _get_active_events_snapshot,
    _parse_event,
    get_last_scan_authority,
)
from src.state import db as state_db
from src.state.db import log_forward_market_substrate


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
    title: str = "Highest temperature in Los Angeles on April 29?",
    slug: str = "highest-temperature-in-los-angeles-on-april-29-2026",
    question: str = "Will the high temperature in Los Angeles be 68°F or higher?",
    resolution_source: str | None = "https://www.wunderground.com/history/daily/us/ca/los-angeles/KLAX",
    market_resolution_source: str | None = None,
) -> dict:
    market = {
        "id": "market1",
        "question": question,
        "outcomePrices": "[0.55, 0.45]",
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["token_yes", "token_no"]',
        "conditionId": "cond1",
        "active": True,
        "closed": False,
    }
    if market_resolution_source is not None:
        market["resolutionSource"] = market_resolution_source
    event = {
        "id": "event1",
        "slug": slug,
        "title": title,
        "markets": [market],
    }
    if resolution_source is not None:
        event["resolutionSource"] = resolution_source
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
            UNIQUE(token_id, recorded_at)
        );
        """
    )
    return conn


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

        readiness = check_economics_readiness(conn)
        assert readiness.ready is False
        assert "empty_table:market_events_v2" not in readiness.blockers
        assert "empty_table:market_price_history" not in readiness.blockers
        assert "missing_table:venue_trade_facts" in readiness.blockers
        assert "no_market_event_outcomes" in readiness.blockers
        assert "economics_engine_not_implemented" in readiness.blockers

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
