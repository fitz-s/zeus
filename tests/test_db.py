# Created: 2026-03-30
# Last reused/audited: 2026-05-16
# Lifecycle: created=2026-03-30; last_reviewed=2026-05-16; last_reused=2026-05-16
# Purpose: Protect DB schema bootstrap contracts, daily revision-history DDL, and fact-smoke authority labels.
# Reuse: Audit touched schema assertions and high-sensitivity skip metadata before closeout.
# Authority basis: P2 4.4.A2 daily observation revision-history schema packet; Wave16 object-meaning fact-smoke authority repair; PR90 latest-event env authority review fix; 2026-05-16 live-continuous Phase B event-status boundary.
"""Tests for database schema initialization."""

import json
import sqlite3
import tempfile
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from src.state.db import get_connection, init_schema


def _create_opportunity_fact_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunity_fact (
            decision_id TEXT PRIMARY KEY,
            candidate_id TEXT,
            city TEXT,
            target_date TEXT,
            range_label TEXT,
            direction TEXT CHECK (direction IN ('buy_yes', 'buy_no', 'unknown')),
            strategy_key TEXT CHECK (strategy_key IN (
                'settlement_capture',
                'shoulder_sell',
                'center_buy',
                'opening_inertia'
            )),
            discovery_mode TEXT,
            entry_method TEXT,
            snapshot_id TEXT,
            p_raw REAL,
            p_cal REAL,
            p_market REAL,
            alpha REAL,
            best_edge REAL,
            ci_width REAL,
            rejection_stage TEXT,
            rejection_reason_json TEXT,
            availability_status TEXT CHECK (availability_status IN (
                'ok',
                'missing',
                'stale',
                'rate_limited',
                'unavailable',
                'chain_unavailable'
            )),
            should_trade INTEGER NOT NULL CHECK (should_trade IN (0, 1)),
            recorded_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _create_availability_fact_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS availability_fact (
            availability_id TEXT PRIMARY KEY,
            scope_type TEXT NOT NULL CHECK (scope_type IN ('cycle', 'candidate', 'city_target', 'order', 'chain')),
            scope_key TEXT NOT NULL,
            failure_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            impact TEXT NOT NULL CHECK (impact IN ('skip', 'degrade', 'retry', 'block')),
            details_json TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _create_execution_fact_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_fact (
            intent_id TEXT PRIMARY KEY,
            position_id TEXT,
            decision_id TEXT,
            order_role TEXT NOT NULL CHECK (order_role IN ('entry', 'exit')),
            strategy_key TEXT CHECK (strategy_key IN (
                'settlement_capture',
                'shoulder_sell',
                'center_buy',
                'opening_inertia'
            )),
            posted_at TEXT,
            filled_at TEXT,
            voided_at TEXT,
            submitted_price REAL,
            fill_price REAL,
            shares REAL,
            fill_quality REAL,
            latency_seconds REAL,
            venue_status TEXT,
            terminal_exec_status TEXT
        )
        """
    )
    conn.commit()


def _create_outcome_fact_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outcome_fact (
            position_id TEXT PRIMARY KEY,
            strategy_key TEXT CHECK (strategy_key IN (
                'settlement_capture',
                'shoulder_sell',
                'center_buy',
                'opening_inertia'
            )),
            entered_at TEXT,
            exited_at TEXT,
            settled_at TEXT,
            exit_reason TEXT,
            admin_exit_reason TEXT,
            decision_snapshot_id TEXT,
            pnl REAL,
            outcome INTEGER CHECK (outcome IN (0, 1)),
            hold_duration_hours REAL,
            monitor_count INTEGER,
            chain_corrections_count INTEGER
        )
        """
    )
    conn.commit()


def _insert_current_position_for_fill_authority_view_test(
    conn,
    *,
    position_id: str,
    phase: str = "active",
    order_status: str = "filled",
    submitted_size_usd: float = 25.0,
    projected_cost_basis_usd: float = 20.0,
    shares: float = 50.0,
    entry_price: float = 0.50,
    mark_price: float = 0.50,
) -> None:
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
            direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
            last_monitor_prob, last_monitor_edge, last_monitor_market_price,
            decision_snapshot_id, entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id, order_status,
            updated_at, temperature_metric
        )
        VALUES (
            ?, ?, ?, 'm-fill', 'NYC', 'US-Northeast', '2026-04-01', '39-40°F',
            'buy_yes', 'F', ?, ?, ?, ?, 0.60,
            NULL, NULL, ?,
            'snap-fill', 'ens_member_counting', 'center_buy', 'center_buy', 'opening_hunt',
            'local_only', 'yes-fill', 'no-fill', 'cond-fill', 'order-fill', ?,
            '2026-04-01T00:00:00+00:00', 'high'
        )
        """,
        (
            position_id,
            phase,
            position_id,
            submitted_size_usd,
            shares,
            projected_cost_basis_usd,
            entry_price,
            mark_price,
            order_status,
        ),
    )


def _insert_entry_execution_fact_for_fill_authority_view_test(
    conn,
    *,
    position_id: str,
    terminal_exec_status: str,
    fill_price: float | None = 0.40,
    shares: float | None = 50.0,
    filled_at: str | None = "2026-04-01T00:00:03+00:00",
) -> None:
    conn.execute(
        """
        INSERT INTO execution_fact (
            intent_id, position_id, decision_id, order_role, strategy_key,
            posted_at, filled_at, voided_at, submitted_price, fill_price,
            shares, fill_quality, latency_seconds, venue_status, terminal_exec_status
        )
        VALUES (?, ?, 'dec-fill', 'entry', 'center_buy',
                '2026-04-01T00:00:00+00:00', ?, NULL, 0.50, ?,
                ?, NULL, NULL, ?, ?)
        """,
        (
            f"{position_id}:entry",
            position_id,
            filled_at,
            fill_price,
            shares,
            terminal_exec_status,
            terminal_exec_status,
        ),
    )


def _insert_status_position_event_for_view_test(
    conn,
    *,
    position_id: str,
    event_type: str,
    status: str,
    occurred_at: str,
    sequence_no: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type, occurred_at,
            phase_before, phase_after, strategy_key, decision_id, snapshot_id, order_id,
            command_id, caused_by, idempotency_key, venue_status, source_module, env, payload_json
        ) VALUES (?, ?, 1, ?, ?, ?, NULL, NULL, 'center_buy', NULL, 'snap-fill', NULL,
                  NULL, 'test', ?, NULL, 'tests', 'test', ?)
        """,
        (
            f"{position_id}:{event_type}:{sequence_no}",
            position_id,
            sequence_no,
            event_type,
            occurred_at,
            f"{position_id}:{event_type}:{sequence_no}",
            json.dumps({"status": status}),
        ),
    )


def test_init_schema_creates_all_tables():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    init_schema(conn)

    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row["name"] for row in cursor.fetchall()}

    expected = {
        "settlements", "observations", "market_events", "token_price_log",
        "ensemble_snapshots", "calibration_pairs", "platt_models",
        "trade_decisions", "shadow_signals", "probability_trace_fact", "chronicle", "position_events", "solar_daily",
        "observation_instants", "diurnal_peak_prob", "daily_observation_revisions"
    }
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"
    conn.close()


def test_init_schema_creates_daily_observation_revision_indexes():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    indexes = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    conn.close()

    assert "idx_daily_observation_revisions_lookup" in indexes
    assert "ux_daily_observation_revisions_payload" in indexes


def test_init_schema_enforces_daily_observation_revision_constraints():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    base_values = {
        "city": "Chicago",
        "target_date": "2026-04-10",
        "source": "wu_icao_history",
        "natural_key_json": "{}",
        "existing_row_id": 1,
        "existing_combined_payload_hash": "sha256:" + "a" * 64,
        "incoming_combined_payload_hash": "sha256:" + "b" * 64,
        "existing_high_payload_hash": "sha256:" + "a" * 64,
        "existing_low_payload_hash": "sha256:" + "a" * 64,
        "incoming_high_payload_hash": "sha256:" + "b" * 64,
        "incoming_low_payload_hash": "sha256:" + "b" * 64,
        "reason": "payload_hash_mismatch",
        "writer": "tests.test_db",
        "existing_row_json": "{}",
        "incoming_row_json": "{}",
    }

    columns = ", ".join(base_values)
    placeholders = ", ".join("?" for _ in base_values)
    conn.execute(
        f"INSERT INTO daily_observation_revisions ({columns}) VALUES ({placeholders})",
        tuple(base_values.values()),
    )

    bad_reason = dict(base_values)
    bad_reason["incoming_combined_payload_hash"] = "sha256:" + "c" * 64
    bad_reason["reason"] = "silent_overwrite"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            f"INSERT INTO daily_observation_revisions ({columns}) VALUES ({placeholders})",
            tuple(bad_reason.values()),
        )

    missing_existing_row = dict(base_values)
    missing_existing_row["incoming_combined_payload_hash"] = "sha256:" + "d" * 64
    missing_existing_row["existing_row_id"] = None
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            f"INSERT INTO daily_observation_revisions ({columns}) VALUES ({placeholders})",
            tuple(missing_existing_row.values()),
        )

    conn.close()


def test_init_schema_idempotent():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    init_schema(conn)
    init_schema(conn)  # Should not raise
    conn.close()


def test_log_opportunity_fact_preserves_missing_snapshot_without_latest_fallback(tmp_path):
    from src.state.db import log_opportunity_fact

    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    _create_opportunity_fact_table(conn)

    candidate = types.SimpleNamespace(
        city=types.SimpleNamespace(name="NYC"),
        target_date="2026-04-01",
        event_id="evt-1",
        slug="nyc-apr-1",
        discovery_mode="opening_hunt",
    )
    edge = types.SimpleNamespace(
        bin=types.SimpleNamespace(label="39-40°F"),
        direction="buy_no",
        p_model=0.75,
        p_market=0.7,
        edge=0.12,
        ci_lower=0.05,
        ci_upper=0.17,
    )
    decision = types.SimpleNamespace(
        decision_id="dec-1",
        edge=edge,
        strategy_key="center_buy",
        selected_method="ens_member_counting",
        decision_snapshot_id="",
        availability_status="RATE_LIMITED",
        p_raw=[0.2],
        p_cal=[0.25],
        p_market=[0.3],
        bin_labels=["39-40°F"],
        alpha=0.4,
    )

    result = log_opportunity_fact(
        conn,
        candidate=candidate,
        decision=decision,
        should_trade=False,
        rejection_stage="MARKET_LIQUIDITY",
        rejection_reasons=["429 capacity exhausted"],
        recorded_at="2026-04-03T00:00:00Z",
    )
    row = conn.execute(
        """
        SELECT candidate_id, direction, snapshot_id, p_raw, p_cal, p_market, availability_status, should_trade
        FROM opportunity_fact
        WHERE decision_id = 'dec-1'
        """
    ).fetchone()
    conn.close()

    assert result["status"] == "written"
    assert row["candidate_id"] == "evt-1"
    assert row["direction"] == "buy_no"
    assert row["snapshot_id"] is None
    assert row["p_raw"] == pytest.approx(0.8)
    assert row["p_cal"] == pytest.approx(0.75)
    assert row["p_market"] == pytest.approx(0.7)
    assert row["availability_status"] == "rate_limited"
    assert row["should_trade"] == 0


def test_log_opportunity_fact_skips_missing_table_explicitly(tmp_path):
    from src.state.db import log_opportunity_fact

    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    candidate = types.SimpleNamespace(
        city=types.SimpleNamespace(name="NYC"),
        target_date="2026-04-01",
        event_id="evt-2",
        discovery_mode="opening_hunt",
    )
    decision = types.SimpleNamespace(
        decision_id="dec-2",
        edge=None,
        strategy_key="",
        selected_method="ens_member_counting",
        decision_snapshot_id="snap-1",
        availability_status="DATA_UNAVAILABLE",
    )

    result = log_opportunity_fact(
        conn,
        candidate=candidate,
        decision=decision,
        should_trade=False,
        rejection_stage="SIGNAL_QUALITY",
        rejection_reasons=["obs down"],
        recorded_at="2026-04-03T00:00:00Z",
    )
    rows = conn.execute("SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name = 'opportunity_fact'").fetchone()
    conn.close()

    assert result == {"status": "written", "table": "opportunity_fact"}
    assert rows["n"] == 1


def test_log_probability_trace_fact_writes_complete_vector_trace(tmp_path):
    from src.state.db import log_probability_trace_fact, query_probability_trace_completeness

    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    candidate = types.SimpleNamespace(
        city=types.SimpleNamespace(name="NYC"),
        target_date="2026-04-01",
        event_id="evt-pt-1",
        slug="nyc-apr-1",
        discovery_mode="opening_hunt",
        outcomes=[
            {"title": "39-40°F", "range_low": 39, "range_high": 40},
            {"title": "41-42°F", "range_low": 41, "range_high": 42},
        ],
    )
    edge = types.SimpleNamespace(
        bin=types.SimpleNamespace(label="39-40°F"),
        direction="buy_yes",
        p_posterior=0.62,
    )
    decision = types.SimpleNamespace(
        decision_id="pt-dec-1",
        decision_snapshot_id="snap-pt-1",
        edge=edge,
        p_raw=[0.2, 0.8],
        p_cal=[0.25, 0.75],
        p_market=[0.3, 0.7],
        alpha=0.55,
        agreement="AGREE",
        selected_method="ens_member_counting",
        strategy_key="center_buy",
        n_edges_found=2,
        n_edges_after_fdr=1,
    )

    result = log_probability_trace_fact(
        conn,
        candidate=candidate,
        decision=decision,
        recorded_at="2026-04-03T00:00:00Z",
        mode="opening_hunt",
    )
    row = conn.execute(
        """
        SELECT decision_id, candidate_id, trace_status, p_raw_json, p_cal_json,
               p_market_json, p_posterior_json, p_posterior, bin_labels_json
        FROM probability_trace_fact
        WHERE decision_id = 'pt-dec-1'
        """
    ).fetchone()
    completeness = query_probability_trace_completeness(conn)
    conn.close()

    assert result == {
        "status": "written",
        "table": "probability_trace_fact",
        "trace_status": "complete",
    }
    assert row["candidate_id"] == "evt-pt-1"
    assert row["trace_status"] == "complete"
    assert json.loads(row["p_raw_json"]) == [0.2, 0.8]
    assert json.loads(row["p_cal_json"]) == [0.25, 0.75]
    assert json.loads(row["p_market_json"]) == [0.3, 0.7]
    assert row["p_posterior_json"] is None
    assert row["p_posterior"] == pytest.approx(0.62)
    assert json.loads(row["bin_labels_json"]) == ["39-40°F", "41-42°F"]
    assert completeness["trace_rows"] == 1
    assert completeness["complete_rows"] == 1
    assert completeness["with_p_raw_json"] == 1
    assert completeness["with_p_cal_json"] == 1
    assert completeness["with_p_market_json"] == 1


def test_log_probability_trace_fact_marks_pre_vector_unavailable(tmp_path):
    from src.state.db import log_probability_trace_fact, query_probability_trace_completeness

    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    candidate = types.SimpleNamespace(
        city=types.SimpleNamespace(name="NYC"),
        target_date="2026-04-01",
        event_id="evt-pt-2",
        discovery_mode="day0_capture",
        outcomes=[],
    )
    decision = types.SimpleNamespace(
        decision_id="pt-dec-2",
        decision_snapshot_id="",
        edge=None,
        selected_method="day0_observation",
        strategy_key="",
        rejection_stage="SIGNAL_QUALITY",
        availability_status="DATA_UNAVAILABLE",
    )

    result = log_probability_trace_fact(
        conn,
        candidate=candidate,
        decision=decision,
        recorded_at="2026-04-03T00:00:00Z",
        mode="day0_capture",
    )
    row = conn.execute(
        """
        SELECT trace_status, missing_reason_json, p_raw_json, p_cal_json, p_market_json
        FROM probability_trace_fact
        WHERE decision_id = 'pt-dec-2'
        """
    ).fetchone()
    completeness = query_probability_trace_completeness(conn)
    conn.close()

    missing = json.loads(row["missing_reason_json"])
    assert result["trace_status"] == "pre_vector_unavailable"
    assert row["trace_status"] == "pre_vector_unavailable"
    assert missing["missing_vectors"] == ["p_raw_json", "p_cal_json", "p_market_json"]
    assert missing["rejection_stage"] == "SIGNAL_QUALITY"
    assert missing["availability_status"] == "DATA_UNAVAILABLE"
    assert row["p_raw_json"] is None
    assert row["p_cal_json"] is None
    assert row["p_market_json"] is None
    assert completeness["pre_vector_rows"] == 1


