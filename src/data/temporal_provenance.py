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
from typing import Iterable

# Minimum provenance a LIVE-consuming row must carry. (Write-time captured_at/imported_at are
# diagnostic; the LOAD-BEARING fields are the source/event identity that establish WHICH datum.)
REQUIRED_LIVE_PROVENANCE: frozenset[str] = frozenset({
    "source_id",
    "source_run_id",
    "source_issue_time",     # source/event-time anchor (NOT a write-time field)
    "target_local_date",     # the civil-day the datum belongs to
    "data_version",          # the semantic product identity (e.g. mx2t3 vs mx2t6)
    "captured_at",           # write-time, retained for audit
})

READINESS_GATE_FLAG = "ZEUS_FRONTIER_READINESS_GATE"

# Authority tiers that may NOT authorize live readiness (spec Data Type Taxonomy).
_NON_LIVE_AUTHORITY_TIERS: frozenset[str] = frozenset({
    "RECONSTRUCTED",                  # Open-Meteo previous-runs / TIGGE archive
    "ARCHIVE",
    "SHADOW",
    "BACKFILL",
    "DIAGNOSTIC",
})


def live_reader_requires_provenance() -> bool:
    """True only when the operator has armed the row-level provenance gate. Default OFF."""
    return os.environ.get(READINESS_GATE_FLAG, "0").strip().lower() in ("1", "true", "yes")


def missing_live_provenance(columns: Iterable[str]) -> list[str]:
    """Return the REQUIRED_LIVE_PROVENANCE fields absent from ``columns`` (empty = complete)."""
    present = set(columns)
    return sorted(REQUIRED_LIVE_PROVENANCE - present)


def row_has_live_provenance(columns: Iterable[str]) -> bool:
    return not missing_live_provenance(columns)


def can_authorize_live_readiness(authority_tier: str, live_authorized: bool) -> bool:
    """Fail-closed: a row authorizes live readiness only if it is explicitly live_authorized AND
    its authority tier is not a non-live (reconstructed/archive/shadow/backfill/diagnostic) tier.
    """
    if not live_authorized:
        return False
    return (authority_tier or "").strip().upper() not in _NON_LIVE_AUTHORITY_TIERS
