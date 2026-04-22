# Created: 2026-04-21
# Last reused/audited: 2026-04-21
# Authority basis: plan v3 antibodies A1/A2/A6 (.omc/plans/observation-
#                  instants-migration-iter3.md L119-124); step2 Phase 0 file #3.
"""Typed writer for observation_instants_v2 with A1/A2/A6 enforcement.

This module is the single entry point for any row that will be written
to ``observation_instants_v2`` going forward (pilot, fleet, HK accumulator).
It refuses to write rows that would silently undermine the migration:

A1 (missing-provenance rejection)
    authority, data_version, and provenance_json MUST be explicitly set
    and non-default. UNVERIFIED/QUARANTINED authorities are rejected —
    readers filter to {VERIFIED, ICAO_STATION_NATIVE} per A4.

A2 (source-tier consistency)
    ``source`` MUST be in ``allowed_sources_for_tier(tier_for_city(city))``.
    E.g. a WU_ICAO city cannot be written with ``source='openmeteo_*'``.

A6 (Hong Kong / VHHH category-error prevention)
    For Hong Kong, ``source`` MUST equal ``'hko_hourly_accumulator'``.
    Any attempt to write a WU ICAO row (``wu_icao_history``) or Ogimet
    row for HK is rejected with a targeted error message that names the
    HKO-vs-VHHH distance gap (40 km). This is a redundant second line
    of defense; tier_resolver already maps HK to HKO_NATIVE whose
    allowed-sources frozenset is ``{'hko_hourly_accumulator'}``.

Design notes
------------
Construction-time validation: every ``ObsV2Row`` is validated in its
``__post_init__``. Callers that build rows from external inputs see
the failure at *row construction time*, not deep inside a batch insert
— a failing row never enters the insert path.

The INSERT is all-or-nothing per batch: one bad row inside a batch
raises ``InvalidObsV2RowError`` before any row is written. This keeps
partial-batch states out of the database.

SQL-level CHECK is only present on NEW DBs (SQLite cannot ALTER TABLE
ADD CHECK); live tables rely on this module. Do not bypass.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

from src.data.tier_resolver import (
    Tier,
    expected_source_for_city,
    tier_for_city,
)


# Allowed authority strings on WRITE (plan v3 A4 reader filter complement).
# 'UNVERIFIED' and 'QUARANTINED' would be silently excluded by downstream
# consumers, so rejecting at write time makes the failure loud.
_ALLOWED_WRITE_AUTHORITIES: frozenset[str] = frozenset(
    {"VERIFIED", "ICAO_STATION_NATIVE"}
)

# data_version must match one of these patterns. 'v0' is the zeus_meta
# pre-cutover sentinel and must NEVER appear on a row; 'v1.*' is the
# Phase 0/1 migration family. Adding a new family is a deliberate edit.
_DATA_VERSION_RE = re.compile(r"^v1\.[a-z0-9\-\._]+$")

# Time basis values — must match what daily_obs_append uses for
# consistency with legacy observation_instants.
_ALLOWED_TIME_BASIS: frozenset[str] = frozenset(
    {"utc_hour_aligned", "station_local", "hourly_accumulator"}
)

_ALLOWED_TEMP_UNITS: frozenset[str] = frozenset({"F", "C"})


class InvalidObsV2RowError(ValueError):
    """Raised when a row fails A1/A2/A6 or structural validation.

    Inherits ValueError so catch-all ``except ValueError`` still works
    at call sites that don't need to distinguish.
    """


@dataclass(frozen=True)
class ObsV2Row:
    """One row of observation_instants_v2 data, validated at construction.

    All fields are positional+keyword safe. Validation runs in
    ``__post_init__`` and raises ``InvalidObsV2RowError`` on any failure.

    Nullable fields (per schema): local_hour, temp_current, running_max,
    running_min, delta_rate_per_h, station_id, observation_count,
    raw_response, source_file. All others are required.
    """

    # Required identity fields
    city: str
    target_date: str  # 'YYYY-MM-DD'
    source: str
    timezone_name: str
    local_timestamp: str
    utc_timestamp: str
    utc_offset_minutes: int
    time_basis: str
    temp_unit: str
    imported_at: str
    authority: str
    data_version: str
    provenance_json: str

    # Nullable / defaulted fields
    local_hour: Optional[float] = None
    dst_active: int = 0
    is_ambiguous_local_hour: int = 0
    is_missing_local_hour: int = 0
    temp_current: Optional[float] = None
    running_max: Optional[float] = None
    running_min: Optional[float] = None
    delta_rate_per_h: Optional[float] = None
    station_id: Optional[str] = None
    observation_count: Optional[int] = None
    raw_response: Optional[str] = None
    source_file: Optional[str] = None

    def __post_init__(self) -> None:
        self._validate()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def _validate(self) -> None:
        # A1: authority / data_version / provenance_json must be non-default
        if self.authority not in _ALLOWED_WRITE_AUTHORITIES:
            raise InvalidObsV2RowError(
                f"A1 violation (city={self.city}, utc={self.utc_timestamp}): "
                f"authority={self.authority!r} not in {sorted(_ALLOWED_WRITE_AUTHORITIES)}. "
                "UNVERIFIED/QUARANTINED rows are filtered out by readers; "
                "writing them creates phantom data."
            )
        if not self.data_version or not _DATA_VERSION_RE.match(self.data_version):
            raise InvalidObsV2RowError(
                f"A1 violation (city={self.city}): data_version={self.data_version!r} "
                f"does not match {_DATA_VERSION_RE.pattern!r}. "
                "Valid examples: 'v1.wu-native.pilot', 'v1.wu-native'. "
                "'v0' is the pre-cutover sentinel and MUST NOT appear on a row."
            )
        if not self.provenance_json or self.provenance_json == "{}":
            raise InvalidObsV2RowError(
                f"A1 violation (city={self.city}): provenance_json must be "
                "a non-empty non-default JSON object with at least a 'tier' key."
            )
        # Parse provenance_json to catch malformed strings at construction time.
        try:
            parsed = json.loads(self.provenance_json)
        except (ValueError, TypeError) as exc:
            raise InvalidObsV2RowError(
                f"A1 violation (city={self.city}): provenance_json is not "
                f"valid JSON: {exc}"
            )
        if not isinstance(parsed, dict):
            raise InvalidObsV2RowError(
                f"A1 violation (city={self.city}): provenance_json must be "
                f"a JSON object, got {type(parsed).__name__}."
            )
        if "tier" not in parsed:
            raise InvalidObsV2RowError(
                f"A1 violation (city={self.city}): provenance_json must "
                "contain a 'tier' key per plan v3 P3 (row-level provenance "
                "contract)."
            )

        # A2: source must match the per-city expected source (stronger
        # than tier-level whitelist — pins station identity within a tier
        # that has multiple station-specific sources, e.g. Ogimet).
        try:
            tier = tier_for_city(self.city)
            expected = expected_source_for_city(self.city)
        except Exception as exc:
            raise InvalidObsV2RowError(
                f"A2 violation: city={self.city!r} has no tier mapping: {exc}"
            )
        if self.source != expected:
            raise InvalidObsV2RowError(
                f"A2 violation (city={self.city}, tier={tier.name}): "
                f"source={self.source!r} does not match expected "
                f"{expected!r}. Source string must trace 1:1 to the "
                "settlement station."
            )

        # A6: Hong Kong explicit — the VHHH/WU category error pin
        if self.city == "Hong Kong":
            if self.source != "hko_hourly_accumulator":
                raise InvalidObsV2RowError(
                    f"A6 violation: Hong Kong row with source={self.source!r}. "
                    "Hong Kong settles via HKO Observatory Headquarters "
                    "(wu_station=null in cities.json). The VHHH airport "
                    "station is 40 km from HKO HQ; using it creates a "
                    "1-3°C systematic offset during urban-heat-island hours. "
                    "Only 'hko_hourly_accumulator' is valid for HK rows."
                )
            if tier is not Tier.HKO_NATIVE:
                raise InvalidObsV2RowError(
                    f"A6 violation: Hong Kong resolved to {tier.name}, "
                    "expected HKO_NATIVE. tier_resolver drift — refusing "
                    "to write."
                )

        # Structural sanity
        if self.time_basis not in _ALLOWED_TIME_BASIS:
            raise InvalidObsV2RowError(
                f"time_basis={self.time_basis!r} not in {sorted(_ALLOWED_TIME_BASIS)}"
            )
        if self.temp_unit not in _ALLOWED_TEMP_UNITS:
            raise InvalidObsV2RowError(
                f"temp_unit={self.temp_unit!r} not in {sorted(_ALLOWED_TEMP_UNITS)}"
            )
        if not _looks_like_iso_date(self.target_date):
            raise InvalidObsV2RowError(
                f"target_date={self.target_date!r} must be YYYY-MM-DD"
            )
        if not _looks_like_iso_datetime(self.utc_timestamp):
            raise InvalidObsV2RowError(
                f"utc_timestamp={self.utc_timestamp!r} must be ISO 8601 "
                "(YYYY-MM-DDTHH:MM:SS[+TZ] or with 'Z')"
            )


def _looks_like_iso_date(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def _looks_like_iso_datetime(s: str) -> bool:
    if not s:
        return False
    # Accept both trailing 'Z' and explicit offsets; datetime.fromisoformat
    # on 3.11+ handles both, but we normalize for 3.10 compatibility.
    normalized = s.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
        return True
    except (ValueError, TypeError):
        return False


# ----------------------------------------------------------------------
# Batch insert
# ----------------------------------------------------------------------

# Column order for the INSERT must match the tuple order below. Cached
# at module load to avoid recomputation per batch.
_INSERT_SQL = """
    INSERT OR REPLACE INTO observation_instants_v2 (
        city, target_date, source, timezone_name, local_hour,
        local_timestamp, utc_timestamp, utc_offset_minutes, dst_active,
        is_ambiguous_local_hour, is_missing_local_hour, time_basis,
        temp_current, running_max, running_min, delta_rate_per_h,
        temp_unit, station_id, observation_count, raw_response,
        source_file, imported_at, authority, data_version,
        provenance_json
    ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
        ?, ?, ?, ?, ?
    )
