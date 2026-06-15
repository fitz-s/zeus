# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("q engine, INV-Q1 through INV-Q8" section, src/probability/event_resolution.py);
#   docs/rebuild/q_engine_violation_ledger.md Layer 0 (EventResolution) + V3/V4/V19;
#   wired to src/contracts/settlement_semantics.py (the live settlement authority).
"""EventResolution — the one versioned settlement identity for a (city, date, metric).

This is Stage 1 of the q-kernel rebuild (consult_build_spec.md). ``EventResolution``
is the single declarative source of the per-city settlement convention that every
downstream q-integration / band / FDR consumer threads through. Its job is to make
the V3/V4 defect — the EMOS seam dropping the per-city ``rounding_rule`` and
silently integrating WMO half-up for Hong Kong — structurally impossible: the
rounding rule is a mandatory field sourced from
``SettlementSemantics.for_city(city).rounding_rule``, never defaulted.

INV-Q1 (settlement preimage byte-identical to on-chain settlement): the
``rounding_rule`` carried here is the SAME rule the bin labels declare, and
``settlement_preimage_offsets`` (in settlement_semantics) derives every q
integration bound from it. HK settles by ``oracle_truncate``; all other current
Zeus cities settle by ``wmo_half_up``.

Fail-closed: if the settlement station id cannot be resolved (missing or the
literal string ``"None"`` — the V19 defect where non-WU stations embed "None"),
``event_resolution_for_city`` raises ``ResolutionError`` rather than serving a
distribution against an unknown settlement station.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Any, Literal

from src.contracts.settlement_semantics import SettlementSemantics

# Versioned settlement semantics identity. Bumped when the resolution contract
# (preimage offsets, rounding rule set, finalization convention) changes shape.
SEMANTICS_VERSION = "settlement_semantics_v1"

# All current Zeus weather markets settle on a 1-degree integer grid (°C point
# bins, °F 2-degree range bins both resolve to integer settlement values). The
# native settlement step is therefore 1.0 in the city's measurement unit; this
# matches ``SettlementSemantics.precision == 1.0`` for every live constructor.
DEFAULT_SETTLEMENT_STEP_NATIVE = 1.0


class ResolutionError(ValueError):
    """Raised when a city cannot be resolved to a complete settlement identity.

    Fail-closed signal: the settlement station id is missing / unknown, so no
    live predictive distribution may be served for this family.
    """


@dataclass(frozen=True)
class EventResolution:
    """The versioned settlement identity for one (city, target_date, metric).

    Every q builder consumes ``rounding_rule`` from THIS object; no money-path
    q integration may default the rounding rule (V3/V4). Mirrors the Layer 0
    contract in the q_engine_violation_ledger.
    """

    city: str
    station_id: str
    settlement_source_type: str
    resolution_source: str
    target_local_date: date
    settlement_timezone: str
    metric: Literal["high", "low"]
    measurement_unit: Literal["C", "F"]
    settlement_step_native: float
    precision: float
    rounding_rule: Literal["wmo_half_up", "oracle_truncate", "floor", "ceil"]
    finalization_local_time: time
    semantics_version: str


def _parse_finalization_time(raw: str) -> time:
    """Parse a SettlementSemantics.finalization_time string into a ``time``.

    The live contract stores finalization as e.g. ``"12:00:00Z"``. We strip a
    trailing ``Z`` (the value is already a fixed UTC wall-clock convention) and
    parse ``HH:MM:SS``. Fail-closed on a malformed value rather than guessing.
    """
    text = (raw or "").strip()
    if text.endswith("Z"):
        text = text[:-1]
    parts = text.split(":")
    if len(parts) < 2:
        raise ResolutionError(
            f"FINALIZATION_TIME_MALFORMED: {raw!r} is not HH:MM[:SS]"
        )
    try:
        hh = int(parts[0])
        mm = int(parts[1])
        ss = int(parts[2]) if len(parts) >= 3 else 0
    except ValueError as exc:  # pragma: no cover - defensive
        raise ResolutionError(
            f"FINALIZATION_TIME_MALFORMED: {raw!r} ({exc})"
        ) from exc
    return time(hour=hh, minute=mm, second=ss)


def _station_id_for_city(city: Any, sem: SettlementSemantics) -> str:
    """Resolve the settlement station id for a city.

    For WU-settled cities the station is ``city.wu_station`` (the ICAO id). For
    non-WU sources (HKO, CWA, NOAA) ``SettlementSemantics.resolution_source``
    carries the authoritative station identity (e.g. ``"HKO_HQ"``).
    """
    if getattr(city, "settlement_source_type", "wu_icao") == "wu_icao":
        return getattr(city, "wu_station", "") or ""
    return sem.resolution_source or ""


def event_resolution_for_city(
    city: Any,
    target_date: date,
    metric: Literal["high", "low"],
) -> EventResolution:
    """Build the versioned ``EventResolution`` for a city/date/metric.

    Wires to the live ``SettlementSemantics.for_city`` so the per-city
    ``rounding_rule`` (HK ``oracle_truncate``, otherwise ``wmo_half_up``) is the
    REAL settlement rule, never a default. Fails closed when the settlement
    station id is missing or the literal ``"None"`` string (V19).
    """
    if metric not in ("high", "low"):
        raise ResolutionError(f"METRIC_INVALID: {metric!r}")

    sem = SettlementSemantics.for_city(city)
    station_id = _station_id_for_city(city, sem)
    if not station_id or station_id == "None":
        raise ResolutionError(
            f"STATION_ID_MISSING: city={getattr(city, 'name', city)!r} "
            f"source_type={getattr(city, 'settlement_source_type', None)!r}"
        )

    return EventResolution(
        city=getattr(city, "name", str(city)),
        station_id=station_id,
        settlement_source_type=getattr(city, "settlement_source_type", "wu_icao"),
        resolution_source=sem.resolution_source,
        target_local_date=target_date,
        settlement_timezone=getattr(city, "timezone", ""),
        metric=metric,
        measurement_unit=sem.measurement_unit,
        settlement_step_native=DEFAULT_SETTLEMENT_STEP_NATIVE,
        precision=sem.precision,
        rounding_rule=sem.rounding_rule,
        finalization_local_time=_parse_finalization_time(sem.finalization_time),
        semantics_version=SEMANTICS_VERSION,
    )
