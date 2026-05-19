# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_1_ULTRAPLAN.md §4.0–§4.2 (Path D natural-key reframe)
"""DecisionNaturalKey NewType and canonical helper stubs.

Path D: (market_id, condition_id, temperature_metric, target_date, observation_time)
is the join key for decision_events.  decision_group_id is audit-only.
Production pass fills NotImplementedError stubs.
"""

from __future__ import annotations

from typing import Any, Literal, NewType, Optional

# ---------------------------------------------------------------------------
# Core type
# ---------------------------------------------------------------------------

DecisionNaturalKey = NewType(
    "DecisionNaturalKey",
    tuple,  # runtime: tuple[str, str, Literal['high','low'], str, str]
)
"""5-tuple: (market_id, condition_id, temperature_metric, target_date, observation_time).
NewType erased at runtime — mypy enforces statically.
"""


def make_decision_natural_key(
    market_id: str,
    condition_id: str,
    temperature_metric: Literal["high", "low"],
    target_date: str,
    observation_time: str,
) -> DecisionNaturalKey:
    """Construct and validate a DecisionNaturalKey. Raises ValueError on bad metric."""
    if temperature_metric not in ("high", "low"):
        raise ValueError(f"temperature_metric must be 'high' or 'low', got {temperature_metric!r}")
    return DecisionNaturalKey(
        (market_id, condition_id, temperature_metric, target_date, observation_time)
    )


# ---------------------------------------------------------------------------
# Helper stubs — production pass fills bodies
# ---------------------------------------------------------------------------


def from_market_event_row(row: Any) -> Optional[DecisionNaturalKey]:
    """Extract key from market_events_v2 row (dict or sqlite3.Row).
    Returns None if required fields absent or temperature_metric invalid.
    Production: map market_events_v2 column names to 5-tuple.
    """
    raise NotImplementedError("SCAFFOLD — pending T1 production pass")


def from_ensemble_snapshot_row(row: Any) -> Optional[DecisionNaturalKey]:
    """Extract key from ensemble_snapshots_v2 row.
    city → (market_id, condition_id) resolved Python-side via market_events_v2.
    Returns None if city→market resolution fails.
    """
    raise NotImplementedError("SCAFFOLD — pending T1 production pass")


def from_artifact_json(j: dict) -> Optional[DecisionNaturalKey]:
    """Extract key from decision_log.artifact_json dict.
    Robust to missing keys (return None, not raise) — historical rows vary.
    Production: audit actual Phase 0 artifact_json key names before finalising.
    """
    raise NotImplementedError("SCAFFOLD — pending T1 production pass")
