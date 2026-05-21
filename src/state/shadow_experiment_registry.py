# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase6_evidence_ladder/PHASE_6_PLAN.md §T2
#                  + docs/operations/task_2026-05-21_mainline_completion_authority/07_PHASE_6_EVIDENCE_LADDER.md §Object model
"""ShadowExperimentRegistry — immutable shadow experiment lifecycle.

Design contract
---------------
A ShadowExperiment is an immutable record of a shadow run for a given
strategy + config pair. Small-N inference is invalid if the experiment
definition changes mid-run; immutability enforces this.

``experiment_id`` is the SHA-256 hash of (strategy_id, config_hash, started_at)
so the same experiment context always produces the same ID — idempotent register.

``immutable: bool`` is audit-trail metadata only; enforcement is the frozen
dataclass + close_experiment gate. Attempting to re-register a started
experiment with different config raises ValueError.

INV-37: all DB-writing functions accept a ``conn`` argument (caller-supplied).
The read-only ``lookup_experiment`` opens its own read-only connection when
``conn=None`` is passed for convenience in analysis contexts.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.state.db import get_world_connection


# ---------------------------------------------------------------------------
# Domain object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShadowExperiment:
    """Immutable record of a shadow experiment run.

    ``immutable`` is always True for a started (open) experiment; it becomes
    True at creation and never changes. Mutation of a started experiment is
    signalled by ValueError from ``register_shadow_experiment``.
    """
    experiment_id: str          # SHA-256 of (strategy_id, config_hash, started_at ISO)
    strategy_id: str
    config_hash: str            # SHA-256 of canonical JSON config — any change → new ID
    started_at: datetime        # UTC
    closed_at: Optional[datetime]  # UTC; None until close_experiment called
    cohort_tag: str             # groups decision_events rows for this experiment
    immutable: bool             # always True for open experiments (audit metadata)


# ---------------------------------------------------------------------------
# ID derivation
# ---------------------------------------------------------------------------

def _derive_experiment_id(
    strategy_id: str,
    config_hash: str,
    started_at: datetime,
) -> str:
    """Deterministic SHA-256 ID from (strategy_id, config_hash, started_at ISO)."""
    key = f"{strategy_id}|{config_hash}|{started_at.isoformat()}"
    return hashlib.sha256(key.encode()).hexdigest()


def hash_config(config: dict) -> str:
    """Canonical SHA-256 of a config dict (sorted keys, no whitespace)."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register_shadow_experiment(
    strategy_id: str,
    config: dict,
    cohort_tag: str,
    started_at: Optional[datetime] = None,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> str:
    """Register a shadow experiment; return experiment_id.

    Idempotent: same (strategy_id, config_hash, started_at) → same ID.
    If the experiment_id already exists in the DB with matching config_hash,
    returns the existing ID (idempotent).
    If the experiment_id already exists with a DIFFERENT config_hash, raises
    ValueError (mutation of started experiment is forbidden).

    Parameters
    ----------
    strategy_id:
        Strategy key (e.g. "shoulder_sell").
    config:
        Strategy configuration dict; any change yields a new experiment_id.
    cohort_tag:
        Tag used to group decision_events for this experiment.
    started_at:
        UTC datetime of experiment start. Defaults to utcnow().
    conn:
        World DB connection. If None, opens a fresh write connection.

    Returns
    -------
    str
        The experiment_id (SHA-256 hex).
    """
    if started_at is None:
        started_at = datetime.now(tz=timezone.utc)
    elif started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    config_hash = hash_config(config)
    experiment_id = _derive_experiment_id(strategy_id, config_hash, started_at)

    own_conn = conn is None
    if own_conn:
        conn = get_world_connection()

    try:
        # Check for existing row
        row = conn.execute(
            "SELECT config_hash FROM shadow_experiments WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()

        if row is not None:
            existing_config_hash = row[0]
            if existing_config_hash != config_hash:
                raise ValueError(
                    f"Mutation of started experiment {experiment_id!r}: "
                    f"existing config_hash={existing_config_hash!r} != "
                    f"new config_hash={config_hash!r}. "
                    "Any config change must produce a new experiment."
                )
            # Idempotent — already registered
            return experiment_id

        conn.execute(
            """
            INSERT INTO shadow_experiments
                (experiment_id, strategy_id, config_hash, started_at,
                 closed_at, cohort_tag, immutable)
            VALUES (?, ?, ?, ?, NULL, ?, 1)
            """,
            (
                experiment_id,
                strategy_id,
                config_hash,
                started_at.isoformat(),
                cohort_tag,
            ),
        )
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()

    return experiment_id


def lookup_experiment(
    experiment_id: str,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> ShadowExperiment:
    """Look up a shadow experiment by ID.

    Raises KeyError if not found.
    """
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_world_connection_read_only
        conn = get_world_connection_read_only()

    try:
        row = conn.execute(
            """
            SELECT experiment_id, strategy_id, config_hash,
                   started_at, closed_at, cohort_tag, immutable
            FROM shadow_experiments
            WHERE experiment_id = ?
            """,
            (experiment_id,),
        ).fetchone()
    finally:
        if own_conn:
            conn.close()

    if row is None:
        raise KeyError(f"ShadowExperiment not found: {experiment_id!r}")

    (eid, sid, chash, started_raw, closed_raw, cohort, immut) = row
    started_at = datetime.fromisoformat(started_raw)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    closed_at: Optional[datetime] = None
    if closed_raw is not None:
        closed_at = datetime.fromisoformat(closed_raw)
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=timezone.utc)

    return ShadowExperiment(
        experiment_id=eid,
        strategy_id=sid,
        config_hash=chash,
        started_at=started_at,
        closed_at=closed_at,
        cohort_tag=cohort,
        immutable=bool(immut),
    )


def close_experiment(
    experiment_id: str,
    closed_at: Optional[datetime] = None,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """Mark a shadow experiment as closed.

    Sets closed_at to now (UTC) if not provided. Raises KeyError if the
    experiment does not exist. Raises ValueError if already closed.
    """
    if closed_at is None:
        closed_at = datetime.now(tz=timezone.utc)
    elif closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=timezone.utc)

    own_conn = conn is None
    if own_conn:
        conn = get_world_connection()

    try:
        row = conn.execute(
            "SELECT closed_at FROM shadow_experiments WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()

        if row is None:
            raise KeyError(f"ShadowExperiment not found: {experiment_id!r}")

        if row[0] is not None:
            raise ValueError(
                f"Experiment {experiment_id!r} is already closed at {row[0]!r}"
            )

        conn.execute(
            "UPDATE shadow_experiments SET closed_at = ? WHERE experiment_id = ?",
            (closed_at.isoformat(), experiment_id),
        )
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()
