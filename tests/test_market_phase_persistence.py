# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v3.md §6.P2 stage 3 — probability_trace_fact.market_phase column + writer.
"""``probability_trace_fact.market_phase`` persistence tests.

Stage 3 of P2 makes the MarketPhase axis A durable. ``init_schema``
creates the column on fresh DBs and ALTERs it onto legacy DBs;
``log_probability_trace_fact`` writes the value from the
``EdgeDecision.market_phase`` field (or, as fallback, from the
``MarketCandidate.market_phase`` enum directly).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.state.db import init_schema, log_probability_trace_fact
from src.strategy.market_phase import MarketPhase

UTC = timezone.utc


def _new_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _decision_payload(*, decision_id: str, market_phase=None) -> SimpleNamespace:
    """Minimal decision-shape stub. The writer pulls fields via getattr,
    so SimpleNamespace is sufficient.
    """
    return SimpleNamespace(
        decision_id=decision_id,
        decision_snapshot_id=f"snap-{decision_id}",
        strategy_key="settlement_capture",
        selected_method="day0_observation",
        entry_method="day0_observation",
        agreement="AGREE",
        rejection_stage="",
        availability_status="OK",
        n_edges_found=1,
        n_edges_after_fdr=1,
        alpha=0.5,
        p_raw=None,
        p_cal=None,
        p_market=None,
        p_posterior_vector=None,
        edge=None,
        market_phase=market_phase,
    )


def _candidate(*, city: str = "London", target_date: str = "2026-05-08", market_phase=None) -> SimpleNamespace:
    return SimpleNamespace(
        city=SimpleNamespace(name=city),
        target_date=target_date,
        outcomes=[],
        discovery_mode="day0_capture",
        market_phase=market_phase,
    )


# ---------------------------------------------------------------------- #
# Schema
# ---------------------------------------------------------------------- #


def test_init_schema_adds_market_phase_column_to_fresh_db() -> None:
    conn = _new_conn()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(probability_trace_fact)").fetchall()}
    assert "market_phase" in cols, f"market_phase missing from fresh-DB schema; got {cols}"


def test_init_schema_alters_market_phase_onto_legacy_db() -> None:
    """Legacy-DB simulation: pre-create the table WITHOUT market_phase,
    then run init_schema and verify the ALTER path adds it. This is the
    code path that protects production DBs predating stage 3.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Build a stripped-down probability_trace_fact (no market_phase) — the
    # ON CONFLICT machinery insists on a UNIQUE-constrained column for
    # the upsert, so legacy schemas keep the same constraints minus the
    # P2 column.
    conn.executescript(
        """
        CREATE TABLE probability_trace_fact (
            trace_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL UNIQUE,
            decision_snapshot_id TEXT,
            candidate_id TEXT,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            direction TEXT,
            mode TEXT,
            strategy_key TEXT,
            discovery_mode TEXT,
            entry_method TEXT,
            selected_method TEXT,
            trace_status TEXT NOT NULL,
            missing_reason_json TEXT NOT NULL DEFAULT '[]',
            bin_labels_json TEXT,
            p_raw_json TEXT,
            p_cal_json TEXT,
            p_market_json TEXT,
            p_posterior_json TEXT,
            p_posterior REAL,
            alpha REAL,
            agreement TEXT,
            n_edges_found INTEGER,
            n_edges_after_fdr INTEGER,
            rejection_stage TEXT,
            availability_status TEXT,
            recorded_at TEXT NOT NULL
        );
        """
    )
    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(probability_trace_fact)").fetchall()}
    assert "market_phase" not in cols_before

    init_schema(conn)

    cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(probability_trace_fact)").fetchall()}
    assert "market_phase" in cols_after, f"ALTER path failed; got {cols_after}"


def test_init_schema_idempotent_on_repeat_call() -> None:
    """Calling init_schema twice on the same connection must not raise
    (duplicate-column OperationalError is swallowed).
    """
    conn = _new_conn()
    init_schema(conn)  # second call must be a no-op
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(probability_trace_fact)").fetchall()}
    assert "market_phase" in cols


# ---------------------------------------------------------------------- #
# Writer
# ---------------------------------------------------------------------- #


