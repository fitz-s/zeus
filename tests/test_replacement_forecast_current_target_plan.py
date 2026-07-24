# Created: 2026-06-06
# Last reused/audited: 2026-07-24
# Lifecycle: created=2026-06-06; last_reviewed=2026-07-20; last_reused=2026-07-20
# Purpose: Protect current-market replacement forecast download and materialization planning.
# Reuse: Run before changing current replacement target coverage or source-run matching.
# Authority basis: Replacement forecast coverage must bind to the live baseline source_run, not stale city/date rows.
"""Tests for current-market replacement forecast download planning."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.data.replacement_forecast_current_target_plan import (
    _day0_observation_lag_reason,
    _latest_authorized_day0_fact,
    _latest_readiness_bound_posterior_ids,
    build_replacement_forecast_current_target_plan,
    replacement_forecast_current_target_keys,
    replacement_forecast_download_plan_from_current_targets,
)
from src.data.replacement_forecast_cycle_policy import (
    CURRENT_EVIDENCE_SEMANTICS_REVISION,
)


def test_day0_observation_hwm_invalidates_older_conditioning() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT,
            target_date TEXT,
            source TEXT,
            station_id TEXT,
            temp_unit TEXT,
            imported_at TEXT,
            local_timestamp TEXT,
            utc_timestamp TEXT,
            running_max REAL,
            running_min REAL,
            authority TEXT,
            training_allowed INTEGER,
            causality_status TEXT,
            source_role TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO observation_instants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "Paris",
            "2026-07-10",
            "wu_icao_history",
            "LFPB",
            "C",
            "2026-07-10T11:05:00+00:00",
            "2026-07-10T13:00:00+02:00",
            "2026-07-10T11:00:00+00:00",
            32.0,
            20.0,
            "VERIFIED",
            1,
            "OK",
            "historical_hourly",
        ),
    )
    for provenance in (
        {"day0_conditioning": {"observation_time": "2026-07-10T10:00:00+00:00"}},
        {
            "day0_provisional_observation": {
                "active": True,
                "observation_time": "2026-07-10T10:00:00+00:00",
            }
        },
    ):
        reason = _day0_observation_lag_reason(
            conn,
            city="Paris",
            target_date="2026-07-10",
            temperature_metric="high",
            decision_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
            posterior_provenance_json=json.dumps(provenance),
        )
        assert reason is not None
        assert reason.startswith("basis=day0_observation_hwm_lag")


def test_readiness_bound_posterior_ids_are_selected_in_one_batch() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE readiness_state (
            readiness_id TEXT PRIMARY KEY,
            strategy_key TEXT,
            city TEXT,
            target_local_date TEXT,
            temperature_metric TEXT,
            status TEXT,
            computed_at TEXT,
            dependency_json TEXT,
            provenance_json TEXT
        )
        """
    )
    rows = (
        (
            "paris-ready",
            "Paris",
            "READY",
            "2026-07-17T10:00:00+00:00",
            101,
        ),
        (
            "london-old-ready",
            "London",
            "READY",
            "2026-07-17T09:00:00+00:00",
            202,
        ),
        (
            "london-new-blocked",
            "London",
            "BLOCKED",
            "2026-07-17T10:00:00+00:00",
            203,
        ),
    )
    for readiness_id, city, status, computed_at, posterior_id in rows:
        conn.execute(
            """
            INSERT INTO readiness_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}')
            """,
            (
                readiness_id,
                "openmeteo_ecmwf_ifs9_bayes_fusion",
                city,
                "2026-07-18",
                "high",
                status,
                computed_at,
                json.dumps(
                    {
                        "dependencies": [
                            {
                                "role": "soft_anchor_posterior",
                                "posterior_id": posterior_id,
                            }
                        ]
                    }
                ),
            ),
        )
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    selected = _latest_readiness_bound_posterior_ids(
        conn,
        requests={
            ("Paris", "2026-07-18", "high"),
            ("London", "2026-07-18", "high"),
        },
        columns={
            "readiness_id",
            "strategy_key",
            "city",
            "target_local_date",
            "temperature_metric",
            "status",
            "computed_at",
            "dependency_json",
            "provenance_json",
        },
        binding_supported=True,
    )

    assert selected == {
        ("Paris", "2026-07-18", "high"): 101,
        ("London", "2026-07-18", "high"): -1,
    }
    assert sum("WITH requested(" in statement for statement in statements) == 1
    assert all("datetime(r.computed_at)" not in statement for statement in statements)
    conn.close()


def test_day0_observation_without_import_clock_is_not_live_visible() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT, target_date TEXT, source TEXT, station_id TEXT,
            temp_unit TEXT, local_timestamp TEXT, utc_timestamp TEXT,
            running_max REAL, running_min REAL, authority TEXT,
            training_allowed INTEGER, causality_status TEXT, source_role TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "Paris", "2026-07-10", "wu_icao_history", "LFPB", "C",
            "2026-07-10T13:00:00+02:00", "2026-07-10T11:00:00+00:00",
            32.0, 20.0, "VERIFIED", 1, "OK", "historical_hourly",
        ),
    )

    assert _latest_authorized_day0_fact(
        conn,
        city="Paris",
        target_date="2026-07-10",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
    ) is None
    conn.close()


def test_day0_global_fact_uses_provider_report_time_and_rejects_lookahead() -> None:
    """RELATIONSHIP: canonical WU row -> global Day0 monitor fact clock."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT, target_date TEXT, source TEXT, station_id TEXT,
            temp_unit TEXT, imported_at TEXT, local_timestamp TEXT,
            utc_timestamp TEXT, running_max REAL, running_min REAL,
            authority TEXT, training_allowed INTEGER, causality_status TEXT,
            source_role TEXT, provenance_json TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "Miami",
            "2026-07-23",
            "wu_icao_history",
            "KMIA",
            "F",
            "2026-07-24T02:15:00+00:00",
            "2026-07-23T21:00:00-04:00",
            "2026-07-24T01:00:00+00:00",
            92.0,
            81.0,
            "VERIFIED",
            1,
            "OK",
            "historical_hourly",
            json.dumps(
                {
                    "latest_raw_ts": "2026-07-24T01:53:00+00:00",
                    "hour_max_raw_ts": "2026-07-24T01:53:00+00:00",
                    "hour_min_raw_ts": "2026-07-24T01:10:00+00:00",
                }
            ),
        ),
    )

    fact = _latest_authorized_day0_fact(
        conn,
        city="Miami",
        target_date="2026-07-23",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 24, 2, 37, tzinfo=timezone.utc),
        require_settlement_channel=True,
    )

    assert fact is not None
    assert fact["observed_extreme_native"] == 92.0
    assert fact["observation_time"] == "2026-07-24T01:53:00+00:00"

    # Defense in depth: even a corrupt row whose possession clock predates its
    # source report cannot leak that future report into an earlier decision.
    conn.execute(
        "UPDATE observation_instants SET imported_at = ?",
        ("2026-07-24T01:10:00+00:00",),
    )
    assert _latest_authorized_day0_fact(
        conn,
        city="Miami",
        target_date="2026-07-23",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 24, 1, 30, tzinfo=timezone.utc),
        require_settlement_channel=True,
    ) is None
    conn.close()


def test_day0_hwm_accepts_authorized_durable_fast_observation_event() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE opportunity_events (
            event_id TEXT,
            event_type TEXT,
            available_at TEXT,
            received_at TEXT,
            created_at TEXT,
            payload_json TEXT
        )
        """
    )
    payload = {
        "city": "Busan",
        "target_date": "2026-07-11",
        "metric": "high",
        "settlement_source": "aviationweather_metar",
        "station_id": "RKPK",
        "observation_time": "2026-07-10T15:00:00+00:00",
        "raw_value": 25.0,
        "rounded_value": 25,
        "high_so_far": 25.0,
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
    }
    conn.execute(
        "INSERT INTO opportunity_events VALUES (?, ?, ?, ?, ?, ?)",
        (
            "day0-busan",
            "DAY0_EXTREME_UPDATED",
            "2026-07-10T15:04:00+00:00",
            "2026-07-10T15:04:01+00:00",
            "2026-07-10T15:04:01+00:00",
            json.dumps(payload),
        ),
    )
    for minute in range(8):
        available_second = 30 + minute
        older_observation_later_arrival = {
            **payload,
            "settlement_source": "wu_icao_history",
            "observation_time": f"2026-07-10T14:{minute:02d}:00+00:00",
            "observation_available_at": (
                f"2026-07-10T15:04:{available_second:02d}+00:00"
            ),
            "raw_value": 24.0,
            "rounded_value": 24,
            "high_so_far": 24.0,
        }
        conn.execute(
            "INSERT INTO opportunity_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"day0-busan-older-observation-later-arrival-{minute}",
                "DAY0_EXTREME_UPDATED",
                f"2026-07-10T15:04:{available_second:02d}+00:00",
                f"2026-07-10T15:04:{available_second + 1:02d}+00:00",
                f"2026-07-10T15:04:{available_second + 1:02d}+00:00",
                json.dumps(older_observation_later_arrival),
            ),
        )

    fact = _latest_authorized_day0_fact(
        conn,
        city="Busan",
        target_date="2026-07-11",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 10, 15, 5, tzinfo=timezone.utc),
    )
    reason = _day0_observation_lag_reason(
        conn,
        city="Busan",
        target_date="2026-07-11",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 10, 15, 5, tzinfo=timezone.utc),
        posterior_provenance_json=json.dumps({}),
    )

    assert fact is not None
    assert fact["observed_extreme_native"] == 25.0
    assert fact["source"] == "durable_day0_event:aviationweather_metar"
    assert fact["unit"] == "C"
    assert reason is not None
    assert reason.startswith("basis=day0_observation_hwm_lag")


