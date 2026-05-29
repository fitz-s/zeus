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
# Daily settlement observation rows (`observations` table) — REAL columns (PR review #329 E):
# source/station_id/fetched_at/target_date, NOT source_id/captured_at.
DAILY_OBSERVATION_PROVENANCE: frozenset[str] = frozenset({
    "source", "station_id", "target_date", "fetched_at",
})
# V2 hourly observation-instant rows (`observation_instants_v2`) — distinct schema (R3 F11):
# utc_timestamp/imported_at/authority/data_version, NOT the daily fetched_at.
OBSERVATION_INSTANT_V2_PROVENANCE: frozenset[str] = frozenset({
    "source", "station_id", "target_date", "utc_timestamp", "imported_at",
    "authority", "data_version",
})
# Back-compat alias: bare "observation" = daily observation.
OBSERVATION_LIVE_PROVENANCE: frozenset[str] = DAILY_OBSERVATION_PROVENANCE
# Polymarket market-topology rows (Gamma / market_events) — uses created_at, not captured_at.
MARKET_TOPOLOGY_PROVENANCE: frozenset[str] = frozenset({
    "condition_id", "created_at",
})
# Polymarket executable-market snapshots (CLOB) — quote-time, not source-run semantics.
EXECUTABLE_MARKET_SNAPSHOT_PROVENANCE: frozenset[str] = frozenset({
    "condition_id", "captured_at", "freshness_deadline", "authority_tier",
})
# Authenticated venue user-channel facts (WS) — writer uses observed_at, not received_at.
VENUE_USER_CHANNEL_PROVENANCE: frozenset[str] = frozenset({
    "condition_id", "observed_at",
})

FAMILY_REQUIRED_PROVENANCE: dict[str, frozenset[str]] = {
    "forecast": FORECAST_LIVE_PROVENANCE,
    "observation": DAILY_OBSERVATION_PROVENANCE,          # alias of daily_observation
    "daily_observation": DAILY_OBSERVATION_PROVENANCE,
    "observation_instant_v2": OBSERVATION_INSTANT_V2_PROVENANCE,
    "market": MARKET_TOPOLOGY_PROVENANCE,
    "executable_snapshot": EXECUTABLE_MARKET_SNAPSHOT_PROVENANCE,
    "venue_user": VENUE_USER_CHANNEL_PROVENANCE,
}

# Back-compat alias (default family = forecast).
REQUIRED_LIVE_PROVENANCE: frozenset[str] = FORECAST_LIVE_PROVENANCE

READINESS_GATE_FLAG = "ZEUS_FRONTIER_READINESS_GATE"

# ALLOW-LIST (fail-closed, PR review #329 F5). PER-FAMILY (F6/F-R2-F): a CLOB tier is live for
# executable snapshots but NOT for forecasts. Default (no family) = forecast tiers only.
FAMILY_LIVE_AUTHORITY_TIERS: dict[str, frozenset[str]] = {
    "forecast": frozenset({"DERIVED_FROM_DISSEMINATION", "LIVE"}),
    "observation": frozenset({"OBSERVED", "WU", "HKO", "NOAA"}),
    "market": frozenset({"GAMMA"}),
    "executable_snapshot": frozenset({"CLOB", "GAMMA", "DATA"}),
    "venue_user": frozenset({"CLOB", "CHAIN"}),
    "settlement": frozenset({"SETTLEMENT_VENUE", "GAMMA", "CHAIN"}),
}
_LIVE_AUTHORITY_TIERS: frozenset[str] = FAMILY_LIVE_AUTHORITY_TIERS["forecast"]


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
    family: str = "forecast",
    allowed_tiers: Optional[frozenset[str]] = None,
) -> bool:
    """Fail-closed ALLOW-LIST: authorizes live readiness only if explicitly live_authorized AND
    the authority tier is in the FAMILY's allow-list. Unknown/empty tier ⇒ False.

    Per-family (PR review #329 F/R2-F): a CLOB tier authorizes an executable-snapshot row but
    NOT a forecast row. ``allowed_tiers`` overrides the family set; unknown family ⇒ empty set
    (fail-closed). Default family = forecast.
    """
    if not live_authorized:
        return False
    if allowed_tiers is not None:
        tiers = allowed_tiers
    else:
        tiers = FAMILY_LIVE_AUTHORITY_TIERS.get(family, frozenset())
    return (authority_tier or "").strip().upper() in tiers
