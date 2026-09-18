"""Grid-to-station provenance is owed by every family that settles off a station.

`_station_grid_provenance_reason` is an input-authority gate on the live forecast-admission
path (`read_executable_forecast_snapshot`, executable_forecast_reader.py:840, and the twin
at ecmwf_open_data.py:2089): an OpenData row may not go live-executable unless it proves
which grid point represented the settlement station. It required that proof only for
`wu_icao`, so after the 2026-09-12 migration 48 of 53 cities were admitted with no
grid-to-station evidence checked at all — the docstring's own premise ("WU airport-settled
markets") had gone stale, since noaa settles off an ICAO airport station just as WU does.

Widening a gate that BLOCKS rows is only safe if it blocks nothing real. Replayed over 1,200
live `ensemble_snapshots` rows before landing: 1069 noaa / 108 wu_icao / 23 hko, every one
passing, zero blocked — the fields are populated in practice, so this closes an unchecked
path rather than creating a blackout.
"""
from __future__ import annotations

import pytest

from src.data.executable_forecast_reader import (
    _STATION_SETTLED_SOURCE_TYPES,
    _station_grid_provenance_reason,
)

MISSING = "EXECUTABLE_FORECAST_STATION_GRID_PROVENANCE_MISSING"


def _row(source_type: str, *, grid: bool) -> dict:
    """A snapshot row shaped as the gate reads it."""
    provenance = {"contract_outcome_evidence": {"settlement_source_type": source_type}}
    if grid:
        provenance.update(
            {
                "nearest_grid_lat": 40.1,
                "nearest_grid_lon": 32.9,
                "nearest_grid_distance_km": 3.2,
            }
        )
    import json

    return {
        "dataset_id": "ecmwf_opendata_ens",
        "settlement_source_type": source_type,
        "provenance_json": json.dumps(provenance),
    }


def test_noaa_row_without_grid_provenance_is_now_refused():
    """The defect: 48 cities were admitted with the proof unchecked."""
    assert _station_grid_provenance_reason(_row("noaa", grid=False)) == MISSING


def test_noaa_row_with_grid_provenance_passes():
    """Populated rows — which is every live row sampled — stay admitted."""
    assert _station_grid_provenance_reason(_row("noaa", grid=True)) is None


def test_wu_row_contract_is_unchanged_in_both_directions():
    """Widening must not alter the family the gate already covered."""
    assert _station_grid_provenance_reason(_row("wu_icao", grid=False)) == MISSING
    assert _station_grid_provenance_reason(_row("wu_icao", grid=True)) is None


def test_a_family_with_no_station_is_exempt():
    """hko names no station, so there is no station to prove a grid against."""
    assert _station_grid_provenance_reason(_row("hko", grid=False)) is None


def test_a_non_opendata_row_is_out_of_scope():
    """The gate only governs OpenData rows; other datasets are untouched."""
    row = _row("noaa", grid=False)
    row["dataset_id"] = "tigge_ens"
    assert _station_grid_provenance_reason(row) is None


def test_both_twins_agree_on_the_family_set():
    """Two implementations of one rule must not drift apart."""
    import inspect

    from src.data import ecmwf_open_data

    twin = inspect.getsource(ecmwf_open_data._station_grid_provenance_reason)
    assert '"wu_icao", "noaa"' in twin, twin
    assert _STATION_SETTLED_SOURCE_TYPES == frozenset({"wu_icao", "noaa"})