def test_day0_settlement_certainty_excludes_unconfirmed_fast_channel() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE opportunity_events (
            event_id TEXT,
            event_type TEXT,
            available_at TEXT,
            received_at TEXT,
            created_at TEXT,
            payload_json TEXT
        )
        """
    )
    authority = {
        "city": "Karachi",
        "target_date": "2026-07-15",
        "metric": "high",
        "station_id": "OPKC",
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
    }
    for event_id, source, observed_at, value in (
        ("wu", "wu_api", "2026-07-15T03:00:00+00:00", 29.0),
        ("fast", "aviationweather_metar", "2026-07-15T03:30:00+00:00", 30.0),
    ):
        payload = {
            **authority,
            "settlement_source": source,
            "observation_time": observed_at,
            "observation_available_at": observed_at,
            "raw_value": value,
            "rounded_value": int(value),
            "high_so_far": value,
            "settlement_unit": "C",
        }
        conn.execute(
            "INSERT INTO opportunity_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                event_id,
                "DAY0_EXTREME_UPDATED",
                observed_at,
                observed_at,
                observed_at,
                json.dumps(payload),
            ),
        )

    physical = _latest_authorized_day0_fact(
        conn,
        city="Karachi",
        target_date="2026-07-15",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 15, 3, 35, tzinfo=timezone.utc),
    )
    settlement = _latest_authorized_day0_fact(
        conn,
        city="Karachi",
        target_date="2026-07-15",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 15, 3, 35, tzinfo=timezone.utc),
        require_settlement_channel=True,
    )

    assert physical is not None
    assert physical["observed_extreme_native"] == 30.0
    assert settlement is not None
    assert settlement["observed_extreme_native"] == 29.0
    assert settlement["observation_source"] == "wu_api"
    conn.close()


def _day0_source_switch_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT, target_date TEXT, source TEXT, station_id TEXT,
            temp_unit TEXT, imported_at TEXT, local_timestamp TEXT, utc_timestamp TEXT,
            running_max REAL, running_min REAL, authority TEXT,
            training_allowed INTEGER, causality_status TEXT, source_role TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE opportunity_events (
            event_id TEXT, event_type TEXT, available_at TEXT,
            received_at TEXT, created_at TEXT, payload_json TEXT
        )
        """
    )
    return conn


def _insert_paris_observation_instant(
    conn: sqlite3.Connection,
    *,
    utc_timestamp: str,
    imported_at: str,
    running_max: float,
    running_min: float,
) -> None:
    conn.execute(
        "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "Paris", "2026-07-14", "wu_icao_history", "LFPB", "C", imported_at,
            "2026-07-14T00:00:00+02:00", utc_timestamp, running_max, running_min,
            "VERIFIED", 1, "OK", "historical_hourly",
        ),
    )


def _insert_paris_day0_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    settlement_source: str,
    metric: str,
    observation_time: str,
    available_at: str,
    raw_value: float,
) -> None:
    payload = {
        "city": "Paris",
        "target_date": "2026-07-14",
        "metric": metric,
        "settlement_source": settlement_source,
        "station_id": "LFPB",
        "observation_time": observation_time,
        "observation_available_at": available_at,
        "raw_value": raw_value,
        "rounded_value": int(raw_value),
        "high_so_far": raw_value if metric == "high" else None,
        "low_so_far": raw_value if metric == "low" else None,
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
        "settlement_unit": "C",
    }
    conn.execute(
        "INSERT INTO opportunity_events VALUES (?, ?, ?, ?, ?, ?)",
        (event_id, "DAY0_EXTREME_UPDATED", available_at, available_at, available_at, json.dumps(payload)),
    )


def test_day0_event_fact_lookup_uses_family_index_when_available() -> None:
    conn = _day0_source_switch_conn()
    conn.execute(
        """
        CREATE INDEX idx_opportunity_events_day0_family_extreme
            ON opportunity_events (
                event_type,
                json_extract(payload_json, '$.city'),
                json_extract(payload_json, '$.target_date'),
                json_extract(payload_json, '$.metric'),
                available_at
            )
        """
    )
    _insert_paris_day0_event(
        conn,
        event_id="wu-api-34-indexed",
        settlement_source="wu_api",
        metric="high",
        observation_time="2026-07-14T14:00:00+00:00",
        available_at="2026-07-14T14:15:00+00:00",
        raw_value=34.0,
    )
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    fact = _latest_authorized_day0_fact(
        conn,
        city="Paris",
        target_date="2026-07-14",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc),
    )

    assert fact is not None
    assert any(
        "FROM opportunity_events INDEXED BY "
        "idx_opportunity_events_day0_family_extreme" in statement
        for statement in statements
    )
    conn.close()


