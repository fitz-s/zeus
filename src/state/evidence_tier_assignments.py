# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: evidence-tier governance follow-up packet 2026-05-21:
#   evidence_tier_assignments is the DB override/reducer surface for runtime
#   strategy eligibility; static strategy registry remains baseline only.
"""Evidence tier assignment writer/reducer for the world DB."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from src.contracts.evidence_tier import EvidenceTier
from src.state.db import SCHEMA_VERSION


@dataclass(frozen=True)
class EvidenceTierAssignment:
    strategy_id: str
    tier: EvidenceTier
    assigned_at: str
    assignment_source: str
    verdict_kind: str
    operator_ref: str | None = None
    verdict_reason: str | None = None
    row_id: int | None = None
    effective_from: str | None = None
    effective_until: str | None = None
    revoked_at: str | None = None
    revoked_by: str | None = None
    supersedes_assignment_id: int | None = None


def _coerce_tier(value: EvidenceTier | int) -> EvidenceTier:
    try:
        return value if isinstance(value, EvidenceTier) else EvidenceTier(int(value))
    except Exception as exc:
        raise ValueError(f"invalid EvidenceTier assignment: {value!r}") from exc


def record_evidence_tier_assignment(
    conn: sqlite3.Connection,
    *,
    strategy_id: str,
    tier: EvidenceTier | int,
    rationale: str | None,
    operator_ref: str | None,
    verdict_reason: str | None,
    assignment_source: str,
    verdict_kind: str,
    commit: bool = True,
    effective_from: str | None = None,
    effective_until: str | None = None,
    supersedes_assignment_id: int | None = None,
) -> EvidenceTierAssignment:
    """Persist a validated evidence-tier assignment and optionally commit it.

    Governance side effects should call this with ``commit=True`` so a returned
    PROMOTE/DEMOTE result cannot outrun the durable authority row.
    """
    strategy_id = str(strategy_id or "").strip()
    if not strategy_id:
        raise ValueError("strategy_id is required")
    tier_value = _coerce_tier(tier)
    if assignment_source not in {"tribunal", "operator_override", "migration"}:
        raise ValueError(f"invalid assignment_source: {assignment_source!r}")
    if verdict_kind not in {"PROMOTE", "HOLD", "DEMOTE", "OPERATOR_OVERRIDE", "MIGRATION"}:
        raise ValueError(f"invalid verdict_kind: {verdict_kind!r}")

    assigned_at = datetime.now(tz=timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO evidence_tier_assignments (
            strategy_id, tier, assigned_at, rationale, operator_ref,
            verdict_reason, schema_version, assignment_source, verdict_kind,
            effective_from, effective_until, supersedes_assignment_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            strategy_id,
            int(tier_value),
            assigned_at,
            rationale,
            operator_ref,
            verdict_reason,
            SCHEMA_VERSION,
            assignment_source,
            verdict_kind,
            effective_from,
            effective_until,
            supersedes_assignment_id,
        ),
    )
    if commit:
        conn.commit()
    return EvidenceTierAssignment(
        strategy_id=strategy_id,
        tier=tier_value,
        assigned_at=assigned_at,
        assignment_source=assignment_source,
        verdict_kind=verdict_kind,
        operator_ref=operator_ref,
        verdict_reason=verdict_reason,
        row_id=int(cur.lastrowid) if cur.lastrowid is not None else None,
        effective_from=effective_from,
        effective_until=effective_until,
        supersedes_assignment_id=supersedes_assignment_id,
    )


def current_evidence_tier_assignment(
    conn: sqlite3.Connection,
    strategy_id: str,
) -> EvidenceTierAssignment | None:
    """Return the current DB assignment using the explicit authority order.

    Policy: latest valid operator_override outranks tribunal rows; latest
    tribunal row outranks migration rows. Static registry is handled by callers
    as the baseline when this reducer returns None.
    """
    strategy_id = str(strategy_id or "").strip()
    if not strategy_id:
        return None
    try:
        row = conn.execute(
            """
            SELECT id, strategy_id, tier, assigned_at, assignment_source,
                   verdict_kind, operator_ref, verdict_reason,
                   effective_from, effective_until, revoked_at, revoked_by,
                   supersedes_assignment_id
            FROM evidence_tier_assignments
            WHERE strategy_id = ?
              AND tier IN (0, 1, 2, 3, 4, 5, 6, 7)
              AND (effective_from IS NULL OR datetime(effective_from) <= datetime('now'))
              AND (effective_until IS NULL OR datetime(effective_until) > datetime('now'))
              AND revoked_at IS NULL
            ORDER BY
              CASE assignment_source
                WHEN 'operator_override' THEN 0
                WHEN 'tribunal' THEN 1
                WHEN 'migration' THEN 2
                ELSE 3
              END ASC,
              assigned_at DESC,
              id DESC
            LIMIT 1
            """,
            (strategy_id,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        err = str(exc)
        if "no such table: evidence_tier_assignments" in err or "no such column" in err:
            return None
        raise
    except sqlite3.Error:
        raise
    if row is None:
        return None
    return EvidenceTierAssignment(
        row_id=int(row[0]),
        strategy_id=str(row[1]),
        tier=_coerce_tier(int(row[2])),
        assigned_at=str(row[3]),
        assignment_source=str(row[4]),
        verdict_kind=str(row[5]),
        operator_ref=row[6],
        verdict_reason=row[7],
        effective_from=row[8],
        effective_until=row[9],
        revoked_at=row[10],
        revoked_by=row[11],
        supersedes_assignment_id=row[12],
    )


def effective_evidence_tier(
    strategy_id: str,
    *,
    baseline: EvidenceTier,
    conn: sqlite3.Connection | None,
) -> EvidenceTier:
    """Return DB-reduced effective tier, falling back to registry baseline."""
    if conn is None:
        return baseline
    assignment = current_evidence_tier_assignment(conn, strategy_id)
    return baseline if assignment is None else assignment.tier
