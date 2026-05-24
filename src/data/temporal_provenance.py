# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: operator "Zeus Data Ingest + Collection Efficiency Refactor" spec §5
#   (Provenance/correctness efficiency) + §7 (Provenance columns); §"Data Type Taxonomy";
#   docs/operations/current/plans/data_temporal_kernel/PLAN.md (PR7).
"""Row-level temporal-provenance contract + live-reader gate — PR7 (validator + flag-gated gate).

A live-consuming row must carry enough temporal provenance to PROVE it is the right datum on the
right clock for the right local day. This module declares that required-field set and validates a
table's columns against it. It also provides the authority rule that backfill/shadow data can
never authorize live readiness.

PR7's SCHEMA MIGRATION (adding any missing columns to the live forecasts DB) is operator-gated
(forecast-class change → SCHEMA_FORECASTS_VERSION bump → daemon schema gate) and deferred. The
live-reader gate (``live_reader_requires_provenance``) defaults OFF behind ZEUS_FRONTIER_READINESS_GATE
so this module changes no runtime behavior on its own.
"""
from __future__ import annotations

import os
from typing import Iterable, Optional

# Provenance is NOT one-size-fits-all (PR review #329 F6): a forecast source_run row, a
# Polymarket executable-market snapshot, a venue user-channel fact, and a settlement row carry
# DIFFERENT identity fields. A single forecast-centric required set would false-block (or invite
# meaningless fields on) venue/market rows. So required provenance is keyed by data FAMILY.

# Forecast / source_run rows (the cycle-issued NWP path).
FORECAST_LIVE_PROVENANCE: frozenset[str] = frozenset({
    "source_id", "source_run_id",
    "source_issue_time",     # source/event-time anchor (NOT a write-time field)
    "target_local_date",     # the civil-day the datum belongs to
    "data_version",          # semantic product identity (e.g. mx2t3 vs mx2t6)
    "captured_at",
})
# Settlement-adjacent observation rows (WU/HKO/Ogimet).
OBSERVATION_LIVE_PROVENANCE: frozenset[str] = frozenset({
    "source_id", "target_local_date", "observation_time_utc", "captured_at",
})
# Polymarket market-topology rows (Gamma).
MARKET_TOPOLOGY_PROVENANCE: frozenset[str] = frozenset({
    "condition_id", "captured_at",
})
# Polymarket executable-market snapshots (CLOB) — quote-time, not source-run semantics.
EXECUTABLE_MARKET_SNAPSHOT_PROVENANCE: frozenset[str] = frozenset({
    "condition_id", "captured_at", "freshness_deadline", "authority_tier",
})
# Authenticated venue user-channel facts (WS).
VENUE_USER_CHANNEL_PROVENANCE: frozenset[str] = frozenset({
    "condition_id", "received_at",
})

FAMILY_REQUIRED_PROVENANCE: dict[str, frozenset[str]] = {
    "forecast": FORECAST_LIVE_PROVENANCE,
    "observation": OBSERVATION_LIVE_PROVENANCE,
    "market": MARKET_TOPOLOGY_PROVENANCE,
    "executable_snapshot": EXECUTABLE_MARKET_SNAPSHOT_PROVENANCE,
    "venue_user": VENUE_USER_CHANNEL_PROVENANCE,
}

# Back-compat alias (default family = forecast).
REQUIRED_LIVE_PROVENANCE: frozenset[str] = FORECAST_LIVE_PROVENANCE

READINESS_GATE_FLAG = "ZEUS_FRONTIER_READINESS_GATE"

# ALLOW-LIST (fail-closed, PR review #329 F5): only these authority tiers may authorize live
# readiness. Anything not listed — including "" / unknown — CANNOT (the prior deny-list let
# unknown tiers through, contradicting the fail-closed contract).
_LIVE_AUTHORITY_TIERS: frozenset[str] = frozenset({
    "DERIVED_FROM_DISSEMINATION",     # ECMWF Open Data live forecast
    "LIVE",
    "SETTLEMENT_VENUE",               # venue-resolved settlement truth
    "OBSERVED",                       # settlement-adjacent observed fact
})


def live_reader_requires_provenance() -> bool:
    """True only when the operator has armed the row-level provenance gate. Default OFF."""
    return os.environ.get(READINESS_GATE_FLAG, "0").strip().lower() in ("1", "true", "yes")


def missing_live_provenance(columns: Iterable[str], family: str = "forecast") -> list[str]:
    """Return required provenance fields for ``family`` absent from ``columns`` (empty=complete).

    Unknown family is fail-closed: returns a sentinel so callers cannot treat it as complete.
    """
    required = FAMILY_REQUIRED_PROVENANCE.get(family)
    if required is None:
        return [f"<unknown-provenance-family:{family}>"]
    return sorted(required - set(columns))


def row_has_live_provenance(columns: Iterable[str], family: str = "forecast") -> bool:
    return not missing_live_provenance(columns, family)


def can_authorize_live_readiness(
    authority_tier: str,
    live_authorized: bool,
    *,
    allowed_tiers: Optional[frozenset[str]] = None,
) -> bool:
    """Fail-closed ALLOW-LIST: authorizes live readiness only if explicitly live_authorized AND
    the authority tier is in the allow-list. Unknown/empty tier ⇒ False.

    ``allowed_tiers`` lets a family pass its own live-authority set; defaults to the global
    live-tier allow-list.
    """
    if not live_authorized:
        return False
    tiers = allowed_tiers if allowed_tiers is not None else _LIVE_AUTHORITY_TIERS
    return (authority_tier or "").strip().upper() in tiers
