"""Tests for B5: force_exit_review flag in riskguard."""

import sqlite3
from unittest.mock import patch

import pytest

from src.riskguard.riskguard import get_force_exit_review, init_risk_db


def _risk_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_risk_db(conn)
    return conn


class TestInitRiskDbMigration:
    """init_risk_db adds force_exit_review column to existing tables."""

    def test_adds_column_to_fresh_table(self):
        conn = _risk_conn()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(risk_state)").fetchall()}
        assert "force_exit_review" in cols

    def test_adds_column_to_table_without_it(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Create the table WITHOUT force_exit_review (pre-migration schema)
        conn.execute("""
            CREATE TABLE risk_state (
                id INTEGER PRIMARY KEY,
                level TEXT NOT NULL,
                brier REAL,
                accuracy REAL,
                win_rate REAL,
                details_json TEXT,
                checked_at TEXT NOT NULL
            )
        """)
        cols_before = {row[1] for row in conn.execute("PRAGMA table_info(risk_state)").fetchall()}
        assert "force_exit_review" not in cols_before

        init_risk_db(conn)
        cols_after = {row[1] for row in conn.execute("PRAGMA table_info(risk_state)").fetchall()}
        assert "force_exit_review" in cols_after

    def test_idempotent_when_column_exists(self):
        conn = _risk_conn()
        # Second call should not raise
        init_risk_db(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(risk_state)").fetchall()}
        assert "force_exit_review" in cols


class TestForceExitReviewFlag:
    """Verify force_exit_review is written correctly and read by getter."""

    def test_flag_set_when_daily_loss_red(self):
        conn = _risk_conn()
        conn.execute(
            "INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at, force_exit_review)"
            " VALUES ('RED', 0.3, 0.5, 0.4, '{}', '2026-01-01T00:00:00Z', 1)"
        )
        row = conn.execute("SELECT force_exit_review FROM risk_state ORDER BY id DESC LIMIT 1").fetchone()
        assert row["force_exit_review"] == 1

    def test_flag_unset_when_not_red(self):
        conn = _risk_conn()
        conn.execute(
            "INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at, force_exit_review)"
            " VALUES ('GREEN', 0.1, 0.8, 0.7, '{}', '2026-01-01T00:00:00Z', 0)"
        )
        row = conn.execute("SELECT force_exit_review FROM risk_state ORDER BY id DESC LIMIT 1").fetchone()
        assert row["force_exit_review"] == 0

    def test_default_is_zero(self):
        conn = _risk_conn()
        conn.execute(
            "INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at)"
            " VALUES ('GREEN', 0.1, 0.8, 0.7, '{}', '2026-01-01T00:00:00Z')"
        )
        row = conn.execute("SELECT force_exit_review FROM risk_state").fetchone()
        assert row["force_exit_review"] == 0


class TestGetForceExitReview:
    """Verify get_force_exit_review() reads flag correctly and is fail-closed."""

    def test_returns_true_when_flag_set(self):
        conn = _risk_conn()
        conn.execute(
            "INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at, force_exit_review)"
            " VALUES ('RED', 0.3, 0.5, 0.4, '{}', '2026-01-01T00:00:00Z', 1)"
        )
        with patch("src.riskguard.riskguard.get_connection", return_value=conn):
            assert get_force_exit_review() is True

    def test_returns_false_when_flag_unset(self):
        conn = _risk_conn()
        conn.execute(
            "INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at, force_exit_review)"
            " VALUES ('GREEN', 0.1, 0.8, 0.7, '{}', '2026-01-01T00:00:00Z', 0)"
        )
        with patch("src.riskguard.riskguard.get_connection", return_value=conn):
            assert get_force_exit_review() is False

    def test_returns_false_when_no_rows(self):
        conn = _risk_conn()
        with patch("src.riskguard.riskguard.get_connection", return_value=conn):
            assert get_force_exit_review() is False

    def test_fail_closed_on_db_error(self):
        """Fail-closed: return True when DB is inaccessible."""
        with patch("src.riskguard.riskguard.get_connection", side_effect=Exception("DB gone")):
            assert get_force_exit_review() is True

    def test_reads_most_recent_row(self):
        conn = _risk_conn()
        # Insert older row with flag=0, newer row with flag=1
        conn.execute(
            "INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at, force_exit_review)"
            " VALUES ('GREEN', 0.1, 0.8, 0.7, '{}', '2026-01-01T00:00:00Z', 0)"
        )
        conn.execute(
            "INSERT INTO risk_state (level, brier, accuracy, win_rate, details_json, checked_at, force_exit_review)"
            " VALUES ('RED', 0.3, 0.5, 0.4, '{}', '2026-01-01T01:00:00Z', 1)"
        )
        with patch("src.riskguard.riskguard.get_connection", return_value=conn):
            assert get_force_exit_review() is True
