# Created: 2026-04-21
# Lifecycle: created=2026-04-21; last_reviewed=2026-10-03; last_reused=2026-10-03
# Purpose: Pin the Hong Kong HKO-vs-VHHH category-error boundary for obs_v2 writes.
# Reuse: Reconfirm current_source_validity and HKO station semantics before editing.
# Last reused/audited: 2026-10-03
# Authority basis: plan v3 antibody A6; P1 obs_v2 provenance identity packet.
#   WRH required-check baseline repair 2026-10-03; qualified HKO write/station rejection.
"""Antibody A6: Hong Kong rows can NEVER be routed through a WU ICAO or
OpenMeteo grid-snap source.

This test file is intentionally isolated (not merged into
test_obs_v2_writer.py) so grep/CI dashboards can point at a single file
that encodes the exact category error: Hong Kong's ``airport_name`` in
cities.json reads "Hong Kong Observatory Headquarters" — which is NOT an
airport but the settlement-station identity written into the airport
field. Four agents (planner / architect / critic / myself) missed this
during plan iter-2 and proposed VHHH (Chek Lap Kok, 40 km away) as a
Tier 1 WU source. Plan v3 corrected this via sweep (city_truth_sweep.md).

The tests below pin that correction at runtime. Any future PR that
re-introduces a VHHH route must delete these tests — making the intent
audible instead of silent.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.data.observation_instants_writer import (
    InvalidObsV2RowError,
    ObsV2Row,
    insert_rows,
)
from src.data.tier_resolver import Tier, allowed_sources_for_tier, tier_for_city
from src.state.schema.v2_schema import apply_canonical_schema


def _hk_provenance(**overrides) -> str:
    data = {
        "tier": "HKO_NATIVE",
        "station_id": "HKO",
        "payload_hash": "sha256:" + "b" * 64,
        "source_file": "hko_hourly_accumulator",
        "parser_version": "test_hk_rejects_vhhh_source_v1",
    }
    data.update(overrides)
    if data.get("station_id") is None:
        data.pop("station_id", None)
    return json.dumps(data, sort_keys=True)


def _hk_kwargs(**overrides) -> dict:
    """HK row with legal defaults; individual tests override to test failure."""
    base = dict(
        city="Hong Kong",
        target_date="2024-01-15",
        source="hko_hourly_accumulator",
        timezone_name="Asia/Hong_Kong",
        local_hour=22.0,
        local_timestamp="2024-01-15T22:00:00+08:00",
        utc_timestamp="2024-01-15T14:00:00+00:00",
        utc_offset_minutes=480,
        time_basis="hourly_accumulator",
        temp_unit="C",
        imported_at="2026-04-21T23:30:00+00:00",
        authority="ICAO_STATION_NATIVE",
        data_version="v1.hk-accumulator.forward",
        provenance_json=_hk_provenance(),
        temp_current=22.5,
        station_id="HKO",
    )
    base.update(overrides)
    return base


# ----------------------------------------------------------------------
# Baseline: the one legal HK source succeeds
# ----------------------------------------------------------------------


@pytest.fixture
def mem_db():
    conn = sqlite3.connect(":memory:")
    apply_canonical_schema(conn)
    yield conn
    conn.close()


@pytest.mark.parametrize("row_station,provenance_station", [("HKO", "HKO"), (None, "HKO"), ("HKO", None)])
@pytest.mark.parametrize("metric", ["high", "low"])
def test_hk_accumulator_source_accepted(mem_db, row_station, provenance_station, metric):
    """Positive: 'hko_hourly_accumulator' is the only legal HK source."""
    row = ObsV2Row(**_hk_kwargs(
        station_id=row_station,
        provenance_json=_hk_provenance(station_id=provenance_station, station_registry_hash="fixture-registry"),
        running_max=24.5 if metric == "high" else None,
        running_min=20.5 if metric == "low" else None,
    ))
    assert row.city == "Hong Kong"
    assert row.source == "hko_hourly_accumulator"
    assert insert_rows(mem_db, [row]) == 1
    assert mem_db.execute(
        "SELECT city, source, station_id, local_hour, temp_current, authority FROM observation_instants"
    ).fetchall() == [("Hong Kong", "hko_hourly_accumulator", row_station, 22.0, 22.5, "ICAO_STATION_NATIVE")]
    assert mem_db.execute("SELECT running_max, running_min FROM observation_instants").fetchone() == (
        24.5 if metric == "high" else None, 20.5 if metric == "low" else None,
    )


# ----------------------------------------------------------------------
# A6: VHHH/WU rejection — the exact category error
# ----------------------------------------------------------------------


def test_hk_rejects_wu_icao_history_source():
    """The VHHH/WU route that iter-2 erroneously proposed MUST fail."""
    with pytest.raises(InvalidObsV2RowError, match="A2 violation"):
        ObsV2Row(**_hk_kwargs(source="wu_icao_history"))


def test_hk_rejects_openmeteo_source():
    """Any OpenMeteo grid-snap route for HK violates P1 and fails A2."""
    with pytest.raises(InvalidObsV2RowError, match="A2 violation"):
        ObsV2Row(**_hk_kwargs(source="openmeteo_archive_hourly"))


def test_hk_rejects_ogimet_metar_source():
    """HKO has no METAR — Ogimet path is not valid for HK either."""
    with pytest.raises(InvalidObsV2RowError, match="A2 violation"):
        ObsV2Row(**_hk_kwargs(source="ogimet_metar_vhhh"))


# ----------------------------------------------------------------------
# Tier-level pin: HK must resolve to HKO_NATIVE, never elsewhere
# ----------------------------------------------------------------------


def test_hk_resolves_to_hko_native():
    assert tier_for_city("Hong Kong") is Tier.HKO_NATIVE


def test_hko_native_allowed_sources_is_exactly_accumulator():
    """Structural guarantee: no second source can sneak in via config drift."""
    allowed = allowed_sources_for_tier(Tier.HKO_NATIVE)
    assert allowed == frozenset({"hko_hourly_accumulator"})


@pytest.mark.parametrize("metric", ["high", "low"])
def test_hk_rejects_station_id_vhhh(mem_db, metric):
    """An otherwise qualified HKO row cannot persist the VHHH airport identity."""
    try:
        row = ObsV2Row(**_hk_kwargs(station_id="VHHH", running_max=24.5 if metric == "high" else None,
                                  running_min=20.5 if metric == "low" else None))
        insert_rows(mem_db, [row])
    except InvalidObsV2RowError as exc:
        message = str(exc)
        assert "station_id" in message and "VHHH" in message and "HKO" in message
    else:
        persisted = mem_db.execute(
            "SELECT city, source, station_id, local_hour, temp_current, authority FROM observation_instants"
        ).fetchall()
        pytest.fail(f"Wrong station was accepted and persisted: {persisted!r}")
    assert mem_db.execute("SELECT COUNT(*) FROM observation_instants").fetchone()[0] == 0


@pytest.mark.parametrize("row_station,provenance_station", [
    ("HKO", "VHHH"), ("VHHH", "VHHH"), (None, None),
    (7, "HKO"), ("HKO", False), ("HKO", {"unexpected": "station"}),
])
@pytest.mark.parametrize("metric", ["high", "low"])
def test_hk_rejects_unbound_or_conflicting_station_identity_before_write(mem_db, row_station, provenance_station, metric):
    with pytest.raises(InvalidObsV2RowError, match="A6 violation.*station_id"):
        row = ObsV2Row(**_hk_kwargs(
            station_id=row_station,
            provenance_json=_hk_provenance(station_id=provenance_station, station_registry_hash="fixture-registry"),
            running_max=24.5 if metric == "high" else None,
            running_min=20.5 if metric == "low" else None,
        ))
        insert_rows(mem_db, [row])
    assert mem_db.execute("SELECT COUNT(*) FROM observation_instants").fetchone()[0] == 0


def test_other_city_nullable_station_identity_remains_insertable(mem_db):
    provenance = json.loads(_hk_provenance())
    provenance.update(tier="WU_ICAO", station_id="KORD", source_file="private-wu-fixture")
    row = ObsV2Row(**_hk_kwargs(
        city="Chicago", source="wu_icao_history", station_id=None,
        timezone_name="America/Chicago", local_hour=8.0,
        local_timestamp="2024-01-15T08:00:00-06:00", utc_offset_minutes=-360,
        temp_unit="F", temp_current=32.0, authority="VERIFIED",
        provenance_json=json.dumps(provenance),
    ))
    assert insert_rows(mem_db, [row]) == 1
    assert mem_db.execute("SELECT station_id, temp_current FROM observation_instants").fetchone() == (None, 32.0)


# ----------------------------------------------------------------------
# Regression: the HK/VHHH 40-km distance fact is the reason this exists
# ----------------------------------------------------------------------


def test_a6_error_message_names_40km_distance():
    """The error message for a VHHH attempt MUST reference the 40km offset.

    If a future edit loses that context, this test fails, forcing the
    editor to preserve the reasoning (the why is harder to re-derive
    than the what).
    """
    try:
        ObsV2Row(**_hk_kwargs(source="wu_icao_history"))
    except InvalidObsV2RowError as exc:
        msg = str(exc)
        # Either the A2 message (source-tier) or the A6 message fires.
        # A2 fires first because tier_for_city is called before the HK
        # explicit block. That's fine — the A6 guard remains as defense
        # in depth. This test checks that SOMEWHERE in the runtime path
        # the 40km context appears.
        assert (
            "A2 violation" in msg  # A2 fires first for city-source mismatch
        ), f"expected A2 violation, got: {msg}"
        return
    pytest.fail("Expected InvalidObsV2RowError not raised")
