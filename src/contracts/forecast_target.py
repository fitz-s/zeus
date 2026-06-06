# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL redesign P1 (ForecastObject/SettlementObject contract);
#   CRITIC_SYNTHESIS_2026-05-29 §2 (lineage collapse; degC/degF mis-scale Cons-SEV-1.C;
#   target-equality completeness Cons-SEV-2-E). Pattern follows
#   src/contracts/alpha_decision.py (frozen dataclass + assert_* + typed mismatch error).
"""Forecast random-variable target identity.

A ``ForecastTarget`` is the tuple that defines *which* random variable a forecast
row is about and which settlement outcome is its payout truth. A residual binding
a forecast to a settlement is only valid when both sides carry an *identical*
target across every dimension — otherwise the residual measures the error of the
wrong random variable.

This is the antibody for the mixed-RV bug family the redesign exists to kill:
TIGGE-6h residuals paired to the wrong/absent settlement (lineage collapse), and
degC/degF unit mis-scale. The target carries the settlement-side identity
(station, unit, authority) precisely because "Chicago HIGH 2026-05-20" is not a
random variable until the settlement station, unit, and source authority are
pinned — two of those differing is two different payout truths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Dimensions that define the random variable. Order is the message order.
_TARGET_DIMENSIONS: tuple[str, ...] = (
    "city",
    "metric",
    "target_local_date",
    "settlement_station",
    "settlement_unit",
    "settlement_authority",
)


class ForecastTargetMismatchError(Exception):
    """Raised when a forecast-side target and a settlement-side target disagree on
    any dimension that defines the random variable. Forming a residual across a
    mismatch would measure the error of the wrong RV (lineage collapse / unit
    mis-scale). This is the redesign's structural antibody.
    """


@dataclass(frozen=True)
class ForecastTarget:
    """Immutable identity of the forecast random variable + its settlement truth.

    Attributes:
        city: settlement city key.
        metric: "HIGH" or "LOW" (daily extreme family).
        target_local_date: local calendar day the contract settles on (ISO date str).
        settlement_station: the station whose observation IS the payout truth
            (e.g. "KORD"). Two stations in the same city are two different RVs.
        settlement_unit: unit the settlement is measured/rounded in ("F"/"C" — the
            settlement vocabulary enforced by the ensemble_snapshots and settlement_outcomes
            CHECKs; distinct from ensemble ``members_unit`` which is "degC"/"degF"). A forecast
            compared in the wrong unit silently mis-scales (Cons-SEV-1.C).
        settlement_authority: the source-authority data_version that owns the truth
            (e.g. "wu_icao_history_v1"). Different authority = different truth basis.
    """

    city: str
    metric: str
    target_local_date: str
    settlement_station: str
    settlement_unit: str
    settlement_authority: str


# Settlement authority is represented two ways across the boundary: the forecast snapshot
# stores the source TYPE ("wu_icao"); the settlement row stores the versioned data_version
# ("wu_icao_history_v1") in provenance. Both must reduce to one canonical token so a true
# pair matches. Method/version suffixes for the known settlement families (see
# ensemble_snapshot_provenance.CANONICAL_SETTLEMENT_DATA_VERSIONS) are stripped to the base.
# NOTE: this is a string-normalization heuristic pending a first-class settlement_unit/
# settlement_authority schema column (P2 D-S1) that would make the authority verifiable.
_AUTH_VERSION_RE = re.compile(r"_v\d+$")
_AUTH_METHOD_SUFFIXES: tuple[str, ...] = (
    "_history",       # wu_icao_history
    "_daily_api",     # hko_daily_api
    "_metar",         # ogimet_metar
    "_no_collector",  # cwa_no_collector
)

# Canonical settlement-authority FAMILIES. The forecast snapshot tags the city's
# settlement_source_type ('wu_icao' / 'noaa' / 'hko' / 'cwa_station'); the settlement harvester
# records the COLLECTOR data_version ('wu_icao_history_v1' / 'ogimet_metar_v1' /
# 'hko_daily_api_v1' / 'cwa_no_collector_v0', per ensemble_snapshot_provenance.
# CANONICAL_SETTLEMENT_DATA_VERSIONS). After suffix-stripping, the collector tokens 'ogimet'
# and 'cwa_station' name the SAME observation authority as the forecast's 'noaa' and 'cwa' tags
# (operator-confirmed same-truth 2026-05-29) — so both write-sites reduce to one family and the
# pairing gate reconciles them instead of silently dropping the city (P2 SEV-2). A token outside
# the known families RAISES (loud quarantine), never passes through to a silent mismatch.
_AUTHORITY_SYNONYMS: dict[str, str] = {
    "ogimet": "noaa",      # ogimet collects the NWS/NOAA-published international airport METAR
    "cwa_station": "cwa",  # forecast-side tag for the Taiwan CWA station family
}
_KNOWN_SETTLEMENT_AUTHORITIES: frozenset[str] = frozenset({"wu_icao", "noaa", "hko", "cwa"})


class UnknownSettlementAuthorityError(ValueError):
    """Raised when a settlement authority token reduces to a family outside the known registry.

    Refusing to pair on an unreconciled authority is a LOUD quarantine — it must not silently
    drop (which would starve a whole city's residual ledger, P2 SEV-2). Resolve by adding the
    family to ``_AUTHORITY_SYNONYMS`` / ``_KNOWN_SETTLEMENT_AUTHORITIES`` or fixing the tag.
    """


def normalize_settlement_authority(raw: str) -> str:
    """Reduce a settlement authority string to its canonical source FAMILY.

    Strips the version suffix ('_v1'/'_v0') and the collector-method suffix
    ('_history'/'_daily_api'/'_metar'/'_no_collector'), then maps known collector synonyms to
    the authority family the forecast tags it under:
      'wu_icao_history_v1' -> 'wu_icao' ; 'hko_daily_api_v1' -> 'hko' ;
      'ogimet_metar_v1' -> 'noaa' ; 'cwa_no_collector_v0' -> 'cwa' ;
      'noaa' -> 'noaa' ; 'cwa_station' -> 'cwa' ; 'wu_icao' -> 'wu_icao'.

    Raises ``UnknownSettlementAuthorityError`` on any token outside the known families so an
    unreconciled authority is quarantined loudly rather than dropped silently.
    """
    s = _AUTH_VERSION_RE.sub("", str(raw).strip())
    for suffix in _AUTH_METHOD_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    s = _AUTHORITY_SYNONYMS.get(s, s)
    if s not in _KNOWN_SETTLEMENT_AUTHORITIES:
        raise UnknownSettlementAuthorityError(
            f"settlement authority {raw!r} reduced to {s!r}, not a known authority family "
            f"{sorted(_KNOWN_SETTLEMENT_AUTHORITIES)}. Refusing to pair on an unreconciled "
            f"authority (loud quarantine, not a silent drop): add the family to the registry "
            f"or fix the source tag."
        )
    return s


def assert_same_target(
    forecast_side: ForecastTarget,
    settlement_side: ForecastTarget,
) -> ForecastTarget:
    """Return the shared target if both sides are identical; else raise.

    Compares every dimension in ``_TARGET_DIMENSIONS``. On any difference, raises
    ``ForecastTargetMismatchError`` naming the mismatched dimension(s) and the two
    values — so a residual can only be constructed for a single, well-defined RV.
    """
    mismatches: list[str] = []
    for dim in _TARGET_DIMENSIONS:
        fval = getattr(forecast_side, dim)
        sval = getattr(settlement_side, dim)
        if fval != sval:
            mismatches.append(f"{dim}: forecast={fval!r} settlement={sval!r}")
    if mismatches:
        raise ForecastTargetMismatchError(
            "forecast and settlement targets disagree on "
            f"{len(mismatches)} dimension(s): " + "; ".join(mismatches)
        )
    return forecast_side
