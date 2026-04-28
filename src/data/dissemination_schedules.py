"""Per-source forecast dissemination schedules.

Maps each forecast source string (as stored in `forecasts.source` column)
to a deterministic `(base_time, lead_day) -> available_at` function plus
the provenance tier the derivation carries.

Used by F11 antibody (packet 2026-04-28) to populate the NULL
`forecast_issue_time` field on existing rows and to assert non-NULL on
new writer inserts. Sources whose dissemination schedule is not yet
verified at primary source carry `RECONSTRUCTED` provenance and are
rejected by SKILL/ECONOMICS gates per
src.backtest.decision_time_truth.gate_for_purpose.

Verification status (2026-04-28):
- ECMWF ENS: confluence.ecmwf.int dissemination wiki — Day 0 = base+6h40m,
  Day N = +(40+4N) min. DERIVED_FROM_DISSEMINATION.
- NOAA GFS: nco.ncep.noaa.gov production status — completion ~base+4h14m
  (00z run finishes 04:14 UTC). DERIVED_FROM_DISSEMINATION.
- DWD ICON: model runs at 00/06/12/18 UTC documented but exact public
  dissemination lag not surfaced in DWD English-language pages.
  RECONSTRUCTED until a primary source schedule is captured.
- UK Met Office UKMO: model runs at 00/06/12/18 UTC documented; specific
  AWS rolling-archive availability time not publicly stated. RECONSTRUCTED.
- Open-Meteo Previous Runs (best_match alias): re-distributor; the
  upstream model varies per request. RECONSTRUCTED.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from src.backtest.decision_time_truth import (
    AvailabilityProvenance,
    ecmwf_ens_available_at,
)


@dataclass(frozen=True)
class DisseminationEntry:
    source_name: str
    derive: Callable[[datetime, int], datetime]
    provenance: AvailabilityProvenance
    schedule_url: str


def _gfs_available_at(base_time: datetime, lead_day: int) -> datetime:
    """NOAA NCEP GFS completion ~ base + 4h14m for the latest product step.
    Source (verified 2026-04-28): https://www.nco.ncep.noaa.gov/pmb/nwprod/prodstat/
    GFS MOS Forecast at 04:14 UTC for the 00 UTC cycle; same +4h14m offset
    holds for the 06 / 12 / 18 UTC cycles per the NCEP production status table.
    """
    return base_time + timedelta(hours=4, minutes=14)


_RECONSTRUCTED_LAG = timedelta(hours=12)


def _reconstructed_available_at(base_time: datetime, lead_day: int) -> datetime:
    return base_time + _RECONSTRUCTED_LAG


_REGISTRY: dict[str, DisseminationEntry] = {
    "ecmwf_previous_runs": DisseminationEntry(
        source_name="ecmwf_previous_runs",
        derive=ecmwf_ens_available_at,
        provenance=AvailabilityProvenance.DERIVED_FROM_DISSEMINATION,
        schedule_url="https://confluence.ecmwf.int/display/DAC/Dissemination+schedule",
    ),
    "gfs_previous_runs": DisseminationEntry(
        source_name="gfs_previous_runs",
        derive=_gfs_available_at,
        provenance=AvailabilityProvenance.DERIVED_FROM_DISSEMINATION,
        schedule_url="https://www.nco.ncep.noaa.gov/pmb/nwprod/prodstat/",
    ),
    "icon_previous_runs": DisseminationEntry(
        source_name="icon_previous_runs",
        derive=_reconstructed_available_at,
        provenance=AvailabilityProvenance.RECONSTRUCTED,
        schedule_url="https://www.dwd.de/EN/ourservices/nwp_forecast_data/nwp_forecast_data.html",
    ),
    "ukmo_previous_runs": DisseminationEntry(
        source_name="ukmo_previous_runs",
        derive=_reconstructed_available_at,
        provenance=AvailabilityProvenance.RECONSTRUCTED,
        schedule_url="https://www.metoffice.gov.uk/research/approach/modelling-systems/unified-model/weather-forecasting",
    ),
    "openmeteo_previous_runs": DisseminationEntry(
        source_name="openmeteo_previous_runs",
        derive=_reconstructed_available_at,
        provenance=AvailabilityProvenance.RECONSTRUCTED,
        schedule_url="https://open-meteo.com/en/docs",
    ),
}


class UnknownSourceError(KeyError):
    """Raised when a source has no registered dissemination schedule."""


def derive_availability(
    source: str,
    base_time: datetime,
    lead_day: int,
) -> tuple[datetime, AvailabilityProvenance]:
    entry = _REGISTRY.get(source)
    if entry is None:
        raise UnknownSourceError(
            f"No dissemination schedule registered for source={source!r}; "
            f"known: {sorted(_REGISTRY.keys())}"
        )
    if lead_day < 0:
        raise ValueError(f"lead_day must be non-negative; got {lead_day}")
    return entry.derive(base_time, lead_day), entry.provenance


def schedule_url_for(source: str) -> str:
    entry = _REGISTRY.get(source)
    if entry is None:
        raise UnknownSourceError(
            f"No dissemination schedule registered for source={source!r}"
        )
    return entry.schedule_url


def known_sources() -> frozenset[str]:
    return frozenset(_REGISTRY.keys())


def verified_sources() -> frozenset[str]:
    """Sources whose schedule is verified at primary source (DERIVED_FROM_DISSEMINATION)."""
    return frozenset(
        name for name, entry in _REGISTRY.items()
        if entry.provenance is AvailabilityProvenance.DERIVED_FROM_DISSEMINATION
    )
