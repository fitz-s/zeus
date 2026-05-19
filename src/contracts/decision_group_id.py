# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4, topology packet "phase0-pr4-decision-group-id"
"""DecisionGroupId NewType and canonical hash constructor.

Phase 0 PR 4 — SCAFFOLD only. No business logic implemented here.
Production implementation deferred to PR 4 implementation phase.

Spec cross-reference:
  - §14.6: DecisionGroupId uniquely identifies a group of calibration pairs
    that share a common decision context (same market × forecast bucket × resolution era).
  - §14.7: decision_group_id is derived from (market_id, bin_index, lead_days_bucket)
    via a deterministic v1 hash.
  - INV-group-id-type: All call sites must accept DecisionGroupId, not raw str.
    Mypy enforces this at static analysis time.
"""

from typing import NewType

# ---------------------------------------------------------------------------
# Type guard
# ---------------------------------------------------------------------------

DecisionGroupId = NewType("DecisionGroupId", str)
"""Opaque type-level guard for decision group identity.

Usage:
    gid: DecisionGroupId = decision_group_id_v1_hash(
        market_id="0xabc",
        bin_index=3,
        lead_days_bucket=7,
    )

    # WRONG (mypy error under strict):
    gid: DecisionGroupId = "0xabc|3|7"  # noqa

Note: NewType is erased at runtime — no isinstance check is possible.
Mypy enforces the constraint statically.
"""


# ---------------------------------------------------------------------------
# Canonical constructor — SCAFFOLD: signature only, no implementation
# ---------------------------------------------------------------------------


def decision_group_id_v1_hash(
    *,
    market_id: str,
    bin_index: int,
    lead_days_bucket: int,
) -> DecisionGroupId:
    """Return the canonical v1 DecisionGroupId for a calibration context.

    Hash algorithm: SHA-256 of the canonical form
        ``"{market_id}|{bin_index:05d}|{lead_days_bucket:03d}"``,
        hex-encoded, truncated to 16 chars, prefixed with "dgid_v1_".

    Example output: ``"dgid_v1_3f8a2b1c4e5d6789"``

    Args:
        market_id:         Polymarket condition ID (hex string, no 0x prefix
                           stripping — pass as-is from the market record).
        bin_index:         Zero-based ordinal position of the bin within its
                           forecast grid.
        lead_days_bucket:  Integer days-ahead bucket (e.g., 1, 3, 7, 14).

    Returns:
        DecisionGroupId: opaque str wrapper; stable across process restarts.

    Raises:
        ValueError: if any argument is outside its valid domain.

    SCAFFOLD: raise NotImplementedError pending PR 4 implementation phase.
    """
    # Implementation deferred — SCAFFOLD only.
    raise NotImplementedError(
        "decision_group_id_v1_hash: implementation deferred to PR 4 production phase"
    )
