"""Typed ObservationAtom frozen dataclass — the ONLY write path into observations table.

An ObservationAtom is a self-describing, provenance-complete temperature observation.
Instances with validation_pass=False cannot be constructed — __post_init__ raises
IngestionRejected immediately, making malformed observations unconstructable.

Field-level contracts:
- ``value`` is in ``target_unit``. ``raw_value`` is in ``raw_unit``. Conversion is explicit.
- ``target_unit`` must match the city's settlement unit (from config/cities.json).
- ``raw_unit`` may differ from target_unit; conversion is the atom's contract.
- ``fetch_utc`` is the UTC timestamp of the HTTP response completion for this data.
- ``local_time`` is the atom's local observation time (may be a window midpoint for daily
  rollups).
- ``collection_window_start_utc`` / ``_end_utc`` bracket the period whose max/min/mean
  became ``value``.
- ``dst_active``, ``is_ambiguous_local_hour``, ``is_missing_local_hour`` MUST be computed
  from SolarDay/ZoneInfo via ``_is_missing_local_hour`` in ``src.signal.diurnal``. Never
  hardcode these to False.
- ``hemisphere`` is "N" (lat >= 0) or "S" (lat < 0). Equator is folded into N by convention.
- ``season`` is "DJF" / "MAM" / "JJA" / "SON" already hemisphere-flipped.
- ``authority`` starts at "VERIFIED" only if validation_pass and source is trusted; the
  "UNVERIFIED" state is for post-hoc marking of untrusted sources only — construction
  with authority="UNVERIFIED" and validation_pass=True raises IngestionRejected.
- ``provenance_metadata`` is a dict serialized to a JSON TEXT column for future
  extensibility without schema migration.

Part of K1 packet. See .omc/plans/k1-freeze.md section 5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Optional


class IngestionRejected(ValueError):
    """Raised during ObservationAtom.__post_init__ or IngestionGuard.validate when data is untrustworthy."""
    pass


@dataclass(frozen=True)
class ObservationAtom:
    """A self-describing, provenance-complete temperature observation atom.

    Instances of this class are the ONLY write path into the observations table.
    An atom with validation_pass=False cannot exist — construction raises IngestionRejected.
    An atom that has not flowed through IngestionGuard.validate() must not be inserted.
    """

    # === Identity ===
    city: str
    target_date: date
    value_type: Literal["high", "low", "mean"]

    # === Value + unit contract ===
    value: float
    target_unit: Literal["F", "C"]
    raw_value: float
    raw_unit: Literal["F", "C", "K"]  # K allowed for raw-from-ECMWF; must be converted before value

    # === Source provenance ===
    source: str  # "wu_icao_history" | "iem_asos" | "openmeteo_archive" | "ecmwf_tigge" | ...
    station_id: Optional[str]  # "KORD", "EGLL", etc. None for sources without station identity
    api_endpoint: str  # full URL template used for the fetch

    # === Temporal provenance ===
    fetch_utc: datetime
    local_time: datetime
    collection_window_start_utc: datetime
    collection_window_end_utc: datetime

    # === DST context (from SolarDay / ZoneInfo) ===
    timezone: str  # IANA tz name e.g. "America/New_York"
    utc_offset_minutes: int
    dst_active: bool
    is_ambiguous_local_hour: bool
    is_missing_local_hour: bool

    # === Geographic / seasonal ===
    hemisphere: Literal["N", "S"]
    season: Literal["DJF", "MAM", "JJA", "SON"]
    month: int  # 1-12

    # === Run provenance ===
    rebuild_run_id: str  # e.g. "backfill_2026-04-12T10:00:00Z_daaeaa7"
    data_source_version: str  # e.g. "wu_icao_v1_2026"

    # === Authority + validation ===
    authority: Literal["VERIFIED", "UNVERIFIED", "QUARANTINED"]
    validation_pass: bool

    # === Extensibility escape hatch — stored as JSON TEXT in DB ===
    provenance_metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Unconstructable if validation_pass is False
        if not self.validation_pass:
            raise IngestionRejected(
                f"{self.city}/{self.target_date} atom rejected: validation_pass=False"
            )
        # Sanity: authority must be VERIFIED if validation_pass is True.
        # Construction never produces UNVERIFIED directly; that state is for
        # post-hoc marking of untrusted sources only.
        if self.authority == "UNVERIFIED" and self.validation_pass:
            raise IngestionRejected(
                f"{self.city}/{self.target_date}: validation_pass=True requires "
                f"authority='VERIFIED', got 'UNVERIFIED'"
            )
        # Month range guard
        if not 1 <= self.month <= 12:
            raise IngestionRejected(
                f"{self.city}: month={self.month} out of range [1, 12]"
            )
