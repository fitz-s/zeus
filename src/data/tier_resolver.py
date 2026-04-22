# Created: 2026-04-21
# Last reused/audited: 2026-04-21
# Authority basis: plan v3 (.omc/plans/observation-instants-migration-iter3.md)
#                  step2 (docs/operations/task_2026-04-21_gate_f_data_backfill/
#                         step2_phase0_pilot_plan.md)
"""Deterministic tier resolution for observation_instants_v2 source routing.

Maps every Zeus city to exactly one of three tiers based on its
``settlement_source_type`` field in ``config/cities.json``. Tier choice
determines which hourly-observation client is called during backfill and
which ``source`` string is allowed by the typed v2 writer (antibody A2).

Structural principle P1 (plan v3 L9): *settlement source IS observation
source*. There is deliberately no OpenMeteo Tier-4 escape hatch — a city
must either have a native hourly source or accept a gap (Hong Kong).

Public API
----------
- ``class Tier`` — the three Phase 0 tiers.
- ``tier_for_city(city_name, target_date=None) -> Tier`` — resolves a city
  to its tier. The ``target_date`` parameter is accepted for forward
  compatibility with per-city date-range schedules; it is ignored today.
- ``cities_in_tier(tier) -> frozenset[str]`` — inverse mapping for audit.
- ``TIER_SCHEDULE`` — the full ``dict[city_name, Tier]`` (public constant
  for antibody A3 and ``scripts/audit_observation_instants_v2.py``).
- ``TIER_ALLOWED_SOURCES`` — per-tier whitelist of acceptable ``source``
  column values (antibody A2 whitelist for the v2 writer).

Invariants (checked by ``tests/test_tier_resolver.py``, antibody A3)
-------------------------------------------------------------------
- I1. Every city in ``src.config.cities_by_name`` has exactly one tier.
- I2. ``cities_in_tier(Tier.WU_ICAO)`` equals
  ``{c.name for c in cities if settlement_source_type == 'wu_icao'}``.
- I3. ``cities_in_tier(Tier.OGIMET_METAR)`` equals
  ``{c.name for c in cities if settlement_source_type == 'noaa'}``.
- I4. ``cities_in_tier(Tier.HKO_NATIVE)`` equals ``{'Hong Kong'}``.
- I5. ``settlement_source_type == 'cwa_station'`` raises
  ``UnsupportedTierError`` — no city uses this today (Taipei migrated
  2026-04-15). A future re-introduction needs an explicit tier decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional

from src.config import cities_by_name


class Tier(Enum):
    """Hourly-observation source tier for observation_instants_v2.

    Phase 0 exposes exactly three tiers. There is deliberately no
    ``OPENMETEO_GRID`` member — plan v3 rejects grid-snap routing (5-15 km
    station offset is chaotic in rough terrain; see plan v3 L29).
    """

    WU_ICAO = "wu_icao"
    OGIMET_METAR = "ogimet_metar"
    HKO_NATIVE = "hko_native"


@dataclass(frozen=True)
class UnsupportedTierError(Exception):
    """Raised when a city cannot be mapped to a Phase 0 tier.

    Current triggers:
    - ``settlement_source_type == 'cwa_station'`` (no city uses this today;
      a future activation must add a new ``Tier`` member or migrate the
      city to ``wu_icao`` first, as Taipei did 2026-04-15).
    - Unknown ``settlement_source_type`` (covered by ``src/config.py``
      validation, but defensive here too).
    - Unknown ``city_name`` (not present in ``cities_by_name``).
    """

    reason: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.reason


# Mapping: settlement_source_type -> Tier
_SSTYPE_TO_TIER: dict[str, Tier] = {
    "wu_icao": Tier.WU_ICAO,
    "noaa": Tier.OGIMET_METAR,  # NOAA settlement via Ogimet METAR mirror
    "hko": Tier.HKO_NATIVE,
}


def _build_tier_schedule() -> dict[str, Tier]:
    """Eagerly materialize the (city_name -> Tier) mapping at import time.

    Import-time construction ensures the A3 antibody test sees exactly the
    same mapping the live backfill driver will see, with zero opportunity
    for config drift between resolver construction and use.
    """
    schedule: dict[str, Tier] = {}
    for name, city in cities_by_name.items():
        sstype = city.settlement_source_type
        tier = _SSTYPE_TO_TIER.get(sstype)
        if tier is None:
            raise UnsupportedTierError(
                reason=(
                    f"{name}: settlement_source_type={sstype!r} has no "
                    "Phase 0 tier mapping. cwa_station is deliberately "
                    "rejected (Taipei migrated 2026-04-15); any new "
                    "sstype requires an explicit Tier member."
                )
            )
        schedule[name] = tier
    return schedule


TIER_SCHEDULE: dict[str, Tier] = _build_tier_schedule()


# Per-tier source whitelist (antibody A2). Keep in sync with the source_tag
# strings the hourly clients actually emit.
TIER_ALLOWED_SOURCES: dict[Tier, frozenset[str]] = {
    Tier.WU_ICAO: frozenset({"wu_icao_history"}),
    Tier.OGIMET_METAR: frozenset(
        {
            "ogimet_metar_ltfm",  # Istanbul (NOAA-mirrored)
            "ogimet_metar_uuww",  # Moscow (NOAA-mirrored)
            "ogimet_metar_llbg",  # Tel Aviv (NOAA-mirrored)
        }
    ),
    Tier.HKO_NATIVE: frozenset({"hko_hourly_accumulator"}),
}


# Per-city expected source (antibody A2-strong). The Ogimet tier has
# three distinct source tags — one per station — so the tier-level
# whitelist above is necessary but not sufficient. This mapping collapses
# to a single expected source per city, catching cross-station mis-writes
# (e.g. Moscow row with LLBG source) at write time instead of audit time.
def _build_expected_source_by_city() -> dict[str, str]:
    """One expected source string per city, derived from station identity.

    Iterates ``TIER_SCHEDULE`` directly (not ``cities_in_tier``) because
    this helper runs at module-import time, before ``cities_in_tier`` is
    bound to a name.
    """
    # Ogimet tier: source tag encodes the specific station.
    _OGIMET_STATION_MAP = {
        "Istanbul": "ogimet_metar_ltfm",
        "Moscow": "ogimet_metar_uuww",
        "Tel Aviv": "ogimet_metar_llbg",
    }
    expected: dict[str, str] = {}
    for name, tier in TIER_SCHEDULE.items():
        if tier is Tier.WU_ICAO:
            # All WU cities share the generic 'wu_icao_history' source;
            # station_id disambiguates downstream queries.
            expected[name] = "wu_icao_history"
        elif tier is Tier.OGIMET_METAR:
            if name not in _OGIMET_STATION_MAP:
                raise UnsupportedTierError(
                    reason=(
                        f"Ogimet city {name!r} has no station→source "
                        "mapping. Add to _OGIMET_STATION_MAP in "
                        "tier_resolver.py."
                    )
                )
            expected[name] = _OGIMET_STATION_MAP[name]
        elif tier is Tier.HKO_NATIVE:
            expected[name] = "hko_hourly_accumulator"
        else:  # pragma: no cover - defensive, Tier enum is closed
            raise UnsupportedTierError(
                reason=f"Tier {tier!r} has no expected-source rule."
            )
    return expected


EXPECTED_SOURCE_BY_CITY: dict[str, str] = _build_expected_source_by_city()


def expected_source_for_city(city_name: str) -> str:
    """Return the ONE source string the v2 writer accepts for *city_name*.

    Stronger than ``allowed_sources_for_tier`` because it pins per-station
    identity within multi-station tiers. This is what the v2 writer's A2
    check actually uses; the tier-level whitelist remains available for
    audit scripts that want to see the tier's full option space.
    """
    if city_name not in EXPECTED_SOURCE_BY_CITY:
        raise UnsupportedTierError(
            reason=(
                f"{city_name!r} is not in EXPECTED_SOURCE_BY_CITY; "
                "tier_resolver may be out of sync with cities.json."
            )
        )
    return EXPECTED_SOURCE_BY_CITY[city_name]


def tier_for_city(city_name: str, target_date: Optional[date] = None) -> Tier:
    """Resolve *city_name* to its hourly-observation tier.

    Parameters
    ----------
    city_name:
        Must be a key in ``src.config.cities_by_name``. Raises
        ``UnsupportedTierError`` if unknown.
    target_date:
        Accepted for forward compatibility with future date-range
        schedules (e.g. a city migrating mid-2025 would use the pre-
        and post-migration tier depending on ``target_date``). Ignored
        today; Phase 0 tier is scalar per city.

    Returns
    -------
    Tier
        The exact tier enum member. Never returns ``None``.

    Raises
    ------
    UnsupportedTierError
        If *city_name* is unknown or its sstype lacks a tier mapping.
    """
    # target_date intentionally unused today; documented for API stability.
    del target_date
    if city_name not in TIER_SCHEDULE:
        raise UnsupportedTierError(
            reason=(
                f"{city_name!r} is not in cities_by_name; add it to "
                "config/cities.json first."
            )
        )
    return TIER_SCHEDULE[city_name]


def cities_in_tier(tier: Tier) -> frozenset[str]:
    """Return the set of city names assigned to *tier*.

    Used by:
    - ``tests/test_tier_resolver.py`` (antibody A3).
    - ``scripts/audit_observation_instants_v2.py`` for per-tier row-count
      reports.
    - ``scripts/backfill_obs_v2.py`` to split the worklist by tier and
      dispatch to the right hourly client.
    """
    return frozenset(name for name, t in TIER_SCHEDULE.items() if t is tier)


def allowed_sources_for_tier(tier: Tier) -> frozenset[str]:
    """Return the frozenset of ``source`` values the v2 writer accepts for *tier*.

    Used by the v2 writer (antibody A2). Keeping the data on one module
    means adding a new source string to Tier X in exactly one place.
    """
    return TIER_ALLOWED_SOURCES[tier]
