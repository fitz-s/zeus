# Created: 2026-09-29
# Last reused/audited: 2026-10-01
"""Station-bound current observations and their settlement roles.

Adapters own fixed endpoints. Configuration cannot inject URLs, SQL, or code.
A shared request budget scales with station count, not with city-name branches.
Settlement-authorized samples remain distinct from final daily resolver publication.

Roles: a canonical resolver product is the resolver's own feed. An optional
fast transport is admitted only by bound evidence that its value equals the
resolver's at the same station and instant under the contract's unit/rounding
law AND that it beat every current path at that value-matched instant. An
invalid fast admission is omitted; incumbent channels keep serving.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
import json
import logging
import math
from pathlib import Path
import re
from typing import Any

from src.data.fmi_airport_temperature import FmiStation, SOURCE_CHANNEL

logger = logging.getLogger(__name__)
REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "physical_current_sources.json"


class SourceRole(str, Enum):
    CANONICAL_RESOLVER = "canonical_resolver"
    FAST_ADMISSION = "fast_admission"
    PHYSICAL_ONLY = "physical_only"


# Resolver products by settlement source type; nothing else may claim the role.
_CANONICAL = {"noaa_wrh": "noaa", "wu_station_history": "wu_icao"}
# Current paths every fast admission must beat, besides other registry routes
# at its station: the AWC METAR feed and the resolver channel itself.
CURRENT_COMPARATORS = ("awc", "resolver")


@dataclass(frozen=True)
class PhysicalCurrentSource:
    provider: str
    source_channel: str
    station_id: str
    settlement_source_types: tuple[str, ...]
    unit: str
    minimum_poll_seconds: float
    station: FmiStation | None
    identity: dict[str, Any] = field(default_factory=dict)
    role: SourceRole = SourceRole.PHYSICAL_ONLY

    @property
    def settlement_authorized(self) -> bool:
        return self.role is not SourceRole.PHYSICAL_ONLY


def _counts_proven(proof: Any) -> bool:
    pairs, exact = proof.get("n_pairs"), proof.get("n_exact")
    return (type(pairs) is int and type(exact) is int and pairs > 0 and exact == pairs
            and proof.get("mismatches") == [] and not proof.get("version_conflicts", []))


def _role(row: dict[str, Any]) -> SourceRole:
    try:
        role = SourceRole(row.get("role"))
    except ValueError:
        raise ValueError("PHYSICAL_CURRENT_ROLE_INVALID") from None
    if role is SourceRole.CANONICAL_RESOLVER and (
        tuple(row["settlement_source_types"]) != (_CANONICAL.get(row["provider"]),)
        or not _counts_proven(row.get("value_identity_proof", {}))
    ):
        raise ValueError("PHYSICAL_CURRENT_CANONICAL_ROLE_INVALID")
    return role


def fast_admission_defect(row: dict[str, Any], rivals: frozenset[str] = frozenset()) -> str | None:
    """Return why a fast-admission row is not proven, or None when it is.

    The proof binds station, channel and unit; its counts show exact equality;
    the lead instant carries a value pair equal under the city's settlement
    rounding; and at that instant the candidate's latest possible receipt
    precedes the earliest possible receipt of every current path (``rivals``
    are other registry providers at the same station).
    """
    from src.config import cities_by_name
    from src.contracts.settlement_semantics import SettlementSemantics

    station, channel, unit = row["station_id"], row["provider"], row["unit"]
    proof = row.get("value_identity_proof") or {}
    city = cities_by_name.get(str(proof.get("city")))
    if (proof.get("station"), proof.get("channel"), proof.get("unit")) != (station, channel, unit):
        return "PROOF_IDENTITY"
    if city is None or str(city.wu_station).upper() != station or city.settlement_unit != unit:
        return "PROOF_CITY"
    if not _counts_proven(proof):
        return "VALUE_IDENTITY_COUNTS"
    lead = (row.get("latency_evidence") or {}).get("first_proven_lead") or {}
    when, candidate = lead.get("observation"), lead.get("candidate") or {}
    if not when or (candidate.get("station"), candidate.get("channel"), candidate.get("observed_at")) != (
            station, channel, when):
        return "LEAD_IDENTITY"
    paired = lead.get("paired_value") or {}
    sides = [paired.get("candidate") or {}, paired.get("resolver") or {}]
    if any(side.get("unit") != unit or type(side.get("value")) not in (int, float) for side in sides):
        return "LEAD_UNPAIRED"
    semantics = SettlementSemantics.for_city(city)
    if semantics.round_single(sides[0]["value"]) != semantics.round_single(sides[1]["value"]):
        return "LEAD_VALUE_MISMATCH"
    try:
        upper = float(candidate["lag_upper_ms"])
        beaten = {c["channel"] for c in lead.get("comparators", ())
                  if c["interval"]["observed_at"] == when
                  and c["interval"].get("station", station) == station
                  and upper < float(c["interval"]["lag_lower_ms"])}
    except (KeyError, TypeError, ValueError):
        return "LEAD_COMPARATOR_MALFORMED"
    missing = (set(CURRENT_COMPARATORS) | set(rivals)) - beaten
    return "LEAD_NOT_FASTER:" + ",".join(sorted(missing)) if missing else None


@lru_cache(maxsize=4)
def _load(path: str, mtime_ns: int, size: int) -> tuple[tuple[PhysicalCurrentSource, ...], float]:
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != 1 or data.get("role") != "station_temperature_observations":
        raise ValueError("PHYSICAL_CURRENT_REGISTRY_ROLE")
    sources = []
    seen = set()
    by_station: dict[str, set[str]] = {}
    for row in data["sources"]:
        by_station.setdefault(row["station_id"], set()).add(row["provider"])
    for row in data["sources"]:
        role = _role(row)
        if role is SourceRole.FAST_ADMISSION:
            defect = fast_admission_defect(row, frozenset(by_station[row["station_id"]] - {row["provider"]}))
            if defect is not None:
                # SCOPE: this one optional route. DRAIN/RESET: a config with
                # bound evidence on restart. Incumbent channels keep serving.
                logger.error("PHYSICAL_CURRENT_FAST_ADMISSION_OMITTED provider=%s station=%s reason=%s",
                             row["provider"], row["station_id"], defect)
                continue
        if row["provider"] != "fmi_wfs":
            from src.data.station_temperature_adapters import CHANNELS
            identity = row["identity"]
            native_id = str(identity["provider_station"])
            key = (row["station_id"], row["source_channel"])
            seconds = float(row["minimum_poll_seconds"])
            kinds = tuple(row["settlement_source_types"])
            expected_channel = (f"noaa_wrh_{row['station_id'].lower()}" if row["provider"] == "noaa_wrh"
                                else CHANNELS.get(row["provider"]))
            unit = row["unit"]
            if (row["source_channel"] != expected_channel
                or key in seen or not re.fullmatch(r"[A-Z]{4}", row["station_id"])
                or not re.fullmatch(r"[A-Za-z0-9:]+", native_id)
                or not math.isfinite(seconds) or seconds < 60
                or unit not in ({"C", "F"} if row["provider"] == "noaa_wrh" else {"C"})
                or (row["provider"] == "noaa_wrh" and
                    (native_id != row["station_id"] or identity.get("resolver_view") not in {"hourly", "all"}))
                or (row["provider"] in {"mgm_metar", "imd_olbs_metar"} and native_id != row["station_id"])
                or not kinds or any(t not in {"noaa", "wu_icao"} for t in kinds)):
                raise ValueError("PHYSICAL_CURRENT_ADAPTER_INVALID")
            sources.append(PhysicalCurrentSource(row["provider"], row["source_channel"], row["station_id"],
                                                  kinds, unit, seconds, None, dict(identity), role))
            seen.add(key)
            continue
        if row["provider"] != "fmi_wfs" or row["source_channel"] != SOURCE_CHANNEL or row["unit"] != "C":
            raise ValueError("PHYSICAL_CURRENT_ADAPTER_UNKNOWN")
        key = (row["station_id"], row["source_channel"])
        identity = row["identity"]
        latitude, longitude = float(identity["latitude"]), float(identity["longitude"])
        seconds = float(row["minimum_poll_seconds"])
        types = tuple(row["settlement_source_types"])
        if (key in seen or not re.fullmatch(r"[A-Z]{4}", row["station_id"])
            or not str(identity["fmisid"]).isdigit() or not str(identity["wmo"]).isdigit()
            or not identity["name"] or not -90 <= latitude <= 90 or not -180 <= longitude <= 180
            or not math.isfinite(seconds) or seconds < 1 or not types
            or any(t not in {"noaa", "wu_icao"} for t in types)):
            raise ValueError("PHYSICAL_CURRENT_STATION_INVALID")
        seen.add(key)
        station = FmiStation(row["station_id"], str(identity["fmisid"]), str(identity["wmo"]),
                             identity["name"], latitude, longitude)
        sources.append(PhysicalCurrentSource(row["provider"], row["source_channel"], row["station_id"],
                                              types, row["unit"], seconds, station, dict(identity), role))
    budget = data["providers"]["fmi_wfs"]
    fraction = float(budget["budget_fraction"])
    per_day, per_window = int(budget["requests_per_day"]), int(budget["requests_per_five_minutes"])
    requests = int(budget["requests_per_poll"])
    if not 0 < fraction <= 0.8 or not 0 < per_day <= 20000 or not 0 < per_window <= 600 or requests < 2:
        raise ValueError("PHYSICAL_CURRENT_PROVIDER_BUDGET_INVALID")
    fmi_sources = [s for s in sources if s.provider == "fmi_wfs"]
    period = max([1.0] + [s.minimum_poll_seconds for s in fmi_sources] + [
        math.ceil(requests * len(fmi_sources) * 86400 / (per_day * fraction)),
        math.ceil(requests * len(fmi_sources) * 300 / (per_window * fraction))])
    return tuple(sources), min([period] + [s.minimum_poll_seconds for s in sources if s.provider != "fmi_wfs"]) if fmi_sources else min([s.minimum_poll_seconds for s in sources] or [300.0])


@lru_cache(maxsize=4)
def load_physical_current_sources(path: Path | None = None) -> tuple[tuple[PhysicalCurrentSource, ...], float]:
    # Registry and scheduler cadence are one process-lifetime snapshot. Apply
    # source-count/budget changes together on restart, never hot-load new work
    # under the old faster scheduler interval.
    path = path or REGISTRY_PATH
    stat = path.stat()
    return _load(str(path), stat.st_mtime_ns, stat.st_size)


def physical_current_sources_for_city(city: Any) -> tuple[PhysicalCurrentSource, ...]:
    try:
        sources, _ = load_physical_current_sources()
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        logger.error("PHYSICAL_CURRENT_CONFIG_UNAVAILABLE error=%s", type(exc).__name__)
        return ()  # Optional evidence missing; the base belief still serves.
    return tuple(s for s in sources
                 if s.station_id == str(getattr(city, "wu_station", "") or "").upper()
                 and str(getattr(city, "settlement_source_type", "") or "").lower() in s.settlement_source_types
                 and s.unit == str(getattr(city, "settlement_unit", "") or "").upper())


def physical_current_poll_seconds() -> float:
    try:
        return load_physical_current_sources()[1]
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return 300.0  # Bad optional config cannot spin or stop the rest of ingest.
