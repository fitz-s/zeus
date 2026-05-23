# Created: 2026-05-22
# Last reused/audited: 2026-05-23
# Authority basis: docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md §PR-A;
#   PR #309 bundle-layer follow-up §2 (NULL fail-closed);
#   p0-2-hardening-20260523: missing/unknown data_version now fail-closed (UNKNOWN), not legacy-passthrough.
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
    _ECMWF_OPENDATA_HIGH_DATA_VERSION_LEGACY,
    _ECMWF_OPENDATA_LOW_DATA_VERSION_LEGACY,
    _ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION_LEGACY,
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
# ONLY for the explicitly-enumerated legacy/pre-cutover data_versions below (mx2t6 era).
# Any other data_version — including None/missing — fails closed as UNKNOWN.
#
# p0-2-hardening: "missing data_version" (None) is now also fail-closed (UNKNOWN).
# The earlier passthrough for None was a hidden hole: _snapshot_row_for_classification
# returns {} when the DB row is not found; {} produces data_version=None; None fell
# through to LEGACY_NULL_PASSTHROUGH, silently bypassing the P0 gate on schema
# drift or missing provenance.  The only safe passthrough is EXPLICIT legacy versions.
CURRENT_EXTREMA_AUTHORITY_REQUIRED_DATA_VERSIONS: frozenset[str] = frozenset({
    ECMWF_OPENDATA_HIGH_DATA_VERSION,            # ecmwf_opendata_mx2t3_local_calendar_day_max_v1
    ECMWF_OPENDATA_LOW_DATA_VERSION,             # ecmwf_opendata_mn2t3_local_calendar_day_min_v1
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,  # ..._min_contract_window_v2
})

# Explicit legacy data_versions for which a NULL contributes_to_target_extrema is
# tolerated (pre-cutover mx2t6/mn2t6 era rows, written before the extrema-authority
# extractor existed).  Historical rows in ensemble_snapshots_v2 remain readable.
# DO NOT add new entries without a documented authority basis.
LEGACY_EXTREMA_AUTHORITY_DATA_VERSIONS: frozenset[str] = frozenset({
    _ECMWF_OPENDATA_HIGH_DATA_VERSION_LEGACY,   # ecmwf_opendata_mx2t6_local_calendar_day_max_v1
    _ECMWF_OPENDATA_LOW_DATA_VERSION_LEGACY,    # ecmwf_opendata_mn2t6_local_calendar_day_min_v1
    _ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION_LEGACY,  # ..._min_contract_window_v2
    # NOTE: TIGGE mx2t6/mn2t6 families are intentionally absent. They are never
    # requested by the executable read path (data_version_for_track returns only
    # ECMWF_OPENDATA mx2t3/mn2t3 versions), so no legacy rows exist for them.
    # Do NOT add TIGGE entries here to "fix" an apparent gap — doing so would
    # silently allow TIGGE null-authority rows through the extrema gate.
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

    # contributes is None or unrecognised value.  Tri-state gate (p0-2-hardening):
    #
    #   1. EXPLICIT LEGACY data_version → LEGACY_NULL_PASSTHROUGH (historical rows).
    #   2. All other cases — including None/missing data_version, CURRENT versions,
    #      or any unknown string — → UNKNOWN (fail-closed).
    #
    # Rationale for None→UNKNOWN: _snapshot_row_for_classification returns {} when
    # the DB row is not found (table missing or snapshot_id unknown); {} yields
    # data_version=None; previously None fell into LEGACY_NULL_PASSTHROUGH, silently
    # bypassing the P0 gate on schema drift or missing provenance.  Only explicit
    # legacy versions are safe to pass through.
    if data_version in LEGACY_EXTREMA_AUTHORITY_DATA_VERSIONS:
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
    # data_version is None, a current/known-live version, or any unrecognised
    # string — fail closed.
    if data_version in CURRENT_EXTREMA_AUTHORITY_REQUIRED_DATA_VERSIONS:
        reason = (
            f"contributes_to_target_extrema is NULL on current data_version "
            f"{data_version!r} (fail-closed)"
        )
    elif data_version is None:
        reason = (
            "contributes_to_target_extrema is NULL and data_version is missing "
            "(empty row or lookup failure) (fail-closed)"
        )
    else:
        reason = (
            f"contributes_to_target_extrema is NULL on unrecognised data_version "
            f"{data_version!r} (fail-closed)"
        )
    return ForecastExtremaAuthority(
        eligibility=ForecastExtremaEligibility.UNKNOWN,
        contributes_to_target_extrema=False,
        attribution_status=attribution_status,
        forecast_window_start_utc=forecast_window_start_utc,
        forecast_window_end_utc=forecast_window_end_utc,
        boundary_ambiguous=boundary_ambiguous,
        reason=reason,
    )