def test_probability_trace_completeness_does_not_count_empty_vectors(tmp_path):
    from src.state.db import query_probability_trace_completeness

    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO probability_trace_fact (
            trace_id, decision_id, trace_status, missing_reason_json,
            p_raw_json, p_cal_json, p_market_json, recorded_at
        )
        VALUES (
            'trace-empty', 'dec-empty', 'degraded_missing_vectors', '[]',
            '[]', '[]', '[]', '2026-04-03T00:00:00Z'
        )
        """
    )
    completeness = query_probability_trace_completeness(conn)
    conn.close()

    assert completeness["trace_rows"] == 1
    assert completeness["with_p_raw_json"] == 0
    assert completeness["with_p_cal_json"] == 0
    assert completeness["with_p_market_json"] == 0


def test_log_probability_trace_fact_does_not_scalar_backfill_vectors(tmp_path):
    from src.state.db import log_probability_trace_fact

    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    candidate = types.SimpleNamespace(
        city=types.SimpleNamespace(name="NYC"),
        target_date="2026-04-01",
        event_id="evt-pt-3",
        discovery_mode="opening_hunt",
        outcomes=[
            {"title": "39-40°F", "range_low": 39, "range_high": 40},
        ],
    )
    edge = types.SimpleNamespace(
        bin=types.SimpleNamespace(label="39-40°F"),
        direction="buy_yes",
        p_model=0.61,
        p_market=0.42,
        p_posterior=0.58,
    )
    decision = types.SimpleNamespace(
        decision_id="pt-dec-3",
        decision_snapshot_id="snap-pt-3",
        edge=edge,
        selected_method="ens_member_counting",
        strategy_key="center_buy",
    )

    result = log_probability_trace_fact(
        conn,
        candidate=candidate,
        decision=decision,
        recorded_at="2026-04-03T00:00:00Z",
        mode="opening_hunt",
    )
    row = conn.execute(
        """
        SELECT trace_status, p_raw_json, p_cal_json, p_market_json, p_posterior
        FROM probability_trace_fact
        WHERE decision_id = 'pt-dec-3'
        """
    ).fetchone()
    conn.close()

    assert result["trace_status"] == "pre_vector_unavailable"
    assert row["trace_status"] == "pre_vector_unavailable"
    assert row["p_raw_json"] is None
    assert row["p_cal_json"] is None
    assert row["p_market_json"] is None
    assert row["p_posterior"] == pytest.approx(0.58)


def test_log_probability_trace_fact_degrades_unavailable_decision_context(tmp_path):
    from src.state.db import log_probability_trace_fact, query_probability_trace_completeness

    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    candidate = types.SimpleNamespace(
        city=types.SimpleNamespace(name="NYC"),
        target_date="2026-04-01",
        event_id="evt-pt-4",
        discovery_mode="opening_hunt",
        outcomes=[
            {"title": "39-40°F", "range_low": 39, "range_high": 40},
        ],
    )
    decision = types.SimpleNamespace(
        decision_id="pt-dec-4",
        decision_snapshot_id="snap-pt-4",
        edge=types.SimpleNamespace(
            bin=types.SimpleNamespace(label="39-40°F"),
            direction="buy_yes",
            p_posterior=0.58,
        ),
        p_raw=[0.2],
        p_cal=[0.25],
        p_market=[0.3],
        selected_method="ens_member_counting",
        strategy_key="center_buy",
        rejection_stage="MARKET_LIQUIDITY",
        availability_status="DATA_UNAVAILABLE",
    )

    result = log_probability_trace_fact(
        conn,
        candidate=candidate,
        decision=decision,
        recorded_at="2026-04-03T00:00:00Z",
        mode="opening_hunt",
    )
    row = conn.execute(
        "SELECT trace_status FROM probability_trace_fact WHERE decision_id = 'pt-dec-4'"
    ).fetchone()
    completeness = query_probability_trace_completeness(conn)
    conn.close()

    assert result["trace_status"] == "degraded_decision_context"
    assert row["trace_status"] == "degraded_decision_context"
    assert completeness["complete_rows"] == 0
    assert completeness["degraded_rows"] == 1


def test_log_probability_trace_fact_skips_missing_table_explicitly(tmp_path):
    from src.state.db import log_probability_trace_fact

    conn = get_connection(tmp_path / "raw.db")
    result = log_probability_trace_fact(
        conn,
        candidate=types.SimpleNamespace(city=types.SimpleNamespace(name="NYC"), target_date="2026-04-01"),
        decision=types.SimpleNamespace(decision_id="pt-dec-3"),
        recorded_at="2026-04-03T00:00:00Z",
        mode="opening_hunt",
    )
    conn.close()

    assert result == {"status": "skipped_missing_table", "table": "probability_trace_fact"}


def test_selection_family_and_hypothesis_facts_write_idempotently(tmp_path):
    from src.state.db import log_selection_family_fact, log_selection_hypothesis_fact

    conn = get_connection(tmp_path / "selection_family.db")
    init_schema(conn)

    family_result = log_selection_family_fact(
        conn,
        family_id="fam-1",
        cycle_mode="opening_hunt",
        decision_snapshot_id="snap-1",
        city="NYC",
        target_date="2026-04-01",
        strategy_key="center_buy",
        discovery_mode="opening_hunt",
        created_at="2026-04-01T00:00:00Z",
        meta={"tested_hypotheses": 2},
    )
    hypothesis_result = log_selection_hypothesis_fact(
        conn,
        hypothesis_id="hyp-1",
        family_id="fam-1",
        decision_id="decision-1",
        candidate_id="candidate-1",
        city="NYC",
        target_date="2026-04-01",
        range_label="39-40°F",
        direction="buy_yes",
        p_value=0.01,
        q_value=0.02,
        ci_lower=0.01,
        ci_upper=0.10,
        edge=0.05,
        tested=True,
        passed_prefilter=True,
        selected_post_fdr=True,
        recorded_at="2026-04-01T00:00:01Z",
        meta={"source": "test"},
    )
    hypothesis_result_2 = log_selection_hypothesis_fact(
        conn,
        hypothesis_id="hyp-1",
        family_id="fam-1",
        city="NYC",
        target_date="2026-04-01",
        range_label="39-40°F",
        direction="unknown",
        selected_post_fdr=False,
        recorded_at="2026-04-01T00:00:02Z",
        meta={},
    )
    rows = {
        "families": conn.execute("SELECT COUNT(*) FROM selection_family_fact").fetchone()[0],
        "hypotheses": conn.execute("SELECT COUNT(*) FROM selection_hypothesis_fact").fetchone()[0],
    }
    hypothesis = conn.execute(
        "SELECT direction, selected_post_fdr, recorded_at FROM selection_hypothesis_fact"
    ).fetchone()
    conn.close()

    assert family_result == {"status": "written", "table": "selection_family_fact"}
    assert hypothesis_result == {"status": "written", "table": "selection_hypothesis_fact"}
    assert hypothesis_result_2 == {"status": "written", "table": "selection_hypothesis_fact"}
    assert rows == {"families": 1, "hypotheses": 1}
    assert hypothesis["direction"] == "unknown"
    assert hypothesis["selected_post_fdr"] == 0
    assert hypothesis["recorded_at"] == "2026-04-01T00:00:02Z"


def test_query_data_improvement_inventory_reports_substrate_tables(tmp_path):
    from src.state.db import query_data_improvement_inventory

    conn = get_connection(tmp_path / "inventory.db")
    init_schema(conn)
    inventory = query_data_improvement_inventory(conn)
    conn.close()

    assert inventory["status"] == "ok"
    assert inventory["missing_tables"] == []
    for table in (
        "probability_trace_fact",
        "calibration_decision_group",
        "selection_family_fact",
        "selection_hypothesis_fact",
    ):
        assert inventory["tables"][table] == {"exists": True, "rows": 0}


def test_log_availability_fact_skips_missing_table_explicitly(tmp_path):
    from src.state.db import log_availability_fact

    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    result = log_availability_fact(
        conn,
        availability_id="avail-1",
        scope_type="city_target",
        scope_key="NYC:2026-04-01",
        failure_type="rate_limited",
        started_at="2026-04-03T00:00:00Z",
        ended_at="2026-04-03T00:00:00Z",
        impact="skip",
        details={"availability_status": "RATE_LIMITED"},
    )
    rows = conn.execute("SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name = 'availability_fact'").fetchone()
    conn.close()

    assert result == {"status": "written", "table": "availability_fact"}
    assert rows["n"] == 1


def test_log_execution_fact_skips_missing_table_explicitly(tmp_path):
    from src.state.db import log_execution_fact

    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    result = log_execution_fact(
        conn,
        intent_id="intent-1",
        position_id="pos-1",
        order_role="entry",
        terminal_exec_status="filled",
    )
    rows = conn.execute("SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name = 'execution_fact'").fetchone()
    conn.close()

    assert result == {"status": "written", "table": "execution_fact"}
    assert rows["n"] == 1


def test_log_outcome_fact_skips_missing_table_explicitly(tmp_path):
    from src.state.db import log_outcome_fact

    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    result = log_outcome_fact(
        conn,
        position_id="pos-1",
        outcome=1,
    )
    rows = conn.execute("SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name = 'outcome_fact'").fetchone()
    conn.close()

    assert result == {"status": "written", "table": "outcome_fact"}
    assert rows["n"] == 1


def test_query_p4_fact_smoke_summary_separates_layers(tmp_path):
    from src.state.db import (
        log_availability_fact,
        log_execution_fact,
        log_opportunity_fact,
        log_outcome_fact,
        query_p4_fact_smoke_summary,
    )

    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    _create_opportunity_fact_table(conn)
    _create_availability_fact_table(conn)
    _create_execution_fact_table(conn)
    _create_outcome_fact_table(conn)

    candidate = types.SimpleNamespace(
        city=types.SimpleNamespace(name="NYC"),
        target_date="2026-04-01",
        event_id="evt-1",
        discovery_mode="opening_hunt",
    )
    edge = types.SimpleNamespace(
        bin=types.SimpleNamespace(label="39-40°F"),
        direction="buy_yes",
        p_model=0.6,
        p_market=0.4,
        edge=0.2,
        ci_lower=0.1,
        ci_upper=0.3,
    )
    trade_decision = types.SimpleNamespace(
        decision_id="dec-trade",
        edge=edge,
        strategy_key="center_buy",
        selected_method="ens_member_counting",
        decision_snapshot_id="snap-1",
        availability_status="",
        p_raw=[0.6],
        p_cal=[0.6],
        p_market=[0.4],
        bin_labels=["39-40°F"],
        alpha=0.5,
    )
    no_trade_decision = types.SimpleNamespace(
        decision_id="dec-no-trade",
        edge=None,
        strategy_key="",
        selected_method="ens_member_counting",
        decision_snapshot_id="snap-2",
        availability_status="RATE_LIMITED",
        p_raw=[],
        p_cal=[],
        p_market=[],
        bin_labels=[],
        alpha=0.0,
    )
    no_edge_decision = types.SimpleNamespace(
        decision_id="dec-no-edge",
        edge=None,
        strategy_key="",
        selected_method="ens_member_counting",
        decision_snapshot_id="snap-3",
        availability_status="",
        p_raw=[],
        p_cal=[],
        p_market=[],
        bin_labels=[],
        alpha=0.0,
    )

    log_opportunity_fact(
        conn,
        candidate=candidate,
        decision=trade_decision,
        should_trade=True,
        rejection_stage="",
        rejection_reasons=[],
        recorded_at="2026-04-04T00:00:00Z",
    )
    log_opportunity_fact(
        conn,
        candidate=candidate,
        decision=no_trade_decision,
        should_trade=False,
        rejection_stage="SIGNAL_QUALITY",
        rejection_reasons=["rate limited"],
        recorded_at="2026-04-04T00:00:00Z",
    )
    log_opportunity_fact(
        conn,
        candidate=candidate,
        decision=no_edge_decision,
        should_trade=False,
        rejection_stage="EDGE_INSUFFICIENT",
        rejection_reasons=["small edge"],
        recorded_at="2026-04-04T00:00:00Z",
    )
    log_availability_fact(
        conn,
        availability_id="avail-1",
        scope_type="candidate",
        scope_key="dec-no-trade",
        failure_type="rate_limited",
        started_at="2026-04-04T00:00:00Z",
        ended_at="2026-04-04T00:00:00Z",
        impact="skip",
        details={"availability_status": "RATE_LIMITED"},
    )
    log_execution_fact(
        conn,
        intent_id="exec-1",
        position_id="pos-1",
        decision_id="dec-trade",
        order_role="entry",
        submitted_price=0.4,
        fill_price=0.42,
        shares=25.0,
        fill_quality=0.05,
        terminal_exec_status="filled",
    )
    log_outcome_fact(
        conn,
        position_id="pos-1",
        strategy_key="center_buy",
        decision_snapshot_id="snap-1",
        pnl=15.0,
        outcome=1,
    )

    summary = query_p4_fact_smoke_summary(conn)
    conn.close()

    assert summary["missing_tables"] == []
    assert summary["opportunity"]["total"] == 3
    assert summary["opportunity"]["trade_eligible"] == 1
    assert summary["opportunity"]["no_trade"] == 2
    assert summary["availability"]["total"] == 1
    assert summary["availability"]["failure_types"]["rate_limited"] == 1
    assert summary["execution"]["total"] == 1
    assert summary["execution"]["terminal_status_counts"]["filled"] == 1
    assert summary["execution"]["authority_scope"] == "execution_lifecycle_projection_not_settlement_authority"
    assert summary["outcome"]["total"] == 1
    assert summary["outcome"]["wins"] == 1
    assert summary["outcome"]["authority_scope"] == "legacy_lifecycle_projection_not_settlement_authority"
    assert summary["outcome"]["learning_eligible"] is False
    assert summary["outcome"]["promotion_eligible"] is False
    assert summary["settlement_authority"]["ready_rows"] == 0
    assert summary["settlement_authority"]["learning_eligible_rows"] == 0
    assert summary["separation"]["availability_failures"] == 1
    assert summary["separation"]["opportunity_loss_without_availability"] == 1
    assert summary["separation"]["execution_vs_outcome_gap"] == 0


def test_query_p4_fact_smoke_summary_separates_verified_settlement_authority(tmp_path):
    from src.engine.lifecycle_events import build_settlement_canonical_write
    from src.state.db import append_many_and_project, query_p4_fact_smoke_summary
    from src.state.portfolio import Position

    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)
    _create_execution_fact_table(conn)
    _create_outcome_fact_table(conn)
    conn.execute("INSERT INTO execution_fact (intent_id, order_role, terminal_exec_status) VALUES ('exec-1', 'entry', 'filled')")
    conn.execute(
        """
        INSERT INTO outcome_fact (
            position_id, strategy_key, decision_snapshot_id, pnl, outcome
        ) VALUES ('legacy-outcome', 'center_buy', 'snap-legacy', 99.0, 1)
        """
    )
    pos = Position(
        trade_id="verified-settle",
        market_id="m-verified",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.61,
        decision_snapshot_id="snap-verified",
        strategy_key="center_buy",
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )
    events, projection = build_settlement_canonical_write(
        pos,
        winning_bin="39-40°F",
        won=True,
        outcome=1,
        sequence_no=1,
        phase_before="pending_exit",
        settlement_authority="VERIFIED",
        settlement_truth_source="world.settlements",
        settlement_market_slug="nyc-high-2026-04-01",
        settlement_temperature_metric="high",
        settlement_source="WU",
        settlement_value=40.0,
    )
    append_many_and_project(conn, events, projection)

    summary = query_p4_fact_smoke_summary(conn)
    conn.close()

    assert summary["outcome"]["total"] == 1
    assert summary["outcome"]["pnl_total"] == pytest.approx(99.0)
    assert summary["outcome"]["authority_scope"] == "legacy_lifecycle_projection_not_settlement_authority"
    assert summary["outcome"]["learning_eligible"] is False
    assert summary["settlement_authority"]["source"] == "position_events_or_decision_log_verified_settlement"
    assert summary["settlement_authority"]["ready_rows"] == 1
    assert summary["settlement_authority"]["learning_eligible_rows"] == 1


def test_query_p4_fact_smoke_summary_reports_missing_tables_explicitly(tmp_path):
    from src.state.db import query_p4_fact_smoke_summary

    conn = get_connection(tmp_path / "test.db")
    init_schema(conn)

    summary = query_p4_fact_smoke_summary(conn)
    conn.close()

    assert summary["missing_tables"] == []
    assert summary["opportunity"]["total"] == 0
    assert summary["availability"]["total"] == 0
    assert summary["execution"]["total"] == 0
    assert summary["outcome"]["total"] == 0
    assert summary["outcome"]["authority_scope"] == "legacy_lifecycle_projection_not_settlement_authority"
    assert summary["settlement_authority"]["ready_rows"] == 0


def test_ensemble_snapshots_unique_constraint():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    init_schema(conn)

    row = {
        "city": "NYC", "target_date": "2026-01-15",
        "issue_time": "2026-01-12T00:00:00Z",
        "valid_time": "2026-01-15T00:00:00Z",
        "available_at": "2026-01-12T06:00:00Z",
        "fetch_time": "2026-01-12T06:05:00Z",
        "lead_hours": 72.0,
        "members_json": "[50.0]",
        "model_version": "ecmwf_ifs025",
        "data_version": "v1"
    }

    conn.execute("""
        INSERT INTO ensemble_snapshots
        (city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, model_version, data_version, temperature_metric)
        VALUES (:city, :target_date, :issue_time, :valid_time, :available_at,
                :fetch_time, :lead_hours, :members_json, :model_version, :data_version, 'high')
    """, row)
    conn.commit()

    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""
            INSERT INTO ensemble_snapshots
            (city, target_date, issue_time, valid_time, available_at, fetch_time,
             lead_hours, members_json, model_version, data_version, temperature_metric)
            VALUES (:city, :target_date, :issue_time, :valid_time, :available_at,
                    :fetch_time, :lead_hours, :members_json, :model_version, :data_version, 'high')
        """, row)

    conn.close()


