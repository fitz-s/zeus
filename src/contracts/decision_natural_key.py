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

    market_events_v2 columns used: market_slug, temperature_metric, target_date.
    observation_time is NOT in market_events_v2 — caller must supply it as context.
    decision_seq is not in source row — caller provides context for sequencing.

    Returns DecisionNaturalKey with observation_time='' and decision_seq=0 as
    sentinels; callers are expected to fill those fields before inserting.
    Returns None if required fields absent or temperature_metric invalid.
    """
    try:
        market_slug = str(row["market_slug"]).strip()
        temperature_metric = str(row["temperature_metric"]).strip().lower()
        target_date = str(row["target_date"]).strip()
    except (KeyError, TypeError):
        return None
    if not market_slug or not target_date:
        return None
    if temperature_metric not in ("high", "low"):
        return None
    return DecisionNaturalKey((market_slug, temperature_metric, target_date, "", 0))


def from_ensemble_snapshot_row(row: Any) -> Optional[DecisionNaturalKey]:
    """Extract partial key from ensemble_snapshots_v2 row.

    ensemble_snapshots_v2 has city, target_date, temperature_metric, available_at.
    city is NOT market_slug — callers must resolve city → market_slug via
    market_events_v2 (keyed on city + target_date + temperature_metric).
    Returns None if required fields absent or temperature_metric invalid.

    The returned key uses city as a PLACEHOLDER in the market_slug position.
    The backfill and antibody callers are responsible for resolving to market_slug
    before writing or comparing decision_events rows.
    """
    try:
        city = str(row["city"]).strip()
        temperature_metric = str(row["temperature_metric"]).strip().lower()
        target_date = str(row["target_date"]).strip()
        available_at = str(row.get("available_at") or row.get("fetch_time", "")).strip()
    except (KeyError, TypeError):
        return None
    if not city or not target_date:
        return None
    if temperature_metric not in ("high", "low"):
        return None
    # city is a placeholder; callers must resolve to market_slug before use
    return DecisionNaturalKey((city, temperature_metric, target_date, available_at, 0))


def from_artifact_json(j: dict) -> Optional[DecisionNaturalKey]:
    """Extract partial key from a decision_log trade_case dict.

    Accepts the individual trade_case dict embedded in artifact_json["trade_cases"].
    The backfill iterates artifact_json["trade_cases"] and calls this per element.

    Fields extracted:
      - temperature_metric: derived from range_label ("highest" → "high", "lowest" → "low")
        or from direction ("buy_yes" with "high" context); returns None if unresolvable.
      - target_date: from "target_date" key directly.
      - observation_time: from "timestamp" key (decision timestamp at cycle time).
      - market_slug: NOT directly available — city is used as PLACEHOLDER.
        Callers must resolve city → market_slug via market_events_v2 before inserting.

    Returns DecisionNaturalKey with city-as-placeholder in market_slug position,
    decision_seq=0 (backfill caller adjusts seq after DELETE-and-recount).
    Returns None if required fields are absent or temperature_metric is unresolvable.
    Robust to missing keys (return None, not raise) — historical rows vary.
    """
    try:
        city = str(j.get("city", "")).strip()
        target_date = str(j.get("target_date", "")).strip()
        observation_time = str(j.get("timestamp", "")).strip()
        range_label = str(j.get("range_label", "")).lower()
    except (AttributeError, TypeError):
        return None

    if not target_date or not observation_time:
        return None

    # Derive temperature_metric from range_label keywords
    if "highest" in range_label:
        temperature_metric: Optional[str] = "high"
    elif "lowest" in range_label:
        temperature_metric = "low"
    else:
        # Fallback: attempt to derive from city-level range_label context
        # Some legacy records may use "high temperature" / "low temperature"
        if "high temperature" in range_label or " high " in range_label:
            temperature_metric = "high"
        elif "low temperature" in range_label or " low " in range_label:
            temperature_metric = "low"
        else:
            return None  # cannot resolve temperature_metric

    if not city:
        return None

    # city is a placeholder for market_slug; caller resolves via market_events_v2
    return DecisionNaturalKey((city, temperature_metric, target_date, observation_time, 0))