def test_writer_persists_market_phase_from_decision() -> None:
    conn = _new_conn()
    candidate = _candidate(market_phase=MarketPhase.SETTLEMENT_DAY)
    decision = _decision_payload(
        decision_id="dec-001", market_phase=MarketPhase.SETTLEMENT_DAY.value
    )

    result = log_probability_trace_fact(
        conn,
        candidate=candidate,
        decision=decision,
        recorded_at=datetime(2026, 5, 7, 23, 30, tzinfo=UTC).isoformat(),
        mode="day0_capture",
    )
    assert result["status"] == "written"

    row = conn.execute(
        "SELECT market_phase FROM probability_trace_fact WHERE decision_id=?",
        ("dec-001",),
    ).fetchone()
    assert row is not None
    assert row["market_phase"] == "settlement_day"


def test_writer_falls_back_to_candidate_phase_when_decision_missing() -> None:
    """If the decision was constructed by a legacy / test path that
    didn't propagate market_phase, the writer still tags from the
    candidate's enum directly. This makes the column fail-soft for
    pre-stage-2 EdgeDecision producers.
    """
    conn = _new_conn()
    candidate = _candidate(market_phase=MarketPhase.PRE_SETTLEMENT_DAY)
    decision = _decision_payload(decision_id="dec-002", market_phase=None)

    log_probability_trace_fact(
        conn,
        candidate=candidate,
        decision=decision,
        recorded_at=datetime(2026, 5, 6, 12, 0, tzinfo=UTC).isoformat(),
        mode="opening_hunt",
    )

    row = conn.execute(
        "SELECT market_phase FROM probability_trace_fact WHERE decision_id=?",
        ("dec-002",),
    ).fetchone()
    assert row["market_phase"] == "pre_settlement_day"


def test_writer_writes_null_when_neither_side_has_phase() -> None:
    """Off-cycle / manual writers without phase get NULL — column is
    additive and nullable; downstream cohort queries handle NULL
    explicitly via WHERE market_phase IS NOT NULL.
    """
    conn = _new_conn()
    candidate = _candidate(market_phase=None)
    decision = _decision_payload(decision_id="dec-003", market_phase=None)

    log_probability_trace_fact(
        conn,
        candidate=candidate,
        decision=decision,
        recorded_at=datetime(2026, 5, 7, 0, 0, tzinfo=UTC).isoformat(),
        mode="opening_hunt",
    )

    row = conn.execute(
        "SELECT market_phase FROM probability_trace_fact WHERE decision_id=?",
        ("dec-003",),
    ).fetchone()
    assert row["market_phase"] is None


def test_writer_idempotent_upsert_preserves_phase() -> None:
    """ON CONFLICT updates market_phase from the new row; this lets a
    re-run of the same decision_id update the phase tag without leaving
    a stale value.
    """
    conn = _new_conn()
    candidate1 = _candidate(market_phase=MarketPhase.PRE_SETTLEMENT_DAY)
    decision1 = _decision_payload(
        decision_id="dec-004", market_phase=MarketPhase.PRE_SETTLEMENT_DAY.value
    )
    log_probability_trace_fact(
        conn,
        candidate=candidate1,
        decision=decision1,
        recorded_at=datetime(2026, 5, 6, 12, 0, tzinfo=UTC).isoformat(),
        mode="opening_hunt",
    )

    # Same decision_id, but the market has since transitioned to
    # SETTLEMENT_DAY (decision_time advanced past the boundary).
    candidate2 = _candidate(market_phase=MarketPhase.SETTLEMENT_DAY)
    decision2 = _decision_payload(
        decision_id="dec-004", market_phase=MarketPhase.SETTLEMENT_DAY.value
    )
    log_probability_trace_fact(
        conn,
        candidate=candidate2,
        decision=decision2,
        recorded_at=datetime(2026, 5, 7, 23, 30, tzinfo=UTC).isoformat(),
        mode="day0_capture",
    )

    rows = conn.execute(
        "SELECT decision_id, market_phase FROM probability_trace_fact "
        "WHERE decision_id=?",
        ("dec-004",),
    ).fetchall()
    assert len(rows) == 1, "upsert must collapse to single row"
    assert rows[0]["market_phase"] == "settlement_day"


# ---------------------------------------------------------------------- #
# Index
# ---------------------------------------------------------------------- #


def test_market_phase_index_present_for_cohort_queries() -> None:
    """Per PLAN_v3 §6.P9, downstream attribution queries filter by
    (strategy_key, market_phase). An index on market_phase keeps
    SELECTs fast as the table grows. This test pins that the index
    survives both fresh-init and legacy-ALTER paths.
    """
    conn = _new_conn()
    indexes = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='probability_trace_fact'"
        ).fetchall()
    }
    assert "idx_probability_trace_market_phase" in indexes