def test_hko_day0_fact_uses_latest_official_snapshot_not_cross_time_max() -> None:
    """HKO cumulative snapshots may correct a provisional value; neither an
    older event nor an earlier row may keep the retracted value absorbing."""
    conn = _day0_source_switch_conn()
    conn.execute(
        "ALTER TABLE observation_instants ADD COLUMN provenance_json TEXT"
    )
    for local_ts, utc_ts, imported_at, high, low in (
        (
            "2026-07-20T00:20:00+08:00",
            "2026-07-19T16:20:00+00:00",
            "2026-07-19T16:20:10+00:00",
            30.0,
            29.5,
        ),
        (
            "2026-07-20T06:20:00+08:00",
            "2026-07-19T22:20:00+00:00",
            "2026-07-19T22:20:10+00:00",
            29.7,
            25.7,
        ),
    ):
        conn.execute(
            "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "Hong Kong",
                "2026-07-20",
                "hko_hourly_accumulator",
                "HKO",
                "C",
                imported_at,
                local_ts,
                utc_ts,
                high,
                low,
                "ICAO_STATION_NATIVE",
                0,
                "OK",
                "runtime_monitoring",
                json.dumps({
                    "observation_basis": "hko_since_midnight_extrema_1min_mean",
                    "official_running_high_c": high,
                    "official_running_low_c": low,
                }),
            ),
        )
    payload = {
        "city": "Hong Kong",
        "target_date": "2026-07-20",
        "metric": "high",
        "settlement_source": "hko_hourly_accumulator",
        "station_id": "HKO",
        "observation_time": "2026-07-19T16:20:00+00:00",
        "observation_available_at": "2026-07-19T16:20:10+00:00",
        "raw_value": 30.0,
        "rounded_value": 30,
        "high_so_far": 30.0,
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
        "settlement_unit": "C",
    }
    conn.execute(
        "INSERT INTO opportunity_events VALUES (?, ?, ?, ?, ?, ?)",
        (
            "hko-provisional-30",
            "DAY0_EXTREME_UPDATED",
            "2026-07-19T16:20:10+00:00",
            "2026-07-19T16:20:10+00:00",
            "2026-07-19T16:20:10+00:00",
            json.dumps(payload),
        ),
    )

    fact = _latest_authorized_day0_fact(
        conn,
        city="Hong Kong",
        target_date="2026-07-20",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 19, 22, 30, tzinfo=timezone.utc),
    )

    assert fact is not None
    assert fact["observed_extreme_native"] == 29.7
    assert fact["observation_time"] == "2026-07-19T22:20:00+00:00"
    assert fact["observation_source"] == "hko_hourly_accumulator"
    conn.close()


def test_hko_day0_fact_rejects_unwitnessed_row_and_legacy_event() -> None:
    conn = _day0_source_switch_conn()
    conn.execute(
        "ALTER TABLE observation_instants ADD COLUMN provenance_json TEXT"
    )
    conn.execute(
        "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "Hong Kong",
            "2026-07-20",
            "hko_hourly_accumulator",
            "HKO",
            "C",
            "2026-07-19T16:30:35+00:00",
            "2026-07-20T00:20:00+08:00",
            "2026-07-19T16:20:00+00:00",
            30.0,
            29.5,
            "ICAO_STATION_NATIVE",
            0,
            "OK",
            "runtime_monitoring",
            '{"observation_basis":"hko_since_midnight_extrema_1min_mean"}',
        ),
    )
    payload = {
        "city": "Hong Kong",
        "target_date": "2026-07-20",
        "metric": "high",
        "settlement_source": "hko_hourly_accumulator",
        "station_id": "HKO",
        "observation_time": "2026-07-19T16:20:00+00:00",
        "observation_available_at": "2026-07-19T16:30:35+00:00",
        "raw_value": 30.0,
        "rounded_value": 30,
        "high_so_far": 30.0,
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
        "settlement_unit": "C",
    }
    conn.execute(
        "INSERT INTO opportunity_events VALUES (?, ?, ?, ?, ?, ?)",
        (
            "legacy-hko-30",
            "DAY0_EXTREME_UPDATED",
            "2026-07-19T16:30:35+00:00",
            "2026-07-19T16:30:35+00:00",
            "2026-07-19T16:30:35+00:00",
            json.dumps(payload),
        ),
    )

    fact = _latest_authorized_day0_fact(
        conn,
        city="Hong Kong",
        target_date="2026-07-20",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 19, 17, 0, tzinfo=timezone.utc),
    )

    assert fact is None
    conn.close()


def test_day0_running_high_does_not_regress_when_fresher_source_saw_less() -> None:
    """2026-07-14 Paris type specimen: wu_icao_history already saw 34.0C at
    14:00-17:00 UTC; a newly-eligible aviationweather_metar (fast lane) event
    at 19:30 UTC reports only 31.0C (its own in-process cache never saw the
    earlier peak). The day-so-far high is a physical lower bound that can
    only advance — the fresher-but-lower source must NOT win."""
    conn = _day0_source_switch_conn()
    _insert_paris_observation_instant(
        conn,
        utc_timestamp="2026-07-14T14:00:00+00:00",
        imported_at="2026-07-14T14:15:00+00:00",
        running_max=34.0,
        running_min=34.0,
    )
    _insert_paris_day0_event(
        conn,
        event_id="metar-fast-31",
        settlement_source="aviationweather_metar",
        metric="high",
        observation_time="2026-07-14T19:30:00+00:00",
        available_at="2026-07-14T19:32:20+00:00",
        raw_value=31.0,
    )
    fact = _latest_authorized_day0_fact(
        conn,
        city="Paris",
        target_date="2026-07-14",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 14, 19, 52, tzinfo=timezone.utc),
    )
    assert fact is not None
    assert fact["observed_extreme_native"] == 34.0
    conn.close()


def test_day0_running_high_does_not_regress_with_sources_reversed() -> None:
    """Same law, roles swapped: the fresher fact now lives in
    observation_instants (a cooling wu_icao_history row) while the higher
    value was seen earlier by a settlement-channel event (wu_api). The
    absorbing-direction reduction must not depend on which branch is
    temporally fresher."""
    conn = _day0_source_switch_conn()
    _insert_paris_observation_instant(
        conn,
        utc_timestamp="2026-07-14T20:00:00+00:00",
        imported_at="2026-07-14T20:15:00+00:00",
        running_max=31.0,
        running_min=31.0,
    )
    _insert_paris_day0_event(
        conn,
        event_id="wu-api-34",
        settlement_source="wu_api",
        metric="high",
        observation_time="2026-07-14T14:00:00+00:00",
        available_at="2026-07-14T14:15:00+00:00",
        raw_value=34.0,
    )
    fact = _latest_authorized_day0_fact(
        conn,
        city="Paris",
        target_date="2026-07-14",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 14, 21, 0, tzinfo=timezone.utc),
    )
    assert fact is not None
    assert fact["observed_extreme_native"] == 34.0
    conn.close()


def test_day0_running_high_advances_when_fresher_source_saw_more() -> None:
    """Legitimate case: the fresher source genuinely observed a NEW peak. The
    running high must advance to it (this is the one direction 'most recent
    wins' happens to get right, and the fix must not break it)."""
    conn = _day0_source_switch_conn()
    _insert_paris_observation_instant(
        conn,
        utc_timestamp="2026-07-14T14:00:00+00:00",
        imported_at="2026-07-14T14:15:00+00:00",
        running_max=31.0,
        running_min=31.0,
    )
    _insert_paris_day0_event(
        conn,
        event_id="metar-fast-36",
        settlement_source="aviationweather_metar",
        metric="high",
        observation_time="2026-07-14T19:30:00+00:00",
        available_at="2026-07-14T19:32:20+00:00",
        raw_value=36.0,
    )
    fact = _latest_authorized_day0_fact(
        conn,
        city="Paris",
        target_date="2026-07-14",
        temperature_metric="high",
        decision_time=datetime(2026, 7, 14, 19, 52, tzinfo=timezone.utc),
    )
    assert fact is not None
    assert fact["observed_extreme_native"] == 36.0
    conn.close()