def test_wal_mode_enabled():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = get_connection(db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    conn.close()


def test_manual_portfolio_state_does_not_write_real_exit_audit(monkeypatch):
    from src.state.portfolio import PortfolioState, Position, close_position

    state = PortfolioState()
    state.positions.append(Position(
        trade_id="t1",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        unit="F",
    ))

    def _boom(*args, **kwargs):
        raise AssertionError("real zeus.db should not be touched from manual test state")

    monkeypatch.setattr("src.state.db.get_connection", _boom)

    closed = close_position(state, "t1", 1.0, "SETTLEMENT")
    assert closed is not None


def test_load_portfolio_enables_audit_logging(tmp_path):
    from src.state.portfolio import load_portfolio
    from src.state.db import get_connection, init_schema

    # P4: load_portfolio now requires a healthy canonical DB to enable audit logging.
    # Set up zeus.db (fallback path) with one active position.
    db = get_connection(tmp_path / "zeus.db")
    init_schema(db)
    db.execute(
        """
        INSERT INTO position_current
        (position_id, phase, trade_id, market_id, city, cluster, target_date, bin_label,
         direction, unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
         entry_method, strategy_key, edge_source, discovery_mode, chain_state,
         order_id, order_status, updated_at, temperature_metric)
        VALUES ('t1','active','t1','m1','NYC','US-Northeast','2026-04-01','39-40\u00b0F',
                'buy_yes','F',8.0,20.0,8.0,0.4,0.6,'ens_member_counting','center_buy',
                'center_buy','opening_hunt','unknown','','filled','2026-04-01T00:00:00Z', 'high')
        """
    )
    db.commit()
    db.close()

    state = load_portfolio(tmp_path / "missing.json")
    assert state.audit_logging_enabled is True


def test_load_portfolio_ignores_non_exit_status_payload(tmp_path):
    from src.state.portfolio import load_portfolio
    from src.state.db import get_connection, init_schema

    db = get_connection(tmp_path / "zeus.db")
    init_schema(db)
    _insert_current_position_for_fill_authority_view_test(db, position_id="portfolio-non-exit-status-pos")
    _insert_status_position_event_for_view_test(
        db,
        position_id="portfolio-non-exit-status-pos",
        event_type="CHAIN_SYNCED",
        status="entered",
        occurred_at="2026-04-01T00:05:00+00:00",
    )
    db.commit()
    db.close()

    state = load_portfolio(tmp_path / "missing.json")

    assert len(state.positions) == 1
    assert getattr(state.positions[0].exit_state, "value", state.positions[0].exit_state) == ""


def test_position_current_views_use_fill_authority_current_open_economics(tmp_path):
    from src.state.db import (
        query_portfolio_loader_view,
        query_position_current_status_view,
        query_strategy_health_snapshot,
        refresh_strategy_health,
    )

    conn = get_connection(tmp_path / "fill-authority-views.db")
    init_schema(conn)
    _insert_current_position_for_fill_authority_view_test(
        conn,
        position_id="fill-authority-pos",
        submitted_size_usd=25.0,
        projected_cost_basis_usd=20.0,
        shares=50.0,
        entry_price=0.50,
        mark_price=0.50,
    )
    _insert_entry_execution_fact_for_fill_authority_view_test(
        conn,
        position_id="fill-authority-pos",
        terminal_exec_status="filled",
        fill_price=0.40,
        shares=50.0,
    )
    conn.commit()

    status_view = query_position_current_status_view(conn)
    loader_view = query_portfolio_loader_view(conn)
    refresh = refresh_strategy_health(conn, as_of="2026-04-01T00:05:00+00:00")
    strategy_snapshot = query_strategy_health_snapshot(
        conn,
        now="2026-04-01T00:06:00+00:00",
        max_age_seconds=300,
    )

    status_position = status_view["positions"][0]
    loader_position = loader_view["positions"][0]
    strategy_row = strategy_snapshot["by_strategy"]["center_buy"]

    assert status_view["total_exposure_usd"] == pytest.approx(20.0)
    assert status_position["size_usd"] == pytest.approx(20.0)
    assert status_position["submitted_size_usd"] == pytest.approx(25.0)
    assert status_position["effective_cost_basis_usd"] == pytest.approx(20.0)
    assert status_position["unrealized_pnl"] == pytest.approx(5.0)
    assert status_position["entry_economics_authority"] == "avg_fill_price"
    assert status_position["fill_authority"] == "venue_confirmed_full"
    assert status_position["entry_economics_source"] == "execution_fact"

    assert loader_position["size_usd"] == pytest.approx(20.0)
    assert loader_position["cost_basis_usd"] == pytest.approx(20.0)
    assert loader_position["submitted_size_usd"] == pytest.approx(25.0)
    assert loader_position["entry_price"] == pytest.approx(0.40)
    assert loader_position["entry_price_avg_fill"] == pytest.approx(0.40)
    assert loader_position["shares_filled"] == pytest.approx(50.0)
    assert loader_position["filled_cost_basis_usd"] == pytest.approx(20.0)
    assert loader_position["entry_fill_verified"] is True
    assert loader_position["entry_economics_authority"] == "avg_fill_price"
    assert loader_position["fill_authority"] == "venue_confirmed_full"

    assert refresh["status"] == "refreshed"
    assert strategy_row["open_exposure_usd"] == pytest.approx(20.0)
    assert strategy_row["unrealized_pnl"] == pytest.approx(5.0)

    conn.close()


def test_position_current_status_view_ignores_non_exit_status_payload(tmp_path):
    from src.state.db import query_position_current_status_view

    conn = get_connection(tmp_path / "status-view-non-exit-status.db")
    init_schema(conn)
    _insert_current_position_for_fill_authority_view_test(conn, position_id="non-exit-status-pos")
    _insert_status_position_event_for_view_test(
        conn,
        position_id="non-exit-status-pos",
        event_type="CHAIN_SYNCED",
        status="entered",
        occurred_at="2026-04-01T00:05:00+00:00",
    )
    conn.commit()

    status_view = query_position_current_status_view(conn)
    conn.close()

    assert status_view["positions"][0]["exit_state"] == "none"
    assert status_view["exit_state_counts"]["none"] == 1


def test_portfolio_loader_view_ignores_non_exit_status_payload(tmp_path):
    from src.state.db import query_portfolio_loader_view

    conn = get_connection(tmp_path / "loader-view-non-exit-status.db")
    init_schema(conn)
    _insert_current_position_for_fill_authority_view_test(conn, position_id="loader-non-exit-status-pos")
    _insert_status_position_event_for_view_test(
        conn,
        position_id="loader-non-exit-status-pos",
        event_type="ENTRY_ORDER_FILLED",
        status="filled",
        occurred_at="2026-04-01T00:05:00+00:00",
    )
    conn.commit()

    loader_view = query_portfolio_loader_view(conn)
    conn.close()

    assert loader_view["positions"][0]["exit_state"] == ""


def test_status_views_use_real_exit_event_status_over_newer_non_exit_noise(tmp_path):
    from src.state.db import query_portfolio_loader_view, query_position_current_status_view

    conn = get_connection(tmp_path / "exit-status-over-non-exit-noise.db")
    init_schema(conn)
    _insert_current_position_for_fill_authority_view_test(conn, position_id="exit-status-pos")
    _insert_status_position_event_for_view_test(
        conn,
        position_id="exit-status-pos",
        event_type="EXIT_ORDER_REJECTED",
        status="retry_pending",
        occurred_at="2026-04-01T00:04:00+00:00",
        sequence_no=1,
    )
    _insert_status_position_event_for_view_test(
        conn,
        position_id="exit-status-pos",
        event_type="ENTRY_ORDER_FILLED",
        status="entered",
        occurred_at="2026-04-01T00:05:00+00:00",
        sequence_no=2,
    )
    conn.commit()

    status_view = query_position_current_status_view(conn)
    loader_view = query_portfolio_loader_view(conn)
    conn.close()

    assert status_view["positions"][0]["exit_state"] == "retry_pending"
    assert status_view["exit_state_counts"]["retry_pending"] == 1
    assert loader_view["positions"][0]["exit_state"] == "retry_pending"


def test_status_views_clear_exit_state_on_retry_release(tmp_path):
    from src.state.db import query_portfolio_loader_view, query_position_current_status_view

    conn = get_connection(tmp_path / "exit-retry-release-clears-state.db")
    init_schema(conn)
    _insert_current_position_for_fill_authority_view_test(conn, position_id="exit-release-pos")
    _insert_status_position_event_for_view_test(
        conn,
        position_id="exit-release-pos",
        event_type="EXIT_ORDER_REJECTED",
        status="retry_pending",
        occurred_at="2026-04-01T00:04:00+00:00",
        sequence_no=1,
    )
    # EXIT_RETRY_RELEASED is live telemetry that older/relaxed DBs may carry;
    # the read model must treat it as a clear signal, not as a non-exit noise row.
    conn.execute("PRAGMA ignore_check_constraints = ON")
    try:
        _insert_status_position_event_for_view_test(
            conn,
            position_id="exit-release-pos",
            event_type="EXIT_RETRY_RELEASED",
            status="ready",
            occurred_at="2026-04-01T00:05:00+00:00",
            sequence_no=2,
        )
    finally:
        conn.execute("PRAGMA ignore_check_constraints = OFF")
    conn.commit()

    status_view = query_position_current_status_view(conn)
    loader_view = query_portfolio_loader_view(conn)
    conn.close()

    assert status_view["positions"][0]["exit_state"] == "none"
    assert status_view["exit_state_counts"]["none"] == 1
    assert loader_view["positions"][0]["exit_state"] == ""


def test_position_current_views_do_not_cap_full_open_fill_cost_to_projection(tmp_path):
    from src.state.db import (
        query_portfolio_loader_view,
        query_position_current_status_view,
        query_strategy_health_snapshot,
        refresh_strategy_health,
    )

    conn = get_connection(tmp_path / "full-open-fill-cost-above-projection.db")
    init_schema(conn)
    _insert_current_position_for_fill_authority_view_test(
        conn,
        position_id="full-open-cost-pos",
        submitted_size_usd=10.0,
        projected_cost_basis_usd=10.0,
        shares=20.0,
        entry_price=0.50,
        mark_price=0.60,
    )
    _insert_entry_execution_fact_for_fill_authority_view_test(
        conn,
        position_id="full-open-cost-pos",
        terminal_exec_status="filled",
        fill_price=0.51,
        shares=20.0,
    )
    conn.commit()

    status_view = query_position_current_status_view(conn)
    loader_view = query_portfolio_loader_view(conn)
    refresh = refresh_strategy_health(conn, as_of="2026-04-01T00:05:00+00:00")
    strategy_snapshot = query_strategy_health_snapshot(
        conn,
        now="2026-04-01T00:06:00+00:00",
        max_age_seconds=300,
    )

    status_position = status_view["positions"][0]
    loader_position = loader_view["positions"][0]
    strategy_row = strategy_snapshot["by_strategy"]["center_buy"]

    assert status_view["total_exposure_usd"] == pytest.approx(10.2)
    assert status_position["size_usd"] == pytest.approx(10.2)
    assert status_position["submitted_size_usd"] == pytest.approx(10.0)
    assert status_position["effective_cost_basis_usd"] == pytest.approx(10.2)
    assert status_position["unrealized_pnl"] == pytest.approx(1.8)
    assert status_position["entry_economics_authority"] == "avg_fill_price"
    assert status_position["fill_authority"] == "venue_confirmed_full"

    assert loader_position["size_usd"] == pytest.approx(10.2)
    assert loader_position["cost_basis_usd"] == pytest.approx(10.2)
    assert loader_position["projection_cost_basis_usd"] == pytest.approx(10.0)
    assert loader_position["entry_price"] == pytest.approx(0.51)
    assert loader_position["shares"] == pytest.approx(20.0)
    assert loader_position["shares_filled"] == pytest.approx(20.0)
    assert loader_position["filled_cost_basis_usd"] == pytest.approx(10.2)
    assert loader_position["effective_cost_basis_usd"] == pytest.approx(10.2)
    assert loader_position["entry_fill_verified"] is True

    assert refresh["status"] == "refreshed"
    assert strategy_row["open_exposure_usd"] == pytest.approx(10.2)
    assert strategy_row["unrealized_pnl"] == pytest.approx(1.8)

    conn.close()


def test_position_current_views_missing_open_shares_do_not_reduce_fill_cost(tmp_path):
    from src.state.db import query_portfolio_loader_view, query_position_current_status_view

    conn = get_connection(tmp_path / "missing-open-shares-fill-cost.db")
    init_schema(conn)
    _insert_current_position_for_fill_authority_view_test(
        conn,
        position_id="missing-open-shares-cost-pos",
        submitted_size_usd=10.0,
        projected_cost_basis_usd=10.0,
        shares=0.0,
        entry_price=0.50,
        mark_price=0.60,
    )
    _insert_entry_execution_fact_for_fill_authority_view_test(
        conn,
        position_id="missing-open-shares-cost-pos",
        terminal_exec_status="filled",
        fill_price=0.51,
        shares=20.0,
    )
    conn.commit()

    status_view = query_position_current_status_view(conn)
    loader_view = query_portfolio_loader_view(conn)

    status_position = status_view["positions"][0]
    loader_position = loader_view["positions"][0]

    assert status_position["effective_cost_basis_usd"] == pytest.approx(10.2)
    assert status_position["shares"] == pytest.approx(20.0)
    assert loader_position["effective_cost_basis_usd"] == pytest.approx(10.2)
    assert loader_position["shares"] == pytest.approx(20.0)
    assert loader_position["filled_cost_basis_usd"] == pytest.approx(10.2)

    conn.close()


def test_position_current_views_do_not_promote_nonfinal_fill_like_execution_fact(tmp_path):
    from src.state.db import query_portfolio_loader_view, query_position_current_status_view

    conn = get_connection(tmp_path / "nonfinal-fill-authority-views.db")
    init_schema(conn)
    _insert_current_position_for_fill_authority_view_test(
        conn,
        position_id="nonfinal-fill-pos",
        submitted_size_usd=25.0,
        projected_cost_basis_usd=20.0,
        shares=50.0,
        entry_price=0.50,
        mark_price=0.50,
    )
    _insert_entry_execution_fact_for_fill_authority_view_test(
        conn,
        position_id="nonfinal-fill-pos",
        terminal_exec_status="pending_fill_authority",
        fill_price=0.40,
        shares=50.0,
        filled_at=None,
    )
    conn.commit()

    status_view = query_position_current_status_view(conn)
    loader_view = query_portfolio_loader_view(conn)

    status_position = status_view["positions"][0]
    loader_position = loader_view["positions"][0]

    assert status_view["total_exposure_usd"] == pytest.approx(25.0)
    assert status_position["size_usd"] == pytest.approx(25.0)
    assert status_position["effective_cost_basis_usd"] == pytest.approx(25.0)
    assert status_position["submitted_size_usd"] == pytest.approx(25.0)
    assert status_position["entry_economics_authority"] == "legacy_unknown"
    assert status_position["fill_authority"] == "none"
    assert status_position["entry_economics_source"] == "position_current_projection"

    assert loader_position["size_usd"] == pytest.approx(25.0)
    assert loader_position["cost_basis_usd"] == pytest.approx(20.0)
    assert loader_position["entry_price"] == pytest.approx(0.50)
    assert loader_position["entry_fill_verified"] is False
    assert loader_position["entry_economics_authority"] == "legacy_unknown"
    assert loader_position["fill_authority"] == "none"

    conn.close()


def test_position_current_pending_entry_without_fill_authority_is_not_open_exposure(tmp_path):
    from src.state.db import query_portfolio_loader_view, query_position_current_status_view

    conn = get_connection(tmp_path / "pending-entry-no-fill-authority.db")
    init_schema(conn)
    _insert_current_position_for_fill_authority_view_test(
        conn,
        position_id="pending-entry-no-fill",
        phase="pending_entry",
        order_status="pending",
        submitted_size_usd=25.0,
        projected_cost_basis_usd=20.0,
        shares=50.0,
        entry_price=0.50,
        mark_price=0.50,
    )
    conn.commit()

    status_view = query_position_current_status_view(conn)
    loader_view = query_portfolio_loader_view(conn)
    conn.close()

    status_position = status_view["positions"][0]
    loader_position = loader_view["positions"][0]

    assert status_view["total_exposure_usd"] == 0.0
    assert status_position["submitted_size_usd"] == pytest.approx(25.0)
    assert status_position["size_usd"] == 0.0
    assert status_position["effective_cost_basis_usd"] == 0.0
    assert status_position["shares"] == 0.0
    assert status_position["entry_price"] == 0.0
    assert status_position["entry_economics_authority"] == "legacy_unknown"
    assert status_position["fill_authority"] == "none"
    assert status_position["entry_economics_source"] == "pending_entry_without_fill_authority"

    assert loader_position["submitted_size_usd"] == pytest.approx(25.0)
    assert loader_position["size_usd"] == 0.0
    assert loader_position["cost_basis_usd"] == 0.0
    assert loader_position["entry_price"] == 0.0
    assert loader_position["shares"] == 0.0
    assert loader_position["entry_fill_verified"] is False
    assert loader_position["entry_economics_source"] == "pending_entry_without_fill_authority"


def test_portfolio_loader_missing_projection_env_stays_unknown_until_builder_rejects(tmp_path):
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.db import query_portfolio_loader_view
    from src.state.portfolio import _position_from_projection_row, POSITION_ENV_UNKNOWN

    conn = get_connection(tmp_path / "portfolio-loader-missing-env.db")
    init_schema(conn)
    _insert_current_position_for_fill_authority_view_test(
        conn,
        position_id="loader-missing-env",
        submitted_size_usd=25.0,
        projected_cost_basis_usd=20.0,
        shares=50.0,
        entry_price=0.50,
        mark_price=0.50,
    )
    conn.commit()

    loader_view = query_portfolio_loader_view(conn)
    conn.close()

    loader_position = loader_view["positions"][0]
    assert loader_position["env"] == POSITION_ENV_UNKNOWN
    position = _position_from_projection_row(loader_position, current_mode="live")
    assert position.env == POSITION_ENV_UNKNOWN
    position.entered_at = "2026-04-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="position event env='unknown_env' is invalid"):
        build_entry_canonical_write(
            position,
            decision_id="dec-loader-missing-env",
            source_module="tests.test_db",
        )


def test_portfolio_loader_latest_missing_event_env_does_not_promote_older_env(tmp_path):
    from src.state.db import query_portfolio_loader_view
    from src.state.portfolio import POSITION_ENV_UNKNOWN

    conn = get_connection(tmp_path / "portfolio-loader-latest-missing-event-env.db")
    init_schema(conn)
    _insert_current_position_for_fill_authority_view_test(
        conn,
        position_id="loader-latest-missing-event-env",
        submitted_size_usd=25.0,
        projected_cost_basis_usd=20.0,
        shares=50.0,
        entry_price=0.50,
        mark_price=0.50,
    )
    conn.execute("DROP TABLE position_events")
    conn.execute("CREATE TABLE position_events (position_id TEXT, sequence_no INTEGER, env TEXT)")
    conn.execute(
        "INSERT INTO position_events (position_id, sequence_no, env) VALUES (?, ?, ?)",
        ("loader-latest-missing-event-env", 1, "live"),
    )
    conn.execute(
        "INSERT INTO position_events (position_id, sequence_no, env) VALUES (?, ?, ?)",
        ("loader-latest-missing-event-env", 2, None),
    )
    conn.commit()

    loader_view = query_portfolio_loader_view(conn)
    conn.close()

    loader_position = loader_view["positions"][0]
    assert loader_position["env"] == POSITION_ENV_UNKNOWN


def test_position_current_views_preserve_current_open_reduction_after_partial_exit(tmp_path):
    from src.state.db import (
        query_portfolio_loader_view,
        query_position_current_status_view,
        query_strategy_health_snapshot,
        refresh_strategy_health,
    )

    conn = get_connection(tmp_path / "reduced-open-fill-authority-views.db")
    init_schema(conn)
    _insert_current_position_for_fill_authority_view_test(
        conn,
        position_id="reduced-open-fill-pos",
        submitted_size_usd=25.0,
        projected_cost_basis_usd=10.0,
        shares=20.0,
        entry_price=0.50,
        mark_price=0.60,
    )
    _insert_entry_execution_fact_for_fill_authority_view_test(
        conn,
        position_id="reduced-open-fill-pos",
        terminal_exec_status="filled",
        fill_price=0.50,
        shares=50.0,
    )
    conn.commit()

    status_view = query_position_current_status_view(conn)
    loader_view = query_portfolio_loader_view(conn)
    refresh = refresh_strategy_health(conn, as_of="2026-04-01T00:05:00+00:00")
    strategy_snapshot = query_strategy_health_snapshot(
        conn,
        now="2026-04-01T00:06:00+00:00",
        max_age_seconds=300,
    )

    status_position = status_view["positions"][0]
    loader_position = loader_view["positions"][0]
    strategy_row = strategy_snapshot["by_strategy"]["center_buy"]

    assert status_view["total_exposure_usd"] == pytest.approx(10.0)
    assert status_position["size_usd"] == pytest.approx(10.0)
    assert status_position["effective_cost_basis_usd"] == pytest.approx(10.0)
    assert status_position["filled_cost_basis_usd"] == pytest.approx(25.0)
    assert status_position["shares"] == pytest.approx(20.0)
    assert status_position["shares_filled"] == pytest.approx(50.0)
    assert status_position["unrealized_pnl"] == pytest.approx(2.0)

    assert loader_position["size_usd"] == pytest.approx(10.0)
    assert loader_position["cost_basis_usd"] == pytest.approx(10.0)
    assert loader_position["effective_cost_basis_usd"] == pytest.approx(10.0)
    assert loader_position["filled_cost_basis_usd"] == pytest.approx(25.0)
    assert loader_position["shares"] == pytest.approx(20.0)
    assert loader_position["shares_filled"] == pytest.approx(50.0)

    assert refresh["status"] == "refreshed"
    assert strategy_row["open_exposure_usd"] == pytest.approx(10.0)
    assert strategy_row["unrealized_pnl"] == pytest.approx(2.0)

    conn.close()


def test_log_trade_entry_persists_replay_critical_fields(tmp_path):
    from src.state.db import log_trade_entry
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, model_version, data_version, temperature_metric)
        VALUES (123, 'NYC', '2026-04-01', '2026-03-31T00:00:00Z', '2026-04-01T00:00:00Z',
                '2026-03-31T01:00:00Z', '2026-03-31T01:00:00Z', 24.0, '[40.0]', 'ecmwf_ifs025', 'test', 'high')
        """
    )

    pos = Position(
        trade_id="t1",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        entry_ci_width=0.10,
        decision_snapshot_id="123",
        calibration_version="platt_v1",
        strategy="center_buy",
        edge_source="center_buy",
        discovery_mode="opening_hunt",
        market_hours_open=2.5,
        fill_quality=0.01,
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch", "platt_calibration"],
        settlement_semantics_json='{"measurement_unit":"F"}',
        epistemic_context_json='{"decision_time_utc":"2026-04-01T01:00:00Z"}',
        edge_context_json='{"forward_edge":0.2}',
        entered_at="2026-04-01T01:00:00Z",
    )

    log_trade_entry(conn, pos)
    conn.commit()

    row = conn.execute(
        """
        SELECT forecast_snapshot_id, calibration_model_version, strategy, edge_source,
               discovery_mode, market_hours_open, fill_quality, entry_method,
               selected_method, applied_validations_json,
               settlement_semantics_json, epistemic_context_json, edge_context_json
        FROM trade_decisions
        ORDER BY trade_id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert row["forecast_snapshot_id"] == 123
    assert row["calibration_model_version"] == "platt_v1"
    assert row["strategy"] == "center_buy"
    assert row["edge_source"] == "center_buy"
    assert row["discovery_mode"] == "opening_hunt"
    assert row["market_hours_open"] == pytest.approx(2.5)
    assert row["fill_quality"] == pytest.approx(0.01)
    assert row["entry_method"] == "ens_member_counting"
    assert row["selected_method"] == "ens_member_counting"
    assert "platt_calibration" in row["applied_validations_json"]
    assert row["settlement_semantics_json"] == '{"measurement_unit":"F"}'
    assert row["epistemic_context_json"] == '{"decision_time_utc":"2026-04-01T01:00:00Z"}'
    assert row["edge_context_json"] == '{"forward_edge":0.2}'


