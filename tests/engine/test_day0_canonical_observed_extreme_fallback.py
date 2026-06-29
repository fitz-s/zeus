# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: docs/evidence/same_day_exit_blindness/2026-06-23_toronto_total_loss.md
#   (Toronto NO@24 -98.94% total loss) + consult REQ-20260623-174044-18fe71. Fix (B): the canonical
#   world.observation_instants WU-hourly surface is the AUTHORITATIVE settlement-grade day0 observed
#   extreme — the hard-fact exit lane (day0_hard_fact_exit._durable_observation_instants_extremes)
#   and the day0_extreme_updated trigger both read it as their truth. The monitor belief reseed,
#   however, sourced the observed extreme ONLY from a live-provider fetch
#   (_fetch_day0_observation -> get_current_observation) which routinely fails on the settlement day
#   ("All observation providers failed for <city>/<date>"), so _day0_observed_extreme_reseed_payload
#   returned {} for EVERY same-day family today (day0_observed_extreme=None in the live log) -> no
#   day0 conditioning -> frozen belief -> blind exit. This reader makes the reseed consume the SAME
#   canonical surface the rest of the day0 lane trusts, and returns the OBSERVATION VERSION so fix
#   (A)'s observation-version idempotency re-materializes on each fresh observed-extreme version.
"""Antibody: the day0 belief reseed reads the observed running extreme from the canonical
settlement-grade world.observation_instants surface (the same source the hard-fact lane uses),
returning the observation version for downstream observation-version idempotency."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.engine import monitor_refresh

UTC = timezone.utc
_NOW = datetime(2026, 6, 23, 23, 0, 0, tzinfo=UTC)

_COLS = (
    "city", "target_date", "local_timestamp", "utc_timestamp",
    "running_max", "running_min", "authority", "causality_status", "source", "temperature_metric",
    "training_allowed", "source_role",
)


def _obs_conn(rows: list[tuple]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE observation_instants ("
        "city TEXT, target_date TEXT, local_timestamp TEXT, utc_timestamp TEXT, "
        "running_max REAL, running_min REAL, authority TEXT, causality_status TEXT, "
        "source TEXT, temperature_metric TEXT, training_allowed INTEGER, source_role TEXT)"
    )
    normalized_rows = []
    for row in rows:
        if len(row) == len(_COLS):
            normalized_rows.append(row)
            continue
        source = str(row[8] if len(row) > 8 else "")
        if source == "hko_hourly_accumulator":
            normalized_rows.append((*row, 0, "runtime_monitoring"))
        elif source.startswith("wu") or source.startswith("ogimet_metar_"):
            normalized_rows.append((*row, 1, "historical_hourly"))
        else:
            normalized_rows.append((*row, 0, "coverage_fill_evidence"))
    conn.executemany(
        f"INSERT INTO observation_instants ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
        normalized_rows,
    )
    return conn


def test_canonical_reader_returns_running_max_and_observation_version():
    """The Toronto incident: the canonical WU-hourly surface holds the running_max climbing to 24
    by 21:00 UTC. The reader returns the MAX extreme AND its latest observation version."""
    conn = _obs_conn([
        ("Toronto", "2026-06-23", "2026-06-23T13:00", "2026-06-23T17:00:00+00:00",
         22.0, 14.0, "VERIFIED", "OK", "wu_icao_history", "high"),
        ("Toronto", "2026-06-23", "2026-06-23T17:00", "2026-06-23T21:00:00+00:00",
         24.0, 14.0, "VERIFIED", "OK", "wu_icao_history", "high"),
    ])
    out = monitor_refresh._day0_observed_extreme_from_canonical_surface(
        city_name="Toronto", target_date="2026-06-23", metric_is_low=False,
        now=_NOW, world_conn=conn,
    )
    assert out is not None
    extreme, obs_time, n_rows = out
    assert extreme == 24.0
    assert obs_time == "2026-06-23T21:00:00+00:00"
    assert n_rows == 2


def test_canonical_reader_rejects_non_verified_non_wu_rows():
    """Only the VERIFIED WU settlement reference is admissible; a METAR/unverified row is not a
    settlement-grade observed extreme for the belief reseed."""
    conn = _obs_conn([
        ("Toronto", "2026-06-23", "2026-06-23T17:00", "2026-06-23T21:00:00+00:00",
         24.0, 14.0, "FALLBACK_EVIDENCE", "OK", "metar", "high"),
    ])
    out = monitor_refresh._day0_observed_extreme_from_canonical_surface(
        city_name="Toronto", target_date="2026-06-23", metric_is_low=False,
        now=_NOW, world_conn=conn,
    )
    assert out is None


def test_canonical_reader_low_metric_uses_running_min():
    """LOW markets condition on the monotone running minimum."""
    conn = _obs_conn([
        ("London", "2026-06-19", "2026-06-19T05:00", "2026-06-19T10:00:00+00:00",
         None, 18.0, "VERIFIED", "OK", "wu_icao_history", "low"),
        ("London", "2026-06-19", "2026-06-19T08:00", "2026-06-19T13:00:00+00:00",
         None, 16.0, "VERIFIED", "OK", "wu_icao_history", "low"),
    ])
    out = monitor_refresh._day0_observed_extreme_from_canonical_surface(
        city_name="London", target_date="2026-06-19", metric_is_low=True,
        now=datetime(2026, 6, 19, 23, 0, 0, tzinfo=UTC), world_conn=conn,
    )
    assert out is not None
    extreme, obs_time, n_rows = out
    assert extreme == 16.0
    assert obs_time == "2026-06-19T13:00:00+00:00"


def test_canonical_reader_accepts_hko_hourly_accumulator_for_hong_kong_low():
    """Hong Kong is not WU-backed; its canonical Day0 surface is the HKO accumulator."""
    conn = _obs_conn([
        ("Hong Kong", "2026-06-26", "2026-06-26T04:00:00+08:00", "2026-06-25T20:00Z",
         28.0, 28.0, "ICAO_STATION_NATIVE", "OK", "hko_hourly_accumulator", "low"),
        ("Hong Kong", "2026-06-26", "2026-06-26T07:00:00+08:00", "2026-06-25T23:00Z",
         27.0, 27.0, "ICAO_STATION_NATIVE", "OK", "hko_hourly_accumulator", "low"),
    ])

    out = monitor_refresh._day0_observed_extreme_from_canonical_surface(
        city_name="Hong Kong",
        target_date="2026-06-26",
        metric_is_low=True,
        now=datetime(2026, 6, 26, 1, 0, 0, tzinfo=UTC),
        world_conn=conn,
    )

    assert out is not None
    extreme, obs_time, n_rows = out
    assert extreme == 27.0
    assert obs_time == "2026-06-25T23:00Z"
    assert n_rows == 2


def test_canonical_reader_rejects_hko_reaudit_rows():
    conn = _obs_conn([
        ("Hong Kong", "2026-06-26", "2026-06-26T07:00:00+08:00", "2026-06-25T23:00Z",
         27.0, 27.0, "ICAO_STATION_NATIVE", "REQUIRES_SOURCE_REAUDIT",
         "hko_hourly_accumulator", "low", 0, "coverage_fill_evidence"),
    ])

    out = monitor_refresh._day0_observed_extreme_from_canonical_surface(
        city_name="Hong Kong",
        target_date="2026-06-26",
        metric_is_low=True,
        now=datetime(2026, 6, 26, 1, 0, 0, tzinfo=UTC),
        world_conn=conn,
    )

    assert out is None


def test_canonical_reader_excludes_future_observations_after_now():
    """Causality: an observation stamped after `now` must not be consumed (no look-ahead)."""
    conn = _obs_conn([
        ("Toronto", "2026-06-23", "2026-06-23T13:00", "2026-06-23T17:00:00+00:00",
         22.0, 14.0, "VERIFIED", "OK", "wu_icao_history", "high"),
        ("Toronto", "2026-06-23", "2026-06-23T20:00", "2026-06-24T00:00:00+00:00",
         26.0, 14.0, "VERIFIED", "OK", "wu_icao_history", "high"),
    ])
    out = monitor_refresh._day0_observed_extreme_from_canonical_surface(
        city_name="Toronto", target_date="2026-06-23", metric_is_low=False,
        now=datetime(2026, 6, 23, 18, 0, 0, tzinfo=UTC), world_conn=conn,
    )
    assert out is not None
    extreme, obs_time, _ = out
    assert extreme == 22.0  # the 26.0 @ 00:00 next-day stamp is after now=18:00, excluded
    assert obs_time == "2026-06-23T17:00:00+00:00"


def test_canonical_reader_none_when_no_eligible_rows():
    conn = _obs_conn([])
    out = monitor_refresh._day0_observed_extreme_from_canonical_surface(
        city_name="Toronto", target_date="2026-06-23", metric_is_low=False,
        now=_NOW, world_conn=conn,
    )
    assert out is None


# ---------------------------------------------------------------------------
# Absorbing-law composition of live + canonical (consult REQ-20260623-184115 BLOCKER):
# canonical is the settlement-grade hard bound; a live reading may only IMPROVE the
# absorbing extreme (raise the high / lower the low), never UNDERCUT it. A stale/lower live
# value must NOT suppress the canonical surface and materialize a fresh-but-wrong belief.
# live tuple = (native, observation_time, source, sample_count); canonical = (native, time, n).
# ---------------------------------------------------------------------------
def test_compose_stale_lower_live_does_not_undercut_canonical_high():
    """THE BLOCKER: live high-so-far 23 @ 22:00 must NOT override canonical running_max 24 @ 21:00."""
    composed = monitor_refresh._compose_day0_observed_extreme(
        live=(23.0, "2026-06-23T22:00:00+00:00", "wu_live", 5),
        canonical=(24.0, "2026-06-23T21:00:00+00:00", 2),
        metric_is_low=False,
    )
    assert composed is not None
    native, obs_time, source, _ = composed
    assert native == 24.0  # absorbing max, canonical wins — NOT the stale 23
    assert obs_time == "2026-06-23T21:00:00+00:00"  # the dominant source's version
    assert source == "durable_observation_instants"


def test_compose_higher_live_improves_absorbing_extreme_high():
    """A live reading ABOVE canonical legitimately raises the absorbing high."""
    composed = monitor_refresh._compose_day0_observed_extreme(
        live=(25.0, "2026-06-23T22:00:00+00:00", "wu_live", 5),
        canonical=(24.0, "2026-06-23T21:00:00+00:00", 2),
        metric_is_low=False,
    )
    native, obs_time, source, _ = composed
    assert native == 25.0
    assert obs_time == "2026-06-23T22:00:00+00:00"
    assert source == "wu_live"


def test_compose_low_metric_live_above_does_not_undercut_canonical_min():
    """LOW: a live low 17 must not raise the absorbing min above canonical 16."""
    composed = monitor_refresh._compose_day0_observed_extreme(
        live=(17.0, "2026-06-19T13:00:00+00:00", "wu_live", 5),
        canonical=(16.0, "2026-06-19T12:00:00+00:00", 2),
        metric_is_low=True,
    )
    native, obs_time, _, _ = composed
    assert native == 16.0  # absorbing min
    assert obs_time == "2026-06-19T12:00:00+00:00"


def test_compose_tie_prefers_later_observation_version():
    """Equal extremes (plateau across both sources) -> the LATER observation version, so a fresh
    plateau observation still advances the version and re-materializes."""
    composed = monitor_refresh._compose_day0_observed_extreme(
        live=(24.0, "2026-06-23T22:00:00+00:00", "wu_live", 5),
        canonical=(24.0, "2026-06-23T21:00:00+00:00", 2),
        metric_is_low=False,
    )
    native, obs_time, _, _ = composed
    assert native == 24.0
    assert obs_time == "2026-06-23T22:00:00+00:00"  # later time


def test_compose_single_source_each():
    assert monitor_refresh._compose_day0_observed_extreme(
        live=None, canonical=(24.0, "2026-06-23T21:00:00+00:00", 2), metric_is_low=False
    ) == (24.0, "2026-06-23T21:00:00+00:00", "durable_observation_instants", 2)
    assert monitor_refresh._compose_day0_observed_extreme(
        live=(23.0, "2026-06-23T22:00:00+00:00", "wu_live", 5), canonical=None, metric_is_low=False
    ) == (23.0, "2026-06-23T22:00:00+00:00", "wu_live", 5)
    assert monitor_refresh._compose_day0_observed_extreme(
        live=None, canonical=None, metric_is_low=False
    ) is None