def test_day0_running_low_does_not_regress_upward_when_fresher_source_saw_more() -> None:
    """LOW-metric mirror of the type specimen: the running low is a MINIMUM.
    An early-morning wu_icao_history row already saw 15.0C; a fresher fast-lane
    event at 19:30 only saw 18.0C (never observed the early cold snap). The
    day-so-far low must stay 15.0C, not rise to 18.0C."""
    conn = _day0_source_switch_conn()
    _insert_paris_observation_instant(
        conn,
        utc_timestamp="2026-07-14T05:00:00+00:00",
        imported_at="2026-07-14T05:15:00+00:00",
        running_max=15.0,
        running_min=15.0,
    )
    _insert_paris_day0_event(
        conn,
        event_id="metar-fast-18",
        settlement_source="aviationweather_metar",
        metric="low",
        observation_time="2026-07-14T19:30:00+00:00",
        available_at="2026-07-14T19:32:20+00:00",
        raw_value=18.0,
    )
    fact = _latest_authorized_day0_fact(
        conn,
        city="Paris",
        target_date="2026-07-14",
        temperature_metric="low",
        decision_time=datetime(2026, 7, 14, 19, 52, tzinfo=timezone.utc),
    )
    assert fact is not None
    assert fact["observed_extreme_native"] == 15.0
    conn.close()


def test_day0_running_low_advances_when_fresher_source_saw_less() -> None:
    """Legitimate LOW-metric case: the fresher source genuinely observed a new
    colder trough. The running low must advance (fall) to it."""
    conn = _day0_source_switch_conn()
    _insert_paris_observation_instant(
        conn,
        utc_timestamp="2026-07-14T05:00:00+00:00",
        imported_at="2026-07-14T05:15:00+00:00",
        running_max=15.0,
        running_min=15.0,
    )
    _insert_paris_day0_event(
        conn,
        event_id="metar-fast-10",
        settlement_source="aviationweather_metar",
        metric="low",
        observation_time="2026-07-14T19:30:00+00:00",
        available_at="2026-07-14T19:32:20+00:00",
        raw_value=10.0,
    )
    fact = _latest_authorized_day0_fact(
        conn,
        city="Paris",
        target_date="2026-07-14",
        temperature_metric="low",
        decision_time=datetime(2026, 7, 14, 19, 52, tzinfo=timezone.utc),
    )
    assert fact is not None
    assert fact["observed_extreme_native"] == 10.0
    conn.close()