def test_log_trade_entry_tolerates_forecast_class_snapshot_ids(tmp_path):
    from src.state.db import log_trade_entry
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="t-k1",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        entry_ci_width=0.10,
        decision_snapshot_id="1129974",
        calibration_version="platt_v1",
        strategy="opening_inertia",
        edge_source="opening_inertia",
        discovery_mode="opening_hunt",
        entry_method="executable_forecast",
        selected_method="executable_forecast",
        order_posted_at="2026-04-01T01:00:00Z",
        order_id="0xlive-order",
        order_status="live",
        state="pending_tracked",
    )

    log_trade_entry(conn, pos)
    conn.commit()

    row = conn.execute(
        """
        SELECT forecast_snapshot_id, runtime_trade_id, order_id, status
        FROM trade_decisions
        ORDER BY trade_id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["forecast_snapshot_id"] is None
    assert row["runtime_trade_id"] == "t-k1"
    assert row["order_id"] == "0xlive-order"
    assert row["status"] == "pending_tracked"


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_log_trade_entry_emits_position_event(tmp_path):
    from src.state.db import log_trade_entry, query_position_events
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="rt-entry",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        entry_ci_width=0.10,
        decision_snapshot_id="snap-1",
        strategy="center_buy",
        edge_source="center_buy",
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        order_posted_at="2026-04-01T01:00:00Z",
        order_id="o1",
        order_status="pending",
        state="pending_tracked",
    )

    log_trade_entry(conn, pos)
    conn.commit()

    events = query_position_events(conn, "rt-entry")
    conn.close()

    assert len(events) == 1
    assert events[0]["event_type"] == "POSITION_ENTRY_RECORDED"
    assert events[0]["position_state"] == "pending_tracked"
    assert events[0]["decision_snapshot_id"] == "snap-1"
    assert events[0]["details"]["status"] == "pending_tracked"
    assert events[0]["details"]["entry_method"] == "ens_member_counting"



@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_log_trade_exit_persists_exit_reason_and_strategy(tmp_path):
    from src.state.db import log_trade_exit, query_position_events
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, model_version, data_version, temperature_metric)
        VALUES (456, 'NYC', '2026-04-01', '2026-03-31T00:00:00Z', '2026-04-01T00:00:00Z',
                '2026-03-31T01:00:00Z', '2026-03-31T01:00:00Z', 24.0, '[40.0]', 'ecmwf_ifs025', 'test', 'high')
        """
    )

    pos = Position(
        trade_id="t2",
        market_id="m2",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_no",
        unit="F",
        size_usd=12.0,
        entry_price=0.70,
        p_posterior=0.82,
        edge=0.12,
        decision_snapshot_id="456",
        calibration_version="platt_v2",
        strategy="shoulder_sell",
        edge_source="shoulder_sell",
        discovery_mode="update_reaction",
        market_hours_open=14.0,
        fill_quality=-0.02,
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        applied_validations=["risk_limits", "anti_churn"],
        exit_reason="EDGE_REVERSAL",
        admin_exit_reason="",
        settlement_semantics_json='{"measurement_unit":"F"}',
        epistemic_context_json='{"decision_time_utc":"2026-04-01T05:00:00Z"}',
        edge_context_json='{"forward_edge":0.12}',
        exit_price=0.55,
        pnl=-2.57,
        last_exit_at="2026-04-01T05:00:00Z",
    )

    log_trade_exit(conn, pos)
    conn.commit()

    row = conn.execute(
        """
        SELECT forecast_snapshot_id, calibration_model_version, strategy, edge_source,
               discovery_mode, market_hours_open, fill_quality, entry_method,
               selected_method, applied_validations_json, exit_reason, admin_exit_reason,
               settlement_semantics_json, epistemic_context_json, edge_context_json
        FROM trade_decisions
        ORDER BY trade_id DESC LIMIT 1
        """
    ).fetchone()
    events = query_position_events(conn, "t2")
    conn.close()

    assert row["forecast_snapshot_id"] == 456
    assert any(event["event_type"] == "POSITION_EXIT_RECORDED" for event in events)
    exit_event = next(event for event in events if event["event_type"] == "POSITION_EXIT_RECORDED")
    assert exit_event["details"]["exit_reason"] == "EDGE_REVERSAL"
    assert exit_event["details"]["status"] == "exited"
    assert row["calibration_model_version"] == "platt_v2"
    assert row["strategy"] == "shoulder_sell"
    assert row["edge_source"] == "shoulder_sell"
    assert row["discovery_mode"] == "update_reaction"
    assert row["market_hours_open"] == pytest.approx(14.0)
    assert row["fill_quality"] == pytest.approx(-0.02)
    assert row["entry_method"] == "ens_member_counting"
    assert row["selected_method"] == "ens_member_counting"
    assert "anti_churn" in row["applied_validations_json"]
    assert row["exit_reason"] == "EDGE_REVERSAL"
    assert row["settlement_semantics_json"] == '{"measurement_unit":"F"}'
    assert row["epistemic_context_json"] == '{"decision_time_utc":"2026-04-01T05:00:00Z"}'
    assert row["edge_context_json"] == '{"forward_edge":0.12}'


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_update_trade_lifecycle_emits_position_event(tmp_path):
    from src.state.db import log_trade_entry, query_position_events, update_trade_lifecycle
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="rt-life",
        market_id="m3",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=15.0,
        entry_price=0.41,
        p_posterior=0.61,
        edge=0.20,
        decision_snapshot_id="snap-life",
        strategy="center_buy",
        edge_source="center_buy",
        order_id="o-life",
        order_status="pending",
        order_posted_at="2026-04-01T01:00:00Z",
        state="pending_tracked",
    )
    log_trade_entry(conn, pos)

    pos.state = "entered"
    pos.entry_order_id = "o-life"
    pos.entry_fill_verified = True
    pos.entered_at = "2026-04-01T01:05:00Z"
    pos.order_status = "filled"
    pos.chain_state = "synced"
    update_trade_lifecycle(conn, pos)
    conn.commit()

    events = query_position_events(conn, "rt-life")
    conn.close()

    lifecycle_events = [event for event in events if event["event_type"] == "POSITION_LIFECYCLE_UPDATED"]
    assert len(lifecycle_events) == 1
    assert lifecycle_events[0]["details"]["status"] == "entered"
    assert lifecycle_events[0]["details"]["entry_order_id"] == "o-life"
    assert lifecycle_events[0]["details"]["entry_fill_verified"] is True
    assert lifecycle_events[0]["details"]["order_status"] == "filled"
    assert lifecycle_events[0]["details"]["chain_state"] == "synced"


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_log_execution_report_emits_fill_telemetry(tmp_path):
    from src.execution.executor import OrderResult
    from src.state.db import log_execution_report, query_position_events
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _create_execution_fact_table(conn)

    pos = Position(
        trade_id="rt-exec",
        market_id="m4",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        order_posted_at="2026-04-01T01:00:00Z",
        order_status="filled",
        state="entered",
    )
    result = OrderResult(
        trade_id="rt-exec",
        status="filled",
        fill_price=0.42,
        filled_at="2026-04-01T01:00:05Z",
        submitted_price=0.40,
        shares=25.0,
        timeout_seconds=60,
    )

    log_execution_report(conn, pos, result, decision_id="dec-fill")
    conn.commit()

    events = query_position_events(conn, "rt-exec")
    fact = conn.execute(
        """
        SELECT decision_id, order_role, posted_at, filled_at, submitted_price, fill_price, shares,
               fill_quality, venue_status, terminal_exec_status
        FROM execution_fact
        WHERE intent_id = 'rt-exec:entry'
        """
    ).fetchone()
    conn.close()

    assert len(events) == 1
    assert events[0]["event_type"] == "ORDER_FILLED"
    assert events[0]["details"]["submitted_price"] == pytest.approx(0.40)
    assert events[0]["details"]["fill_price"] == pytest.approx(0.42)
    assert events[0]["details"]["fill_quality"] == pytest.approx(0.05)
    assert fact["decision_id"] == "dec-fill"
    assert fact["order_role"] == "entry"
    assert fact["posted_at"] == "2026-04-01T01:00:00Z"
    assert fact["filled_at"] == "2026-04-01T01:00:05Z"
    assert fact["submitted_price"] == pytest.approx(0.40)
    assert fact["fill_price"] == pytest.approx(0.42)
    assert fact["shares"] == pytest.approx(25.0)
    assert fact["fill_quality"] == pytest.approx(0.05)
    assert fact["venue_status"] == "filled"
    assert fact["terminal_exec_status"] == "filled"


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_log_execution_report_emits_rejected_entry_event(tmp_path):
    from src.execution.executor import OrderResult
    from src.state.db import log_execution_report, query_position_events
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _create_execution_fact_table(conn)

    pos = Position(
        trade_id="rt-exec-rejected",
        market_id="m4",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        order_status="rejected",
    )
    result = OrderResult(
        trade_id="rt-exec-rejected",
        status="rejected",
        submitted_price=0.40,
        reason="insufficient_liquidity",
    )

    log_execution_report(conn, pos, result, decision_id="dec-reject")
    conn.commit()

    events = query_position_events(conn, "rt-exec-rejected")
    fact = conn.execute(
        """
        SELECT decision_id, order_role, voided_at, submitted_price, terminal_exec_status
        FROM execution_fact
        WHERE intent_id = 'rt-exec-rejected:entry'
        """
    ).fetchone()
    conn.close()

    assert len(events) == 1
    assert events[0]["event_type"] == "ORDER_REJECTED"
    assert events[0]["details"]["status"] == "rejected"
    assert events[0]["details"]["reason"] == "insufficient_liquidity"
    assert fact["decision_id"] == "dec-reject"
    assert fact["order_role"] == "entry"
    assert fact["voided_at"] is not None
    assert fact["submitted_price"] == pytest.approx(0.40)
    assert fact["terminal_exec_status"] == "rejected"


