# Created: 2026-05-22
# Last reused/audited: 2026-05-22
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-A
"""Forecast extrema authority classifier.

Classifies whether a fetched ensemble_snapshots_v2 row contributes to the
target local-day extrema that the market settles on.  Used by
executable_forecast_reader to prefer contributing runs (e.g. 00Z) over
later non-contributing ones (e.g. post-peak 12Z).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)


class ForecastExtremaEligibility(Enum):
    """Contribution eligibility of a forecast snapshot to the target local-day extrema."""

    FULL_CONTRIBUTOR = "FULL_CONTRIBUTOR"
    PARTIAL_CONTRIBUTOR = "PARTIAL_CONTRIBUTOR"
    NON_CONTRIBUTOR = "NON_CONTRIBUTOR"
    UNKNOWN = "UNKNOWN"
    # NULL contributes flag on a LEGACY (pre-extractor) data_version.  The row
    # predates the contribution extractor; we pass it through (prior behavior)
    # rather than fail-closed, but record the passthrough so it is auditable.
    LEGACY_NULL_PASSTHROUGH = "LEGACY_NULL_PASSTHROUGH"


# P0 follow-up §2: NULL contribution must fail closed for CURRENT data_versions.
# A current ECMWF Open Data period-extrema snapshot with contributes_to_target_extrema
# IS NULL is a schema-drift / writer-bug / missing-provenance signal — it must NOT
# silently pass through (that would re-open the P0 cold-bias).  NULL is tolerated
# ONLY for explicit legacy/pre-cutover data_versions (mx2t6 era, kept readable for
# historical rows).  The current set is the live mx2t3 v1 HIGH/LOW versions plus the
# LOW contract-window v2 (all written by the current ECMWF Open Data ingest).
CURRENT_EXTREMA_AUTHORITY_REQUIRED_DATA_VERSIONS: frozenset[str] = frozenset({
    ECMWF_OPENDATA_HIGH_DATA_VERSION,            # ecmwf_opendata_mx2t3_local_calendar_day_max_v1
    ECMWF_OPENDATA_LOW_DATA_VERSION,             # ecmwf_opendata_mn2t3_local_calendar_day_min_v1
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,  # ..._min_contract_window_v2
})

# Token recorded in applied_validations when a legacy NULL row passes through.
LEGACY_NULL_PASSTHROUGH_VALIDATION = "forecast_extrema_authority_legacy_null_passthrough"


# Attribution status values that are considered unambiguously positive.
# This is the single authoritative set referenced by both the classifier and
# the SQL ORDER BY CASE in executable_forecast_reader.py.  Change here
# propagates automatically to the SQL IN-list via POSITIVE_ATTRIBUTION_STATUS_SQL_IN_LIST.
POSITIVE_ATTRIBUTION_STATUSES: frozenset[str] = frozenset({
    "EXPLICIT",
    "VERIFIED",
    "OK",
    "CONTRIBUTES",
    "FULLY_INSIDE_TARGET_LOCAL_DAY",
})

# SQL-ready IN-list literal derived from the positive set above.
# e.g. "('CONTRIBUTES','EXPLICIT','FULLY_INSIDE_TARGET_LOCAL_DAY','OK','VERIFIED')"
# Use this in ORDER BY CASE … IN <POSITIVE_ATTRIBUTION_STATUS_SQL_IN_LIST> to stay
# in sync with the classifier without manual duplication.
POSITIVE_ATTRIBUTION_STATUS_SQL_IN_LIST: str = (
    "(" + ",".join(f"'{s}'" for s in sorted(POSITIVE_ATTRIBUTION_STATUSES)) + ")"
)

# Private alias kept for internal use within this module.
_POSITIVE_ATTRIBUTION_STATUSES = POSITIVE_ATTRIBUTION_STATUSES


@dataclass(frozen=True)
class ForecastExtremaAuthority:
    """Result of classifying a snapshot's extrema contribution."""

    eligibility: ForecastExtremaEligibility
    contributes_to_target_extrema: bool
    attribution_status: str | None
    forecast_window_start_utc: str | None
    forecast_window_end_utc: str | None
    boundary_ambiguous: bool
    reason: str