def _create_db(path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE market_events (
                event_id INTEGER PRIMARY KEY,
                market_slug TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                condition_id TEXT,
                token_id TEXT,
                range_label TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE forecast_posteriors (
                posterior_id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                data_version TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                dependency_source_run_ids_json TEXT,
                trade_authority_status TEXT NOT NULL,
                training_allowed INTEGER NOT NULL,
                runtime_layer TEXT NOT NULL DEFAULT 'live'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE readiness_state (
                readiness_id TEXT PRIMARY KEY,
                strategy_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'READY',
                dependency_json TEXT NOT NULL DEFAULT '{}',
                provenance_json TEXT NOT NULL,
                expires_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE source_run (
                source_run_id TEXT PRIMARY KEY,
                source_cycle_time TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE source_run_coverage (
                coverage_id TEXT PRIMARY KEY,
                source_run_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                city TEXT NOT NULL,
                target_local_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                data_version TEXT NOT NULL,
                completeness_status TEXT NOT NULL,
                readiness_status TEXT NOT NULL,
                computed_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE raw_forecast_artifacts (
                artifact_id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                data_version TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                product_metadata_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE raw_model_forecasts (
                raw_model_forecast_id INTEGER PRIMARY KEY,
                city TEXT NOT NULL,
                metric TEXT NOT NULL,
                target_date TEXT NOT NULL,
                model TEXT NOT NULL,
                forecast_value_c REAL NOT NULL,
                lead_days INTEGER,
                source_cycle_time TEXT NOT NULL,
                captured_at TEXT,
                endpoint TEXT NOT NULL
            )
            """
        )
        for city in ("Madrid", "London", "Paris"):
            conn.execute(
                """
                INSERT INTO source_run VALUES (
                    ?,
                    '2026-06-07T06:00:00+00:00'
                )
                """,
                (f"baseline-current-{city}",),
            )
            conn.execute(
                """
                INSERT INTO market_events (
                    market_slug, city, target_date, temperature_metric,
                    condition_id, token_id, range_label
                ) VALUES (?, ?, '2026-06-09', 'high', 'condition', ?, ?)
                """,
                (
                    f"highest-temperature-in-{city.lower()}-on-june-9-2026",
                    city,
                    f"token-{city}",
                    f"Will the highest temperature in {city} be 30°C on June 9?",
                ),
            )
            conn.execute(
                """
                INSERT INTO source_run_coverage (
                    coverage_id, source_run_id, source_id, city, target_local_date,
                    temperature_metric, data_version, completeness_status,
                    readiness_status, computed_at, recorded_at
                ) VALUES (?, ?, 'ecmwf_open_data', ?, '2026-06-09',
                    'high', 'ecmwf_opendata_mx2t3_local_calendar_day_max',
                    'COMPLETE', 'LIVE_ELIGIBLE',
                    '2026-06-07T08:00:00+00:00',
                    '2026-06-07T08:00:00+00:00')
                """,
                (f"coverage-{city}", f"baseline-current-{city}", city),
            )
        conn.execute(
            """
            INSERT INTO forecast_posteriors (
                source_id, product_id, data_version, city, target_date,
                temperature_metric, dependency_source_run_ids_json,
                trade_authority_status, training_allowed
            ) VALUES (
                'openmeteo_ecmwf_ifs9_bayes_fusion',
                'openmeteo_ecmwf_ifs9_bayes_fusion_v1',
                'openmeteo_ecmwf_ifs9_bayes_fusion_high_v1',
                'Paris', '2026-06-09', 'high',
                '{"baseline_b0":"baseline-current-Paris","openmeteo_ifs9_anchor":"openmeteo-current-Paris"}',
                'live', 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO forecast_posteriors (
                source_id, product_id, data_version, city, target_date,
                temperature_metric, dependency_source_run_ids_json,
                trade_authority_status, training_allowed
            ) VALUES (
                'openmeteo_ecmwf_ifs9_bayes_fusion',
                'openmeteo_ecmwf_ifs9_bayes_fusion_v1',
                'openmeteo_ecmwf_ifs9_bayes_fusion_high_v1',
                'Madrid', '2026-06-09', 'high',
                '{"baseline_b0":"baseline-stale-Madrid","openmeteo_ifs9_anchor":"openmeteo-current-Madrid"}',
                'live', 0
            )
            """
        )
        conn.execute(
            """
                INSERT INTO readiness_state (
                    readiness_id, strategy_key, status, dependency_json, provenance_json
                ) VALUES (?, ?, 'READY', ?, ?)
            """,
            (
                "ready-paris",
                "openmeteo_ecmwf_ifs9_bayes_fusion",
                json.dumps(
                    {
                        "dependencies": [
                            {"role": "baseline_b0", "source_run_id": "baseline-current-Paris"},
                            {"role": "openmeteo_ifs9_anchor", "source_run_id": "openmeteo-current-Paris"},
                        ]
                    }
                ),
                json.dumps({"city": "Paris", "target_date": "2026-06-09", "temperature_metric": "high"}),
            ),
        )
        conn.execute(
            """
                INSERT INTO readiness_state (
                    readiness_id, strategy_key, status, dependency_json, provenance_json
                ) VALUES (?, ?, 'READY', ?, ?)
            """,
            (
                "ready-madrid-stale",
                "openmeteo_ecmwf_ifs9_bayes_fusion",
                json.dumps(
                    {
                        "dependencies": [
                            {"role": "baseline_b0", "source_run_id": "baseline-stale-Madrid"},
                            {"role": "openmeteo_ifs9_anchor", "source_run_id": "openmeteo-current-Madrid"},
                        ]
                    }
                ),
                json.dumps({"city": "Madrid", "target_date": "2026-06-09", "temperature_metric": "high"}),
            ),
        )
        # An artifact only counts as coverage if its file is actually on disk (DB<->disk
        # provenance antibody). Write a real file for the "present" London artifacts.
        present_artifact = Path(path).parent / "present_artifact.grib2"
        present_artifact.write_bytes(b"GRIB")
        for city in ("London", "Paris"):
            conn.execute(
                """
                INSERT INTO raw_forecast_artifacts (
                    source_id, product_id, data_version, artifact_path, product_metadata_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "openmeteo_ecmwf_ifs_9km",
                    "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                    "openmeteo_ecmwf_ifs9_anchor_localday_high",
                    str(present_artifact),
                    json.dumps(
                        {
                            "city": city,
                            "cities": [city],
                            "target_date": "2026-06-09",
                            "target_dates": ["2026-06-09"],
                            "source_cycle_time": "2026-06-07T06:00:00+00:00",
                            "source_run_id": f"openmeteo-current-{city}",
                        }
                    ),
                ),
            )
            if city in {"London", "Paris"}:
                conn.execute(
                    """
                    INSERT INTO raw_model_forecasts (
                        city, metric, target_date, model, forecast_value_c, lead_days,
                        source_cycle_time, captured_at, endpoint
                    ) VALUES (?, 'high', '2026-06-09', 'gfs_global', 21.0, 2,
                        '2026-06-07T06:00:00+00:00',
                        '2026-06-07T08:00:00+00:00',
                        'single_runs')
                    """,
                    (city,),
                )
        conn.commit()
    finally:
        conn.close()


def test_current_target_plan_classifies_covered_seedable_and_missing_manifest_targets(
    tmp_path,
    monkeypatch,
) -> None:
    import src.data.replacement_forecast_current_target_plan as plan_module

    db = tmp_path / "forecasts.db"
    _create_db(db)
    primed: list[set[tuple[str, str, str]]] = []
    released: list[bool] = []

    def _prime(conn, *, requests, decision_time):
        assert conn.in_transaction
        primed.append(set(requests))
        return lambda: released.append(True)

    monkeypatch.setattr(
        plan_module,
        "prime_frozen_replacement_artifact_hwm",
        _prime,
    )

    # Fixed evaluation time before the 2026-06-09 target so day0 logic does not lock the targets
    # (the fixture dates are static; real wall-clock has since advanced past them).
    now_utc = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    plan = build_replacement_forecast_current_target_plan(db, now_utc=now_utc)
    download_plan = replacement_forecast_download_plan_from_current_targets(plan)

    assert plan.status == "CURRENT_TARGETS_MISSING_REPLACEMENT_COVERAGE"
    assert plan.target_count == 3
    assert plan.covered_count == 1
    # Seeding gates on the OpenMeteo anchor manifest and coverage requires the same
    # anchor source_run_id in both posterior and readiness. London (manifest present,
    # no posterior) is seedable; Madrid (no OpenMeteo manifest) is the download target.
    assert plan.can_seed_count == 1
    assert plan.missing_openmeteo_manifest_count == 1
    assert [row["city"] for row in download_plan["seedable_targets"]] == ["London"]
    assert [row["city"] for row in download_plan["openmeteo_download_targets"]] == ["Madrid"]
    assert download_plan["fusion_current_value_missing_targets"] == []
    assert primed == [
        {
            ("London", "2026-06-09", "high"),
            ("Madrid", "2026-06-09", "high"),
            ("Paris", "2026-06-09", "high"),
        }
    ]
    assert released == [True]


def test_current_target_plan_does_not_count_all_market_history(
    tmp_path,
    monkeypatch,
) -> None:
    import src.data.replacement_forecast_current_target_plan as plan_module

    db = tmp_path / "forecasts.db"
    _create_db(db)
    statements: list[str] = []

    def _traced_read_only(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(plan_module, "_connect_read_only", _traced_read_only)
    monkeypatch.setattr(
        plan_module,
        "prime_frozen_replacement_artifact_hwm",
        lambda *args, **kwargs: lambda: None,
    )

    build_replacement_forecast_current_target_plan(
        db,
        min_target_date="2026-06-07",
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )

    normalized = {" ".join(statement.split()) for statement in statements}
    assert "SELECT 1 FROM market_events LIMIT 1" in normalized
    assert "SELECT COUNT(*) FROM market_events" not in normalized


def test_current_target_plan_uses_typed_readiness_scope_when_available(
    tmp_path,
    monkeypatch,
) -> None:
    import src.data.replacement_forecast_current_target_plan as plan_module

    db = tmp_path / "forecasts.db"
    _create_db(db)
    now_utc = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)
    legacy = build_replacement_forecast_current_target_plan(db, now_utc=now_utc)
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            ALTER TABLE readiness_state ADD COLUMN city TEXT;
            ALTER TABLE readiness_state ADD COLUMN target_local_date TEXT;
            ALTER TABLE readiness_state ADD COLUMN temperature_metric TEXT;
            UPDATE readiness_state
               SET city = json_extract(provenance_json, '$.city'),
                   target_local_date = json_extract(provenance_json, '$.target_date'),
                   temperature_metric = json_extract(
                       provenance_json,
                       '$.temperature_metric'
                   );
            """
        )
        conn.commit()
    finally:
        conn.close()

    statements: list[str] = []

    def _traced_read_only(path: Path) -> sqlite3.Connection:
        traced = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
        traced.set_trace_callback(statements.append)
        return traced

    monkeypatch.setattr(plan_module, "_connect_read_only", _traced_read_only)
    typed = build_replacement_forecast_current_target_plan(db, now_utc=now_utc)

    assert typed.as_dict() == legacy.as_dict()
    readiness_sql = [
        " ".join(statement.split())
        for statement in statements
        if "LEFT JOIN readiness_state r" in statement
    ]
    assert readiness_sql
    assert all("r.city = requested.city" in statement for statement in readiness_sql)
    assert all(
        "json_extract(r.provenance_json, '$.city')" not in statement
        for statement in readiness_sql
    )
    assert all("datetime(r.computed_at)" not in statement for statement in readiness_sql)
    assert all("datetime(p.computed_at)" not in statement for statement in statements)


def test_current_target_plan_orders_nearest_market_date_first(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "UPDATE market_events SET target_date='2026-06-10' WHERE city='London'"
        )
        conn.execute(
            """
            UPDATE source_run_coverage
               SET target_local_date='2026-06-10'
             WHERE city='London'
            """
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )

    assert [row.target_date for row in plan.rows] == [
        "2026-06-09",
        "2026-06-09",
        "2026-06-10",
    ]


def test_current_target_keys_match_full_plan_scope_without_coverage_work(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)

    keys = replacement_forecast_current_target_keys(
        db,
        min_target_date="2026-06-07",
    )
    plan = build_replacement_forecast_current_target_plan(
        db,
        min_target_date="2026-06-07",
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )

    assert [
        (key.city, key.target_date, key.temperature_metric)
        for key in keys
    ] == [
        (row.city, row.target_date, row.temperature_metric)
        for row in plan.rows
    ]


def test_current_target_plan_reseeds_old_probability_semantics(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        for ddl in (
            "ALTER TABLE forecast_posteriors ADD COLUMN q_lcb_json TEXT",
            "ALTER TABLE forecast_posteriors ADD COLUMN q_ucb_json TEXT",
            "ALTER TABLE forecast_posteriors ADD COLUMN provenance_json TEXT",
            "ALTER TABLE forecast_posteriors ADD COLUMN source_cycle_time TEXT",
            "ALTER TABLE forecast_posteriors ADD COLUMN computed_at TEXT",
        ):
            conn.execute(ddl)
        conn.execute(
            """
            UPDATE forecast_posteriors
               SET q_lcb_json='{}', q_ucb_json='{}',
                   source_cycle_time='2026-06-07T06:00:00+00:00',
                   computed_at='2026-06-07T10:00:00+00:00',
                   provenance_json=?
             WHERE city='Paris'
            """,
            (
                json.dumps(
                    {
                        "q_lcb_basis": "fused_center_bootstrap_p05",
                        "bayes_precision_fusion": {
                            "current_evidence_shape": {
                                "semantics_revision": "older-law"
                            }
                        },
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    stale_plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    stale = next(row for row in stale_plan.rows if row.city == "Paris")
    assert stale.covered is False
    assert stale.can_seed is True

    conn = sqlite3.connect(db)
    try:
        provenance = json.loads(
            conn.execute(
                "SELECT provenance_json FROM forecast_posteriors WHERE city='Paris'"
            ).fetchone()[0]
        )
        provenance["bayes_precision_fusion"]["current_evidence_shape"][
            "semantics_revision"
        ] = CURRENT_EVIDENCE_SEMANTICS_REVISION
        conn.execute(
            "UPDATE forecast_posteriors SET provenance_json=? WHERE city='Paris'",
            (json.dumps(provenance),),
        )
        conn.commit()
    finally:
        conn.close()

    current_plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    current = next(row for row in current_plan.rows if row.city == "Paris")
    assert current.covered is True
    assert current.can_seed is False


def test_current_target_plan_reseeds_same_cycle_late_used_model_input(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("ALTER TABLE forecast_posteriors ADD COLUMN source_cycle_time TEXT")
        conn.execute("ALTER TABLE forecast_posteriors ADD COLUMN computed_at TEXT")
        conn.execute("ALTER TABLE forecast_posteriors ADD COLUMN provenance_json TEXT")
        conn.execute(
            "UPDATE forecast_posteriors SET source_cycle_time=?, computed_at=?, "
            "provenance_json=? WHERE city='Paris'",
            (
                "2026-06-07T06:00:00+00:00",
                "2026-06-07T08:30:00+00:00",
                json.dumps(
                        {
                            "used_models": ["gfs_global"],
                            "q_lcb_basis": "fused_center_bootstrap_p05",
                            "bayes_precision_fusion": {
                                "current_evidence_shape": {
                                    "semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION
                                }
                            },
                        }
                ),
            ),
        )
        conn.execute(
            "UPDATE raw_model_forecasts SET captured_at=? WHERE city='Paris' "
            "AND model='gfs_global'",
            ("2026-06-07T09:00:00+00:00",),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    paris = next(row for row in plan.rows if row.city == "Paris")

    assert paris.input_lag_reason is not None
    assert "same_cycle_late_input" in paris.input_lag_reason
    assert paris.covered is False
    assert paris.can_seed is True


def test_current_target_plan_does_not_seed_when_fusion_current_values_are_missing(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM raw_model_forecasts WHERE city = 'London'")
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    download_plan = replacement_forecast_download_plan_from_current_targets(plan)
    london = next(row for row in plan.rows if row.city == "London")

    assert london.openmeteo_manifest_count == 1
    assert london.fusion_current_value_count == 0
    assert london.missing_openmeteo_manifest is False
    assert london.missing_fusion_current_values is True
    assert london.can_seed is False
    assert plan.can_seed_count == 0
    assert plan.missing_fusion_current_values_count == 1
    assert "REPLACEMENT_CURRENT_TARGET_PLAN_MISSING_FUSION_CURRENT_VALUES" in plan.reason_codes
    assert [row["city"] for row in download_plan["fusion_current_value_missing_targets"]] == ["London"]
    assert download_plan["seedable_targets"] == []


def test_current_target_plan_seeds_when_openmeteo_cycle_outruns_lagging_baseline(tmp_path) -> None:
    """Regression: baseline (ECMWF-Open-Data, 00Z/12Z cadence) can lag behind a
    finer-cadence (00/06/12/18Z) openmeteo/BAYES_PRECISION_FUSION anchor manifest. The
    fusion current-value ceiling must be checked against the OPENMETEO MANIFEST'S OWN
    resolved cycle, not the baseline's cycle -- otherwise a scope with real captured
    fusion rows at the newer openmeteo cycle is wrongly blocked (count 0 ->
    missing_fusion_current_values -> can_seed False) even though the data is genuinely
    servable at the manifest's own resolved cycle."""
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        # London's OpenMeteo anchor manifest resolves to an 18Z cycle -- newer than the
        # baseline's 06Z source_run cycle (baseline has not published its next cycle yet).
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "source_cycle_time": "2026-06-07T18:00:00+00:00",
                        "source_run_id": "openmeteo-18z-London",
                    }
                ),
            ),
        )
        # The captured fusion current-value row exists ONLY at the newer 18Z cycle -- the
        # baseline's 06Z cycle (the pre-fix ceiling) has nothing at or before it.
        conn.execute("DELETE FROM raw_model_forecasts WHERE city = 'London'")
        conn.execute(
            """
            INSERT INTO raw_model_forecasts (
                city, metric, target_date, model, forecast_value_c, lead_days,
                source_cycle_time, captured_at, endpoint
            ) VALUES ('London', 'high', '2026-06-09', 'gfs_global', 21.0, 0,
                '2026-06-07T18:00:00+00:00',
                '2026-06-07T19:00:00+00:00',
                'single_runs')
            """
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 19, 30, tzinfo=timezone.utc),
    )
    london = next(row for row in plan.rows if row.city == "London")

    assert london.baseline_source_cycle_time == "2026-06-07T06:00:00+00:00"
    assert london.openmeteo_manifest_count == 1
    assert london.fusion_current_value_count > 0
    assert london.missing_fusion_current_values is False
    assert london.can_seed is True


def test_current_target_plan_still_blocks_when_no_row_at_openmeteo_resolved_cycle(tmp_path) -> None:
    """Invariant: even with the manifest's-own-cycle fix, a scope with NO captured fusion
    row at (or before) the openmeteo manifest's resolved cycle must still be blocked. This
    guards against the fix over-admitting -- e.g. degenerating into an unconditional pass
    -- by proving the ceiling semantics still exclude a row from a cycle strictly newer
    than the manifest's own resolved cycle."""
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "source_cycle_time": "2026-06-07T18:00:00+00:00",
                        "source_run_id": "openmeteo-18z-London",
                    }
                ),
            ),
        )
        # Only a row from the NEXT cycle after the manifest's resolved 18Z cycle exists --
        # strictly newer than the ceiling under either the old (baseline) or new (manifest)
        # resolved cycle, so it must not be servable under the ceiling semantics either way.
        conn.execute("DELETE FROM raw_model_forecasts WHERE city = 'London'")
        conn.execute(
            """
            INSERT INTO raw_model_forecasts (
                city, metric, target_date, model, forecast_value_c, lead_days,
                source_cycle_time, captured_at, endpoint
            ) VALUES ('London', 'high', '2026-06-09', 'gfs_global', 21.0, 0,
                '2026-06-08T00:00:00+00:00',
                '2026-06-08T01:00:00+00:00',
                'single_runs')
            """
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 19, 30, tzinfo=timezone.utc),
    )
    london = next(row for row in plan.rows if row.city == "London")

    assert london.openmeteo_manifest_count == 1
    assert london.fusion_current_value_count == 0
    assert london.missing_fusion_current_values is True
    assert london.can_seed is False