"""


def _row_to_tuple(row: ObsV2Row) -> tuple[Any, ...]:
    """Serialize an ObsV2Row into the parameter tuple matching _INSERT_SQL."""
    return (
        row.city,
        row.target_date,
        row.source,
        row.timezone_name,
        row.local_hour,
        row.local_timestamp,
        row.utc_timestamp,
        row.utc_offset_minutes,
        row.dst_active,
        row.is_ambiguous_local_hour,
        row.is_missing_local_hour,
        row.time_basis,
        row.temp_current,
        row.running_max,
        row.running_min,
        row.delta_rate_per_h,
        row.temp_unit,
        row.station_id,
        row.observation_count,
        row.raw_response,
        row.source_file,
        row.imported_at,
        row.authority,
        row.data_version,
        row.provenance_json,
    )


def insert_rows(conn: sqlite3.Connection, rows: Iterable[ObsV2Row]) -> int:
    """Insert a batch of validated ``ObsV2Row``s.

    Because ``ObsV2Row`` validates at construction, any row that reaches
    this function has already passed A1/A2/A6. This function therefore
    focuses on the SQL side: single transaction, INSERT OR REPLACE (the
    UNIQUE(city, source, utc_timestamp) index dedupes).

    Returns
    -------
    int
        Number of rows inserted (len of the input iterable after
        materialization).

    Raises
    ------
    sqlite3.Error
        For any DB-level failure (disk full, locked, constraint). The
        caller is responsible for retry/rollback semantics outside the
        row-level invariants this module protects.
    """
    tuples = [_row_to_tuple(r) for r in rows]
    if not tuples:
        return 0
    conn.executemany(_INSERT_SQL, tuples)
    return len(tuples)
