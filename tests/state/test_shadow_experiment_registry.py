# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase6_evidence_ladder/PHASE_6_PLAN.md §T2
"""T2 invariant tests for ShadowExperimentRegistry."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.state.db import init_schema
from src.state.shadow_experiment_registry import (
    ShadowExperiment,
    close_experiment,
    hash_config,
    lookup_experiment,
    register_shadow_experiment,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def world_conn() -> sqlite3.Connection:
    """In-memory world DB with full schema."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


_STARTED_AT = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
_CONFIG_A = {"kelly": 0.5, "min_edge": 0.05}
_CONFIG_B = {"kelly": 0.6, "min_edge": 0.05}


# ---------------------------------------------------------------------------
# T2-1: idempotent register
# ---------------------------------------------------------------------------

def test_t2_idempotent_register(world_conn) -> None:
    """Registering the same (strategy, config, started_at) twice returns same ID."""
    id1 = register_shadow_experiment(
        "shoulder_sell", _CONFIG_A, "cohort_a",
        started_at=_STARTED_AT, conn=world_conn,
    )
    id2 = register_shadow_experiment(
        "shoulder_sell", _CONFIG_A, "cohort_a",
        started_at=_STARTED_AT, conn=world_conn,
    )
    assert id1 == id2
    # Only one row in DB
    count = world_conn.execute(
        "SELECT COUNT(*) FROM shadow_experiments WHERE experiment_id = ?", (id1,)
    ).fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# T2-2: different config → different ID
# ---------------------------------------------------------------------------

def test_t2_different_config_different_id(world_conn) -> None:
    """Different config dicts produce different experiment IDs."""
    id_a = register_shadow_experiment(
        "shoulder_sell", _CONFIG_A, "cohort_a",
        started_at=_STARTED_AT, conn=world_conn,
    )
    id_b = register_shadow_experiment(
        "shoulder_sell", _CONFIG_B, "cohort_b",
        started_at=_STARTED_AT, conn=world_conn,
    )
    assert id_a != id_b


# ---------------------------------------------------------------------------
# T2-3: mutation of started experiment raises ValueError
# ---------------------------------------------------------------------------

def test_t2_mutation_raises(world_conn) -> None:
    """Re-registering same experiment_id with different config_hash raises ValueError.

    Note: different config means different config_hash but could produce the same
    experiment_id only if the hash function collides (astronomically unlikely).
    This test verifies the guard code path by inserting a row manually with a
    mismatched config_hash for an existing experiment_id.
    """
    experiment_id = register_shadow_experiment(
        "shoulder_sell", _CONFIG_A, "cohort_a",
        started_at=_STARTED_AT, conn=world_conn,
    )
    # Manually corrupt the config_hash for this experiment_id to trigger the guard
    world_conn.execute(
        "UPDATE shadow_experiments SET config_hash = 'corrupted_hash' WHERE experiment_id = ?",
        (experiment_id,),
    )
    world_conn.commit()

    # Now re-registering should detect the mismatch and raise
    with pytest.raises(ValueError, match="Mutation of started experiment"):
        register_shadow_experiment(
            "shoulder_sell", _CONFIG_A, "cohort_a",
            started_at=_STARTED_AT, conn=world_conn,
        )


# ---------------------------------------------------------------------------
# T2-4: roundtrip via lookup_experiment
# ---------------------------------------------------------------------------

def test_t2_roundtrip(world_conn) -> None:
    """Registered experiment roundtrips correctly through lookup_experiment."""
    experiment_id = register_shadow_experiment(
        "shoulder_sell", _CONFIG_A, "cohort_alpha",
        started_at=_STARTED_AT, conn=world_conn,
    )
    exp = lookup_experiment(experiment_id, conn=world_conn)

    assert isinstance(exp, ShadowExperiment)
    assert exp.experiment_id == experiment_id
    assert exp.strategy_id == "shoulder_sell"
    assert exp.config_hash == hash_config(_CONFIG_A)
    assert exp.started_at == _STARTED_AT
    assert exp.closed_at is None
    assert exp.cohort_tag == "cohort_alpha"
    assert exp.immutable is True


# ---------------------------------------------------------------------------
# T2-5: lookup_experiment raises KeyError for unknown ID
# ---------------------------------------------------------------------------

def test_t2_lookup_unknown_raises(world_conn) -> None:
    """lookup_experiment raises KeyError for an unknown experiment_id."""
    with pytest.raises(KeyError, match="ShadowExperiment not found"):
        lookup_experiment("nonexistent_id", conn=world_conn)


# ---------------------------------------------------------------------------
# T2-6: close_experiment sets closed_at
# ---------------------------------------------------------------------------

def test_t2_close_experiment(world_conn) -> None:
    """close_experiment sets closed_at; lookup reflects it."""
    experiment_id = register_shadow_experiment(
        "shoulder_sell", _CONFIG_A, "cohort_a",
        started_at=_STARTED_AT, conn=world_conn,
    )
    closed_at = datetime(2026, 5, 22, 9, 0, 0, tzinfo=timezone.utc)
    close_experiment(experiment_id, closed_at=closed_at, conn=world_conn)

    exp = lookup_experiment(experiment_id, conn=world_conn)
    assert exp.closed_at == closed_at


# ---------------------------------------------------------------------------
# T2-7: PRAGMA index_list confirms idx_eta_strategy_assigned
# ---------------------------------------------------------------------------

def test_t2_index_eta_strategy_assigned_exists(world_conn) -> None:
    """idx_eta_strategy_assigned index exists on evidence_tier_assignments."""
    indices = world_conn.execute(
        "PRAGMA index_list(evidence_tier_assignments)"
    ).fetchall()
    index_names = {row[1] for row in indices}
    assert "idx_eta_strategy_assigned" in index_names, (
        f"Expected idx_eta_strategy_assigned in {index_names}"
    )


# ---------------------------------------------------------------------------
# T2-8: shadow_experiments table has immutable column defaulting to 1
# ---------------------------------------------------------------------------

def test_t2_immutable_default_is_1(world_conn) -> None:
    """shadow_experiments.immutable defaults to 1 (True)."""
    experiment_id = register_shadow_experiment(
        "shoulder_sell", _CONFIG_A, "cohort_a",
        started_at=_STARTED_AT, conn=world_conn,
    )
    row = world_conn.execute(
        "SELECT immutable FROM shadow_experiments WHERE experiment_id = ?",
        (experiment_id,),
    ).fetchone()
    assert row[0] == 1
