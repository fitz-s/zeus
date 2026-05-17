# Created: 2026-05-17
# Last reused/audited: 2026-05-17
# Authority basis: SCAFFOLD.md §4 FM-10 + EXECUTION_PLAN.md W5 + fatal_misreads.yaml FM-10 entry
"""Typed source-family system — FM-10 structural antibody.

Prevents SEMANTIC_SOURCE_FAMILY_FALSE_EQUIVALENCE: agents conflating IFS grid
identifiers with ENS grid identifiers, or settlement sources with day0/hourly
sources. The wrong combinations must be UNCONSTRUCTABLE, not merely detectable.

Mechanism: distinct frozen dataclass types per family. A function that accepts
an IFSGridId will raise TypeError at construction if given an ENSGridId object.
Python's `NewType` provides only static-typing hints (zero runtime enforcement);
this module uses distinct concrete types so isinstance checks and function
signatures enforce family boundaries at runtime.

Source-family taxonomy (locked per SCAFFOLD §4 FM-10):
  Grid families:
    IFSGridId   — ECMWF IFS model grid identifiers
    ENSGridId   — ECMWF ENS ensemble grid identifiers
  Settlement-timeline families:
    SettlementSourceId — settlement-window source identifiers
    Day0SourceId       — day0 (same-day) source identifiers
    HourlySourceId     — sub-daily hourly source identifiers

Cross-family combinations are structurally blocked: any function typed to accept
IFSGridId will raise TypeError if passed an ENSGridId at runtime. No runtime
validator needed — the wrong object simply cannot be constructed as the right type.

Note: FM-10 prevention is enforced at assert_*() call boundaries. Untyped functions
that consume source-family objects via attribute access (e.g., obj.identifier) can
still bypass; Phase-2 will add a CI lint for missing assert_* calls on source-family
parameters.

Usage:
    ifs_id = IFSGridId("IFS:EGLL:2026-06-01")
    ens_id = ENSGridId("ENS:EGLL:2026-06-01")
    # These are different types — cannot be mixed in typed function signatures.

    sett_id = SettlementSourceId("NCDC:EGLL:2026-06-01")
    day0_id = Day0SourceId("NCDC:EGLL:2026-06-01")
    # Different types — settlement vs day0 are structurally distinct.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


# ---------------------------------------------------------------------------
# Grid families
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IFSGridId:
    """Identifier for an ECMWF IFS (deterministic) model grid source.

    Cannot be combined with ENSGridId — distinct runtime type enforces boundary.
    """

    FAMILY: ClassVar[str] = "IFS"

    identifier: str

    def __post_init__(self) -> None:
        if not self.identifier:
            raise ValueError("IFSGridId.identifier must be non-empty")

    def __repr__(self) -> str:
        return f"IFSGridId({self.identifier!r})"


@dataclass(frozen=True)
class ENSGridId:
    """Identifier for an ECMWF ENS (ensemble) model grid source.

    Cannot be combined with IFSGridId — distinct runtime type enforces boundary.
    """

    FAMILY: ClassVar[str] = "ENS"

    identifier: str

    def __post_init__(self) -> None:
        if not self.identifier:
            raise ValueError("ENSGridId.identifier must be non-empty")

    def __repr__(self) -> str:
        return f"ENSGridId({self.identifier!r})"


# ---------------------------------------------------------------------------
# Settlement-timeline families
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SettlementSourceId:
    """Identifier for a settlement-window weather source.

    Cannot be combined with Day0SourceId or HourlySourceId.
    Settlement sources are used for final market resolution only.
    """

    FAMILY: ClassVar[str] = "SETTLEMENT"

    identifier: str

    def __post_init__(self) -> None:
        if not self.identifier:
            raise ValueError("SettlementSourceId.identifier must be non-empty")

    def __repr__(self) -> str:
        return f"SettlementSourceId({self.identifier!r})"


@dataclass(frozen=True)
class Day0SourceId:
    """Identifier for a day0 (same-day, pre-settlement) weather source.

    Cannot be combined with SettlementSourceId or HourlySourceId.
    """

    FAMILY: ClassVar[str] = "DAY0"

    identifier: str

    def __post_init__(self) -> None:
        if not self.identifier:
            raise ValueError("Day0SourceId.identifier must be non-empty")

    def __repr__(self) -> str:
        return f"Day0SourceId({self.identifier!r})"


@dataclass(frozen=True)
class HourlySourceId:
    """Identifier for a sub-daily hourly weather source.

    Cannot be combined with SettlementSourceId or Day0SourceId.
    """

    FAMILY: ClassVar[str] = "HOURLY"

    identifier: str

    def __post_init__(self) -> None:
        if not self.identifier:
            raise ValueError("HourlySourceId.identifier must be non-empty")

    def __repr__(self) -> str:
        return f"HourlySourceId({self.identifier!r})"


# ---------------------------------------------------------------------------
# Family sentinels for assertion guards
# ---------------------------------------------------------------------------

# All grid families — use isinstance(x, GRID_FAMILIES) to assert grid boundary.
GRID_FAMILIES = (IFSGridId, ENSGridId)

# All timeline families — use isinstance(x, TIMELINE_FAMILIES) to assert timeline boundary.
TIMELINE_FAMILIES = (SettlementSourceId, Day0SourceId, HourlySourceId)

# All source families combined.
ALL_SOURCE_FAMILIES = GRID_FAMILIES + TIMELINE_FAMILIES


def assert_ifs_grid(source_id: object) -> IFSGridId:
    """Assert that source_id is an IFSGridId. Raises TypeError on cross-family input."""
    if not isinstance(source_id, IFSGridId):
        raise TypeError(
            f"Expected IFSGridId but got {type(source_id).__name__!r}. "
            "IFS and ENS grid identifiers are not interchangeable — "
            "check FM-10 in architecture/fatal_misreads.yaml."
        )
    return source_id


def assert_ens_grid(source_id: object) -> ENSGridId:
    """Assert that source_id is an ENSGridId. Raises TypeError on cross-family input."""
    if not isinstance(source_id, ENSGridId):
        raise TypeError(
            f"Expected ENSGridId but got {type(source_id).__name__!r}. "
            "IFS and ENS grid identifiers are not interchangeable — "
            "check FM-10 in architecture/fatal_misreads.yaml."
        )
    return source_id


def assert_settlement_source(source_id: object) -> SettlementSourceId:
    """Assert that source_id is a SettlementSourceId. Raises TypeError on cross-timeline input."""
    if not isinstance(source_id, SettlementSourceId):
        raise TypeError(
            f"Expected SettlementSourceId but got {type(source_id).__name__!r}. "
            "Settlement, day0, and hourly sources are not interchangeable — "
            "check FM-10 in architecture/fatal_misreads.yaml."
        )
    return source_id


def assert_day0_source(source_id: object) -> Day0SourceId:
    """Assert that source_id is a Day0SourceId. Raises TypeError on cross-timeline input."""
    if not isinstance(source_id, Day0SourceId):
        raise TypeError(
            f"Expected Day0SourceId but got {type(source_id).__name__!r}. "
            "Settlement, day0, and hourly sources are not interchangeable — "
            "check FM-10 in architecture/fatal_misreads.yaml."
        )
    return source_id


def assert_hourly_source(source_id: object) -> HourlySourceId:
    """Assert that source_id is a HourlySourceId. Raises TypeError on cross-timeline input."""
    if not isinstance(source_id, HourlySourceId):
        raise TypeError(
            f"Expected HourlySourceId but got {type(source_id).__name__!r}. "
            "Settlement, day0, and hourly sources are not interchangeable — "
            "check FM-10 in architecture/fatal_misreads.yaml."
        )
    return source_id
