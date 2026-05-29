# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL redesign P1 (residual-pairing seam); CRITIC_SYNTHESIS_2026-05-29
#   §2 C1 (source_kind='prior' hardcoded -> TIGGE/OpenData lineage collapse) + the
#   target-equality antibody (forecast_target.assert_same_target).
"""Residual-pairing seam: bind a ForecastObject to a SettlementObject into a typed,
correctly-keyed ResidualKey — and ONLY when they describe the same random variable.

Two structural fixes over the legacy ledger:
  1. ``source_kind`` is DERIVED from the forecast's data_version lineage
     (tigge_prior vs opendata_live), never the hardcoded literal 'prior' that
     collapsed TIGGE and OpenData provenance (build_ens_residual_evidence.py:227).
  2. The pairing routes through ``assert_same_target`` — a forecast cannot be paired
     to a settlement of a different RV (wrong date/station/unit/authority/metric).

The residual VALUE arithmetic (member mean, unit conversion) stays with the ledger;
this module owns the *identity + lineage* of the residual, so every residual the
ledger emits carries a single well-defined random variable and an honest source_kind.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from src.contracts.forecast_object import ForecastObject
from src.contracts.forecast_target import (
    ForecastTarget,
    assert_same_target,
    normalize_settlement_authority,
)

# Lineage classes. "paired_delta" (a TIGGE<->OpenData same-window paired residual) is
# a two-forecast construction produced elsewhere; a single forecast<->settlement
# residual is one of the two below.
_TIGGE_PRIOR = "tigge_prior"
_OPENDATA_LIVE = "opendata_live"


def source_kind_for_data_version(data_version: str) -> str:
    """Classify a residual's lineage from its forecast data_version.

    Raises ValueError on an unrecognized lineage — an unknown source must not
    silently inherit a default tag (the legacy 'prior' collapse).
    """
    if data_version.startswith("tigge"):
        return _TIGGE_PRIOR
    if data_version.startswith("ecmwf_opendata"):
        return _OPENDATA_LIVE
    raise ValueError(
        f"source_kind refused: data_version={data_version!r} has no recognized "
        f"lineage prefix (expected 'tigge*' or 'ecmwf_opendata*'). Refusing to "
        f"assign a default tag — that is the lineage-collapse bug this gate prevents."
    )


class SettlementIncompleteError(ValueError):
    """Raised when a settlement row lacks a field needed to define its target identity
    (authority, station). The row is refused rather than paired on a partial identity.
    """


_STATION_RE = re.compile(r"^[A-Za-z0-9]{3,8}$")
# Some settlement sources carry the station in a query param, not the path tail
# (weather.gov/wrh/timeseries?site=LLBG — Istanbul/Moscow/Tel Aviv). Check this first.
_STATION_QUERY_RE = re.compile(r"[?&]site=([A-Za-z0-9]{3,8})", re.IGNORECASE)


def _station_from_settlement_source(source: str) -> str:
    """Extract the settlement station from settlement_source.

    Settlement rows store the station inside the source URL
    ('https://www.wunderground.com/history/daily/us/il/chicago/KORD' -> 'KORD').
    settlement_outcomes has no first-class station column (P2 D-S1); this parse is the
    interim. Raises if a plausible station code cannot be recovered (fail-closed).
    """
    if not source or "/" not in source:
        raise SettlementIncompleteError(
            f"settlement refused: cannot recover station from settlement_source={source!r} "
            f"(expected a URL with a '?site=' param or a station-code last path segment). "
            f"settlement_outcomes lacks a station column (D-S1)."
        )
    qp = _STATION_QUERY_RE.search(source)
    if qp:
        return qp.group(1)
    # Path-tail form (.../chicago/KORD); drop any residual query string defensively.
    segment = source.rstrip("/").rsplit("/", 1)[-1].strip().split("?", 1)[0]
    if not _STATION_RE.match(segment):
        raise SettlementIncompleteError(
            f"settlement refused: settlement_source={source!r} yields no plausible station code "
            f"(no '?site=' param and last path segment {segment!r} is not a station)."
        )
    return segment


@dataclass(frozen=True)
class SettlementObject:
    """The settlement outcome that is a forecast's payout truth."""

    target: ForecastTarget
    settlement_value: float

    @classmethod
    def from_settlement_row(cls, row: dict, *, claimed_unit: str) -> "SettlementObject":
        """Build a SettlementObject from a settlement_outcomes row dict.

        Derives identity that IS recoverable from the row — city/metric/date (columns),
        authority (provenance_json.data_version, normalized), station (settlement_source
        URL). The UNIT is NOT a settlement column (D-S1), so it is supplied as the
        forecast's claimed_unit; pair_residual matches the verifiable dims and converts
        with this unit. Raises SettlementIncompleteError on any unrecoverable field.
        """
        def _req(key: str, human: str | None = None):
            v = row.get(key)
            if v is None or (isinstance(v, str) and v == ""):
                raise SettlementIncompleteError(
                    f"settlement refused: required field {human or key!r} missing/empty."
                )
            return v

        raw_prov = _req("provenance_json", "provenance_json")
        prov = json.loads(raw_prov) if isinstance(raw_prov, str) else dict(raw_prov)
        data_version = prov.get("data_version")
        if not data_version:
            raise SettlementIncompleteError(
                "settlement refused: provenance_json has no data_version (the authority)."
            )

        target = ForecastTarget(
            city=str(_req("city")),
            metric=str(_req("temperature_metric", "metric")),
            target_local_date=str(_req("target_date", "target_local_date")),
            settlement_station=_station_from_settlement_source(str(_req("settlement_source"))),
            settlement_unit=str(claimed_unit),
            settlement_authority=normalize_settlement_authority(str(data_version)),
        )
        return cls(target=target, settlement_value=float(_req("settlement_value")))


@dataclass(frozen=True)
class ResidualKey:
    """Identity + lineage of a single residual. The keying dims a product-segregated,
    lead-respecting bias model needs (product, cycle, lead_hours) plus the RV target
    and the derived source_kind. Carries NO value — the ledger computes residual_c.
    """

    target: ForecastTarget
    product: str
    cycle: str
    lead_hours: float
    source_kind: str


def pair_residual(forecast: ForecastObject, settlement: SettlementObject) -> ResidualKey:
    """Return the keyed identity of the residual binding ``forecast`` to ``settlement``.

    Raises ForecastTargetMismatchError if the two describe different random variables.
    """
    shared_target = assert_same_target(forecast.target, settlement.target)
    return ResidualKey(
        target=shared_target,
        product=forecast.product,
        cycle=forecast.cycle,
        lead_hours=forecast.lead_hours,
        source_kind=source_kind_for_data_version(forecast.data_version),
    )