def test_log_execution_report_does_not_promote_nonfinal_fill_price(tmp_path):
    from src.execution.executor import OrderResult
    from src.state.db import log_execution_report
    from src.state.portfolio import (
        ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        FILL_AUTHORITY_NONE,
        Position,
    )

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _create_execution_fact_table(conn)

    pos = Position(
        trade_id="rt-exec-nonfinal",
        market_id="m4",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=11.0,
        entry_price=0.55,
        entry_price_submitted=0.55,
        p_posterior=0.60,
        edge=0.05,
        shares=20.0,
        order_posted_at="2026-04-01T01:00:00Z",
        order_status="filled",
        state="pending_tracked",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_economics_authority=ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        fill_authority=FILL_AUTHORITY_NONE,
    )
    result = OrderResult(
        trade_id="rt-exec-nonfinal",
        status="filled",
        fill_price=0.60,
        submitted_price=0.55,
        shares=20.0,
        command_state="ACKED",
    )

    log_execution_report(conn, pos, result, decision_id="dec-nonfinal")
    conn.commit()

    fact = conn.execute(
        """
        SELECT decision_id, filled_at, submitted_price, fill_price, shares, fill_quality,
               venue_status, terminal_exec_status
        FROM execution_fact
        WHERE intent_id = 'rt-exec-nonfinal:entry'
        """
    ).fetchone()
    conn.close()

    assert fact["decision_id"] == "dec-nonfinal"
    assert fact["filled_at"] is None
    assert fact["submitted_price"] == pytest.approx(0.55)
    assert fact["fill_price"] is None
    assert fact["shares"] is None
    assert fact["fill_quality"] is None
    assert fact["venue_status"] == "filled"
    assert fact["terminal_exec_status"] == "pending_fill_authority"


def test_log_execution_report_uses_entry_fill_authority_average_not_limit_price(tmp_path):
    from src.execution.executor import OrderResult
    from src.state.db import log_execution_report
    from src.state.portfolio import (
        ENTRY_ECONOMICS_AVG_FILL_PRICE,
        FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        Position,
    )

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _create_execution_fact_table(conn)
    shares = 24.39
    submitted_limit = 0.40
    avg_fill = 0.41

    pos = Position(
        trade_id="rt-exec-authority-fill",
        market_id="m4",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=shares * avg_fill,
        entry_price=submitted_limit,
        entry_price_submitted=submitted_limit,
        entry_price_avg_fill=avg_fill,
        shares=shares,
        shares_filled=shares,
        filled_cost_basis_usd=shares * avg_fill,
        p_posterior=0.60,
        edge=0.05,
        order_posted_at="2026-04-01T01:00:00Z",
        entered_at="2026-04-01T01:00:05Z",
        order_status="filled",
        state="entered",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_economics_authority=ENTRY_ECONOMICS_AVG_FILL_PRICE,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    )
    result = OrderResult(
        trade_id="rt-exec-authority-fill",
        status="filled",
        fill_price=submitted_limit,
        filled_at="2026-04-01T01:00:05Z",
        submitted_price=submitted_limit,
        shares=shares,
        command_state="FILLED",
    )

    log_execution_report(conn, pos, result, decision_id="dec-authority-fill")
    conn.commit()

    fact = conn.execute(
        """
        SELECT submitted_price, fill_price, shares, fill_quality, terminal_exec_status
        FROM execution_fact
        WHERE intent_id = 'rt-exec-authority-fill:entry'
        """
    ).fetchone()
    conn.close()

    assert fact["submitted_price"] == pytest.approx(submitted_limit)
    assert fact["fill_price"] == pytest.approx(avg_fill)
    assert fact["shares"] == pytest.approx(shares)
    assert fact["fill_quality"] == pytest.approx((avg_fill - submitted_limit) / submitted_limit)
    assert fact["terminal_exec_status"] == "filled"


def test_log_execution_report_clears_stale_nonfinal_fill_telemetry(tmp_path):
    from src.execution.executor import OrderResult
    from src.state.db import log_execution_fact, log_execution_report
    from src.state.portfolio import (
        ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        FILL_AUTHORITY_NONE,
        Position,
    )

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _create_execution_fact_table(conn)
    log_execution_fact(
        conn,
        intent_id="rt-exec-stale:entry",
        position_id="rt-exec-stale",
        decision_id="old-dec",
        order_role="entry",
        strategy_key="center_buy",
        posted_at="2026-04-01T01:00:00Z",
        filled_at="2026-04-01T01:00:03Z",
        submitted_price=0.55,
        fill_price=0.60,
        shares=20.0,
        fill_quality=0.09,
        latency_seconds=3.0,
        venue_status="filled",
        terminal_exec_status="filled",
    )
    conn.commit()

    pos = Position(
        trade_id="rt-exec-stale",
        market_id="m4",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=11.0,
        entry_price=0.55,
        entry_price_submitted=0.55,
        p_posterior=0.60,
        edge=0.05,
        shares=20.0,
        order_posted_at="2026-04-01T01:00:00Z",
        order_status="filled",
        state="pending_tracked",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_economics_authority=ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        fill_authority=FILL_AUTHORITY_NONE,
    )
    result = OrderResult(
        trade_id="rt-exec-stale",
        status="filled",
        fill_price=0.61,
        submitted_price=0.55,
        shares=21.0,
        command_state="ACKED",
    )

    log_execution_report(conn, pos, result, decision_id="dec-nonfinal")
    conn.commit()

    fact = conn.execute(
        """
        SELECT decision_id, filled_at, submitted_price, fill_price, shares, fill_quality,
               latency_seconds, venue_status, terminal_exec_status
        FROM execution_fact
        WHERE intent_id = 'rt-exec-stale:entry'
        """
    ).fetchone()
    conn.close()

    assert fact["decision_id"] == "dec-nonfinal"
    assert fact["filled_at"] is None
    assert fact["submitted_price"] == pytest.approx(0.55)
    assert fact["fill_price"] is None
    assert fact["shares"] is None
    assert fact["fill_quality"] is None
    assert fact["latency_seconds"] is None
    assert fact["venue_status"] == "filled"
    assert fact["terminal_exec_status"] == "pending_fill_authority"


def test_log_execution_report_clears_stale_pending_fill_like_telemetry(tmp_path):
    from src.execution.executor import OrderResult
    from src.state.db import log_execution_fact, log_execution_report, query_p4_fact_smoke_summary
    from src.state.portfolio import (
        ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        FILL_AUTHORITY_NONE,
        Position,
    )

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _create_execution_fact_table(conn)
    log_execution_fact(
        conn,
        intent_id="rt-exec-pending-stale:entry",
        position_id="rt-exec-pending-stale",
        decision_id="old-dec",
        order_role="entry",
        strategy_key="center_buy",
        posted_at="2026-04-01T01:00:00Z",
        filled_at="2026-04-01T01:00:03Z",
        submitted_price=0.55,
        fill_price=0.60,
        shares=20.0,
        fill_quality=0.09,
        latency_seconds=3.0,
        venue_status="filled",
        terminal_exec_status="filled",
    )
    conn.commit()

    pos = Position(
        trade_id="rt-exec-pending-stale",
        market_id="m4",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=11.0,
        entry_price=0.55,
        entry_price_submitted=0.55,
        p_posterior=0.60,
        edge=0.05,
        shares=20.0,
        order_posted_at="2026-04-01T01:00:00Z",
        order_status="pending",
        state="pending_tracked",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_economics_authority=ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        fill_authority=FILL_AUTHORITY_NONE,
    )
    result = OrderResult(
        trade_id="rt-exec-pending-stale",
        status="pending",
        fill_price=0.61,
        submitted_price=0.55,
        shares=21.0,
        command_state="ACKED",
    )

    log_execution_report(conn, pos, result, decision_id="dec-pending-nonfinal")
    conn.commit()

    fact = conn.execute(
        """
        SELECT decision_id, filled_at, submitted_price, fill_price, shares, fill_quality,
               latency_seconds, venue_status, terminal_exec_status
        FROM execution_fact
        WHERE intent_id = 'rt-exec-pending-stale:entry'
        """
    ).fetchone()
    summary = query_p4_fact_smoke_summary(conn)
    conn.close()

    assert fact["decision_id"] == "dec-pending-nonfinal"
    assert fact["filled_at"] is None
    assert fact["submitted_price"] == pytest.approx(0.55)
    assert fact["fill_price"] is None
    assert fact["shares"] is None
    assert fact["fill_quality"] is None
    assert fact["latency_seconds"] is None
    assert fact["venue_status"] == "pending"
    assert fact["terminal_exec_status"] == "pending_fill_authority"
    assert summary["execution"]["avg_fill_quality"] is None


def test_log_exit_attempt_clears_stale_nonfinal_exit_fill_telemetry(tmp_path):
    from src.state.db import (
        log_execution_fact,
        log_exit_attempt_event,
        query_p4_fact_smoke_summary,
    )
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _create_execution_fact_table(conn)
    log_execution_fact(
        conn,
        intent_id="rt-exit-stale:exit",
        position_id="rt-exit-stale",
        decision_id="old-dec",
        order_role="exit",
        strategy_key="center_buy",
        posted_at="2026-04-01T01:00:00Z",
        filled_at="2026-04-01T01:05:00Z",
        submitted_price=0.44,
        fill_price=0.43,
        shares=25.0,
        fill_quality=-0.02,
        latency_seconds=300.0,
        venue_status="CONFIRMED",
        terminal_exec_status="filled",
    )
    conn.commit()

    pos = Position(
        trade_id="rt-exit-stale",
        market_id="m7",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        strategy="center_buy",
        edge_source="center_buy",
        exit_reason="EDGE_REVERSAL",
        state="holding",
        exit_state="sell_pending",
        shares=25.0,
        last_exit_order_id="sell-1",
        last_monitor_market_price=0.44,
    )

    log_exit_attempt_event(
        conn,
        pos,
        order_id="sell-2",
        status="placed",
        current_market_price=0.44,
        best_bid=0.43,
        shares=25.0,
        timestamp="2026-04-01T01:10:00Z",
    )
    conn.commit()

    fact = conn.execute(
        """
        SELECT filled_at, submitted_price, fill_price, shares, fill_quality,
               latency_seconds, venue_status, terminal_exec_status
        FROM execution_fact
        WHERE intent_id = 'rt-exit-stale:exit'
        """
    ).fetchone()
    summary = query_p4_fact_smoke_summary(conn)
    conn.close()

    assert fact["filled_at"] is None
    assert fact["submitted_price"] == pytest.approx(0.44)
    assert fact["fill_price"] is None
    assert fact["shares"] is None
    assert fact["fill_quality"] is None
    assert fact["latency_seconds"] is None
    assert fact["venue_status"] == "placed"
    assert fact["terminal_exec_status"] == "placed"
    assert summary["execution"]["avg_fill_quality"] is None


def test_log_execution_report_clears_stale_missing_status_fill_authority(tmp_path):
    from src.execution.executor import OrderResult
    from src.state.db import log_execution_fact, log_execution_report, query_p4_fact_smoke_summary
    from src.state.portfolio import (
        ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        FILL_AUTHORITY_NONE,
        Position,
    )

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _create_execution_fact_table(conn)
    log_execution_fact(
        conn,
        intent_id="rt-exec-missing-status:entry",
        position_id="rt-exec-missing-status",
        decision_id="old-dec",
        order_role="entry",
        strategy_key="center_buy",
        posted_at="2026-04-01T01:00:00Z",
        filled_at="2026-04-01T01:00:03Z",
        submitted_price=0.55,
        fill_price=0.60,
        shares=20.0,
        fill_quality=0.09,
        latency_seconds=3.0,
        venue_status="CONFIRMED",
        terminal_exec_status="filled",
    )
    conn.commit()

    pos = Position(
        trade_id="rt-exec-missing-status",
        market_id="m4",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=11.0,
        entry_price=0.55,
        entry_price_submitted=0.55,
        p_posterior=0.60,
        edge=0.05,
        shares=20.0,
        order_posted_at="2026-04-01T01:00:00Z",
        order_status="",
        state="pending_tracked",
        strategy_key="center_buy",
        strategy="center_buy",
        entry_economics_authority=ENTRY_ECONOMICS_SUBMITTED_LIMIT,
        fill_authority=FILL_AUTHORITY_NONE,
    )
    result = OrderResult(
        trade_id="rt-exec-missing-status",
        status="",
        fill_price=None,
        submitted_price=0.55,
        shares=21.0,
        command_state="ACKED",
    )

    log_execution_report(conn, pos, result, decision_id="dec-missing-status")
    conn.commit()

    fact = conn.execute(
        """
        SELECT decision_id, filled_at, submitted_price, fill_price, shares, fill_quality,
               latency_seconds, venue_status, terminal_exec_status
        FROM execution_fact
        WHERE intent_id = 'rt-exec-missing-status:entry'
        """
    ).fetchone()
    summary = query_p4_fact_smoke_summary(conn)
    conn.close()

    assert fact["decision_id"] == "dec-missing-status"
    assert fact["filled_at"] is None
    assert fact["submitted_price"] == pytest.approx(0.55)
    assert fact["fill_price"] is None
    assert fact["shares"] is None
    assert fact["fill_quality"] is None
    assert fact["latency_seconds"] is None
    assert fact["venue_status"] == "pending_fill_authority"
    assert fact["terminal_exec_status"] == "pending_fill_authority"
    assert summary["execution"]["terminal_status_counts"] == {"pending_fill_authority": 1}
    assert summary["execution"]["avg_fill_quality"] is None


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_log_settlement_event_emits_durable_record(tmp_path):
    from src.state.db import log_settlement_event, query_position_events
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _create_outcome_fact_table(conn)

    pos = Position(
        trade_id="rt-settle",
        market_id="m5",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        strategy="center_buy",
        edge_source="center_buy",
        decision_snapshot_id="snap1",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )

    log_settlement_event(conn, pos, winning_bin="39-40°F", won=True, outcome=1)
    conn.commit()

    events = query_position_events(conn, "rt-settle")
    outcome_row = conn.execute(
        """
        SELECT strategy_key, entered_at, exited_at, settled_at, exit_reason, decision_snapshot_id,
               pnl, outcome, hold_duration_hours, monitor_count, chain_corrections_count
        FROM outcome_fact
        WHERE position_id = 'rt-settle'
        """
    ).fetchone()
    conn.close()

    assert len(events) == 1
    assert events[0]["event_type"] == "POSITION_SETTLED"
    assert events[0]["details"]["winning_bin"] == "39-40°F"
    assert events[0]["details"]["won"] is True
    assert events[0]["details"]["outcome"] == 1
    assert events[0]["details"]["contract_version"] == "position_settled.v1"
    assert events[0]["details"]["p_posterior"] == pytest.approx(0.60)
    assert events[0]["details"]["exit_price"] == pytest.approx(1.0)
    assert events[0]["details"]["pnl"] == pytest.approx(15.0)
    assert events[0]["details"]["exit_reason"] == "SETTLEMENT"
    assert outcome_row["strategy_key"] == "center_buy"
    assert outcome_row["entered_at"] is None
    assert outcome_row["exited_at"] is None
    assert outcome_row["settled_at"] == "2026-04-01T23:00:00Z"
    assert outcome_row["exit_reason"] == "SETTLEMENT"
    assert outcome_row["decision_snapshot_id"] == "snap1"
    assert outcome_row["pnl"] == pytest.approx(15.0)
    assert outcome_row["outcome"] == 1
    assert outcome_row["hold_duration_hours"] is None
    assert outcome_row["monitor_count"] == 0
    assert outcome_row["chain_corrections_count"] == 0


