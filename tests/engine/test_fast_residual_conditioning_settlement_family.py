"""The fast-residual consumer must admit both station-settled families.

`build_fast_station_residual_likelihood` (src/data/day0_fast_obs.py) measures the
residual against the channel that actually SETTLES the city -- `wu_icao_history`
for wu_icao, `noaa_wrh_<icao>` for noaa -- and stamps that channel into both the
payload field and the identity hash. The consumer
`_validated_fast_residual_day0_conditioning` demanded the literal
`wu_icao_history` in both places, so every NOAA posterior the producer
legitimately stamped was rejected as
GLOBAL_DAY0_FAST_RESIDUAL_POSTERIOR_IDENTITY_INVALID.

Live consequence (2026-09-18): Shanghai NO ran 169 consecutive
BELIEF_AUTHORITY_FAULT monitor cycles -- "the exit organ is blind on a live
position" -- while the market price stayed fresh, which held the deploy restart
guard and left entries paused. Replaying the shipped producer over live captures
gave 2 validated / 15 rejected before this fix and 17 / 0 after; the two
validated rows in both runs are the wu_icao cities, so the WU path is unchanged.
"""
import hashlib
import json

import pytest

from src.data.day0_fast_obs import (
    FAST_OBS_SOURCE_ID,
    FAST_RESIDUAL_LIKELIHOOD_REVISION,
    FAST_RESIDUAL_MIN_PAIRS,
)
from src.engine.event_reactor_adapter import (
    _validated_fast_residual_day0_conditioning as validate,
)

STATION = "ZSPD"
AS_OF = "2026-09-18T06:05:00+00:00"
WINDOW_START = "2026-09-11T06:05:00+00:00"
UNKNOWN_WEIGHT = 0.1
RESIDUALS = [{"residual_c": 0.0, "weight": 1.0 - UNKNOWN_WEIGHT}]


def _conditioning(*, settlement_channel: str, station_id: str = STATION) -> dict:
    identity = {
        "semantics_revision": FAST_RESIDUAL_LIKELIHOOD_REVISION,
        "station_id": station_id,
        "settlement_channel": settlement_channel,
        "fast_channel": FAST_OBS_SOURCE_ID,
        "unit": "C",
        "as_of": AS_OF,
        "window_start": WINDOW_START,
        "matched_pairs": FAST_RESIDUAL_MIN_PAIRS,
        "residual_weights_c": tuple(
            (row["residual_c"], row["weight"]) for row in RESIDUALS
        ),
        "unknown_weight": UNKNOWN_WEIGHT,
        "settlement_extreme_c": None,
    }
    identity_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "active": True,
        "source": FAST_OBS_SOURCE_ID,
        "metric": "low",
        "unit": "C",
        "observation_time": AS_OF,
        "observed_extreme_c": 25.0,
        "sample_count": FAST_RESIDUAL_MIN_PAIRS,
        "support_truncation": False,
        "fast_residual_likelihood": {
            "station_id": station_id,
            "settlement_channel": settlement_channel,
            "fast_channel": FAST_OBS_SOURCE_ID,
            "semantics_revision": FAST_RESIDUAL_LIKELIHOOD_REVISION,
            "unit": "C",
            "as_of": AS_OF,
            "window_start": WINDOW_START,
            "matched_pairs": FAST_RESIDUAL_MIN_PAIRS,
            "unknown_weight": UNKNOWN_WEIGHT,
            "settlement_extreme_c": None,
            "residual_weights_c": RESIDUALS,
            "scenario_weights": [{"observed_bound_c": None, "weight": 1.0}],
            "identity_hash": identity_hash,
            "support_truncation": False,
        },
    }


def test_noaa_station_page_channel_is_admitted():
    conditioning = _conditioning(settlement_channel=f"noaa_wrh_{STATION.lower()}")
    validated = validate(conditioning)
    assert validated is conditioning


def test_wu_history_channel_stays_admitted():
    conditioning = _conditioning(settlement_channel="wu_icao_history")
    assert validate(conditioning) is conditioning


def test_another_stations_noaa_page_is_rejected():
    # The channel must name THIS likelihood's own station: a residual measured
    # against a different city's settlement page is not evidence about this one.
    conditioning = _conditioning(settlement_channel="noaa_wrh_kord")
    with pytest.raises(ValueError, match="FAST_RESIDUAL_POSTERIOR_IDENTITY_INVALID"):
        validate(conditioning)


def test_an_unknown_channel_family_is_rejected():
    conditioning = _conditioning(settlement_channel=f"ogimet_metar_{STATION.lower()}")
    with pytest.raises(ValueError, match="FAST_RESIDUAL_POSTERIOR_IDENTITY_INVALID"):
        validate(conditioning)


def test_the_channel_is_bound_into_the_identity_hash():
    # A payload whose hash was computed over the WU literal must not pass just
    # because the field now says noaa_wrh_<icao> -- the hash covers the channel.
    wu = _conditioning(settlement_channel="wu_icao_history")
    forged = _conditioning(settlement_channel=f"noaa_wrh_{STATION.lower()}")
    forged["fast_residual_likelihood"]["identity_hash"] = wu[
        "fast_residual_likelihood"
    ]["identity_hash"]
    with pytest.raises(ValueError, match="FAST_RESIDUAL_POSTERIOR_IDENTITY_INVALID"):
        validate(forged)


def test_a_stationless_likelihood_is_rejected():
    conditioning = _conditioning(settlement_channel="wu_icao_history", station_id="")
    with pytest.raises(ValueError, match="FAST_RESIDUAL_POSTERIOR_IDENTITY_INVALID"):
        validate(conditioning)
