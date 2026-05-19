# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4.0–§4.2 (Path D natural-key reframe, v3)
"""DecisionNaturalKey NewType and canonical helper stubs.

Path D v3: (market_slug, temperature_metric, target_date, observation_time, decision_seq)
is the 5-component natural PK for decision_events.
condition_id is nullable enrichment — NOT in PK.
decision_event_id is the audit-only derived hash (deid_v1_ prefix — distinct from
calibration's dgid_v1_ used in decision_group_id.py).
Production pass fills NotImplementedError stubs.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal, NewType, Optional

# ---------------------------------------------------------------------------
# Core type
# ---------------------------------------------------------------------------

DecisionNaturalKey = NewType(
    "DecisionNaturalKey",
    tuple,  # runtime: tuple[str, Literal['high','low'], str, str, int]
)
"""5-tuple: (market_slug, temperature_metric, target_date, observation_time, decision_seq).
NewType erased at runtime — mypy enforces statically.
condition_id is NOT part of the key (nullable pre-discovery; market_slug is canonical).
"""


def make_decision_natural_key(
    market_slug: str,
    temperature_metric: Literal["high", "low"],
    target_date: str,
    observation_time: str,
    decision_seq: int,
) -> DecisionNaturalKey:
    """Construct and validate a DecisionNaturalKey. Raises ValueError on bad inputs."""
    if temperature_metric not in ("high", "low"):
        raise ValueError(f"temperature_metric must be 'high' or 'low', got {temperature_metric!r}")
    if decision_seq < 0:
        raise ValueError(f"decision_seq must be >= 0, got {decision_seq!r}")
    return DecisionNaturalKey(
        (market_slug, temperature_metric, target_date, observation_time, decision_seq)
    )


# ---------------------------------------------------------------------------
# Audit-only hash — Option β (writer-side computation)
# ---------------------------------------------------------------------------

_DEID_V1_PREFIX = "deid_v1_"
_DEID_V1_SEP = "|"
_DEID_V1_DIGEST_CHARS = 16


def decision_event_id_v1_hash(
    *,
    market_slug: str,
    temperature_metric: str,
    target_date: str,
    observation_time: str,
    decision_seq: int,
) -> str:
    """Compute the v1 decision_event_id for audit lookups.

    Namespace: deid_v1_ — DISTINCT from dgid_v1_ used in decision_group_id.py.
    Cross-namespace lookups must fail explicitly.

    Canonical form (pipe-delimited, field order is version-locked):
        "{market_slug}|{temperature_metric}|{target_date}|{observation_time}|{decision_seq:010d}"

    SHA-256 hexdigest truncated to 16 chars, prefixed with "deid_v1_".
    Example output: "deid_v1_3f8a2b1c4e5d6789"
    """
    if not market_slug:
        raise ValueError("market_slug must be non-empty")
    canonical = (
        f"{market_slug}{_DEID_V1_SEP}"
        f"{temperature_metric}{_DEID_V1_SEP}"
        f"{target_date}{_DEID_V1_SEP}"
        f"{observation_time}{_DEID_V1_SEP}"
        f"{decision_seq:010d}"
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_DEID_V1_DIGEST_CHARS]
    return f"{_DEID_V1_PREFIX}{digest}"


# ---------------------------------------------------------------------------
# Helper stubs — production pass fills bodies
# ---------------------------------------------------------------------------


def from_market_event_row(row: Any) -> Optional[DecisionNaturalKey]:
    """Extract key from market_events_v2 row (dict or sqlite3.Row).
    Returns None if required fields absent or temperature_metric invalid.
    Production: map market_events_v2 column names; market_slug is the canonical id.
    decision_seq is not in source row — caller provides context for sequencing.
    """
    raise NotImplementedError("SCAFFOLD — pending T1 production pass")


def from_ensemble_snapshot_row(row: Any) -> Optional[DecisionNaturalKey]:
    """Extract key from ensemble_snapshots_v2 row.
    city → market_slug resolved Python-side via market_events_v2 (not condition_id).
    Returns None if city→market_slug resolution fails.
    decision_seq not in source row — caller provides context for sequencing.
    """
    raise NotImplementedError("SCAFFOLD — pending T1 production pass")


def from_artifact_json(j: dict) -> Optional[DecisionNaturalKey]:
    """Extract 4-component prefix from decision_log.artifact_json dict.
    Returns (market_slug, temperature_metric, target_date, observation_time, 0) tuple
    wrapped as DecisionNaturalKey with decision_seq=0 (backfill caller adjusts seq).
    Robust to missing keys (return None, not raise) — historical rows vary.
    Production: audit actual Phase 0 artifact_json key names before finalising.
    """
    raise NotImplementedError("SCAFFOLD — pending T1 production pass")