def test_log_settlement_event_preserves_prior_exit_time_in_outcome_fact(tmp_path):
    from src.state.db import log_settlement_event
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _create_outcome_fact_table(conn)

    pos = Position(
        trade_id="rt-settle-prior-exit",
        market_id="m5",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        strategy="center_buy",
        edge_source="center_buy",
        decision_snapshot_id="snap1",
        entered_at="2026-04-01T00:00:00Z",
        exit_price=0.70,
        pnl=7.5,
        exit_reason="EDGE_REVERSAL",
        last_exit_at="2026-04-01T18:00:00Z",
        state="economically_closed",
    )

    log_settlement_event(
        conn,
        pos,
        winning_bin="39-40°F",
        won=True,
        outcome=1,
        exited_at_override="2026-04-01T18:00:00Z",
    )
    row = conn.execute(
        """
        SELECT entered_at, exited_at, settled_at, hold_duration_hours
        FROM outcome_fact
        WHERE position_id = 'rt-settle-prior-exit'
        """
    ).fetchone()
    conn.close()

    assert row["entered_at"] == "2026-04-01T00:00:00Z"
    assert row["exited_at"] == "2026-04-01T18:00:00Z"
    assert row["settled_at"] == "2026-04-01T18:00:00Z"
    assert row["hold_duration_hours"] == pytest.approx(18.0)


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_query_authoritative_settlement_rows_prefers_position_events(tmp_path):
    from src.state.db import log_settlement_event, query_authoritative_settlement_rows
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="rt-settle-auth",
        market_id="m6",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.61,
        edge=0.21,
        decision_snapshot_id="snap-auth",
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )

    log_settlement_event(conn, pos, winning_bin="39-40°F", won=True, outcome=1)
    conn.commit()

    rows = query_authoritative_settlement_rows(conn, limit=10)
    conn.close()

    assert len(rows) == 1
    assert rows[0]["trade_id"] == "rt-settle-auth"
    assert rows[0]["source"] == "position_events"
    assert rows[0]["authority_level"] == "durable_event"
    assert rows[0]["contract_version"] == "position_settled.v1"
    assert rows[0]["canonical_payload_complete"] is True
    assert rows[0]["contract_missing_fields"] == []
    assert rows[0]["learning_snapshot_ready"] is True
    assert rows[0]["p_posterior"] == pytest.approx(0.61)
    assert rows[0]["outcome"] == 1
    assert rows[0]["pnl"] == pytest.approx(15.0)
    assert rows[0]["winning_bin"] == "39-40°F"
    assert rows[0]["exit_reason"] == "SETTLEMENT"


def test_query_authoritative_settlement_rows_falls_back_to_decision_log(tmp_path):
    from src.state.db import query_authoritative_settlement_rows
    from src.state.decision_chain import SettlementRecord, store_settlement_records

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    store_settlement_records(
        conn,
        [
            SettlementRecord(
                trade_id="legacy-settle",
                city="NYC",
                target_date="2026-04-01",
                range_label="39-40°F",
                direction="buy_yes",
                p_posterior=0.58,
                outcome=1,
                pnl=12.5,
                decision_snapshot_id="legacy-snap",
                edge_source="center_buy",
                strategy="center_buy",
                settled_at="2026-04-01T23:00:00Z",
            )
        ],
    )
    conn.commit()  # Fix B: store_settlement_records no longer commits internally.

    rows = query_authoritative_settlement_rows(conn, limit=10)
    conn.close()

    assert len(rows) == 1
    assert rows[0]["trade_id"] == "legacy-settle"
    assert rows[0]["source"] == "decision_log"
    assert rows[0]["authority_level"] == "legacy_decision_log_fallback"
    assert rows[0]["is_degraded"] is True
    assert rows[0]["canonical_payload_complete"] is False
    assert rows[0]["metric_ready"] is False
    assert rows[0]["learning_snapshot_ready"] is False
    assert rows[0]["settlement_authority"] == "LEGACY_UNKNOWN"
    assert rows[0]["settlement_truth_source"] == "decision_log"
    assert {
        "winning_bin",
        "position_bin",
        "won",
        "exit_price",
        "exit_reason",
    }.issubset(set(rows[0]["contract_missing_fields"]))
    assert rows[0]["outcome"] == 1
    assert rows[0]["pnl"] == pytest.approx(12.5)


def test_query_authoritative_settlement_rows_accepts_env_keyword_for_portfolio_compat(tmp_path):
    from src.state.db import query_authoritative_settlement_rows

    conn = get_connection(tmp_path / "settlement-env-compat.db")
    init_schema(conn)

    assert query_authoritative_settlement_rows(conn, limit=None, env="live") == []

    conn.close()


def test_query_authoritative_settlement_rows_filters_canonical_position_events_by_env(tmp_path):
    from src.engine.lifecycle_events import build_settlement_canonical_write
    from src.state.db import append_many_and_project, query_authoritative_settlement_rows
    from src.state.portfolio import Position

    conn = get_connection(tmp_path / "settlement-env-filter.db")
    init_schema(conn)
    pos = Position(
        trade_id="replay-settle",
        market_id="m-replay",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        env="replay",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.61,
        decision_snapshot_id="snap-replay",
        strategy_key="center_buy",
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )

    events, projection = build_settlement_canonical_write(
        pos,
        winning_bin="39-40°F",
        won=True,
        outcome=1,
        sequence_no=1,
        phase_before="pending_exit",
        settlement_authority="VERIFIED",
        settlement_truth_source="world.settlements",
        settlement_market_slug="nyc-high-2026-04-01",
        settlement_temperature_metric="high",
        settlement_source="WU",
        settlement_value=40.0,
    )
    append_many_and_project(conn, events, projection)

    assert query_authoritative_settlement_rows(conn, limit=None) == []
    assert query_authoritative_settlement_rows(conn, limit=None, env="live") == []
    replay_rows = query_authoritative_settlement_rows(conn, limit=None, env="replay")
    conn.close()

    assert len(replay_rows) == 1
    assert replay_rows[0]["trade_id"] == "replay-settle"
    assert replay_rows[0]["env"] == "replay"
    assert replay_rows[0]["learning_snapshot_ready"] is True


def test_append_many_and_project_requires_env_for_canonical_position_events(tmp_path):
    from src.engine.lifecycle_events import build_settlement_canonical_write
    from src.state.db import append_many_and_project
    from src.state.portfolio import Position

    conn = get_connection(tmp_path / "settlement-missing-env.db")
    init_schema(conn)
    pos = Position(
        trade_id="missing-env-settle",
        market_id="m-missing-env",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.61,
        decision_snapshot_id="snap-missing-env",
        strategy_key="center_buy",
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )
    events, projection = build_settlement_canonical_write(
        pos,
        winning_bin="39-40°F",
        won=True,
        outcome=1,
        sequence_no=1,
        phase_before="pending_exit",
    )
    events[0].pop("env")

    with pytest.raises(ValueError, match="canonical position event missing env"):
        append_many_and_project(conn, events, projection)

    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 0
    conn.close()


def test_lifecycle_builder_rejects_position_without_explicit_env():
    from src.engine.lifecycle_events import build_entry_canonical_write
    from src.state.portfolio import Position, POSITION_ENV_UNKNOWN

    pos = Position(
        trade_id="implicit-env-entry",
        market_id="m-implicit-env",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.61,
        decision_snapshot_id="snap-implicit-env",
        strategy_key="center_buy",
        strategy="center_buy",
        edge_source="center_buy",
        order_id="ord-implicit-env",
        order_status="pending",
        order_posted_at="2026-04-01T12:00:00Z",
        state="pending_tracked",
    )

    assert pos.env == POSITION_ENV_UNKNOWN
    with pytest.raises(ValueError, match="position event env='unknown_env' is invalid"):
        build_entry_canonical_write(
            pos,
            decision_id="dec-implicit-env",
            source_module="tests.test_db",
        )


def test_append_many_and_project_rejects_missing_env_for_non_settlement_events(tmp_path):
    from src.state.db import append_many_and_project

    conn = get_connection(tmp_path / "entry-missing-env.db")
    init_schema(conn)
    event = {
        "event_id": "evt-missing-entry-env",
        "position_id": "pos-missing-entry-env",
        "event_version": 1,
        "sequence_no": 1,
        "event_type": "ENTRY_ORDER_POSTED",
        "occurred_at": "2026-05-07T00:00:00Z",
        "phase_before": None,
        "phase_after": "pending_entry",
        "strategy_key": "center_buy",
        "decision_id": "dec-missing-entry-env",
        "snapshot_id": "snap-missing-entry-env",
        "order_id": None,
        "command_id": None,
        "caused_by": None,
        "idempotency_key": "idem-missing-entry-env",
        "venue_status": None,
        "source_module": "tests.test_db",
        "payload_json": "{}",
    }
    projection = {
        "position_id": "pos-missing-entry-env",
        "phase": "pending_entry",
        "trade_id": "pos-missing-entry-env",
        "market_id": "m-entry-env",
        "city": "NYC",
        "cluster": "US-Northeast",
        "target_date": "2026-04-01",
        "bin_label": "39-40°F",
        "direction": "buy_yes",
        "unit": "F",
        "size_usd": 10.0,
        "shares": 20.0,
        "cost_basis_usd": 10.0,
        "entry_price": 0.5,
        "p_posterior": 0.6,
        "last_monitor_prob": 0.6,
        "last_monitor_edge": 0.1,
        "last_monitor_market_price": 0.5,
        "decision_snapshot_id": "snap-missing-entry-env",
        "entry_method": "test",
        "strategy_key": "center_buy",
        "edge_source": "center_buy",
        "discovery_mode": "opening_hunt",
        "chain_state": "local_only",
        "token_id": "yes-token",
        "no_token_id": "no-token",
        "condition_id": "condition",
        "order_id": None,
        "order_status": None,
        "updated_at": "2026-05-07T00:00:00Z",
        "temperature_metric": "high",
    }

    with pytest.raises(ValueError, match="canonical position event missing env"):
        append_many_and_project(conn, [event], projection)

    assert conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0] == 0
    conn.close()


def test_query_authoritative_settlement_rows_requires_verified_settlement_truth(tmp_path):
    from src.engine.lifecycle_events import build_settlement_canonical_write
    from src.state.db import append_many_and_project, query_authoritative_settlement_rows
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    pos = Position(
        trade_id="verified-settle",
        market_id="m-verified",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.61,
        decision_snapshot_id="snap-verified",
        strategy_key="center_buy",
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )

    events, projection = build_settlement_canonical_write(
        pos,
        winning_bin="39-40°F",
        won=True,
        outcome=1,
        sequence_no=1,
        phase_before="pending_exit",
        settlement_authority="VERIFIED",
        settlement_truth_source="world.settlements",
        settlement_market_slug="nyc-high-2026-04-01",
        settlement_temperature_metric="high",
        settlement_source="WU",
        settlement_value=40.0,
    )
    append_many_and_project(conn, events, projection)
    rows = query_authoritative_settlement_rows(conn, limit=10)
    conn.close()

    assert len(rows) == 1
    assert rows[0]["source"] == "position_events"
    assert rows[0]["settlement_authority"] == "VERIFIED"
    assert rows[0]["settlement_truth_source"] == "world.settlements"
    assert rows[0]["settlement_temperature_metric"] == "high"
    assert rows[0]["settlement_value"] == pytest.approx(40.0)
    assert rows[0]["canonical_payload_complete"] is True
    assert rows[0]["metric_ready"] is True
    assert rows[0]["learning_snapshot_ready"] is True


def test_query_authoritative_settlement_rows_degrades_settled_event_without_truth_authority(tmp_path):
    from src.engine.lifecycle_events import build_settlement_canonical_write
    from src.state.db import append_many_and_project, query_authoritative_settlement_rows
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    pos = Position(
        trade_id="legacy-shaped-settle",
        market_id="m-legacy-shaped",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        env="live",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.61,
        decision_snapshot_id="snap-legacy-shaped",
        strategy_key="center_buy",
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )

    events, projection = build_settlement_canonical_write(
        pos,
        winning_bin="39-40°F",
        won=True,
        outcome=1,
        sequence_no=1,
        phase_before="pending_exit",
    )
    append_many_and_project(conn, events, projection)
    rows = query_authoritative_settlement_rows(conn, limit=10)
    conn.close()

    assert len(rows) == 1
    assert rows[0]["source"] == "position_events"
    assert rows[0]["settlement_authority"] == "UNKNOWN"
    assert rows[0]["metric_ready"] is False
    assert rows[0]["learning_snapshot_ready"] is False
    assert rows[0]["is_degraded"] is True
    assert "missing_verified_settlement_truth" in rows[0]["degraded_reason"]


def test_query_learning_surface_summary_excludes_metric_unready_settlement_rows(monkeypatch, tmp_path):
    import src.state.db as db_module
    from src.state.decision_chain import query_learning_surface_summary

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    monkeypatch.setattr(
        db_module,
        "query_authoritative_settlement_rows",
        lambda *_args, **_kwargs: [
            {
                "trade_id": "legacy-settle",
                "strategy": "shoulder_sell",
                "pnl": 99.0,
                "outcome": 1,
                "metric_ready": False,
                "is_degraded": True,
                "settlement_authority": "LEGACY_UNKNOWN",
            },
            {
                "trade_id": "verified-settle",
                "strategy": "center_buy",
                "pnl": 4.2,
                "outcome": 1,
                "metric_ready": True,
                "is_degraded": False,
                "settlement_authority": "VERIFIED",
            },
        ],
    )
    monkeypatch.setattr(
        db_module,
        "query_execution_event_summary",
        lambda *_args, **_kwargs: {"overall": {}, "by_strategy": {}},
    )

    summary = query_learning_surface_summary(conn)
    conn.close()

    assert summary["settlement_sample_size"] == 1
    assert summary["settlement_degraded_count"] == 1
    assert "shoulder_sell" not in summary["by_strategy"]
    assert summary["by_strategy"]["center_buy"]["settlement_count"] == 1
    assert summary["by_strategy"]["center_buy"]["settlement_pnl"] == pytest.approx(4.2)


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_query_authoritative_settlement_rows_marks_malformed_position_event(tmp_path):
    from src.state.db import (
        log_position_event,
        query_authoritative_settlement_rows,
        query_authoritative_settlement_source,
    )
    from src.state.decision_chain import SettlementRecord, store_settlement_records
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    malformed_pos = Position(
        trade_id="rt-malformed",
        market_id="m7",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.61,
        edge=0.21,
        decision_snapshot_id="snap-missing-posterior",
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=1.0,
        pnl=15.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )
    log_position_event(
        conn,
        "POSITION_SETTLED",
        malformed_pos,
        details={
            "contract_version": "position_settled.v1",
            "winning_bin": "39-40°F",
            "position_bin": "39-40°F",
            "won": True,
            "outcome": 1,
            # p_posterior intentionally omitted: malformed canonical payload
            "exit_price": 1.0,
            "pnl": 15.0,
            "exit_reason": "SETTLEMENT",
        },
        timestamp="2026-04-01T23:00:00Z",
        source="settlement",
    )

    store_settlement_records(
        conn,
        [
            SettlementRecord(
                trade_id="legacy-fallback",
                city="NYC",
                target_date="2026-04-01",
                range_label="39-40°F",
                direction="buy_yes",
                p_posterior=0.58,
                outcome=1,
                pnl=12.5,
                decision_snapshot_id="legacy-snap",
                edge_source="center_buy",
                strategy="center_buy",
                settled_at="2026-04-01T23:00:00Z",
            )
        ],
    )

    rows = query_authoritative_settlement_rows(conn, limit=10)
    assert query_authoritative_settlement_source(conn) == "position_events"
    conn.close()

    assert len(rows) == 1
    assert rows[0]["trade_id"] == "rt-malformed"
    assert rows[0]["source"] == "position_events"
    assert rows[0]["authority_level"] == "durable_event_malformed"
    assert rows[0]["metric_ready"] is False
    assert "p_posterior" in rows[0]["required_missing_fields"]


