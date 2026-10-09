# Lifecycle: created=2026-10-06; last_reviewed=2026-10-09; last_reused=2026-10-09
# Purpose: Preserve qualified complete-product revision identity through current-state delivery.
# Reuse: Run when WRH current membership, current path identity or fusion delivery changes.
"""Real WRH owner/reader identity tests; these do not manufacture a posterior."""
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import sqlite3

import pytest

from src.config import cities_by_name
from src.data.daily_obs_append import append_current_noaa_wrh_product
from src.data.day0_hourly_vectors import read_day0_current_temperature_state
from src.data import noaa_wrh_timeseries as wrh
from src.data import replacement_fusion_upgrade_trigger as fusion
from tests.test_noaa_wrh_settlement_product import (
    _attached, _current_product, _live_schema_db_pair,
)


@pytest.mark.parametrize("alternative_point", [False, True])
@pytest.mark.parametrize("values", [(29.0, 28.0), (33.0, 28.0)])
def test_older_extreme_revision_changes_actual_current_state_delivery(
    tmp_path, monkeypatch, values, alternative_point,
):
    city = cities_by_name["Singapore"]
    forecasts, world = _live_schema_db_pair(tmp_path)
    if alternative_point:
        from src.state.schema.observation_prints_schema import ensure_table, append_print
        with sqlite3.connect(world) as observations:
            ensure_table(observations)
            append_print(
                observations, city=city.name, station_id="WSSS", source_channel="aviationweather_metar",
                publish_ts_utc="2026-10-06T01:30:00+00:00", value_native=30.0, unit="C",
                fetched_at_utc="2026-10-06T01:30:30+00:00",
                raw_report="METAR WSSS 060130Z 00000KT CAVOK 30/25 Q1010",
            )
    conn = _attached(forecasts, world)
    first = _current_product(values=(32.0, 28.0))
    now = first.station_reference.fetched_at

    def read_world():
        reader = sqlite3.connect(f"file:{world}?mode=ro", uri=True)
        reader.execute("ATTACH DATABASE ? AS forecasts", (f"file:{forecasts}?mode=ro",))
        return reader

    monkeypatch.setattr("src.state.db.get_world_connection_read_only", read_world)
    try:
        assert append_current_noaa_wrh_product(
            conn, city=city, target_date="2026-10-06", product=first, as_of=now,
        ) == "inserted"
        conn.commit()
        before = read_day0_current_temperature_state(
            conn=conn, city=city, target_date="2026-10-06", decision_time=now,
        ).identity()
        assert before["source"] == ("aviationweather_metar" if alternative_point else "noaa_wrh_wsss")
        later = now + timedelta(minutes=1)
        corrected = _current_product(values=values, receipt=later.isoformat())
        assert append_current_noaa_wrh_product(
            conn, city=city, target_date="2026-10-06", product=corrected, as_of=later,
        ) == "revision"
        conn.commit()
        after = fusion._capturable_current_temperature_state(
            city=city.name, target_date="2026-10-06", decision_time=later,
        )
        assert {k: before[k] for k in ("value_native", "observed_at_utc", "source")} == {
            k: after[k] for k in ("value_native", "observed_at_utc", "source")
        }
        assert before != after
        assert before["source_revision_identity"] == first.response_sha256
        assert after["source_revision_identity"] == corrected.response_sha256

        # The changed R remains real even when selected scalar conditioning
        # does not consume it. Prior posterior shape cannot grant carrier
        # admission; the canonical currently selected source owns that route.
        monkeypatch.setattr(fusion, "_latest_posterior_inputs", lambda *_a, **_k: (
            now.isoformat(), frozenset(), {}, frozenset(), frozenset(), None, (),
            False, False, before, True, False, {}, frozenset(),
        ))
        monkeypatch.setattr(fusion, "_capturable_inputs_for_scope", lambda *_a, **_k: {})
        result = fusion.scope_capture_offers_larger_provider_set(
            conn, city=city.name, target_date="2026-10-06", metric="high",
            decision_time=later, changed_sources=("day0_current_temperature_state",),
        )
        assert result["is_upgrade"]
        from src.data.replacement_forecast_seed_discovery import _day0_observed_extreme_seed_payload
        from src.data.replacement_cycle_advance_trigger import _day0_conditioning_identity
        from src.events.day0_authority import day0_is_carrier_source
        payload = _day0_observed_extreme_seed_payload(city=city.name,
            target_date="2026-10-06", metric="high", computed_at=later)
        assert payload is not None
        if day0_is_carrier_source(payload["day0_observed_extreme_source"]):
            assert result["changed_input_revisions"]["day0_current_temperature_state"] == after
        else:
            assert "day0_current_temperature_state" not in result["changed_input_revisions"]
            assert result["changed_input_revisions"]["day0_scalar_conditioning"] == _day0_conditioning_identity(
                source=payload["day0_observed_extreme_source"],
                observation_time=payload["day0_observed_extreme_observation_time"],
                observed_extreme_c=payload["day0_observed_extreme_c"],
                unit=payload["day0_observed_extreme_unit"],
            )
    finally:
        conn.close()


def test_transport_confirmation_does_not_change_current_revision_or_source_clock(tmp_path):
    city = cities_by_name["Singapore"]
    conn = _attached(*_live_schema_db_pair(tmp_path))
    first = _current_product()
    now = first.station_reference.fetched_at
    try:
        append_current_noaa_wrh_product(
            conn, city=city, target_date="2026-10-06", product=first, as_of=now,
        )
        before = read_day0_current_temperature_state(
            conn=conn, city=city, target_date="2026-10-06", decision_time=now,
        )
        payload = json.loads(first.native_body)
        payload["SUMMARY"]["response_duration"] = 0.25
        body = json.dumps(payload).encode()
        later = now + timedelta(minutes=1)
        confirmation = replace(
            wrh.product_from_response(
                body, "WSSS", unit="C", fetched_at=later,
                source_response_sha256=hashlib.sha256(body).hexdigest(),
            ),
            request_started_at=later - timedelta(seconds=1),
            coverage_start_utc=first.coverage_start_utc,
            coverage_end_utc=later - timedelta(seconds=1),
        )
        assert confirmation.response_sha256 != first.response_sha256
        assert append_current_noaa_wrh_product(
            conn, city=city, target_date="2026-10-06", product=confirmation, as_of=later,
        ) == "noop"
        after = read_day0_current_temperature_state(
            conn=conn, city=city, target_date="2026-10-06", decision_time=later,
        )
        assert after.identity() == before.identity()
        assert after.clock_evidence == before.clock_evidence
    finally:
        conn.close()
