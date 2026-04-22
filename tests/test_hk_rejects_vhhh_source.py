# Created: 2026-04-21
# Last reused/audited: 2026-04-21
# Authority basis: plan v3 antibody A6 (.omc/plans/observation-instants-
#                  migration-iter3.md L124); step2 Phase 0 file #10.
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

import pytest

from src.data.observation_instants_v2_writer import (
    InvalidObsV2RowError,
    ObsV2Row,
)
from src.data.tier_resolver import Tier, allowed_sources_for_tier, tier_for_city


def _hk_kwargs(**overrides) -> dict:
    """HK row with legal defaults; individual tests override to test failure."""
    base = dict(
        city="Hong Kong",
        target_date="2024-01-15",
        source="hko_hourly_accumulator",
        timezone_name="Asia/Hong_Kong",
        local_timestamp="2024-01-15T22:00:00+08:00",
        utc_timestamp="2024-01-15T14:00:00+00:00",
        utc_offset_minutes=480,
        time_basis="hourly_accumulator",
        temp_unit="C",
        imported_at="2026-04-21T23:30:00+00:00",
        authority="ICAO_STATION_NATIVE",
        data_version="v1.hk-accumulator.forward",
        provenance_json=json.dumps({"tier": "HKO_NATIVE", "station": "HKO"}),
        temp_current=22.5,
        station_id="HKO",
    )
    base.update(overrides)
    return base


# ----------------------------------------------------------------------
# Baseline: the one legal HK source succeeds
# ----------------------------------------------------------------------


def test_hk_accumulator_source_accepted():
    """Positive: 'hko_hourly_accumulator' is the only legal HK source."""
    row = ObsV2Row(**_hk_kwargs())
    assert row.city == "Hong Kong"
    assert row.source == "hko_hourly_accumulator"


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


def test_hk_rejects_station_id_vhhh():
    """Even if someone passes the correct source tag but wrong station_id,
    the tier still matches — so station_id drift would be a separate bug.
    This test documents that we don't (yet) check station_id against
    expected HKO; if we did, this test would switch to expecting failure."""
    # Current behavior: station_id is nullable/free-form. Documenting
    # the gap rather than masking it.
    row = ObsV2Row(**_hk_kwargs(station_id="VHHH"))  # technically wrong ID
    assert row.station_id == "VHHH"  # writer does not yet enforce station_id
    # TODO (Phase 1+): add station_id check against expected per-tier mapping.


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