def test_query_legacy_settlement_records_reads_live_only_rows(tmp_path):
    from src.state.decision_chain import query_legacy_settlement_records

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    diagnostic_artifact = {
        "mode": "settlement",
        "settlements": [
            {
                "trade_id": "diagnostic-legacy",
                "city": "NYC",
                "target_date": "2026-04-01",
                "range_label": "39-40°F",
                "direction": "buy_yes",
                "p_posterior": 0.6,
                "outcome": 1,
                "pnl": 6.0,
                "settled_at": "2026-04-01T23:00:00Z",
            }
        ],
    }
    live_artifact = {
        "mode": "settlement",
        "settlements": [
            {
                "trade_id": "live-legacy",
                "city": "NYC",
                "target_date": "2026-04-01",
                "range_label": "41-42°F",
                "direction": "buy_yes",
                "p_posterior": 0.7,
                "outcome": 1,
                "pnl": 7.0,
                "settled_at": "2026-04-01T23:00:00Z",
            }
        ],
    }
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        ("settlement", "2026-04-01T23:00:00Z", "2026-04-01T23:00:00Z", json.dumps(diagnostic_artifact), "2026-04-01T23:00:00Z", "diagnostic"),
    )
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        ("settlement", "2026-04-01T23:00:00Z", "2026-04-01T23:00:00Z", json.dumps(live_artifact), "2026-04-01T23:00:00Z", "live"),
    )
    conn.commit()

    rows = query_legacy_settlement_records(conn, limit=10)
    conn.close()

    assert [row["trade_id"] for row in rows] == ["live-legacy"]


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_query_settlement_events_latest_wins_by_runtime_trade_id(tmp_path):
    from src.state.db import log_position_event, query_settlement_events
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="dup-stage",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.4,
        p_posterior=0.6,
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=0.0,
        pnl=-1.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )
    log_position_event(
        conn,
        "POSITION_SETTLED",
        pos,
        details={
            "contract_version": "position_settled.v1",
            "winning_bin": "41-42°F",
            "position_bin": "39-40°F",
            "won": False,
            "outcome": 0,
            "p_posterior": 0.6,
            "exit_price": 0.0,
            "pnl": -1.0,
            "exit_reason": "SETTLEMENT",
        },
        timestamp="2026-04-01T23:00:00Z",
        source="settlement",
    )
    log_position_event(
        conn,
        "POSITION_SETTLED",
        pos,
        details={
            "contract_version": "position_settled.v1",
            "winning_bin": "41-42°F",
            "position_bin": "39-40°F",
            "won": False,
            "outcome": 0,
            "p_posterior": 0.6,
            "exit_price": 0.0,
            "pnl": -2.5,
            "exit_reason": "SETTLEMENT",
        },
        timestamp="2026-04-02T00:00:00Z",
        source="settlement",
    )
    conn.commit()

    rows = query_settlement_events(conn, limit=10)
    conn.close()

    assert len(rows) == 1
    assert rows[0]["runtime_trade_id"] == "dup-stage"
    assert rows[0]["timestamp"] == "2026-04-02T00:00:00Z"
    assert rows[0]["details"]["pnl"] == pytest.approx(-2.5)


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_query_settlement_events_preserves_distinct_trade_ids_when_deduping_duplicates(tmp_path):
    from src.state.db import log_position_event, query_settlement_events
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    dup = Position(
        trade_id="dup-stage",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.4,
        p_posterior=0.6,
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=0.0,
        pnl=-1.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )
    other = Position(
        trade_id="other-stage",
        market_id="m2",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="41-42°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.4,
        p_posterior=0.7,
        strategy="opening_inertia",
        edge_source="opening_inertia",
        exit_price=1.0,
        pnl=2.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:30:00Z",
        state="settled",
    )
    for ts, pnl in [("2026-04-01T23:00:00Z", -1.0), ("2026-04-02T00:00:00Z", -2.5)]:
        dup.pnl = pnl
        log_position_event(
            conn,
            "POSITION_SETTLED",
            dup,
            details={
                "contract_version": "position_settled.v1",
                "winning_bin": "41-42°F",
                "position_bin": "39-40°F",
                "won": False,
                "outcome": 0,
                "p_posterior": 0.6,
                "exit_price": 0.0,
                "pnl": pnl,
                "exit_reason": "SETTLEMENT",
            },
            timestamp=ts,
            source="settlement",
        )
    log_position_event(
        conn,
        "POSITION_SETTLED",
        other,
        details={
            "contract_version": "position_settled.v1",
            "winning_bin": "41-42°F",
            "position_bin": "41-42°F",
            "won": True,
            "outcome": 1,
            "p_posterior": 0.7,
            "exit_price": 1.0,
            "pnl": 2.0,
            "exit_reason": "SETTLEMENT",
        },
        timestamp="2026-04-02T01:00:00Z",
        source="settlement",
    )
    conn.commit()

    rows = query_settlement_events(conn, limit=10)
    conn.close()

    assert sorted(row["runtime_trade_id"] for row in rows) == ["dup-stage", "other-stage"]
    latest_dup = next(row for row in rows if row["runtime_trade_id"] == "dup-stage")
    assert latest_dup["details"]["pnl"] == pytest.approx(-2.5)


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_query_authoritative_settlement_rows_dedupes_legacy_stage_rows_by_trade_id(tmp_path):
    from src.state.db import log_position_event, query_authoritative_settlement_rows
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="dup-stage-auth",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.4,
        p_posterior=0.6,
        decision_snapshot_id="snap1",
        strategy="center_buy",
        edge_source="center_buy",
        exit_price=0.0,
        pnl=-1.0,
        exit_reason="SETTLEMENT",
        last_exit_at="2026-04-01T23:00:00Z",
        state="settled",
    )
    for ts, pnl in [("2026-04-01T23:00:00Z", -1.0), ("2026-04-02T00:00:00Z", -2.5)]:
        pos.pnl = pnl
        log_position_event(
            conn,
            "POSITION_SETTLED",
            pos,
            details={
                "contract_version": "position_settled.v1",
                "winning_bin": "41-42°F",
                "position_bin": "39-40°F",
                "won": False,
                "outcome": 0,
                "p_posterior": 0.6,
                "exit_price": 0.0,
                "pnl": pnl,
                "exit_reason": "SETTLEMENT",
            },
            timestamp=ts,
            source="settlement",
        )
    conn.commit()

    rows = query_authoritative_settlement_rows(conn, limit=10)
    conn.close()

    assert len(rows) == 1
    assert rows[0]["trade_id"] == "dup-stage-auth"
    assert rows[0]["pnl"] == pytest.approx(-2.5)
    assert rows[0]["settled_at"] == "2026-04-02T00:00:00Z"
    assert rows[0]["source"] == "position_events"

@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_query_execution_event_summary_groups_entry_and_exit_events(tmp_path):
    from src.state.db import log_position_event, query_execution_event_summary
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="exec-summary-1",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        strategy="center_buy",
        edge_source="center_buy",
        env="live",
    )
    log_position_event(conn, "ORDER_ATTEMPTED", pos, details={"status": "pending"}, source="execution")
    log_position_event(conn, "ORDER_FILLED", pos, details={"status": "filled"}, source="execution")
    log_position_event(conn, "EXIT_ORDER_ATTEMPTED", pos, details={"status": "placed"}, source="exit_lifecycle")
    log_position_event(conn, "EXIT_RETRY_SCHEDULED", pos, details={"status": "retry"}, source="exit_lifecycle")
    conn.commit()

    summary = query_execution_event_summary(conn)
    conn.close()

    assert summary["event_sample_size"] == 4
    assert summary["overall"]["entry_attempted"] == 1
    assert summary["overall"]["entry_filled"] == 1
    assert summary["overall"]["exit_attempted"] == 1
    assert summary["overall"]["exit_retry_scheduled"] == 1
    assert summary["by_strategy"]["center_buy"]["entry_filled"] == 1


def test_query_no_trade_cases_reads_live_only_rows(tmp_path):
    from src.state.decision_chain import query_no_trade_cases

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    diagnostic_artifact = {
        "no_trade_cases": [
            {
                "decision_id": "diagnostic-1",
                "city": "NYC",
                "target_date": "2026-04-01",
                "range_label": "39-40°F",
                "direction": "buy_yes",
                "rejection_stage": "EDGE_INSUFFICIENT",
                "rejection_reasons": ["small"],
            }
        ]
    }
    live_artifact = {
        "no_trade_cases": [
            {
                "decision_id": "live-1",
                "city": "NYC",
                "target_date": "2026-04-01",
                "range_label": "41-42°F",
                "direction": "buy_yes",
                "rejection_stage": "RISK_REJECTED",
                "rejection_reasons": ["risk"],
            }
        ]
    }
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        ("opening_hunt", now, now, json.dumps(diagnostic_artifact), now, "diagnostic"),
    )
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        ("opening_hunt", now, now, json.dumps(live_artifact), now, "live"),
    )
    conn.commit()

    cases = query_no_trade_cases(conn, hours=24)
    conn.close()

    assert [case["decision_id"] for case in cases] == ["live-1"]


def _insert_s2_position_event(
    conn,
    *,
    position_id: str,
    sequence_no: int,
    event_type: str,
    occurred_at: str,
    strategy_key: str = "center_buy",
    phase_before: str | None = None,
    phase_after: str | None = None,
    order_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO position_events (
            event_id, position_id, event_version, sequence_no, event_type,
            occurred_at, phase_before, phase_after, strategy_key, decision_id,
            snapshot_id, order_id, command_id, caused_by, idempotency_key,
            venue_status, source_module, env, payload_json
        ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{position_id}:{sequence_no}:{event_type.lower()}",
            position_id,
            sequence_no,
            event_type,
            occurred_at,
            phase_before,
            phase_after,
            strategy_key,
            f"decision-{position_id}",
            f"snapshot-{position_id}",
            order_id,
            None,
            None,
            f"{position_id}:{sequence_no}:{event_type.lower()}",
            None,
            "tests.test_db",
            "live",
            json.dumps({"test_surface": "s2_lifecycle_funnel"}),
        ),
    )


def test_query_lifecycle_funnel_report_certifies_event_no_trade_chain(tmp_path):
    from src.state.decision_chain import query_lifecycle_funnel_report

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    now = "2026-05-08T16:00:00+00:00"
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "opening_hunt",
            now,
            now,
            json.dumps(
                {
                    "no_trade_cases": [
                        {
                            "decision_id": "nt-edge",
                            "strategy": "center_buy",
                            "rejection_stage": "EDGE_INSUFFICIENT",
                        }
                    ]
                }
            ),
            now,
            "live",
        ),
    )
    _insert_s2_position_event(
        conn,
        position_id="filled-1",
        sequence_no=1,
        event_type="POSITION_OPEN_INTENT",
        occurred_at=now,
        phase_after="pending_entry",
    )
    _insert_s2_position_event(
        conn,
        position_id="filled-1",
        sequence_no=2,
        event_type="ENTRY_ORDER_POSTED",
        occurred_at=now,
        phase_before="pending_entry",
        phase_after="pending_entry",
        order_id="order-filled-1",
    )
    _insert_s2_position_event(
        conn,
        position_id="filled-1",
        sequence_no=3,
        event_type="ENTRY_ORDER_FILLED",
        occurred_at=now,
        phase_before="pending_entry",
        phase_after="active",
        order_id="order-filled-1",
    )
    _insert_s2_position_event(
        conn,
        position_id="filled-1",
        sequence_no=4,
        event_type="SETTLED",
        occurred_at=now,
        phase_before="active",
        phase_after="settled",
        order_id="order-filled-1",
    )
    _insert_s2_position_event(
        conn,
        position_id="pending-1",
        sequence_no=1,
        event_type="POSITION_OPEN_INTENT",
        occurred_at=now,
        phase_after="pending_entry",
        strategy_key="opening_inertia",
    )
    _insert_s2_position_event(
        conn,
        position_id="pending-1",
        sequence_no=2,
        event_type="ENTRY_ORDER_POSTED",
        occurred_at=now,
        phase_before="pending_entry",
        phase_after="pending_entry",
        strategy_key="opening_inertia",
        order_id="order-pending-1",
    )
    _insert_s2_position_event(
        conn,
        position_id="rejected-1",
        sequence_no=1,
        event_type="POSITION_OPEN_INTENT",
        occurred_at=now,
        phase_after="pending_entry",
    )
    _insert_s2_position_event(
        conn,
        position_id="rejected-1",
        sequence_no=2,
        event_type="ENTRY_ORDER_REJECTED",
        occurred_at=now,
        phase_before="pending_entry",
        phase_after="voided",
    )
    conn.commit()

    report = query_lifecycle_funnel_report(conn, not_before="2026-05-08T00:00:00+00:00")
    conn.close()

    assert report["status"] == "observed"
    assert report["counts"] == {
        "evaluated": 4,
        "selected": 3,
        "rejected": 2,
        "submitted": 2,
        "filled": 1,
        "learned": 1,
    }
    assert report["rejection_breakdown"] == {
        "pre_entry_no_trade": 1,
        "post_selection_entry_rejected": 1,
    }
    assert report["relationships"] == {
        "selected_lte_evaluated": True,
        "submitted_lte_selected": True,
        "filled_lte_submitted": True,
        "learned_lte_filled": True,
    }
    assert report["by_strategy"]["center_buy"]["evaluated"] == 3
    assert report["by_strategy"]["center_buy"]["rejected"] == 2
    assert report["by_strategy"]["opening_inertia"]["submitted"] == 1
    assert report["certification"]["empty_trade_tables_certified"] is False
    assert report["authority"] == "derived_operator_visibility"


def test_query_lifecycle_funnel_report_certifies_empty_state(tmp_path):
    from src.state.decision_chain import query_lifecycle_funnel_report

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    report = query_lifecycle_funnel_report(conn, not_before="2026-05-08T00:00:00+00:00")
    conn.close()

    assert report["status"] == "certified_empty"
    assert report["counts"] == {
        "evaluated": 0,
        "selected": 0,
        "rejected": 0,
        "submitted": 0,
        "filled": 0,
        "learned": 0,
    }
    assert report["certification"]["empty_trade_tables_certified"] is True
    assert report["source_errors"] == []


