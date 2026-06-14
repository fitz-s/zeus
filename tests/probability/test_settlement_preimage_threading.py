# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md ([HIGH] settlement preimage
#   can be dropped at EMOS seam; Stage 1 RED-on-revert
#   test_hk_oracle_truncate_reaches_emos_and_band_builders) +
#   docs/rebuild/q_engine_violation_ledger.md V3/V4 (HK rounding_rule dropped,
#   defaults to WMO). q-kernel rebuild Stage 1.
"""Settlement-preimage threading: the per-city rounding rule reaches q builders.

For THIS stage the testable contract is that EventResolution carries the city's
REAL rounding rule sourced from settlement_semantics (not a WMO default):
  * Hong Kong (hko)  -> oracle_truncate
  * every other city -> wmo_half_up

and that the carried rule, threaded into ``settlement_preimage_offsets`` (the one
declarative preimage source every q-integration / band builder derives its
integration bounds from), produces the ASYMMETRIC HK truncation preimage
``(0.0, +1.0)`` rather than the symmetric WMO ``(-0.5, +0.5)``. This is exactly
the byte-difference the EMOS seam dropped (V3/V4): using the WMO preimage for HK
shifts every HK bin's mass up by ~half a bin.

RED-on-revert: if EventResolution defaults the rounding rule (or HK regresses to
wmo_half_up), HK's resolution rule and preimage collapse onto WMO and the
asymmetry assertions fail.
"""
from __future__ import annotations

from datetime import date

import pytest

from src.config import City
from src.contracts.settlement_semantics import settlement_preimage_offsets
from src.probability.event_resolution import event_resolution_for_city


def _city(name, source_type, *, wu_station, unit="C"):
    return City(
        name=name,
        lat=22.3,
        lon=114.17,
        timezone="Asia/Hong_Kong" if source_type == "hko" else "Asia/Tokyo",
        settlement_unit=unit,
        cluster="asia",
        wu_station=wu_station,
        settlement_source_type=source_type,
    )


def test_hk_oracle_truncate_reaches_emos_and_band_builders():
    # --- Hong Kong settles by oracle truncation, sourced from settlement_semantics ---
    hk_res = event_resolution_for_city(
        _city("Hong Kong", "hko", wu_station=""), date(2026, 6, 14), "high"
    )
    assert hk_res.rounding_rule == "oracle_truncate"
    assert hk_res.station_id == "HKO_HQ"  # not "None", not WU

    # The HK rule, threaded into the shared preimage source the EMOS and band
    # builders both consume, is the ASYMMETRIC truncation preimage [t, t+1).
    hk_offsets = settlement_preimage_offsets(hk_res.rounding_rule)
    assert hk_offsets == (0.0, 1.0)

    # --- a WU °C city settles WMO half-up: symmetric [t-0.5, t+0.5) ---
    tokyo_res = event_resolution_for_city(
        _city("Tokyo", "wu_icao", wu_station="RJTT"), date(2026, 6, 14), "high"
    )
    assert tokyo_res.rounding_rule == "wmo_half_up"
    wmo_offsets = settlement_preimage_offsets(tokyo_res.rounding_rule)
    assert wmo_offsets == (-0.5, 0.5)

    # --- the two rules are genuinely different at the integration boundary ---
    # HK's lower bound starts at the bin label (no -0.5 shift), so HK preimage
    # mass sits strictly to the RIGHT of the WMO preimage for the same label.
    assert hk_offsets != wmo_offsets
    assert hk_offsets[0] > wmo_offsets[0]  # 0.0 > -0.5: HK does not round down half a degree

    # The rule is sourced from settlement_semantics, NOT a builder default: a
    # non-WU non-HK source (CWA) still resolves to wmo_half_up via for_city.
    cwa_res = event_resolution_for_city(
        _city("Taipei", "cwa_station", wu_station="RCSS"), date(2026, 6, 14), "high"
    )
    assert cwa_res.rounding_rule == "wmo_half_up"


def test_event_resolution_fails_closed_on_missing_station():
    from src.probability.event_resolution import ResolutionError

    bad = _city("Nowhere", "wu_icao", wu_station="")  # WU city with no station
    with pytest.raises(ResolutionError):
        event_resolution_for_city(bad, date(2026, 6, 14), "high")
