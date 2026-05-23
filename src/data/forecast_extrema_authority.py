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


class ForecastExtremaEligibility(Enum):
    """Contribution eligibility of a forecast snapshot to the target local-day extrema."""

    FULL_CONTRIBUTOR = "FULL_CONTRIBUTOR"
    PARTIAL_CONTRIBUTOR = "PARTIAL_CONTRIBUTOR"
    NON_CONTRIBUTOR = "NON_CONTRIBUTOR"
    UNKNOWN = "UNKNOWN"


# Attribution status values that are considered unambiguously positive.
_POSITIVE_ATTRIBUTION_STATUSES = frozenset({
    "EXPLICIT",
    "VERIFIED",
    "OK",
    "CONTRIBUTES",
    "FULLY_INSIDE_TARGET_LOCAL_DAY",
})


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


def classify_forecast_extrema_authority(row: dict[str, Any]) -> ForecastExtremaAuthority:
    """Classify whether *row* contributes to the target local-day extrema.

    Priority logic (fail-closed):
    - FULL_CONTRIBUTOR  : contributes==1 AND attribution in positive set AND not boundary_ambiguous
    - PARTIAL_CONTRIBUTOR: contributes==1 AND boundary_ambiguous
    - NON_CONTRIBUTOR   : contributes present and ==0
    - UNKNOWN           : all other cases (contributes is None, attribution absent, etc.)
    """
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

    # contributes is None or unrecognised value.
    return ForecastExtremaAuthority(
        eligibility=ForecastExtremaEligibility.UNKNOWN,
        contributes_to_target_extrema=False,
        attribution_status=attribution_status,
        forecast_window_start_utc=forecast_window_start_utc,
        forecast_window_end_utc=forecast_window_end_utc,
        boundary_ambiguous=boundary_ambiguous,
        reason=f"contributes_to_target_extrema not set: {contributes_raw!r}",
    )