def test_query_lifecycle_funnel_report_applies_hours_to_position_events(tmp_path):
    from src.state.decision_chain import query_lifecycle_funnel_report

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    recent = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()

    _insert_s2_position_event(
        conn,
        position_id="recent-1",
        sequence_no=1,
        event_type="POSITION_OPEN_INTENT",
        occurred_at=recent,
        phase_after="pending_entry",
    )
    _insert_s2_position_event(
        conn,
        position_id="old-1",
        sequence_no=1,
        event_type="POSITION_OPEN_INTENT",
        occurred_at=old,
        phase_after="pending_entry",
    )
    conn.commit()

    report = query_lifecycle_funnel_report(conn, hours=24, not_before=None)
    conn.close()

    assert report["counts"]["evaluated"] == 1
    assert report["counts"]["selected"] == 1
    assert report["by_strategy"]["center_buy"]["evaluated"] == 1


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_query_learning_surface_summary_combines_settlement_no_trade_and_execution(tmp_path):
    from src.state.db import log_position_event, log_settlement_event
    from src.state.decision_chain import query_learning_surface_summary
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "opening_hunt",
            now,
            now,
            json.dumps(
                {
                    "no_trade_cases": [
                        {
                            "decision_id": "nt1",
                            "city": "NYC",
                            "target_date": "2026-04-01",
                            "range_label": "39-40°F",
                            "direction": "buy_yes",
                            "strategy": "center_buy",
                            "edge_source": "center_buy",
                            "rejection_stage": "EDGE_INSUFFICIENT",
                            "rejection_reasons": ["small"],
                        }
                    ]
                }
            ),
            now,
            "live",
        ),
    )
    pos = Position(
        trade_id="learn-1",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        strategy="center_buy",
        edge_source="center_buy",
        decision_snapshot_id="snap1",
        exit_price=1.0,
        pnl=5.0,
        exit_reason="SETTLEMENT",
        last_exit_at=now,
        state="settled",
        env="live",
        size_usd=10.0,
        entry_price=0.4,
        p_posterior=0.7,
        edge=0.2,
    )
    log_settlement_event(conn, pos, winning_bin="39-40°F", won=True, outcome=1)
    log_position_event(conn, "ORDER_REJECTED", pos, details={"status": "rejected"}, source="execution")
    conn.commit()

    summary = query_learning_surface_summary(conn)
    conn.close()

    assert summary["settlement_sample_size"] == 1
    assert summary["settlement_degraded_count"] == 0
    assert summary["no_trade_stage_counts"]["EDGE_INSUFFICIENT"] == 1
    assert summary["execution"]["overall"]["entry_rejected"] == 1
    assert summary["by_strategy"]["center_buy"]["settlement_count"] == 1
    assert summary["by_strategy"]["center_buy"]["no_trade_count"] == 1
    assert summary["by_strategy"]["center_buy"]["no_trade_stage_counts"]["EDGE_INSUFFICIENT"] == 1
    assert summary["by_strategy"]["center_buy"]["entry_rejected"] == 1


def test_query_no_trade_cases_filters_recent_rows_by_real_timestamp(monkeypatch, tmp_path):
    import src.state.decision_chain as decision_chain_module

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 4, 2, 23, 30, tzinfo=timezone.utc)

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    older_artifact = {
        "no_trade_cases": [
            {
                "decision_id": "older",
                "city": "NYC",
                "target_date": "2026-04-02",
                "range_label": "39-40°F",
                "direction": "buy_yes",
                "rejection_stage": "EDGE_INSUFFICIENT",
                "rejection_reasons": ["small"],
            }
        ]
    }
    newer_artifact = {
        "no_trade_cases": [
            {
                "decision_id": "newer",
                "city": "NYC",
                "target_date": "2026-04-02",
                "range_label": "41-42°F",
                "direction": "buy_yes",
                "rejection_stage": "RISK_REJECTED",
                "rejection_reasons": ["risk"],
            }
        ]
    }
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "opening_hunt",
            "2026-04-02T00:00:00+00:00",
            "2026-04-02T00:01:00+00:00",
            json.dumps(older_artifact),
            "2026-04-02T00:30:00+00:00",
            "live",
        ),
    )
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "opening_hunt",
            "2026-04-02T23:00:00+00:00",
            "2026-04-02T23:01:00+00:00",
            json.dumps(newer_artifact),
            "2026-04-02T23:15:00+00:00",
            "live",
        ),
    )
    conn.commit()

    monkeypatch.setattr(decision_chain_module, "datetime", FrozenDatetime)
    cases = decision_chain_module.query_no_trade_cases(conn, hours=1)
    conn.close()

    assert [case["decision_id"] for case in cases] == ["newer"]


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_query_learning_surface_summary_respects_current_regime_start(tmp_path):
    from src.state.db import log_position_event, log_settlement_event
    from src.state.decision_chain import query_learning_surface_summary
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    old_ts = "2026-04-01T00:30:00+00:00"
    new_ts = "2026-04-03T12:30:00+00:00"
    current_regime_started_at = "2026-04-03T00:00:00+00:00"

    old_artifact = {
        "no_trade_cases": [
            {
                "decision_id": "old-nt",
                "city": "NYC",
                "target_date": "2026-04-01",
                "range_label": "39-40°F",
                "direction": "buy_yes",
                "strategy": "center_buy",
                "edge_source": "center_buy",
                "rejection_stage": "EDGE_INSUFFICIENT",
                "rejection_reasons": ["small"],
            }
        ]
    }
    new_artifact = {
        "no_trade_cases": [
            {
                "decision_id": "new-nt",
                "city": "NYC",
                "target_date": "2026-04-03",
                "range_label": "41-42°F",
                "direction": "buy_yes",
                "strategy": "center_buy",
                "edge_source": "center_buy",
                "rejection_stage": "RISK_REJECTED",
                "rejection_reasons": ["risk"],
            }
        ]
    }
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        ("opening_hunt", old_ts, old_ts, json.dumps(old_artifact), old_ts, "live"),
    )
    conn.execute(
        "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
        ("opening_hunt", new_ts, new_ts, json.dumps(new_artifact), new_ts, "live"),
    )

    old_pos = Position(
        trade_id="old-settle",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        strategy="center_buy",
        edge_source="center_buy",
        decision_snapshot_id="snap-old",
        exit_price=1.0,
        pnl=2.0,
        exit_reason="SETTLEMENT",
        last_exit_at=old_ts,
        state="settled",
        env="live",
        size_usd=10.0,
        entry_price=0.4,
        p_posterior=0.7,
        edge=0.2,
    )
    new_pos = Position(
        trade_id="new-settle",
        market_id="m2",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-03",
        bin_label="41-42°F",
        direction="buy_yes",
        strategy="center_buy",
        edge_source="center_buy",
        decision_snapshot_id="snap-new",
        exit_price=1.0,
        pnl=5.0,
        exit_reason="SETTLEMENT",
        last_exit_at=new_ts,
        state="settled",
        env="live",
        size_usd=10.0,
        entry_price=0.4,
        p_posterior=0.7,
        edge=0.2,
    )
    log_settlement_event(conn, old_pos, winning_bin="39-40°F", won=True, outcome=1)
    log_settlement_event(conn, new_pos, winning_bin="41-42°F", won=True, outcome=1)
    log_position_event(conn, "ORDER_REJECTED", old_pos, details={"status": "rejected"}, source="execution", timestamp=old_ts)
    log_position_event(conn, "ORDER_REJECTED", new_pos, details={"status": "rejected"}, source="execution")
    conn.commit()

    summary = query_learning_surface_summary(
        conn,
        not_before=current_regime_started_at,
    )
    conn.close()

    assert summary["settlement_sample_size"] == 1
    assert summary["no_trade_stage_counts"] == {"RISK_REJECTED": 1}
    assert summary["by_strategy"]["center_buy"]["settlement_pnl"] == 5.0
    assert summary["by_strategy"]["center_buy"]["no_trade_count"] == 1
    assert summary["execution"]["overall"]["entry_rejected"] == 1
    assert summary["by_strategy"]["center_buy"]["entry_rejected"] == 1


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_query_learning_surface_summary_does_not_cap_regime_scoped_samples(tmp_path):
    from src.state.db import log_position_event, log_settlement_event
    from src.state.decision_chain import query_learning_surface_summary
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    current_regime_started_at = "2026-04-03T00:00:00+00:00"
    for i in range(55):
        ts = f"2026-04-03T12:{i%60:02d}:00+00:00"
        artifact = {
            "no_trade_cases": [
                {
                    "decision_id": f"nt-{i}",
                    "city": "NYC",
                    "target_date": "2026-04-03",
                    "range_label": "39-40°F",
                    "direction": "buy_yes",
                    "strategy": "center_buy",
                    "edge_source": "center_buy",
                    "rejection_stage": "RISK_REJECTED",
                    "rejection_reasons": ["risk"],
                }
            ]
        }
        conn.execute(
            "INSERT INTO decision_log (mode, started_at, completed_at, artifact_json, timestamp, env) VALUES (?, ?, ?, ?, ?, ?)",
            ("opening_hunt", ts, ts, json.dumps(artifact), ts, "live"),
        )
        pos = Position(
            trade_id=f"settle-{i}",
            market_id=f"m{i}",
            city="NYC",
            cluster="US-Northeast",
            target_date="2026-04-03",
            bin_label="39-40°F",
            direction="buy_yes",
            strategy="center_buy",
            edge_source="center_buy",
            decision_snapshot_id=f"snap-{i}",
            exit_price=1.0,
            pnl=1.0,
            exit_reason="SETTLEMENT",
            last_exit_at=ts,
            state="settled",
            env="live",
            size_usd=10.0,
            entry_price=0.4,
            p_posterior=0.7,
            edge=0.2,
        )
        log_settlement_event(conn, pos, winning_bin="39-40°F", won=True, outcome=1)
    for i in range(205):
        pos = Position(
            trade_id=f"exec-{i}",
            market_id=f"mx{i}",
            city="NYC",
            cluster="US-Northeast",
            target_date="2026-04-03",
            bin_label="39-40°F",
            direction="buy_yes",
            strategy="center_buy",
            edge_source="center_buy",
            env="live",
        )
        log_position_event(
            conn,
            "ORDER_REJECTED",
            pos,
            details={"status": "rejected"},
            source="execution",
            timestamp=f"2026-04-03T13:{i%60:02d}:00+00:00",
        )
    conn.commit()

    summary = query_learning_surface_summary(
        conn,
        not_before=current_regime_started_at,
    )
    conn.close()

    assert summary["settlement_sample_size"] == 55
    assert summary["by_strategy"]["center_buy"]["settlement_count"] == 55
    assert summary["by_strategy"]["center_buy"]["no_trade_count"] == 55
    assert summary["execution"]["event_sample_size"] == 205
    assert summary["execution"]["overall"]["entry_rejected"] == 205
    assert summary["by_strategy"]["center_buy"]["entry_rejected"] == 205


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_exit_lifecycle_event_helpers_emit_sell_side_events(tmp_path):
    from src.state.db import (
        log_exit_attempt_event,
        log_exit_fill_event,
        log_exit_retry_event,
        log_pending_exit_recovery_event,
        log_pending_exit_status_event,
        query_position_events,
    )
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)
    _create_execution_fact_table(conn)

    pos = Position(
        trade_id="rt-exit-events",
        market_id="m7",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        strategy="center_buy",
        edge_source="center_buy",
        exit_reason="EDGE_REVERSAL",
        state="holding",
        exit_state="sell_pending",
        shares=25.0,
        last_exit_order_id="sell-1",
        exit_retry_count=2,
        next_exit_retry_at="2026-04-01T01:10:00Z",
        last_monitor_market_price=0.43,
    )

    log_exit_attempt_event(
        conn,
        pos,
        order_id="sell-1",
        status="placed",
        current_market_price=0.44,
        best_bid=0.43,
        shares=25.0,
    )
    log_pending_exit_status_event(conn, pos, status="OPEN")
    log_exit_retry_event(conn, pos, reason="SELL_REJECTED", error="REJECTED")
    log_pending_exit_recovery_event(
        conn,
        pos,
        event_type="EXIT_INTENT_RECOVERED",
        reason="STRANDED_EXIT_INTENT",
        error="exception_during_sell",
    )
    pos.last_exit_at = "2026-04-01T01:05:00Z"
    log_exit_fill_event(
        conn,
        pos,
        order_id="sell-1",
        fill_price=0.43,
        current_market_price=0.43,
        best_bid=0.43,
        timestamp=pos.last_exit_at,
    )
    conn.commit()

    events = query_position_events(conn, "rt-exit-events")
    fact = conn.execute(
        """
        SELECT order_role, posted_at, filled_at, submitted_price, fill_price, shares, venue_status, terminal_exec_status
        FROM execution_fact
        WHERE intent_id = 'rt-exit-events:exit'
        """
    ).fetchone()
    conn.close()

    event_types = [event["event_type"] for event in events]
    assert "EXIT_ORDER_ATTEMPTED" in event_types
    assert "EXIT_FILL_CHECKED" in event_types
    assert "EXIT_RETRY_SCHEDULED" in event_types
    assert "EXIT_INTENT_RECOVERED" in event_types
    assert "EXIT_ORDER_FILLED" in event_types

    retry_event = next(event for event in events if event["event_type"] == "EXIT_RETRY_SCHEDULED")
    assert retry_event["details"]["error"] == "REJECTED"
    assert retry_event["details"]["retry_count"] == 2

    fill_event = next(event for event in events if event["event_type"] == "EXIT_ORDER_FILLED")
    assert fill_event["order_id"] == "sell-1"
    assert fill_event["details"]["fill_price"] == pytest.approx(0.43)
    assert fact["order_role"] == "exit"
    assert fact["posted_at"] is not None
    assert fact["filled_at"] == "2026-04-01T01:05:00Z"
    assert fact["submitted_price"] == pytest.approx(0.44)
    assert fact["fill_price"] == pytest.approx(0.43)
    assert fact["shares"] == pytest.approx(25.0)
    assert fact["venue_status"] == "CONFIRMED"
    assert fact["terminal_exec_status"] == "filled"


@pytest.mark.skip(reason="P9: legacy position_events write path eliminated")
def test_log_exit_retry_event_uses_backoff_exhausted_type(tmp_path):
    from src.state.db import log_exit_retry_event, query_position_events
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="rt-exit-backoff",
        market_id="m8",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        state="holding",
        exit_state="backoff_exhausted",
        exit_retry_count=10,
    )

    log_exit_retry_event(conn, pos, reason="SELL_STATUS_UNKNOWN", error="3_consecutive_unknown")
    conn.commit()

    events = query_position_events(conn, "rt-exit-backoff")
    conn.close()

    assert len(events) == 1
    assert events[0]["event_type"] == "EXIT_BACKOFF_EXHAUSTED"
    assert events[0]["details"]["error"] == "3_consecutive_unknown"


def test_log_trade_entry_persists_pending_lifecycle_state(tmp_path):
    from src.state.db import log_trade_entry
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="runtime-t1",
        market_id="m_pending",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        strategy="center_buy",
        edge_source="center_buy",
        discovery_mode="opening_hunt",
        market_hours_open=2.5,
        fill_quality=0.01,
        entry_method="ens_member_counting",
        selected_method="ens_member_counting",
        applied_validations=["ens_fetch"],
        state="pending_tracked",
        order_id="order-123",
        order_status="pending",
        order_posted_at="2026-04-01T01:00:00Z",
        chain_state="local_only",
    )

    log_trade_entry(conn, pos)
    conn.commit()

    row = conn.execute(
        """
        SELECT status, timestamp, runtime_trade_id, order_id, order_status_text,
               order_posted_at, entered_at_ts, chain_state, fill_price
        FROM trade_decisions
        ORDER BY trade_id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert row["status"] == "pending_tracked"
    assert row["timestamp"] == "2026-04-01T01:00:00Z"
    assert row["runtime_trade_id"] == "runtime-t1"
    assert row["order_id"] == "order-123"
    assert row["order_status_text"] == "pending"
    assert row["order_posted_at"] == "2026-04-01T01:00:00Z"
    assert row["entered_at_ts"] == ""
    assert row["chain_state"] == "local_only"
    assert row["fill_price"] is None


def test_update_trade_lifecycle_promotes_pending_row_to_entered(tmp_path):
    from src.state.db import log_trade_entry, update_trade_lifecycle
    from src.state.portfolio import Position

    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_schema(conn)

    pos = Position(
        trade_id="runtime-t2",
        market_id="m_pending",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="39-40°F",
        direction="buy_yes",
        unit="F",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.60,
        edge=0.20,
        state="pending_tracked",
        order_id="order-234",
        order_status="pending",
        order_posted_at="2026-04-01T01:00:00Z",
        chain_state="local_only",
    )
    log_trade_entry(conn, pos)

    pos.state = "entered"
    pos.entry_price = 0.41
    pos.order_status = "filled"
    pos.chain_state = "synced"
    pos.entered_at = "2026-04-01T01:05:00Z"
    update_trade_lifecycle(conn, pos)
    conn.commit()

    row = conn.execute(
        """
        SELECT status, timestamp, fill_price, filled_at, entered_at_ts, chain_state, order_status_text
        FROM trade_decisions
        WHERE runtime_trade_id = 'runtime-t2'
        ORDER BY trade_id DESC LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert row["status"] == "entered"
    assert row["timestamp"] == "2026-04-01T01:05:00Z"
    assert row["fill_price"] == pytest.approx(0.41)
    assert row["filled_at"] == "2026-04-01T01:05:00Z"
    assert row["entered_at_ts"] == "2026-04-01T01:05:00Z"
    assert row["chain_state"] == "synced"
    assert row["order_status_text"] == "filled"
