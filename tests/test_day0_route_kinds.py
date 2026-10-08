# Created: 2026-10-07
# Last reused/audited: 2026-10-07
# Lifecycle: created=2026-10-07; last_reviewed=2026-10-07; last_reused=2026-10-07
# Authority basis: consult REQ-20261007-125708-2dc18b (NO-GO) finding (d)/(f); coordinator D1 (typed route kinds).
# Purpose: Pin typed fast-route kinds. NATIVE_REPORT routes (MGM, IMD, metaviatelecom) are AWC-equivalent METAR
#   content (routine and SPECI). INSTRUMENT_PROXY routes (JMA, SWOB) are never a settlement fact, boundary or
#   frontier substitute. The real adapter binding accepts Tokyo JMA 24.6 / page 24.6 / AWC 25.
# Reuse: Private SQLite fixtures; calls the unmodified adapter function.
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.state.schema.observation_prints_schema import append_print, ensure_table

UTC = timezone.utc


def _route_raw(channel: str, station: str, provider_station: str, at: datetime, value: float) -> str:
    return json.dumps({"observed_at": at.isoformat(), "payload_sha256": "ab" * 32,
                       "provider_observed_at_ms": int(at.timestamp() * 1000), "provider_published_at_ms": None,
                       "provider_station": provider_station, "received_at_ms": int(at.timestamp() * 1000) + 60000,
                       "source_channel": channel, "station_id": station, "unit": "C", "value_native": value},
                      sort_keys=True)


def _tokyo_world(*, jma: float, page: float, awc: int, at: datetime) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    append_print(conn, city="Tokyo", station_id="RJTT", source_channel="jma_amedas_temperature",
                 publish_ts_utc=at.isoformat(), value_native=jma, unit="C",
                 fetched_at_utc=(at + timedelta(minutes=7)).isoformat(),
                 raw_report=_route_raw("jma_amedas_temperature", "RJTT", "44166", at, jma))
    append_print(conn, city="Tokyo", station_id="RJTT", source_channel="noaa_wrh_rjtt",
                 publish_ts_utc=at.isoformat(), value_native=page, unit="C",
                 fetched_at_utc=(at + timedelta(minutes=15)).isoformat(),
                 raw_report=f"RJTT {at:%d%H%M}Z 35005KT 9999 {round(page):02d}/10 Q1013")
    append_print(conn, city="Tokyo", station_id="RJTT", source_channel="aviationweather_metar",
                 publish_ts_utc=(at + timedelta(minutes=5)).isoformat(), value_native=awc, unit="C",
                 fetched_at_utc=(at + timedelta(minutes=5, seconds=4)).isoformat(),
                 raw_report=f"RJTT {at:%d%H%M}Z 35005KT 9999 FEW030 {awc:02d}/10 Q1013")
    conn.execute("CREATE TABLE observation_instants (city TEXT, target_date TEXT, source TEXT, station_id TEXT, "
                 "local_timestamp TEXT, utc_timestamp TEXT, imported_at TEXT, temp_unit TEXT, running_max REAL, "
                 "running_min REAL, authority TEXT, training_allowed INTEGER, causality_status TEXT, "
                 "source_role TEXT, raw_response TEXT)")
    return conn


def _carrier(at: datetime, value: float, source: str):
    from tests.integration.test_w3_solve_seam_g3 import make_opportunity_event

    payload = {
        "city": "Tokyo", "target_date": "2026-10-07", "metric": "high", "temperature_metric": "high",
        "station_id": "RJTT", "configured_station_id": "RJTT", "settlement_source": source, "settlement_unit": "C",
        "observation_time": at.isoformat(), "observation_available_at": (at + timedelta(minutes=7)).isoformat(),
        "raw_value": value, "rounded_value": round(value), "high_so_far": value,
        "source_match_status": "MATCH", "local_date_status": "MATCH", "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS", "metric_match_status": "MATCH", "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED", "live_authority_status": "live",
        "raw_payload_sha256": "cd" * 32,
    }
    return make_opportunity_event(event_type="DAY0_EXTREME_UPDATED", entity_key="Tokyo|2026-10-07|high|RJTT",
                                  source="test", observed_at=at.isoformat(),
                                  available_at=(at + timedelta(minutes=7)).isoformat(),
                                  received_at=(at + timedelta(minutes=7)).isoformat(), payload=payload,
                                  causal_snapshot_id="tokyo-g1")


def test_tokyo_jma_proxy_binding_is_not_substituted_by_awc():
    """Consult (d): JMA 24.6, page 24.6, AWC 25 at 06:00Z (all settle 25). A JMA-bound conditioning must
    bind; the adapter must not treat the JMA proxy and AWC as one physical frontier."""
    from src.engine import event_reactor_adapter as era

    at = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)
    conn = _tokyo_world(jma=24.6, page=24.6, awc=25, at=at)
    conditioning = {"active": True, "metric": "high", "observation_time": at.isoformat(), "observed_extreme_c": 24.6,
                    "sample_count": 1, "source": "jma_amedas_temperature", "unit": "C"}
    payload = era._global_day0_execution_payload(
        _carrier(at, 24.6, "noaa_wrh_rjtt"),
        family=SimpleNamespace(city="Tokyo", target_date="2026-10-07", metric="high"),
        resolution=SimpleNamespace(measurement_unit="C", station_id="RJTT"),
        conditioning=conditioning, observation_conn=conn,
        decision_time=at + timedelta(minutes=20), posterior_id=760001,
    )
    assert payload["_edli_global_day0_binding"]["metric"] == "high"
    conn.close()