def test_current_target_plan_can_require_openmeteo_manifest_cycle(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
        required_openmeteo_source_cycle_time="2026-06-07T12:00:00+00:00",
    )
    london = next(row for row in plan.rows if row.city == "London")
    paris = next(row for row in plan.rows if row.city == "Paris")

    assert london.openmeteo_manifest_count == 0
    assert london.missing_openmeteo_manifest is True
    assert london.can_seed is False
    assert paris.openmeteo_manifest_count == 0
    assert paris.covered is False
    assert plan.missing_openmeteo_manifest_count >= 2


def test_current_target_plan_requires_openmeteo_cycle_matching_each_baseline_source_run(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE source_run
            SET source_cycle_time = '2026-06-07T12:00:00+00:00'
            WHERE source_run_id = 'baseline-current-London'
            """
        )
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "source_cycle_time": "2026-06-07T06:00:00+00:00",
                        "source_run_id": "openmeteo-06z-London",
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 30, tzinfo=timezone.utc),
    )
    download_plan = replacement_forecast_download_plan_from_current_targets(plan)
    london = next(row for row in plan.rows if row.city == "London")

    assert london.baseline_source_cycle_time == "2026-06-07T12:00:00+00:00"
    assert london.openmeteo_manifest_count == 0
    assert london.missing_openmeteo_manifest is True
    assert "London" in [row["city"] for row in download_plan["openmeteo_download_targets"]]


def test_current_target_plan_explicit_cycle_currency_overrides_stale_baseline_cycle(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE source_run
            SET source_cycle_time = '2026-06-07T12:00:00+00:00'
            WHERE source_run_id = 'baseline-current-London'
            """
        )
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "source_cycle_time": "2026-06-07T12:00:00+00:00",
                        "source_run_id": "openmeteo-12z-London",
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 18, 30, tzinfo=timezone.utc),
        required_openmeteo_source_cycle_time="2026-06-07T18:00:00+00:00",
    )
    london = next(row for row in plan.rows if row.city == "London")

    assert london.baseline_source_cycle_time == "2026-06-07T12:00:00+00:00"
    assert london.openmeteo_manifest_count == 0
    assert london.missing_openmeteo_manifest is True
    assert london.can_seed is False