def classify_forecast_extrema_authority(
    row: dict[str, Any],
    *,
    data_version: str | None = None,
) -> ForecastExtremaAuthority:
    """Classify whether *row* contributes to the target local-day extrema.

    Priority logic (fail-closed):
    - FULL_CONTRIBUTOR      : contributes==1 AND attribution in positive set AND not boundary_ambiguous
    - PARTIAL_CONTRIBUTOR   : contributes==1 AND boundary_ambiguous
    - NON_CONTRIBUTOR       : contributes present and ==0
    - UNKNOWN               : contributes==1 with unknown attribution, OR contributes is None
                              on a CURRENT data_version (P0 follow-up §2 fail-closed)
    - LEGACY_NULL_PASSTHROUGH: contributes is None on a legacy/pre-cutover data_version

    ``data_version`` defaults to ``row.get("data_version")`` so existing callers
    that pass only *row* still get the data-version-aware NULL handling (the
    ensemble_snapshots_v2 row always carries its own data_version).
    """
    if data_version is None:
        dv = row.get("data_version")
        data_version = str(dv) if dv is not None else None
    # Read the contributes flag (0/1/None integer column).
    contributes_raw = row.get("contributes_to_target_extrema")
    if contributes_raw is not None:
        try:
            contributes_int = int(contributes_raw)
        except (TypeError, ValueError):
            contributes_int = None
    else:
        contributes_int = None

    # Prefer the canonical DB column name; fall back to the short alias.
    attribution_status: str | None = (
        row.get("forecast_window_attribution_status")
        or row.get("attribution_status")
        or None
    )

    boundary_ambiguous_raw = row.get("boundary_ambiguous")
    try:
        boundary_ambiguous = int(boundary_ambiguous_raw or 0) != 0
    except (TypeError, ValueError):
        boundary_ambiguous = False

    forecast_window_start_utc: str | None = (
        row.get("forecast_window_start_utc") or None
    )
    forecast_window_end_utc: str | None = (
        row.get("forecast_window_end_utc") or None
    )

    # Explicit non-contributor.
    if contributes_int is not None and contributes_int == 0:
        return ForecastExtremaAuthority(
            eligibility=ForecastExtremaEligibility.NON_CONTRIBUTOR,
            contributes_to_target_extrema=False,
            attribution_status=attribution_status,
            forecast_window_start_utc=forecast_window_start_utc,
            forecast_window_end_utc=forecast_window_end_utc,
            boundary_ambiguous=boundary_ambiguous,
            reason="contributes_to_target_extrema=0",
        )

    if contributes_int == 1:
        if boundary_ambiguous:
            return ForecastExtremaAuthority(
                eligibility=ForecastExtremaEligibility.PARTIAL_CONTRIBUTOR,
                contributes_to_target_extrema=True,
                attribution_status=attribution_status,
                forecast_window_start_utc=forecast_window_start_utc,
                forecast_window_end_utc=forecast_window_end_utc,
                boundary_ambiguous=True,
                reason="contributes=1 but boundary_ambiguous",
            )
        if attribution_status in _POSITIVE_ATTRIBUTION_STATUSES:
            return ForecastExtremaAuthority(
                eligibility=ForecastExtremaEligibility.FULL_CONTRIBUTOR,
                contributes_to_target_extrema=True,
                attribution_status=attribution_status,
                forecast_window_start_utc=forecast_window_start_utc,
                forecast_window_end_utc=forecast_window_end_utc,
                boundary_ambiguous=False,
                reason="contributes=1 attribution OK",
            )
        # contributes=1 but attribution unknown/missing — fail-closed.
        return ForecastExtremaAuthority(
            eligibility=ForecastExtremaEligibility.UNKNOWN,
            contributes_to_target_extrema=True,
            attribution_status=attribution_status,
            forecast_window_start_utc=forecast_window_start_utc,
            forecast_window_end_utc=forecast_window_end_utc,
            boundary_ambiguous=False,
            reason=f"contributes=1 but attribution_status not in positive set: {attribution_status!r}",
        )

    # contributes is None or unrecognised value.  P0 follow-up §2: fail closed
    # for CURRENT data_versions (a live mx2t3 snapshot with NULL contribution is
    # a provenance hole that would re-open the cold-bias); pass through ONLY for
    # explicit legacy/pre-cutover data_versions.
    if data_version in CURRENT_EXTREMA_AUTHORITY_REQUIRED_DATA_VERSIONS:
        return ForecastExtremaAuthority(
            eligibility=ForecastExtremaEligibility.UNKNOWN,
            contributes_to_target_extrema=False,
            attribution_status=attribution_status,
            forecast_window_start_utc=forecast_window_start_utc,
            forecast_window_end_utc=forecast_window_end_utc,
            boundary_ambiguous=boundary_ambiguous,
            reason=(
                f"contributes_to_target_extrema is NULL on current data_version "
                f"{data_version!r} (fail-closed)"
            ),
        )
    return ForecastExtremaAuthority(
        eligibility=ForecastExtremaEligibility.LEGACY_NULL_PASSTHROUGH,
        contributes_to_target_extrema=False,
        attribution_status=attribution_status,
        forecast_window_start_utc=forecast_window_start_utc,
        forecast_window_end_utc=forecast_window_end_utc,
        boundary_ambiguous=boundary_ambiguous,
        reason=(
            f"contributes_to_target_extrema is NULL on legacy data_version "
            f"{data_version!r} (passthrough)"
        ),
    )
