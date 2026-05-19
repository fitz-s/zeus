# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4, topology packet "phase0-pr4-decision-group-id"
"""DecisionGroupId NewType and canonical hash constructor.

Phase 0 PR 4 — production implementation.
"""

import hashlib
from typing import NewType

# ---------------------------------------------------------------------------
# Type guard
# ---------------------------------------------------------------------------

DecisionGroupId = NewType("DecisionGroupId", str)
"""Opaque type-level guard for decision group identity.

Usage:
    gid: DecisionGroupId = decision_group_id_v1_hash(
        market_id="0xabc",
        target_date="2026-06-01",
        forecast_available_at="2026-05-25T12:00:00",
        source_id="tigge_mars",
        data_version="v2.3",
        bin_index=3,
        lead_days_bucket=7,
    )

Note: NewType is erased at runtime — no isinstance check is possible.
Mypy enforces the constraint statically.
"""


# ---------------------------------------------------------------------------
# Canonical constructor
# ---------------------------------------------------------------------------

# Canonical separator — chosen to be outside base-64 alphabet to avoid
# collisions between adjacent field concatenations.
_V1_SEP = "|"
# Output prefix makes the version explicit in stored values.
_V1_PREFIX = "dgid_v1_"
# Hex digest truncation length (chars). 16 hex chars = 64 bits of SHA-256,
# probability of collision across 91M rows ≈ 2.3×10⁻⁴ — acceptable for a
# surrogate grouping key that is always verified by the full tuple query.
_V1_DIGEST_CHARS = 16


def decision_group_id_v1_hash(
    *,
    market_id: str,
    target_date: str,
    forecast_available_at: str,
    source_id: str,
    data_version: str,
    bin_index: int,
    lead_days_bucket: int,
) -> DecisionGroupId:
    """Return the canonical v1 DecisionGroupId for a calibration context.

    Hash algorithm
    --------------
    Canonical form (pipe-delimited, field order is version-locked):

        ``"{market_id}|{target_date}|{forecast_available_at}|{source_id}|"``
        ``"{data_version}|{bin_index:05d}|{lead_days_bucket:03d}"``

    SHA-256 hexdigest of the UTF-8-encoded canonical form, truncated to
    16 chars, prefixed with "dgid_v1_".

    Example output: ``"dgid_v1_3f8a2b1c4e5d6789"``

    Version lock
    ------------
    The field order, separator, and digest-char count are frozen at v1.
    Any change to the algorithm requires a new version prefix ("dgid_v2_")
    and a full-table rehash of calibration_pairs_v2.

    Args:
        market_id:             Polymarket condition ID; pass as-is (no stripping).
        target_date:           ISO-8601 date string (e.g., "2026-06-01").
        forecast_available_at: ISO-8601 datetime string of forecast origin.
        source_id:             Data source identifier (e.g., "tigge_mars").
        data_version:          Data pipeline version string (e.g., "v2.3").
        bin_index:             Zero-based ordinal position of the bin (>= 0).
        lead_days_bucket:      Integer days-ahead bucket (> 0, e.g., 1, 3, 7, 14).

    Returns:
        DecisionGroupId: opaque str wrapper; stable across process restarts.

    Raises:
        ValueError: if any argument is outside its valid domain.
    """
    # Input validation — permanent API contracts.
    if not market_id:
        raise ValueError("market_id must be a non-empty string")
    if not target_date:
        raise ValueError("target_date must be a non-empty string")
    if not forecast_available_at:
        raise ValueError("forecast_available_at must be a non-empty string")
    if not source_id:
        raise ValueError("source_id must be a non-empty string")
    if not data_version:
        raise ValueError("data_version must be a non-empty string")
    if bin_index < 0:
        raise ValueError(f"bin_index must be >= 0, got {bin_index!r}")
    if lead_days_bucket <= 0:
        raise ValueError(f"lead_days_bucket must be > 0, got {lead_days_bucket!r}")

    canonical = (
        f"{market_id}{_V1_SEP}"
        f"{target_date}{_V1_SEP}"
        f"{forecast_available_at}{_V1_SEP}"
        f"{source_id}{_V1_SEP}"
        f"{data_version}{_V1_SEP}"
        f"{bin_index:05d}{_V1_SEP}"
        f"{lead_days_bucket:03d}"
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_V1_DIGEST_CHARS]
    return DecisionGroupId(f"{_V1_PREFIX}{digest}")