def test_current_target_plan_rejects_openmeteo_manifest_without_target_day_samples(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    payload = tmp_path / "london_partial_payload.json"
    payload.write_text(
        json.dumps(
            {
                "hourly": {
                    "time": ["2026-06-08T00:00", "2026-06-08T01:00"],
                    "temperature_2m": [12.0, 13.0],
                }
            }
        ),
        encoding="utf-8",
    )
    precision = tmp_path / "precision.json"
    precision.write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
                        "openmeteo_endpoint": "single_runs_api",
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "forecast_hours": 120,
                        "source_cycle_time": "2026-06-07T06:00:00+00:00",
                        "source_run_id": "openmeteo-current-London",
                        "openmeteo_payload_json": str(payload),
                        "precision_metadata_json": str(precision),
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    london = next(row for row in plan.rows if row.city == "London")

    assert london.openmeteo_manifest_count == 0
    assert london.missing_openmeteo_manifest is True
    assert london.can_seed is False


def test_current_target_plan_counts_openmeteo_manifest_with_target_day_samples(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    payload = tmp_path / "london_covering_payload.json"
    payload.write_text(
        json.dumps(
            {
                "hourly": {
                    "time": ["2026-06-09T00:00", "2026-06-09T12:00"],
                    "temperature_2m": [14.0, 18.0],
                }
            }
        ),
        encoding="utf-8",
    )
    precision = tmp_path / "precision.json"
    precision.write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
                        "openmeteo_endpoint": "single_runs_api",
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "forecast_hours": 120,
                        "source_cycle_time": "2026-06-07T06:00:00+00:00",
                        "source_run_id": "openmeteo-current-London",
                        "openmeteo_payload_json": str(payload),
                        "precision_metadata_json": str(precision),
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    london = next(row for row in plan.rows if row.city == "London")

    assert london.openmeteo_manifest_count == 1
    assert london.can_seed is True


def test_current_target_plan_counts_meta_stamped_horizon_manifest_with_target_day_samples(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    payload = tmp_path / "london_meta_stamped_payload.json"
    payload.write_text(
        json.dumps(
            {
                "hourly": {
                    "time": ["2026-06-08T12:00", "2026-06-09T00:00", "2026-06-09T12:00"],
                    "temperature_2m": [13.0, 14.0, 18.0],
                }
            }
        ),
        encoding="utf-8",
    )
    precision = tmp_path / "precision.json"
    precision.write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
                        "openmeteo_endpoint": "standard_api_meta_stamped",
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-08",
                        "target_dates": ["2026-06-08"],
                        "forecast_hours": 120,
                        "source_cycle_time": "2026-06-07T06:00:00+00:00",
                        "source_run_id": "openmeteo-current-London",
                        "openmeteo_payload_json": str(payload),
                        "precision_metadata_json": str(precision),
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    london = next(row for row in plan.rows if row.city == "London")

    assert london.openmeteo_manifest_count == 1
    assert london.can_seed is True


def test_current_target_plan_counts_single_runs_horizon_manifest_with_target_day_samples(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    payload = tmp_path / "london_single_runs_horizon_payload.json"
    payload.write_text(
        json.dumps(
            {
                "hourly": {
                    "time": ["2026-06-08T12:00", "2026-06-09T00:00", "2026-06-09T12:00"],
                    "temperature_2m": [13.0, 14.0, 18.0],
                }
            }
        ),
        encoding="utf-8",
    )
    precision = tmp_path / "precision.json"
    precision.write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            WHERE product_metadata_json LIKE '%London%'
            """,
            (
                json.dumps(
                    {
                        "artifact_class": "openmeteo_ecmwf_ifs9_anchor_current_targets",
                        "openmeteo_endpoint": "single_runs_api",
                        "city": "London",
                        "cities": ["London"],
                        "target_date": "2026-06-08",
                        "target_dates": ["2026-06-08"],
                        "forecast_hours": 120,
                        "source_cycle_time": "2026-06-07T06:00:00+00:00",
                        "source_run_id": "openmeteo-current-London",
                        "openmeteo_payload_json": str(payload),
                        "precision_metadata_json": str(precision),
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    london = next(row for row in plan.rows if row.city == "London")

    assert london.openmeteo_manifest_count == 1
    assert london.can_seed is True


def test_current_target_plan_reseeds_when_openmeteo_anchor_advances_under_same_baseline(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    newer_artifact = Path(db).parent / "newer_paris_artifact.json"
    newer_artifact.write_text("{}", encoding="utf-8")
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            INSERT INTO raw_forecast_artifacts (
                source_id, product_id, data_version, artifact_path, product_metadata_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "openmeteo_ecmwf_ifs_9km",
                "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                "openmeteo_ecmwf_ifs9_anchor_localday_high",
                str(newer_artifact),
                json.dumps(
                    {
                        "city": "Paris",
                        "cities": ["Paris"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "source_cycle_time": "2026-06-07T06:00:00+00:00",
                        "requested_source_available_at": "2026-06-07T12:00:00+00:00",
                        "source_run_id": "openmeteo-newer-Paris",
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    paris = next(row for row in plan.rows if row.city == "Paris")

    assert paris.baseline_source_run_id == "baseline-current-Paris"
    assert paris.openmeteo_source_run_id == "openmeteo-newer-Paris"
    assert paris.posterior_count == 0
    assert paris.readiness_count == 0
    assert paris.covered is False
    assert paris.can_seed is True


def test_current_target_plan_does_not_treat_blocked_replacement_readiness_as_covered(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE readiness_state SET status = 'BLOCKED' WHERE readiness_id = 'ready-paris'")
        present_artifact = Path(db).parent / "present_artifact.grib2"  # written by _create_db
        conn.execute(
            """
            INSERT INTO raw_forecast_artifacts (
                source_id, product_id, data_version, artifact_path, product_metadata_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "openmeteo_ecmwf_ifs_9km",
                "openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
                "openmeteo_ecmwf_ifs9_anchor_localday_high",
                str(present_artifact),
                json.dumps(
                    {
                        "city": "Paris",
                        "cities": ["Paris"],
                        "target_date": "2026-06-09",
                        "target_dates": ["2026-06-09"],
                        "source_run_id": "openmeteo-current-Paris",
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        now_utc=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    paris = next(row for row in plan.rows if row.city == "Paris")

    assert paris.posterior_count == 1
    assert paris.readiness_count == 0
    assert paris.covered is False
    assert paris.can_seed is True


def test_current_target_plan_ignores_artifact_rows_whose_file_is_deleted(tmp_path) -> None:
    """DB<->disk provenance relationship (Fitz #4): when a raw_forecast_artifacts FILE is
    deleted but its DB row survives, the plan must NOT keep reporting the target as covered/
    seedable. Otherwise the download-skip gate believes raw inputs are present and never
    re-fetches, while disk-based seed discovery finds nothing -> the ~30h zero-trade stall.

    Models the real incident exactly: London is seedable with files on disk; delete the file
    (leave the DB row) and London must flip to missing_openmeteo_manifest so the gate re-downloads.

    The DB<->disk provenance invariant (a deleted file flips the target back
    to needs-download, so the gate re-fetches) is preserved via the OpenMeteo
    manifest.
    """
    db = tmp_path / "forecasts.db"
    _create_db(db)
    now_utc = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)

    # Baseline: London has artifacts on disk -> seedable (openmeteo present), nothing missing.
    before = build_replacement_forecast_current_target_plan(db, now_utc=now_utc)
    london_before = next(row for row in before.rows if row.city == "London")
    assert london_before.can_seed is True
    assert london_before.openmeteo_manifest_count == 1
    assert london_before.missing_openmeteo_manifest is False

    # The cleanup deletes the GRIB/manifest FILE but the DB row survives (dangling pointer).
    present_artifact = Path(db).parent / "present_artifact.grib2"
    present_artifact.unlink()

    after = build_replacement_forecast_current_target_plan(db, now_utc=now_utc)
    london_after = next(row for row in after.rows if row.city == "London")
    assert london_after.openmeteo_manifest_count == 0, "a deleted artifact file must not count as coverage"
    assert london_after.missing_openmeteo_manifest is True, "gate must see missing -> re-download"
    assert london_after.can_seed is False
    assert after.missing_openmeteo_manifest_count >= 1


def test_current_target_plan_does_not_seed_after_local_target_day_starts(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    _create_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            UPDATE market_events
            SET target_date = '2026-06-07'
            WHERE city = 'London'
            """
        )
        conn.execute(
            """
            UPDATE source_run_coverage
            SET target_local_date = '2026-06-07'
            WHERE city = 'London'
            """
        )
        conn.execute(
            """
            UPDATE raw_forecast_artifacts
            SET product_metadata_json = ?
            """,
            (json.dumps({"cities": ["London"], "target_dates": ["2026-06-07"]}),),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(
        db,
        min_target_date="2026-06-07",
        now_utc=datetime(2026, 6, 7, 1, 0, tzinfo=timezone.utc),
    )
    london = next(row for row in plan.rows if row.city == "London")

    assert london.covered is False
    assert london.day0_observed_extreme_required is True
    assert london.can_seed is False
    assert plan.day0_observed_extreme_required_count == 1
    assert "REPLACEMENT_CURRENT_TARGET_PLAN_DAY0_OBSERVED_EXTREME_REQUIRED" in plan.reason_codes


def test_current_target_plan_blocks_when_source_run_dependency_schema_is_missing(tmp_path) -> None:
    db = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE market_events (
                event_id INTEGER PRIMARY KEY,
                market_slug TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                token_id TEXT,
                range_label TEXT
            );
            CREATE TABLE forecast_posteriors (
                posterior_id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                data_version TEXT NOT NULL,
                city TEXT NOT NULL,
                target_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                trade_authority_status TEXT NOT NULL,
                training_allowed INTEGER NOT NULL
            );
            CREATE TABLE readiness_state (
                readiness_id TEXT PRIMARY KEY,
                strategy_key TEXT NOT NULL,
                provenance_json TEXT NOT NULL
            );
            CREATE TABLE source_run_coverage (
                coverage_id TEXT PRIMARY KEY,
                source_run_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                city TEXT NOT NULL,
                target_local_date TEXT NOT NULL,
                temperature_metric TEXT NOT NULL,
                data_version TEXT NOT NULL,
                completeness_status TEXT NOT NULL,
                readiness_status TEXT NOT NULL,
                computed_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE raw_forecast_artifacts (
                artifact_id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                product_id TEXT NOT NULL,
                data_version TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                product_metadata_json TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO market_events (
                market_slug, city, target_date, temperature_metric, token_id, range_label
            ) VALUES ('slug', 'Madrid', '2026-06-09', 'high', 'token', '30°C')
            """
        )
        conn.execute(
            """
            INSERT INTO source_run_coverage (
                coverage_id, source_run_id, source_id, city, target_local_date,
                temperature_metric, data_version, completeness_status, readiness_status,
                computed_at, recorded_at
            ) VALUES (
                'coverage', 'baseline-current', 'ecmwf_open_data', 'Madrid',
                '2026-06-09', 'high', 'ecmwf_opendata_mx2t3_local_calendar_day_max',
                'COMPLETE', 'LIVE_ELIGIBLE',
                '2026-06-07T08:00:00+00:00', '2026-06-07T08:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO forecast_posteriors (
                source_id, product_id, data_version, city, target_date,
                temperature_metric, trade_authority_status, training_allowed
            ) VALUES (
                'openmeteo_ecmwf_ifs9_bayes_fusion',
                'openmeteo_ecmwf_ifs9_bayes_fusion_v1',
                'openmeteo_ecmwf_ifs9_bayes_fusion_high_v1',
                'Madrid', '2026-06-09', 'high', 'live', 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO readiness_state (
                readiness_id, strategy_key, provenance_json
            ) VALUES (?, ?, ?)
            """,
            (
                "ready-old",
                "openmeteo_ecmwf_ifs9_bayes_fusion",
                json.dumps({"city": "Madrid", "target_date": "2026-06-09", "temperature_metric": "high"}),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    plan = build_replacement_forecast_current_target_plan(db)

    assert plan.status == "BLOCKED"
    assert plan.reason_codes == ("REPLACEMENT_CURRENT_TARGET_PLAN_SOURCE_RUN_DEPENDENCY_SCHEMA_MISSING",)
