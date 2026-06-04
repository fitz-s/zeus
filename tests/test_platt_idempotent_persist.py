# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: docs/operations/CONSOLIDATED_AUDIT_AND_PLAN_2026-06-04.md
#                  R4 (#174) — swallowed DB faults must not become per-candidate
#                  trade rejections. Two-part category kill:
#                    (1) store-level: a re-fit of the same Platt bucket is
#                        idempotent (INSERT OR REPLACE), never IntegrityError.
#                    (2) boundary-level: read-time-fit persistence is best-effort;
#                        a DB write fault must NOT destroy the in-memory calibrator
#                        (which is already valid) nor propagate out of the fit.
"""Relationship tests for #174 — platt_models UNIQUE-collision category kill.

Live evidence (state/zeus-world.db, 2026-06-04): 870x
`UNIQUE constraint failed: platt_models...` rejections at stage
UNKNOWN_REVIEW_REQUIRED + 496x downstream `CALIBRATION_AUTHORITY_MISSING:
no Platt calibrator` — both caused by a read-time refit's persistence
INSERT colliding on the bucket UNIQUE index and propagating as a per-candidate
no-trade. The candidate itself was tradeable; the cache write failed.

These tests reproduce the live scenario (two reactor cycles refit the same
bucket) and pin the contract that persistence failure is contained.

All tests use :memory: SQLite. No production DB writes.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.store import save_platt_model
from src.state.db import init_schema, init_schema_forecasts
from src.types.metric_identity import HIGH_LOCALDAY_MAX


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_schema_forecasts(conn)
    return conn


def _save(conn: sqlite3.Connection, *, param_A: float) -> None:
    """Save a Platt model to a fixed bucket; only param_A varies across calls."""
    save_platt_model(
        conn,
        metric_identity=HIGH_LOCALDAY_MAX,
        cluster="US-Northeast",
        season="JJA",
        data_version=HIGH_LOCALDAY_MAX.data_version,
        param_A=param_A,
        param_B=0.1,
        param_C=0.0,
        bootstrap_params=[[param_A, 0.1, 0.0]],
        n_samples=42,
        input_space="raw_probability",
        cycle="00",
        source_id="tigge_mars",
        horizon_profile="full",
    )


# ---------------------------------------------------------------------------
# Part 1 — store-level idempotency (the instance kill).
# A re-fit of the same bucket must UPDATE, not raise IntegrityError.
# RED on plain `INSERT INTO`; GREEN on `INSERT OR REPLACE`.
# ---------------------------------------------------------------------------


def test_save_platt_model_refit_same_bucket_is_idempotent():
    """Two saves of the same bucket (two reactor cycles) → no IntegrityError.

    Reproduces the live 870x `UNIQUE constraint failed: platt_models` exactly:
    the second refit of an identical (metric,cluster,season,data_version,
    input_space,is_active,cycle,source_id,horizon_profile) bucket.
    """
    conn = _conn()
    _save(conn, param_A=1.11)
    # Second refit of the SAME bucket must not raise.
    _save(conn, param_A=2.22)

    rows = conn.execute(
        "SELECT param_A, is_active FROM platt_models "
        "WHERE temperature_metric='high' AND cluster='US-Northeast' "
        "AND season='JJA' AND is_active=1"
    ).fetchall()
    # Exactly one active row, holding the SECOND fit's params.
    assert len(rows) == 1, f"expected 1 active row, got {len(rows)}"
    assert rows[0]["param_A"] == 2.22


def test_save_platt_model_refit_does_not_multiply_rows():
    """N refits of one bucket leave exactly one active row (no history bloat)."""
    conn = _conn()
    for a in (1.0, 2.0, 3.0, 4.0):
        _save(conn, param_A=a)
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM platt_models WHERE cluster='US-Northeast' "
        "AND season='JJA' AND is_active=1"
    ).fetchone()["c"]
    assert n == 1


# ---------------------------------------------------------------------------
# Part 2 — boundary best-effort (the category kill).
# Read-time-fit persistence is a cache side-effect. A DB write fault must NOT
# propagate out of the fit nor destroy the already-valid in-memory calibrator.
# RED today (helper does not exist / save raises propagate); GREEN after the
# best-effort wrap is extracted.
# ---------------------------------------------------------------------------


class _CalStub:
    """Minimal calibrator carrier with the attrs the persister reads."""
    A = 0.5
    B = 0.2
    C = 0.0
    bootstrap_params = [[0.5, 0.2, 0.0]]
    n_samples = 30
    input_space = "raw_probability"


def test_persist_calibrator_best_effort_swallows_db_error():
    """A DB write fault during persistence returns False, never raises."""
    from src.calibration import manager as m

    assert hasattr(m, "_persist_calibrator_best_effort"), (
        "expected named best-effort persister so the 'persistence is best-effort' "
        "relationship is explicit and tested"
    )

    def _boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    orig = m.save_platt_model
    m.save_platt_model = _boom  # type: ignore[assignment]
    try:
        conn = _conn()
        # Must NOT raise — the candidate's calibrator is already valid in memory.
        ok = m._persist_calibrator_best_effort(
            conn, _CalStub(),
            cluster="US-Northeast", season="JJA",
            cycle="00", source_id="tigge_mars",
            horizon_profile="full", data_version=HIGH_LOCALDAY_MAX.data_version,
        )
        assert ok is False
    finally:
        m.save_platt_model = orig  # type: ignore[assignment]


def test_persist_calibrator_best_effort_persists_on_success():
    """The happy path actually writes the row (best-effort != no-op)."""
    from src.calibration import manager as m

    conn = _conn()
    ok = m._persist_calibrator_best_effort(
        conn, _CalStub(),
        cluster="US-Northeast", season="JJA",
        cycle="00", source_id="tigge_mars",
        horizon_profile="full", data_version=HIGH_LOCALDAY_MAX.data_version,
    )
    assert ok is True
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM platt_models WHERE cluster='US-Northeast' "
        "AND season='JJA' AND is_active=1"
    ).fetchone()["c"]
    assert n == 1
